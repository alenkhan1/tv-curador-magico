# -*- coding: utf-8 -*-
"""
Inyector Universal de Canales Lineales Deportivos:
Toma la Agenda Maestra verificada de hoy y la empareja con los canales deportivos
permanentes de la lista Xtream del usuario (Win Sports+, DSports, DAZN, Eurosport, FS1, TyC, etc.).
"""
from __future__ import annotations

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
    envolver_cdn_proxy,
    resolver_logo_equipo,
    resolver_logo_torneo,
)

log = logging.getLogger("inyector_lineales")

# Patrones regex de canales canonicos a variantes de nombres en Xtream
REGLAS_CANALES: Dict[str, str] = {
    "WIN SPORTS+": r"WIN\s*SPORTS\s*\+",
    "WIN SPORTS": r"WIN\s*SPORTS(?!\s*\+)",
    "DSPORTS 2": r"(?:DSPORTS|DIRECTV\s*SPORTS)\s*2",
    "DSPORTS +": r"(?:DSPORTS|DIRECTV\s*SPORTS)\s*\+",
    "DSPORTS FIGHT": r"(?:DSPORTS|DIRECTV\s*SPORTS)\s*FIGHT",
    "DSPORTS": r"(?:DSPORTS|DIRECTV\s*SPORTS)(?!\s*[\+2])",
    "EUROSPORT 1": r"EUROSPORT\s*1",
    "EUROSPORT 2": r"EUROSPORT\s*2",
    "TELEDEPORTE": r"TELEDEPORTE|\bTDP\b",
    "DAZN LALIGA": r"DAZN\s*LALIGA",
    "DAZN 1": r"DAZN\s*1",
    "DAZN 2": r"DAZN\s*2",
    "MOVISTAR #VAMOS": r"\bVAMOS\b",
    "MOVISTAR DEPORTES": r"M\+\s*DEPORTES|MOVISTAR\s*DEPORTES",
    "M+ LIGA DE CAMPEONES": r"LIGA\s*DE\s*CAMPEONES",
    "TYC SPORTS": r"TYC\s*SPORTS",
    "TNT SPORTS": r"TNT\s*SPORTS",
    "FS1": r"\bFS1\b|FOX\s*SPORTS\s*1",
    "FS2": r"\bFS2\b|FOX\s*SPORTS\s*2",
    "FOX SPORTS 2": r"FOX\s*SPORTS\s*2",
    "FOX SPORTS 3": r"FOX\s*SPORTS\s*3",
    "FOX SPORTS": r"FOX\s*SPORTS(?!\s*[123])",
    "TENNIS CHANNEL": r"TENNIS\s*CHANNEL",
    "GOLF CHANNEL": r"GOLF\s*CHANNEL",
    "CBS SPORTS NETWORK": r"CBS\s*SPORTS",
    "USA NETWORK": r"USA\s*NETWORK",
    "TUDN USA": r"TUDN",
    "ESPN PREMIUM ARGENTINA": r"ESPN\s*PREMIUM",
    "ESPN 2": r"ESPN\s*2",
    "ESPN 3": r"ESPN\s*3",
    "ESPN 4": r"ESPN\s*4",
    "ESPN 5": r"ESPN\s*5",
    "ESPN": r"ESPN(?!\s*[2345])",
}

def normalizar(s: str) -> str:
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    sin_tildes = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin_tildes).strip().upper()

def construir_indice_canales_lineales(canales_xtream: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, str]]]:
    """
    Agrupa los streams de Xtream bajo sus nombres canónicos.
    Ej: 'WIN SPORTS+' -> [{'nombre': 'WIN SPORTS + (Opc.1)', 'id_xtream': '52637'}, ...]
    """
    indice: Dict[str, List[Dict[str, str]]] = {canon: [] for canon in REGLAS_CANALES}

    for c in canales_xtream:
        nombre = c.get("name") or c.get("stream_name") or ""
        sid = str(c.get("stream_id") or c.get("id") or "")
        if not nombre or not sid:
            continue

        n_norm = normalizar(nombre)
        # Limpiar prefijos de país o numeración
        n_limpio = re.sub(r"^[0-9]{1,3}\s*\|\s*", "", n_norm)
        n_limpio = re.sub(r"^(?:SP|ARG|CO|ES|CL|MX|PE)\s*:\s*(?:CO|ARG|ES|CL|MX|PE)?\s*:\s*", "", n_limpio)

        for canon, patron in REGLAS_CANALES.items():
            if re.search(patron, n_limpio, re.I):
                indice[canon].append({
                    "nombre": nombre.strip(),
                    "id_xtream": sid,
                })

    metricas = {k: len(v) for k, v in indice.items() if v}
    log.info("Canales lineales Xtream indexados: %s", metricas)
    return indice

def inyectar_eventos_lineales(
    agenda_hoy: List[EventoAgenda],
    indice_canales: Dict[str, List[Dict[str, str]]],
    zona_horaria: str = "America/Bogota"
) -> List[Dict[str, Any]]:
    """
    Cruza los eventos confirmados de la Agenda Maestra con los streams lineales de Xtream.
    Genera la estructura de eventos requerida por Android TV.
    """
    tz_prod = obtener_tz(zona_horaria)
    eventos_inyectados = []

    for ev in agenda_hoy:
        # Buscar streams disponibles para los canales del evento
        fuentes_disponibles = []
        canales_usados = []
        for c in ev.canales:
            canon = c.upper()
            if canon in indice_canales and indice_canales[canon]:
                fuentes_disponibles.extend(indice_canales[canon])
                canales_usados.append(canon)

        if not fuentes_disponibles:
            # No tenemos este canal en la lista Xtream del usuario
            continue

        # Deduplicar fuentes por id_xtream
        vistas = set()
        fuentes_unicas = []
        for f in fuentes_disponibles:
            if f["id_xtream"] not in vistas:
                vistas.add(f["id_xtream"])
                fuentes_unicas.append(f)

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
        hora_slug = hora_local_prod.replace(":", "")
        id_evento = f"lineal_{slug_canal}_{hora_slug}"

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
