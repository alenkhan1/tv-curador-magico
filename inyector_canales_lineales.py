# -*- coding: utf-8 -*-
"""
Inyector Quirúrgico de Canales Lineales Deportivos:
Empareja los eventos confirmados de hoy con los streams lineales autorizados de la lista Xtream.
- Aislamiento territorial estricto 1 a 1:
  * Win Sports+, Win Sports, Caracol, RCN -> Colombia exclusivamente.
  * ESPN CO, ESPN 2 CO, ESPN 3 CO -> Feeds Colombia (CO) exclusivamente.
  * ESPN AR, ESPN 2 AR, ESPN 3 AR, ESPN Premium -> Feeds Argentina (AR) exclusivamente.
  * TyC Sports, TNT Sports -> Argentina exclusivamente.
  * Eurosport 1, Eurosport 2, Teledeporte -> España exclusivamente.
  * DAZN F1 y Sky Sports F1 -> Feeds autorizados de motor.
- Exclusión total de feeds de USA, Brasil, México y apps OTT.
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

FAMILIAS_CANALES_HERMANOS: List[List[str]] = [
    # Familia Win Sports: Win Sports+ tiene maxima prioridad sobre la senal basica
    ["WIN SPORTS+", "WIN SPORTS"],
    # Familia DSports: senales especificas sobre la generica
    ["DSPORTS 2", "DSPORTS +", "DSPORTS AR", "DSPORTS CO", "DSPORTS"],
    # Familia Eurosport: senales especificas 1 vs 2
    ["EUROSPORT 1", "EUROSPORT 2"],
    # Familia ESPN Argentina
    ["ESPN PREMIUM ARGENTINA", "ESPN 2 AR", "ESPN 3 AR", "ESPN 4", "ESPN 5", "ESPN 6", "ESPN 7", "ESPN AR"],
    # Familia ESPN Colombia
    ["ESPN 2 CO", "ESPN 3 CO", "ESPN CO"],
]

def normalizar(s: str) -> str:
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    sin_tildes = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9\+]+", " ", sin_tildes.upper()).strip()

def clasificar_canal_xtream(nombre_crudo: str, cat_cruda: str) -> Optional[Tuple[str, str]]:
    """
    Clasifica un stream de Xtream en su canal canonico y su pais/territorio exacto.
    Devuelve (canon, pais) o None si el canal no es deportivo relevante o es de un territorio no deseado.
    """
    n = normalizar(nombre_crudo)
    c = normalizar(cat_cruda)

    # Exclusiones globales de VOD, feeds extranjeros irrelevantes
    if any(k in f" {c} " for k in [" USA ", " BRASIL ", " BRAZIL ", " FRANCE ", " UK ", " GERMANY ", " MOVIES ", " SERIES ", " ADULT "]):
        return None
    if any(k in f" {n} " for k in [" USA ", " US ", " FR ", " BR ", " UK ", " DE "]) and not any(k in n for k in ["SUR", "CO", "ARG"]):
        return None
    if any(k in f" {n} " for k in [" INTERNACIONAL ", " NOVELAS ", " HD2 ", " HD 2 ", " MAS ", " MÁS ", " RADIO "]):
        return None

    # Win Sports (Colombia)
    if "WIN" in n and "SPORTS" in n:
        if "+" in n:
            return ("WIN SPORTS+", "CO")
        return ("WIN SPORTS", "CO")

    # Caracol y RCN (Colombia)
    if re.search(r"CARACOL", n) and ("COLOMBIA" in c or "CO" in n.split()):
        return ("CARACOL", "CO")
    if re.search(r"(?:CANAL\s*)?RCN", n) and ("COLOMBIA" in c or "CO" in n.split()):
        return ("RCN", "CO")

    # TyC Sports (Argentina)
    if "TYC" in n and "SPORTS" in n:
        return ("TYC SPORTS", "AR")

    # TNT Sports (Argentina)
    if "TNT" in n and ("SPORT" in n or "PREMIUM" in n) and ("ARGENTINA" in c or "PREMIUM" in c):
        if not any(k in n for k in ["CHILE", "BRASIL", "BRAZIL"]):
            return ("TNT SPORTS", "AR")

    # DSPORTS
    if "DSPORTS" in n or "DIRECTV SPORTS" in n:
        if "2" in n:
            return ("DSPORTS 2", "SUR")
        if "+" in n:
            return ("DSPORTS +", "SUR")
        if "CO" in n.split() or "COLOMBIA" in c:
            return ("DSPORTS CO", "CO")
        if any(k in n for k in ["AR", "ARG"]) or "ARGENTINA" in c:
            return ("DSPORTS AR", "AR")
        return ("DSPORTS", "SUR")

    # España: Eurosport, Teledeporte, DAZN F1
    if any(k in c for k in ["ESPANA", "ESPAÑA", "SPAIN", "DAZN ESPANA", "DAZN ESPAÑA"]):
        if "EUROSPORT" in n:
            if "2" in n:
                return ("EUROSPORT 2", "ES")
            return ("EUROSPORT 1", "ES")
        if "TELEDEPORTE" in n or "TDP" in n:
            return ("TELEDEPORTE", "ES")
        if "DAZN" in n and "F1" in n:
            return ("DAZN F1", "ES")

    # ESPN Territorial
    if "ESPN" in n:
        # Excluir explícitamente México y feeds USA
        if any(k in f" {n} " for k in [" MEX ", " MEXICO ", " MX ", " DEPORTES ", " NEWS ", " ESPNU "]):
            return None

        # ESPN Premium (Argentina)
        if "PREMIUM" in n:
            if any(k in n for k in ["AR", "ARG"]) or "ARGENTINA" in c:
                return ("ESPN PREMIUM ARGENTINA", "AR")
            return None

        # ESPN 2
        if re.search(r"ESPN\s*2", n):
            if any(k in n for k in ["AR", "ARG"]) or "ARGENTINA" in c:
                return ("ESPN 2 AR", "AR")
            if "CO" in n.split() or "COL" in n.split() or "COLOMBIA" in c:
                return ("ESPN 2 CO", "CO")
            return None

        # ESPN 3
        if re.search(r"ESPN\s*3", n):
            if any(k in n for k in ["AR", "ARG"]) or "ARGENTINA" in c:
                return ("ESPN 3 AR", "AR")
            if "CO" in n.split() or "COL" in n.split() or "COLOMBIA" in c:
                return ("ESPN 3 CO", "CO")
            return None

        # Diales altos de ESPN (Suramérica general)
        for num in ["4", "5", "6", "7"]:
            if re.search(rf"ESPN\s*{num}", n):
                return (f"ESPN {num}", "SUR")

        # ESPN 1 / ESPN básico
        if re.search(r"ESPN(?:\s*1)?", n):
            if any(k in n for k in ["AR", "ARG"]) or "ARGENTINA" in c:
                return ("ESPN AR", "AR")
            if "CO" in n.split() or "COL" in n.split() or "COLOMBIA" in c:
                return ("ESPN CO", "CO")

    return None

def construir_indice_canales_lineales(canales_xtream: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, str]]]:
    """
    Agrupa los streams de Xtream bajo sus nombres canónicos respetando filtros territoriales estrictos.
    """
    indice: Dict[str, List[Dict[str, str]]] = {}

    for c in canales_xtream:
        nombre = c.get("name") or c.get("stream_name") or ""
        sid = str(c.get("stream_id") or c.get("id") or "")
        cat_nombre = c.get("category_name") or ""
        if not nombre or not sid:
            continue

        res = clasificar_canal_xtream(nombre, cat_nombre)
        if not res:
            continue
        canon, pais = res

        # Asignar prioridad de calidad (FHD/4K > HD > SD)
        n_u = nombre.upper()
        if "4K" in n_u or "UHD" in n_u or "FHD" in n_u:
            prioridad = 30
        elif "HD" in n_u:
            prioridad = 20
        else:
            prioridad = 10

        item = {
            "nombre": nombre.strip(),
            "id_xtream": sid,
            "canal_lineal": canon,
            "pais": pais,
            "_prioridad": prioridad,
        }
        indice.setdefault(canon, []).append(item)

    # Ordenar cada canal por prioridad de calidad y limpiar campo temporal
    for canon in indice:
        indice[canon].sort(key=lambda x: x.get("_prioridad", 0), reverse=True)
        for item in indice[canon]:
            item.pop("_prioridad", None)

    metricas = {k: len(v) for k, v in indice.items()}
    log.info("Canales lineales Xtream indexados con aislamiento territorial: %s", metricas)
    return indice

def inyectar_eventos_lineales(
    agenda_hoy: List[EventoAgenda],
    indice_canales: Dict[str, List[Dict[str, str]]],
    zona_horaria: str = "America/Bogota"
) -> List[Dict[str, Any]]:
    """
    Cruza los eventos confirmados de la Agenda Maestra con los streams lineales de Xtream,
    garantizando que cada evento se conecte únicamente con los streams de su territorio exacto.
    """
    tz_prod = obtener_tz(zona_horaria)
    eventos_inyectados = []

    for ev in agenda_hoy:
        canales_a_procesar = []

        # Mapeo territorial tolerante: si la agenda trae "ESPN" genérico, asociar las variantes correspondientes
        for c in ev.canales:
            cu = c.upper()
            if cu == "ESPN":
                canales_a_procesar.extend(["ESPN CO", "ESPN AR"])
            elif cu == "ESPN 2":
                canales_a_procesar.extend(["ESPN 2 CO", "ESPN 2 AR"])
            elif cu == "ESPN 3":
                canales_a_procesar.extend(["ESPN 3 CO", "ESPN 3 AR"])
            elif cu == "DSPORTS":
                canales_a_procesar.extend(["DSPORTS CO", "DSPORTS AR"])
            else:
                canales_a_procesar.append(cu)

        # Desambiguacion de Canales Hermanos Mutuamente Excluyentes
        canales_desambiguados = []
        familias_vistas = set()
        for c_u in canales_a_procesar:
            familia_encontrada = None
            for idx_f, fam in enumerate(FAMILIAS_CANALES_HERMANOS):
                if c_u in fam:
                    familia_encontrada = idx_f
                    break
            if familia_encontrada is not None:
                if familia_encontrada not in familias_vistas:
                    familias_vistas.add(familia_encontrada)
                    canales_desambiguados.append(c_u)
            else:
                canales_desambiguados.append(c_u)
        canales_a_procesar = canales_desambiguados

        fuentes_unicas = []
        vistas = set()
        canales_usados = []
        max_por_canal = 3 if len(canales_a_procesar) > 1 else 5

        for canon in canales_a_procesar:
            if canon in indice_canales:
                canales_usados.append(canon)
                count_c = 0
                for f in indice_canales[canon]:
                    if f["id_xtream"] not in vistas:
                        vistas.add(f["id_xtream"])
                        item_f = dict(f)
                        item_f["canal_lineal"] = canon
                        fuentes_unicas.append(item_f)
                        count_c += 1
                        if count_c >= max_por_canal:
                            break

        if not fuentes_unicas:
            continue

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
            "canales_candidatos": list(dict.fromkeys(canales_usados)),
            "texto_original_crudo": getattr(ev, "texto_original_crudo", "") or ev.titulo,
            "titulo_original_crudo": getattr(ev, "texto_original_crudo", "") or ev.titulo,
        }
        eventos_inyectados.append(evento_dict)

    log.info("Total eventos lineales inyectados con streams: %d", len(eventos_inyectados))
    return eventos_inyectados
