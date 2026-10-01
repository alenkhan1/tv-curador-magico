# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
import ssl
import urllib.request
from datetime import datetime, timezone
from typing import List

from .modelos import EventoAgenda, HEADERS_WEB, obtener_tz

log = logging.getLogger("adaptador_skysports")

CANALES_SKY = [
    ("SKY SPORTS MAIN EVENT", "sky-sports-main-event"),
    ("SKY SPORTS FOOTBALL", "sky-sports-football"),
    ("SKY SPORTS F1", "sky-sports-f1"),
    ("SKY SPORTS GOLF", "sky-sports-golf"),
]

def _crear_contexto_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def obtener_directos_sky_sports(fecha_hoy_iso: str) -> List[EventoAgenda]:
    eventos = []
    tz_uk = obtener_tz("Europe/London")

    for canon, slug in CANALES_SKY:
        url = f"https://www.tvguide.co.uk/channel/{slug}"
        req = urllib.request.Request(url, headers=HEADERS_WEB)
        try:
            with urllib.request.urlopen(req, timeout=12, context=_crear_contexto_ssl()) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            log.debug("Error descargando Sky Sports %s: %s", slug, e)
            continue

        patron = re.compile(
            r'<a[^>]*href="[^"]*programme/([^"]*live-[^"]*)"[^>]*>.*?'
            r'(?:([0-9]{1,2}:[0-9]{2}))?.*?'
            r'<h[2345][^>]*>\s*([^<]+)\s*</h[2345]>',
            re.S | re.I
        )

        for m in patron.finditer(html):
            prog_slug = m.group(1)
            hora_str = m.group(2) or ""
            titulo = m.group(3).strip()

            if not hora_str:
                m2 = re.search(r'([0-9]{1,2}:[0-9]{2})', m.group(0))
                hora_str = m2.group(1) if m2 else ""

            if not hora_str:
                continue

            try:
                h, mi = [int(x) for x in hora_str.split(":")]
                dt_uk = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_uk)
                hora_utc = dt_uk.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                continue

            ev = EventoAgenda(
                titulo=titulo,
                deporte="Motor" if "f1" in slug else ("Golf" if "golf" in slug else "Fútbol"),
                torneo=titulo,
                local="",
                visitante="",
                hora_utc=hora_utc,
                canales=[canon],
                duracion_min=180 if "golf" in slug or "f1" in slug else 120,
                fuente="tvguide_sky",
                tipo_evento="circuito",
            )
            eventos.append(ev)

    log.info("Sky Sports UK: %d directos extraidos", len(eventos))
    return eventos
