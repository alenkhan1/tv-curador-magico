# -*- coding: utf-8 -*-
"""Inyector multicanal definitivo de TV lineal deportiva (Eurosport, Teledeporte, ESPN, Win Sports, DSports)."""
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

PUENTE_URL = (os.environ.get("PUENTE_URL") or "").strip() or "https://mi-dashboard-tv.onrender.com/api/puente_xtream"

HEADERS_DEFAULT = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

REGEX_PAIS_EXTRANJERO = re.compile(r"\b(DE|FR|UK|EN|PT|IT|GERMAN|FRENCH)\b", re.I)
PATRON_TEMPORADA_HISTORICA = re.compile(r"\b(19\d\d|20[0-1]\d|202[0-5])\b|\bT(19\d\d|20[0-1]\d|202[0-5]|2[0-5])\b", re.I)

# Veto estricto de programas no deportivos / no competitivos de las parrillas
VETO_PROGRAMAS_ESTUDIO = {
    "SPORTSCENTER", "SAQUE LARGO", "PRIMER TOQUE", "LINEA DE 4", "PLANETA FUTBOL",
    "ESTUDIO ESTADIO", "EL CHIRINGUITO", "NOTICIAS", "NEWS", "MAGAZINE", "INFORMATIVO",
    "PREVIA", "POST", "RESUMEN", "HIGHLIGHTS", "COMPACTO", "REPETICION", "REPLAY", "VINTAGE",
    "CLASICOS", "MEMORIAS", "LO MEJOR DE", "DOCUMENTAL",
}


def llamada_proxy(url: str, headers: Optional[dict[str, str]] = None, timeout: int = 15, usar_proxy: bool = True) -> Optional[requests.Response]:
    """Ejecuta peticiones canalizándolas por Render si está disponible, con fallback directo."""
    h = headers or HEADERS_DEFAULT
    if usar_proxy and PUENTE_URL:
        try:
            resp = requests.get(PUENTE_URL, params={"url": url}, headers=h, timeout=timeout)
            if resp.status_code == 200:
                return resp
        except Exception:
            pass

    try:
        resp = requests.get(url, headers=h, timeout=timeout)
        return resp if resp.status_code == 200 else None
    except Exception as exc:
        log.warning("Petición no completada para %s: %s", url, exc)
        return None


def clasificar_canal_lineal(nombre: str) -> Optional[str]:
    """Clasifica canales lineales descartando DAZN y aislando códigos de país mediante límites de palabra."""
    n = normalizar_texto(nombre)
    if "DAZN" in n:
        return None

    # 1. Teledeporte España
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


def es_bloque_en_vivo(texto: str) -> bool:
    """Valida estrictamente si el bloque de programación lineal corresponde a una transmisión en vivo."""
    n = normalizar_texto(texto)
    if any(p in n for p in VETO_PROGRAMAS_ESTUDIO) or contiene_veto(n) or PATRON_TEMPORADA_HISTORICA.search(n):
        return False
    return bool(re.search(r"\b(DIRECTO|VIVO|LIVE|EN DIRECTO|EN VIVO|DIREKT|EN DIRECT|AO VIVO|\[VIVO\]|\(VIVO\))\b", n))


def extraer_eventos_guia_universal(fecha_colombia: str, canales_map: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Extrae la programación lineal de Eurosport, TDP, ESPN, Win Sports y DSports desde el XMLTV de alta disponibilidad."""
    eventos: list[dict[str, Any]] = []
    url = "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml"
    resp = llamada_proxy(url, timeout=35, usar_proxy=False)
    if not resp:
        return []

    tz = obtener_zona_aplicacion()
    consenso_live: dict[tuple[str, str], bool] = {}
    programas_candidatos: list[dict[str, Any]] = []

    try:
        for _, el in ET.iterparse(io.BytesIO(resp.content), events=("end",)):
            if el.tag != "programme":
                continue
            canal = el.attrib.get("channel", "")
            clave = clasificar_canal_lineal(canal)
            start_raw = el.attrib.get("start", "")
            stop_raw = el.attrib.get("stop", "")

            if clave and start_raw:
                try:
                    dt_inicio = datetime.strptime(start_raw[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                    dt_fin = datetime.strptime(stop_raw[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc) if stop_raw else None
                except ValueError:
                    el.clear()
                    continue

                if dt_inicio.astimezone(tz).date().isoformat() == fecha_colombia:
                    tit = el.findtext("title", "") or ""
                    desc = el.findtext("desc", "") or ""
                    texto_completo = f"{tit} {desc}"
                    live_flag = es_bloque_en_vivo(texto_completo)
                    slot = dt_inicio.strftime("%Y%m%d%H%M")
                    if live_flag:
                        consenso_live[(clave, slot)] = True

                    programas_candidatos.append({
                        "clave": clave, "canal": canal, "inicio": dt_inicio, "fin": dt_fin,
                        "titulo": tit, "desc": desc, "es_live": live_flag
                    })
            el.clear()
    except Exception as exc:
        log.warning("Fallo en lectura de guía universal: %s", exc)
        return []

    for p in programas_candidatos:
        slot_key = (p["clave"], p["inicio"].strftime("%Y%m%d%H%M"))
        if not (p["es_live"] or consenso_live.get(slot_key, False)):
            continue

        tit, desc = p["titulo"], p["desc"]
        texto_completo = f"{tit} {desc}"
        categoria = inferir_deporte(texto_completo)
        if not categoria:
            continue

        # Limpieza de sufijos y etiquetas de directo
        tit_limpio = re.sub(r"(?i)\b(DIRECTO|VIVO|LIVE|EN DIRECTO|EN VIVO|DIREKT|EN DIRECT|\[VIVO\]|\(VIVO\))\b", "", tit)
        tit_limpio = " ".join(tit_limpio.strip(" -:·|▫/").split())

        duelo_match = re.search(r"(.+?)\s+(?:vs\.?|v\.?|versus)\s+(.+)", tit_limpio, re.I)
        tipo = "duelo" if duelo_match and categoria not in DEPORTES_INDIVIDUALES else "sencillo"
        local = duelo_match.group(1).strip() if tipo == "duelo" else ""
        visitante = duelo_match.group(2).strip() if tipo == "duelo" else ""

        torneo = tit_limpio
        if "VUELTA" in normalizar_texto(tit_limpio):
            torneo = "La Vuelta"
        elif "LIGA BETPLAY" in normalizar_texto(texto_completo):
            torneo = "Liga BetPlay Dimayor"

        logo_final = resolver_logo_torneo(torneo, categoria)
        fuentes = canales_map.get(p["clave"], [])

        iso_ini = p["inicio"].isoformat()
        cadena_ident = f"{tit_limpio}|{iso_ini}"
        ev_id = hashlib.sha1(cadena_ident.encode("utf-8")).hexdigest()[:14]

        eventos.append({
            "id": f"guia_{ev_id}",
            "agenda_id": "", "titulo": tit_limpio, "torneo": torneo, "categoria": categoria,
            "tipo_evento": tipo, "equipo_local": local, "equipo_visitante": visitante,
            "subtitulo": desc, "hora_utc": iso_utc(p["inicio"]),
            "hora_local_producto": p["inicio"].astimezone(tz).strftime("%H:%M"),
            "duracion_min": max(20, int((p["fin"] - p["inicio"]).total_seconds() / 60)) if p["fin"] else DURACION_POR_CATEGORIA.get(categoria, 120),
            "logo_torneo": logo_final, "logo_local": logo_final if tipo == "sencillo" else "", "logo_visitante": "",
            "tier": 2, "origen": f"lineal_{p['clave'].lower()}", "origenes": [f"lineal_{p['clave'].lower()}"],
            "estado": "confirmado", "estado_evento": "programado", "confianza": "alta", "puntuacion_confianza": 92,
            "metodo_correlacion": "guia_lineal_verificada", "razones_correlacion": ["directo:true", f"canal:{p['clave']}"],
            "fuentes": fuentes,
        })
    return eventos


# ─── Adaptador Directo Web: ESPN Live TV Schedule ───────────────────────────
def extraer_eventos_espn_web(fecha_colombia: str, canales_map: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
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
    log.info("=== Inyector Multicanal Oficial v15 | Colombia %s ===", fecha_colombia)

    try:
        salida = json.loads(ARCHIVO_SALIDA.read_text(encoding="utf-8")) if ARCHIVO_SALIDA.exists() else {"version": 11, "eventos": []}
    except Exception:
        salida = {"version": 11, "eventos": []}

    eventos_base = list(salida.get("eventos") or [])
    canales_lineales = mapear_canales_lineales_xtream()

    # 1. Extracción multicanal completa (Eurosport 1/2, TDP, ESPN, Win Sports, DSports)
    ev_guia = extraer_eventos_guia_universal(fecha_colombia, canales_lineales)

    # 2. Extracción complementaria ESPN
    ev_espn = extraer_eventos_espn_web(fecha_colombia, canales_lineales)

    total_inyectados = ev_guia + ev_espn
    eventos_finales = fusionar_eventos_multicanal(eventos_base, total_inyectados)

    salida.update({
        "version": 11, "generado_utc": iso_utc(ahora), "zona_horaria_producto": str(tz),
        "fecha_local_producto": fecha_colombia, "eventos": eventos_finales
    })
    ARCHIVO_SALIDA.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = {
        "version": 11, "generado_utc": salida["generado_utc"], "fecha_local_producto": fecha_colombia,
        "canales_lineales_mapeados": {k: len(v) for k, v in canales_lineales.items()},
        "inyectados_guia_lineal": len(ev_guia), "inyectados_espn": len(ev_espn),
        "total_cartelera_unificada": len(eventos_finales),
    }
    ARCHIVO_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "Cartelera unificada: Lineal=%d ESPN=%d Total=%d",
        len(ev_guia), len(ev_espn), len(eventos_finales)
    )


if __name__ == "__main__":
    main()
