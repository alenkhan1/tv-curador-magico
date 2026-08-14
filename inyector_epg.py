#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
INYECTOR EPG EUROSPORT — AllStreamTV
====================================
Arquitectura de Enriquecimiento y Mapeo Estricto:
1. Lectura de parrilla Eurosport 1 HD y Eurosport 2 desde EPG Europa.
2. Filtro Antirreemisiones (disciplinas activas, fases de prueba, veto de diferidos).
3. Mapeo estricto por canal emisor (Eurosport 1 -> fuentes E1 | Eurosport 2 -> fuentes E2).
4. Enriquecimiento híbrido en cascada: Categoría auténtica + Logos oficiales HD.
5. Inyección limpia en eventos_hoy.json sin duplicados ni pérdida de eventos VIP previos.
"""

import os
import io
import re
import json
import time
import hashlib
import logging
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus
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

THESPORTSDB_KEY = os.environ.get("THESPORTSDB_KEY", "123")
THESPORTSDB_BASE = f"https://www.thesportsdb.com/api/v1/json/{THESPORTSDB_KEY}"

ARCHIVO_EVENTOS = "eventos_hoy.json"
URL_EPG_EUROPA = "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml"

# ─── CATÁLOGO DE IDENTIDAD VISUAL Y CATEGORÍAS (NIVEL 2 Y 3) ───────────────
CATALOGO_DISCIPLINAS = {
    "ATLETISMO": {
        "categoria": "Atletismo",
        "keywords": ["ATLETISMO", "ATHLETICS", "JABALINA", "MARATON", "VELOCIDAD", "PERTIGA", "DECATLON", "DIAMOND LEAGUE"],
        "logo_oficial": "https://r2.thesportsdb.com/images/media/league/badge/0gcrre1598967916.png",  # World Athletics
    },
    "CICLISMO": {
        "categoria": "Ciclismo",
        "keywords": ["CICLISMO", "CYCLING", "ARCTIC RACE", "TOUR DE FRANCE", "VUELTA", "GIRO", "VUELTA A CHEQUIA", "CRITERIUM", "UCI", "ETAPA"],
        "logo_oficial": "https://r2.thesportsdb.com/images/media/league/badge/9n5q1r1598968134.png",  # UCI ProSeries
    },
    "SNOOKER": {
        "categoria": "Otros Deportes",
        "keywords": ["SNOOKER", "BILLAR", "OPEN DE CHINA", "WORLD SNOOKER", "MASTERS SNOOKER", "CRUCIBLE"],
        "logo_oficial": "https://r2.thesportsdb.com/images/media/league/badge/8m4v5z1600171203.png",  # World Snooker Tour
    },
    "MOTOR": {
        "categoria": "Motor",
        "keywords": ["FORMULA E", "MOTOGP", "SUPERBIKE", "LE MANS", "ENDURANCE", "RALLY", "WRC", "F1", "DAKAR", "EPRIX"],
        "logo_oficial": "https://r2.thesportsdb.com/images/media/league/badge/1k3h1r1598968001.png",  # FIA
    },
    "TENIS": {
        "categoria": "Tenis",
        "keywords": ["TENIS", "TENNIS", "ROLAND GARROS", "AUSTRALIAN OPEN", "US OPEN", "WIMBLEDON", "ATP", "WTA"],
        "logo_oficial": "https://r2.thesportsdb.com/images/media/league/badge/vuytsu1432828359.png",  # Grand Slam / ATP
    },
    "DEPORTES DE INVIERNO": {
        "categoria": "Otros Deportes",
        "keywords": ["ESQUI", "SKI", "BIATLON", "BIATHLON", "SALTOS DE ESQUI", "SNOWBOARD", "CURLING", "PATINAJE", "FIS "],
        "logo_oficial": "https://r2.thesportsdb.com/images/media/league/badge/fis_ski_federation.png",  # FIS
    },
    "COMBATE": {
        "categoria": "Combate",
        "keywords": ["BOXEO", "BOXING", "UFC", "MMA", "JUDO", "KARATE", "TAEKWONDO", "DANA WHITE", "CONTENDER"],
        "logo_oficial": "https://r2.thesportsdb.com/images/media/league/badge/ufc_badge.png",
    },
    "TRIATLON": {
        "categoria": "Otros Deportes",
        "keywords": ["TRIATLON", "TRIATHLON", "IRONMAN", "T100 WORLD TOUR", "T100"],
        "logo_oficial": "https://r2.thesportsdb.com/images/media/league/badge/triathlon_badge.png",
    }
}

LOGO_DEFAULT_EUROSPORT = "https://r2.thesportsdb.com/images/media/tv/badge/eurosport_hd.png"

# Veto de repeticiones y contenidos diferidos
VETO_REPETICIONES = [
    "REPETICION", "REPETICIÓN", "RESUMEN", "HIGHLIGHTS", "NOTICIAS", "NEWS",
    "MAGAZINE", "MEMORIAS", "VINTAGE", "CLASICOS", "CLÁSICOS", "ESPECIAL",
    "LO MEJOR DE", "PROGRAMACION", "PROGRAMACIÓN", "SPORT CENTER", "SPORTSCENTER"
]


def normalizar_cadena(texto: str) -> str:
    """Elimina acentos y normaliza texto para comparaciones."""
    if not texto:
        return ""
    t = str(texto).upper()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^A-Z0-9\s·\-\:\/]", " ", t)
    return " ".join(t.split())


def parse_time_epg(time_str: str) -> datetime:
    """Convierte marcas de tiempo del XML '20260814073000 +0200' a datetime UTC."""
    try:
        dt = datetime.strptime(time_str[:14], "%Y%m%d%H%M%S")
        offset_str = time_str[15:].strip()
        if len(offset_str) >= 5:
            sign = 1 if offset_str[0] == '+' else -1
            h, m = int(offset_str[1:3]), int(offset_str[3:5])
            tz = timezone(timedelta(hours=sign * h, minutes=sign * m))
        else:
            tz = timezone.utc
        return dt.replace(tzinfo=tz).astimezone(timezone.utc)
    except Exception:
        return None


def es_evento_en_vivo_valido(titulo_raw: str, dt_inicio_utc: datetime) -> tuple:
    """
    Filtro determinista para identificar emisiones en vivo en Eurosport.
    Devuelve (es_valido, titulo_limpio, torneo_identificado, info_disciplina).
    """
    if not titulo_raw or len(titulo_raw.strip()) < 5:
        return False, "", "", None

    t_norm = normalizar_cadena(titulo_raw)

    # 1. Veto estricto de repeticiones
    if any(veto in t_norm for veto in VETO_REPETICIONES):
        return False, "", "", None

    # Veto de series y capítulos
    if re.search(r'\bE\d+\b', t_norm) or re.search(r'\bEPISODIO\s*\d+\b', t_norm):
        return False, "", "", None

    # 2. Detección de disciplina deportiva
    info_disc = None
    for _, datos in CATALOGO_DISCIPLINAS.items():
        if any(kw in t_norm for kw in datos["keywords"]):
            info_disc = datos
            break

    if not info_disc:
        return False, "", "", None

    # 3. Limpieza de texto para la interfaz de TV
    t_limpio = re.sub(r'\[COLOR.*?\]', '', titulo_raw, flags=re.IGNORECASE)
    t_limpio = re.sub(r'\[/COLOR\]', '', t_limpio, flags=re.IGNORECASE)
    t_limpio = re.sub(r'(?i)\bDIRECTO\b', '', t_limpio).strip(" -:·")
    t_limpio = ' '.join(t_limpio.split())

    partes = t_limpio.split("·")
    torneo_base = partes[0].strip() if len(partes) > 1 else t_limpio.split("-")[0].strip()

    return True, t_limpio, torneo_base, info_disc


def consultar_logo_thesportsdb(nombre_torneo: str) -> str:
    """Consulta dinámica del badge de la competición en TheSportsDB."""
    try:
        url = f"{THESPORTSDB_BASE}/searchleagues.php?l={quote_plus(nombre_torneo)}"
        r = requests.get(url, timeout=3)
        if r.status_code == 200:
            datos = r.json().get("leagues") or []
            if datos:
                badge = datos[0].get("strBadge") or datos[0].get("strLogo") or ""
                if badge:
                    return badge
    except Exception:
        pass
    return ""


def resolver_identidad_visual(torneo_nombre: str, info_disc: dict) -> tuple:
    """
    Modelo en cascada:
    Nivel 1: TheSportsDB dinámico.
    Nivel 2 y 3: Catálogo oficial local.
    Devuelve (logo_url, categoria_real).
    """
    categoria_real = info_disc["categoria"]

    # Nivel 1
    logo_api = consultar_logo_thesportsdb(torneo_nombre)
    if logo_api:
        return logo_api, categoria_real

    # Nivel 2 y 3
    logo_local = info_disc.get("logo_oficial") or LOGO_DEFAULT_EUROSPORT
    return logo_local, categoria_real


def mapear_streams_por_canal() -> dict:
    """
    Mapeo estricto e independiente de señales:
    Eurosport 1 -> únicamente streams de Eurosport 1.
    Eurosport 2 -> únicamente streams de Eurosport 2.
    """
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
        log.info(f"Streams recibidos del panel Xtream: {len(streams)}")

        for s in streams:
            nombre = (s.get("name") or "").upper()
            sid = str(s.get("stream_id"))
            if not sid or ("EUROSPORT" not in nombre and "EUROSPORTS" not in nombre):
                continue

            fuente = {"nombre": s.get("name"), "id_xtream": sid}

            # Clasificación estricta por canal
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


def extraer_eventos_epg(mapa_streams: dict) -> list:
    """Descarga y procesa el EPG para Eurosport 1 y Eurosport 2."""
    log.info("Descargando EPG Europa (EPG_dobleM)...")
    eventos_aprobados = []
    ahora_utc = datetime.now(timezone.utc)
    ventana_inicio = ahora_utc - timedelta(hours=2)
    ventana_fin = ahora_utc + timedelta(hours=24)

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
                    dt_inicio = parse_time_epg(elem.attrib.get("start", ""))
                    if dt_inicio and (ventana_inicio <= dt_inicio < ventana_fin):
                        tit_raw = elem.findtext("title", "")
                        valido, tit_limpio, torneo_nombre, info_disc = es_evento_en_vivo_valido(tit_raw, dt_inicio)

                        if valido:
                            fuentes_asignadas = mapa_streams.get(canal_clave, [])
                            if fuentes_asignadas:
                                logo_oficial, categoria_real = resolver_identidad_visual(torneo_nombre, info_disc)

                                # ID único determinístico
                                id_base = f"{tit_limpio.upper()}|{dt_inicio.strftime('%Y%m%d%H%M')}|{canal_clave}"
                                id_unico = f"epg_{hashlib.md5(id_base.encode('utf-8')).hexdigest()[:12]}"

                                eventos_aprobados.append({
                                    "id": id_unico,
                                    "titulo": tit_limpio,
                                    "torneo": torneo_nombre,
                                    "categoria": categoria_real,  # Deporte auténtico
                                    "tipo_evento": "sencillo",
                                    "equipo_local": "",
                                    "equipo_visitante": "",
                                    "subtitulo": f"Canal {canal_clave.replace('E', 'Eurosport ')}",
                                    "hora_utc": dt_inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "duracion_min": 120,
                                    "logo_torneo": logo_oficial,
                                    "logo_local": logo_oficial,
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

    log.info(f"Total eventos Eurosport en vivo validados: {len(unicos)}")
    return list(unicos.values())


def main():
    log.info("=== Iniciando Inyector Eurosport (Filtrado En Vivo + Logos Oficiales) ===")

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

    # 1. Mapeo estricto de streams por canal (E1 vs E2)
    mapa_streams = mapear_streams_por_canal()

    # 2. Extracción y enriquecimiento de eventos en directo
    eventos_eurosport = extraer_eventos_epg(mapa_streams)

    # 3. Poda de eventos de Eurosport vencidos (>4h pasadas) respetando eventos de TheSportsDB
    ahora_utc = datetime.now(timezone.utc)
    eventos_base_conservados = []
    for ev in eventos_existentes:
        if ev.get("id", "").startswith("epg_"):
            try:
                dt_ev = datetime.strptime(ev["hora_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if (ahora_utc - dt_ev).total_seconds() > (4 * 3600):
                    continue
            except Exception:
                continue
        eventos_base_conservados.append(ev)

    # 4. Inyección sin colisiones
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
            "generado_utc": ahora_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "base_media": "",
            "eventos": eventos_base_conservados
        }

    try:
        with open(ARCHIVO_EVENTOS, "w", encoding="utf-8") as f:
            json.dump(data_salida, f, ensure_ascii=False, indent=2)
        log.info(f"¡Inyección completada! Nuevos eventos inyectados: {inyectados_nuevos} | Total eventos activos en JSON: {len(eventos_base_conservados)}")
    except Exception as e:
        log.error(f"Error escribiendo {ARCHIVO_EVENTOS}: {e}")


if __name__ == "__main__":
    main()
