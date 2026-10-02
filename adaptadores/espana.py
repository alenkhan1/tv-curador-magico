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
        elif "snooker" in slug_dep or "billar" in slug_dep:
            dep = "Snooker"
        elif "golf" in slug_dep:
            dep = "Golf"

        try:
            h, mi = [int(x) for x in hora_str.split(":")]
            dt_madrid = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_madrid)
            hora_utc = dt_madrid.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        duelo = re.split(r"\s+(?:vs\.?|v\.?|-)\s+", titulo_raw, flags=re.I)
        if len(duelo) == 2 and dep in ["Fútbol", "Baloncesto", "Pádel", "Tenis"]:
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

            dep = "Otros Deportes"
            u_tit = titulo.upper()
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
            elif any(k in u_tit for k in ["ASOBAL", "BALONMANO"]):
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

def obtener_dazn_espana_directos(fecha_hoy_iso: str) -> List[EventoAgenda]:
    """
    Extrae eventos de DAZN y multideporte limitados estrictamente a la fecha de hoy.
    """
    from .canales_suramerica import extraer_partidos_canal_hoy

    # Extraer partidos de DAZN España de hoy
    eventos_hoy = extraer_partidos_canal_hoy("DAZN 1", "https://www.futbolenlatv.es/canal/dazn-spain", "Europe/Madrid", "", fecha_hoy_iso)
    for ev in eventos_hoy:
        ev.canales = ["DAZN 1", "DAZN 2"]
        if "LALIGA" in ev.torneo.upper():
            ev.canales = ["DAZN LALIGA"]

    # Extraer eventos de motor / F1 desde la URL multideporte
    html_dep = _descargar_html("https://www.futbolenlatv.es/deporte")
    if html_dep:
        tz_madrid = obtener_tz("Europe/Madrid")
        # Fecha en formato DD/MM/YYYY
        partes_f = fecha_hoy_iso.split("-")
        fecha_patron = f"{partes_f[2]}/{partes_f[1]}/{partes_f[0]}"

        # Segmentar la tabla por días
        bloques_dias = re.split(r'<tr[^>]*>\s*<td[^>]*colspan=[^>]*>.*?([0-3]?[0-9]/[0-1]?[0-9]/[0-9]{4}).*?</td>\s*</tr>', html_dep, flags=re.S | re.I)
        
        bloque_hoy = ""
        for i in range(1, len(bloques_dias), 2):
            if bloques_dias[i].strip() == fecha_patron:
                bloque_hoy = bloques_dias[i+1]
                break

        if bloque_hoy:
            filas = re.findall(r"<tr[^>]*>.*?</tr>", bloque_hoy, flags=re.S)
            for f in filas:
                if "DAZN" not in f and "Ver en directo" not in f:
                    continue
                m_h = re.search(r"([0-9]{1,2}:[0-9]{2})", f)
                if not m_h:
                    continue
                hora_str = m_h.group(1)

                tds = re.findall(r"<td[^>]*>(.*?)</td>", f, flags=re.S)
                celdas = [" ".join(re.sub(r"<[^>]+>", " ", td).split()) for td in tds]
                if len(celdas) < 4:
                    continue

                torneo_raw = html_lib.unescape(celdas[1])
                enfrentamiento_raw = html_lib.unescape(celdas[2])
                u_todo = f"{torneo_raw} {enfrentamiento_raw}".upper()

                dep = "Motor"
                canales = ["DAZN 1", "DAZN 2"]
                if any(k in u_todo for k in ["FÓRMULA 1", "FORMULA 1", "F1", "LIBRES 1", "LIBRES 2", "LIBRES 3"]):
                    dep = "Fórmula 1"
                    canales = ["DAZN F1", "SKY SPORTS F1"]
                elif any(k in u_todo for k in ["MOTOGP", "RALLY", "WRC"]):
                    dep = "Motor"

                try:
                    h, mi = [int(x) for x in hora_str.split(":")]
                    dt_madrid = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_madrid)
                    hora_utc = dt_madrid.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    continue

                ev = EventoAgenda(
                    titulo=f"{torneo_raw}: {enfrentamiento_raw}" if dep == "Fórmula 1" else enfrentamiento_raw,
                    deporte=dep,
                    torneo=torneo_raw,
                    local="",
                    visitante="",
                    hora_utc=hora_utc,
                    canales=canales,
                    duracion_min=120,
                    fuente="futbolenlatv_deporte",
                    tipo_evento="circuito",
                )
                eventos_hoy.append(ev)

    log.info("DAZN España (exclusivo hoy): %d directos confirmados", len(eventos_hoy))
    return eventos_hoy
