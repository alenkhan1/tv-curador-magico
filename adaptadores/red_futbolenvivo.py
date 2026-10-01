# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
import ssl
import urllib.request
from datetime import datetime, timezone
from typing import List
from zoneinfo import ZoneInfo

from .modelos import EventoAgenda, HEADERS_WEB, obtener_tz

log = logging.getLogger("adaptador_futbolenvivo")

def _crear_contexto_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def _descargar_html(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers=HEADERS_WEB)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_crear_contexto_ssl()) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log.debug("Error descargando %s: %s", url, e)
        return ""

def _mapear_canales_texto(texto: str) -> List[str]:
    res = []
    u = texto.upper()
    if "DSPORTS 2" in u or "612" in u:
        res.append("DSPORTS 2")
    elif "DSPORTS +" in u or "1613" in u:
        res.append("DSPORTS +")
    elif "DSPORTS" in u or "610" in u or "DIRECTV" in u:
        res.append("DSPORTS")

    if "WIN SPORTS +" in u or "WIN SPORTS+" in u:
        res.append("WIN SPORTS+")
    elif "WIN SPORTS" in u:
        res.append("WIN SPORTS")

    if "TNT SPORTS" in u:
        res.append("TNT SPORTS")
    if "TYC SPORTS" in u:
        res.append("TYC SPORTS")

    if "ESPN PREMIUM" in u:
        res.append("ESPN PREMIUM ARGENTINA")
    elif "ESPN 5" in u or "ESPN5" in u:
        res.append("ESPN 5")
    elif "ESPN 4" in u or "ESPN4" in u:
        res.append("ESPN 4")
    elif "ESPN 3" in u or "ESPN3" in u:
        res.append("ESPN 3")
    elif "ESPN 2" in u or "ESPN2" in u:
        res.append("ESPN 2")
    elif "ESPN" in u:
        res.append("ESPN")

    if "FOX SPORTS 3" in u:
        res.append("FOX SPORTS 3")
    elif "FOX SPORTS 2" in u:
        res.append("FOX SPORTS 2")
    elif "FOX SPORTS" in u:
        res.append("FOX SPORTS")

    return list(dict.fromkeys(res))

def extraer_partidos_canal(url: str, canal_canonico: str, tz_name: str, fecha_hoy_iso: str) -> List[EventoAgenda]:
    html = _descargar_html(url)
    if not html:
        return []

    eventos = []
    tz = obtener_tz(tz_name)

    filas = re.findall(r'<tr[^>]*>.*?</tr>', html, flags=re.S)
    for f in filas:
        m_hora = re.search(r'([0-9]{1,2}:[0-9]{2})', f)
        if not m_hora:
            continue
        hora_str = m_hora.group(1)

        celdas = re.findall(r'<td[^>]*>(.*?)</td>', f, flags=re.S)
        textos = [re.sub(r'<[^>]+>', '', c).strip() for c in celdas]
        textos = [t for t in textos if t]
        if len(textos) < 2:
            continue

        torneo = textos[0]
        enfrentamiento = textos[1] if len(textos) >= 2 else ""
        if not enfrentamiento or "vs" not in enfrentamiento.lower():
            continue

        duelo = re.split(r"\s+(?:vs\.?|v\.?|-)\s+", enfrentamiento, flags=re.I)
        loc = duelo[0].strip() if len(duelo) == 2 else ""
        vis = duelo[1].strip() if len(duelo) == 2 else ""

        try:
            h, mi = [int(x) for x in hora_str.split(":")]
            dt_local = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz)
            hora_utc = dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        ev = EventoAgenda(
            titulo=enfrentamiento,
            deporte="Fútbol",
            torneo=torneo,
            local=loc,
            visitante=vis,
            hora_utc=hora_utc,
            canales=[canal_canonico],
            duracion_min=120,
            fuente=url.split("/")[-1],
            tipo_evento="duelo",
        )
        eventos.append(ev)

    return eventos

def extraer_multideporte_argentina(deporte_slug: str, deporte_nombre: str, fecha_hoy_iso: str) -> List[EventoAgenda]:
    url = f"https://www.futbolenvivoargentina.com/deporte/{deporte_slug}"
    html = _descargar_html(url)
    if not html:
        return []

    eventos = []
    tz_ar = obtener_tz("America/Argentina/Buenos_Aires")

    filas = re.findall(r'<tr[^>]*>.*?</tr>', html, flags=re.S)
    for f in filas:
        m_hora = re.search(r'([0-9]{1,2}:[0-9]{2})', f)
        if not m_hora:
            continue
        hora_str = m_hora.group(1)

        celdas = re.findall(r'<td[^>]*>(.*?)</td>', f, flags=re.S)
        textos = [re.sub(r'<[^>]+>', '', c).strip() for c in celdas]
        textos = [t for t in textos if t]
        if len(textos) < 3:
            continue

        torneo = textos[0]
        p1 = textos[1]
        p2 = textos[2] if len(textos) >= 3 else ""
        canales_texto = textos[3] if len(textos) >= 4 else f

        canales = _mapear_canales_texto(canales_texto)
        if not canales:
            canales = ["ESPN", "DSPORTS"]

        try:
            h, mi = [int(x) for x in hora_str.split(":")]
            dt_local = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_ar)
            hora_utc = dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        titulo = f"{p1} vs {p2}" if p1 and p2 else p1 or torneo
        ev = EventoAgenda(
            titulo=titulo,
            deporte=deporte_nombre,
            torneo=torneo,
            local=p1,
            visitante=p2,
            hora_utc=hora_utc,
            canales=canales,
            duracion_min=180 if deporte_nombre in ["Tenis", "Béisbol"] else 120,
            fuente=f"multideporte_{deporte_slug}",
            tipo_evento="duelo" if p1 and p2 else "circuito",
        )
        eventos.append(ev)

    log.info("Multideporte %s: %d eventos extraidos", deporte_nombre, len(eventos))
    return eventos
