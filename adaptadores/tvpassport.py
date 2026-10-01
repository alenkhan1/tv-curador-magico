# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
import ssl
import urllib.request
from datetime import datetime, timezone
from typing import List

from .modelos import EventoAgenda, HEADERS_WEB, obtener_tz

log = logging.getLogger("adaptador_tvpassport")

ESTACIONES_USA = [
    ("FS1", "fox-sports-1", 668),
    ("FS2", "fox-sports-2", 2114),
    ("TENNIS CHANNEL", "the-tennis-channel", 2269),
    ("GOLF CHANNEL", "golf-channel-usa", 193),
    ("CBS SPORTS NETWORK", "cbs-sports-network-usa", 3115),
    ("TUDN USA", "tudn", 11145),
    ("USA NETWORK", "usa-network--east-feed", 640),
]

def _crear_contexto_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def extraer_canal_tvpassport(nombre_canal: str, slug: str, station_id: int, fecha_hoy_iso: str) -> List[EventoAgenda]:
    url = f"https://www.tvpassport.com/tv-listings/stations/{slug}/{station_id}"
    req = urllib.request.Request(url, headers=HEADERS_WEB)
    try:
        with urllib.request.urlopen(req, timeout=12, context=_crear_contexto_ssl()) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log.debug("Error descargando tvpassport %s: %s", nombre_canal, e)
        return []

    m_tz = re.search(r'timezone\s*:\s*"([^"]+)"', html)
    tz_str = m_tz.group(1) if m_tz else "America/New_York"
    tz_local = obtener_tz(tz_str)

    eventos = []
    patron_item = re.compile(
        r'<div[^>]*class="[^"]*list-group-item[^"]*"[^>]*data-st="([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})"[^>]*data-live="1"[^>]*data-showType="([^"]*)"[^>]*data-showTitle="([^"]*)"[^>]*>',
        re.I
    )

    for m in patron_item.finditer(html):
        st_raw = m.group(1).strip()
        show_type = m.group(2).strip()
        show_title = m.group(3).strip()

        u_type = show_type.upper()
        u_tit = show_title.upper()
        if any(x in u_type for x in ["TALK SHOW", "NEWS", "MAGAZINE"]) or any(x in u_tit for x in ["PREGAME", "POSTGAME", "THE HERD", "FIRST THINGS FIRST", "BOOMER AND GIO", "GOLF CENTRAL"]):
            continue

        es_duelo = bool(re.search(r"\s+(?:vs\.?|at|v\.?|-)\s+", show_title, re.I))
        if nombre_canal not in ["TENNIS CHANNEL", "GOLF CHANNEL"] and not es_duelo:
            continue

        try:
            dt_local = datetime.fromisoformat(st_raw).replace(tzinfo=tz_local)
            hora_utc = dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        dep = "Otros Deportes"
        if any(k in u_type or k in u_tit for k in ["SOCCER", "FÚTBOL", "FUTBOL", "NATIONS LEAGUE", "CHAMPIONS"]):
            dep = "Fútbol"
        elif any(k in u_type or k in u_tit for k in ["BASKETBALL", "BALONCESTO", "NBA", "WNBA"]):
            dep = "Baloncesto"
        elif any(k in u_type or k in u_tit for k in ["BASEBALL", "BÉISBOL", "BEISBOL", "MLB"]):
            dep = "Béisbol"
        elif any(k in u_type or k in u_tit for k in ["TENNIS", "TENIS"]):
            dep = "Tenis"
        elif any(k in u_type or k in u_tit for k in ["GOLF"]):
            dep = "Golf"

        loc, vis = "", ""
        if es_duelo:
            partes = re.split(r"\s+(?:vs\.?|at|v\.?)\s+", show_title, flags=re.I)
            if len(partes) == 2:
                loc, vis = partes[0].strip(), partes[1].strip()

        ev = EventoAgenda(
            titulo=show_title,
            deporte=dep,
            torneo=show_title if not es_duelo else dep,
            local=loc,
            visitante=vis,
            hora_utc=hora_utc,
            canales=[nombre_canal],
            duracion_min=180 if dep in ["Béisbol", "Golf", "Tenis"] else 120,
            fuente=f"tvpassport_{slug}",
            tipo_evento="duelo" if es_duelo else "circuito",
        )
        eventos.append(ev)

    log.info("tvpassport %s: %d directos filtrados", nombre_canal, len(eventos))
    return eventos

def obtener_directos_usa(fecha_hoy_iso: str) -> List[EventoAgenda]:
    todos = []
    for canon, slug, sid in ESTACIONES_USA:
        todos.extend(extraer_canal_tvpassport(canon, slug, sid, fecha_hoy_iso))
    return todos
