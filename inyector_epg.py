#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
INYECTOR EPG POLIDEPORTIVO (TheSportsDB API + Eurosport 1/2 + Teledeporte)
========================================================================
1. API como Fuente de la Verdad: Consulta los eventos oficiales de hoy en TheSportsDB.
2. Cruce Inteligente EPG: Valida emisiones en Eurosport 1, Eurosport 2 y Teledeporte.
3. Cero Repeticiones: Descarta enlatados y emisiones de jornadas no activas hoy.
4. Soporte Polideportivo Enriquecido: Snooker, Atletismo, Ciclismo, Gimnasia, Motor, etc.
"""

import os
import io
import re
import json
import hashlib
import logging
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
import requests

# ─── LOGGING ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("inyector_polideportivo")

# ─── CONFIGURACIÓN DE ENTORNO ───────────────────────────────────────────────
XTREAM_URL = os.environ.get("XTREAM_URL")
XTREAM_USER = os.environ.get("XTREAM_USER")
XTREAM_PASS = os.environ.get("XTREAM_PASS")
PUENTE_URL = os.environ.get("PUENTE_URL", "https://mi-dashboard-tv.onrender.com/api/puente_xtream")

ARCHIVO_EVENTOS = "eventos_hoy.json"
URL_EPG_EUROPA = "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml"
THESPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json/3"

# ─── LOGOS OFICIALES THE SPORTS DB (HTTP 200 VERIFICADOS) ────────────────────
LOGOS_DISCIPLINAS = {
    "Snooker": "https://r2.thesportsdb.com/images/media/league/badge/0gmkgj1555600537.png",
    "Atletismo": "https://r2.thesportsdb.com/images/media/league/badge/9rr2ad1741364834.png",
    "Ciclismo": "https://r2.thesportsdb.com/images/media/league/badge/igahc11535183469.png",
    "Motor": "https://r2.thesportsdb.com/images/media/league/badge/64f67s1770108650.png",
    "Gimnasia": "https://r2.thesportsdb.com/images/media/league/badge/9rr2ad1741364834.png",
    "Natación": "https://r2.thesportsdb.com/images/media/league/badge/9rr2ad1741364834.png",
    "Tenis": "https://r2.thesportsdb.com/images/media/league/badge/1adg221775893214.png",
    "Combate": "https://r2.thesportsdb.com/images/media/league/badge/x9cxmq1578222615.png",
    "Triatlón": "https://r2.thesportsdb.com/images/media/league/badge/igahc11535183469.png",
    "Otros Deportes": "https://r2.thesportsdb.com/images/media/league/badge/0gmkgj1555600537.png"
}

VETO_EXPLICITO = [
    "REPETICION", "REPETICIÓN", "RESUMEN", "HIGHLIGHTS", "NOTICIAS", "NEWS",
    "MAGAZINE", "MEMORIAS", "VINTAGE", "CLASICOS", "CLÁSICOS", "ESPECIAL",
    "LO MEJOR DE", "PROGRAMACION", "PROGRAMACIÓN", "SPORT CENTER", "SPORTSCENTER"
]


def normalizar_texto(texto: str) -> str:
    """Homogeniza cadenas eliminando tildes y caracteres especiales."""
    if not texto:
        return ""
    t = str(texto).upper()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^A-Z0-9\s·\-\:\/]", " ", t)
    return " ".join(t.split())


def parse_timestamp_epg(time_str: str) -> datetime:
    """Convierte cadenas horarias del EPG (notación estándar o exponencial)."""
    if not time_str:
        return None
    partes = time_str.strip().split()
    ts_num = partes[0]
    offset_str = partes[1] if len(partes) > 1 else "+0000"

    if "e+" in ts_num.lower():
        try:
            val = float(ts_num)
            ts_digits = f"{int(round(val)):014d}"
        except Exception:
            return None
    else:
        ts_digits = re.sub(r"\D", "", ts_num).ljust(14, "0")[:14]

    try:
        dt_naive = datetime.strptime(ts_digits, "%Y%m%d%H%M%S")
        if len(offset_str) >= 5:
            sign = 1 if offset_str[0] == "+" else -1
            h, m = int(offset_str[1:3]), int(offset_str[3:5])
            tz = timezone(timedelta(hours=sign * h, minutes=sign * m))
        else:
            tz = timezone.utc
        return dt_naive.replace(tzinfo=tz).astimezone(timezone.utc)
    except Exception:
        return None


def calcular_ventana_hoy_espana():
    """Calcula el rango 'hoy' en huso español (CEST UTC+2)."""
    ahora_utc = datetime.now(timezone.utc)
    tz_espana = timezone(timedelta(hours=2))
    hoy_espana = ahora_utc.astimezone(tz_espana).date()
    inicio_utc = datetime.combine(hoy_espana, datetime.min.time(), tzinfo=tz_espana).astimezone(timezone.utc)
    fin_utc = datetime.combine(hoy_espana, datetime.max.time(), tzinfo=tz_espana).astimezone(timezone.utc)
    return inicio_utc, fin_utc, hoy_espana.strftime("%Y-%m-%d")


def obtener_agenda_thesportsdb(fecha_str: str) -> list:
    """Descarga los eventos oficiales programados para hoy desde TheSportsDB."""
    deportes = ["Snooker", "Athletics", "Cycling", "Motorsport", "Gymnastics", "Golf", "Fighting", "Tennis"]
    eventos_tsdb = []

    for dep in deportes:
        url = f"{THESPORTSDB_BASE}/eventsday.php?d={fecha_str}&s={dep}"
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200 and res.text:
                evs = res.json().get("events") or []
                for e in evs:
                    eventos_tsdb.append({
                        "id": e.get("idEvent"),
                        "nombre": e.get("strEvent", ""),
                        "deporte": dep,
                        "torneo": e.get("strLeague", ""),
                        "badge": e.get("strLeagueBadge") or LOGOS_DISCIPLINAS.get(dep, LOGOS_DISCIPLINAS["Otros Deportes"]),
                        "ronda": e.get("strRound", ""),
                        "resultados_o_cruces": e.get("strResult", "")
                    })
        except Exception as e:
            log.warning(f"Error consultando TheSportsDB ({dep}): {e}")

    log.info(f"TheSportsDB: {len(eventos_tsdb)} eventos oficiales programados para hoy ({fecha_str})")
    return eventos_tsdb


def clasificar_disciplina(texto: str) -> tuple:
    """Clasifica el deporte y el logo correspondiente según palabras clave."""
    t_norm = normalizar_texto(texto)
    categoria = "Otros Deportes"

    if any(k in t_norm for k in ["SNOOKER", "BILLAR", "CHINA OPEN"]):
        categoria = "Snooker"
    elif any(k in t_norm for k in ["ATLETISMO", "ATHLETICS", "JABALINA", "MARATON", "PERTIGA", "VELOCIDAD", "DECATLON"]):
        categoria = "Atletismo"
    elif any(k in t_norm for k in ["CICLISMO", "CYCLING", "ARCTIC RACE", "VUELTA", "TOUR", "GIRO", "ETAPA"]):
        categoria = "Ciclismo"
    elif any(k in t_norm for k in ["GIMNASIA", "RITMICA", "ALL AROUND"]):
        categoria = "Gimnasia"
    elif any(k in t_norm for k in ["NATACION", "ACUATICOS", "SINCRONIZADOS", "SALTOS"]):
        categoria = "Natación"
    elif any(k in t_norm for k in ["FORMULA E", "MOTOGP", "SUPERBIKE", "RALLY", "WRC", "F1", "MOTOR"]):
        categoria = "Motor"
    elif any(k in t_norm for k in ["TENIS", "TENNIS", "ROLAND GARROS", "WIMBLEDON", "US OPEN", "ATP", "WTA"]):
        categoria = "Tenis"
    elif any(k in t_norm for k in ["TRIATLON", "TRIATHLON", "T100", "IRONMAN"]):
        categoria = "Triatlón"
    elif any(k in t_norm for k in ["BOXEO", "UFC", "MMA", "COMBATE"]):
        categoria = "Combate"

    logo = LOGOS_DISCIPLINAS.get(categoria, LOGOS_DISCIPLINAS["Otros Deportes"])
    return categoria, logo


def procesar_programa_epg(titulo_raw: str, tsdb_agenda: list) -> dict:
    """Evalúa la validez del programa contra la agenda oficial de TheSportsDB."""
    if not titulo_raw or len(titulo_raw.strip()) < 5:
        return None

    t_norm = normalizar_texto(titulo_raw)

    # 1. Filtro de vetos absolutos
    if any(v in t_norm for v in VETO_EXPLICITO):
        return None
    if re.search(r"\bE\d+\b", t_norm) or re.search(r"\bEPISODIO\s*\d+\b", t_norm):
        return None

    categoria, logo_defecto = clasificar_disciplina(titulo_raw)
    es_directo_etiqueta = bool(re.search(r"\b(DIRECTO|VIVO|LIVE|EN DIRECTO|EN VIVO)\b", titulo_raw, re.I))

    # 2. Cruce con TheSportsDB: ¿Existe coincidencia activa hoy?
    match_tsdb = None
    for ev in tsdb_agenda:
        dep_api = ev["deporte"].upper()
        ev_norm = normalizar_texto(ev["nombre"] + " " + ev["torneo"])

        # Match por disciplina o palabras clave del torneo
        if categoria.upper() == dep_api or (categoria == "Motor" and dep_api == "MOTORSPORT"):
            palabras_epg = set(t_norm.split())
            palabras_api = set(ev_norm.split())
            comunes = palabras_epg.intersection(palabras_api)
            if len(comunes) >= 2 or any(k in t_norm for k in ["CHINA OPEN", "EUROPEO DE ATLETISMO", "GIMNASIA"]):
                match_tsdb = ev
                break

    # 3. Decisión de admisión:
    # Entra si tiene marca explícita de DIRECTO o si está confirmado en la agenda de TheSportsDB
    if not es_directo_etiqueta and not match_tsdb:
        return None

    # 4. Limpieza del título para la UI
    t_limpio = re.sub(r"\[COLOR.*?\]", "", titulo_raw, flags=re.I)
    t_limpio = re.sub(r"\[/COLOR\]", "", t_limpio, flags=re.I)
    t_limpio = re.sub(r"(?i)\b(DIRECTO|VIVO|LIVE|EN DIRECTO|EN VIVO)\b", "", t_limpio).strip(" -:·")
    t_limpio = " ".join(t_limpio.split())

    torneo = t_limpio
    subtitulo = ""

    if "·" in t_limpio:
        partes = [p.strip() for p in t_limpio.split("·") if p.strip()]
        if len(partes) >= 2:
            p1, p2 = partes[0], partes[1]
            indicadores = ["DIA", "DÍA", "ETAPA", "STAGE", "SESION", "SESIÓN", "FINAL", "SEMIFINAL", "CUARTOS", "CARRERA"]
            if any(ind in normalizar_texto(p1) for ind in indicadores) and not any(ind in normalizar_texto(p2) for ind in indicadores):
                torneo, subtitulo = p2, p1
            else:
                torneo, subtitulo = p1, p2
    elif " - " in t_limpio:
        partes = [p.strip() for p in t_limpio.split(" - ") if p.strip()]
        if len(partes) >= 2:
            torneo, subtitulo = partes[0], " - ".join(partes[1:])
    elif ":" in t_limpio:
        partes = [p.strip() for p in t_limpio.split(":") if p.strip()]
        if len(partes) >= 2:
            torneo, subtitulo = partes[0], ": ".join(partes[1:])

    # Limpieza de etiquetas de temporada
    torneo = re.sub(r"\bT\d{2,4}(/\d{2})?\b", "", torneo).strip(" -:·")
    torneo = " ".join(torneo.split()) or t_limpio

    subtitulo = re.sub(r"\bT\d{2,4}(/\d{2})?\b", "", subtitulo).strip(" -:·")
    subtitulo = " ".join(subtitulo.split()) or categoria

    # Si hay detalles en TheSportsDB, enriquecemos el subtítulo
    if match_tsdb and match_tsdb.get("resultados_o_cruces"):
        cruces = [c.strip() for c in match_tsdb["resultados_o_cruces"].split("\r\n") if c.strip()]
        if cruces:
            subtitulo = f"{subtitulo} ({cruces[0]})" if subtitulo != categoria else cruces[0]

    titulo_final = f"{torneo} · {subtitulo}" if subtitulo != categoria else torneo
    logo_final = match_tsdb["badge"] if match_tsdb and match_tsdb.get("badge") else logo_defecto

    return {
        "categoria": categoria,
        "torneo": torneo,
        "subtitulo": subtitulo,
        "titulo": titulo_final,
        "logo": logo_final
    }


def mapear_streams_por_canal() -> dict:
    """Mapea fuentes de reproducción para Eurosport 1, Eurosport 2 y Teledeporte."""
    mapa = {"E1": [], "E2": [], "TDP": []}
    if not XTREAM_URL or not XTREAM_USER or not XTREAM_PASS:
        return mapa

    try:
        base_url = XTREAM_URL.rstrip("/")
        api_url = f"{base_url}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
        r = requests.get(PUENTE_URL, params={"url": api_url}, timeout=45)
        if r.status_code != 200:
            log.warning(f"Error obteniendo streams de Xtream: HTTP {r.status_code}")
            return mapa

        streams = r.json()
        for s in streams:
            nombre = (s.get("name") or "").upper()
            sid = str(s.get("stream_id"))
            if not sid:
                continue

            fuente = {"nombre": s.get("name"), "id_xtream": sid}

            # Teledeporte
            if any(k in nombre for k in ["TELEDEPORTE", " TDP ", "TDP HD", "TDP FHD"]):
                mapa["TDP"].append(fuente)
            # Eurosport 2
            elif any(k in nombre for k in ["EUROSPORT 2", "EUROSPORT2", "EUROSPORTS 2", "ES2"]):
                mapa["E2"].append(fuente)
            # Eurosport 1
            elif any(k in nombre for k in ["EUROSPORT 1", "EUROSPORT1", "EUROSPORTS 1", "ES1", "EUROSPORT HD"]):
                mapa["E1"].append(fuente)

        log.info(f"Mapeo de señales: {len(mapa['E1'])} en Eurosport 1 | {len(mapa['E2'])} en Eurosport 2 | {len(mapa['TDP'])} en Teledeporte")
    except Exception as e:
        log.error(f"Error procesando streams: {e}")

    return mapa


def extraer_eventos_epg(mapa_streams: dict, inicio_hoy_utc: datetime, fin_hoy_utc: datetime, tsdb_agenda: list) -> list:
    """Descarga el EPG y procesa los eventos confirmados de la jornada."""
    log.info("Descargando EPG Europa (EPG_dobleM)...")
    eventos_aprobados = []

    try:
        r = requests.get(URL_EPG_EUROPA, timeout=25)
        if r.status_code != 200:
            log.error(f"No se pudo descargar el EPG: HTTP {r.status_code}")
            return []

        it = ET.iterparse(io.BytesIO(r.content), events=("end",))
        for _, elem in it:
            if elem.tag == "programme":
                cid = elem.attrib.get("channel", "")
                canal_clave = None

                if cid in ["Eurosport 1 HD", "Eurosport 1"]:
                    canal_clave = "E1"
                elif cid in ["Eurosport 2", "Eurosport 2 HD"]:
                    canal_clave = "E2"
                elif cid in ["Teledeporte", "Teledeporte HD", "TDP"]:
                    canal_clave = "TDP"

                if canal_clave:
                    dt_inicio = parse_timestamp_epg(elem.attrib.get("start", ""))
                    if dt_inicio and (inicio_hoy_utc <= dt_inicio <= fin_hoy_utc):
                        tit_raw = elem.findtext("title", "")
                        info_ev = procesar_programa_epg(tit_raw, tsdb_agenda)

                        if info_ev:
                            fuentes_asignadas = mapa_streams.get(canal_clave, [])
                            if fuentes_asignadas:
                                id_base = f"{info_ev['titulo'].upper()}|{dt_inicio.strftime('%Y%m%d%H%M')}|{canal_clave}"
                                id_unico = f"epg_{hashlib.md5(id_base.encode('utf-8')).hexdigest()[:12]}"

                                eventos_aprobados.append({
                                    "id": id_unico,
                                    "titulo": info_ev["titulo"],
                                    "torneo": info_ev["torneo"],
                                    "categoria": info_ev["categoria"],
                                    "tipo_evento": "sencillo",
                                    "equipo_local": "",
                                    "equipo_visitante": "",
                                    "subtitulo": info_ev["subtitulo"],
                                    "hora_utc": dt_inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "duracion_min": 120,
                                    "logo_torneo": info_ev["logo"],
                                    "logo_local": info_ev["logo"],
                                    "logo_visitante": "",
                                    "tier": 2,
                                    "fuentes": list(fuentes_asignadas)
                                })
                elem.clear()
    except Exception as e:
        log.error(f"Error procesando EPG: {e}")

    # Deduplicación cronológica (solo la primera emisión por evento/sesión)
    unicos = {}
    for ev in eventos_aprobados:
        k = (ev["torneo"], ev["subtitulo"])
        if k not in unicos:
            unicos[k] = ev
        else:
            # Conservamos la emisión más temprana
            if ev["hora_utc"] < unicos[k]["hora_utc"]:
                unicos[k] = ev

    log.info(f"Eventos polideportivos aprobados para hoy: {len(unicos)}")
    return list(unicos.values())


def main():
    log.info("=== Iniciando Inyector Polideportivo (TheSportsDB API + Eurosport + Teledeporte) ===")

    if not os.path.exists(ARCHIVO_EVENTOS):
        log.error(f"No se encontró {ARCHIVO_EVENTOS}. Ejecuta primero curador_eventos.py.")
        return

    try:
        with open(ARCHIVO_EVENTOS, "r", encoding="utf-8") as f:
            data_salida = json.load(f)
    except Exception as e:
        log.error(f"Error leyendo {ARCHIVO_EVENTOS}: {e}")
        return

    eventos_existentes = data_salida.get("eventos", []) if isinstance(data_salida, dict) else data_salida

    # 1. Ventana de Hoy en España
    inicio_hoy_utc, fin_hoy_utc, fecha_str = calcular_ventana_hoy_espana()
    log.info(f"Ventana 'Hoy': {inicio_hoy_utc.isoformat()} -> {fin_hoy_utc.isoformat()} ({fecha_str})")

    # 2. Consultar agenda de TheSportsDB
    tsdb_agenda = obtener_agenda_thesportsdb(fecha_str)

    # 3. Mapeo de canales Xtream
    mapa_streams = mapear_streams_por_canal()

    # 4. Extracción de eventos del EPG cruzados con la API
    eventos_epg = extraer_eventos_epg(mapa_streams, inicio_hoy_utc, fin_hoy_utc, tsdb_agenda)

    # 5. Poda de eventos EPG antiguos fuera del rango de hoy
    eventos_base_conservados = []
    for ev in eventos_existentes:
        if ev.get("id", "").startswith("epg_"):
            try:
                dt_ev = datetime.strptime(ev["hora_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if not (inicio_hoy_utc <= dt_ev <= fin_hoy_utc):
                    continue
            except Exception:
                continue
        eventos_base_conservados.append(ev)

    # 6. Inyección final
    ids_existentes = {ev["id"] for ev in eventos_base_conservados}
    inyectados_nuevos = 0
    for ev_nuevo in eventos_epg:
        if ev_nuevo["id"] not in ids_existentes:
            eventos_base_conservados.append(ev_nuevo)
            ids_existentes.add(ev_nuevo["id"])
            inyectados_nuevos += 1

    eventos_base_conservados.sort(key=lambda x: x["hora_utc"])

    if isinstance(data_salida, dict):
        data_salida["eventos"] = eventos_base_conservados
    else:
        data_salida = {
            "generado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "base_media": "",
            "eventos": eventos_base_conservados
        }

    try:
        with open(ARCHIVO_EVENTOS, "w", encoding="utf-8") as f:
            json.dump(data_salida, f, ensure_ascii=False, indent=2)
        log.info(f"¡Inyección completada! Nuevos eventos: {inyectados_nuevos} | Total en cartelera: {len(eventos_base_conservados)}")
    except Exception as e:
        log.error(f"Error guardando {ARCHIVO_EVENTOS}: {e}")


if __name__ == "__main__":
    main()
