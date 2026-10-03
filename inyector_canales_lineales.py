# -*- coding: utf-8 -*-
"""
Inyector Quir?rgico de Canales Lineales Deportivos:
Empareja los eventos confirmados de hoy con los streams lineales autorizados de la lista Xtream.
- Enfoque Suram?rica (Colombia y Argentina prioritarios): Win Sports, ESPN Suram?rica, DSports, TyC, TNT Sports.
- Enfoque Espa?a autorizado: Eurosport 1, Eurosport 2, Teledeporte.
- Motor: DAZN F1 y Sky Sports F1.
- Exclusi?n total de feeds de USA, Brasil, M?xico y apps OTT.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from adaptadores.modelos import EventoAgenda, obtener_tz
from resolvedor_logos import (
    resolver_logo_equipo,
    resolver_logo_torneo,
)

log = logging.getLogger("inyector_lineales")

REGLAS_CANALES: Dict[str, str] = {
    "CARACOL": r"\bCARACOL\b",
    "RCN": r"\b(?:CANAL\s*)?RCN\b",
    "WIN SPORTS+": r"\bWIN\s*SPORTS\s*\+",
    "WIN SPORTS": r"\bWIN\s*SPORTS\b(?!\s*\+)",
    "DSPORTS 2": r"\b(?:DSPORTS|DIRECTV\s*SPORTS)\s*2\b",
    "DSPORTS +": r"\b(?:DSPORTS|DIRECTV\s*SPORTS)\s*\+",
    "DSPORTS": r"\b(?:DSPORTS|DIRECTV\s*SPORTS)\b(?!\s*[\+2])",
    "ESPN PREMIUM ARGENTINA": r"\bESPN\s*PREMIUM\b",
    "ESPN 2": r"\bESPN\s*2\b",
    "ESPN 3": r"\bESPN\s*3\b",
    "ESPN 4": r"\bESPN\s*4\b",
    "ESPN 5": r"\bESPN\s*5\b",
    "ESPN 6": r"\bESPN\s*6\b",
    "ESPN 7": r"\bESPN\s*7\b",
    "ESPN": r"\bESPN(?:\s*1)?\b(?!\s*(?:[234567]|PREMIUM))",
    "TYC SPORTS": r"\bTYC\s*SPORTS\b",
    "TNT SPORTS": r"\bTNT\s*SPORTS\b",
    "EUROSPORT 1": r"\bEUROSPORTS?\s*1\b|\bEUROSPORTS?\b(?!\s*2)",
    "EUROSPORT 2": r"\bEUROSPORTS?\s*2\b",
    "TELEDEPORTE": r"\b(?:TELEDEPORTE|TDP)\b",
    "DAZN F1": r"\b(?:DAZN\s*F1|DAZN\s*FORMULA\s*1)\b",
    "SKY SPORTS F1": r"\bSKY\s*SPORTS\s*F1\b",
}

# Canales que pertenecen a Suram?rica: excluimos feeds no sudamericanos
CANALES_SURAMERICA = {
    "CARACOL", "RCN",
    "WIN SPORTS+", "WIN SPORTS", "DSPORTS", "DSPORTS 2", "DSPORTS +",
    "ESPN", "ESPN 2", "ESPN 3", "ESPN 4", "ESPN 5", "ESPN 6", "ESPN 7",
    "ESPN PREMIUM ARGENTINA", "TYC SPORTS", "TNT SPORTS"
}

# Exclusiones geogr?ficas estrictas solo para feeds de Suram?rica
PATRON_EXCLUSION_SUR_NOMBRE = re.compile(
    r"\b(USA|US|MEX|MEXICO|MX|BRASIL|BRAZIL|BR|UK|SPAIN|ESPANA|ESPNU|ESPNEWS)\b|ESPN\s*DEPORTES|\bESPN\s*U\b",
    re.I
)
PATRON_EXCLUSION_SUR_CAT = re.compile(
    r"\b(USA|US|MEX|MEXICO|BRASIL|BRAZIL|BR|UK|SPAIN|ESPANA)\b",
    re.I
)

# Priorizaci?n de feeds locales
PATRON_PRIORIDAD_SUR = re.compile(
    r"\b(COLOMBIA|COL|CO|ARGENTINA|ARG|AR)\b|\|(COL|CO|ARG|AR)\||\((COL|CO|ARG|AR)\)",
    re.I
)
PATRON_PRIORIDAD_ESPANA = re.compile(
    r"\b(ESPANA|ESPA?A|ES|SPAIN)\b|\|(ES)\||\((ES)\)",
    re.I
)

def normalizar(s: str) -> str:
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    sin_tildes = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9\+]+", " ", sin_tildes.upper()).strip()

def construir_indice_canales_lineales(canales_xtream: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, str]]]:
    """
    Agrupa los streams de Xtream bajo sus nombres can?nicos respetando filtros geogr?ficos estrictos.
    - Suram?rica: Exclusi?n total de feeds de USA, M?xico y Brasil. Prioridad Colombia y Argentina.
    - Espa?a (Eurosport, Teledeporte, DAZN F1): Prioridad para se?ales espa?olas.
    """
    indice: Dict[str, List[Dict[str, str]]] = {canon: [] for canon in REGLAS_CANALES}

    for c in canales_xtream:
        nombre = c.get("name") or c.get("stream_name") or ""
        sid = str(c.get("stream_id") or c.get("id") or "")
        cat_nombre = c.get("category_name") or ""
        if not nombre or not sid:
            continue

        n_norm = normalizar(nombre)
        cat_norm = normalizar(cat_nombre)

        for canon, patron in REGLAS_CANALES.items():
            if re.search(patron, n_norm, re.I):
                # Reglas estrictas de exclusion para senales nacionales principales de Colombia
                if canon in ["CARACOL", "RCN"]:
                    # Excluir explicitamente senales secundarias (HD 2, Internacional, Novelas, etc.)
                    if re.search(r"\bHD\s*2\b|\bHD2\b|\b(?:CARACOL|RCN)\s*2\b", n_norm):
                        continue
                    if any(w in f" {n_norm} " for w in [" INT ", " INTERNACIONAL ", " NOVELAS ", " MAS ", " MÁS ", " RADIO ", " NUESTRA "]):
                        continue
                if canon in CANALES_SURAMERICA:
                    if PATRON_EXCLUSION_SUR_NOMBRE.search(f" {n_norm} ") or PATRON_EXCLUSION_SUR_CAT.search(f" {cat_norm} "):
                        continue
                    if canon in ["CARACOL", "RCN"]:
                        # Maxima prioridad a senales maestras SAT y HD
                        if "SAT" in n_norm or " HD" in n_norm:
                            prioridad = 25
                        elif "OP 1" in n_norm or "OPC 1" in n_norm or "OP1" in n_norm:
                            prioridad = 20
                        else:
                            prioridad = 15
                    else:
                        prioridad = 10 if PATRON_PRIORIDAD_SUR.search(n_norm) or PATRON_PRIORIDAD_SUR.search(cat_norm) else 1
                elif canon in ["TELEDEPORTE", "EUROSPORT 1", "EUROSPORT 2", "DAZN F1"]:
                    prioridad = 10 if PATRON_PRIORIDAD_ESPANA.search(n_norm) or PATRON_PRIORIDAD_ESPANA.search(cat_norm) else 1
                else:
                    prioridad = 1

                indice[canon].append({
                    "nombre": nombre.strip(),
                    "id_xtream": sid,
                    "_prioridad": prioridad
                })
                break

    # Ordenar por prioridad geogr?fica
    for canon in indice:
        indice[canon].sort(key=lambda x: x.get("_prioridad", 1), reverse=True)
        for item in indice[canon]:
            item.pop("_prioridad", None)

    metricas = {k: len(v) for k, v in indice.items() if v}
    log.info("Canales lineales Xtream indexados (limpios con aislamiento geo): %s", metricas)
    return indice

def inyectar_eventos_lineales(
    agenda_hoy: List[EventoAgenda],
    indice_canales: Dict[str, List[Dict[str, str]]],
    zona_horaria: str = "America/Bogota"
) -> List[Dict[str, Any]]:
    """
    Cruza los eventos confirmados de la Agenda Maestra con los streams lineales de Xtream.
    """
    tz_prod = obtener_tz(zona_horaria)
    eventos_inyectados = []

    for ev in agenda_hoy:
        fuentes_disponibles = []
        canales_usados = []

        # Priorizar canales nacionales abiertos de Colombia al inicio
        canales_priorizados = sorted(
            ev.canales,
            key=lambda x: 0 if x.upper() in ["CARACOL", "RCN"] else 1
        )
        for c in canales_priorizados:
            canon = c.upper()
            if canon in indice_canales and indice_canales[canon]:
                fuentes_disponibles.extend(indice_canales[canon])
                canales_usados.append(canon)

        if not fuentes_disponibles:
            continue

        # Si hay multiples canales emisores (ej. CARACOL y RCN),
        # repartir equilibradamente las mejores opciones de cada canal (hasta 3 de cada uno)
        fuentes_unicas = []
        vistas = set()
        max_por_canal = 3 if len(canales_usados) > 1 else 5

        for c in canales_priorizados:
            canon = c.upper()
            if canon in indice_canales:
                count_c = 0
                for f in indice_canales[canon]:
                    if f["id_xtream"] not in vistas:
                        vistas.add(f["id_xtream"])
                        fuentes_unicas.append(f)
                        count_c += 1
                        if count_c >= max_por_canal:
                            break

        # Convertir hora a hora_local_producto
        try:
            dt_utc = datetime.fromisoformat(ev.hora_utc.replace("Z", "+00:00"))
            dt_local = dt_utc.astimezone(tz_prod)
            hora_local_prod = dt_local.strftime("%H:%M")
        except Exception:
            hora_local_prod = "00:00"

        # Resolver logos
        logo_torneo = resolver_logo_torneo(ev.torneo or ev.titulo, ev.deporte)
        logo_loc = resolver_logo_equipo(ev.local, ev.deporte, ev.torneo) if ev.local else ""
        logo_vis = resolver_logo_equipo(ev.visitante, ev.deporte, ev.torneo) if ev.visitante else ""

        slug_canal = canales_usados[0].lower().replace(" ", "_").replace("+", "plus")
        hash_evento = hashlib.sha1(f"{ev.titulo}_{ev.hora_utc}_{canales_usados[0]}".encode()).hexdigest()[:8]
        id_evento = f"lineal_{slug_canal}_{hash_evento}"

        evento_dict = {
            "id": id_evento,
            "agenda_id": "",
            "titulo": ev.titulo,
            "torneo": ev.torneo or ev.titulo,
            "categoria": ev.deporte,
            "tipo_evento": ev.tipo_evento,
            "equipo_local": ev.local,
            "equipo_visitante": ev.visitante,
            "subtitulo": f"{ev.torneo}",
            "referencia": ev.referencia or ev.torneo,
            "hora_utc": ev.hora_utc,
            "hora_local_producto": hora_local_prod,
            "duracion_min": ev.duracion_min,
            "logo_torneo": logo_torneo,
            "logo_local": logo_loc,
            "logo_visitante": logo_vis,
            "banner": logo_torneo,
            "tier": 1 if any(k in (ev.torneo or "").upper() for k in ["LALIGA", "PREMIER", "CHAMPIONS", "BETPLAY", "CONMEBOL", "NBA", "MLB"]) else 2,
            "origen": "inyector_lineal",
            "origenes": ["inyector_lineal", ev.fuente],
            "estado": "confirmado",
            "estado_evento": "confirmado",
            "confianza": "alta",
            "puntuacion_confianza": 0.95,
            "fuentes": fuentes_unicas,
        }
        eventos_inyectados.append(evento_dict)

    log.info("Total eventos lineales inyectados con streams: %d", len(eventos_inyectados))
    return eventos_inyectados
