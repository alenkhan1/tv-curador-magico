# -*- coding: utf-8 -*-
"""Resolvedor universal de logos deportivos con CDN Proxy (anti-403) y caché local."""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("resolvedor_logos")

ARCHIVO_CACHE_LOGOS = Path(os.environ.get("ARCHIVO_CACHE_LOGOS", "logos_cache.json"))


def es_logo_basura(url: Any) -> bool:
    """Detecta si una URL es un placeholder genérico, basura de panel IPTV o enlace corrupto."""
    if not url or not isinstance(url, str):
        return True
    u = url.lower()
    patrones_basura = [
        ":25461", ":80/images", "premieresupport", "1play.cool",
        "placeholder", "generic", "logo_generico", "stadium.png",
        "icons8.com", "ppv", "default_channel", "no_logo", "blank.png"
    ]
    return any(p in u for p in patrones_basura)


def envolver_cdn_proxy(url: str) -> str:
    """Envuelve URLs externas en wsrv.nl para evitar bloqueos HTTP 403 por User-Agent en Android TV."""
    if not url or es_logo_basura(url):
        return ""
    if url.startswith("https://wsrv.nl/?url="):
        return url
    url_limpia = url.strip()
    return f"https://wsrv.nl/?url={urllib.parse.quote(url_limpia, safe='')}&w=400&output=webp"


# Catálogo maestro de insignias de federaciones, ligas y circuitos oficiales
CIRCUITO_LOGOS_RAW: dict[str, str] = {
    # Tenis
    "ATP": "https://upload.wikimedia.org/wikipedia/commons/3/3f/ATP_Tour_logo.svg",
    "WTA": "https://upload.wikimedia.org/wikipedia/en/0/03/WTA_logo_2020.svg",
    "ITF": "https://upload.wikimedia.org/wikipedia/en/9/91/International_Tennis_Federation_logo.svg",
    "DAVIS CUP": "https://upload.wikimedia.org/wikipedia/en/thumb/e/e0/Davis_Cup_Logo.svg/512px-Davis_Cup_Logo.svg.png",
    "US OPEN": "https://upload.wikimedia.org/wikipedia/commons/3/3f/ATP_Tour_logo.svg",
    "ROLAND GARROS": "https://upload.wikimedia.org/wikipedia/en/2/22/Roland_Garros_logo.svg",
    "WIMBLEDON": "https://upload.wikimedia.org/wikipedia/en/b/b9/Wimbledon.svg",
    "AUSTRALIAN OPEN": "https://upload.wikimedia.org/wikipedia/en/7/7b/Australian_Open_logo.svg",
    "CINCINNATI OPEN": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/14/Cincinnati_Open_logo.svg/512px-Cincinnati_Open_logo.svg.png",
    "WINSTON-SALEM": "https://upload.wikimedia.org/wikipedia/en/thumb/8/87/Winston-Salem_Open_logo.svg/512px-Winston-Salem_Open_logo.svg.png",
    "ABIERTO GNP": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d7/Abierto_GNP_Seguros_Logo.png/512px-Abierto_GNP_Seguros_Logo.png",
    "GUADALAJARA OPEN": "https://upload.wikimedia.org/wikipedia/en/0/03/WTA_logo_2020.svg",
    "SP OPEN": "https://upload.wikimedia.org/wikipedia/en/0/03/WTA_logo_2020.svg",
    "LJUBLJANA": "https://upload.wikimedia.org/wikipedia/en/0/03/WTA_logo_2020.svg",

    # Fútbol Internacional y Torneos Mayores
    "UEFA": "https://upload.wikimedia.org/wikipedia/commons/b/b5/UEFA_logo.svg",
    "EUROPA LEAGUE": "https://logodownload.org/wp-content/uploads/2019/12/europa-league-logo.png",
    "UEL": "https://logodownload.org/wp-content/uploads/2019/12/europa-league-logo.png",
    "CHAMPIONS LEAGUE": "https://upload.wikimedia.org/wikipedia/commons/f/f3/UEFA_Champions_League_logo_2.svg",
    "UCL": "https://upload.wikimedia.org/wikipedia/commons/f/f3/UEFA_Champions_League_logo_2.svg",
    "CONFERENCE LEAGUE": "https://upload.wikimedia.org/wikipedia/commons/b/b5/UEFA_logo.svg",
    "UECL": "https://upload.wikimedia.org/wikipedia/commons/b/b5/UEFA_logo.svg",
    "CONMEBOL": "https://upload.wikimedia.org/wikipedia/commons/0/0e/CONMEBOL_logo.svg",
    "LIBERTADORES": "https://upload.wikimedia.org/wikipedia/commons/c/c5/Copa_Libertadores_logo_2017.svg",
    "SUDAMERICANA": "https://upload.wikimedia.org/wikipedia/commons/e/eb/Copa_Sudamericana_logo.svg",
    "LALIGA": "https://upload.wikimedia.org/wikipedia/commons/0/0f/LaLiga_logo_2023.svg",
    "PREMIER LEAGUE": "https://upload.wikimedia.org/wikipedia/en/f/f2/Premier_League_Logo.svg",
    "SERIE A": "https://upload.wikimedia.org/wikipedia/commons/e/e9/Serie_A_logo_2019.svg",
    "BUNDESLIGA": "https://upload.wikimedia.org/wikipedia/en/d/df/Bundesliga_logo_%282017%29.svg",
    "LIGUE 1": "https://upload.wikimedia.org/wikipedia/commons/5/5e/Ligue1_McDonald%27s_logo.svg",
    "LIGA BETPLAY": "https://upload.wikimedia.org/wikipedia/commons/e/ed/Liga_BetPlay_Dimayor_logo.png",
    "BETPLAY": "https://upload.wikimedia.org/wikipedia/commons/e/ed/Liga_BetPlay_Dimayor_logo.png",
    "FIFA": "https://upload.wikimedia.org/wikipedia/commons/1/10/FIFA_logo_without_slogan.svg",
    "MUNDIAL FEM": "https://upload.wikimedia.org/wikipedia/commons/1/10/FIFA_logo_without_slogan.svg",
    "SUB20": "https://upload.wikimedia.org/wikipedia/commons/1/10/FIFA_logo_without_slogan.svg",
    "SUB 20": "https://upload.wikimedia.org/wikipedia/commons/1/10/FIFA_logo_without_slogan.svg",

    # Padel y Escalada
    "PREMIER PADEL": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Premier_Padel_logo.svg",
    "PADEL": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Premier_Padel_logo.svg",
    "FIP": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Premier_Padel_logo.svg",
    "ESCALADA": "https://upload.wikimedia.org/wikipedia/commons/d/d7/International_Federation_of_Sport_Climbing_logo.svg",
    "IFSC": "https://upload.wikimedia.org/wikipedia/commons/d/d7/International_Federation_of_Sport_Climbing_logo.svg",

    # Ciclismo
    "UCI": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/29/Union_Cycliste_Internationale_logo.svg/512px-Union_Cycliste_Internationale_logo.svg.png",
    "VUELTA": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/La_Vuelta_logo.svg/512px-La_Vuelta_logo.svg.png",
    "LA VUELTA": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/La_Vuelta_logo.svg/512px-La_Vuelta_logo.svg.png",
    "TOUR DE FRANCE": "https://upload.wikimedia.org/wikipedia/en/thumb/9/91/Tour_de_France_logo.svg/512px-Tour_de_France_logo.svg.png",
    "GIRO": "https://upload.wikimedia.org/wikipedia/en/thumb/8/82/Giro_d%27Italia_logo.svg/512px-Giro_d%27Italia_logo.svg.png",
    "RENEWI TOUR": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/93/Renewi_Tour_logo.svg/512px-Renewi_Tour_logo.svg.png",

    # Snooker
    "WST": "https://upload.wikimedia.org/wikipedia/en/thumb/6/64/World_Snooker_Tour_logo.svg/512px-World_Snooker_Tour_logo.svg.png",
    "SNOOKER": "https://upload.wikimedia.org/wikipedia/en/thumb/6/64/World_Snooker_Tour_logo.svg/512px-World_Snooker_Tour_logo.svg.png",
    "WUHAN OPEN": "https://upload.wikimedia.org/wikipedia/en/thumb/6/64/World_Snooker_Tour_logo.svg/512px-World_Snooker_Tour_logo.svg.png",

    # Motor
    "F1": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/33/F1.svg/512px-F1.svg.png",
    "FORMULA 1": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/33/F1.svg/512px-F1.svg.png",
    "MOTOGP": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a0/Moto_Gp_logo.svg/512px-Moto_Gp_logo.svg.png",
    "INDYCAR": "https://upload.wikimedia.org/wikipedia/en/thumb/3/3c/IndyCar_Series_logo.svg/512px-IndyCar_Series_logo.svg.png",
    "FORMULA E": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/41/Formula_E_logo.svg/512px-Formula_E_logo.svg.png",
    "SUPERBIKE": "https://upload.wikimedia.org/wikipedia/en/thumb/9/9c/Superbike_World_Championship_logo.svg/512px-Superbike_World_Championship_logo.svg.png",
    "NASCAR": "https://upload.wikimedia.org/wikipedia/commons/a/a2/NASCAR_logo.svg",
    "ARCA": "https://upload.wikimedia.org/wikipedia/commons/2/27/ARCA_Menards_Series_logo.svg",

    # Combate
    "UFC": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/UFC_logo.svg/512px-UFC_logo.svg.png",
    "BKFC": "https://upload.wikimedia.org/wikipedia/en/thumb/6/60/Bare_Knuckle_Fighting_Championship_logo.png/512px-Bare_Knuckle_Fighting_Championship_logo.png",

    # Golf
    "PGA": "https://upload.wikimedia.org/wikipedia/en/thumb/c/cf/PGA_Tour_logo.svg/512px-PGA_Tour_logo.svg.png",
    "DP WORLD": "https://upload.wikimedia.org/wikipedia/en/thumb/6/65/DP_World_Tour_logo.svg/512px-DP_World_Tour_logo.svg.png",
    "BMW CHAMPIONSHIP": "https://upload.wikimedia.org/wikipedia/en/thumb/f/f6/BMW_Championship_logo.svg/512px-BMW_Championship_logo.svg.png",

    # Béisbol y otros
    "MLB": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Major_League_Baseball_logo.svg/512px-Major_League_Baseball_logo.svg.png",
    "LMB": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8a/Liga_Mexicana_de_Beisbol_logo.svg/512px-Liga_Mexicana_de_Beisbol_logo.svg.png",
    "LITTLE LEAGUE": "https://upload.wikimedia.org/wikipedia/en/thumb/9/90/Little_League_logo.svg/512px-Little_League_logo.svg.png",
    "NCAA": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/NCAA_logo.svg/512px-NCAA_logo.svg.png",
    "GIMNASIA": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/European_Gymnastics_logo.svg/512px-European_Gymnastics_logo.svg.png",
    "HOCKEY HIERBA": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/52/International_Hockey_Federation_logo.svg/512px-International_Hockey_Federation_logo.svg.png",
}

FALLBACK_POR_CATEGORIA_RAW: dict[str, str] = {
    "Tenis": "https://upload.wikimedia.org/wikipedia/commons/3/3f/ATP_Tour_logo.svg",
    "Padel": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Premier_Padel_logo.svg",
    "Escalada": "https://upload.wikimedia.org/wikipedia/commons/d/d7/International_Federation_of_Sport_Climbing_logo.svg",
    "Ciclismo": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/29/Union_Cycliste_Internationale_logo.svg/512px-Union_Cycliste_Internationale_logo.svg.png",
    "Snooker": "https://upload.wikimedia.org/wikipedia/en/thumb/6/64/World_Snooker_Tour_logo.svg/512px-World_Snooker_Tour_logo.svg.png",
    "Motor": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/33/F1.svg/512px-F1.svg.png",
    "Golf": "https://upload.wikimedia.org/wikipedia/en/thumb/c/cf/PGA_Tour_logo.svg/512px-PGA_Tour_logo.svg.png",
    "Combate": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/UFC_logo.svg/512px-UFC_logo.svg.png",
    "Béisbol": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Major_League_Baseball_logo.svg/512px-Major_League_Baseball_logo.svg.png",
    "Gimnasia": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/European_Gymnastics_logo.svg/512px-European_Gymnastics_logo.svg.png",
    "Hockey": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/52/International_Hockey_Federation_logo.svg/512px-International_Hockey_Federation_logo.svg.png",
    "Fútbol": "https://upload.wikimedia.org/wikipedia/commons/1/10/FIFA_logo_without_slogan.svg",
    "Baloncesto": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7a/Basketball.png/512px-Basketball.png",
    "Rugby": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/91/Rugby_ball.svg/512px-Rugby_ball.svg.png",
    "Fútbol Americano": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a2/National_Football_League_logo.svg/512px-National_Football_League_logo.svg.png",
    "Otros Deportes": "https://upload.wikimedia.org/wikipedia/commons/a/a7/Olympic_flag.svg",
    "Deportes": "https://upload.wikimedia.org/wikipedia/commons/a/a7/Olympic_flag.svg",
    "Tejo": "https://upload.wikimedia.org/wikipedia/commons/a/a7/Olympic_flag.svg",
}

# Diccionario pre-envuelto con CDN Proxy
CIRCUITO_LOGOS = {k: envolver_cdn_proxy(v) for k, v in CIRCUITO_LOGOS_RAW.items() if envolver_cdn_proxy(v)}
FALLBACK_POR_CATEGORIA = {k: envolver_cdn_proxy(v) for k, v in FALLBACK_POR_CATEGORIA_RAW.items() if envolver_cdn_proxy(v)}


def _normalizar(texto: Any) -> str:
    if not texto:
        return ""
    valor = unicodedata.normalize("NFD", str(texto).upper())
    valor = "".join(c for c in valor if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^A-Z0-9\s]", " ", valor).split())


def _cargar_cache() -> dict[str, str]:
    if not ARCHIVO_CACHE_LOGOS.exists():
        return {}
    try:
        return json.loads(ARCHIVO_CACHE_LOGOS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar_cache(cache: dict[str, str]) -> None:
    try:
        ARCHIVO_CACHE_LOGOS.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("No se pudo guardar logos_cache.json: %s", exc)


def resolver_logo_torneo(torneo: str, categoria: str, permitir_red: bool = False) -> str:
    """Resuelve el logo oficial con precedencia: Caché -> Catálogo Maestro -> Wikidata -> Fallback CDN."""
    if not torneo and not categoria:
        return ""

    torneo_norm = _normalizar(torneo)
    cache = _cargar_cache()
    if torneo_norm in cache and cache[torneo_norm] and not es_logo_basura(cache[torneo_norm]):
        # Limpiar si quedó cacheado el antiguo balón de fútbol para otros deportes
        if "Soccerball.svg" not in cache[torneo_norm] or categoria == "Fútbol":
            return cache[torneo_norm]

    # 1. Búsqueda directa en catálogo de federaciones/circuitos
    for clave, url in CIRCUITO_LOGOS.items():
        if clave in torneo_norm or torneo_norm in clave:
            cache[torneo_norm] = url
            _guardar_cache(cache)
            return url

    # 2. Búsqueda ligera en Wikidata API (si está habilitada la red)
    if permitir_red and torneo_norm:
        try:
            url_api = "https://www.wikidata.org/w/api.php"
            params = {
                "action": "wbsearchentities",
                "search": torneo,
                "language": "es",
                "format": "json",
                "limit": 1,
            }
            resp = requests.get(url_api, params=params, timeout=5)
            if resp.status_code == 200:
                resultados = resp.json().get("search", [])
                if resultados:
                    entidad_id = resultados[0].get("id")
                    if entidad_id:
                        claims_url = f"https://www.wikidata.org/wiki/Special:EntityData/{entidad_id}.json"
                        c_resp = requests.get(claims_url, timeout=5)
                        if c_resp.status_code == 200:
                            claims = c_resp.json().get("entities", {}).get(entidad_id, {}).get("claims", {})
                            imagen_prop = claims.get("P154") or claims.get("P18")
                            if imagen_prop:
                                filename = imagen_prop[0]["mainsnak"]["datavalue"]["value"]
                                filename_clean = filename.replace(" ", "_")
                                raw_url = f"https://commons.wikimedia.org/wiki/Special:FilePath/{filename_clean}"
                                cdn_url = envolver_cdn_proxy(raw_url)
                                cache[torneo_norm] = cdn_url
                                _guardar_cache(cache)
                                return cdn_url
        except Exception as exc:
            log.debug("Wikidata sin resultado para %s: %s", torneo, exc)

    # 3. Fallback oficial garantizado por categoría (Sin balones de fútbol para otros deportes)
    fallback = FALLBACK_POR_CATEGORIA.get(
        categoria,
        FALLBACK_POR_CATEGORIA.get("Otros Deportes", "")
    )
    if fallback:
        cache[torneo_norm] = fallback
        _guardar_cache(cache)
    return fallback
