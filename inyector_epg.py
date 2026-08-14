#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
INYECTOR EPG EUROSPORT — AllStreamTV (Versión Definitiva)
=========================================================
1. Ventana Estricta de 'Hoy': Huso horario de emisión (España UTC+2), desde 00:00 hasta 23:59.
2. Filtro Determinista 'EN DIRECTO': Solo eventos con marca explícita de transmisión en vivo.
3. Desglose Estético de Tarjetas:
   - categoria: 'Snooker', 'Atletismo', 'Ciclismo', 'Motor', etc.
   - torneo: Nombre limpio de la competición (ej: 'Open de China', 'Europeo de Atletismo').
   - subtitulo: Fase o sesión real (ej: 'Cuartos de final', 'Día 5 - Sesión de tarde').
   - titulo: Torneo + Fase/Sesión.
4. Mapeo Aislado de Canales: Eurosport 1 -> E1 | Eurosport 2 -> E2.
5. Badges Oficiales Verificados (HTTP 200) de TheSportsDB.
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
log = logging.getLogger("inyector_eurosport")

# ─── CONFIGURACIÓN DE ENTORNO ───────────────────────────────────────────────
XTREAM_URL = os.environ.get("XTREAM_URL")
XTREAM_USER = os.environ.get("XTREAM_USER")
XTREAM_PASS = os.environ.get("XTREAM_PASS")
PUENTE_URL = os.environ.get("PUENTE_URL", "https://mi-dashboard-tv.onrender.com/api/puente_xtream")

ARCHIVO_EVENTOS = "eventos_hoy.json"
URL_EPG_EUROPA = "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml"

# ─── LOGOS OFICIALES VERIFICADOS (THE SPORTS DB - HTTP 200) ──────────────────
LOGOS_DISCIPLINAS = {
    "Snooker": "https://r2.thesportsdb.com/images/media/league/badge/0gmkgj1555600537.png",       # World Snooker Tour
    "Atletismo": "https://r2.thesportsdb.com/images/media/league/badge/9rr2ad1741364834.png",     # European Athletics
    "Ciclismo": "https://r2.thesportsdb.com/images/media/league/badge/igahc11535183469.png",      # UCI World Tour
    "Motor": "https://r2.thesportsdb.com/images/media/league/badge/64f67s1770108650.png",         # Motorsport / FIA
    "Tenis": "https://r2.thesportsdb.com/images/media/league/badge/1adg221775893214.png",         # Grand Slam / ATP
    "Combate": "https://r2.thesportsdb.com/images/media/league/badge/x9cxmq1578222615.png",       # Combat Sports
    "Triatlón": "https://r2.thesportsdb.com/images/media/league/badge/igahc11535183469.png",      # Triathlon / Endurance
    "Deportes de Invierno": "https://r2.thesportsdb.com/images/media/league/badge/9rr2ad1741364834.png",
    "Otros Deportes": "https://r2.thesportsdb.com/images/media/league/badge/0gmkgj1555600537.png"
}

VETO_PALABRAS = [
    "REPETICION", "REPETICIÓN", "RESUMEN", "HIGHLIGHTS", "NOTICIAS", "NEWS",
    "MAGAZINE", "MEMORIAS", "VINTAGE", "CLASICOS", "CLÁSICOS", "ESPECIAL",
    "LO MEJOR DE", "PROGRAMACION", "PROGRAMACIÓN", "SPORT CENTER", "SPORTSCENTER"
]


def normalizar_texto(texto: str) -> str:
    """Elimina acentos y normaliza el texto a mayúsculas."""
    if not texto:
        return ""
    t = str(texto).upper()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^A-Z0-9\s·\-\:\/]", " ", t)
    return " ".join(t.split())


def parse_timestamp_epg(time_str: str) -> datetime:
    """
    Convierte marcas de tiempo del XML soportando notación exponencial o estándar.
    Ejemplos: '2.0260814203e+13 +0200', '20260814203000 +0200' -> datetime UTC.
    """
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
    """Calcula el rango 'hoy' exacto en huso español (CEST UTC+2)."""
    ahora_utc = datetime.now(timezone.utc)
    tz_espana = timezone(timedelta(hours=2))
    hoy_espana = ahora_utc.astimezone(tz_espana).date()
    inicio_utc = datetime.combine(hoy_espana, datetime.min.time(), tzinfo=tz_espana).astimezone(timezone.utc)
    fin_utc = datetime.combine(hoy_espana, datetime.max.time(), tzinfo=tz_espana).astimezone(timezone.utc)
    return inicio_utc, fin_utc


def procesar_programa_epg(titulo_raw: str) -> dict:
    """
    Evalúa si es en vivo y desglosa el título en (torneo, subtitulo, categoria).
    """
    if not titulo_raw or len(titulo_raw.strip()) < 5:
        return None

    t_norm = normalizar_texto(titulo_raw)

    # 1. Veto de repeticiones y revistas
    if any(veto in t_norm for veto in VETO_PALABRAS):
        return None
    if re.search(r"\bE\d+\b", t_norm) or re.search(r"\bEPISODIO\s*\d+\b", t_norm):
        return None

    # 2. Verificación estricta de transmisión en vivo (DIRECTO o LIVE en título)
    es_directo = bool(re.search(r"\b(DIRECTO|VIVO|LIVE|EN DIRECTO|EN VIVO)\b", titulo_raw, re.IGNORECASE))
    if not es_directo:
        return None

    # 3. Limpiar etiquetas y la palabra DIRECTO
    t_limpio = re.sub(r"\[COLOR.*?\]", "", titulo_raw, flags=re.IGNORECASE)
    t_limpio = re.sub(r"\[/COLOR\]", "", t_limpio, flags=re.IGNORECASE)
    t_limpio = re.sub(r"(?i)\b(DIRECTO|VIVO|LIVE|EN DIRECTO|EN VIVO)\b", "", t_limpio).strip(" -:·")
    t_limpio = " ".join(t_limpio.split())

    # 4. Detectar categoría deportiva real
    t_clean_norm = normalizar_texto(t_limpio)
    categoria = "Otros Deportes"
    if any(k in t_clean_norm for k in ["SNOOKER", "BILLAR"]):
        categoria = "Snooker"
    elif any(k in t_clean_norm for k in ["ATLETISMO", "ATHLETICS", "JABALINA", "MARATON", "PERTIGA", "VELOCIDAD", "DECATLON"]):
        categoria = "Atletismo"
    elif any(k in t_clean_norm for k in ["CICLISMO", "CYCLING", "ARCTIC RACE", "VUELTA", "TOUR", "GIRO", "ETAPA", "CRITERIUM"]):
        categoria = "Ciclismo"
    elif any(k in t_clean_norm for k in ["FORMULA E", "MOTOGP", "SUPERBIKE", "RALLY", "WRC", "F1", "MOTOR", "EPRIX"]):
        categoria = "Motor"
    elif any(k in t_clean_norm for k in ["TENIS", "TENNIS", "ROLAND GARROS", "WIMBLEDON", "US OPEN", "AUSTRALIAN OPEN", "ATP", "WTA"]):
        categoria = "Tenis"
    elif any(k in t_clean_norm for k in ["TRIATLON", "TRIATHLON", "T100", "IRONMAN"]):
        categoria = "Triatlón"
    elif any(k in t_clean_norm for k in ["BOXEO", "UFC", "MMA", "COMBATE", "DANA WHITE"]):
        categoria = "Combate"
    elif any(k in t_clean_norm for k in ["ESQUI", "SKI", "BIATLON", "SNOWBOARD", "PATINAJE"]):
        categoria = "Deportes de Invierno"

    # 5. Desglose en Torneo y Subtítulo
    torneo = t_limpio
    subtitulo = ""

    if "·" in t_limpio:
        partes = [p.strip() for p in t_limpio.split("·") if p.strip()]
        if len(partes) >= 2:
            p1, p2 = partes[0], partes[1]
            indicadores_sesion = ["DIA", "DÍA", "ETAPA", "STAGE", "SESION", "SESIÓN", "FINAL", "SEMIFINAL", "CUARTOS", "CARRERA", "RACE"]
            if any(ind in normalizar_texto(p1) for ind in indicadores_sesion) and not any(ind in normalizar_texto(p2) for ind in indicadores_sesion):
                torneo = p2
                subtitulo = p1
            else:
                torneo = p1
                subtitulo = p2
    elif " - " in t_limpio:
        partes = [p.strip() for p in t_limpio.split(" - ") if p.strip()]
        if len(partes) >= 2:
            torneo = partes[0]
            subtitulo = " - ".join(partes[1:])
    elif ":" in t_limpio:
        partes = [p.strip() for p in t_limpio.split(":") if p.strip()]
        if len(partes) >= 2:
            torneo = partes[0]
            subtitulo = ": ".join(partes[1:])

    # Limpiar prefijos o etiquetas de temporada crudas (ej: T2026, T26/27)
    torneo_limpio = re.sub(r"\bT\d{2,4}(/\d{2})?\b", "", torneo).strip(" -:·")
    torneo_limpio = " ".join(torneo_limpio.split())
    if not torneo_limpio:
        torneo_limpio = torneo

    subtitulo_limpio = re.sub(r"\bT\d{2,4}(/\d{2})?\b", "", subtitulo).strip(" -:·")
    subtitulo_limpio = " ".join(subtitulo_limpio.split())
    if not subtitulo_limpio:
        subtitulo_limpio = categoria

    titulo_final = f"{torneo_limpio} · {subtitulo_limpio}" if subtitulo_limpio != categoria else torneo_limpio

    return {
        "categoria": categoria,
        "torneo": torneo_limpio,
        "subtitulo": subtitulo_limpio,
        "titulo": titulo_final,
        "logo": LOGOS_DISCIPLINAS.get(categoria, LOGOS_DISCIPLINAS["Otros Deportes"])
    }


def mapear_streams_por_canal() -> dict:
    """Mapea fuentes de reproducción separando Eurosport 1 de Eurosport 2."""
    mapa = {"E1": [], "E2": []}
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
            if not sid or ("EUROSPORT" not in nombre and "EUROSPORTS" not in nombre):
                continue

            fuente = {"nombre": s.get("name"), "id_xtream": sid}
            if any(k in nombre for k in ["EUROSPORT 2", "EUROSPORT2", "EUROSPORTS 2", "ES2", "E2"]):
                mapa["E2"].append(fuente)
            elif any(k in nombre for k in ["EUROSPORT 1", "EUROSPORT1", "EUROSPORTS 1", "ES1", "E1", "EUROSPORT HD"]):
                mapa["E1"].append(fuente)
            else:
                mapa["E1"].append(fuente)

        log.info(f"Mapeo de señales: {len(mapa['E1'])} streams en Eurosport 1 | {len(mapa['E2'])} streams en Eurosport 2")
    except Exception as e:
        log.error(f"Error procesando streams de Eurosport: {e}")

    return mapa


def extraer_eventos_epg(mapa_streams: dict, inicio_hoy_utc: datetime, fin_hoy_utc: datetime) -> list:
    """Descarga el EPG y procesa únicamente los eventos en directo del día actual."""
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

                if canal_clave:
                    dt_inicio = parse_timestamp_epg(elem.attrib.get("start", ""))
                    if dt_inicio and (inicio_hoy_utc <= dt_inicio <= fin_hoy_utc):
                        tit_raw = elem.findtext("title", "")
                        info_ev = procesar_programa_epg(tit_raw)

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
        log.error(f"Error procesando EPG Eurosport: {e}")

    # Deduplicación por título y hora
    unicos = {}
    for ev in eventos_aprobados:
        k = (ev["titulo"], ev["hora_utc"])
        if k not in unicos:
            unicos[k] = ev

    log.info(f"Eventos Eurosport en directo de HOY aprobados: {len(unicos)}")
    return list(unicos.values())


def main():
    log.info("=== Iniciando Inyector Eurosport (Filtrado Estricto de Directos + Badges Oficiales) ===")

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

    # 1. Ventana horaria de Hoy (España)
    inicio_hoy_utc, fin_hoy_utc = calcular_ventana_hoy_espana()
    log.info(f"Ventana 'Hoy' (España UTC+2): {inicio_hoy_utc.isoformat()} -> {fin_hoy_utc.isoformat()}")

    # 2. Mapeo estricto de streams
    mapa_streams = mapear_streams_por_canal()

    # 3. Extracción de eventos en vivo
    eventos_eurosport = extraer_eventos_epg(mapa_streams, inicio_hoy_utc, fin_hoy_utc)

    # 4. Poda de eventos de Eurosport que no pertenezcan a la ventana de hoy
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

    # 5. Inyección sin colisiones
    ids_existentes = {ev["id"] for ev in eventos_base_conservados}
    inyectados_nuevos = 0
    for ev_euro in eventos_eurosport:
        if ev_euro["id"] not in ids_existentes:
            eventos_base_conservados.append(ev_euro)
            ids_existentes.add(ev_euro["id"])
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
        log.info(f"¡Inyección finalizada con éxito! Nuevos directos inyectados: {inyectados_nuevos} | Total eventos en cartelera: {len(eventos_base_conservados)}")
    except Exception as e:
        log.error(f"Error escribiendo {ARCHIVO_EVENTOS}: {e}")


if __name__ == "__main__":
    main()
