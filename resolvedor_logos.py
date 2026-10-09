# -*- coding: utf-8 -*-
"""Resolvedor universal de logos y escudos deportivos con TheSportsDB, CDN Proxy (anti-403) y caché local."""
from __future__ import annotations

import json
import logging
import os
import time
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

# Cargar .env si existe en el directorio del script
_env_file = Path(__file__).resolve().parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        if "=" in _line and not _line.strip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ[_k.strip()] = _v.strip()

ARCHIVO_CACHE_LOGOS = Path(os.environ.get("ARCHIVO_CACHE_LOGOS", "logos_cache.json"))
ARCHIVO_CATALOGO_TORNEOS = Path(__file__).resolve().parent / "catalogo_maestro_torneos.json"
ARCHIVO_CATALOGO_EQUIPOS = Path(__file__).resolve().parent / "catalogo_maestro_equipos.json"
_CATALOGO_TORNEOS_CACHE: dict[str, str] | None = None
_CATALOGO_EQUIPOS_CACHE: dict[str, str] | None = None

def _obtener_catalogo_equipos() -> dict[str, str]:
    global _CATALOGO_EQUIPOS_CACHE
    if _CATALOGO_EQUIPOS_CACHE is None:
        if ARCHIVO_CATALOGO_EQUIPOS.exists():
            try:
                _CATALOGO_EQUIPOS_CACHE = json.loads(ARCHIVO_CATALOGO_EQUIPOS.read_text(encoding="utf-8"))
            except Exception:
                _CATALOGO_EQUIPOS_CACHE = {}
        else:
            _CATALOGO_EQUIPOS_CACHE = {}
    return _CATALOGO_EQUIPOS_CACHE

def _guardar_catalogo_equipos(nuevos: dict[str, str]) -> None:
    if not nuevos:
        return
    cat = _obtener_catalogo_equipos()
    cat.update(nuevos)
    tmp_path = ARCHIVO_CATALOGO_EQUIPOS.with_suffix(".tmp")
    try:
        tmp_path.write_text(json.dumps(cat, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(ARCHIVO_CATALOGO_EQUIPOS)
    except Exception as e:
        log.warning("No se pudo guardar catalogo_maestro_equipos: %s", e)
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

def _obtener_catalogo_torneos() -> dict[str, str]:
    global _CATALOGO_TORNEOS_CACHE
    if _CATALOGO_TORNEOS_CACHE is None:
        if ARCHIVO_CATALOGO_TORNEOS.exists():
            try:
                _CATALOGO_TORNEOS_CACHE = json.loads(ARCHIVO_CATALOGO_TORNEOS.read_text(encoding="utf-8"))
            except Exception:
                _CATALOGO_TORNEOS_CACHE = {}
        else:
            _CATALOGO_TORNEOS_CACHE = {}
    return _CATALOGO_TORNEOS_CACHE
THESPORTSDB_KEY = (os.environ.get("THESPORTSDB_KEY") or "3").strip()
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
    """Envuelve URLs externas en wsrv.nl para evitar bloqueos HTTP 403 y recortar paddings vacios con &trim=10."""
    if not url or es_logo_basura(url):
        return ""
    if url.startswith("https://flagcdn.com/"):
        return url
    if url.startswith("https://wsrv.nl/?url="):
        if "&trim=" not in url:
            return f"{url}&trim=10"
        return url
    url_limpia = url.strip()
    return f"https://wsrv.nl/?url={urllib.parse.quote(url_limpia, safe='')}&w=400&output=webp&trim=10"

CIRCUITO_LOGOS_RAW: dict[str, str] = {
    # Competiciones Mayores de Selecciones (Diferenciadas por Torneo, Nunca por Confederacion Generica)
    "UEFA NATIONS LEAGUE": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/UEFA_Nations_League_logo.svg/512px-UEFA_Nations_League_logo.svg.png",
    "NATIONS LEAGUE": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/UEFA_Nations_League_logo.svg/512px-UEFA_Nations_League_logo.svg.png",
    "UEFA EURO": "https://upload.wikimedia.org/wikipedia/en/thumb/9/96/UEFA_Euro_2024_logo.svg/512px-UEFA_Euro_2024_logo.svg.png",
    "EUROCOPA": "https://upload.wikimedia.org/wikipedia/en/thumb/9/96/UEFA_Euro_2024_logo.svg/512px-UEFA_Euro_2024_logo.svg.png",
    "COPA AMERICA": "https://upload.wikimedia.org/wikipedia/en/thumb/0/07/2024_Copa_Am%C3%A9rica_logo.svg/512px-2024_Copa_Am%C3%A9rica_logo.svg.png",
    "CONMEBOL COPA AMERICA": "https://upload.wikimedia.org/wikipedia/en/thumb/0/07/2024_Copa_Am%C3%A9rica_logo.svg/512px-2024_Copa_Am%C3%A9rica_logo.svg.png",
    "COPA LIBERTADORES": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c5/Copa_Libertadores_logo_2017.svg/512px-Copa_Libertadores_logo_2017.svg.png",
    "LIBERTADORES": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c5/Copa_Libertadores_logo_2017.svg/512px-Copa_Libertadores_logo_2017.svg.png",
    "COPA SUDAMERICANA": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/eb/Copa_Sudamericana_logo.svg/512px-Copa_Sudamericana_logo.svg.png",
    "SUDAMERICANA": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/eb/Copa_Sudamericana_logo.svg/512px-Copa_Sudamericana_logo.svg.png",
    "RECOPA SUDAMERICANA": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e5/Recopa_Sudamericana_logo.svg/512px-Recopa_Sudamericana_logo.svg.png",
    "CONCACAF NATIONS LEAGUE": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/52/CONCACAF_Nations_League_logo.svg/512px-CONCACAF_Nations_League_logo.svg.png",
    "COPA ORO": "https://upload.wikimedia.org/wikipedia/en/thumb/3/36/CONCACAF_Gold_Cup_logo.svg/512px-CONCACAF_Gold_Cup_logo.svg.png",
    "COPA AFRICANA": "https://upload.wikimedia.org/wikipedia/en/thumb/0/06/Africa_Cup_of_Nations_logo.svg/512px-Africa_Cup_of_Nations_logo.svg.png",

    # Clubes UEFA
    "UEFA CHAMPIONS LEAGUE": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f3/UEFA_Champions_League_logo_2.svg/512px-UEFA_Champions_League_logo_2.svg.png",
    "CHAMPIONS LEAGUE": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f3/UEFA_Champions_League_logo_2.svg/512px-UEFA_Champions_League_logo_2.svg.png",
    "UEFA EUROPA LEAGUE": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/03/UEFA_Europa_League_logo_%282024%29.svg/512px-UEFA_Europa_League_logo_%282024%29.svg.png",
    "EUROPA LEAGUE": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/03/UEFA_Europa_League_logo_%282024%29.svg/512px-UEFA_Europa_League_logo_%282024%29.svg.png",
    "CONFERENCE LEAGUE": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/53/UEFA_Europa_Conference_League_logo.svg/512px-UEFA_Europa_Conference_League_logo.svg.png",
    "UECL": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/53/UEFA_Europa_Conference_League_logo.svg/512px-UEFA_Europa_Conference_League_logo.svg.png",
    "CHAMPIONS LEAGUE FEMENINA": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f3/UEFA_Champions_League_logo_2.svg/512px-UEFA_Champions_League_logo_2.svg.png",

    # Balonmano
    "ASOBAL": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e4/Liga_Asobal_logo.svg/512px-Liga_Asobal_logo.svg.png",
    "LIGA ASOBAL": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e4/Liga_Asobal_logo.svg/512px-Liga_Asobal_logo.svg.png",
    "LIGA NEXUS ENERGIA ASOBAL": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e4/Liga_Asobal_logo.svg/512px-Liga_Asobal_logo.svg.png",
    "EHF CHAMPIONS LEAGUE": "https://upload.wikimedia.org/wikipedia/en/thumb/7/7b/EHF_Champions_League_logo.svg/512px-EHF_Champions_League_logo.svg.png",

    # Ligas Nacionales
    "LALIGA": "https://upload.wikimedia.org/wikipedia/commons/0/0f/LaLiga_logo_2023.svg",
    "LA LIGA": "https://upload.wikimedia.org/wikipedia/commons/0/0f/LaLiga_logo_2023.svg",
    "LALIGA HYPERMOTION": "https://upload.wikimedia.org/wikipedia/commons/0/0f/LaLiga_logo_2023.svg",
    "LALIGA SMARTBANK": "https://upload.wikimedia.org/wikipedia/commons/0/0f/LaLiga_logo_2023.svg",
    "PREMIER LEAGUE": "https://upload.wikimedia.org/wikipedia/en/f/f2/Premier_League_Logo.svg",
    "SERIE A": "https://upload.wikimedia.org/wikipedia/commons/e/e9/Serie_A_logo_2019.svg",
    "BUNDESLIGA": "https://upload.wikimedia.org/wikipedia/en/d/df/Bundesliga_logo_%282017%29.svg",
    "LIGUE 1": "https://upload.wikimedia.org/wikipedia/commons/5/5e/Ligue1_McDonald%27s_logo.svg",
    "LIGA BETPLAY": "https://upload.wikimedia.org/wikipedia/commons/e/ed/Liga_BetPlay_Dimayor_logo.png",
    "COPA BETPLAY": "https://upload.wikimedia.org/wikipedia/commons/e/ed/Liga_BetPlay_Dimayor_logo.png",
    "COPA ARGENTINA": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/87/Copa_Argentina_logo.svg/512px-Copa_Argentina_logo.svg.png",
    "MLS": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/76/MLS_crest_logo_RGB_gradient.svg/512px-MLS_crest_logo_RGB_gradient.svg.png",
    "MAJOR LEAGUE SOCCER": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/76/MLS_crest_logo_RGB_gradient.svg/512px-MLS_crest_logo_RGB_gradient.svg.png",
    "LIGA MX": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/36/Liga_MX_logo.svg/512px-Liga_MX_logo.svg.png",

    # Tenis / Pádel
    "ATP": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3f/ATP_Tour_logo.svg/512px-ATP_Tour_logo.svg.png",
    "WTA": "https://upload.wikimedia.org/wikipedia/en/thumb/3/38/WTA_Tour_logo_2020.svg/512px-WTA_Tour_logo_2020.svg.png",
    "JAPAN OPEN": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3f/ATP_Tour_logo.svg/512px-ATP_Tour_logo.svg.png",
    "CHINA OPEN": "https://upload.wikimedia.org/wikipedia/en/thumb/3/38/WTA_Tour_logo_2020.svg/512px-WTA_Tour_logo_2020.svg.png",
    "PREMIER PADEL": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Premier_Padel_logo.svg",
    "PADEL": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Premier_Padel_logo.svg",

    # Deportes Americanos y Globales
    "NBA": "https://upload.wikimedia.org/wikipedia/en/thumb/0/03/National_Basketball_Association_logo.svg/512px-National_Basketball_Association_logo.svg.png",
    "WNBA": "https://upload.wikimedia.org/wikipedia/en/thumb/8/86/WNBA_logo.svg/512px-WNBA_logo.svg.png",
    "MLB": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Major_League_Baseball_logo.svg/512px-Major_League_Baseball_logo.svg.png",
    "NFL": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a2/National_Football_League_logo.svg/512px-National_Football_League_logo.svg.png",
    "NHL": "https://upload.wikimedia.org/wikipedia/en/thumb/3/3a/05_NHL_Shield.svg/512px-05_NHL_Shield.svg.png",
    "ACB": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/87/Liga_Endesa_logo.svg/512px-Liga_Endesa_logo.svg.png",
    "LIGA ENDESA": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/87/Liga_Endesa_logo.svg/512px-Liga_Endesa_logo.svg.png",
    "EUROLEAGUE": "https://upload.wikimedia.org/wikipedia/en/thumb/5/52/Euroleague_Basketball_logo.svg/512px-Euroleague_Basketball_logo.svg.png",

    # Motor
    "FORMULA 1": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/33/F1.svg/512px-F1.svg.png",
    "F1": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/33/F1.svg/512px-F1.svg.png",
    "NASCAR": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0f/NASCAR_logo.svg/512px-NASCAR_logo.svg.png",
    "INDYCAR": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b2/IndyCar_Series_logo.svg/512px-IndyCar_Series_logo.svg.png",
    "WRC": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c5/WRC_logo.svg/512px-WRC_logo.svg.png",
    "SUPERBIKE": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e0/WorldSBK_logo.svg/512px-WorldSBK_logo.svg.png",
    "MOTOGP": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a0/Moto_Gp_logo.svg/512px-Moto_Gp_logo.svg.png",
    "WRC": "https://upload.wikimedia.org/wikipedia/commons/thumb/6/65/World_Rally_Championship_logo.svg/512px-World_Rally_Championship_logo.svg.png",

    # Ciclismo / Snooker / Combate
    "UCI": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/29/Union_Cycliste_Internationale_logo.svg/512px-Union_Cycliste_Internationale_logo.svg.png",
    "LA VUELTA": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/La_Vuelta_logo.svg/512px-La_Vuelta_logo.svg.png",
    "TOUR DE FRANCE": "https://upload.wikimedia.org/wikipedia/en/thumb/9/91/Tour_de_France_logo.svg/512px-Tour_de_France_logo.svg.png",
    "GIRO D ITALIA": "https://upload.wikimedia.org/wikipedia/en/thumb/8/82/Giro_d%27Italia_logo.svg/512px-Giro_d%27Italia_logo.svg.png",
    "WST": "https://upload.wikimedia.org/wikipedia/en/thumb/6/64/World_Snooker_Tour_logo.svg/512px-World_Snooker_Tour_logo.svg.png",
    "SNOOKER": "https://upload.wikimedia.org/wikipedia/en/thumb/6/64/World_Snooker_Tour_logo.svg/512px-World_Snooker_Tour_logo.svg.png",
    "SHENZHEN OPEN": "https://upload.wikimedia.org/wikipedia/en/thumb/6/64/World_Snooker_Tour_logo.svg/512px-World_Snooker_Tour_logo.svg.png",
    "UFC": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/UFC_logo.svg/512px-UFC_logo.svg.png",
    "BOXEO": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/UFC_logo.svg/512px-UFC_logo.svg.png",
}

FALLBACK_POR_CATEGORIA_RAW: dict[str, str] = {
    "Tenis": "https://upload.wikimedia.org/wikipedia/commons/3/3f/ATP_Tour_logo.svg",
    "Padel": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Premier_Padel_logo.svg",
    "Pádel": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Premier_Padel_logo.svg",
    "Ciclismo": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/29/Union_Cycliste_Internationale_logo.svg/512px-Union_Cycliste_Internationale_logo.svg.png",
    "Snooker": "https://upload.wikimedia.org/wikipedia/en/thumb/6/64/World_Snooker_Tour_logo.svg/512px-World_Snooker_Tour_logo.svg.png",
    "Motor": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/33/F1.svg/512px-F1.svg.png",
    "Golf": "https://upload.wikimedia.org/wikipedia/en/thumb/c/cf/PGA_Tour_logo.svg/512px-PGA_Tour_logo.svg.png",
    "Combate": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/UFC_logo.svg/512px-UFC_logo.svg.png",
    "Balonmano": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e4/Liga_Asobal_logo.svg/512px-Liga_Asobal_logo.svg.png",
    "Beisbol": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Major_League_Baseball_logo.svg/512px-Major_League_Baseball_logo.svg.png",
    "Béisbol": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Major_League_Baseball_logo.svg/512px-Major_League_Baseball_logo.svg.png",
    "Fútbol": "",
    "Futbol": "",
    "Baloncesto": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7a/Basketball.png/512px-Basketball.png",
    "Otros Deportes": "",
}

CIRCUITO_LOGOS = {k: envolver_cdn_proxy(v) for k, v in CIRCUITO_LOGOS_RAW.items() if envolver_cdn_proxy(v)}
FALLBACK_POR_CATEGORIA = {k: envolver_cdn_proxy(v) for k, v in FALLBACK_POR_CATEGORIA_RAW.items() if envolver_cdn_proxy(v)}

# Banderas oficiales directas para todas las selecciones del mundo en FlagCDN HD (Universal)
BANDERAS_PAISES_RAW: dict[str, str] = {
    # Europa
    "ESPANA": "https://flagcdn.com/256x192/es.png", "SPAIN": "https://flagcdn.com/256x192/es.png",
    "REPUBLICA CHECA": "https://flagcdn.com/256x192/cz.png", "CHEQUIA": "https://flagcdn.com/256x192/cz.png",
    "CZECH REPUBLIC": "https://flagcdn.com/256x192/cz.png", "CZECHIA": "https://flagcdn.com/256x192/cz.png",
    "R CHECA": "https://flagcdn.com/256x192/cz.png", "REP CHECA": "https://flagcdn.com/256x192/cz.png",
    "FRANCIA": "https://flagcdn.com/256x192/fr.png", "FRANCE": "https://flagcdn.com/256x192/fr.png",
    "ITALIA": "https://flagcdn.com/256x192/it.png", "ITALY": "https://flagcdn.com/256x192/it.png",
    "ALEMANIA": "https://flagcdn.com/256x192/de.png", "GERMANY": "https://flagcdn.com/256x192/de.png",
    "INGLATERRA": "https://flagcdn.com/256x192/gb-eng.png", "ENGLAND": "https://flagcdn.com/256x192/gb-eng.png",
    "ESCOCIA": "https://flagcdn.com/256x192/gb-sct.png", "SCOTLAND": "https://flagcdn.com/256x192/gb-sct.png",
    "GALES": "https://flagcdn.com/256x192/gb-wls.png", "WALES": "https://flagcdn.com/256x192/gb-wls.png",
    "IRLANDA DEL NORTE": "https://flagcdn.com/256x192/gb-nir.png", "NORTHERN IRELAND": "https://flagcdn.com/256x192/gb-nir.png",
    "PORTUGAL": "https://flagcdn.com/256x192/pt.png",
    "PAISES BAJOS": "https://flagcdn.com/256x192/nl.png", "HOLANDA": "https://flagcdn.com/256x192/nl.png", "NETHERLANDS": "https://flagcdn.com/256x192/nl.png",
    "BELGICA": "https://flagcdn.com/256x192/be.png", "BELGIUM": "https://flagcdn.com/256x192/be.png",
    "CROACIA": "https://flagcdn.com/256x192/hr.png", "CROATIA": "https://flagcdn.com/256x192/hr.png",
    "SUIZA": "https://flagcdn.com/256x192/ch.png", "SWITZERLAND": "https://flagcdn.com/256x192/ch.png",
    "DINAMARCA": "https://flagcdn.com/256x192/dk.png", "DENMARK": "https://flagcdn.com/256x192/dk.png",
    "SUECIA": "https://flagcdn.com/256x192/se.png", "SWEDEN": "https://flagcdn.com/256x192/se.png",
    "POLONIA": "https://flagcdn.com/256x192/pl.png", "POLAND": "https://flagcdn.com/256x192/pl.png",
    "TURQUIA": "https://flagcdn.com/256x192/tr.png", "TURKEY": "https://flagcdn.com/256x192/tr.png", "TURKIYE": "https://flagcdn.com/256x192/tr.png",
    "SERBIA": "https://flagcdn.com/256x192/rs.png",
    "AUSTRIA": "https://flagcdn.com/256x192/at.png",
    "HUNGRIA": "https://flagcdn.com/256x192/hu.png", "HUNGARY": "https://flagcdn.com/256x192/hu.png",
    "IRLANDA": "https://flagcdn.com/256x192/ie.png", "IRELAND": "https://flagcdn.com/256x192/ie.png", "REPUBLICA DE IRLANDA": "https://flagcdn.com/256x192/ie.png",
    "NORUEGA": "https://flagcdn.com/256x192/no.png", "NORWAY": "https://flagcdn.com/256x192/no.png",
    "GRECIA": "https://flagcdn.com/256x192/gr.png", "GREECE": "https://flagcdn.com/256x192/gr.png",
    "RUMANIA": "https://flagcdn.com/256x192/ro.png", "ROMANIA": "https://flagcdn.com/256x192/ro.png",
    "ESLOVAQUIA": "https://flagcdn.com/256x192/sk.png", "SLOVAKIA": "https://flagcdn.com/256x192/sk.png",
    "ESLOVENIA": "https://flagcdn.com/256x192/si.png", "SLOVENIA": "https://flagcdn.com/256x192/si.png",
    "ISLANDIA": "https://flagcdn.com/256x192/is.png", "ICELAND": "https://flagcdn.com/256x192/is.png",
    "FINLANDIA": "https://flagcdn.com/256x192/fi.png", "FINLAND": "https://flagcdn.com/256x192/fi.png",
    "ALBANIA": "https://flagcdn.com/256x192/al.png",
    "KAZAJISTAN": "https://flagcdn.com/256x192/kz.png", "KAZAKHSTAN": "https://flagcdn.com/256x192/kz.png",
    "MOLDAVIA": "https://flagcdn.com/256x192/md.png", "MOLDOVA": "https://flagcdn.com/256x192/md.png",
    "LETONIA": "https://flagcdn.com/256x192/lv.png", "LATVIA": "https://flagcdn.com/256x192/lv.png",
    "MONTENEGRO": "https://flagcdn.com/256x192/me.png",
    "CHIPRE": "https://flagcdn.com/256x192/cy.png", "CYPRUS": "https://flagcdn.com/256x192/cy.png",
    "ARMENIA": "https://flagcdn.com/256x192/am.png",
    "GEORGIA": "https://flagcdn.com/256x192/ge.png",
    "BULGARIA": "https://flagcdn.com/256x192/bg.png",
    "ESTONIA": "https://flagcdn.com/256x192/ee.png",
    "LITUANIA": "https://flagcdn.com/256x192/lt.png", "LITHUANIA": "https://flagcdn.com/256x192/lt.png",
    "LUXEMBURGO": "https://flagcdn.com/256x192/lu.png", "LUXEMBOURG": "https://flagcdn.com/256x192/lu.png",
    "BIELORRUSIA": "https://flagcdn.com/256x192/by.png", "BELARUS": "https://flagcdn.com/256x192/by.png",
    "SAN MARINO": "https://flagcdn.com/256x192/sm.png",
    "ISLAS FEROE": "https://flagcdn.com/256x192/fo.png", "FAROE ISLANDS": "https://flagcdn.com/256x192/fo.png",
    "BOSNIA": "https://flagcdn.com/256x192/ba.png", "BOSNIA Y HERZEGOVINA": "https://flagcdn.com/256x192/ba.png",
    "MACEDONIA DEL NORTE": "https://flagcdn.com/256x192/mk.png", "NORTH MACEDONIA": "https://flagcdn.com/256x192/mk.png",
    "KOSOVO": "https://flagcdn.com/256x192/xk.png",
    "MALTA": "https://flagcdn.com/256x192/mt.png",
    "GIBRALTAR": "https://flagcdn.com/256x192/gi.png",
    "ANDORRA": "https://flagcdn.com/256x192/ad.png",
    "LIECHTENSTEIN": "https://flagcdn.com/256x192/li.png",
    "AZERBAIYAN": "https://flagcdn.com/256x192/az.png", "AZERBAIJAN": "https://flagcdn.com/256x192/az.png",
    "ISRAEL": "https://flagcdn.com/256x192/il.png",
    "UCRANIA": "https://flagcdn.com/256x192/ua.png", "UKRAINE": "https://flagcdn.com/256x192/ua.png",

    # America del Sur
    "ARGENTINA": "https://flagcdn.com/256x192/ar.png",
    "BRASIL": "https://flagcdn.com/256x192/br.png", "BRAZIL": "https://flagcdn.com/256x192/br.png",
    "COLOMBIA": "https://flagcdn.com/256x192/co.png",
    "URUGUAY": "https://flagcdn.com/256x192/uy.png",
    "CHILE": "https://flagcdn.com/256x192/cl.png",
    "ECUADOR": "https://flagcdn.com/256x192/ec.png",
    "PERU": "https://flagcdn.com/256x192/pe.png",
    "PARAGUAY": "https://flagcdn.com/256x192/py.png",
    "VENEZUELA": "https://flagcdn.com/256x192/ve.png",
    "BOLIVIA": "https://flagcdn.com/256x192/bo.png",

    # Concacaf y Caribe
    "ESTADOS UNIDOS": "https://flagcdn.com/256x192/us.png", "USA": "https://flagcdn.com/256x192/us.png",
    "EEUU": "https://flagcdn.com/256x192/us.png", "EE UU": "https://flagcdn.com/256x192/us.png", "UNITED STATES": "https://flagcdn.com/256x192/us.png",
    "MEXICO": "https://flagcdn.com/256x192/mx.png",
    "CANADA": "https://flagcdn.com/256x192/ca.png",
    "COSTA RICA": "https://flagcdn.com/256x192/cr.png",
    "PANAMA": "https://flagcdn.com/256x192/pa.png",
    "HONDURAS": "https://flagcdn.com/256x192/hn.png",
    "EL SALVADOR": "https://flagcdn.com/256x192/sv.png",
    "GUATEMALA": "https://flagcdn.com/256x192/gt.png",
    "JAMAICA": "https://flagcdn.com/256x192/jm.png",
    "HAITI": "https://flagcdn.com/256x192/ht.png",
    "REPUBLICA DOMINICANA": "https://flagcdn.com/256x192/do.png", "DOMINICAN REPUBLIC": "https://flagcdn.com/256x192/do.png",
    "TRINIDAD Y TOBAGO": "https://flagcdn.com/256x192/tt.png", "TRINIDAD AND TOBAGO": "https://flagcdn.com/256x192/tt.png",
    "CURAZAO": "https://flagcdn.com/256x192/cw.png", "CURACAO": "https://flagcdn.com/256x192/cw.png",
    "PUERTO RICO": "https://flagcdn.com/256x192/pr.png",
    "SURINAM": "https://flagcdn.com/256x192/sr.png", "SURINAME": "https://flagcdn.com/256x192/sr.png",
    "NICARAGUA": "https://flagcdn.com/256x192/ni.png",
    "CUBA": "https://flagcdn.com/256x192/cu.png",
    "BERMUDA": "https://flagcdn.com/256x192/bm.png",
    "GUYANA": "https://flagcdn.com/256x192/gy.png",
    "BELICE": "https://flagcdn.com/256x192/bz.png", "BELIZE": "https://flagcdn.com/256x192/bz.png",

    # Asia / Africa / Oceania
    "JAPON": "https://flagcdn.com/256x192/jp.png", "JAPAN": "https://flagcdn.com/256x192/jp.png",
    "COREA DEL SUR": "https://flagcdn.com/256x192/kr.png", "SOUTH KOREA": "https://flagcdn.com/256x192/kr.png",
    "COREA DEL NORTE": "https://flagcdn.com/256x192/kp.png", "NORTH KOREA": "https://flagcdn.com/256x192/kp.png",
    "CHINA": "https://flagcdn.com/256x192/cn.png",
    "AUSTRALIA": "https://flagcdn.com/256x192/au.png",
    "NUEVA ZELANDA": "https://flagcdn.com/256x192/nz.png", "NEW ZEALAND": "https://flagcdn.com/256x192/nz.png",
    "ARABIA SAUDITA": "https://flagcdn.com/256x192/sa.png", "ARABIA SAUDI": "https://flagcdn.com/256x192/sa.png", "SAUDI ARABIA": "https://flagcdn.com/256x192/sa.png",
    "QATAR": "https://flagcdn.com/256x192/qa.png", "CATAR": "https://flagcdn.com/256x192/qa.png",
    "IRAN": "https://flagcdn.com/256x192/ir.png",
    "IRAK": "https://flagcdn.com/256x192/iq.png", "IRAQ": "https://flagcdn.com/256x192/iq.png",
    "EMIRATOS ARABES UNIDOS": "https://flagcdn.com/256x192/ae.png", "EAU": "https://flagcdn.com/256x192/ae.png", "UAE": "https://flagcdn.com/256x192/ae.png",
    "TAILANDIA": "https://flagcdn.com/256x192/th.png", "THAILAND": "https://flagcdn.com/256x192/th.png",
    "VIETNAM": "https://flagcdn.com/256x192/vn.png",
    "INDONESIA": "https://flagcdn.com/256x192/id.png",
    "MALASIA": "https://flagcdn.com/256x192/my.png", "MALAYSIA": "https://flagcdn.com/256x192/my.png",
    "FILIPINAS": "https://flagcdn.com/256x192/ph.png", "PHILIPPINES": "https://flagcdn.com/256x192/ph.png",
    "INDIA": "https://flagcdn.com/256x192/in.png",
    "PAKISTAN": "https://flagcdn.com/256x192/pk.png",
    "UZBEKISTAN": "https://flagcdn.com/256x192/uz.png",
    "MARRUECOS": "https://flagcdn.com/256x192/ma.png", "MOROCCO": "https://flagcdn.com/256x192/ma.png",
    "EGIPTO": "https://flagcdn.com/256x192/eg.png", "EGYPT": "https://flagcdn.com/256x192/eg.png",
    "SENEGAL": "https://flagcdn.com/256x192/sn.png",
    "ARGELIA": "https://flagcdn.com/256x192/dz.png", "ALGERIA": "https://flagcdn.com/256x192/dz.png",
    "NIGERIA": "https://flagcdn.com/256x192/ng.png",
    "CAMERUN": "https://flagcdn.com/256x192/cm.png", "CAMEROON": "https://flagcdn.com/256x192/cm.png",
    "SUDAFRICA": "https://flagcdn.com/256x192/za.png", "SOUTH AFRICA": "https://flagcdn.com/256x192/za.png",
    "GHANA": "https://flagcdn.com/256x192/gh.png",
    "COSTA DE MARFIL": "https://flagcdn.com/256x192/ci.png", "IVORY COAST": "https://flagcdn.com/256x192/ci.png",
    "TUNEZ": "https://flagcdn.com/256x192/tn.png", "TUNISIA": "https://flagcdn.com/256x192/tn.png",
    "BURKINA FASO": "https://flagcdn.com/256x192/bf.png",
    "MALI": "https://flagcdn.com/256x192/ml.png",
    "CONGO": "https://flagcdn.com/256x192/cg.png",
    "RD CONGO": "https://flagcdn.com/256x192/cd.png",
    "GUINEA": "https://flagcdn.com/256x192/gn.png",
    "GUINEA ECUATORIAL": "https://flagcdn.com/256x192/gq.png",
    "GABON": "https://flagcdn.com/256x192/ga.png",
    "ANGOLA": "https://flagcdn.com/256x192/ao.png",
    "ZAMBIA": "https://flagcdn.com/256x192/zm.png",
    "KENIA": "https://flagcdn.com/256x192/ke.png", "KENYA": "https://flagcdn.com/256x192/ke.png",
    "UGANDA": "https://flagcdn.com/256x192/ug.png",
    "TANZANIA": "https://flagcdn.com/256x192/tz.png",
    "ETIOPIA": "https://flagcdn.com/256x192/et.png", "ETHIOPIA": "https://flagcdn.com/256x192/et.png",
    "CABO VERDE": "https://flagcdn.com/256x192/cv.png", "CAPE VERDE": "https://flagcdn.com/256x192/cv.png",
}

BANDERAS_PAISES = {k: envolver_cdn_proxy(v) for k, v in BANDERAS_PAISES_RAW.items() if envolver_cdn_proxy(v)}

def _normalizar(texto: str) -> str:
    """Normaliza texto eliminando acentos y caracteres especiales."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    sin_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    limpio = re.sub(r"[^A-Za-z0-9\s]", " ", sin_acento).upper().strip()
    return re.sub(r"\s+", " ", limpio)

def _cargar_cache() -> dict[str, str]:
    if ARCHIVO_CACHE_LOGOS.exists():
        try:
            return json.loads(ARCHIVO_CACHE_LOGOS.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _guardar_cache(cache: dict[str, str]) -> None:
    if not cache:
        return
    existente = _cargar_cache()
    existente.update(cache)
    tmp_path = ARCHIVO_CACHE_LOGOS.with_suffix(".tmp")
    try:
        tmp_path.write_text(json.dumps(existente, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(ARCHIVO_CACHE_LOGOS)
    except Exception as e:
        log.warning("No se pudo guardar la cache de logos: %s", e)
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

def guardar_cache_logos() -> None:
    pass

def resolver_logo_torneo(torneo: str, categoria: str, permitir_red: bool = False) -> str:
    """Resuelve el logo oficial del torneo con precedencia estricta por palabras completas."""
    if not torneo and not categoria:
        return ""

    torneo_norm = _normalizar(torneo)
    cache = _cargar_cache()
    clave_cache = f"torneo_{torneo_norm}"

    # 0. Catálogo maestro oficial de torneos verificados en disco
    catalogo = _obtener_catalogo_torneos()
    if catalogo:
        claves_ordenadas = sorted(catalogo.keys(), key=len, reverse=True)
        for clave in claves_ordenadas:
            patron = r"\b" + re.escape(clave) + r"\b"
            if re.search(patron, torneo_norm) or (clave == torneo_norm):
                url = catalogo[clave]
                cache[clave_cache] = url
                _guardar_cache(cache)
                return url

    # 1. Catálogo maestro de insignias oficiales (Precedencia Máxima por longitud de clave)
    claves_ordenadas = sorted(CIRCUITO_LOGOS.keys(), key=len, reverse=True)
    for clave in claves_ordenadas:
        patron = r"\b" + re.escape(clave) + r"\b"
        if re.search(patron, torneo_norm):
            url = CIRCUITO_LOGOS[clave]
            cache[clave_cache] = url
            _guardar_cache(cache)
            return url

    # 2. Caché previo en disco
    if clave_cache in cache and cache[clave_cache] and not es_logo_basura(cache[clave_cache]):
        return cache[clave_cache]

    # 3. Consulta de red a TheSportsDB
    if permitir_red and THESPORTSDB_KEY:
        try:
            tsdb_url = f"https://www.thesportsdb.com/api/v1/json/{THESPORTSDB_KEY}/search_all_leagues.php?c={urllib.parse.quote(torneo)}"
            data = _http_get_json(tsdb_url, timeout=3)
            if data and data.get("countries"):
                leagues = data["countries"]
                if leagues:
                    badge = leagues[0].get("strBadge") or leagues[0].get("strLogo")
                    if badge and not es_logo_basura(badge):
                        cdn_url = envolver_cdn_proxy(badge)
                        cache[clave_cache] = cdn_url
                        _guardar_cache(cache)
                        return cdn_url
        except Exception:
            pass

    # 4. Fallback por categoria solo si está explícitamente listado
    cat_limpia = categoria.strip()
    if cat_limpia in FALLBACK_POR_CATEGORIA:
        url_fb = FALLBACK_POR_CATEGORIA[cat_limpia]
        if url_fb:
            return url_fb

    return ""

def resolver_logo_equipo(equipo: str, deporte: str = "Fútbol", torneo: str = "", permitir_red: bool = True) -> str:
    """
    Resuelve el escudo/logo o bandera de un equipo.
    Regla de Oro: En selecciones/países siempre se inyecta la bandera oficial en HD.
    """
    if not equipo:
        return ""

    equipo_limpio = equipo.strip()
    equipo_norm = _normalizar(equipo_limpio)
    cache = _cargar_cache()
    clave_cache = f"equipo_{equipo_norm}"

    # 0. Banderas oficiales: Búsqueda exacta y por prefijo de país reconocido
    if equipo_norm in BANDERAS_PAISES:
        flag_url = BANDERAS_PAISES[equipo_norm]
        _guardar_catalogo_equipos({clave_cache: flag_url})
        return flag_url

    # Soporte para variantes como "España Sub 21", "Selección Colombia", etc.
    for pais_k, flag_u in BANDERAS_PAISES.items():
        if len(pais_k) >= 4 and (equipo_norm.startswith(pais_k + " ") or equipo_norm.endswith(" " + pais_k)):
            _guardar_catalogo_equipos({clave_cache: flag_u})
            return flag_u

    # 1. Catálogo maestro permanente de equipos en disco (Prioridad Absoluta)
    cat_equipos = _obtener_catalogo_equipos()
    if clave_cache in cat_equipos and cat_equipos[clave_cache] and not es_logo_basura(cat_equipos[clave_cache]):
        return cat_equipos[clave_cache]

    # Limpieza de ranking universitario americano ("24 Harvard" -> "Harvard")
    equipo_sin_ranking = re.sub(r"^\d+\s+", "", equipo_limpio).strip()
    if equipo_sin_ranking != equipo_limpio:
        clave_sin_rank = f"equipo_{_normalizar(equipo_sin_ranking)}"
        if clave_sin_rank in cat_equipos and cat_equipos[clave_sin_rank] and not es_logo_basura(cat_equipos[clave_sin_rank]):
            cat_equipos[clave_cache] = cat_equipos[clave_sin_rank]
            _guardar_catalogo_equipos({clave_cache: cat_equipos[clave_sin_rank]})
            return cat_equipos[clave_cache]

    # 2. Caché previo en disco
    cache = _cargar_cache()
    if clave_cache in cache and cache[clave_cache] and not es_logo_basura(cache[clave_cache]):
        return cache[clave_cache]

    if not permitir_red:
        return ""

    time.sleep(0.35)

    # 1. Consulta a TheSportsDB
    try:
        tsdb_url = f"https://www.thesportsdb.com/api/v1/json/{THESPORTSDB_KEY}/searchteams.php?t={urllib.parse.quote(equipo_limpio)}"
        data = _http_get_json(tsdb_url, timeout=3)
        if data and data.get("teams"):
            teams = data["teams"]
            if teams:
                badge = teams[0].get("strBadge") or teams[0].get("strLogo")
                if badge and not es_logo_basura(badge):
                    cdn_url = envolver_cdn_proxy(badge)
                    cache[clave_cache] = cdn_url
                    _guardar_cache(cache)
                    return cdn_url
    except Exception:
        pass

    # 2. Consulta a Api-Football
    if API_SPORTS_KEY and deporte in ["Fútbol", "Futbol"]:
        try:
            api_url = f"https://v3.football.api-sports.io/teams?search={urllib.parse.quote(equipo_limpio)}"
            headers = {"x-apisports-key": API_SPORTS_KEY}
            data = _http_get_json(api_url, headers=headers, timeout=4)
            if data and data.get("response"):
                resp_teams = data["response"]
                if resp_teams:
                    logo_url = resp_teams[0].get("team", {}).get("logo")
                    if logo_url and not es_logo_basura(logo_url):
                        cdn_url = envolver_cdn_proxy(logo_url)
                        cache[clave_cache] = cdn_url
                        _guardar_cache(cache)
                        return cdn_url
        except Exception:
            pass

    return ""
