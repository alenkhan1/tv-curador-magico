# -*- coding: utf-8 -*-
"""Resolvedor universal de logos y escudos deportivos con TheSportsDB, CDN Proxy (anti-403) y caché local."""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any, Optional

try:
    import requests
except ImportError:
    requests = None

log = logging.getLogger("resolvedor_logos")

def _http_get_json(url: str, headers: dict | None = None, timeout: int = 4) -> dict | None:
    if requests is not None:
        try:
            resp = requests.get(url, headers=headers or {}, timeout=timeout)
            return resp.json() if resp.status_code == 200 else None
        except Exception:
            return None
    import urllib.request, json
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="ignore"))
    except Exception:
        return None


ARCHIVO_CACHE_LOGOS = Path(os.environ.get("ARCHIVO_CACHE_LOGOS", "logos_cache.json"))
THESPORTSDB_KEY = (os.environ.get("THESPORTSDB_KEY") or "123").strip()
API_SPORTS_KEY = os.environ.get("API_SPORTS_KEY", "").strip()

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
    "CHENGDU OPEN": "https://upload.wikimedia.org/wikipedia/commons/3/3f/ATP_Tour_logo.svg",
    "HANGZHOU OPEN": "https://upload.wikimedia.org/wikipedia/commons/3/3f/ATP_Tour_logo.svg",

    # Fútbol Torneos Mayores y Ligas
    "CHAMPIONS LEAGUE": "https://upload.wikimedia.org/wikipedia/commons/f/f3/UEFA_Champions_League_logo_2.svg",
    "UCL": "https://upload.wikimedia.org/wikipedia/commons/f/f3/UEFA_Champions_League_logo_2.svg",
    "EUROPA LEAGUE": "https://logodownload.org/wp-content/uploads/2019/12/europa-league-logo.png",
    "CONFERENCE LEAGUE": "https://upload.wikimedia.org/wikipedia/commons/b/b5/UEFA_logo.svg",
    "UECL": "https://upload.wikimedia.org/wikipedia/commons/b/b5/UEFA_logo.svg",
    "UEFA NATIONS LEAGUE": "https://upload.wikimedia.org/wikipedia/commons/b/b5/UEFA_logo.svg",
    "NATIONS LEAGUE": "https://upload.wikimedia.org/wikipedia/commons/b/b5/UEFA_logo.svg",
    "UEFA": "https://upload.wikimedia.org/wikipedia/commons/b/b5/UEFA_logo.svg",
    "CONCACAF NATIONS LEAGUE": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/07/CONCACAF_logo.svg/512px-CONCACAF_logo.svg.png",
    "CONCACAF": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/07/CONCACAF_logo.svg/512px-CONCACAF_logo.svg.png",
    "COPA AFRICANA": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/07/Confederation_of_African_Football_logo.svg/512px-Confederation_of_African_Football_logo.svg.png",
    "CAF": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/07/Confederation_of_African_Football_logo.svg/512px-Confederation_of_African_Football_logo.svg.png",
    "CONMEBOL": "https://upload.wikimedia.org/wikipedia/commons/0/0e/CONMEBOL_logo.svg",
    "LIBERTADORES": "https://upload.wikimedia.org/wikipedia/commons/c/c5/Copa_Libertadores_logo_2017.svg",
    "SUDAMERICANA": "https://upload.wikimedia.org/wikipedia/commons/e/eb/Copa_Sudamericana_logo.svg",
    "LALIGA": "https://upload.wikimedia.org/wikipedia/commons/0/0f/LaLiga_logo_2023.svg",
    "LA LIGA": "https://upload.wikimedia.org/wikipedia/commons/0/0f/LaLiga_logo_2023.svg",
    "LALIGA HYPERMOTION": "https://upload.wikimedia.org/wikipedia/commons/0/0f/LaLiga_logo_2023.svg",
    "LALIGA SMARTBANK": "https://upload.wikimedia.org/wikipedia/commons/0/0f/LaLiga_logo_2023.svg",
    "PREMIER LEAGUE": "https://upload.wikimedia.org/wikipedia/en/f/f2/Premier_League_Logo.svg",
    "SERIE A": "https://upload.wikimedia.org/wikipedia/commons/e/e9/Serie_A_logo_2019.svg",
    "BUNDESLIGA": "https://upload.wikimedia.org/wikipedia/en/d/df/Bundesliga_logo_%282017%29.svg",
    "LIGUE 1": "https://upload.wikimedia.org/wikipedia/commons/5/5e/Ligue1_McDonald%27s_logo.svg",
    "LIGA BETPLAY": "https://upload.wikimedia.org/wikipedia/commons/e/ed/Liga_BetPlay_Dimayor_logo.png",
    "DIMAYOR": "https://upload.wikimedia.org/wikipedia/commons/e/ed/Liga_BetPlay_Dimayor_logo.png",
    "BETPLAY": "https://upload.wikimedia.org/wikipedia/commons/e/ed/Liga_BetPlay_Dimayor_logo.png",
    "COPA BETPLAY": "https://upload.wikimedia.org/wikipedia/commons/e/ed/Liga_BetPlay_Dimayor_logo.png",
    "TORNEO BETPLAY": "https://upload.wikimedia.org/wikipedia/commons/e/ed/Liga_BetPlay_Dimayor_logo.png",
    "MLS": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/76/MLS_crest_logo_RGB_gradient.svg/512px-MLS_crest_logo_RGB_gradient.svg.png",
    "MAJOR LEAGUE SOCCER": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/76/MLS_crest_logo_RGB_gradient.svg/512px-MLS_crest_logo_RGB_gradient.svg.png",
    "LIGA MX FEMENIL": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/36/Liga_MX_logo.svg/512px-Liga_MX_logo.svg.png",
    "LIGA MX": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/36/Liga_MX_logo.svg/512px-Liga_MX_logo.svg.png",
    "SEGUNDA DIVISION URUGUAY": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/cd/Asociacion_Uruguaya_de_Futbol_logo.svg/512px-Asociacion_Uruguaya_de_Futbol_logo.svg.png",
    "PRIMERA DIVISION URUGUAY": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/cd/Asociacion_Uruguaya_de_Futbol_logo.svg/512px-Asociacion_Uruguaya_de_Futbol_logo.svg.png",
    "NCAA": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/NCAA_logo.svg/512px-NCAA_logo.svg.png",
    "FIFA": "https://upload.wikimedia.org/wikipedia/commons/1/10/FIFA_logo_without_slogan.svg",
    "AMISTOSO": "https://upload.wikimedia.org/wikipedia/commons/1/10/FIFA_logo_without_slogan.svg",

    # Pádel y Escalada
    "PREMIER PADEL": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Premier_Padel_logo.svg",
    "PADEL": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Premier_Padel_logo.svg",
    "FIP": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Premier_Padel_logo.svg",
    "ESCALADA": "https://upload.wikimedia.org/wikipedia/commons/d/d7/International_Federation_of_Sport_Climbing_logo.svg",
    "IFSC": "https://upload.wikimedia.org/wikipedia/commons/d/d7/International_Federation_of_Sport_Climbing_logo.svg",

    # Ciclismo
    "LA VUELTA A ESPANA": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/La_Vuelta_logo.svg/512px-La_Vuelta_logo.svg.png",
    "LA VUELTA": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/La_Vuelta_logo.svg/512px-La_Vuelta_logo.svg.png",
    "VUELTA A ESPANA": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/La_Vuelta_logo.svg/512px-La_Vuelta_logo.svg.png",
    "VUELTA": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/La_Vuelta_logo.svg/512px-La_Vuelta_logo.svg.png",
    "TOUR DE FRANCE": "https://upload.wikimedia.org/wikipedia/en/thumb/9/91/Tour_de_France_logo.svg/512px-Tour_de_France_logo.svg.png",
    "GIRO D ITALIA": "https://upload.wikimedia.org/wikipedia/en/thumb/8/82/Giro_d%27Italia_logo.svg/512px-Giro_d%27Italia_logo.svg.png",
    "GIRO": "https://upload.wikimedia.org/wikipedia/en/thumb/8/82/Giro_d%27Italia_logo.svg/512px-Giro_d%27Italia_logo.svg.png",
    "UCI": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/29/Union_Cycliste_Internationale_logo.svg/512px-Union_Cycliste_Internationale_logo.svg.png",

    # Snooker
    "WST": "https://upload.wikimedia.org/wikipedia/en/thumb/6/64/World_Snooker_Tour_logo.svg/512px-World_Snooker_Tour_logo.svg.png",
    "SNOOKER": "https://upload.wikimedia.org/wikipedia/en/thumb/6/64/World_Snooker_Tour_logo.svg/512px-World_Snooker_Tour_logo.svg.png",

    # Motor
    "FORMULA 1": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/33/F1.svg/512px-F1.svg.png",
    "F1": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/33/F1.svg/512px-F1.svg.png",
    "MOTOGP": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a0/Moto_Gp_logo.svg/512px-Moto_Gp_logo.svg.png",
    "INDYCAR": "https://upload.wikimedia.org/wikipedia/en/thumb/3/3c/IndyCar_Series_logo.svg/512px-IndyCar_Series_logo.svg.png",
    "NASCAR": "https://upload.wikimedia.org/wikipedia/commons/a/a2/NASCAR_logo.svg",

    # Combate
    "UFC": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/UFC_logo.svg/512px-UFC_logo.svg.png",
    "BKFC": "https://upload.wikimedia.org/wikipedia/en/thumb/6/60/Bare_Knuckle_Fighting_Championship_logo.png/512px-Bare_Knuckle_Fighting_Championship_logo.png",
    "WWE RAW": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/07/WWE_Raw_logo.svg/512px-WWE_Raw_logo.svg.png",
    "WRESTLING": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/07/WWE_Raw_logo.svg/512px-WWE_Raw_logo.svg.png",
    "BOXEO": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/UFC_logo.svg/512px-UFC_logo.svg.png",

    # Golf
    "PGA TOUR": "https://upload.wikimedia.org/wikipedia/en/thumb/c/cf/PGA_Tour_logo.svg/512px-PGA_Tour_logo.svg.png",
    "PGA": "https://upload.wikimedia.org/wikipedia/en/thumb/c/cf/PGA_Tour_logo.svg/512px-PGA_Tour_logo.svg.png",
    "DP WORLD": "https://upload.wikimedia.org/wikipedia/en/thumb/6/65/DP_World_Tour_logo.svg/512px-DP_World_Tour_logo.svg.png",

    # Baloncesto
    "NBA": "https://upload.wikimedia.org/wikipedia/en/thumb/0/03/National_Basketball_Association_logo.svg/512px-National_Basketball_Association_logo.svg.png",
    "EUROLEAGUE": "https://upload.wikimedia.org/wikipedia/en/thumb/5/52/Euroleague_Basketball_logo.svg/512px-Euroleague_Basketball_logo.svg.png",
    "ACB": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/87/Liga_Endesa_logo.svg/512px-Liga_Endesa_logo.svg.png",
    "LIGA ENDESA": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/87/Liga_Endesa_logo.svg/512px-Liga_Endesa_logo.svg.png",

    # Béisbol
    "MLB": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Major_League_Baseball_logo.svg/512px-Major_League_Baseball_logo.svg.png",

    # Fútbol Americano
    "NFL": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a2/National_Football_League_logo.svg/512px-National_Football_League_logo.svg.png",

    # Polo
    "POLO": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d3/Polo_pictogram.svg/512px-Polo_pictogram.svg.png",
    "PALERMO": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d3/Polo_pictogram.svg/512px-Polo_pictogram.svg.png",
    "CAMPEONATO ARGENTINO DE POLO": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d3/Polo_pictogram.svg/512px-Polo_pictogram.svg.png",
    "ASOCIACION ARGENTINA DE POLO": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d3/Polo_pictogram.svg/512px-Polo_pictogram.svg.png",
}

FALLBACK_POR_CATEGORIA_RAW: dict[str, str] = {
    "Tenis": "https://upload.wikimedia.org/wikipedia/commons/3/3f/ATP_Tour_logo.svg",
    "Padel": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Premier_Padel_logo.svg",
    "Pádel": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Premier_Padel_logo.svg",
    "Escalada": "https://upload.wikimedia.org/wikipedia/commons/d/d7/International_Federation_of_Sport_Climbing_logo.svg",
    "Ciclismo": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/29/Union_Cycliste_Internationale_logo.svg/512px-Union_Cycliste_Internationale_logo.svg.png",
    "Snooker": "https://upload.wikimedia.org/wikipedia/en/thumb/6/64/World_Snooker_Tour_logo.svg/512px-World_Snooker_Tour_logo.svg.png",
    "Motor": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/33/F1.svg/512px-F1.svg.png",
    "Golf": "https://upload.wikimedia.org/wikipedia/en/thumb/c/cf/PGA_Tour_logo.svg/512px-PGA_Tour_logo.svg.png",
    "Combate": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/UFC_logo.svg/512px-UFC_logo.svg.png",
    "Beisbol": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Major_League_Baseball_logo.svg/512px-Major_League_Baseball_logo.svg.png",
    "Béisbol": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Major_League_Baseball_logo.svg/512px-Major_League_Baseball_logo.svg.png",
    "Fútbol": "https://upload.wikimedia.org/wikipedia/commons/1/10/FIFA_logo_without_slogan.svg",
    "Futbol": "https://upload.wikimedia.org/wikipedia/commons/1/10/FIFA_logo_without_slogan.svg",
    "Baloncesto": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7a/Basketball.png/512px-Basketball.png",
    "Rugby": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/91/Rugby_ball.svg/512px-Rugby_ball.svg.png",
    "Fútbol Americano": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a2/National_Football_League_logo.svg/512px-National_Football_League_logo.svg.png",
    "Polo": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d3/Polo_pictogram.svg/512px-Polo_pictogram.svg.png",
    "Tejo": "",
    "Otros Deportes": "",
}

CIRCUITO_LOGOS = {k: envolver_cdn_proxy(v) for k, v in CIRCUITO_LOGOS_RAW.items() if envolver_cdn_proxy(v)}
FALLBACK_POR_CATEGORIA = {k: envolver_cdn_proxy(v) for k, v in FALLBACK_POR_CATEGORIA_RAW.items() if envolver_cdn_proxy(v)}

def _normalizar(texto: str) -> str:
    """Normaliza texto eliminando acentos y caracteres especiales."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    sin_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z0-9\s]", " ", sin_acento).upper().strip()

def _cargar_cache() -> dict[str, str]:
    if ARCHIVO_CACHE_LOGOS.exists():
        try:
            return json.loads(ARCHIVO_CACHE_LOGOS.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _guardar_cache(cache: dict[str, str]) -> None:
    try:
        ARCHIVO_CACHE_LOGOS.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("No se pudo guardar la cache de logos: %s", e)

def resolver_logo_torneo(torneo: str, categoria: str, permitir_red: bool = False) -> str:
    """Resuelve el logo oficial del torneo con precedencia estricta por palabras completas."""
    if not torneo and not categoria:
        return ""

    torneo_norm = _normalizar(torneo)
    cache = _cargar_cache()
    clave_cache = f"torneo_{torneo_norm}"

    # 1. Catálogo maestro de insignias oficiales (Precedencia Máxima)
    claves_ordenadas = sorted(CIRCUITO_LOGOS.keys(), key=len, reverse=True)
    for clave in claves_ordenadas:
        patron = r"\b" + re.escape(clave) + r"\b"
        if re.search(patron, torneo_norm):
            url = CIRCUITO_LOGOS[clave]
            cache[clave_cache] = url
            _guardar_cache(cache)
            return url

    # 2. Caché previo en disco (siempre que no sea un fallback genérico)
    fallbacks_set = set(FALLBACK_POR_CATEGORIA.values())
    if clave_cache in cache and cache[clave_cache] and not es_logo_basura(cache[clave_cache]):
        if cache[clave_cache] not in fallbacks_set:
            return cache[clave_cache]

    # 3. Búsqueda en TheSportsDB si la red está permitida
    if permitir_red and torneo_norm:
        try:
            url_tsdb = f"https://www.thesportsdb.com/api/v1/json/{THESPORTSDB_KEY}/search_all_leagues.php?c={urllib.parse.quote(torneo)}"
            resp = requests.get(url_tsdb, timeout=4)
            if resp.status_code == 200:
                data = resp.json()
                leagues = data.get("countries") or []
                if leagues:
                    badge = leagues[0].get("strBadge") or leagues[0].get("strLogo")
                    if badge and not es_logo_basura(badge):
                        cdn_url = envolver_cdn_proxy(badge)
                        cache[clave_cache] = cdn_url
                        _guardar_cache(cache)
                        return cdn_url
        except Exception:
            pass

    # 4. Fallback por categoría (nunca bandera olímpica como comodín genérico)
    fallback = FALLBACK_POR_CATEGORIA.get(
        categoria,
        FALLBACK_POR_CATEGORIA.get("Otros Deportes", "")
    )
    if fallback:
        cache[clave_cache] = fallback
        _guardar_cache(cache)
    return fallback


PAISES_ALIAS = {
    "BELGICA": "Belgium",
    "FRANCIA": "France",
    "TURQUIA": "Turkey",
    "ITALIA": "Italy",
    "HUNGRIA": "Hungary",
    "IRLANDA DEL NORTE": "Northern Ireland",
    "IRLANDA": "Ireland",
    "UCRANIA": "Ukraine",
    "GEORGIA": "Georgia",
    "ARMENIA": "Armenia",
    "MONTENEGRO": "Montenegro",
    "CHIPRE": "Cyprus",
    "LETONIA": "Latvia",
    "SURINAM": "Suriname",
    "MARTINICA": "Martinique",
    "GUADALUPE": "Guadeloupe",
    "BARBADOS": "Barbados",
    "GRANADA": "Grenada",
    "BERMUDAS": "Bermuda",
    "SANTA LUCIA": "Saint Lucia",
    "BOTSUANA": "Botswana",
    "TUNEZ": "Tunisia",
    "ZIMBABUE": "Zimbabwe",
    "REPUBLICA CENTROAFRICANA": "Central African Republic",
    "RD DEL CONGO": "DR Congo",
    "CONGO": "Congo",
    "GUINEA ECUATORIAL": "Equatorial Guinea",
    "SIERRA LEONA": "Sierra Leone",
    "BURKINA FASO": "Burkina Faso",
    "JAPON": "Japan",
    "ALEMANIA": "Germany",
    "ESPANA": "Spain",
    "ESTADOS UNIDOS": "USA",
    "PAISES BAJOS": "Netherlands",
    "INGLATERRA": "England",
    "SUIZA": "Switzerland",
    "SUECIA": "Sweden",
    "POLONIA": "Poland",
    "RUMANIA": "Romania",
    "ARGELIA": "Algeria",
    "MARRUECOS": "Morocco",
    "EGIPTO": "Egypt",
    "SENEGAL": "Senegal",
    "COSTA DE MARFIL": "Ivory Coast",
    "SUDAFRICA": "South Africa",
    "CAMERUN": "Cameroon",
    "NIGERIA": "Nigeria",
    "GHANA": "Ghana",
    "CHILE": "Chile",
    "COLOMBIA": "Colombia",
    "ARGENTINA": "Argentina",
    "BRASIL": "Brazil",
    "PERU": "Peru",
    "URUGUAY": "Uruguay",
    "PARAGUAY": "Paraguay",
    "ECUADOR": "Ecuador",
    "VENEZUELA": "Venezuela",
    "BOLIVIA": "Bolivia",
    "MEXICO": "Mexico",
    "COSTA RICA": "Costa Rica",
    "PANAMA": "Panama",
    "HONDURAS": "Honduras",
    "EL SALVADOR": "El Salvador",
    "GUATEMALA": "Guatemala",
    "JAMAICA": "Jamaica",
    "CANADA": "Canada",
}

def resolver_logo_equipo(equipo: str, deporte: str = "Fútbol", permitir_red: bool = True) -> str:
    """Resuelve el escudo/logo oficial de un equipo mediante TheSportsDB o Api-Football con caché local."""
    if not equipo:
        return ""

    equipo_limpio = equipo.strip()
    equipo_norm = _normalizar(equipo_limpio)
    cache = _cargar_cache()
    clave_cache = f"equipo_{equipo_norm}"

    if clave_cache in cache and cache[clave_cache] and not es_logo_basura(cache[clave_cache]):
        return cache[clave_cache]

    if not permitir_red:
        return ""

    # 1. Consulta a TheSportsDB
    try:
        nombre_busqueda = PAISES_ALIAS.get(equipo_norm, equipo_limpio)
        url_tsdb = f"https://www.thesportsdb.com/api/v1/json/{THESPORTSDB_KEY}/searchteams.php?t={urllib.parse.quote(nombre_busqueda)}"
        resp = requests.get(url_tsdb, timeout=4)
        if resp.status_code == 200:
            data = resp.json()
            teams = data.get("teams") or []
            if teams:
                badge = teams[0].get("strBadge") or teams[0].get("strLogo")
                if badge and not es_logo_basura(badge):
                    cdn_url = envolver_cdn_proxy(badge)
                    cache[clave_cache] = cdn_url
                    _guardar_cache(cache)
                    return cdn_url
    except Exception as exc:
        log.debug("TheSportsDB fallo para equipo %s: %s", equipo_limpio, exc)

    # 2. Respaldo opcional en Api-Football si existe API_SPORTS_KEY y es fútbol
    if API_SPORTS_KEY and ("futbol" in deporte.lower() or "fútbol" in deporte.lower()):
        try:
            url_api = f"https://v3.football.api-sports.io/teams?search={urllib.parse.quote(equipo_limpio)}"
            resp = requests.get(url_api, headers={"x-apisports-key": API_SPORTS_KEY}, timeout=4)
            if resp.status_code == 200:
                data = resp.json()
                res = data.get("response") or []
                if res:
                    logo = res[0].get("team", {}).get("logo")
                    if logo and not es_logo_basura(logo):
                        cdn_url = envolver_cdn_proxy(logo)
                        cache[clave_cache] = cdn_url
                        _guardar_cache(cache)
                        return cdn_url
        except Exception as exc:
            log.debug("Api-Football fallo para equipo %s: %s", equipo_limpio, exc)

    return ""


def guardar_cache_logos():
    try:
        cache = _cargar_cache()
        return _guardar_cache(cache)
    except Exception:
        pass
