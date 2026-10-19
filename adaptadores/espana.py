# -*- coding: utf-8 -*-
from __future__ import annotations

import html as html_lib
import logging
import re
import ssl
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Dict, List

from .modelos import EventoAgenda, HEADERS_WEB, calcular_duracion_evento, normalizar_texto, obtener_tz

log = logging.getLogger("adaptador_espana")

PROGRAMAS_NO_DEPORTIVOS = [
    "ESTADIO 2", "TELEDETALLE", "RESUMEN", "INFORMATIVO", "NOTICIAS", "NOTICIAS TELEDEPORTE",
    "EL DIA DESPUES", "UNIVERSO VALDANO", "PLANETA OLIMPICO", "CONEXION TDP", "ZONA BALONCESTO",
    "PROGRAMA", "PREVIO", "POST", "ESPECIAL", "MAGAZINE", "REPORTAJE",
    "CONTENDER SERIES", "ULTIMATE FIGHTER", "UFC COUNTDOWN", "UFC REWIND", "UFC UNLEASHED"
]

def _es_programa_no_deportivo(titulo: str) -> bool:
    t_u = normalizar_texto(titulo).upper()
    for prog in PROGRAMAS_NO_DEPORTIVOS:
        if prog == t_u or f"{prog}:" in t_u or f"{prog} " in t_u or prog in t_u:
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
            elif any(k in u_tit for k in ["UFC", "COMBATE", "BOXEO"]):
                dep = "Combate"

            try:
                h, mi = [int(x) for x in hora_str.split(":")]
                d_esp = datetime.fromisoformat(fecha_hoy_iso)
                if h < 6:
                    d_esp += timedelta(days=1)
                dt_madrid = d_esp.replace(hour=h, minute=mi, second=0, microsecond=0, tzinfo=tz_madrid)
                hora_utc = dt_madrid.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                continue

            dur_calc = None
            m_fin = re.search(r"termina\s*(?:<!--\s*-->)?\s*([0-9]{1,2}:[0-9]{2})", item, re.I)
            if m_fin:
                try:
                    hf, mf = [int(x) for x in m_fin.group(1).split(":")]
                    hi, mi_ = [int(x) for x in hora_str.split(":")]
                    mins_fin = hf * 60 + mf
                    mins_ini = hi * 60 + mi_
                    if mins_fin < mins_ini:
                        mins_fin += 24 * 60
                    dur_calc = mins_fin - mins_ini
                except Exception:
                    dur_calc = None

            duracion_final = calcular_duracion_evento(dep, titulo, dur_calc)

            ev = EventoAgenda(
                titulo=titulo,
                deporte=dep,
                torneo=titulo,
                local="",
                visitante="",
                hora_utc=hora_utc,
                canales=[canon],
                duracion_min=duracion_final,
                fuente=f"mundodeportivo_{canon.lower().replace(' ', '_')}",
                tipo_evento="circuito",
                texto_original_crudo=f"{titulo} ({hora_str} {canon})",
                canales_mencionados=[canon],
            )
            eventos.append(ev)

    log.info("Eurosport (MundoDeportivo): %d directos confirmados", len(eventos))
    return eventos

def parse_rtve_teledeporte_title(raw: str) -> Dict[str, str]:
    m_desde = re.search(r"\s+DESDE\s+.*$", raw, re.I)
    sin_desde = raw[:m_desde.start()].strip() if m_desde else raw.strip()
    
    partes = re.split(r"\s*(?:[\u2013\u2014\x96]|\s+[\-]\s+)\s*", sin_desde)
    if len(partes) == 2:
        izq, vis = partes[0].strip(), partes[1].strip()
        sin_dep = re.sub(r"^(?:BALONCESTO|BALONMANO|F[ÚU]TBOL|HOCKEY\s+PATINES|HOCKEY|WATERPOLO)\s+", "", izq, flags=re.I).strip()
        m_jornada = re.search(r"^(.*?)\s+(\d+[ªºa-zA-Z]*\s+(?:JORNADA|PARTIDO|FASE)|FINAL|SEMIFINAL)\s+(.*)$", sin_dep, re.I)
        if m_jornada:
            torneo = f"{m_jornada.group(1)} {m_jornada.group(2)}".strip()
            loc = m_jornada.group(3).strip()
        else:
            torneo = sin_dep
            loc = izq

        return {
            "tipo": "duelo",
            "titulo": f"{loc.title()} vs {vis.title()}",
            "local": loc.title(),
            "visitante": vis.title(),
            "torneo": torneo.title() or "Deportes",
        }
    else:
        return {
            "tipo": "circuito",
            "titulo": sin_desde.title(),
            "local": "",
            "visitante": "",
            "torneo": sin_desde.title(),
        }

def obtener_teledeporte_directos(fecha_hoy_iso: str) -> List[EventoAgenda]:
    """Obtiene la programación 100% oficial de Teledeporte directamente de RTVE."""
    tz_madrid = obtener_tz("Europe/Madrid")
    try:
        dt_hoy = datetime.fromisoformat(fecha_hoy_iso)
        dias_semana = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
        dia_str = dias_semana[dt_hoy.weekday()]
    except Exception:
        dia_str = "miercoles"

    url = f"https://www.rtve.es/tve/b/teledeporte/modulos/tdp_table_parrilla.php?dia={dia_str}"
    req = urllib.request.Request(url, headers=HEADERS_WEB)
    try:
        with urllib.request.urlopen(req, timeout=12, context=_crear_contexto_ssl()) as resp:
            raw = resp.read()
            text_html = raw.decode("windows-1252", errors="ignore")
    except Exception as e:
        log.warning("Error descargando parrilla oficial de Teledeporte (RTVE): %s", e)
        return []

    clean = re.sub(r"<[^>]+>", " ", text_html)
    clean = html_lib.unescape(clean)

    matches = re.findall(r"([0-2][0-9]:[0-5][0-9])\s+(.*?)(?=[0-2][0-9]:[0-5][0-9]|$)", clean, flags=re.S)
    eventos = []

    for hora_str, contenido in matches:
        c_clean = " ".join(contenido.split()).strip()
        if "DIRECTO" not in c_clean.upper():
            continue

        raw_item = re.sub(r"\bDIRECTO\b\s*", "", c_clean, flags=re.I).strip()
        if _es_programa_no_deportivo(raw_item) or any(k in raw_item.upper() for k in ["JUEGO DE NACIONES", "ESTADIO 2", "INFORMATIVO", "NOTICIAS", "RUEDA DE PRENSA"]):
            log.info("Descartando programa/informativo oficial de Teledeporte: '%s'", raw_item)
            continue

        u_tit = raw_item.upper()
        dep = "Otros Deportes"
        if any(k in u_tit for k in ["BALONCESTO", "BASKET", "LIGA ENDESA", "ACB", "LIGA U", "WNBA", "NBA"]):
            dep = "Baloncesto"
        elif any(k in u_tit for k in ["BALONMANO", "ASOBAL", "HANDBALL"]):
            dep = "Balonmano"
        elif any(k in u_tit for k in ["HOCKEY PATINES", "HOCKEY"]):
            dep = "Hockey"
        elif any(k in u_tit for k in ["WATERPOLO"]):
            dep = "Waterpolo"
        elif any(k in u_tit for k in ["NATACIÓN", "NATACION", "WATERPOLO", "AQUATICS", "SWIMMING"]):
            dep = "Natación"
        elif any(k in u_tit for k in ["CICLISMO", "VUELTA", "TOUR", "GIRO"]):
            dep = "Ciclismo"
        elif any(k in u_tit for k in ["FÚTBOL", "FUTBOL"]):
            dep = "Fútbol"
        elif any(k in u_tit for k in ["TENIS", "ATP", "WTA"]):
            dep = "Tenis"

        parsed = parse_rtve_teledeporte_title(raw_item)

        try:
            h, mi = [int(x) for x in hora_str.split(":")]
            d_esp = datetime.fromisoformat(fecha_hoy_iso)
            if h < 6:
                d_esp += timedelta(days=1)
            dt_madrid = d_esp.replace(hour=h, minute=mi, second=0, microsecond=0, tzinfo=tz_madrid)
            hora_utc = dt_madrid.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        ev = EventoAgenda(
            titulo=parsed["titulo"],
            deporte=dep,
            torneo=parsed["torneo"],
            local=parsed["local"],
            visitante=parsed["visitante"],
            hora_utc=hora_utc,
            canales=["TELEDEPORTE"],
            duracion_min=120,
            fuente="rtve_teledeporte_oficial",
            tipo_evento=parsed["tipo"],
            texto_original_crudo=raw_item,
        )
        eventos.append(ev)

    log.info("Teledeporte oficial (RTVE): %d directos confirmados", len(eventos))
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

        m_dia = re.search(r"\b([0-9]{1,2})(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", raw_tiempo, re.I)
        if m_dia:
            dia_num = int(m_dia.group(1))
            try:
                dt_obj = datetime.fromisoformat(fecha_hoy_iso)
                if dia_num != dt_obj.day:
                    continue
            except Exception:
                pass

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
            dep = "Motor"
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
            duracion_min=180 if dep in ["Golf", "Motor"] else 120,
            fuente="wheresthematch_skysports",
            tipo_evento="circuito",
        )
        eventos.append(ev)

    log.info("Sky Sports UK (wheresthematch): %d directos confirmados", len(eventos))
    return eventos

def obtener_dazn_f1_directos(fecha_hoy_iso: str) -> List[EventoAgenda]:
    url_deporte = "https://www.futbolenlatv.es/deporte"
    html_dep = _descargar_html(url_deporte)
    if not html_dep:
        return []

    eventos_f1 = []
    tz_madrid = obtener_tz("Europe/Madrid")
    try:
        d_obj = datetime.fromisoformat(fecha_hoy_iso)
        pat_dd_mm = d_obj.strftime("%d/%m")
    except Exception:
        pat_dd_mm = ""

    filas = re.findall(r"<tr[^>]*>.*?</tr>", html_dep, flags=re.S)
    dentro_de_hoy = False

    for f in filas:
        txt_l = re.sub(r"<[^>]+>", " ", f).lower()
        if "cabeceratabla" in f.lower() or ("partidos de hoy" in txt_l and ("/" in txt_l or "-" in txt_l)):
            if (pat_dd_mm and pat_dd_mm in txt_l) or "partidos de hoy" in txt_l:
                dentro_de_hoy = True
                continue
            elif dentro_de_hoy:
                break

        if not dentro_de_hoy:
            continue

        canales_raw = re.findall(r'<li[^>]*title=["\x27]([^"\x27]+)["\x27]', f)
        if not any("DAZN F1" in c.upper() for c in canales_raw) and "DAZN F1" not in f.upper():
            continue

        m_hora = re.search(r'<td class=["\x27]hora\s*["\x27]>\s*([0-9]{1,2}:[0-9]{2})', f)
        if not m_hora:
            continue
        hora_str = m_hora.group(1).strip()

        m_ev = re.search(r'<span class=["\x27]eventoUnico["\x27]>(.*?)</span>', f, flags=re.S)
        if m_ev:
            titulo = html_lib.unescape(" ".join(re.sub(r"<[^>]+>", " - ", m_ev.group(1)).split())).strip(" -")
        else:
            titulo = "Fórmula 1"

        m_comp = re.search(r'<span class=["\x27]ajusteDoslineas["\x27][^>]*title=["\x27]([^"\x27]+)["\x27]', f)
        torneo = html_lib.unescape(m_comp.group(1).strip()) if m_comp else "Fórmula 1"

        try:
            h, mi = [int(x) for x in hora_str.split(":")]
            d_esp = datetime.fromisoformat(fecha_hoy_iso)
            if h < 6:
                d_esp += timedelta(days=1)
            dt_madrid = d_esp.replace(hour=h, minute=mi, second=0, microsecond=0, tzinfo=tz_madrid)
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