# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
import ssl
import urllib.request
from datetime import datetime, timezone
from typing import List

from .modelos import EventoAgenda, HEADERS_WEB, normalizar_texto, obtener_tz

log = logging.getLogger("adaptador_espana")

def _crear_contexto_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def _descargar_html(url: str, timeout: int = 12) -> str:
    req = urllib.request.Request(url, headers=HEADERS_WEB)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_crear_contexto_ssl()) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log.debug("Error descargando %s: %s", url, e)
        return ""

def obtener_movistar_directos(fecha_hoy_iso: str) -> List[EventoAgenda]:
    url = "https://www.movistarplus.es/deportes/programacion/partidos-hoy"
    html = _descargar_html(url)
    if not html:
        return []

    eventos = []
    tz_madrid = obtener_tz("Europe/Madrid")
    
    patron = re.compile(
        r'<div[^>]*class="[^"]*ficha-partido[^"]*"[^>]*>.*?'
        r'<span[^>]*class="[^"]*hora[^"]*"[^>]*>\s*([0-9]{1,2}:[0-9]{2})\s*</span>.*?'
        r'<span[^>]*class="[^"]*deporte[^"]*"[^>]*>\s*([^<]+)\s*</span>.*?'
        r'<h3[^>]*>\s*([^<]+)\s*</h3>.*?'
        r'(?:<div[^>]*class="[^"]*canales[^"]*"[^>]*>(.*?)</div>)?',
        re.S | re.I
    )

    for m in patron.finditer(html):
        hora_str = m.group(1).strip()
        dep = m.group(2).strip()
        titulo = m.group(3).strip()
        canales_raw = m.group(4) or ""

        canales = []
        c_upper = canales_raw.upper()
        if "#VAMOS" in c_upper or "VAMOS" in c_upper:
            canales.append("MOVISTAR #VAMOS")
        if "DEPORTES" in c_upper or "M+ DEPORTES" in c_upper:
            canales.append("MOVISTAR DEPORTES")
        if "LIGA DE CAMPEONES" in c_upper:
            canales.append("M+ LIGA DE CAMPEONES")
        if "LALIGA" in c_upper:
            canales.append("DAZN LALIGA")
        if not canales:
            canales.append("MOVISTAR DEPORTES")

        try:
            h, mi = [int(x) for x in hora_str.split(":")]
            dt_madrid = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_madrid)
            hora_utc = dt_madrid.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        duelo = re.split(r"\s+(?:vs\.?|v\.?|-)\s+", titulo, flags=re.I)
        if len(duelo) == 2:
            loc, vis = duelo[0].strip(), duelo[1].strip()
            tipo = "duelo"
        else:
            loc, vis = "", ""
            tipo = "circuito"

        ev = EventoAgenda(
            titulo=titulo,
            deporte=dep or "Deportes",
            torneo=dep,
            local=loc,
            visitante=vis,
            hora_utc=hora_utc,
            canales=canales,
            duracion_min=120,
            fuente="movistarplus",
            tipo_evento=tipo,
        )
        eventos.append(ev)

    log.info("Movistar Plus: %d directos extraidos", len(eventos))
    return eventos

def obtener_eurosport_directos(fecha_hoy_iso: str) -> List[EventoAgenda]:
    canales_url = [
        ("EUROSPORT 1", "https://www.mundodeportivo.com/guia-tv/canal/eurosport-1"),
        ("EUROSPORT 2", "https://www.mundodeportivo.com/guia-tv/canal/eurosport-2"),
    ]
    eventos = []
    tz_madrid = obtener_tz("Europe/Madrid")

    for canon, url in canales_url:
        html = _descargar_html(url)
        if not html or "EN DIRECTO" not in html:
            continue

        bloques = re.split(r'<article|class="[^"]*tv-item', html)
        for b in bloques:
            if "EN DIRECTO" not in b:
                continue

            m_hora = re.search(r'([0-9]{1,2}:[0-9]{2})', b)
            if not m_hora:
                continue
            hora_str = m_hora.group(1)

            m_tit = re.search(r'<h[2345][^>]*>\s*([^<]+)\s*</h[2345]>', b)
            titulo = m_tit.group(1).strip() if m_tit else "Eurosport Directo"
            titulo = re.sub(r"\s*-\s*EN DIRECTO.*", "", titulo, flags=re.I).strip()

            dep = "Otros Deportes"
            u_tit = titulo.upper()
            if any(k in u_tit for k in ["SNOOKER", "BILLAR"]):
                dep = "Snooker"
            elif any(k in u_tit for k in ["CICLISMO", "GIRO", "TOUR", "VUELTA"]):
                dep = "Ciclismo"
            elif any(k in u_tit for k in ["TENIS", "OPEN", "ATP", "WTA"]):
                dep = "Tenis"
            elif any(k in u_tit for k in ["ESCALADA", "CLIMBING"]):
                dep = "Escalada"

            try:
                h, mi = [int(x) for x in hora_str.split(":")]
                dt_madrid = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_madrid)
                hora_utc = dt_madrid.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                continue

            ev = EventoAgenda(
                titulo=titulo,
                deporte=dep,
                torneo=titulo,
                local="",
                visitante="",
                hora_utc=hora_utc,
                canales=[canon],
                duracion_min=180 if dep in ["Ciclismo", "Tenis", "Snooker"] else 120,
                fuente=f"mundodeportivo_{canon.lower().replace(' ', '_')}",
                tipo_evento="circuito",
            )
            eventos.append(ev)

    log.info("Eurosport (MundoDeportivo): %d directos confirmados", len(eventos))
    return eventos

def obtener_dazn_espana_directos(fecha_hoy_iso: str) -> List[EventoAgenda]:
    url = "https://www.futbolenlatv.es/canal/dazn-spain"
    html = _descargar_html(url)
    if not html:
        return []

    eventos = []
    tz_madrid = obtener_tz("Europe/Madrid")
    
    filas = re.findall(r'<tr[^>]*>.*?</tr>', html, flags=re.S)
    for f in filas:
        if "Ver en directo" not in f and "DAZN" not in f:
            continue
        m_hora = re.search(r'([0-9]{1,2}:[0-9]{2})', f)
        if not m_hora:
            continue
        hora_str = m_hora.group(1)

        partes = re.findall(r'<td[^>]*>\s*([^<]+)\s*</td>', f)
        if len(partes) >= 2:
            torneo = partes[0].strip()
            enfrentamiento = partes[1].strip()
        else:
            torneo = "DAZN Event"
            enfrentamiento = ""

        if not enfrentamiento:
            continue

        duelo = re.split(r"\s+(?:vs\.?|v\.?|-)\s+", enfrentamiento, flags=re.I)
        loc = duelo[0].strip() if len(duelo) == 2 else ""
        vis = duelo[1].strip() if len(duelo) == 2 else ""

        try:
            h, mi = [int(x) for x in hora_str.split(":")]
            dt_madrid = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_madrid)
            hora_utc = dt_madrid.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        ev = EventoAgenda(
            titulo=enfrentamiento,
            deporte="Fútbol",
            torneo=torneo,
            local=loc,
            visitante=vis,
            hora_utc=hora_utc,
            canales=["DAZN 1", "DAZN LALIGA"],
            duracion_min=120,
            fuente="futbolenlatv_dazn",
            tipo_evento="duelo" if loc and vis else "circuito",
        )
        eventos.append(ev)

    log.info("DAZN España (futbolenlatv): %d directos extraidos", len(eventos))
    return eventos
