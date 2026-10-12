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
        "icons8.com", "ppv", "default_channel", "no_logo", "blank.png",
        "american-football/leagues/1.png", "image_not_available",
        "not_available", "not-available", "no-image", "no_image"
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
}

FALLBACK_POR_CATEGORIA_RAW: dict[str, str] = {
    "Tenis": "https://upload.wikimedia.org/wikipedia/commons/3/3f/ATP_Tour_logo.svg",
    "Padel": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Premier_Padel_logo.svg",
    "Pádel": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Premier_Padel_logo.svg",
    "Ciclismo": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/29/Union_Cycliste_Internationale_logo.svg/512px-Union_Cycliste_Internationale_logo.svg.png",
    "Snooker": "https://upload.wikimedia.org/wikipedia/en/thumb/6/64/World_Snooker_Tour_logo.svg/512px-World_Snooker_Tour_logo.svg.png",
    "Motor": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/33/F1.svg/512px-F1.svg.png",
    "Golf": "https://upload.wikimedia.org/wikipedia/en/thumb/c/cf/PGA_Tour_logo.svg/512px-PGA_Tour_logo.svg.png",
    "Combate": "",
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
# ==============================================================================
# CATALOGO UNIVERSAL ISO-3166-1 ALPHA-2 DE PAISES Y TERRITORIOS
# ==============================================================================
ISO_PAISES_RAW = {
    'af': ['AFGANISTAN', 'AFGHANISTAN'],
    'al': ['ALBANIA'],
    'dz': ['ARGELIA', 'ALGERIA'],
    'ad': ['ANDORRA'],
    'ao': ['ANGOLA'],
    'ag': ['ANTIGUA Y BARBUDA', 'ANTIGUA AND BARBUDA'],
    'ar': ['ARGENTINA'],
    'am': ['ARMENIA'],
    'au': ['AUSTRALIA'],
    'at': ['AUSTRIA'],
    'az': ['AZERBAIYAN', 'AZERBAIJAN'],
    'bs': ['BAHAMAS'],
    'bh': ['BAREIN', 'BAHRAIN'],
    'bd': ['BANGLADESH'],
    'bb': ['BARBADOS'],
    'by': ['BIELORRUSIA', 'BELARUS'],
    'be': ['BELGICA', 'BELGIUM'],
    'bz': ['BELICE', 'BELIZE'],
    'bj': ['BENIN'],
    'bt': ['BUTAN', 'BHUTAN'],
    'bo': ['BOLIVIA'],
    'ba': ['BOSNIA Y HERZEGOVINA', 'BOSNIA AND HERZEGOVINA', 'BOSNIA'],
    'bw': ['BOTSUANA', 'BOTSWANA'],
    'br': ['BRASIL', 'BRAZIL'],
    'bn': ['BRUNEI'],
    'bg': ['BULGARIA'],
    'bf': ['BURKINA FASO'],
    'bi': ['BURUNDI'],
    'cv': ['CABO VERDE', 'CAPE VERDE'],
    'kh': ['CAMBOYA', 'CAMBODIA'],
    'cm': ['CAMERUN', 'CAMEROON'],
    'ca': ['CANADA'],
    'cf': ['REPUBLICA CENTROAFRICANA', 'CENTRAL AFRICAN REPUBLIC'],
    'td': ['CHAD'],
    'cl': ['CHILE'],
    'cn': ['CHINA'],
    'co': ['COLOMBIA'],
    'km': ['COMORAS', 'COMOROS'],
    'cg': ['CONGO', 'REPUBLICA DEL CONGO'],
    'cd': ['RD CONGO', 'REPUBLICA DEMOCRATICA DEL CONGO', 'DR CONGO'],
    'cr': ['COSTA RICA'],
    'ci': ['COSTA DE MARFIL', 'IVORY COAST', 'COTE D IVOIRE'],
    'hr': ['CROACIA', 'CROATIA'],
    'cu': ['CUBA'],
    'cy': ['CHIPRE', 'CYPRUS'],
    'cz': ['CHEQUIA', 'REPUBLICA CHECA', 'CZECHIA', 'CZECH REPUBLIC'],
    'dk': ['DINAMARCA', 'DENMARK'],
    'dj': ['YIBUTI', 'DJIBOUTI'],
    'dm': ['DOMINICA'],
    'do': ['REPUBLICA DOMINICANA', 'DOMINICAN REPUBLIC'],
    'ec': ['ECUADOR'],
    'eg': ['EGIPTO', 'EGYPT'],
    'sv': ['EL SALVADOR'],
    'gq': ['GUINEA ECUATORIAL', 'EQUATORIAL GUINEA'],
    'er': ['ERITREA'],
    'ee': ['ESTONIA'],
    'sz': ['ESWATINI', 'SUAZILANDIA', 'SWAZILAND'],
    'et': ['ETIOPIA', 'ETHIOPIA'],
    'fj': ['FIYI', 'FIJI'],
    'fi': ['FINLANDIA', 'FINLAND'],
    'fr': ['FRANCIA', 'FRANCE'],
    'ga': ['GABON'],
    'gm': ['GAMBIA'],
    'ge': ['GEORGIA'],
    'de': ['ALEMANIA', 'GERMANY'],
    'gh': ['GHANA'],
    'gr': ['GRECIA', 'GREECE'],
    'gd': ['GRANADA', 'GRENADA'],
    'gt': ['GUATEMALA'],
    'gn': ['GUINEA'],
    'gw': ['GUINEA BISSAU', 'GUINEA-BISSAU'],
    'gy': ['GUYANA'],
    'ht': ['HAITI'],
    'hn': ['HONDURAS'],
    'hu': ['HUNGRIA', 'HUNGARY'],
    'is': ['ISLANDIA', 'ICELAND'],
    'in': ['INDIA'],
    'id': ['INDONESIA'],
    'ir': ['IRAN'],
    'iq': ['IRAK', 'IRAQ'],
    'ie': ['IRLANDA', 'IRELAND'],
    'il': ['ISRAEL'],
    'it': ['ITALIA', 'ITALY'],
    'jm': ['JAMAICA'],
    'jp': ['JAPON', 'JAPAN'],
    'jo': ['JORDANIA', 'JORDAN'],
    'kz': ['KAZAJISTAN', 'KAZAKHSTAN'],
    'ke': ['KENIA', 'KENYA'],
    'ki': ['KIRIBATI'],
    'kp': ['COREA DEL NORTE', 'NORTH KOREA'],
    'kr': ['COREA DEL SUR', 'SOUTH KOREA', 'COREA', 'KOREA'],
    'kw': ['KUWAIT'],
    'kg': ['KIRGUISTAN', 'KYRGYZSTAN'],
    'la': ['LAOS'],
    'lv': ['LETONIA', 'LATVIA'],
    'lb': ['LIBANO', 'LEBANON'],
    'ls': ['LESOTO', 'LESOTHO'],
    'lr': ['LIBERIA'],
    'ly': ['LIBIA', 'LIBYA'],
    'li': ['LIECHTENSTEIN'],
    'lt': ['LITUANIA', 'LITHUANIA'],
    'lu': ['LUXEMBURGO', 'LUXEMBOURG'],
    'mg': ['MADAGASCAR'],
    'mw': ['MALAWI'],
    'my': ['MALASIA', 'MALAYSIA'],
    'mv': ['MALDIVAS', 'MALDIVES'],
    'ml': ['MALI'],
    'mt': ['MALTA'],
    'mh': ['ISLAS MARSHALL', 'MARSHALL ISLANDS'],
    'mr': ['MAURITANIA'],
    'mu': ['MAURICIO', 'MAURITIUS'],
    'mx': ['MEXICO'],
    'fm': ['MICRONESIA'],
    'md': ['MOLDAVIA', 'MOLDOVA'],
    'mc': ['MONACO'],
    'mn': ['MONGOLIA'],
    'me': ['MONTENEGRO'],
    'ma': ['MARRUECOS', 'MOROCCO'],
    'mz': ['MOZAMBIQUE'],
    'mm': ['MYANMAR', 'BIRMANIA'],
    'na': ['NAMIBIA'],
    'nr': ['NAURU'],
    'np': ['NEPAL'],
    'nl': ['PAISES BAJOS', 'HOLANDA', 'NETHERLANDS'],
    'nz': ['NUEVA ZELANDA', 'NEW ZEALAND'],
    'ni': ['NICARAGUA'],
    'ne': ['NIGER'],
    'ng': ['NIGERIA'],
    'mk': ['MACEDONIA DEL NORTE', 'MACEDONIA', 'NORTH MACEDONIA'],
    'no': ['NORUEGA', 'NORWAY'],
    'om': ['OMAN'],
    'pk': ['PAKISTAN'],
    'pw': ['PALAOS', 'PALAU'],
    'ps': ['PALESTINA', 'PALESTINE'],
    'pa': ['PANAMA'],
    'pg': ['PAPUA NUEVA GUINEA', 'PAPUA NEW GUINEA'],
    'py': ['PARAGUAY'],
    'pe': ['PERU'],
    'ph': ['FILIPINAS', 'PHILIPPINES'],
    'pl': ['POLONIA', 'POLAND'],
    'pt': ['PORTUGAL'],
    'qa': ['QATAR', 'CATAR'],
    'ro': ['RUMANIA', 'ROMANIA'],
    'ru': ['RUSIA', 'RUSSIA'],
    'rw': ['RUANDA', 'RWANDA'],
    'kn': ['SAN CRISTOBAL Y NIEVES', 'SAINT KITTS AND NEVIS'],
    'lc': ['SANTA LUCIA', 'SAINT LUCIA'],
    'vc': ['SAN VICENTE Y LAS GRANADINAS', 'SAINT VINCENT AND THE GRENADINES'],
    'ws': ['SAMOA'],
    'sm': ['SAN MARINO'],
    'st': ['SANTO TOME Y PRINCIPE', 'SAO TOME AND PRINCIPE'],
    'sa': ['ARABIA SAUDITA', 'ARABIA SAUDI', 'SAUDI ARABIA'],
    'sn': ['SENEGAL'],
    'rs': ['SERBIA'],
    'sc': ['SEYCHELLES'],
    'sl': ['SIERRA LEONA', 'SIERRA LEONE'],
    'sg': ['SINGAPUR', 'SINGAPORE'],
    'sk': ['ESLOVAQUIA', 'SLOVAKIA'],
    'si': ['ESLOVENIA', 'SLOVENIA'],
    'sb': ['ISLAS SALOMON', 'SOLOMON ISLANDS'],
    'so': ['SOMALIA'],
    'za': ['SUDAFRICA', 'SOUTH AFRICA'],
    'ss': ['SUDAN DEL SUR', 'SOUTH SUDAN'],
    'es': ['ESPANA', 'SPAIN'],
    'lk': ['SRI LANKA'],
    'sd': ['SUDAN'],
    'sr': ['SURINAM', 'SURINAME'],
    'se': ['SUECIA', 'SWEDEN'],
    'ch': ['SUIZA', 'SWITZERLAND'],
    'sy': ['SIRIA', 'SYRIA'],
    'tj': ['TAYIKISTAN', 'TAJIKISTAN'],
    'tz': ['TANZANIA'],
    'th': ['TAILANDIA', 'THAILAND'],
    'tl': ['TIMOR ORIENTAL', 'TIMOR-LESTE'],
    'tg': ['TOGO'],
    'to': ['TONGA'],
    'tt': ['TRINIDAD Y TOBAGO', 'TRINIDAD AND TOBAGO'],
    'tn': ['TUNEZ', 'TUNISIA'],
    'tr': ['TURQUIA', 'TURKEY', 'TURKIYE'],
    'tm': ['TURKMENISTAN'],
    'tv': ['TUVALU'],
    'ug': ['UGANDA'],
    'ua': ['UCRANIA', 'UKRAINE'],
    'ae': ['EMIRATOS ARABES UNIDOS', 'EAU', 'UAE', 'UNITED ARAB EMIRATES'],
    'gb': ['REINO UNIDO', 'UNITED KINGDOM', 'GRAN BRETANA', 'GREAT BRITAIN'],
    'gb-eng': ['INGLATERRA', 'ENGLAND'],
    'gb-sct': ['ESCOCIA', 'SCOTLAND'],
    'gb-wls': ['GALES', 'WALES'],
    'gb-nir': ['IRLANDA DEL NORTE', 'NORTHERN IRELAND'],
    'us': ['ESTADOS UNIDOS', 'USA', 'EEUU', 'EE UU', 'UNITED STATES'],
    'uy': ['URUGUAY'],
    'uz': ['UZBEKISTAN'],
    'vu': ['VANUATU'],
    'va': ['VATICANO', 'VATICAN'],
    've': ['VENEZUELA'],
    'vn': ['VIETNAM'],
    'ye': ['YEMEN'],
    'zm': ['ZAMBIA'],
    'zw': ['ZIMBABWE'],
    'aw': ['ARUBA'],
    'bm': ['BERMUDA'],
    'ky': ['ISLAS CAIMAN', 'CAYMAN ISLANDS'],
    'cw': ['CURAZAO', 'CURACAO'],
    'fo': ['ISLAS FEROE', 'FAROE ISLANDS'],
    'gi': ['GIBRALTAR'],
    'gl': ['GROENLANDIA', 'GREENLAND'],
    'gu': ['GUAM'],
    'hk': ['HONG KONG'],
    'mo': ['MACAO', 'MACAU'],
    'pr': ['PUERTO RICO'],
    'pf': ['TAHITI', 'POLINESIA FRANCESA', 'FRENCH POLYNESIA'],
    'nc': ['NUEVA CALEDONIA', 'NEW CALEDONIA'],
    'as': ['SAMOA AMERICANA', 'AMERICAN SAMOA'],
    'ck': ['ISLAS COOK', 'COOK ISLANDS'],
    'tw': ['TAIWAN', 'CHINESE TAIPEI'],
    'vi': ['ISLAS VIRGENES', 'VIRGIN ISLANDS'],
}

BANDERAS_PAISES_RAW: dict[str, str] = {}
for code, aliases in ISO_PAISES_RAW.items():
    flag_u = f"https://flagcdn.com/256x192/{code}.png"
    for alias in aliases:
        BANDERAS_PAISES_RAW[alias] = flag_u

BANDERAS_PAISES = {k: envolver_cdn_proxy(v) for k, v in BANDERAS_PAISES_RAW.items() if envolver_cdn_proxy(v)}

# Paises ordenados por longitud de caracteres descendente para coincidencia sintactica
PAISES_ISO_ORDENADOS = sorted(
    BANDERAS_PAISES.items(),
    key=lambda item: len(item[0]),
    reverse=True
)

TOKENS_PREFIJO_SELECCION = {
    "SELECCION", "EQUIPO", "NATIONAL", "TEAM", "SELECCION DE", "EQUIPO NACIONAL", "NATIONAL TEAM"
}

TOKENS_CALIFICADOR_SELECCION = {
    "SUB", "U", "SUB15", "SUB16", "SUB17", "SUB18", "SUB19", "SUB20", "SUB21", "SUB22", "SUB23",
    "U15", "U16", "U17", "U18", "U19", "U20", "U21", "U22", "U23",
    "JUNIOR", "JUVENIL", "MAYOR", "SENIOR", "OLIMPICA", "OLIMPICO", "OLYMPIC",
    "FEMENINA", "FEMENINO", "FEMENIL", "FEM", "WOMEN", "WOMAN", "MASCULINA", "MASCULINO", "MEN", "MAN",
    "DE", "DEL"
}

def es_entidad_seleccion_nacional(equipo_norm: str) -> tuple[bool, str]:
    """
    Invarianza Gramatical Estricta (Universal):
    Determina si un nombre corresponde a una Selección Nacional.
    Fórmula: [PREFIJO]* + PAIS_ISO + [CALIFICADOR_EDAD_GENERO]*

    Regla Universal: Si contiene cualquier token adicional ajeno a la fórmula
    (ej. Montevideo, Coronado, Juniors, Real, City, etc.), la regla gramatical
    lo descarta matemáticamente sin necesidad de listas negras de clubes.
    """
    if not equipo_norm:
        return False, ""

    tokens = equipo_norm.split()
    if not tokens:
        return False, ""

    # 1. Coincidencia exacta con país ISO
    if equipo_norm in BANDERAS_PAISES:
        return True, BANDERAS_PAISES[equipo_norm]

    # 2. Análisis sintáctico con país compuesto o simple
    for pais_norm, flag_url in PAISES_ISO_ORDENADOS:
        pais_tokens = pais_norm.split()
        len_p = len(pais_tokens)

        encontrado_idx = -1
        for i in range(len(tokens) - len_p + 1):
            if tokens[i:i + len_p] == pais_tokens:
                encontrado_idx = i
                break

        if encontrado_idx >= 0:
            prefijo = tokens[:encontrado_idx]
            sufijo = tokens[encontrado_idx + len_p:]

            prefijo_valido = all(
                t in TOKENS_PREFIJO_SELECCION or t in {"DE", "DEL"}
                for t in prefijo
            )
            sufijo_valido = all(
                t in TOKENS_CALIFICADOR_SELECCION or t.isdigit()
                for t in sufijo
            )

            if prefijo_valido and sufijo_valido:
                return True, flag_url

    return False, ""

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


DISCRIMINADORES_TORNEO: set[str] = {
    # Confederaciones
    "AFC", "CAF", "CONCACAF", "OFC", "UEFA", "CONMEBOL",
    # Paises / Regiones
    "ENGLAND", "INGLATERRA", "SPAIN", "ESPANA", "ESPANOLA",
    "MEXICO", "MX", "COLOMBIA", "COLOMBIANA", "ARGENTINA", "ARGENTINO",
    "BRASIL", "BRAZIL", "BRASILEIRAO", "CHILE", "CHILENA", "PERU", "PERUANA",
    "ECUADOR", "ECUATORIANA", "URUGUAY", "URUGUAYA", "PARAGUAY", "PARAGUAYA",
    "VENEZUELA", "BOLIVIA", "PORTUGAL", "PORTUGUESA", "FRANCIA", "FRANCE",
    "ITALIA", "ITALY", "ITALIANA", "ALEMANIA", "GERMANY", "ALEMANA",
    "PAISES BAJOS", "NETHERLANDS", "HOLANDA", "BELGICA", "BELGIUM",
    "ESCOCIA", "SCOTLAND", "SCOTTISH", "RUSIA", "RUSSIAN", "RUSSIA",
    "CANADA", "CANADIAN", "EEUU", "USA", "ESTADOS UNIDOS", "AMERICAN",
    "SAUDI", "ARABIA", "QATAR", "JAPON", "JAPAN", "COREA", "KOREA",
    "CHINA", "AUSTRALIA", "AUSTRALIAN", "INDIA", "INDIAN", "TURQUIA", "TURKEY",
    "GRECIA", "GREECE", "POLONIA", "POLAND",
    # Marcas y sponsors exclusivos de ligas nacionales
    "BETPLAY", "BETANO", "DIMAYOR", "HYPERMOTION", "ENDESA", "ASOBAL", "EFL",
    # Genero y categorias formativas
    "FEMENINA", "FEMENIL", "WOMEN", "WOMENS", "FEM",
    "SUB 20", "SUB 17", "SUB 23", "U20", "U17", "U23",
    # Disciplinas deportivas especificas que comparten nombres genericos
    "PADEL", "FUTSAL", "PLAYA", "3X3"
}

SINONIMOS_DISCRIMINADORES: dict[str, str] = {
    "MEXICO": "MX", "MX": "MX",
    "COLOMBIA": "COLOMBIANA", "COLOMBIANA": "COLOMBIANA",
    "ARGENTINA": "ARGENTINO", "ARGENTINO": "ARGENTINO",
    "BRASIL": "BRAZIL", "BRAZIL": "BRAZIL",
    "FEMENINA": "FEM", "FEMENIL": "FEM", "WOMEN": "FEM", "WOMENS": "FEM", "FEM": "FEM",
    "ENGLAND": "INGLATERRA", "INGLATERRA": "INGLATERRA",
    "SPAIN": "ESPANA", "ESPANA": "ESPANA", "ESPANOLA": "ESPANA",
}

def _normalizar_mods_torneo(mods: set[str]) -> set[str]:
    return {SINONIMOS_DISCRIMINADORES.get(m, m) for m in mods}

def son_torneos_compatibles(torneo_norm: str, clave: str) -> bool:
    if clave == torneo_norm:
        return True
    mods_torneo = _normalizar_mods_torneo({d for d in DISCRIMINADORES_TORNEO if re.search(r"\b" + re.escape(d) + r"\b", torneo_norm)})
    mods_clave = _normalizar_mods_torneo({d for d in DISCRIMINADORES_TORNEO if re.search(r"\b" + re.escape(d) + r"\b", clave)})
    return mods_torneo == mods_clave

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
            if (clave == torneo_norm) or re.search(patron, torneo_norm):
                if not son_torneos_compatibles(torneo_norm, clave):
                    continue
                url = catalogo[clave]
                cache[clave_cache] = url
                _guardar_cache(cache)
                return url

    # 1. Catálogo maestro de insignias oficiales (Precedencia Máxima por longitud de clave)
    claves_ordenadas = sorted(CIRCUITO_LOGOS.keys(), key=len, reverse=True)
    for clave in claves_ordenadas:
        patron = r"\b" + re.escape(clave) + r"\b"
        if (clave == torneo_norm) or re.search(patron, torneo_norm):
            if not son_torneos_compatibles(torneo_norm, clave):
                continue
            url = CIRCUITO_LOGOS[clave]
            cache[clave_cache] = url
            _guardar_cache(cache)
            return url

    # 2. Caché previo en disco con invalidación de falsos positivos
    if clave_cache in cache and cache[clave_cache] and not es_logo_basura(cache[clave_cache]):
        cached_url = cache[clave_cache]
        # Invalida UEFA Champions League si el torneo pertenece a otra confederación
        if "uefa_champions_league" in cached_url.lower() and any(k in torneo_norm for k in ["AFC", "CAF", "CONCACAF", "OFC", "EHF"]):
            pass
        else:
            return cached_url

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

def resolver_logo_equipo(equipo: str, deporte: str = "Fútbol", torneo: str = "", permitir_red: bool = True, es_seleccion: Optional[bool] = None) -> str:
    """
    Resuelve el escudo/logo o bandera de un equipo.
    Principio Universal:
    - Si es selección nacional (validado gramaticalmente o por bandera es_seleccion), asigna bandera oficial HD.
    - Si contiene elementos de club o es_seleccion=False, busca escudo oficial de club en catálogos y APIs.
    """
    if not equipo:
        return ""

    equipo_limpio = equipo.strip()
    equipo_norm = _normalizar(equipo_limpio)
    clave_cache = f"equipo_{equipo_norm}"

    # 0. Resolución Universal de Selecciones Nacionales (Invarianza Gramatical)
    if es_seleccion is not False:
        es_nat, flag_url = es_entidad_seleccion_nacional(equipo_norm)
        if es_nat:
            # Si el torneo es explícitamente una liga doméstica de clubes de primer nivel, verificar si no es selección forzada
            torneo_norm = _normalizar(torneo)
            es_liga_domestica = any(l in torneo_norm for l in [
                "SEGUNDA", "PRIMERA DIVISION", "PREMIER LEAGUE", "LALIGA", "SERIE A", "BUNDESLIGA",
                "LIGUE 1", "BETPLAY", "LIGA MX", "BRASILEIRAO"
            ])
            if es_seleccion is True or not es_liga_domestica:
                return flag_url

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
