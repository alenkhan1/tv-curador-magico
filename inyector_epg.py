# -*- coding: utf-8 -*-
"""Inyector multicanal oficial (Eurosport, RTVE, ESPN, Win Sports, DSports) con aislamiento robusto de países."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
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
    "Accept": "application/json",
}

# Patrón estricto para aislar códigos de país (evita falso positivo en 'teleDEporte' o 'DEportes')
REGEX_PAIS_EXTRANJERO = re.compile(r"\b(DE|FR|UK|EN|PT|IT|GERMAN|FRENCH)\b", re.I)


def clasificar_canal_lineal(nombre: str) -> Optional[str]:
    """Clasifica el canal Xtream descartando DAZN y aislando códigos de país mediante límites de palabra."""
    n = normalizar_texto(nombre)
    if "DAZN" in n:
        return None

    # 1. Teledeporte España (Verifica que 'DE' sea país aislado y no parte de deporte/teledep)
    if "TELEDEPORTE" in n or re.search(r"\bTDP\b", n):
        partes_limpias = REGEX_PAIS_EXTRANJERO.sub("", n)
        if not any(x in partes_limpias for x in ["FRANCE", "UK", "ENGL"]):
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

    # 6. ESPN Familia (Latinoamérica)
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


def llamada_xtream(url: str, timeout: int = 60) -> Any:
    respuesta = requests.get(PUENTE_URL, params={"url": url}, timeout=timeout)
    respuesta.raise_for_status()
    return respuesta.json()


def mapear_canales_lineales_xtream() -> dict[str, list[dict[str, Any]]]:
    mapa: dict[str, list[dict[str, Any]]] = {}
    if not (XTREAM_URL and XTREAM_USER and XTREAM_PASS):
        return mapa
    url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
    try:
        streams = llamada_xtream(url, 75) or []
    except Exception as exc:
        log.warning("No se pudieron leer canales lineales de Xtream: %s", exc)
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
        resp_tok = requests.get("https://eu3-prod-direct.eurosport.es/token?realm=eurosport", headers=HEADERS_DEFAULT, timeout=8)
        if resp_tok.status_code != 200:
            return []
        token = resp_tok.json().get("data", {}).get("attributes", {}).get("token")
        if not token:
            return []

        headers = {**HEADERS_DEFAULT, "Authorization": f"Bearer {token}"}
        url_grid = f"https://eu3-prod-direct.eurosport.es/cms/routes/watch/schedule?date={fecha_colombia}"
        resp_grid = requests.get(url_grid, headers=headers, timeout=10)
        if resp_grid.status_code != 200:
            return []

        data = resp_grid.json()
        items = data.get("data", {}).get("attributes", {}).get("scheduleItems", [])

        for item in items:
            material_type = str(item.get("materialType", "")).upper()
            is_live = item.get("live") is True or material_type == "LIVE"
            if not is_live:
                continue

            titulo = str(item.get("title") or item.get("name") or "").strip()
            desc = str(item.get("description") or item.get("subtitle") or "").strip()
            if contiene_veto(f"{titulo} {desc}"):
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
        log.warning("Adaptador Eurosport no disponible: %s", exc)
    return eventos


# ─── Adaptador 2: RTVE Teledeporte ──────────────────────────────────────────
def extraer_eventos_teledeporte(fecha_colombia: str, canales_map: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    eventos: list[dict[str, Any]] = []
    fuentes = canales_map.get("TDP", [])

    try:
        url = f"https://www.rtve.es/api/parrilla/tve/teledeporte/{fecha_colombia}.json"
        resp = requests.get(url, headers=HEADERS_DEFAULT, timeout=8)
        if resp.status_code != 200:
            return []

        programas = resp.json().get("parrilla", {}).get("programas", [])
        for prog in programas:
            es_directo = bool(prog.get("directo") is True or str(prog.get("directo")).lower() in {"true", "1", "si", "directo"})
            if not es_directo:
                continue

            titulo = str(prog.get("title") or prog.get("name") or "").strip()
            desc = str(prog.get("desc") or prog.get("description") or "").strip()
            if contiene_veto(f"{titulo} {desc}"):
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
        log.warning("Adaptador RTVE Teledeporte no disponible: %s", exc)
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
        try:
            resp = requests.get(url, headers=HEADERS_DEFAULT, timeout=6)
            if resp.status_code != 200:
                continue

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


# ─── Adaptador 4: Win Sports Colombia (API Oficial CMS) ──────────────────────
def extraer_eventos_winsports(fecha_colombia: str, canales_map: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    eventos: list[dict[str, Any]] = []
    try:
        url = f"https://www.winsports.co/api/v1/programacion?fecha={fecha_colombia}"
        resp = requests.get(url, headers={**HEADERS_DEFAULT, "Referer": "https://www.winsports.co/"}, timeout=8)
        if resp.status_code != 200:
            return []

        bloques = resp.json() if isinstance(resp.json(), list) else resp.json().get("data", [])
        for prog in bloques:
            if not (prog.get("en_vivo") is True or str(prog.get("en_vivo")) in {"1", "true"}):
                continue

            titulo = str(prog.get("titulo") or prog.get("name") or "").strip()
            desc = str(prog.get("subtitulo") or prog.get("descripcion") or "").strip()
            if not titulo or contiene_veto(f"{titulo} {desc}"):
                continue

            h_ini = prog.get("hora_inicio") or prog.get("start")
            dt_inicio = datetime.fromisoformat(h_ini.replace("Z", "+00:00")) if (h_ini and "T" in h_ini) else None
            if not dt_inicio:
                continue

            es_premium = "plus" in normalizar_texto(prog.get("canal", "")) or "premium" in normalizar_texto(prog.get("canal", ""))
            fuentes = canales_map.get("WIN_PLUS" if es_premium else "WIN_BASICO", [])
            fuentes.extend(canales_map.get("WIN_BASICO", []))

            categoria = "Fútbol"
            logo_final = resolver_logo_torneo("Liga BetPlay", categoria)

            eventos.append({
                "id": f"winsports_{hashlib.sha1(f'{titulo}|{dt_inicio.isoformat()}'.encode()).hexdigest()[:14]}",
                "agenda_id": "", "titulo": titulo, "torneo": "Liga BetPlay Dimayor", "categoria": categoria,
                "tipo_evento": "duelo" if " vs " in titulo.lower() else "sencillo",
                "equipo_local": titulo.split(" vs ")[0].strip() if " vs " in titulo.lower() else "",
                "equipo_visitante": titulo.split(" vs ")[1].strip() if " vs " in titulo.lower() else "",
                "subtitulo": desc, "hora_utc": iso_utc(dt_inicio),
                "hora_local_producto": dt_inicio.astimezone(obtener_zona_aplicacion()).strftime("%H:%M"),
                "duracion_min": 130, "logo_torneo": logo_final, "logo_local": logo_final, "logo_visitante": "",
                "tier": 2, "origen": "winsports_oficial", "origenes": ["winsports_oficial"],
                "estado": "confirmado", "estado_evento": "programado", "confianza": "alta", "puntuacion_confianza": 95,
                "metodo_correlacion": "api_oficial_winsports", "razones_correlacion": ["directo:true"],
                "fuentes": fuentes,
            })
    except Exception as exc:
        log.warning("Adaptador Win Sports no disponible: %s", exc)
    return eventos


# ─── Adaptador 5: DSports / DIRECTV Sports (API DGO) ─────────────────────────
def extraer_eventos_dsports(fecha_colombia: str, canales_map: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    eventos: list[dict[str, Any]] = []
    try:
        url = f"https://api.directvgo.com/epg/v1/programs?country=CO&device=web&date={fecha_colombia}"
        headers = {**HEADERS_DEFAULT, "Origin": "https://www.directvgo.com", "Referer": "https://www.directvgo.com/"}
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code != 200:
            return []

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
                if not titulo or contiene_veto(f"{titulo} {desc}"):
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
        log.warning("Adaptador DSports no disponible: %s", exc)
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
    log.info("=== Inyector Multicanal Oficial v13 (Eurosport, RTVE, ESPN, Win, DSports) | Colombia %s ===", fecha_colombia)

    try:
        salida = json.loads(ARCHIVO_SALIDA.read_text(encoding="utf-8")) if ARCHIVO_SALIDA.exists() else {"version": 11, "eventos": []}
    except Exception:
        salida = {"version": 11, "eventos": []}

    eventos_base = list(salida.get("eventos") or [])
    canales_lineales = mapear_canales_lineales_xtream()

    # Ejecución de todos los adaptadores oficiales
    ev_eurosport = extraer_eventos_eurosport(fecha_colombia, canales_lineales)
    ev_rtve = extraer_eventos_teledeporte(fecha_colombia, canales_lineales)
    ev_espn = extraer_eventos_espn(fecha_colombia, canales_lineales)
    ev_win = extraer_eventos_winsports(fecha_colombia, canales_lineales)
    ev_dsports = extraer_eventos_dsports(fecha_colombia, canales_lineales)

    total_inyectados = ev_eurosport + ev_rtve + ev_espn + ev_win + ev_dsports
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
        "inyectados_dsports": len(ev_dsports), "total_cartelera_unificada": len(eventos_finales),
    }
    ARCHIVO_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Cartelera unificada con éxito: E1/E2=%d TDP=%d ESPN=%d Win=%d DSports=%d Total=%d", 
             len(ev_eurosport), len(ev_rtve), len(ev_espn), len(ev_win), len(ev_dsports), len(eventos_finales))


if __name__ == "__main__":
    main()
