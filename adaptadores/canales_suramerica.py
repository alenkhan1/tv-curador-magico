# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
import ssl
import urllib.request
from datetime import datetime, timezone
from typing import List

from .modelos import EventoAgenda, HEADERS_WEB, obtener_tz

log = logging.getLogger("canales_suramerica")

CANALES_VERIFICADOS = [
    # Colombia / Región Andina (hora Colombia: America/Bogota)
    ("WIN SPORTS+", "https://www.futbolenvivocolombia.com/canal/win-sports-mas", "America/Bogota", "Liga BetPlay"),
    ("WIN SPORTS", "https://www.futbolenvivocolombia.com/canal/win-sports", "America/Bogota", "Liga BetPlay"),
    ("ESPN", "https://www.futbolenvivocolombia.com/canal/espn-colombia", "America/Bogota", ""),
    ("ESPN 2", "https://www.futbolenvivocolombia.com/canal/espn2-andinon", "America/Bogota", ""),
    ("ESPN 4", "https://www.futbolenvivocolombia.com/canal/espn4-sur", "America/Bogota", ""),
    ("ESPN 5", "https://www.futbolenvivocolombia.com/canal/espn-5-sudamerica", "America/Bogota", ""),
    ("DSPORTS", "https://www.futbolenvivocolombia.com/canal/directv-sports-colombia", "America/Bogota", ""),
    # Argentina / Cono Sur (hora Argentina: America/Argentina/Buenos_Aires)
    ("ESPN", "https://www.futbolenvivoargentina.com/canal/espn-argentina", "America/Argentina/Buenos_Aires", ""),
    ("ESPN 2", "https://www.futbolenvivoargentina.com/canal/espn2-argentina", "America/Argentina/Buenos_Aires", ""),
    ("ESPN 3", "https://www.futbolenvivoargentina.com/canal/espn3-argentina", "America/Argentina/Buenos_Aires", ""),
    ("ESPN 4", "https://www.futbolenvivoargentina.com/canal/espn4-argentina", "America/Argentina/Buenos_Aires", ""),
    ("ESPN PREMIUM ARGENTINA", "https://www.futbolenvivoargentina.com/canal/espn-premium-argentina", "America/Argentina/Buenos_Aires", "Liga Profesional"),
    ("TYC SPORTS", "https://www.futbolenvivoargentina.com/canal/tyc-sports", "America/Argentina/Buenos_Aires", "Copa Argentina"),
    ("TNT SPORTS", "https://www.futbolenvivoargentina.com/canal/tnt-sports", "America/Argentina/Buenos_Aires", "Liga Profesional"),
]

def _crear_contexto_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def extraer_partidos_canal_hoy(canal_nombre: str, url: str, tz_name: str, torneo_default: str, fecha_hoy_iso: str) -> List[EventoAgenda]:
    req = urllib.request.Request(url, headers=HEADERS_WEB)
    try:
        with urllib.request.urlopen(req, timeout=12, context=_crear_contexto_ssl()) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log.warning("No se pudo descargar %s (%s): %s", canal_nombre, url, e)
        return []

    filas = re.findall(r'<tr[^>]*>.*?</tr>', html, flags=re.S)
    dentro_de_hoy = False
    eventos = []
    tz = obtener_tz(tz_name)

    dias_semana = ['mañana', 'sabado', 'sábado', 'domingo', 'lunes', 'martes', 'miercoles', 'miércoles', 'jueves', 'viernes']

    for f in filas:
        txt = ' '.join(re.sub(r'<[^>]+>', ' ', f).split())
        txt_l = txt.lower()

        # Detector de inicio de la sección de hoy
        if 'partidos de hoy' in txt_l:
            dentro_de_hoy = True
            continue

        # Si ya estábamos en hoy y encontramos el siguiente encabezado de fecha, cortamos
        if dentro_de_hoy:
            if any(d in txt_l for d in dias_semana) and ('/' in txt_l or '-' in txt_l):
                break

            # Extraer celdas <td>
            celdas = [re.sub(r'<[^>]+>', ' ', c).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', f, flags=re.S)]
            celdas = [' '.join(c.split()) for c in celdas if c.strip()]
            if len(celdas) < 4:
                continue

            hora_str = celdas[0]
            m_hora = re.search(r'([0-2]?[0-9]:[0-5][0-9])', hora_str)
            if not m_hora:
                continue
            hora_limpia = m_hora.group(1)
            if len(hora_limpia) == 4:
                hora_limpia = "0" + hora_limpia

            competicion = celdas[1] if len(celdas) >= 2 else torneo_default
            local = celdas[2] if len(celdas) >= 3 else ""
            visitante = celdas[3] if len(celdas) >= 4 else ""

            if not local or not visitante:
                continue

            # Convertir hora a ISO 8601 UTC
            try:
                h, mi = [int(x) for x in hora_limpia.split(":")]
                dt_local = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz)
                hora_utc = dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                continue

            ev = EventoAgenda(
                titulo=f"{local} vs {visitante}",
                deporte="Fútbol",
                torneo=competicion or torneo_default or "Fútbol en Vivo",
                local=local,
                visitante=visitante,
                hora_utc=hora_utc,
                canales=[canal_nombre],
                duracion_min=120,
                fuente=url.split("/")[-1],
                tipo_evento="duelo",
            )
            eventos.append(ev)

    log.info("%s (%s): %d eventos confirmados para hoy", canal_nombre, tz_name, len(eventos))
    return eventos

def obtener_directos_suramerica(fecha_hoy_iso: str) -> List[EventoAgenda]:
    """Descarga de forma segura y sin mezclas los partidos de hoy para todos los canales verificados de Suramérica."""
    eventos_totales = []
    for canal_nom, url, tz_nom, tor_def in CANALES_VERIFICADOS:
        evs = extraer_partidos_canal_hoy(canal_nom, url, tz_nom, tor_def, fecha_hoy_iso)
        eventos_totales.extend(evs)
    return eventos_totales
