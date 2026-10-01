# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
import ssl
import urllib.request
from datetime import datetime, timezone
from typing import List

from .modelos import EventoAgenda, HEADERS_WEB, obtener_tz

log = logging.getLogger("adaptador_mitv")

def _crear_contexto_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def obtener_directos_win_sports(fecha_hoy_iso: str) -> List[EventoAgenda]:
    canales = [
        ("WIN SPORTS+", "https://mi.tv/co/async/channel/win-sports-hd/-300"),
        ("WIN SPORTS", "https://mi.tv/co/async/channel/win-sports/-300"),
    ]
    eventos = []
    tz_col = obtener_tz("America/Bogota")

    for canon, url in canales:
        req = urllib.request.Request(url, headers=HEADERS_WEB)
        try:
            with urllib.request.urlopen(req, timeout=10, context=_crear_contexto_ssl()) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            log.debug("Error descargando %s: %s", url, e)
            continue

        items = re.findall(r'<li[^>]*class="[^"]*program[^"]*"[^>]*>.*?</li>', html, flags=re.S)
        if not items:
            items = re.findall(r'<div[^>]*class="[^"]*program[^"]*"[^>]*>.*?</div>', html, flags=re.S)

        for item in items:
            m_hora = re.search(r'([0-9]{1,2}:[0-9]{2})', item)
            if not m_hora:
                continue
            hora_str = m_hora.group(1)

            m_tit = re.search(r'<h[2345][^>]*>\s*([^<]+)\s*</h[2345]>', item)
            if not m_tit:
                continue
            titulo = m_tit.group(1).strip()

            u_tit = titulo.upper()
            if any(k in u_tit for k in ["NOTICIAS", "PRIMER TOQUE", "SAQUE LARGO", "LINEA DE 4", "SHOW", "LO MEJOR", "RESUMEN"]):
                continue

            duelo = re.split(r"\s+(?:vs\.?|v\.?|-)\s+", titulo, flags=re.I)
            if len(duelo) == 2:
                loc, vis = duelo[0].strip(), duelo[1].strip()
                tipo = "duelo"
            else:
                loc, vis = "", ""
                tipo = "circuito"

            try:
                h, mi = [int(x) for x in hora_str.split(":")]
                dt_local = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_col)
                hora_utc = dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                continue

            ev = EventoAgenda(
                titulo=titulo,
                deporte="Fútbol",
                torneo="Liga BetPlay" if canon == "WIN SPORTS+" else "Torneo BetPlay",
                local=loc,
                visitante=vis,
                hora_utc=hora_utc,
                canales=[canon],
                duracion_min=120,
                fuente="mitv_colombia",
                tipo_evento=tipo,
            )
            eventos.append(ev)

    log.info("Win Sports (mi.tv): %d eventos detectados", len(eventos))
    return eventos
