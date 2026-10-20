# -*- coding: utf-8 -*-
from __future__ import annotations

import html as html_lib
import logging
import re
import ssl
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Dict, List

from .modelos import EventoAgenda, HEADERS_WEB, calcular_duracion_evento, normalizar_texto, obtener_tz, limpiar_nombre_equipo

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
        # Usar limites de palabra para evitar que 'PROGRAMA' coincida con 'PROGRAMACION'
        if re.search(r'\b' + re.escape(prog) + r'\b', t_u):
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
    canales_slugs = [
        ("EUROSPORT 1", "eurosport-1"),
        ("EUROSPORT 2", "eurosport-2"),
    ]
    eventos = []
    tz_madrid = obtener_tz("Europe/Madrid")

    for canon, slug in canales_slugs:
        # 1. Probar primero con la fecha especifica y luego la URL general de Mundo Deportivo
        urls_md = [
            f"https://www.mundodeportivo.com/guia-tv/canal/{slug}/{fecha_hoy_iso}",
            f"https://www.mundodeportivo.com/guia-tv/canal/{slug}",
        ]
        html = ""
        for u in urls_md:
            h_try = _descargar_html(u)
            if h_try and ("DIRECTO" in h_try.upper() or "prow__t--live" in h_try):
                html = h_try
                break
        if not html:
            html = _descargar_html(urls_md[0]) or _descargar_html(urls_md[1])

        items = re.findall(r"<li[^>]*class=[^>]*prow[^>]*>(.*?)</li>", html, flags=re.S) if html else []
        for item in items:
            # 1. Si la etiqueta visible dice 'Diferido', es repeticion grabada -> descartar
            if re.search(r'>\s*Diferido\s*<', item, re.I):
                continue

            # 2. Quitar los atributos title="..." antes de buscar 'DIRECTO' para no caer en tooltips de diferidos
            item_sin_tooltips = re.sub(r'title="[^"]*"', '', item)
            item_sin_tooltips = re.sub(r"title='[^']*'", '', item_sin_tooltips)
            if "EN DIRECTO" not in item_sin_tooltips.upper() and "prow__t--live" not in item:
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
            if any(k in u_tit for k in ["SNOOKER", "BILLAR", "SHENZHEN", "NORTHERN IRELAND", "UK CHAMPIONSHIP", "MASTERS SNOOKER", "CRUCIBLE", "WST"]):
                dep = "Snooker"
            elif any(k in u_tit for k in ["CICLISMO", "GIRO", "TOUR", "VUELTA", "CROSS COUNTRY", "LAKE PLACID"]):
                dep = "Ciclismo"
            elif any(k in u_tit for k in ["TENIS", "TENNIS", "ATP", "WTA", "ROLAND GARROS", "WIMBLEDON", "DAVIS"]):
                dep = "Tenis"
            elif any(k in u_tit for k in ["PADEL", "PÁDEL", "PREMIER PADEL", "FIP"]):
                dep = "Pádel"
            elif any(k in u_tit for k in ["GOLF", "PGA", "LIV GOLF", "RYDER"]):
                dep = "Golf"
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

    # Complementar con SincroGuia TV de Expansion para asegurar la cobertura 24h
    try:
        dt_h = datetime.fromisoformat(fecha_hoy_iso)
        dias_es = ["lunes", "martes", "mi?rcoles", "jueves", "viernes", "s?bado", "domingo"]
        dia_nom = dias_es[dt_h.weekday()]
        pat_dia_sg = f"{dia_nom} {dt_h.day} de "

        for canon, slug in canales_slugs:
            url_sg = f"https://sincroguia-tv.expansion.com/programacion/{slug}"
            html_sg = _descargar_html(url_sg)
            if not html_sg:
                continue
            idx_d = html_sg.lower().find(pat_dia_sg)
            if idx_d != -1:
                idx_sig = html_sg.lower().find('class="m-schedule-day__title"', idx_d + 30)
                frag = html_sg[idx_d:idx_sig] if idx_sig != -1 else html_sg[idx_d:idx_d+45000]
            else:
                frag = html_sg

            progs_sg = re.findall(r'<div class=[\x22\x27]m-program[\x22\x27][^>]*>(.*?)</div>\s*</div>\s*</div>', frag, flags=re.S)
            for p in progs_sg:
                m_name = re.search(r'<h2 class=[\x22\x27]m-program__name[\x22\x27]>.*?<a[^>]*>([^<]+)</a>', p, flags=re.S)
                m_time = re.search(r'<p class=[\x22\x27]m-program__info[\x22\x27]>\s*([0-9]{1,2}:[0-9]{2})\s*a\s*([0-9]{1,2}:[0-9]{2})h', p)
                m_sub = re.search(r'<p class=[\x22\x27]m-program__title[\x22\x27]>([^<]+)</p>', p)
                if not (m_name and m_time):
                    continue
                tit_sg = html_lib.unescape(m_name.group(1).strip())
                if _es_programa_no_deportivo(tit_sg):
                    continue
                h_ini_str = m_time.group(1)
                h_fin_str = m_time.group(2)
                sub_sg = html_lib.unescape(" ".join(m_sub.group(1).split())) if m_sub else ""

                try:
                    h_i, mi_i = [int(x) for x in h_ini_str.split(":")]
                    d_esp = datetime.fromisoformat(fecha_hoy_iso)
                    if h_i < 6:
                        d_esp += timedelta(days=1)
                    dt_madrid = d_esp.replace(hour=h_i, minute=mi_i, second=0, microsecond=0, tzinfo=tz_madrid)
                    h_utc_sg = dt_madrid.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    continue

                # Evitar duplicar si ya vino por Mundo Deportivo a la misma hora
                if any(e.canales == [canon] and abs((datetime.fromisoformat(e.hora_utc.replace("Z", "+00:00")) - dt_madrid.astimezone(timezone.utc)).total_seconds()) < 1800 for e in eventos):
                    continue

                dep_sg = "Otros Deportes"
                u_tit_sg = f"{tit_sg} {sub_sg}".upper()
                if any(k in u_tit_sg for k in ["SNOOKER", "BILLAR"]):
                    dep_sg = "Snooker"
                elif any(k in u_tit_sg for k in ["CICLISMO", "GIRO", "TOUR", "VUELTA", "MUNDIAL DE CICLISMO"]):
                    dep_sg = "Ciclismo"
                elif any(k in u_tit_sg for k in ["TENIS", "OPEN", "ATP", "WTA"]):
                    dep_sg = "Tenis"

                dur_sg = 180 if dep_sg in ["Snooker", "Ciclismo"] else 120

                ev_sg = EventoAgenda(
                    titulo=tit_sg,
                    deporte=dep_sg,
                    torneo=sub_sg or tit_sg,
                    local="",
                    visitante="",
                    hora_utc=h_utc_sg,
                    canales=[canon],
                    duracion_min=dur_sg,
                    fuente=f"sincroguia_{slug.replace('-', '_')}",
                    tipo_evento="circuito",
                    texto_original_crudo=f"{tit_sg} - {sub_sg} ({h_ini_str} a {h_fin_str} {canon})",
                    canales_mencionados=[canon],
                )
                eventos.append(ev_sg)
    except Exception as e_sg:
        log.warning("Fallo en complemento SincroGuia para Eurosport: %s", e_sg)

    log.info("Eurosport (MundoDeportivo + SincroGuia): %d directos confirmados", len(eventos))
    return eventos

def parse_rtve_teledeporte_title(raw: str) -> Dict[str, str]:
    m_desde = re.search(r"\s+DESDE\s+.*$", raw, re.I)
    sin_desde = raw[:m_desde.start()].strip() if m_desde else raw.strip()
    
    partes = re.split(r"\s*(?:[\u2013\u2014\x96]|\s+[\-]\s+)\s*", sin_desde)
    if len(partes) == 2:
        izq, vis = partes[0].strip(), partes[1].strip()
        sin_dep = re.sub(r"^(?:F[ÚU]TBOL|BALONCESTO|BASKET|BALONMANO|VOLEIBOL|RUGBY|HOCKEY\s+PATINES|HOCKEY|WATERPOLO|B[ÉE]ISBOL)\s+", "", izq, flags=re.I).strip()
        
        pat_fase = r'(?:(?:\d+[ªºa-zA-Z]*\s+)?(?:JORNADA|PARTIDO|FASE|ROUND|MATCH|FECHA|GAME)(?:\s+\d+[ªºa-zA-Z]*)?|FINAL|SEMIFINAL|CUARTOS|OCTAVOS|IDA|VUELTA)'
        m_jornada = re.search(rf'^(.*?)\s+({pat_fase})\s+(.*)$', sin_dep, re.I)
        if m_jornada:
            torneo = f"{m_jornada.group(1)} {m_jornada.group(2)}".strip()
            loc = m_jornada.group(3).strip()
        else:
            palabras_torneo = r'(?:CAMPEONATO\s+(?:DEL\s+)?MUNDO|COPA\s+(?:DEL\s+)?REY|COPA|LIGA|TORNEO|MUNDIAL|EUROCOPA|SUPERCOPA|DIVISION\s+DE\s+HONOR|FASE\s+DE\s+GRUPOS|SUB-\d+(?:\s+FEMENINO|\s+MASCULINO)?|FEMENINO|MASCULINO)'
            m_tor = re.search(rf'^(.*{palabras_torneo})\s+(.+)$', sin_dep, re.I)
            if m_tor:
                torneo = m_tor.group(1).strip()
                loc = m_tor.group(2).strip()
            else:
                torneo = sin_dep
                loc = izq

        loc_puro = limpiar_nombre_equipo(loc)
        vis_puro = limpiar_nombre_equipo(vis)
        return {
            "tipo": "duelo",
            "titulo": f"{loc_puro.title()} vs. {vis_puro.title()}",
            "local": loc_puro.title(),
            "visitante": vis_puro.title(),
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
    # Limpiar pie de pagina generico de RTVE para que no mutile ni contamine los eventos de madrugada
    clean = re.sub(r"Esta programaci[oó]n puede sufrir modificaciones.*", "", clean, flags=re.I)

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
        MANDATORY_SPORTS = [
            ("FÚTBOL", "Fútbol"), ("FUTBOL", "Fútbol"),
            ("BALONCESTO", "Baloncesto"), ("BASKET", "Baloncesto"), ("WNBA", "Baloncesto"), ("NBA", "Baloncesto"), ("ACB", "Baloncesto"),
            ("TENIS", "Tenis"), ("ATP", "Tenis"), ("WTA", "Tenis"),
            ("CICLISMO", "Ciclismo"), ("VUELTA", "Ciclismo"), ("TOUR", "Ciclismo"), ("GIRO", "Ciclismo"),
            ("BALONMANO", "Balonmano"), ("HANDBALL", "Balonmano"),
            ("HOCKEY", "Hockey"),
            ("WATERPOLO", "Waterpolo"),
            ("NATACIÓN", "Natación"), ("NATACION", "Natación"), ("AQUATICS", "Natación"), ("SWIMMING", "Natación"),
            ("REMO", "Remo"), ("ROWING", "Remo"),
            ("VELA", "Vela"), ("SAILING", "Vela"),
            ("PIRAGÜISMO", "Piragüismo"), ("PIRAGUISMO", "Piragüismo"), ("CANOE", "Piragüismo"), ("KAYAK", "Piragüismo"),
            ("ATLETISMO", "Atletismo"), ("ATHLETICS", "Atletismo"),
            ("GIMNASIA", "Gimnasia"), ("GYMNASTICS", "Gimnasia"),
            ("BOXEO", "Combate"), ("BOXING", "Combate"), ("JUDO", "Combate"), ("TAEKWONDO", "Combate"), ("LUCHA", "Combate"),
            ("SNOOKER", "Snooker"), ("BILLAR", "Snooker"),
            ("PÁDEL", "Pádel"), ("PADEL", "Pádel"),
            ("PATINAJE", "Patinaje"), ("SKATING", "Patinaje"),
            ("TRIATLÓN", "Triatlón"), ("TRIATLON", "Triatlón"),
            ("RUGBY", "Rugby"),
            ("BÉISBOL", "Béisbol"), ("BEISBOL", "Béisbol"), ("BASEBALL", "Béisbol"),
            ("MOTOR", "Motor"), ("RALLY", "Motor"), ("MOTOGP", "Motor"),
            ("GOLF", "Golf"),
            ("VOLEIBOL", "Voleibol"), ("VOLLEYBALL", "Voleibol"),
            ("ESGRIMA", "Esgrima"),
            ("TIRO CON ARCO", "Tiro con Arco"), ("ARCHERY", "Tiro con Arco"),
            ("HÍPICA", "Hípica"), ("HIPICA", "Hípica"), ("EQUESTRIAN", "Hípica"),
            ("ESQUÍ", "Esquí"), ("ESQUI", "Esquí"), ("SKI", "Esquí"),
            ("SURF", "Surf"),
            ("ESCALADA", "Escalada"), ("CLIMBING", "Escalada"),
            ("BÁDMINTON", "Bádminton"), ("BADMINTON", "Bádminton"),
            ("CURLING", "Curling"),
            ("TEJO", "Tejo"),
        ]
        for kw, sport_name in MANDATORY_SPORTS:
            if re.search(r'\b' + re.escape(kw) + r'\b', u_tit):
                dep = sport_name
                break

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