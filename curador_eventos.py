#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CURADOR DE EVENTOS DEPORTIVOS — AllStreamTV
===========================================
Motor de Reverse Matching Directo:
1. Detección nativa de zona horaria vía server_info de Xtream.
2. TheSportsDB como única fuente de verdad estructurada (nombres, logos, torneos).
3. Pipeline en 2 vías: Duelos (coincidencia simultánea de rivales) y Sencillos.
4. Preservación estricta de categorías deportivas oficiales (sin 'Otros Deportes').
5. Consolidación de streams bajo el mismo ID y cuarentena para descartes.
"""

import os
import re
import json
import time
import logging
import unicodedata
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
import zoneinfo
import requests

# ─── LOGGING ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("curador")

# ─── CONFIGURACIÓN DE ENTORNO ───────────────────────────────────────────────
XTREAM_URL = os.environ.get("XTREAM_URL")
XTREAM_USER = os.environ.get("XTREAM_USER")
XTREAM_PASS = os.environ.get("XTREAM_PASS")

THESPORTSDB_KEY = os.environ.get("THESPORTSDB_KEY", "123")
THESPORTSDB_BASE = f"https://www.thesportsdb.com/api/v1/json/{THESPORTSDB_KEY}"
PUENTE_URL = os.environ.get("PUENTE_URL", "https://mi-dashboard-tv.onrender.com/api/puente_xtream")

ARCHIVO_CACHE = "agenda_api_v8.json"
HORAS_CACHE = 4
ARCHIVO_SALIDA = "eventos_hoy.json"
ARCHIVO_CUARENTENA = "eventos_descartados.json"

# ─── DEPORTES A CONSULTAR EN THESPORTSDB ────────────────────────────────────
DEPORTES_MAP = {
    "Fútbol": "Soccer",
    "Baloncesto": "Basketball",
    "Tenis": "Tennis",
    "Motor": "Motorsport",
    "Béisbol": "Baseball",
    "Hockey": "Ice Hockey",
    "Voleibol": "Volleyball",
    "Rugby": "Rugby",
    "Fútbol Americano": "American Football",
    "Combate": "Fighting",
    "Ciclismo": "Cycling",
}

DURACION_POR_DEPORTE = {
    "Fútbol": 120,
    "Baloncesto": 150,
    "Tenis": 180,
    "Motor": 210,
    "Béisbol": 210,
    "Hockey": 150,
    "Combate": 180,
    "Ciclismo": 300,
    "Voleibol": 150,
    "Rugby": 150,
    "Fútbol Americano": 210,
    "Deportes": 150,
}

# Palabras genéricas que no definen identidad de equipo
STOPWORDS = {
    "THE", "AND", "DEL", "LAS", "LOS", "VS", "EN", "EL", "DE", "LA",
    "SAN", "FC", "CF", "CD", "CLUB", "CITY", "UNITED", "REAL",
    "SPORTING", "ATHLETIC", "DEPORTIVO", "RACING"
}


def normalizar_texto(texto: str) -> str:
    """Elimina tildes, símbolos raros y homogeniza a mayúsculas."""
    if not texto:
        return ""
    t = str(texto).upper()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^A-Z0-9\s]", " ", t)
    return " ".join(t.split())


def obtener_palabras_clave(texto: str, filtrar_stopwords: bool = True) -> set:
    """Extrae palabras de longitud significativa para cruces de identidad."""
    limpio = normalizar_texto(texto)
    palabras = limpio.split()
    if filtrar_stopwords:
        return {p for p in palabras if len(p) > 2 and p not in STOPWORDS}
    return {p for p in palabras if len(p) > 2}


def extraer_hora_canal(texto: str, fecha_base: datetime):
    """
    Extrae la hora y minuto del stream soportando formatos pegados como 03:07pm.
    """
    t = texto.strip()

    # Formato HH:MM con AM/PM (ej: 03:07pm, 19:00, 20:00)
    for m in re.finditer(r'(\d{1,2}):(\d{2})\s*([APap][Mm])?', t):
        h, mi = int(m.group(1)), int(m.group(2))
        ampm = m.group(3)
        if ampm:
            ampm = ampm.upper()
            if ampm == "PM" and h < 12:
                h += 12
            elif ampm == "AM" and h == 12:
                h = 0
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return fecha_base.replace(hour=h, minute=mi, second=0, microsecond=0)

    # Formato hora suelta (ej: 8pm, 8 AM)
    for m in re.finditer(r'\b(\d{1,2})\s*([APap][Mm])\b', t):
        h = int(m.group(1))
        ampm = m.group(2).upper()
        if ampm == "PM" and h < 12:
            h += 12
        elif ampm == "AM" and h == 12:
            h = 0
        if 0 <= h <= 23:
            return fecha_base.replace(hour=h, minute=0, second=0, microsecond=0)

    return None


def obtener_info_servidor_xtream() -> tuple:
    """
    Lee server_info de Xtream Codes para obtener la zona horaria real y hora local.
    """
    base_url = XTREAM_URL.rstrip("/")
    api_url = f"{base_url}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
    try:
        r = requests.get(PUENTE_URL, params={"url": api_url}, timeout=20)
        if r.status_code == 200:
            datos = r.json()
            server_info = datos.get("server_info", {})
            tz_name = server_info.get("timezone", "").strip()
            if tz_name:
                try:
                    tz_obj = zoneinfo.ZoneInfo(tz_name)
                    hoy_servidor = datetime.now(tz_obj)
                    log.info(f"Zona horaria nativa del servidor Xtream: {tz_name} ({hoy_servidor.strftime('%Y-%m-%d %H:%M:%S')})")
                    return tz_obj, hoy_servidor
                except Exception as ex:
                    log.warning(f"Error interpretando zona horaria '{tz_name}': {ex}")
    except Exception as e:
        log.warning(f"No se pudo consultar server_info de Xtream: {e}")

    tz_defecto = timezone(timedelta(hours=-5))
    hoy_defecto = datetime.now(tz_defecto)
    log.info(f"Usando zona horaria de respaldo: UTC-5 ({hoy_defecto.strftime('%Y-%m-%d')})")
    return tz_defecto, hoy_defecto


def obtener_agenda_maestra(fecha_consulta: str) -> list:
    """Descarga la agenda completa del día desde TheSportsDB."""
    if os.path.exists(ARCHIVO_CACHE):
        tiempo_modificacion = os.path.getmtime(ARCHIVO_CACHE)
        if (time.time() - tiempo_modificacion) < (HORAS_CACHE * 3600):
            try:
                with open(ARCHIVO_CACHE, "r", encoding="utf-8") as f:
                    agenda = json.load(f)
                if agenda:
                    log.info(f"Cargada agenda desde caché local ({len(agenda)} eventos).")
                    return agenda
            except Exception:
                pass

    log.info(f"Descargando Agenda Maestra de TheSportsDB para fecha {fecha_consulta}...")
    eventos_api = []

    for deporte_es, deporte_api in DEPORTES_MAP.items():
        url = f"{THESPORTSDB_BASE}/eventsday.php"
        params = {"d": fecha_consulta, "s": deporte_api}
        try:
            time.sleep(1.0)
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 429:
                time.sleep(5)
                r = requests.get(url, params=params, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json().get("events") or []
        except Exception as e:
            log.warning(f"Error consultando agenda para {deporte_es}: {e}")
            continue

        for ev in data:
            try:
                fecha_str = ev.get("dateEvent")
                hora_str = ev.get("strTime") or "00:00:00"
                if not fecha_str:
                    continue
                dt_naive = datetime.strptime(f"{fecha_str} {hora_str[:8]}", "%Y-%m-%d %H:%M:%S")
                dt_utc = dt_naive.replace(tzinfo=timezone.utc)

                torneo = ev.get("strLeague", "") or ""
                eq_local = ev.get("strHomeTeam", "") or ""
                eq_visit = ev.get("strAwayTeam", "") or ""

                if eq_local and eq_visit:
                    titulo = f"{eq_local} vs {eq_visit}"
                    tipo_evento_api = "duelo"
                else:
                    titulo = ev.get("strEvent", "") or ev.get("strFilename", "") or torneo
                    tipo_evento_api = "sencillo"

                eventos_api.append({
                    "id": str(ev.get("idEvent")),
                    "titulo": titulo,
                    "torneo": torneo,
                    "categoria": deporte_es,  # Preserva su deporte real
                    "tipo_evento": tipo_evento_api,
                    "equipo_local": eq_local,
                    "equipo_visitante": eq_visit,
                    "subtitulo": ev.get("strEvent", "") if tipo_evento_api == "sencillo" else "",
                    "hora_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "duracion_min": DURACION_POR_DEPORTE.get(deporte_es, 150),
                    "logo_torneo": ev.get("strLeagueBadge", "") or ev.get("strBadge", "") or "",
                    "logo_local": ev.get("strHomeTeamBadge", "") or ev.get("strThumb", "") or "",
                    "logo_visitante": ev.get("strAwayTeamBadge", "") or "",
                    "tier": 2,
                })
            except Exception:
                continue

    if eventos_api:
        with open(ARCHIVO_CACHE, "w", encoding="utf-8") as f:
            json.dump(eventos_api, f, ensure_ascii=False, indent=2)
    return eventos_api


def obtener_variaciones_fecha(dt: datetime) -> list:
    """Genera variaciones de fecha para identificar categorías del día."""
    d, m = dt.strftime("%d"), dt.strftime("%m")
    meses_es = {"01":"ENE","02":"FEB","03":"MAR","04":"ABR","05":"MAY","06":"JUN",
                "07":"JUL","08":"AGO","09":"SEP","10":"OCT","11":"NOV","12":"DIC"}
    meses_en = {"01":"JAN","02":"FEB","03":"MAR","04":"APR","05":"MAY","06":"JUN",
                "07":"JUL","08":"AUG","09":"SEP","10":"OCT","11":"NOV","12":"DEC"}
    m_es, m_en = meses_es[m], meses_en[m]
    return [
        f"{d}/{m}", f"{d}-{m}", f"{d} {m}", f"{d}.{m}",
        f"{m}/{d}", f"{m}-{d}", f"{m} {d}", f"{m}.{d}",
        f"{d} {m_es}", f"{m_es} {d}", f"{d} {m_en}", f"{m_en} {d}",
        f"{m_en} {int(d)}",
    ]


def procesar_cubo_a(tz_servidor, hoy_servidor: datetime) -> list:
    """Extrae canales de eventos de hoy desde Xtream."""
    log.info("Extrayendo canales candidatos desde Xtream...")
    base_url = XTREAM_URL.rstrip("/")
    api_url = f"{base_url}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
    cubo_a = []

    fechas_hoy = obtener_variaciones_fecha(hoy_servidor)
    palabras_principales = ["HOY", "TODAY", "DAILY", "DIARIO", "DIA", "VIVO", "LIVE", "EVENTS", "EVENTOS"]
    palabras_contexto = ["EVENTOS", "EVENTS", "AGENDA", "PARTIDOS", "CARTELERA", "CALENDARIO", "PPV", "SPORT"]

    try:
        url_cat = f"{api_url}&action=get_live_categories"
        r_cat = requests.get(PUENTE_URL, params={"url": url_cat}, timeout=45)
        categorias_hoy_ids = set()

        if r_cat.status_code == 200:
            for cat in r_cat.json():
                nombre_cat = cat.get("category_name", "").upper()
                match_fecha = any(f in nombre_cat for f in fechas_hoy)
                tiene_prin = any(p in nombre_cat for p in palabras_principales)
                tiene_ctx = any(c in nombre_cat for c in palabras_contexto)
                if match_fecha or (tiene_prin and tiene_ctx):
                    categorias_hoy_ids.add(str(cat.get("category_id")))
            log.info(f"Categorías de eventos hoy detectadas: {len(categorias_hoy_ids)}")

        url_str = f"{api_url}&action=get_live_streams"
        r_str = requests.get(PUENTE_URL, params={"url": url_str}, timeout=60)
        if r_str.status_code != 200:
            log.error(f"Error obteniendo streams: HTTP {r_str.status_code}")
            return []

        streams = r_str.json()
        log.info(f"Total streams recibidos sin filtrar: {len(streams)}")

        vistos = set()
        for s in streams:
            stream_id = str(s.get("stream_id"))
            if stream_id in vistos:
                continue
            vistos.add(stream_id)

            nombre_canal = s.get("name", "").strip()
            if not nombre_canal:
                continue

            cat_id = str(s.get("category_id"))
            pertenece_cat = (not categorias_hoy_ids) or (cat_id in categorias_hoy_ids)
            hora_dt = extraer_hora_canal(nombre_canal, hoy_servidor)
            tiene_kw = any(kw in nombre_canal.upper() for kw in ["PPV", "LIVE EVENT", "PEA ", "PARA+"])

            if pertenece_cat and (hora_dt is not None or tiene_kw):
                hora_local = hora_dt.replace(tzinfo=tz_servidor) if hora_dt else None
                cubo_a.append({
                    "id_xtream": stream_id,
                    "nombre_ui": nombre_canal,
                    "hora_local": hora_local,
                    "texto_normalizado": normalizar_texto(nombre_canal)
                })

        log.info(f"Canales candidatos en Cubo A: {len(cubo_a)}")
        return cubo_a
    except Exception as e:
        log.error(f"Error procesando Xtream: {e}")
        return []


def detectar_base_media_m3u() -> str:
    """Obtiene el host real de streaming para reproducir los .ts."""
    base_api = XTREAM_URL.rstrip("/")
    url_m3u = f"{base_api}/get.php?username={XTREAM_USER}&password={XTREAM_PASS}&type=m3u&output=ts"
    candidatos = []
    try:
        with requests.get(url_m3u, headers={"Range": "bytes=0-65535"}, stream=True, timeout=(15, 30)) as r:
            if r.status_code not in (200, 206):
                return base_api
            for linea_bytes in r.iter_lines(chunk_size=8192):
                linea = linea_bytes.decode("utf-8", errors="ignore").strip()
                if not linea.startswith(("http://", "https://")):
                    continue
                parsed = urlparse(linea)
                partes = [p for p in parsed.path.split("/") if p]
                if len(partes) >= 3 and partes[-3] == XTREAM_USER and partes[-2] == XTREAM_PASS:
                    candidatos.append(f"{parsed.scheme}://{parsed.netloc}")
                if len(candidatos) >= 3:
                    break

        if len(candidatos) >= 3 and len(set(candidatos)) == 1:
            base_media = candidatos[0]
            log.info(f"Base de media detectada desde M3U: {base_media}")
            return base_media
    except Exception:
        pass
    return base_api


def emparejar_evento(canal: dict, duelos_api: list, sencillos_api: list) -> tuple:
    """
    Ejecuta el Reverse Matching determinista:
    1. Vía Duelos: Coincidencia simultánea de ambos rivales y proximidad horaria.
    2. Vía Sencillos: Presencia del título del torneo/carrera en el texto.
    """
    texto_canal = canal["texto_normalizado"]
    palabras_canal = set(texto_canal.split())
    hora_canal = canal["hora_local"]

    # ── VÍA 1: DUELOS (Fútbol, Baloncesto, Tenis, Béisbol, NFL, etc.) ─────────
    for ev in duelos_api:
        palabras_local = obtener_palabras_clave(ev["equipo_local"])
        palabras_visit = obtener_palabras_clave(ev["equipo_visitante"])

        if not palabras_local or not palabras_visit:
            continue

        match_local = bool(palabras_local.intersection(palabras_canal))
        match_visit = bool(palabras_visit.intersection(palabras_canal))

        # Requiere coincidencia de ambos rivales (independiente de si hay 'vs', '@' o '-')
        if match_local and match_visit:
            if hora_canal and ev.get("hora_utc"):
                try:
                    dt_api_utc = datetime.strptime(ev["hora_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    dt_canal_utc = hora_canal.astimezone(timezone.utc)
                    diff_min = abs((dt_api_utc - dt_canal_utc).total_seconds()) / 60.0
                    # Si difiere más de 3 horas, no es el mismo partido
                    if diff_min > 180:
                        continue
                except Exception:
                    pass
            return ev, "Match Duelo Exitoso"

    # ── VÍA 2: SENCILLOS (F1, UFC, Rally, Golf, Ciclismo, Boxeo) ──────────────
    for ev in sencillos_api:
        palabras_evento = obtener_palabras_clave(ev["titulo"], filtrar_stopwords=False)
        palabras_torneo = obtener_palabras_clave(ev["torneo"], filtrar_stopwords=False)
        palabras_totales = palabras_evento.union(palabras_torneo)

        if not palabras_totales:
            continue

        coincidencias = palabras_totales.intersection(palabras_canal)
        if len(coincidencias) >= 2 or (len(palabras_totales) == 1 and len(coincidencias) == 1):
            return ev, "Match Sencillo Exitoso"

    return None, "Sin coincidencia en agenda oficial"


def main():
    log.info("=== Iniciando Curador Universal Directo (TheSportsDB + Xtream) ===")

    if not XTREAM_URL or not XTREAM_USER or not XTREAM_PASS:
        log.error("Faltan variables de entorno de Xtream (XTREAM_URL, XTREAM_USER, XTREAM_PASS).")
        return

    # 1. Obtener zona horaria del servidor
    tz_servidor, hoy_servidor = obtener_info_servidor_xtream()
    fecha_hoy_str = hoy_servidor.strftime("%Y-%m-%d")

    # 2. Descargar Agenda Maestra
    agenda_completa = obtener_agenda_maestra(fecha_hoy_str)
    duelos_api = [e for e in agenda_completa if e["tipo_evento"] == "duelo"]
    sencillos_api = [e for e in agenda_completa if e["tipo_evento"] == "sencillo"]
    log.info(f"Agenda Maestra lista: {len(duelos_api)} duelos | {len(sencillos_api)} eventos sencillos")

    # 3. Procesar canales temporales
    cubo_a = procesar_cubo_a(tz_servidor, hoy_servidor)
    if not cubo_a:
        log.warning("No se detectaron eventos temporales en Xtream.")
        return

    base_media = detectar_base_media_m3u()

    # 4. Emparejamiento Directo
    eventos_vip_dict = {}
    eventos_cuarentena = []

    for canal in cubo_a:
        ev_match, motivo = emparejar_evento(canal, duelos_api, sencillos_api)
        fuente_stream = {
            "nombre": canal["nombre_ui"],
            "id_xtream": canal["id_xtream"]
        }

        if ev_match:
            ev_id = ev_match["id"]
            if ev_id in eventos_vip_dict:
                # Si el evento ya existe, solo añadimos la nueva opción de stream
                if not any(f["id_xtream"] == fuente_stream["id_xtream"] for f in eventos_vip_dict[ev_id]["fuentes"]):
                    eventos_vip_dict[ev_id]["fuentes"].append(fuente_stream)
            else:
                evento_clon = ev_match.copy()
                evento_clon["fuentes"] = [fuente_stream]
                eventos_vip_dict[ev_id] = evento_clon
        else:
            fuente_cuarentena = fuente_stream.copy()
            fuente_cuarentena["motivo"] = motivo
            fuente_cuarentena["texto_analizado"] = canal["texto_normalizado"]
            eventos_cuarentena.append(fuente_cuarentena)

    resultados_vip = list(eventos_vip_dict.values())
    resultados_vip.sort(key=lambda x: x["hora_utc"])

    # 5. Guardado de JSONs
    salida_vip = {
        "generado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base_media": base_media,
        "eventos": resultados_vip
    }

    try:
        with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as f_out:
            json.dump(salida_vip, f_out, ensure_ascii=False, indent=2)

        with open(ARCHIVO_CUARENTENA, "w", encoding="utf-8") as f_cuar:
            json.dump(eventos_cuarentena, f_cuar, ensure_ascii=False, indent=2)

        log.info(f"¡Proceso finalizado! Eventos VIP en JSON: {len(resultados_vip)} | En cuarentena: {len(eventos_cuarentena)}")
    except Exception as e:
        log.error(f"Error escribiendo archivos de salida: {e}")


if __name__ == "__main__":
    main()
