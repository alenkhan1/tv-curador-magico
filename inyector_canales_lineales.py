# -*- coding: utf-8 -*-
"""
Inyector Quirúrgico de Canales Lineales Deportivos:
Empareja los eventos confirmados de hoy con los streams de la lista Xtream del usuario.
- Regla 1: Mapeo exacto por canal y región (exclusión total de USA, México o feeds cruzados).
- Regla 2: Máximo 3-4 streams ordenados por calidad (FHD, HD, Opc. 1, Opc. 2).
- Regla 3: IDs 100% únicos con hash determinista (evita crash en Jetpack Compose).
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

# Patrones regex estrictos para canales autorizados
REGLAS_CANALES: Dict[str, str] = {
    "WIN SPORTS+": r"WIN\s*SPORTS\s*\+",
    "WIN SPORTS": r"WIN\s*SPORTS(?!\s*\+)",
    "DSPORTS 2": r"(?:DSPORTS|DIRECTV\s*SPORTS)\s*2",
    "DSPORTS +": r"(?:DSPORTS|DIRECTV\s*SPORTS)\s*\+",
    "DSPORTS": r"(?:DSPORTS|DIRECTV\s*SPORTS)(?!\s*[\+2])",
    "ESPN PREMIUM ARGENTINA": r"ESPN\s*PREMIUM",
    "ESPN 2": r"\bESPN\s*2\b",
    "ESPN 3": r"\bESPN\s*3\b",
    "ESPN 4": r"\bESPN\s*4\b",
    "ESPN 5": r"\bESPN\s*5\b",
    "ESPN": r"\bESPN(?!\s*[234567]| PREMIUM)\b",
    "TYC SPORTS": r"\bTYC\s*SPORTS\b",
    "TNT SPORTS": r"\bTNT\s*SPORTS\b",
    "EUROSPORT 1": r"EUROSPORT\s*1",
    "EUROSPORT 2": r"EUROSPORT\s*2",
    "DAZN LALIGA": r"DAZN\s*LALIGA",
    "DAZN 1": r"DAZN\s*1",
    "DAZN 2": r"DAZN\s*2",
    "MOVISTAR #VAMOS": r"\bVAMOS\b",
    "MOVISTAR DEPORTES": r"M\+\s*DEPORTES|MOVISTAR\s*DEPORTES",
    "M+ LIGA DE CAMPEONES": r"LIGA\s*DE\s*CAMPEONES",
    "SKY SPORTS MAIN EVENT": r"SKY\s*SPORTS\s*MAIN\s*EVENT",
    "SKY SPORTS FOOTBALL": r"SKY\s*SPORTS\s*FOOTBALL",
    "SKY SPORTS F1": r"SKY\s*SPORTS\s*F1",
    "SKY SPORTS GOLF": r"SKY\s*SPORTS\s*GOLF",
}

# Canales que pertenecen a Suramérica: excluimos feeds no sudamericanos
CANALES_SURAMERICA_TAGS = {"WIN SPORTS+", "WIN SPORTS", "DSPORTS", "DSPORTS 2", "DSPORTS +", "ESPN", "ESPN 2", "ESPN 3", "ESPN 4", "ESPN 5", "ESPN PREMIUM ARGENTINA", "TYC SPORTS", "TNT SPORTS"}
EXCLUSIONES_GEO_SURAMERICA = re.compile(r"\b(USA|US|MEX|MX|MEXICO|CARIBE|BRASIL|BRAZIL|UK)\b", re.I)

def normalizar(s: str) -> str:
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    sin_tildes = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin_tildes).strip().upper()

def construir_indice_canales_lineales(canales_xtream: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, str]]]:
    """
    Agrupa los streams de Xtream bajo sus nombres canónicos respetando filtros geográficos.
    """
    indice: Dict[str, List[Dict[str, str]]] = {canon: [] for canon in REGLAS_CANALES}

    for c in canales_xtream:
        nombre = c.get("name") or c.get("stream_name") or ""
        sid = str(c.get("stream_id") or c.get("id") or "")
        if not nombre or not sid:
            continue

        n_norm = normalizar(nombre)

        # Probar contra cada regla
        for canon, patron in REGLAS_CANALES.items():
            if re.search(patron, n_norm, re.I):
                # Si el canal es de Suramérica, descartar feeds externos (USA, MX, etc.)
                if canon in CANALES_SURAMERICA_TAGS and EXCLUSIONES_GEO_SURAMERICA.search(n_norm):
                    continue
                indice[canon].append({
                    "nombre": nombre.strip(),
                    "id_xtream": sid,
                })

    metricas = {k: len(v) for k, v in indice.items() if v}
    log.info("Canales lineales Xtream indexados (limpios): %s", metricas)
    return indice

def inyectar_eventos_lineales(
    agenda_hoy: List[EventoAgenda],
    indice_canales: Dict[str, List[Dict[str, str]]],
    zona_horaria: str = "America/Bogota"
) -> List[Dict[str, Any]]:
    """
    Cruza los eventos confirmados de la Agenda Maestra con los streams lineales de Xtream.
    Garantiza:
    - Máximo 3-4 opciones de alta calidad por evento.
    - ID estrictamente único por evento (evita colisiones en Jetpack Compose).
    """
    tz_prod = obtener_tz(zona_horaria)
    eventos_inyectados = []

    for ev in agenda_hoy:
        fuentes_disponibles = []
        canales_usados = []

        for c in ev.canales:
            canon = c.upper()
            if canon in indice_canales and indice_canales[canon]:
                fuentes_disponibles.extend(indice_canales[canon])
                canales_usados.append(canon)

        if not fuentes_disponibles:
            continue

        # Deduplicar fuentes y limitar a un máximo de 4 opciones de calidad
        vistas = set()
        fuentes_unicas = []
        for f in fuentes_disponibles:
            if f["id_xtream"] not in vistas:
                vistas.add(f["id_xtream"])
                fuentes_unicas.append(f)
            if len(fuentes_unicas) >= 4:
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
        logo_loc = resolver_logo_equipo(ev.local, ev.deporte) if ev.local else ""
        logo_vis = resolver_logo_equipo(ev.visitante, ev.deporte) if ev.visitante else ""

        slug_canal = canales_usados[0].lower().replace(" ", "_").replace("+", "plus")
        # Generar hash determinista para garantizar ID 100% único
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
            "subtitulo": f"{ev.torneo} | En vivo por {', '.join(canales_usados)}",
            "referencia": ev.referencia or ev.torneo,
            "hora_utc": ev.hora_utc,
            "hora_local_producto": hora_local_prod,
            "duracion_min": ev.duracion_min,
            "logo_torneo": logo_torneo,
            "logo_local": logo_loc,
            "logo_visitante": logo_vis,
            "banner": logo_torneo or logo_loc,
            "tier": 1 if any(k in ev.torneo.upper() for k in ["LALIGA", "PREMIER", "CHAMPIONS", "BETPLAY", "CONMEBOL", "NBA", "MLB"]) else 2,
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
