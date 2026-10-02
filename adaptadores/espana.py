# -*- coding: utf-8 -*-
from __future__ import annotations

import html as html_lib
import logging
import re
import ssl
import urllib.request
from datetime import datetime, timezone
from typing import List

from .modelos import EventoAgenda, HEADERS_WEB, normalizar_texto, obtener_tz

log = logging.getLogger("adaptador_espana")

PROGRAMAS_NO_DEPORTIVOS = [
    "ESTADIO 2", "TELEDETALLE", "RESUMEN", "INFORMATIVO", "NOTICIAS", "NOTICIAS TELEDEPORTE",
    "EL DIA DESPUES", "UNIVERSO VALDANO", "PLANETA OLIMPICO", "CONEXION TDP", "ZONA BALONCESTO",
    "PROGRAMA", "PREVIO", "POST", "ESPECIAL", "MAGAZINE", "REPORTAJE"
]

def _es_programa_no_deportivo(titulo: str) -> bool:
    t_u = normalizar_texto(titulo).upper()
    for prog in PROGRAMAS_NO_DEPORTIVOS:
        if prog == t_u or f"{prog}:" in t_u or f"{prog} " in t_u:
            return True
    return False

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

    for block in html.split("</li>"):
        if "mplus-collection__date" not in block:
            continue
        m_fecha = re.search(r"mplus-collection__date[^>]*>\s*Hoy\s*-\s*([0-9]{1,2}:[0-9]{2})", block)
        if not m_fecha:
            continue
        hora_str = m_fecha.group(1)

        m_url = re.search(r"href=['\"]https://www\.movistarplus\.es/deportes/([^/]+)/([^/]+)/[^/]+/ficha", block)
        m_tit = re.search(r"mplus-collection__title[^>]*>.*?<a[^>]*>(.*?)</a>", block, flags=re.S)
        if not m_tit:
            continue

        titulo_raw = html_lib.unescape(" ".join(re.sub(r"<[^>]+>", "", m_tit.group(1)).split()))
        slug_dep = m_url.group(1).lower() if m_url else "deportes"
        slug_torneo = m_url.group(2).replace("-", " ").title() if m_url else ""

        # En España el snooker/billar se emite por Eurosport, no por Movistar Deportes
        if "snooker" in slug_dep or "billar" in slug_dep or "shenzhen" in slug_torneo.lower():
            continue

        if _es_programa_no_deportivo(titulo_raw):
            continue

        dep = "Otros Deportes"
        if "padel" in slug_dep or "pádel" in slug_dep:
            dep = "Pádel"
        elif "tenis" in slug_dep:
            dep = "Tenis"
        elif "baloncesto" in slug_dep or "basket" in slug_dep:
            dep = "Baloncesto"
        elif "futbol" in slug_dep:
            dep = "Fútbol"
        elif "ciclismo" in slug_dep:
            dep = "Ciclismo"
        elif "golf" in slug_dep:
            dep = "Golf"
        elif "balonmano" in slug_dep or "asobal" in slug_torneo.lower():
            dep = "Balonmano"

        try:
            h, mi = [int(x) for x in hora_str.split(":")]
            dt_madrid = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_madrid)
            hora_utc = dt_madrid.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        duelo = re.split(r"\s+(?:vs\.?|v\.?|-)\s+", titulo_raw, flags=re.I)
        if len(duelo) == 2 and dep in ["Fútbol", "Baloncesto", "Pádel", "Tenis", "Balonmano"]:
            loc, vis = duelo[0].strip(), duelo[1].strip()
            loc = re.sub(r"^(?:Fase de grupos|Jornada \d+|Cuartos de final.*?|2ª Ronda.*?):\s*", "", loc, flags=re.I).strip()
            tipo = "duelo"
        else:
            loc, vis = "", ""
            tipo = "circuito"

        canales = ["MOVISTAR DEPORTES"]
        if dep == "Fútbol":
            canales = ["MOVISTAR LALIGA", "MOVISTAR LIGA DE CAMPEONES", "MOVISTAR DEPORTES"]
        elif dep == "Baloncesto":
            canales = ["MOVISTAR DEPORTES", "MOVISTAR #VAMOS"]

        ev = EventoAgenda(
            titulo=titulo_raw,
            deporte=dep,
            torneo=slug_torneo or dep,
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
        if not html or "DIRECTO" not in html.upper():
            continue

        items = re.findall(r"<li[^>]*class=[^>]*prow[^>]*>(.*?)</li>", html, flags=re.S)
        for item in items:
            if "DIRECTO" not in item.upper():
                continue

            m_hora = re.search(r"([0-9]{1,2}:[0-9]{2})", item)
            m_tit = re.search(r"class=[^>]*prow__title[^>]*>([^<]+)</a>", item)
            if not m_hora or not m_tit:
                continue

            hora_str = m_hora.group(1)
            titulo = html_lib.unescape(m_tit.group(1).strip())
            titulo = re.sub(r"\s*-\s*EN DIRECTO.*", "", titulo, flags=re.I).strip()

            if _es_programa_no_deportivo(titulo):
                continue

            u_tit = titulo.upper()
            if "ASOBAL" in u_tit:
                # La Liga ASOBAL se emite exclusivamente en abierto por Teledeporte en TV lineal en España
                continue

            dep = "Otros Deportes"
            if any(k in u_tit for k in ["SNOOKER", "BILLAR", "SHENZHEN"]):
                dep = "Snooker"
            elif any(k in u_tit for k in ["CICLISMO", "GIRO", "TOUR", "VUELTA", "CROSS COUNTRY", "LAKE PLACID"]):
                dep = "Ciclismo"
            elif any(k in u_tit for k in ["TENIS", "OPEN", "ATP", "WTA"]):
                dep = "Tenis"
            elif any(k in u_tit for k in ["ESCALADA", "CLIMBING"]):
                dep = "Escalada"
            elif any(k in u_tit for k in ["TIRO", "SKEET"]):
                dep = "Tiro"
            elif "BALONMANO" in u_tit:
                dep = "Balonmano"
            elif any(k in u_tit for k in ["UFC", "CONTENDER", "COMBATE", "BOXEO"]):
                dep = "Combate"

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

def obtener_teledeporte_directos(fecha_hoy_iso: str) -> List[EventoAgenda]:
    url = "https://www.mundodeportivo.com/guia-tv/canal/teledeporte"
    html = _descargar_html(url)
    if not html or "DIRECTO" not in html.upper():
        return []

    eventos = []
    tz_madrid = obtener_tz("Europe/Madrid")
    items = re.findall(r"<li[^>]*class=[^>]*prow[^>]*>(.*?)</li>", html, flags=re.S)

    for item in items:
        if "DIRECTO" not in item.upper():
            continue

        m_hora = re.search(r"([0-9]{1,2}:[0-9]{2})", item)
        m_tit = re.search(r"class=[^>]*prow__title[^>]*>([^<]+)</a>", item)
        if not m_hora or not m_tit:
            continue

        hora_str = m_hora.group(1)
        titulo = html_lib.unescape(m_tit.group(1).strip())
        titulo = re.sub(r"\s*-\s*EN DIRECTO.*", "", titulo, flags=re.I).strip()

        # Descartar programas informativos, magazines o no deportivos (ej. Estadio 2)
        if _es_programa_no_deportivo(titulo):
            log.info("Descartando programa no deportivo de Teledeporte: '%s'", titulo)
            continue

        dep = "Otros Deportes"
        u_tit = titulo.upper()
        if any(k in u_tit for k in ["LIGA ENDESA", "BALONCESTO", "ACB", "BASKET", "LIGA U"]):
            dep = "Baloncesto"
        elif any(k in u_tit for k in ["ASOBAL", "BALONMANO"]):
            dep = "Balonmano"
        elif any(k in u_tit for k in ["CICLISMO", "VUELTA"]):
            dep = "Ciclismo"
        elif any(k in u_tit for k in ["NATACION", "SWIMMING", "AQUATICS"]):
            dep = "Natación"
        elif any(k in u_tit for k in ["HIPICA", "CSIO"]):
            dep = "Hípica"
        elif any(k in u_tit for k in ["FUTBOL", "FÚTBOL"]):
            dep = "Fútbol"

        try:
            h, mi = [int(x) for x in hora_str.split(":")]
            dt_madrid = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_madrid)
            hora_utc = dt_madrid.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        duelo = re.split(r"\s+(?:vs\.?|v\.?|-)\s+", titulo, flags=re.I)
        if len(duelo) == 2 and dep in ["Baloncesto", "Balonmano", "Fútbol"]:
            loc, vis = duelo[0].strip(), duelo[1].strip()
            loc = re.sub(r"^(?:Liga\s+[A-Za-z0-9\s]+:)\s*", "", loc, flags=re.I).strip()
            tipo = "duelo"
        else:
            loc, vis = "", ""
            tipo = "circuito"

        ev = EventoAgenda(
            titulo=titulo,
            deporte=dep,
            torneo=titulo,
            local=loc,
            visitante=vis,
            hora_utc=hora_utc,
            canales=["TELEDEPORTE"],
            duracion_min=120,
            fuente="mundodeportivo_teledeporte",
            tipo_evento=tipo,
        )
        eventos.append(ev)

    log.info("Teledeporte: %d directos confirmados", len(eventos))
    return eventos

def obtener_sky_sports_uk_directos(fecha_hoy_iso: str) -> List[EventoAgenda]:
    url = "https://www.wheresthematch.com/live-sport-on-tv/"
    html = _descargar_html(url)
    if not html:
        return []

    eventos = []
    tz_uk = obtener_tz("Europe/London")
    matches = re.findall(r"<tr[^>]*>.*?</tr>", html, flags=re.S)

    for m in matches:
        if "Sky Sports" not in m:
            continue

        tds = re.findall(r"<td[^>]*>(.*?)</td>", m, flags=re.S)
        if len(tds) < 6:
            continue

        raw_detalles = " ".join(re.sub(r"<[^>]+>", " ", tds[1]).split())
        raw_tiempo = " ".join(re.sub(r"<[^>]+>", " ", tds[3]).split())
        raw_comp = " ".join(re.sub(r"<[^>]+>", " ", tds[4]).split())
        raw_canales = " ".join(re.sub(r"<[^>]+>", " ", tds[5]).split())

        if _es_programa_no_deportivo(raw_detalles):
            continue

        m_h = re.search(r"([0-9]{1,2}:[0-9]{2})", raw_tiempo)
        if not m_h:
            continue
        hora_str = m_h.group(1)

        canales = []
        if "Sky Sports F1" in raw_canales:
            canales.append("SKY SPORTS F1")
        if "Sky Sports Main Event" in raw_canales:
            canales.append("SKY SPORTS MAIN EVENT")
        if "Sky Sports Football" in raw_canales or "Sky Sports Premier League" in raw_canales:
            canales.append("SKY SPORTS FOOTBALL")
        if "Sky Sports Golf" in raw_canales:
            canales.append("SKY SPORTS GOLF")

        if not canales:
            continue

        dep = "Otros Deportes"
        u_todo = f"{raw_detalles} {raw_comp}".upper()
        if "F1" in u_todo or "PRACTICE" in u_todo or "GRAND PRIX" in u_todo:
            dep = "Fórmula 1"
        elif "GOLF" in u_todo or "PGA" in u_todo or "DUNHILL" in u_todo or "UTAH" in u_todo:
            dep = "Golf"
        elif "DARTS" in u_todo:
            dep = "Dardos"
        elif "FOOTBALL" in u_todo or "SOCCER" in u_todo:
            dep = "Fútbol"

        try:
            h, mi = [int(x) for x in hora_str.split(":")]
            dt_uk = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_uk)
            hora_utc = dt_uk.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        ev = EventoAgenda(
            titulo=html_lib.unescape(raw_detalles),
            deporte=dep,
            torneo=html_lib.unescape(raw_comp) or dep,
            local="",
            visitante="",
            hora_utc=hora_utc,
            canales=canales,
            duracion_min=180 if dep in ["Golf", "Fórmula 1"] else 120,
            fuente="wheresthematch_skysports",
            tipo_evento="circuito",
        )
        eventos.append(ev)

    log.info("Sky Sports UK (wheresthematch): %d directos confirmados", len(eventos))
    return eventos

def obtener_dazn_espana_directos(fecha_hoy_iso: str) -> List[EventoAgenda]:
    url_deporte = "https://www.futbolenlatv.es/deporte"
    html_dep = _descargar_html(url_deporte)
    if not html_dep:
        return []

    eventos_hoy = []
    tz_madrid = obtener_tz("Europe/Madrid")
    filas = re.findall(r"<tr[^>]*>.*?</tr>", html_dep, flags=re.S)

    for f in filas:
        m_start = re.search(r'itemprop=["\']startDate["\']\s+content=["\']([0-9]{4}-[0-9]{2}-[0-9]{2})', f)
        if not m_start or m_start.group(1) != fecha_hoy_iso:
            continue

        canales_raw = re.findall(r'<li[^>]*title=["\']([^"\']+)["\']', f)
        if not any("DAZN" in c for c in canales_raw):
            continue

        canales_lineales = []
        for c in canales_raw:
            c_u = c.upper()
            if "DAZN LALIGA" in c_u:
                canales_lineales.append("DAZN LALIGA")
            elif "DAZN 1" in c_u and "BAR" not in c_u:
                canales_lineales.append("DAZN 1")
            elif "DAZN 2" in c_u:
                canales_lineales.append("DAZN 2")
            elif "DAZN F1" in c_u:
                canales_lineales.append("DAZN F1")

        if not canales_lineales:
            canales_lineales = ["DAZN 1", "DAZN 2"]

        m_name = re.search(r'itemprop=["\']name["\']\s+content=["\']([^"\']+)["\']', f)
        m_hora = re.search(r'<td class=["\']hora\s*["\']>\s*([0-9]{1,2}:[0-9]{2})', f)
        if not m_name or not m_hora:
            continue

        titulo_partido = html_lib.unescape(m_name.group(1).strip())
        hora_str = m_hora.group(1).strip()

        if _es_programa_no_deportivo(titulo_partido):
            continue

        m_comp = re.search(r'title=["\']([^"\']+)["\']\s+class=["\']js-webp-default["\']', f)
        torneo = html_lib.unescape(m_comp.group(1).strip()) if m_comp else "Deportes"

        duelo = re.split(r"\s+-\s+|\s+vs\.?\s+", titulo_partido)
        loc, vis = (duelo[0].strip(), duelo[1].strip()) if len(duelo) == 2 else ("", "")

        try:
            h, mi = [int(x) for x in hora_str.split(":")]
            dt_madrid = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_madrid)
            hora_utc = dt_madrid.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        dep = "Fútbol"
        u_todo = f"{torneo} {titulo_partido}".upper()
        if any(k in u_todo for k in ["F1", "FÓRMULA 1", "FORMULA 1", "MOTOGP", "RALLY"]):
            dep = "Motor"
            canales_lineales = ["DAZN F1"]

        ev = EventoAgenda(
            titulo=f"{loc} vs {vis}" if loc and vis else titulo_partido,
            deporte=dep,
            torneo=torneo,
            local=loc,
            visitante=vis,
            hora_utc=hora_utc,
            canales=canales_lineales,
            duracion_min=120,
            fuente="futbolenlatv_dazn",
            tipo_evento="duelo" if loc and vis else "circuito",
        )
        eventos_hoy.append(ev)

    log.info("DAZN España (exclusivo hoy): %d directos confirmados", len(eventos_hoy))
    return eventos_hoy



def obtener_dazn_f1_directos(fecha_hoy_iso: str) -> List[EventoAgenda]:
    url_deporte = "https://www.futbolenlatv.es/deporte"
    html_dep = _descargar_html(url_deporte)
    if not html_dep:
        return []

    eventos_f1 = []
    tz_madrid = obtener_tz("Europe/Madrid")
    filas = re.findall(r"<tr[^>]*>.*?</tr>", html_dep, flags=re.S)

    for f in filas:
        m_start = re.search(r'itemprop=["\']startDate["\']\s+content=["\']([0-9]{4}-[0-9]{2}-[0-9]{2})', f)
        if not m_start or m_start.group(1) != fecha_hoy_iso:
            continue

        canales_raw = re.findall(r'<li[^>]*title=["\']([^"\']+)["\']', f)
        if not any("DAZN F1" in c.upper() for c in canales_raw):
            continue

        m_name = re.search(r'itemprop=["\']name["\']\s+content=["\']([^"\']+)["\']', f)
        m_hora = re.search(r'<td class=["\']hora\s*["\']>\s*([0-9]{1,2}:[0-9]{2})', f)
        if not m_name or not m_hora:
            continue

        titulo = html_lib.unescape(m_name.group(1).strip())
        hora_str = m_hora.group(1).strip()

        m_comp = re.search(r'title=["\']([^"\']+)["\']\s+class=["\']js-webp-default["\']', f)
        torneo = html_lib.unescape(m_comp.group(1).strip()) if m_comp else "Fórmula 1"

        try:
            h, mi = [int(x) for x in hora_str.split(":")]
            dt_madrid = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_madrid)
            hora_utc = dt_madrid.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        ev = EventoAgenda(
            titulo=titulo,
            deporte="Motor",
            torneo=torneo,
            local="",
            visitante="",
            hora_utc=hora_utc,
            canales=["DAZN F1"],
            duracion_min=180,
            fuente="futbolenlatv_dazn_f1",
            tipo_evento="circuito",
        )
        eventos_f1.append(ev)

    log.info("DAZN F1: %d directos de Fórmula 1 confirmados", len(eventos_f1))
    return eventos_f1
