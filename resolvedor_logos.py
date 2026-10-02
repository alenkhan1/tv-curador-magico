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

# Cargar .env si existe en el directorio del script
_env_file = Path(__file__).resolve().parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        if "=" in _line and not _line.strip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ[_k.strip()] = _v.strip()

ARCHIVO_CACHE_LOGOS = Path(os.environ.get("ARCHIVO_CACHE_LOGOS", "logos_cache.json"))
ARCHIVO_CATALOGO_TORNEOS = Path(__file__).resolve().parent / "catalogo_maestro_torneos.json"
_CATALOGO_TORNEOS_CACHE: dict[str, str] | None = None

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
    """Envuelve URLs externas en wsrv.nl para evitar bloqueos HTTP 403 por User-Agent en Android TV."""
    if not url or es_logo_basura(url):
        return ""
    if url.startswith("https://wsrv.nl/?url="):
        return url
    url_limpia = url.strip()
    return f"https://wsrv.nl/?url={urllib.parse.quote(url_limpia, safe='')}&w=400&output=webp"

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

# Banderas oficiales directas para todas las selecciones del mundo en FlagCDN HD
BANDERAS_PAISES_RAW: dict[str, str] = {
    # Europa
    "ESPANA": "https://flagcdn.com/w320/es.png",
    "FRANCIA": "https://flagcdn.com/w320/fr.png",
    "ITALIA": "https://flagcdn.com/w320/it.png",
    "ALEMANIA": "https://flagcdn.com/w320/de.png",
    "INGLATERRA": "https://flagcdn.com/w320/gb-eng.png",
    "PORTUGAL": "https://flagcdn.com/w320/pt.png",
    "BELGICA": "https://flagcdn.com/w320/be.png",
    "PAISES BAJOS": "https://flagcdn.com/w320/nl.png",
    "HOLANDA": "https://flagcdn.com/w320/nl.png",
    "CROACIA": "https://flagcdn.com/w320/hr.png",
    "SUIZA": "https://flagcdn.com/w320/ch.png",
    "DINAMARCA": "https://flagcdn.com/w320/dk.png",
    "SUECIA": "https://flagcdn.com/w320/se.png",
    "POLONIA": "https://flagcdn.com/w320/pl.png",
    "TURQUIA": "https://flagcdn.com/w320/tr.png",
    "SERBIA": "https://flagcdn.com/w320/rs.png",
    "AUSTRIA": "https://flagcdn.com/w320/at.png",
    "HUNGRIA": "https://flagcdn.com/w320/hu.png",
    "ESCOCIA": "https://flagcdn.com/w320/gb-sct.png",
    "GALES": "https://flagcdn.com/w320/gb-wls.png",
    "REPUBLICA DE IRLANDA": "https://flagcdn.com/w320/ie.png",
    "CURACAO": "https://flagcdn.com/w320/cw.png",
    "DOMINICAN REPUBLIC": "https://flagcdn.com/w320/do.png",
    "TRINIDAD AND TOBAGO": "https://flagcdn.com/w320/tt.png",
    "IRLANDA": "https://flagcdn.com/w320/ie.png",
    "IRLANDA DEL NORTE": "https://flagcdn.com/w320/gb-nir.png",
    "NORUEGA": "https://flagcdn.com/w320/no.png",
    "GRECIA": "https://flagcdn.com/w320/gr.png",
    "RUMANIA": "https://flagcdn.com/w320/ro.png",
    "ESLOVAQUIA": "https://flagcdn.com/w320/sk.png",
    "ESLOVENIA": "https://flagcdn.com/w320/si.png",
    "ISLANDIA": "https://flagcdn.com/w320/is.png",
    "FINLANDIA": "https://flagcdn.com/w320/fi.png",
    "ALBANIA": "https://flagcdn.com/w320/al.png",
    "KAZAJISTAN": "https://flagcdn.com/w320/kz.png",
    "MOLDAVIA": "https://flagcdn.com/w320/md.png",
    "LETONIA": "https://flagcdn.com/w320/lv.png",
    "MONTENEGRO": "https://flagcdn.com/w320/me.png",
    "CHIPRE": "https://flagcdn.com/w320/cy.png",
    "ARMENIA": "https://flagcdn.com/w320/am.png",
    "GEORGIA": "https://flagcdn.com/w320/ge.png",
    "BULGARIA": "https://flagcdn.com/w320/bg.png",
    "ESTONIA": "https://flagcdn.com/w320/ee.png",
    "LITUANIA": "https://flagcdn.com/w320/lt.png",
    "LUXEMBURGO": "https://flagcdn.com/w320/lu.png",
    "BIELORRUSIA": "https://flagcdn.com/w320/by.png",
    "SAN MARINO": "https://flagcdn.com/w320/sm.png",
    "ISLAS FEROE": "https://flagcdn.com/w320/fo.png",
    "BOSNIA": "https://flagcdn.com/w320/ba.png",
    "BOSNIA Y HERZEGOVINA": "https://flagcdn.com/w320/ba.png",
    "MACEDONIA DEL NORTE": "https://flagcdn.com/w320/mk.png",
    "MACEDONIA NORTE": "https://flagcdn.com/w320/mk.png",
    "KOSOVO": "https://flagcdn.com/w320/xk.png",
    "MALTA": "https://flagcdn.com/w320/mt.png",
    "GIBRALTAR": "https://flagcdn.com/w320/gi.png",
    "ANDORRA": "https://flagcdn.com/w320/ad.png",
    "LIECHTENSTEIN": "https://flagcdn.com/w320/li.png",
    "AZERBAIYAN": "https://flagcdn.com/w320/az.png",
    "ISRAEL": "https://flagcdn.com/w320/il.png",
    "UCRANIA": "https://flagcdn.com/w320/ua.png",

    # América del Sur
    "ARGENTINA": "https://flagcdn.com/w320/ar.png",
    "BRASIL": "https://flagcdn.com/w320/br.png",
    "COLOMBIA": "https://flagcdn.com/w320/co.png",
    "URUGUAY": "https://flagcdn.com/w320/uy.png",
    "CHILE": "https://flagcdn.com/w320/cl.png",
    "ECUADOR": "https://flagcdn.com/w320/ec.png",
    "PERU": "https://flagcdn.com/w320/pe.png",
    "PARAGUAY": "https://flagcdn.com/w320/py.png",
    "VENEZUELA": "https://flagcdn.com/w320/ve.png",
    "BOLIVIA": "https://flagcdn.com/w320/bo.png",

    # Concacaf y Caribe
    "ESTADOS UNIDOS": "https://flagcdn.com/w320/us.png",
    "USA": "https://flagcdn.com/w320/us.png",
    "MEXICO": "https://flagcdn.com/w320/mx.png",
    "CANADA": "https://flagcdn.com/w320/ca.png",
    "COSTA RICA": "https://flagcdn.com/w320/cr.png",
    "PANAMA": "https://flagcdn.com/w320/pa.png",
    "HONDURAS": "https://flagcdn.com/w320/hn.png",
    "EL SALVADOR": "https://flagcdn.com/w320/sv.png",
    "GUATEMALA": "https://flagcdn.com/w320/gt.png",
    "JAMAICA": "https://flagcdn.com/w320/jm.png",
    "HAITI": "https://flagcdn.com/w320/ht.png",
    "REPUBLICA DOMINICANA": "https://flagcdn.com/w320/do.png",
    "TRINIDAD Y TOBAGO": "https://flagcdn.com/w320/tt.png",
    "CURAZAO": "https://flagcdn.com/w320/cw.png",
    "ISLAS CAIMAN": "https://flagcdn.com/w320/ky.png",
    "PUERTO RICO": "https://flagcdn.com/w320/pr.png",
    "DOMINICA": "https://flagcdn.com/w320/dm.png",
    "GUYANA": "https://flagcdn.com/w320/gy.png",
    "SURINAM": "https://flagcdn.com/w320/sr.png",
    "NICARAGUA": "https://flagcdn.com/w320/ni.png",
    "CUBA": "https://flagcdn.com/w320/cu.png",
    "BERMUDA": "https://flagcdn.com/w320/bm.png",
    "MONTSERRAT": "https://flagcdn.com/w320/ms.png",
    "ISLAS VIRGENES BRITANICAS": "https://flagcdn.com/w320/vg.png",
    "SAINT MARTIN": "https://flagcdn.com/w320/mf.png",
    "ANGUILA": "https://flagcdn.com/w320/ai.png",
    "ANTIGUA Y BARBUDA": "https://flagcdn.com/w320/ag.png",

    # Asia / África / Oceanía
    "JAPON": "https://flagcdn.com/w320/jp.png",
    "COREA DEL SUR": "https://flagcdn.com/w320/kr.png",
    "CHINA": "https://flagcdn.com/w320/cn.png",
    "AUSTRALIA": "https://flagcdn.com/w320/au.png",
    "TAILANDIA": "https://flagcdn.com/w320/th.png",
    "VIETNAM": "https://flagcdn.com/w320/vn.png",
    "FILIPINAS": "https://flagcdn.com/w320/ph.png",
    "PAKISTAN": "https://flagcdn.com/w320/pk.png",
    "MARRUECOS": "https://flagcdn.com/w320/ma.png",
    "EGIPTO": "https://flagcdn.com/w320/eg.png",
    "SENEGAL": "https://flagcdn.com/w320/sn.png",
    "ARGELIA": "https://flagcdn.com/w320/dz.png",
    "NIGERIA": "https://flagcdn.com/w320/ng.png",
    "CAMERUN": "https://flagcdn.com/w320/cm.png",
    "SUDAFRICA": "https://flagcdn.com/w320/za.png",
    "GHANA": "https://flagcdn.com/w320/gh.png",
    "COSTA DE MARFIL": "https://flagcdn.com/w320/ci.png",
    "NUEVA ZELANDA": "https://flagcdn.com/w320/nz.png",
}

BANDERAS_PAISES = {k: envolver_cdn_proxy(v) for k, v in BANDERAS_PAISES_RAW.items() if envolver_cdn_proxy(v)}

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

    # 0. Banderas oficiales: Si es una seleccion nacional o país reconocido
    es_torneo_selecciones = any(k in _normalizar(torneo) for k in [
        "NATIONS LEAGUE", "COPA AMERICA", "EURO", "MUNDIAL", "ELIMINATORIAS", "QUALIFIER", "AMISTOSO", "ASEAN"
    ])

    if equipo_norm in BANDERAS_PAISES:
        flag_url = BANDERAS_PAISES[equipo_norm]
        cache[clave_cache] = flag_url
        _guardar_cache(cache)
        return flag_url

    if clave_cache in cache and cache[clave_cache] and not es_logo_basura(cache[clave_cache]):
        return cache[clave_cache]

    if not permitir_red:
        return ""

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
