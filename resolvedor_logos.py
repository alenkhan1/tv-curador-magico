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


def envolver_cdn_proxy(url: str) -> str:
    """Envuelve URLs externas en wsrv.nl para evitar bloqueos HTTP 403 por User-Agent en Android TV."""
    if not url:
        return ""
    if url.startswith("https://wsrv.nl/?url="):
        return url
    url_limpia = url.strip()
    return f"https://wsrv.nl/?url={urllib.parse.quote(url_limpia, safe='')}&w=400&output=webp"


CIRCUITO_LOGOS_RAW: dict[str, str] = {
    # Tenis
    "ATP": "https://upload.wikimedia.org/wikipedia/en/thumb/2/2a/ATP_Tour_logo.svg/512px-ATP_Tour_logo.svg.png",
    "WTA": "https://upload.wikimedia.org/wikipedia/en/thumb/0/03/WTA_logo_2020.svg/512px-WTA_logo_2020.svg.png",
    "DAVIS CUP": "https://upload.wikimedia.org/wikipedia/en/thumb/e/e0/Davis_Cup_Logo.svg/512px-Davis_Cup_Logo.svg.png",
    "CINCINNATI OPEN": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/14/Cincinnati_Open_logo.svg/512px-Cincinnati_Open_logo.svg.png",
    "WINSTON-SALEM": "https://upload.wikimedia.org/wikipedia/en/thumb/8/87/Winston-Salem_Open_logo.svg/512px-Winston-Salem_Open_logo.svg.png",
    "ABIERTO GNP": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d7/Abierto_GNP_Seguros_Logo.png/512px-Abierto_GNP_Seguros_Logo.png",
    "US OPEN": "https://upload.wikimedia.org/wikipedia/en/thumb/5/53/US_Open_logo.svg/512px-US_Open_logo.svg.png",
    "ROLAND GARROS": "https://upload.wikimedia.org/wikipedia/en/thumb/4/4c/Roland_Garros_logo.svg/512px-Roland_Garros_logo.svg.png",
    "WIMBLEDON": "https://upload.wikimedia.org/wikipedia/en/thumb/b/b9/Wimbledon_logo.svg/512px-Wimbledon_logo.svg.png",
    "AUSTRALIAN OPEN": "https://upload.wikimedia.org/wikipedia/en/thumb/0/00/Australian_Open_logo.svg/512px-Australian_Open_logo.svg.png",

    # Ciclismo
    "UCI": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/29/Union_Cycliste_Internationale_logo.svg/512px-Union_Cycliste_Internationale_logo.svg.png",
    "VUELTA": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/La_Vuelta_logo.svg/512px-La_Vuelta_logo.svg.png",
    "LA VUELTA": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/La_Vuelta_logo.svg/512px-La_Vuelta_logo.svg.png",
    "TOUR DE FRANCE": "https://upload.wikimedia.org/wikipedia/en/thumb/9/91/Tour_de_France_logo.svg/512px-Tour_de_France_logo.svg.png",
    "GIRO": "https://upload.wikimedia.org/wikipedia/en/thumb/8/82/Giro_d%27Italia_logo.svg/512px-Giro_d%27Italia_logo.svg.png",
    "RENEWI TOUR": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/93/Renewi_Tour_logo.svg/512px-Renewi_Tour_logo.svg.png",
    "VUELTA A COLOMBIA": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/29/Union_Cycliste_Internationale_logo.svg/512px-Union_Cycliste_Internationale_logo.svg.png",
    "CLASICO RCN": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/29/Union_Cycliste_Internationale_logo.svg/512px-Union_Cycliste_Internationale_logo.svg.png",

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

    # Combate
    "UFC": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/UFC_logo.svg/512px-UFC_logo.svg.png",
    "BKFC": "https://upload.wikimedia.org/wikipedia/en/thumb/6/60/Bare_Knuckle_Fighting_Championship_logo.png/512px-Bare_Knuckle_Fighting_Championship_logo.png",

    # Golf
    "PGA": "https://upload.wikimedia.org/wikipedia/en/thumb/c/cf/PGA_Tour_logo.svg/512px-PGA_Tour_logo.svg.png",
    "DP WORLD": "https://upload.wikimedia.org/wikipedia/en/thumb/6/65/DP_World_Tour_logo.svg/512px-DP_World_Tour_logo.svg.png",
    "BMW CHAMPIONSHIP": "https://upload.wikimedia.org/wikipedia/en/thumb/f/f6/BMW_Championship_logo.svg/512px-BMW_Championship_logo.svg.png",

    # Fútbol & Ligas Oficiales
    "LALIGA": "https://media.api-sports.io/football/leagues/140.png",
    "PREMIER LEAGUE": "https://media.api-sports.io/football/leagues/39.png",
    "SERIE A": "https://media.api-sports.io/football/leagues/135.png",
    "LIGUE 1": "https://media.api-sports.io/football/leagues/61.png",
    "BUNDESLIGA": "https://media.api-sports.io/football/leagues/78.png",
    "LIGA BETPLAY": "https://media.api-sports.io/football/leagues/239.png",
    "TORNEO BETPLAY": "https://media.api-sports.io/football/leagues/240.png",
    "COPA BETPLAY": "https://media.api-sports.io/football/leagues/241.png",
    "DIMAYOR": "https://media.api-sports.io/football/leagues/239.png",
    "COPA LIBERTADORES": "https://media.api-sports.io/football/leagues/13.png",
    "COPA SUDAMERICANA": "https://media.api-sports.io/football/leagues/11.png",
    "CHAMPIONS LEAGUE": "https://media.api-sports.io/football/leagues/2.png",

    # Béisbol y otros
    "MLB": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Major_League_Baseball_logo.svg/512px-Major_League_Baseball_logo.svg.png",
    "LMB": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8a/Liga_Mexicana_de_Beisbol_logo.svg/512px-Liga_Mexicana_de_Beisbol_logo.svg.png",
    "LITTLE LEAGUE": "https://upload.wikimedia.org/wikipedia/en/thumb/9/90/Little_League_logo.svg/512px-Little_League_logo.svg.png",
    "NCAA": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/NCAA_logo.svg/512px-NCAA_logo.svg.png",
    "GIMNASIA": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/European_Gymnastics_logo.svg/512px-European_Gymnastics_logo.svg.png",
    "HOCKEY HIERBA": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/52/International_Hockey_Federation_logo.svg/512px-International_Hockey_Federation_logo.svg.png",
}

FALLBACK_POR_CATEGORIA_RAW: dict[str, str] = {
    "Tenis": "https://upload.wikimedia.org/wikipedia/en/thumb/2/2a/ATP_Tour_logo.svg/512px-ATP_Tour_logo.svg.png",
    "Ciclismo": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/29/Union_Cycliste_Internationale_logo.svg/512px-Union_Cycliste_Internationale_logo.svg.png",
    "Snooker": "https://upload.wikimedia.org/wikipedia/en/thumb/6/64/World_Snooker_Tour_logo.svg/512px-World_Snooker_Tour_logo.svg.png",
    "Motor": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/33/F1.svg/512px-F1.svg.png",
    "Golf": "https://upload.wikimedia.org/wikipedia/en/thumb/c/cf/PGA_Tour_logo.svg/512px-PGA_Tour_logo.svg.png",
    "Combate": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/UFC_logo.svg/512px-UFC_logo.svg.png",
    "Béisbol": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Major_League_Baseball_logo.svg/512px-Major_League_Baseball_logo.svg.png",
    "Gimnasia": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/European_Gymnastics_logo.svg/512px-European_Gymnastics_logo.svg.png",
    "Hockey": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/52/International_Hockey_Federation_logo.svg/512px-International_Hockey_Federation_logo.svg.png",
    "Fútbol": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d3/Soccerball.svg/512px-Soccerball.svg.png",
    "Baloncesto": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7a/Basketball.png/512px-Basketball.png",
    "Rugby": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/91/Rugby_ball.svg/512px-Rugby_ball.svg.png",
    "Fútbol Americano": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a2/National_Football_League_logo.svg/512px-National_Football_League_logo.svg.png",
}

CIRCUITO_LOGOS = {k: envolver_cdn_proxy(v) for k, v in CIRCUITO_LOGOS_RAW.items()}
FALLBACK_POR_CATEGORIA = {k: envolver_cdn_proxy(v) for k, v in FALLBACK_POR_CATEGORIA_RAW.items()}


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
    """Resuelve el logo oficial garantizando el proxy anti-403 para Android TV."""
    if not torneo and not categoria:
        return ""

    torneo_norm = _normalizar(torneo)
    cache = _cargar_cache()
    if torneo_norm in cache and cache[torneo_norm]:
        return cache[torneo_norm]

    # 1. Búsqueda directa en catálogo de circuitos
    for clave, url in CIRCUITO_LOGOS.items():
        if clave in torneo_norm or torneo_norm in clave:
            cache[torneo_norm] = url
            _guardar_cache(cache)
            return url

    # 2. Búsqueda en Wikidata si está autorizada
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
                                filename = imagen_prop[0]["mainsnak"]["datavalue"]["value"].replace(" ", "_")
                                raw_url = f"https://commons.wikimedia.org/wiki/Special:FilePath/{filename}"
                                cdn_url = envolver_cdn_proxy(raw_url)
                                cache[torneo_norm] = cdn_url
                                _guardar_cache(cache)
                                return cdn_url
        except Exception as exc:
            log.debug("Wikidata sin resultado para %s: %s", torneo, exc)

    # 3. Fallback oficial garantizado por categoría
    fallback = FALLBACK_POR_CATEGORIA.get(
        categoria,
        envolver_cdn_proxy("https://upload.wikimedia.org/wikipedia/commons/thumb/d/d3/Soccerball.svg/512px-Soccerball.svg.png")
    )
    cache[torneo_norm] = fallback
    _guardar_cache(cache)
    return fallback
