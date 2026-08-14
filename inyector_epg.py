#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
INYECTOR EPG DINÁMICO UNIVERSAL (TheSportsDB API + Canales Lineales EPG)
========================================================================
- Cero URLs fijas o hardcodeadas: los logos provienen dinámicamente de TheSportsDB.
- Cero listas manuales de torneos: validación por coincidencia de tokens en tiempo real.
- Duración exacta calculada desde los timestamps (start / stop) del XML.
- Sintaxis optimizada para tarjetas de Android TV (evita desbordes y redundancias).
- Soporte multicanal: Eurosport 1, Eurosport 2, Teledeporte y canales lineales mapeados.
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

# ─── CONFIGURACIÓN DE LOGGING ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("inyector_universal")

# ─── VARIABLES DE ENTORNO ───────────────────────────────────────────────────
XTREAM_URL = os.environ.get("XTREAM_URL")
XTREAM_USER = os.environ.get("XTREAM_USER")
XTREAM_PASS = os.environ.get("XTREAM_PASS")
PUENTE_URL = os.environ.get("PUENTE_URL", "https://mi-dashboard-tv.onrender.com/api/puente_xtream")

ARCHIVO_EVENTOS = "eventos_hoy.json"
URL_EPG_EUROPA = "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml"
THESPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json/3"

# Palabras genéricas a omitir al calcular la similitud léxica entre EPG y API
STOP_WORDS = {
    "DE", "DEL", "LA", "EL", "LOS", "LAS", "EN", "UN", "UNA", "Y", "O", "POR",
    "THE", "OF", "AND", "IN", "ON", "AT", "TO", "FOR", "WITH", "BY",
    "DIRECTO", "VIVO", "LIVE", "EN DIRECTO", "EN VIVO", "HD", "FHD", "SD", "4K",
    "T2024", "T2025", "T2026", "T2027", "T24", "T25", "T26", "T27"
}

VETO_EXPLICITO = [
    "REPETICION", "REPETICIÓN", "RESUMEN", "HIGHLIGHTS", "NOTICIAS", "NEWS",
    "MAGAZINE", "MEMORIAS", "VINTAGE", "CLASICOS", "CLÁSICOS", "ESPECIAL",
    "LO MEJOR DE", "PROGRAMACION", "PROGRAMACIÓN", "SPORT CENTER", "SPORTSCENTER",
    "INFORMATIVO", "TELEDIARIO", "REPORTAJES"
]


def normalizar_texto(texto: str) -> str:
    """Homogeniza cadenas eliminando acentos y caracteres no alfanuméricos."""
    if not texto:
        return ""
    t = str(texto).upper()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^A-Z0-9\s·\-\:\/]", " ", t)
    return " ".join(t.split())


def extraer_tokens_distintivos(texto: str) -> set:
    """Extrae palabras clave no genéricas de longitud mayor a 2 caracteres."""
    palabras = set(normalizar_texto(texto).split())
    return {p for p in (palabras - STOP_WORDS) if len(p) > 2 and not p.isdigit()}


def parse_timestamp_epg(time_str: str) -> datetime:
    """Convierte timestamps del XMLTV (estándar o exponencial) a objeto datetime UTC."""
    if not time_str:
        return None
    partes = time_str.strip().split()
    ts_num = partes[0]
    offset_str = partes[1] if len(partes) > 1 else "+0000"

    if "e+" in ts_num.lower():
        try:
            ts_digits = f"{int(round(float(ts_num))):014d}"
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
    """Define el rango del día actual en hora local de España (CEST UTC+2)."""
    ahora_utc = datetime.now(timezone.utc)
    tz_espana = timezone(timedelta(hours=2))
    hoy_espana = ahora_utc.astimezone(tz_espana).date()
    inicio_utc = datetime.combine(hoy_espana, datetime.min.time(), tzinfo=tz_espana).astimezone(timezone.utc)
    fin_utc = datetime.combine(hoy_espana, datetime.max.time(), tzinfo=tz_espana).astimezone(timezone.utc)
    return inicio_utc, fin_utc, hoy_espana.strftime("%Y-%m-%d")


def obtener_agenda_dinamica_tsdb(fecha_str: str) -> list:
    """Descarga de forma dinámica la agenda oficial de TheSportsDB para el día."""
    url_all = f"{THESPORTSDB_BASE}/eventsday.php?d={fecha_str}"
    eventos_tsdb = []

    try:
        res = requests.get(url_all, timeout=12)
        if res.status_code == 200 and res.text:
            evs = res.json().get("events") or []
            for e in evs:
                eventos_tsdb.append({
                    "id": e.get("idEvent"),
                    "nombre": e.get("strEvent", ""),
                    "deporte": e.get("strSport", "Deportes"),
                    "torneo": e.get("strLeague", ""),
                    "badge": e.get("strLeagueBadge") or e.get("strBadge") or e.get("strThumb") or "",
                    "ronda": e.get("strRound", ""),
                    "cruces": e.get("strResult", "")
                })
    except Exception as e:
        log.warning(f"Error consultando endpoint general de TheSportsDB: {e}")

    # Consultar disciplinas adicionales que reportan por parámetro deportivo
    deportes_adicionales = ["Snooker", "Athletics", "Cycling", "Motorsport", "Gymnastics", "Golf", "Fighting", "Tennis", "Swimming"]
    for dep in deportes_adicionales:
        try:
            url_dep = f"{THESPORTSDB_BASE}/eventsday.php?d={fecha_str}&s={dep}"
            res = requests.get(url_dep, timeout=8)
            if res.status_code == 200 and res.text:
                evs = res.json().get("events") or []
                for e in evs:
                    if not any(x["id"] == e.get("idEvent") for x in eventos_tsdb):
                        eventos_tsdb.append({
                            "id": e.get("idEvent"),
                            "nombre": e.get("strEvent", ""),
                            "deporte": e.get("strSport") or dep,
                            "torneo": e.get("strLeague", ""),
                            "badge": e.get("strLeagueBadge") or e.get("strBadge") or e.get("strThumb") or "",
                            "ronda": e.get("strRound", ""),
                            "cruces": e.get("strResult", "")
                        })
        except Exception:
            pass

    log.info(f"TheSportsDB: {len(eventos_tsdb)} eventos oficiales programados para {fecha_str}")
    return eventos_tsdb


def procesar_programa_epg(titulo_raw: str, tsdb_agenda: list, dt_inicio: datetime) -> dict:
    """Analiza la emisión, efectúa el cruce dinámico y estructura la información."""
    if not titulo_raw or len(titulo_raw.strip()) < 5:
        return None

    t_norm = normalizar_texto(titulo_raw)

    if any(v in t_norm for v in VETO_EXPLICITO):
        return None
    if re.search(r"\bE\d+\b", t_norm) or re.search(r"\bEPISODIO\s*\d+\b", t_norm):
        return None

    es_directo_etiqueta = bool(re.search(r"\b(DIRECTO|VIVO|LIVE|EN DIRECTO|EN VIVO)\b", titulo_raw, re.I))

    # Cruce semántico dinámico por tokens con la API
    match_tsdb = None
    kw_epg = extraer_tokens_distintivos(titulo_raw)

    for ev in tsdb_agenda:
        kw_api = extraer_tokens_distintivos(f"{ev['nombre']} {ev['torneo']} {ev['deporte']}")
        coincidencias = kw_epg.intersection(kw_api)
        if len(coincidencias) >= 1:
            match_tsdb = ev
            break

    # Admisión: tener etiqueta explícita o match directo en la API de hoy
    if not es_directo_etiqueta and not match_tsdb:
        return None

    # Limpieza de sintaxis para Android TV
    t_limpio = re.sub(r"\[COLOR.*?\]", "", titulo_raw, flags=re.I)
    t_limpio = re.sub(r"\[/COLOR\]", "", t_limpio, flags=re.I)
    t_limpio = re.sub(r"(?i)\b(DIRECTO|VIVO|LIVE|EN DIRECTO|EN VIVO)\b", "", t_limpio).strip(" -:·")
    t_limpio = " ".join(t_limpio.split())

    partes = [p.strip() for p in t_limpio.split("·") if p.strip()] if "·" in t_limpio else [t_limpio, ""]
    p1 = partes[0]
    p2 = partes[1] if len(partes) > 1 else ""

    # Detección estructural del Torneo vs Ronda/Fase
    kw_torneo = ["MUNDIAL", "CAMPEONATO", "EUROPEO", "OPEN", "VUELTA", "TOUR", "RACE", "GRAND PRIX", "SERIES", "LEAGUE", "COPA"]
    score_p1 = sum(1 for k in kw_torneo if k in normalizar_texto(p1))
    score_p2 = sum(1 for k in kw_torneo if k in normalizar_texto(p2))

    if score_p2 > score_p1:
        torneo_raw, sub_raw = p2, p1
    else:
        torneo_raw, sub_raw = p1, p2

    categoria = match_tsdb["deporte"] if match_tsdb else "Deportes"

    # Eliminar redundancia de categoría en el título (ej: "Snooker: Open de China" -> "Open de China")
    torneo = torneo_raw
    if torneo.lower().startswith(f"{categoria.lower()}:"):
        torneo = torneo[len(categoria)+1:].strip()
    elif torneo.lower().startswith(categoria.lower()):
        torneo = torneo[len(categoria):].strip(" -:·")

    torneo = re.sub(r"\bT\d{2,4}(/\d{2})?\b", "", torneo).strip(" -:·")
    torneo = " ".join(torneo.split())

    subtitulo = re.sub(r"\bT\d{2,4}(/\d{2})?\b", "", sub_raw).strip(" -:·")
    subtitulo = " ".join(subtitulo.split())

    # Formateo dinámico de cruces si TheSportsDB entrega los duelos de la sesión
    if match_tsdb and match_tsdb.get("cruces"):
        cruces_lista = [c.strip() for c in match_tsdb["cruces"].split("\r\n") if c.strip()]
        if cruces_lista:
            c_text = cruces_lista[1] if dt_inicio.hour < 12 and len(cruces_lista) >= 2 else cruces_lista[0]
            c_clean = re.sub(r"^\d+\s*", "", c_text).replace(" v ", " vs ")
            if "cuartos" in subtitulo.lower():
                subtitulo = f"Cuartos: {c_clean}"
            elif "semifinal" in subtitulo.lower():
                subtitulo = f"Semifinal: {c_clean}"
            elif not subtitulo:
                subtitulo = c_clean

    # Límites proporcionales para evitar desbordamiento en la UI
    if len(torneo) > 30:
        torneo = torneo[:29].rstrip() + "…"
    if len(subtitulo) > 36:
        subtitulo = subtitulo[:35].rstrip() + "…"

    titulo_final = f"{torneo} · {subtitulo}" if subtitulo else torneo
    logo_dinamico = match_tsdb["badge"] if match_tsdb and match_tsdb.get("badge") else ""

    return {
        "categoria": categoria,
        "torneo": torneo,
        "subtitulo": subtitulo,
        "titulo": titulo_final,
        "logo": logo_dinamico
    }


def mapear_streams_canales_lineales() -> dict:
    """Mapea streams de Xtream para Eurosport 1, Eurosport 2 y Teledeporte."""
    mapa = {"E1": [], "E2": [], "TDP": []}
    if not XTREAM_URL or not XTREAM_USER or not XTREAM_PASS:
        return mapa

    try:
        base_url = XTREAM_URL.rstrip("/")
        api_url = f"{base_url}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
        r = requests.get(PUENTE_URL, params={"url": api_url}, timeout=45)
        if r.status_code != 200:
            log.warning(f"Error consultando streams: HTTP {r.status_code}")
            return mapa

        streams = r.json()
        for s in streams:
            nombre = (s.get("name") or "").upper()
            sid = str(s.get("stream_id"))
            if not sid:
                continue

            fuente = {"nombre": s.get("name"), "id_xtream": sid}

            if any(k in nombre for k in ["TELEDEPORTE", " TDP ", "TDP HD", "TDP FHD"]):
                mapa["TDP"].append(fuente)
            elif any(k in nombre for k in ["EUROSPORT 2", "EUROSPORT2", "EUROSPORTS 2", "ES2"]):
                mapa["E2"].append(fuente)
            elif any(k in nombre for k in ["EUROSPORT 1", "EUROSPORT1", "EUROSPORTS 1", "ES1", "EUROSPORT HD"]):
                mapa["E1"].append(fuente)

        log.info(f"Señales mapeadas: E1={len(mapa['E1'])} | E2={len(mapa['E2'])} | TDP={len(mapa['TDP'])}")
    except Exception as e:
        log.error(f"Error mapeando streams lineales: {e}")

    return mapa


def extraer_eventos_epg(mapa_streams: dict, inicio_hoy_utc: datetime, fin_hoy_utc: datetime, tsdb_agenda: list) -> list:
    """Extrae y procesa los programas en vivo del EPG con cálculo exacto de duración."""
    log.info("Descargando guía EPG...")
    eventos_aprobados = []

    try:
        r = requests.get(URL_EPG_EUROPA, timeout=25)
        if r.status_code != 200:
            log.error(f"Error descargando EPG: HTTP {r.status_code}")
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
                    dt_fin = parse_timestamp_epg(elem.attrib.get("stop", ""))

                    if dt_inicio and (inicio_hoy_utc <= dt_inicio <= fin_hoy_utc):
                        tit_raw = elem.findtext("title", "")
                        info_ev = procesar_programa_epg(tit_raw, tsdb_agenda, dt_inicio)

                        if info_ev:
                            fuentes_asignadas = mapa_streams.get(canal_clave, [])
                            if fuentes_asignadas:
                                duracion_min = int((dt_fin - dt_inicio).total_seconds() / 60) if dt_fin else 120
                                if duracion_min <= 0:
                                    duracion_min = 120

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
                                    "duracion_min": duracion_min,
                                    "logo_torneo": info_ev["logo"],
                                    "logo_local": info_ev["logo"],
                                    "logo_visitante": "",
                                    "tier": 2,
                                    "fuentes": list(fuentes_asignadas)
                                })
                elem.clear()
    except Exception as e:
        log.error(f"Error procesando EPG: {e}")

    # Deduplicación por torneo y hora de inicio (permite sesiones de mañana/tarde independientes)
    unicos = {}
    for ev in eventos_aprobados:
        k = (ev["torneo"], ev["hora_utc"])
        if k not in unicos:
            unicos[k] = ev

    log.info(f"Eventos aprobados del EPG para hoy: {len(unicos)}")
    return list(unicos.values())


def main():
    log.info("=== Iniciando Inyector EPG Universal Dinámico ===")

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

    inicio_hoy_utc, fin_hoy_utc, fecha_str = calcular_ventana_hoy_espana()
    tsdb_agenda = obtener_agenda_dinamica_tsdb(fecha_str)
    mapa_streams = mapear_streams_canales_lineales()
    eventos_epg = extraer_eventos_epg(mapa_streams, inicio_hoy_utc, fin_hoy_utc, tsdb_agenda)

    # Conservar eventos del curador base y podar EPG antiguos
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
        log.info(f"¡Proceso universal completado! Total en cartelera: {len(eventos_base_conservados)}")
    except Exception as e:
        log.error(f"Error guardando {ARCHIVO_EVENTOS}: {e}")


if __name__ == "__main__":
    main()
