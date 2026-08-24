# -*- coding: utf-8 -*-
"""Inyector multicanal oficial (Eurosport, RTVE, ESPN, Win Sports Claro, DSports) con fallback XMLTV y proxy."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

from curador_eventos import (
    ARCHIVO_META,
    ARCHIVO_SALIDA,
    DEPORTES_INDIVIDUALES,
    DURACION_POR_CATEGORIA,
    PUENTE_URL,
    XTREAM_PASS,
    XTREAM_URL,
    XTREAM_USER,
    cargar_agenda_cache,
    contiene_veto,
    fusionar_fuente,
    generar_huella_canonica,
    inferir_deporte,
    iso_utc,
    normalizar_texto,
    obtener_agenda_maestra,
    obtener_zona_aplicacion,
)
from resolvedor_logos import envolver_cdn_proxy, resolver_logo_torneo

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("inyector_canales_vivo")

HEADERS_DEFAULT = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

REGEX_PAIS_EXTRANJERO = re.compile(r"\b(DE|FR|UK|EN|PT|IT|GERMAN|FRENCH)\b", re.I)
PATRON_TEMPORADA_HISTORICA = re.compile(r"\b(19\d\d|20[0-1]\d|202[0-5])\b|\bT(19\d\d|20[0-1]\d|202[0-5]|2[0-5])\b", re.I)


def llamada_proxy(url: str, headers: Optional[dict[str, str]] = None, timeout: int = 15, usar_proxy: bool = True) -> Optional[requests.Response]:
    """Ejecuta peticiones canalizándolas por PUENTE_URL (Render) si está disponible, con fallback directo."""
    h = headers or HEADERS_DEFAULT
    if usar_proxy and PUENTE_URL:
        try:
            resp = requests.get(PUENTE_URL, params={"url": url}, headers=h, timeout=timeout)
            if resp.status_code == 200:
                return resp
        except Exception as exc:
            log.debug("Proxy Render falló para %s: %s. Reintentando directo...", url, exc)

    try:
        resp = requests.get(url, headers=h, timeout=timeout)
        return resp if resp.status_code == 200 else None
    except Exception as exc:
        log.warning("Petición fallida a %s: %s", url, exc)
        return None


def clasificar_canal_lineal(nombre: str) -> Optional[str]:
    """Clasifica canales lineales de Xtream aislando códigos de país mediante límites de palabra."""
    n = normalizar_texto(nombre)
    if "DAZN" in n:
        return None

    # 1. Teledeporte España (descarta si 'DE' es código de país aislado)
    if "TELEDEPORTE" in n or re.search(r"\bTDP\b", n):
        n_sin_tdp = n.replace("TELEDEPORTE", "").replace("TDP", "")
        if not REGEX_PAIS_EXTRANJERO.search(n_sin_tdp):
            return "TDP"

    # 2. Eurosport 1 España
    if re.search(r"\bEUROSPORTS?\s*1\b", n) and not re.search(r"\bEUROSPORTS?\s*2\b", n):
        if not REGEX_PAIS_EXTRANJERO.search(n):
            return "E1"

    # 3. Eurosport 2 España
    if re.search(r"\bEUROSPORTS?\s*2\b", n):
        if not REGEX_PAIS_EXTRANJERO.search(n):
            return "E2"

    # 4. Win Sports Colombia
    if "WIN SPORTS" in n or "WIN+" in n or "WIN PLUS" in n:
        return "WIN_PLUS" if ("+" in n or "PLUS" in n or "PREMIUM" in n) else "WIN_BASICO"

    # 5. DSports / DIRECTV Sports
    if "DSPORTS" in n or "DIRECTV SPORTS" in n or "D SPORTS" in n:
        if "2" in n:
            return "DSPORTS_2"
        if "+" in n or "PLUS" in n:
            return "DSPORTS_PLUS"
        return "DSPORTS_1"

    # 6. ESPN Familia
    if "ESPN" in n and not any(x in n for x in ["BR", "BRASIL", "USA"]):
        for i in range(1, 8):
            if f"ESPN {i}" in n or f"ESPN{i}" in n:
                return f"ESPN_{i}"
        if "EXTRA" in n:
            return "ESPN_EXTRA"
        if "PREMIUM" in n:
            return "ESPN_PREMIUM"
        return "ESPN_1"

    return None


def mapear_canales_lineales_xtream() -> dict[str, list[dict[str, Any]]]:
    mapa: dict[str, list[dict[str, Any]]] = {}
    if not (XTREAM_URL and XTREAM_USER and XTREAM_PASS):
        return mapa
    url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
    resp = llamada_proxy(url, timeout=75, usar_proxy=True)
    if not resp:
        return mapa
    try:
        streams = resp.json() or []
    except Exception:
        return mapa

    for stream in streams:
        nombre = str(stream.get("name") or "").strip()
        sid = str(stream.get("stream_id") or "")
        clave = clasificar_canal_lineal(nombre)
        if clave and sid:
            mapa.setdefault(clave, []).append({"nombre": nombre, "id_xtream": sid})
    return mapa


# ─── Adaptador 1: Eurosport España (API Discovery) ──────────────────────────
def extraer_eventos_eurosport(fecha_colombia: str, canales_map: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    eventos: list[dict[str, Any]] = []
    try:
        resp_tok = llamada_proxy("https://eu3-prod-direct.eurosport.es/token?realm=eurosport", timeout=8)
        if not resp_tok:
            return []
        token = resp_tok.json().get("data", {}).get("attributes", {}).get("token")
        if not token:
            return []

        h = {**HEADERS_DEFAULT, "Authorization": f"Bearer {token}"}
        url_grid = f"https://eu3-prod-direct.eurosport.es/cms/routes/watch/schedule?date={fecha_colombia}"
        resp_grid = llamada_proxy(url_grid, headers=h, timeout=10)
        if not resp_grid:
            return []

        items = resp_grid.json().get("data", {}).get("attributes", {}).get("scheduleItems", [])
        for item in items:
            material_type = str(item.get("materialType", "")).upper()
            is_live = item.get("live") is True or material_type == "LIVE"
            if not is_live:
                continue

            titulo = str(item.get("title") or item.get("name") or "").strip()
            desc = str(item.get("description") or item.get("subtitle") or "").strip()
            if contiene_veto(f"{titulo} {desc}") or PATRON_TEMPORADA_HISTORICA.search(f"{titulo} {desc}"):
                continue

            canal_nombre = str(item.get("channelName", "")).upper()
            clave_canal = "E2" if "2" in canal_nombre else "E1"
            fuentes = canales_map.get(clave_canal, [])

            inicio_utc = item.get("start") or item.get("startTime")
            fin_utc = item.get("end") or item.get("endTime")
            dt_inicio = datetime.fromisoformat(inicio_utc.replace("Z", "+00:00")) if inicio_utc else None
            dt_fin = datetime.fromisoformat(fin_utc.replace("Z", "+00:00")) if fin_utc else None
            if not dt_inicio:
                continue

            duracion = max(15, int((dt_fin - dt_inicio).total_seconds() / 60)) if dt_fin else 120
            categoria = inferir_deporte(f"{titulo} {desc}") or "Deportes"
            logo_img = item.get("images", {}).get("logo") or item.get("images", {}).get("poster")
            logo_final = envolver_cdn_proxy(logo_img) if logo_img else resolver_logo_torneo(titulo, categoria)

            eventos.append({
                "id": f"eurosport_{hashlib.sha1(f'{titulo}|{inicio_utc}'.encode()).hexdigest()[:14]}",
                "agenda_id": "", "titulo": titulo, "torneo": titulo, "categoria": categoria,
                "tipo_evento": "sencillo", "equipo_local": "", "equipo_visitante": "",
                "subtitulo": desc, "hora_utc": iso_utc(dt_inicio),
                "hora_local_producto": dt_inicio.astimezone(obtener_zona_aplicacion()).strftime("%H:%M"),
                "duracion_min": duracion, "logo_torneo": logo_final, "logo_local": logo_final, "logo_visitante": "",
                "tier": 2, "origen": "eurosport_oficial", "origenes": ["eurosport_oficial"],
                "estado": "confirmado", "estado_evento": "programado", "confianza": "alta", "puntuacion_confianza": 95,
                "metodo_correlacion": "api_oficial_eurosport", "razones_correlacion": ["directo:true", f"canal:{clave_canal}"],
                "fuentes": fuentes,
            })
    except Exception as exc:
        log.warning("Adaptador Eurosport Discovery falló: %s", exc)
    return eventos


# ─── Adaptador 2: RTVE Teledeporte ──────────────────────────────────────────
def extraer_eventos_teledeporte(fecha_colombia: str, canales_map: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    eventos: list[dict[str, Any]] = []
    fuentes = canales_map.get("TDP", [])

    try:
        url = f"https://www.rtve.es/api/parrilla/tve/teledeporte/{fecha_colombia}.json"
        resp = llamada_proxy(url, headers={**HEADERS_DEFAULT, "Referer": "https://www.rtve.es/"}, timeout=8)
        if not resp:
            return []

        programas = resp.json().get("parrilla", {}).get("programas", [])
        for prog in programas:
            es_directo = bool(prog.get("directo") is True or str(prog.get("directo")).lower() in {"true", "1", "si", "directo"})
            if not es_directo:
                continue

            titulo = str(prog.get("title") or prog.get("name") or "").strip()
            desc = str(prog.get("desc") or prog.get("description") or "").strip()
            if contiene_veto(f"{titulo} {desc}") or PATRON_TEMPORADA_HISTORICA.search(f"{titulo} {desc}"):
                continue

            h_ini = prog.get("hora_inicio") or prog.get("start")
            h_fin = prog.get("hora_fin") or prog.get("end")
            dt_inicio = datetime.fromisoformat(h_ini.replace("Z", "+00:00")) if (h_ini and "T" in h_ini) else None
            dt_fin = datetime.fromisoformat(h_fin.replace("Z", "+00:00")) if (h_fin and "T" in h_fin) else None

            if not dt_inicio and h_ini and ":" in h_ini:
                partes = h_ini.split(":")
                tz_esp = timezone(timedelta(hours=2))
                dt_inicio = datetime.strptime(f"{fecha_colombia} {partes[0]}:{partes[1]}", "%Y-%m-%d %H:%M").replace(tzinfo=tz_esp).astimezone(timezone.utc)

            if not dt_inicio:
                continue

            duracion = max(15, int((dt_fin - dt_inicio).total_seconds() / 60)) if (dt_fin and dt_inicio) else 90
            categoria = inferir_deporte(f"{titulo} {desc}") or "Deportes"
            logo_final = resolver_logo_torneo(titulo, categoria)

            eventos.append({
                "id": f"rtve_tdp_{hashlib.sha1(f'{titulo}|{dt_inicio.isoformat()}'.encode()).hexdigest()[:14]}",
                "agenda_id": "", "titulo": titulo, "torneo": titulo, "categoria": categoria,
                "tipo_evento": "sencillo", "equipo_local": "", "equipo_visitante": "",
                "subtitulo": desc, "hora_utc": iso_utc(dt_inicio),
                "hora_local_producto": dt_inicio.astimezone(obtener_zona_aplicacion()).strftime("%H:%M"),
                "duracion_min": duracion, "logo_torneo": logo_final, "logo_local": logo_final, "logo_visitante": "",
                "tier": 2, "origen": "rtve_oficial", "origenes": ["rtve_oficial"],
                "estado": "confirmado", "estado_evento": "programado", "confianza": "alta", "puntuacion_confianza": 95,
                "metodo_correlacion": "api_oficial_rtve", "razones_correlacion": ["directo:true", "canal:TDP"],
                "fuentes": fuentes,
            })
    except Exception as exc:
        log.warning("Adaptador RTVE falló: %s", exc)
    return eventos


# ─── Adaptador 3: ESPN Latinoamérica ────────────────────────────────────────
def extraer_eventos_espn(fecha_colombia: str, canales_map: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    eventos: list[dict[str, Any]] = []
    fecha_compacta = fecha_colombia.replace("-", "")

    ligas_espn = [
        ("soccer", "col.1", "Fútbol", "Liga BetPlay"),
        ("soccer", "esp.1", "Fútbol", "LaLiga"),
        ("soccer", "eng.1", "Fútbol", "Premier League"),
        ("soccer", "ita.1", "Fútbol", "Serie A"),
        ("soccer", "uefa.champions", "Fútbol", "Champions League"),
        ("soccer", "conmebol.libertadores", "Fútbol", "Copa Libertadores"),
        ("soccer", "conmebol.sudamericana", "Fútbol", "Copa Sudamericana"),
        ("tennis", "atp", "Tenis", "ATP Tour"),
        ("tennis", "wta", "Tenis", "WTA Tour"),
        ("baseball", "mlb", "Béisbol", "MLB"),
        ("basketball", "nba", "Baloncesto", "NBA"),
        ("basketball", "wnba", "Baloncesto", "WNBA"),
    ]

    for deporte_slug, liga_slug, categoria, torneo_default in ligas_espn:
        url = f"https://site.api.espn.com/apis/site/v2/sports/{deporte_slug}/{liga_slug}/scoreboard?dates={fecha_compacta}"
        resp = llamada_proxy(url, timeout=6, usar_proxy=False)
        if not resp:
            continue
        try:
            datos = resp.json()
            for ev in datos.get("events", []):
                competencia = ev.get("competitions", [{}])[0]
                competidores = competencia.get("competitors", [])
                if len(competidores) < 2:
                    continue

                local_raw = next((c for c in competidores if c.get("homeAway") == "home"), competidores[0])
                visita_raw = next((c for c in competidores if c.get("homeAway") == "away"), competidores[1])
                local = local_raw.get("team", {}).get("displayName", "")
                visita = visita_raw.get("team", {}).get("displayName", "")
                logo_local = local_raw.get("team", {}).get("logo", "")
                logo_visita = visita_raw.get("team", {}).get("logo", "")

                fecha_iso = competencia.get("date")
                dt_inicio = datetime.fromisoformat(fecha_iso.replace("Z", "+00:00")) if fecha_iso else None
                if not dt_inicio:
                    continue

                broadcasts = competencia.get("broadcasts", [])
                nombres_bcast = [b.get("names", []) for b in broadcasts]
                canales_texto = normalizar_texto(" ".join([item for sub in nombres_bcast for item in sub]))

                fuentes: list[dict[str, Any]] = []
                for k, v in canales_map.items():
                    if k.startswith("ESPN") and (k.replace("_", " ") in canales_texto or not canales_texto):
                        fuentes.extend(v)

                eventos.append({
                    "id": f"espn_{ev.get('id')}", "agenda_id": f"espn_{ev.get('id')}",
                    "titulo": f"{local} vs {visita}", "torneo": torneo_default, "categoria": categoria,
                    "tipo_evento": "duelo", "equipo_local": local, "equipo_visitante": visita,
                    "subtitulo": "", "hora_utc": iso_utc(dt_inicio),
                    "hora_local_producto": dt_inicio.astimezone(obtener_zona_aplicacion()).strftime("%H:%M"),
                    "duracion_min": DURACION_POR_CATEGORIA.get(categoria, 130),
                    "logo_torneo": resolver_logo_torneo(torneo_default, categoria),
                    "logo_local": envolver_cdn_proxy(logo_local),
                    "logo_visitante": envolver_cdn_proxy(logo_visita),
                    "tier": 1, "origen": "espn_oficial", "origenes": ["espn_oficial"],
                    "estado": "confirmado", "estado_evento": "programado", "confianza": "alta", "puntuacion_confianza": 98,
                    "metodo_correlacion": "api_oficial_espn", "razones_correlacion": [f"liga:{liga_slug}"],
                    "fuentes": fuentes,
                })
        except Exception:
            continue
    return eventos


# ─── Adaptador 4: Win Sports & Win+ (Claro Video Colombia EPG) ──────────────
def extraer_eventos_claro_winsports(fecha_colombia: str, canales_map: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    eventos: list[dict[str, Any]] = []
    fecha_compacta = fecha_colombia.replace("-", "")
    url = (
        f"https://mfwkweb-api.clarovideo.net/services/epg/channel?"
        f"device_category=web&device_model=web&device_type=web&device_so=Chrome&format=json&"
        f"date_from={fecha_compacta}000000&date_to={fecha_compacta}235959&quantity_channels=50"
    )

    resp = llamada_proxy(url, timeout=12, usar_proxy=True)
    if not resp:
        return []

    try:
        canales = resp.json().get("response", {}).get("channels", [])
        for canal in canales:
            c_name = normalizar_texto(canal.get("name", ""))
            if "WIN" not in c_name:
                continue

            es_plus = "+" in c_name or "PLUS" in c_name or "PREMIUM" in c_name
            fuentes = canales_map.get("WIN_PLUS" if es_plus else "WIN_BASICO", [])

            for prog in canal.get("events", []):
                titulo = str(prog.get("name") or "").strip()
                desc = str(prog.get("description") or "").strip()
                texto_analizar = f"{titulo} {desc}"

                if contiene_veto(texto_analizar) or PATRON_TEMPORADA_HISTORICA.search(texto_analizar):
                    continue

                es_vivo = bool(re.search(r"\b(VIVO|EN VIVO|DIRECTO|LIVE|\[VIVO\]|\(VIVO\))\b", normalizar_texto(texto_analizar)))
                if not es_vivo:
                    continue

                h_ini = prog.get("date_begin")
                h_fin = prog.get("date_end")
                dt_inicio = parsear_iso_o_timestamp(h_ini)
                dt_fin = parsear_iso_o_timestamp(h_fin)
                if not dt_inicio:
                    continue

                duracion = max(20, int((dt_fin - dt_inicio).total_seconds() / 60)) if (dt_fin and dt_inicio) else 120
                categoria = inferir_deporte(texto_analizar) or "Fútbol"
                torneo = "Liga BetPlay Dimayor" if "LIGA" in normalizar_texto(texto_analizar) else (prog.get("parental_rating") or "Win Sports Evento")
                logo_final = resolver_logo_torneo(torneo, categoria)

                duelo_match = re.search(r"(.+?)\s+(?:vs\.?|v\.?|versus)\s+(.+)", titulo, re.I)
                tipo = "duelo" if duelo_match and categoria not in DEPORTES_INDIVIDUALES else "sencillo"
                local = duelo_match.group(1).strip() if tipo == "duelo" else ""
                visitante = duelo_match.group(2).strip() if tipo == "duelo" else ""

                eventos.append({
                    "id": f"winsports_{hashlib.sha1(f'{titulo}|{dt_inicio.isoformat()}'.encode()).hexdigest()[:14]}",
                    "agenda_id": "", "titulo": titulo, "torneo": torneo, "categoria": categoria,
                    "tipo_evento": tipo, "equipo_local": local, "equipo_visitante": visitante,
                    "subtitulo": desc, "hora_utc": iso_utc(dt_inicio),
                    "hora_local_producto": dt_inicio.astimezone(obtener_zona_aplicacion()).strftime("%H:%M"),
                    "duracion_min": duracion, "logo_torneo": logo_final, "logo_local": logo_final, "logo_visitante": "",
                    "tier": 2, "origen": "winsports_claro_oficial", "origenes": ["winsports_claro_oficial"],
                    "estado": "confirmado", "estado_evento": "programado", "confianza": "alta", "puntuacion_confianza": 95,
                    "metodo_correlacion": "api_claro_winsports", "razones_correlacion": ["directo:true"],
                    "fuentes": fuentes,
                })
    except Exception as exc:
        log.warning("Adaptador Claro Win Sports falló: %s", exc)
    return eventos


# ─── Adaptador 5: DSports / DIRECTV Sports (DGO API) ─────────────────────────
def extraer_eventos_dsports(fecha_colombia: str, canales_map: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    eventos: list[dict[str, Any]] = []
    url = f"https://api.directvgo.com/epg/v1/programs?country=CO&device=web&date={fecha_colombia}"
    h = {**HEADERS_DEFAULT, "Origin": "https://www.directvgo.com", "Referer": "https://www.directvgo.com/"}
    resp = llamada_proxy(url, headers=h, timeout=10, usar_proxy=True)
    if not resp:
        return []

    try:
        canales = resp.json().get("channels", [])
        for canal in canales:
            c_name = normalizar_texto(canal.get("channelName", ""))
            if "DSPORTS" not in c_name and "DIRECTV SPORTS" not in c_name:
                continue

            clave_canal = "DSPORTS_2" if " 2" in c_name else ("DSPORTS_PLUS" if "+" in c_name or "PLUS" in c_name else "DSPORTS_1")
            fuentes = canales_map.get(clave_canal, [])

            for prog in canal.get("programs", []):
                if not prog.get("isLive") is True:
                    continue

                titulo = str(prog.get("title") or "").strip()
                desc = str(prog.get("description") or "").strip()
                if not titulo or contiene_veto(f"{titulo} {desc}") or PATRON_TEMPORADA_HISTORICA.search(f"{titulo} {desc}"):
                    continue

                h_ini = prog.get("startDate")
                dt_inicio = datetime.fromisoformat(h_ini.replace("Z", "+00:00")) if h_ini else None
                if not dt_inicio:
                    continue

                categoria = inferir_deporte(f"{titulo} {desc}") or "Deportes"
                logo_prog = prog.get("images", {}).get("poster")
                logo_final = envolver_cdn_proxy(logo_prog) if logo_prog else resolver_logo_torneo(titulo, categoria)

                eventos.append({
                    "id": f"dsports_{hashlib.sha1(f'{titulo}|{dt_inicio.isoformat()}'.encode()).hexdigest()[:14]}",
                    "agenda_id": "", "titulo": titulo, "torneo": titulo, "categoria": categoria,
                    "tipo_evento": "duelo" if " vs " in titulo.lower() else "sencillo",
                    "equipo_local": titulo.split(" vs ")[0].strip() if " vs " in titulo.lower() else "",
                    "equipo_visitante": titulo.split(" vs ")[1].strip() if " vs " in titulo.lower() else "",
                    "subtitulo": desc, "hora_utc": iso_utc(dt_inicio),
                    "hora_local_producto": dt_inicio.astimezone(obtener_zona_aplicacion()).strftime("%H:%M"),
                    "duracion_min": 130, "logo_torneo": logo_final, "logo_local": logo_final, "logo_visitante": "",
                    "tier": 2, "origen": "dsports_oficial", "origenes": ["dsports_oficial"],
                    "estado": "confirmado", "estado_evento": "programado", "confianza": "alta", "puntuacion_confianza": 95,
                    "metodo_correlacion": "api_oficial_dsports", "razones_correlacion": ["isLive:true"],
                    "fuentes": fuentes,
                })
    except Exception as exc:
        log.warning("Adaptador DSports falló: %s", exc)
    return eventos


# ─── Adaptador 6: Red de Seguridad XMLTV Multi-Feed ─────────────────────────
def extraer_eventos_xmltv_fallback(fecha_colombia: str, canales_map: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Red de seguridad que descarga el XMLTV solo si las APIs oficiales devuelven 0."""
    eventos: list[dict[str, Any]] = []
    url = "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml"
    resp = llamada_proxy(url, timeout=30, usar_proxy=False)
    if not resp:
        return []

    tz = obtener_zona_aplicacion()
    programas_todos: list[dict[str, Any]] = []
    consenso_live: dict[tuple[str, str], bool] = {}

    try:
        for _, el in ET.iterparse(io.BytesIO(resp.content), events=("end",)):
            if el.tag != "programme":
                continue
            canal = el.attrib.get("channel", "")
            clave = clasificar_canal_lineal(canal)
            start_raw = el.attrib.get("start", "")
            stop_raw = el.attrib.get("stop", "")

            if clave and start_raw:
                dt_inicio = datetime.strptime(start_raw[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                dt_fin = datetime.strptime(stop_raw[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc) if stop_raw else None
                if dt_inicio.astimezone(tz).date().isoformat() == fecha_colombia:
                    tit = el.findtext("title", "") or ""
                    desc = el.findtext("desc", "") or ""
                    es_live = bool(re.search(r"\b(DIRECTO|VIVO|LIVE|DIREKT|EN DIRECT)\b", normalizar_texto(f"{tit} {desc}")))
                    slot = dt_inicio.strftime("%Y%m%d%H%M")
                    if es_live:
                        consenso_live[(clave, slot)] = True

                    programas_todos.append({
                        "clave": clave, "canal": canal, "inicio": dt_inicio, "fin": dt_fin,
                        "titulo": tit, "desc": desc, "es_live": es_live
                    })
            el.clear()
    except Exception as exc:
        log.warning("Fallo parseo XMLTV fallback: %s", exc)
        return []

    for p in programas_todos:
        if not (p["es_live"] or consenso_live.get((p["clave"], p["inicio"].strftime("%Y%m%d%H%M")), False)):
            continue
        tit, desc = p["titulo"], p["desc"]
        if contiene_veto(f"{tit} {desc}") or PATRON_TEMPORADA_HISTORICA.search(f"{tit} {desc}"):
            continue

        categoria = inferir_deporte(f"{tit} {desc}") or "Deportes"
        logo_final = resolver_logo_torneo(tit, categoria)
        fuentes = canales_map.get(p["clave"], [])

        eventos.append({
            "id": f"xmltv_{hashlib.sha1(f'{tit}|{p['inicio'].isoformat()}'.encode()).hexdigest()[:14]}",
            "agenda_id": "", "titulo": tit, "torneo": tit, "categoria": categoria,
            "tipo_evento": "sencillo", "equipo_local": "", "equipo_visitante": "",
            "subtitulo": desc, "hora_utc": iso_utc(p["inicio"]),
            "hora_local_producto": p["inicio"].astimezone(tz).strftime("%H:%M"),
            "duracion_min": max(20, int((p["fin"] - p["inicio"]).total_seconds() / 60)) if p["fin"] else 120,
            "logo_torneo": logo_final, "logo_local": logo_final, "logo_visitante": "",
            "tier": 2, "origen": "xmltv_consenso_fallback", "origenes": ["xmltv_consenso_fallback"],
            "estado": "confirmado", "estado_evento": "programado", "confianza": "alta", "puntuacion_confianza": 88,
            "metodo_correlacion": "xmltv_consenso_fallback", "razones_correlacion": ["directo:true"],
            "fuentes": fuentes,
        })
    return eventos


# ─── Motor de Fusión Canónica Universal ──────────────────────────────────────
def fusionar_eventos_multicanal(eventos_base: list[dict[str, Any]], eventos_nuevos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indice_canonica: dict[str, dict[str, Any]] = {}

    for ev in eventos_base + eventos_nuevos:
        huella = generar_huella_canonica(ev)
        existente = indice_canonica.get(huella)

        if existente is None:
            clon = {k: v for k, v in ev.items() if k != "fuentes"}
            clon["fuentes"] = []
            for f in ev.get("fuentes", []):
                fusionar_fuente(clon, f)
            indice_canonica[huella] = clon
        else:
            if not existente.get("logo_local") and ev.get("logo_local"):
                existente["logo_local"] = ev["logo_local"]
            if not existente.get("logo_visitante") and ev.get("logo_visitante"):
                existente["logo_visitante"] = ev["logo_visitante"]
            if not existente.get("logo_torneo") and ev.get("logo_torneo"):
                existente["logo_torneo"] = ev["logo_torneo"]

            existente["puntuacion_confianza"] = max(
                int(existente.get("puntuacion_confianza", 0)),
                int(ev.get("puntuacion_confianza", 0))
            )
            existente["origenes"] = list(dict.fromkeys(list(existente.get("origenes", [])) + list(ev.get("origenes", []))))

            for f in ev.get("fuentes", []):
                fusionar_fuente(existente, f)

    return sorted(indice_canonica.values(), key=lambda e: e["hora_utc"])


def main() -> None:
    tz, ahora = obtener_zona_aplicacion(), datetime.now(timezone.utc)
    fecha_colombia = ahora.astimezone(tz).date().isoformat()
    log.info("=== Inyector Multicanal Oficial v14 | Colombia %s ===", fecha_colombia)

    try:
        salida = json.loads(ARCHIVO_SALIDA.read_text(encoding="utf-8")) if ARCHIVO_SALIDA.exists() else {"version": 11, "eventos": []}
    except Exception:
        salida = {"version": 11, "eventos": []}

    eventos_base = list(salida.get("eventos") or [])
    canales_lineales = mapear_canales_lineales_xtream()

    # 1. Extracción oficial por cadena
    ev_eurosport = extraer_eventos_eurosport(fecha_colombia, canales_lineales)
    ev_rtve = extraer_eventos_teledeporte(fecha_colombia, canales_lineales)
    ev_espn = extraer_eventos_espn(fecha_colombia, canales_lineales)
    ev_win = extraer_eventos_claro_winsports(fecha_colombia, canales_lineales)
    ev_dsports = extraer_eventos_dsports(fecha_colombia, canales_lineales)

    # 2. Red de seguridad XMLTV si Eurosport/TDP están en 0
    ev_fallback: list[dict[str, Any]] = []
    if (len(ev_eurosport) + len(ev_rtve)) == 0:
        log.info("Activando red de seguridad XMLTV fallback...")
        ev_fallback = extraer_eventos_xmltv_fallback(fecha_colombia, canales_lineales)

    total_inyectados = ev_eurosport + ev_rtve + ev_espn + ev_win + ev_dsports + ev_fallback
    eventos_finales = fusionar_eventos_multicanal(eventos_base, total_inyectados)

    salida.update({
        "version": 11, "generado_utc": iso_utc(ahora), "zona_horaria_producto": str(tz),
        "fecha_local_producto": fecha_colombia, "eventos": eventos_finales
    })
    ARCHIVO_SALIDA.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = {
        "version": 11, "generado_utc": salida["generado_utc"], "fecha_local_producto": fecha_colombia,
        "canales_lineales_mapeados": {k: len(v) for k, v in canales_lineales.items()},
        "inyectados_eurosport": len(ev_eurosport), "inyectados_teledeporte": len(ev_rtve),
        "inyectados_espn": len(ev_espn), "inyectados_winsports": len(ev_win),
        "inyectados_dsports": len(ev_dsports), "inyectados_xmltv_fallback": len(ev_fallback),
        "total_cartelera_unificada": len(eventos_finales),
    }
    ARCHIVO_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "Cartelera unificada: E1/E2=%d TDP=%d ESPN=%d Win=%d DSports=%d Fallback=%d Total=%d",
        len(ev_eurosport), len(ev_rtve), len(ev_espn), len(ev_win), len(ev_dsports), len(ev_fallback), len(eventos_finales)
    )


if __name__ == "__main__":
    main()
