# -*- coding: utf-8 -*-
"""Inyector multicanal 100% basado en APIs oficiales (Discovery, RTVE, Claro, DGO, ESPN) vía Render."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

from curador_eventos import (
    ARCHIVO_META,
    ARCHIVO_SALIDA,
    DEPORTES_INDIVIDUALES,
    DURACION_POR_CATEGORIA,
    XTREAM_PASS,
    XTREAM_URL,
    XTREAM_USER,
    contiene_veto,
    fusionar_fuente,
    generar_huella_canonica,
    inferir_deporte,
    iso_utc,
    normalizar_texto,
    obtener_zona_aplicacion,
)
from resolvedor_logos import envolver_cdn_proxy, resolver_logo_torneo

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("inyector_canales_vivo")

# Proxy universal en Render garantizado (evita variables vacías de GitHub Actions)
PUENTE_RENDER_DEFAULT = "https://mi-dashboard-tv.onrender.com/api/puente_xtream"
ENV_PUENTE = (os.environ.get("PUENTE_URL") or "").strip()
PUENTE_URL = ENV_PUENTE if ENV_PUENTE else PUENTE_RENDER_DEFAULT

HEADERS_CHROME = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

REGEX_PAIS_EXTRANJERO = re.compile(r"\b(DE|FR|UK|EN|PT|IT|GERMAN|FRENCH)\b", re.I)


def consultar_api_render(url: str, headers_extra: Optional[dict[str, str]] = None, timeout: int = 15) -> Optional[dict[str, Any]]:
    """Enruta la llamada a la API a través del proxy en Render para saltar bloqueos geográficos y de WAF."""
    h = {**HEADERS_CHROME, **(headers_extra or {})}
    url_proxy = f"{PUENTE_URL}?url={urllib.parse.quote(url, safe='')}"

    # 1. Intento por proxy Render
    try:
        resp = requests.get(url_proxy, headers=h, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        log.debug("Proxy Render falló para %s: %s. Reintentando directo...", url, exc)

    # 2. Respaldo directo
    try:
        resp = requests.get(url, headers=h, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        log.warning("Fallo total al consultar %s: %s", url, exc)

    return None


def clasificar_canal_lineal(nombre: str) -> Optional[str]:
    """Clasifica canales lineales de Xtream descartando DAZN y aislando códigos de país."""
    n = normalizar_texto(nombre)
    if "DAZN" in n:
        return None

    if "TELEDEPORTE" in n or re.search(r"\bTDP\b", n):
        n_sin = n.replace("TELEDEPORTE", "").replace("TDP", "")
        if not REGEX_PAIS_EXTRANJERO.search(n_sin):
            return "TDP"

    if re.search(r"\bEUROSPORTS?\s*1\b", n) and not re.search(r"\bEUROSPORTS?\s*2\b", n):
        if not REGEX_PAIS_EXTRANJERO.search(n):
            return "E1"

    if re.search(r"\bEUROSPORTS?\s*2\b", n):
        if not REGEX_PAIS_EXTRANJERO.search(n):
            return "E2"

    if "WIN SPORTS" in n or "WIN+" in n or "WIN PLUS" in n:
        return "WIN_PLUS" if ("+" in n or "PLUS" in n or "PREMIUM" in n) else "WIN_BASICO"

    if "DSPORTS" in n or "DIRECTV SPORTS" in n or "D SPORTS" in n:
        if "2" in n:
            return "DSPORTS_2"
        if "+" in n or "PLUS" in n:
            return "DSPORTS_PLUS"
        return "DSPORTS_1"

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
    datos = consultar_api_render(url, timeout=75)
    if not isinstance(datos, list):
        return mapa

    for stream in datos:
        nombre = str(stream.get("name") or "").strip()
        sid = str(stream.get("stream_id") or "")
        clave = clasificar_canal_lineal(nombre)
        if clave and sid:
            mapa.setdefault(clave, []).append({"nombre": nombre, "id_xtream": sid})
    return mapa


def limpiar_titulo_evento(titulo_raw: str, desc_raw: str = "") -> tuple[str, str, str]:
    """Limpia marcas técnicas y extrae (titulo_limpio, torneo, subtitulo)."""
    t = re.sub(r"(?i)\b(DIRECTO|VIVO|LIVE|EN DIRECTO|EN VIVO|DIREKT|\[VIVO\]|\(VIVO\)|T\d{2,4}(/\d{2,4})?)\b", "", titulo_raw)
    t = " ".join(t.strip(" -:·|▫/").split())

    d = re.sub(r"(?i)\b(DIRECTO|VIVO|LIVE|EN DIRECTO|EN VIVO)\b", "", desc_raw).strip()
    partes = [p.strip() for p in re.split(r"\s*[·|▫:/]\s*", t, maxsplit=1) if p.strip()]
    torneo = partes[0] if partes else t
    subtitulo = partes[1] if len(partes) > 1 else d

    if "VUELTA" in normalizar_texto(torneo):
        torneo = "La Vuelta"

    return t, torneo, subtitulo


# ─── 1. Adaptador Eurosport España (Discovery API) ───────────────────────────
def extraer_eventos_eurosport(fecha_colombia: str, canales_map: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    eventos: list[dict[str, Any]] = []
    token_json = consultar_api_render("https://eu3-prod-direct.eurosport.es/token?realm=eurosport", timeout=8)
    token = (token_json or {}).get("data", {}).get("attributes", {}).get("token")
    if not token:
        return []

    url_grid = f"https://eu3-prod-direct.eurosport.es/cms/routes/watch/schedule?date={fecha_colombia}"
    grid_json = consultar_api_render(url_grid, headers_extra={"Authorization": f"Bearer {token}"}, timeout=10)
    if not grid_json:
        return []

    items = grid_json.get("data", {}).get("attributes", {}).get("scheduleItems", [])
    for item in items:
        # Filtro estructural nativo: solo competiciones en vivo (descarta magazines / La Montonera)
        material_type = str(item.get("materialType", "")).upper()
        content_type = str(item.get("contentType", "")).upper()
        if material_type in {"MAGAZINE", "STUDIO_SHOW", "REPLAY", "RECORDED"} or content_type == "STUDIO_SHOW":
            continue
        if not (item.get("live") is True or material_type in {"LIVE", "COMPETITION"}):
            continue

        tit_raw = str(item.get("title") or item.get("name") or "").strip()
        desc_raw = str(item.get("description") or item.get("subtitle") or "").strip()
        if contiene_veto(f"{tit_raw} {desc_raw}"):
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

        titulo_limpio, torneo, subtitulo = limpiar_titulo_evento(tit_raw, desc_raw)
        categoria = inferir_deporte(f"{titulo_limpio} {subtitulo}") or "Deportes"
        duracion = max(15, int((dt_fin - dt_inicio).total_seconds() / 60)) if dt_fin else 120
        logo_img = item.get("images", {}).get("logo") or item.get("images", {}).get("poster")
        logo_final = envolver_cdn_proxy(logo_img) if logo_img else resolver_logo_torneo(torneo, categoria)

        cadena_ident = f"{torneo}|{subtitulo}|{inicio_utc}"
        ev_id = hashlib.sha1(cadena_ident.encode("utf-8")).hexdigest()[:14]

        eventos.append({
            "id": f"eurosport_{ev_id}", "agenda_id": "", "titulo": titulo_limpio, "torneo": torneo,
            "categoria": categoria, "tipo_evento": "sencillo", "equipo_local": "", "equipo_visitante": "",
            "subtitulo": subtitulo, "hora_utc": iso_utc(dt_inicio),
            "hora_local_producto": dt_inicio.astimezone(obtener_zona_aplicacion()).strftime("%H:%M"),
            "duracion_min": duracion, "logo_torneo": logo_final, "logo_local": logo_final, "logo_visitante": "",
            "tier": 2, "origen": "eurosport_oficial", "origenes": ["eurosport_oficial"],
            "estado": "confirmado", "estado_evento": "programado", "confianza": "alta", "puntuacion_confianza": 96,
            "metodo_correlacion": "api_oficial_eurosport", "razones_correlacion": ["directo:true", f"canal:{clave_canal}"],
            "fuentes": fuentes,
        })
    return eventos


# ─── 2. Adaptador RTVE Teledeporte ──────────────────────────────────────────
def extraer_eventos_teledeporte(fecha_colombia: str, canales_map: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    eventos: list[dict[str, Any]] = []
    fuentes = canales_map.get("TDP", [])
    url = f"https://www.rtve.es/api/parrilla/tve/teledeporte/{fecha_colombia}.json"
    data = consultar_api_render(url, headers_extra={"Referer": "https://www.rtve.es/"}, timeout=8)
    if not data:
        return []

    programas = data.get("parrilla", {}).get("programas", [])
    for prog in programas:
        tipo_prog = str(prog.get("tipo_programa") or "").lower()
        if "magazine" in tipo_prog or "informativo" in tipo_prog:
            continue

        es_directo = bool(prog.get("directo") is True or str(prog.get("directo")).lower() in {"true", "1", "si", "directo"})
        if not es_directo:
            continue

        tit_raw = str(prog.get("title") or prog.get("name") or "").strip()
        desc_raw = str(prog.get("desc") or prog.get("description") or "").strip()
        if contiene_veto(f"{tit_raw} {desc_raw}"):
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

        titulo_limpio, torneo, subtitulo = limpiar_titulo_evento(tit_raw, desc_raw)
        categoria = inferir_deporte(f"{titulo_limpio} {subtitulo}") or "Deportes"
        duracion = max(15, int((dt_fin - dt_inicio).total_seconds() / 60)) if (dt_fin and dt_inicio) else 90
        logo_final = resolver_logo_torneo(torneo, categoria)

        iso_ini = dt_inicio.isoformat()
        cadena_ident = f"{torneo}|{subtitulo}|{iso_ini}"
        ev_id = hashlib.sha1(cadena_ident.encode("utf-8")).hexdigest()[:14]

        eventos.append({
            "id": f"rtve_tdp_{ev_id}", "agenda_id": "", "titulo": titulo_limpio, "torneo": torneo,
            "categoria": categoria, "tipo_evento": "sencillo", "equipo_local": "", "equipo_visitante": "",
            "subtitulo": subtitulo, "hora_utc": iso_utc(dt_inicio),
            "hora_local_producto": dt_inicio.astimezone(obtener_zona_aplicacion()).strftime("%H:%M"),
            "duracion_min": duracion, "logo_torneo": logo_final, "logo_local": logo_final, "logo_visitante": "",
            "tier": 2, "origen": "rtve_oficial", "origenes": ["rtve_oficial"],
            "estado": "confirmado", "estado_evento": "programado", "confianza": "alta", "puntuacion_confianza": 96,
            "metodo_correlacion": "api_oficial_rtve", "razones_correlacion": ["directo:true", "canal:TDP"],
            "fuentes": fuentes,
        })
    return eventos


# ─── 3. Adaptador Win Sports & Win+ (Claro Video Colombia API) ──────────────
def extraer_eventos_claro_winsports(fecha_colombia: str, canales_map: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    eventos: list[dict[str, Any]] = []
    fecha_compacta = fecha_colombia.replace("-", "")
    url = (
        f"https://mfwkweb-api.clarovideo.net/services/epg/channel?"
        f"device_category=web&device_model=web&device_type=web&device_so=Chrome&format=json&"
        f"date_from={fecha_compacta}000000&date_to={fecha_compacta}235959&quantity_channels=50"
    )

    data = consultar_api_render(url, timeout=12)
    if not data:
        return []

    canales = data.get("response", {}).get("channels", [])
    for canal in canales:
        c_name = normalizar_texto(canal.get("name", ""))
        if "WIN" not in c_name:
            continue

        es_plus = "+" in c_name or "PLUS" in c_name or "PREMIUM" in c_name
        fuentes = canales_map.get("WIN_PLUS" if es_plus else "WIN_BASICO", [])

        for prog in canal.get("events", []):
            if prog.get("program_type") == "EPISODIC" or "talk" in str(prog.get("genre", "")).lower():
                continue

            tit_raw = str(prog.get("name") or "").strip()
            desc_raw = str(prog.get("description") or "").strip()
            texto_completo = f"{tit_raw} {desc_raw}"

            if contiene_veto(texto_completo):
                continue

            es_vivo = bool(re.search(r"\b(VIVO|EN VIVO|DIRECTO|LIVE|\[VIVO\]|\(VIVO\))\b", normalizar_texto(texto_completo)))
            if not es_vivo:
                continue

            h_ini = prog.get("date_begin")
            h_fin = prog.get("date_end")
            dt_inicio = datetime.fromisoformat(h_ini.replace("Z", "+00:00")) if (h_ini and "T" in h_ini) else None
            dt_fin = datetime.fromisoformat(h_fin.replace("Z", "+00:00")) if (h_fin and "T" in h_fin) else None
            if not dt_inicio:
                continue

            titulo_limpio, torneo, subtitulo = limpiar_titulo_evento(tit_raw, desc_raw)
            categoria = inferir_deporte(texto_completo) or "Fútbol"
            duracion = max(20, int((dt_fin - dt_inicio).total_seconds() / 60)) if (dt_fin and dt_inicio) else 120
            logo_final = resolver_logo_torneo(torneo, categoria)

            duelo_match = re.search(r"(.+?)\s+(?:vs\.?|v\.?|versus)\s+(.+)", titulo_limpio, re.I)
            tipo = "duelo" if duelo_match and categoria not in DEPORTES_INDIVIDUALES else "sencillo"
            local = duelo_match.group(1).strip() if tipo == "duelo" else ""
            visitante = duelo_match.group(2).strip() if tipo == "duelo" else ""

            iso_ini = dt_inicio.isoformat()
            cadena_ident = f"{torneo}|{subtitulo}|{iso_ini}"
            ev_id = hashlib.sha1(cadena_ident.encode("utf-8")).hexdigest()[:14]

            eventos.append({
                "id": f"winsports_{ev_id}", "agenda_id": "", "titulo": titulo_limpio, "torneo": torneo,
                "categoria": categoria, "tipo_evento": tipo, "equipo_local": local, "equipo_visitante": visitante,
                "subtitulo": subtitulo, "hora_utc": iso_utc(dt_inicio),
                "hora_local_producto": dt_inicio.astimezone(obtener_zona_aplicacion()).strftime("%H:%M"),
                "duracion_min": duracion, "logo_torneo": logo_final, "logo_local": logo_final, "logo_visitante": "",
                "tier": 2, "origen": "winsports_claro_oficial", "origenes": ["winsports_claro_oficial"],
                "estado": "confirmado", "estado_evento": "programado", "confianza": "alta", "puntuacion_confianza": 96,
                "metodo_correlacion": "api_claro_winsports", "razones_correlacion": ["directo:true"],
                "fuentes": fuentes,
            })
    return eventos


# ─── 4. Adaptador DSports / DIRECTV Sports (DGO API) ─────────────────────────
def extraer_eventos_dsports(fecha_colombia: str, canales_map: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    eventos: list[dict[str, Any]] = []
    url = f"https://api.directvgo.com/epg/v1/programs?country=CO&device=web&date={fecha_colombia}"
    h = {"Origin": "https://www.directvgo.com", "Referer": "https://www.directvgo.com/"}
    data = consultar_api_render(url, headers_extra=h, timeout=10)
    if not data:
        return []

    canales = data.get("channels", [])
    for canal in canales:
        c_name = normalizar_texto(canal.get("channelName", ""))
        if "DSPORTS" not in c_name and "DIRECTV SPORTS" not in c_name:
            continue

        clave_canal = "DSPORTS_2" if " 2" in c_name else ("DSPORTS_PLUS" if "+" in c_name or "PLUS" in c_name else "DSPORTS_1")
        fuentes = canales_map.get(clave_canal, [])

        for prog in canal.get("programs", []):
            if prog.get("programType") in {"SportsNews", "SportsTalk"}:
                continue
            if not prog.get("isLive") is True:
                continue

            tit_raw = str(prog.get("title") or "").strip()
            desc_raw = str(prog.get("description") or "").strip()
            if contiene_veto(f"{tit_raw} {desc_raw}"):
                continue

            h_ini = prog.get("startDate")
            dt_inicio = datetime.fromisoformat(h_ini.replace("Z", "+00:00")) if h_ini else None
            if not dt_inicio:
                continue

            titulo_limpio, torneo, subtitulo = limpiar_titulo_evento(tit_raw, desc_raw)
            categoria = inferir_deporte(f"{titulo_limpio} {subtitulo}") or "Deportes"
            logo_prog = prog.get("images", {}).get("poster")
            logo_final = envolver_cdn_proxy(logo_prog) if logo_prog else resolver_logo_torneo(torneo, categoria)

            duelo_match = re.search(r"(.+?)\s+(?:vs\.?|v\.?|versus)\s+(.+)", titulo_limpio, re.I)
            tipo = "duelo" if duelo_match and categoria not in DEPORTES_INDIVIDUALES else "sencillo"
            local = duelo_match.group(1).strip() if tipo == "duelo" else ""
            visitante = duelo_match.group(2).strip() if tipo == "duelo" else ""

            iso_ini = dt_inicio.isoformat()
            cadena_ident = f"{torneo}|{subtitulo}|{iso_ini}"
            ev_id = hashlib.sha1(cadena_ident.encode("utf-8")).hexdigest()[:14]

            eventos.append({
                "id": f"dsports_{ev_id}", "agenda_id": "", "titulo": titulo_limpio, "torneo": torneo,
                "categoria": categoria, "tipo_evento": tipo, "equipo_local": local, "equipo_visitante": visitante,
                "subtitulo": subtitulo, "hora_utc": iso_utc(dt_inicio),
                "hora_local_producto": dt_inicio.astimezone(obtener_zona_aplicacion()).strftime("%H:%M"),
                "duracion_min": 130, "logo_torneo": logo_final, "logo_local": logo_final, "logo_visitante": "",
                "tier": 2, "origen": "dsports_oficial", "origenes": ["dsports_oficial"],
                "estado": "confirmado", "estado_evento": "programado", "confianza": "alta", "puntuacion_confianza": 96,
                "metodo_correlacion": "api_oficial_dsports", "razones_correlacion": ["isLive:true"],
                "fuentes": fuentes,
            })
    return eventos


# ─── 5. Adaptador ESPN Latinoamérica ────────────────────────────────────────
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
        datos = consultar_api_render(url, timeout=6)
        if not datos:
            continue
        try:
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
    log.info("=== Inyector Oficial 100%% APIs | Colombia %s ===", fecha_colombia)

    try:
        salida = json.loads(ARCHIVO_SALIDA.read_text(encoding="utf-8")) if ARCHIVO_SALIDA.exists() else {"version": 11, "eventos": []}
    except Exception:
        salida = {"version": 11, "eventos": []}

    eventos_base = list(salida.get("eventos") or [])
    canales_lineales = mapear_canales_lineales_xtream()

    # Extracción estricta de las 5 APIs oficiales
    ev_eurosport = extraer_eventos_eurosport(fecha_colombia, canales_lineales)
    ev_rtve = extraer_eventos_teledeporte(fecha_colombia, canales_lineales)
    ev_win = extraer_eventos_claro_winsports(fecha_colombia, canales_lineales)
    ev_dsports = extraer_eventos_dsports(fecha_colombia, canales_lineales)
    ev_espn = extraer_eventos_espn(fecha_colombia, canales_lineales)

    total_nuevos = ev_eurosport + ev_rtve + ev_win + ev_dsports + ev_espn
    eventos_finales = fusionar_eventos_multicanal(eventos_base, total_nuevos)

    salida.update({
        "version": 11, "generado_utc": iso_utc(ahora), "zona_horaria_producto": str(tz),
        "fecha_local_producto": fecha_colombia, "eventos": eventos_finales
    })
    ARCHIVO_SALIDA.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = {
        "version": 11, "generado_utc": salida["generado_utc"], "fecha_local_producto": fecha_colombia,
        "canales_lineales_mapeados": {k: len(v) for k, v in canales_lineales.items()},
        "inyectados_eurosport": len(ev_eurosport), "inyectados_teledeporte": len(ev_rtve),
        "inyectados_winsports": len(ev_win), "inyectados_dsports": len(ev_dsports),
        "inyectados_espn": len(ev_espn), "total_cartelera_unificada": len(eventos_finales),
    }
    ARCHIVO_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "Cartelera APIs oficial: E1/E2=%d TDP=%d Win=%d DSports=%d ESPN=%d Total=%d",
        len(ev_eurosport), len(ev_rtve), len(ev_win), len(ev_dsports), len(ev_espn), len(eventos_finales)
    )


if __name__ == "__main__":
    main()
