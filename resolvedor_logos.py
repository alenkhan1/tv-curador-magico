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
    if url.startswith("https://flagcdn.com/") or "raw.githubusercontent.com/alenkhan1/tv-curador-magico" in url:
        return url
    if url.startswith("https://wsrv.nl/?url="):
        if "&trim=" not in url:
            return f"{url}&trim=10"
        return url
    url_limpia = url.strip()
    return f"https://wsrv.nl/?url={urllib.parse.quote(url_limpia, safe='')}&w=400&output=webp&trim=10"

REPO_ASSETS_BASE = "https://raw.githubusercontent.com/alenkhan1/tv-curador-magico/main/assets/logos/torneos"

CIRCUITO_LOGOS_RAW: dict[str, str] = {
    # Competiciones Mayores de Selecciones (Diferenciadas por Torneo, Nunca por Confederacion Generica)
    "UEFA NATIONS LEAGUE": f"{REPO_ASSETS_BASE}/uefa_nations_league.png",
    "NATIONS LEAGUE": f"{REPO_ASSETS_BASE}/uefa_nations_league.png",
    "UEFA EURO": f"{REPO_ASSETS_BASE}/uefa_euro.png",
    "EUROCOPA": f"{REPO_ASSETS_BASE}/uefa_euro.png",
    "COPA AMERICA": f"{REPO_ASSETS_BASE}/copa_america.png",
    "CONMEBOL COPA AMERICA": f"{REPO_ASSETS_BASE}/copa_america.png",
    "COPA LIBERTADORES": f"{REPO_ASSETS_BASE}/copa_libertadores.png",
    "LIBERTADORES": f"{REPO_ASSETS_BASE}/copa_libertadores.png",
    "COPA SUDAMERICANA": f"{REPO_ASSETS_BASE}/copa_sudamericana.png",
    "SUDAMERICANA": f"{REPO_ASSETS_BASE}/copa_sudamericana.png",
    "RECOPA SUDAMERICANA": f"{REPO_ASSETS_BASE}/recopa_sudamericana.png",
    "CONCACAF NATIONS LEAGUE": f"{REPO_ASSETS_BASE}/concacaf_nations_league.png",
    "COPA ORO": f"{REPO_ASSETS_BASE}/copa_oro.png",
    "COPA AFRICANA": f"{REPO_ASSETS_BASE}/copa_africana.png",

    # Clubes UEFA
    "UEFA CHAMPIONS LEAGUE": f"{REPO_ASSETS_BASE}/uefa_champions_league.png",
    "CHAMPIONS LEAGUE": f"{REPO_ASSETS_BASE}/uefa_champions_league.png",
    "UEFA YOUTH LEAGUE": f"{REPO_ASSETS_BASE}/uefa_champions_league.png",
    "UEFA EUROPA LEAGUE": f"{REPO_ASSETS_BASE}/uefa_europa_league.png",
    "EUROPA LEAGUE": f"{REPO_ASSETS_BASE}/uefa_europa_league.png",
    "CONFERENCE LEAGUE": f"{REPO_ASSETS_BASE}/uefa_conference_league.png",
    "UECL": f"{REPO_ASSETS_BASE}/uefa_conference_league.png",
    "CHAMPIONS LEAGUE FEMENINA": f"{REPO_ASSETS_BASE}/uefa_champions_league.png",

    # Balonmano
    "ASOBAL": f"{REPO_ASSETS_BASE}/liga_asobal.png",
    "LIGA ASOBAL": f"{REPO_ASSETS_BASE}/liga_asobal.png",
    "LIGA NEXUS ENERGIA ASOBAL": f"{REPO_ASSETS_BASE}/liga_asobal.png",
    "EHF CHAMPIONS LEAGUE": "https://upload.wikimedia.org/wikipedia/en/thumb/7/7b/EHF_Champions_League_logo.svg/512px-EHF_Champions_League_logo.svg.png",

    # Ligas Nacionales
    "LALIGA": f"{REPO_ASSETS_BASE}/laliga.png",
    "LA LIGA": f"{REPO_ASSETS_BASE}/laliga.png",
    "LALIGA HYPERMOTION": f"{REPO_ASSETS_BASE}/laliga_hypermotion.png",
    "LALIGA SMARTBANK": f"{REPO_ASSETS_BASE}/laliga_hypermotion.png",
    "COPA DEL REY": f"{REPO_ASSETS_BASE}/copa_del_rey.png",
    "SUPERCOPA DE ESPANA": f"{REPO_ASSETS_BASE}/supercopa_espana.png",
    "LIGA F": f"{REPO_ASSETS_BASE}/liga_f.png",
    "PREMIER LEAGUE": f"{REPO_ASSETS_BASE}/premier_league.png",
    "FA CUP": f"{REPO_ASSETS_BASE}/fa_cup.png",
    "CARABAO CUP": f"{REPO_ASSETS_BASE}/carabao_cup.png",
    "EFL CHAMPIONSHIP": f"{REPO_ASSETS_BASE}/efl_championship.png",
    "SERIE A": f"{REPO_ASSETS_BASE}/serie_a.png",
    "SERIE B": f"{REPO_ASSETS_BASE}/serie_b.png",
    "COPPA ITALIA": f"{REPO_ASSETS_BASE}/coppa_italia.png",
    "BUNDESLIGA": f"{REPO_ASSETS_BASE}/bundesliga.png",
    "2 BUNDESLIGA": f"{REPO_ASSETS_BASE}/bundesliga_2.png",
    "BUNDESLIGA 2": f"{REPO_ASSETS_BASE}/bundesliga_2.png",
    "DFB POKAL": f"{REPO_ASSETS_BASE}/dfb_pokal.png",
    "LIGUE 1": f"{REPO_ASSETS_BASE}/ligue_1.png",
    "PRIMEIRA LIGA": f"{REPO_ASSETS_BASE}/primeira_liga.png",
    "BRASILEIRAO": f"{REPO_ASSETS_BASE}/brasileirao.png",
    "SERIE A BRASIL": f"{REPO_ASSETS_BASE}/brasileirao.png",
    "LIGA BETPLAY": f"{REPO_ASSETS_BASE}/liga_betplay.png",
    "LIGA BETPLAY DIMAYOR": f"{REPO_ASSETS_BASE}/liga_betplay.png",
    "PRIMERA A": f"{REPO_ASSETS_BASE}/liga_betplay.png",
    "TORNEO BETPLAY": f"{REPO_ASSETS_BASE}/torneo_betplay.png",
    "TORNEO BETPLAY DIMAYOR": f"{REPO_ASSETS_BASE}/torneo_betplay.png",
    "COPA BETPLAY": f"{REPO_ASSETS_BASE}/copa_betplay.png",
    "COPA BETPLAY DIMAYOR": f"{REPO_ASSETS_BASE}/copa_betplay.png",
    "COPA COLOMBIA": f"{REPO_ASSETS_BASE}/copa_betplay.png",
    "LIGA PROFESIONAL ARGENTINA": f"{REPO_ASSETS_BASE}/torneo_betano.png",
    "LIGA PROFESIONAL": f"{REPO_ASSETS_BASE}/torneo_betano.png",
    "TORNEO BETANO": f"{REPO_ASSETS_BASE}/torneo_betano.png",
    "PRIMERA DIVISION ARGENTINA": f"{REPO_ASSETS_BASE}/torneo_betano.png",
    "PRIMERA NACIONAL": f"{REPO_ASSETS_BASE}/primera_nacional.png",
    "COPA ARGENTINA": f"{REPO_ASSETS_BASE}/copa_argentina.png",
    "MLS": f"{REPO_ASSETS_BASE}/mls.png",
    "MAJOR LEAGUE SOCCER": f"{REPO_ASSETS_BASE}/mls.png",
    "LIGA MX": f"{REPO_ASSETS_BASE}/liga_mx.png",
    "LIGA MX FEMENIL": f"{REPO_ASSETS_BASE}/liga_mx_femenil.png",
    "LIGA EXPANSION MX": f"{REPO_ASSETS_BASE}/liga_expansion_mx.png",

    # Tenis / Pádel
    "ATP": f"{REPO_ASSETS_BASE}/atp.png",
    "ROLEX SHANGHAI MASTERS": f"{REPO_ASSETS_BASE}/atp.png",
    "SHANGHAI MASTERS": f"{REPO_ASSETS_BASE}/atp.png",
    "WTA": f"{REPO_ASSETS_BASE}/wta.png",
    "JAPAN OPEN": f"{REPO_ASSETS_BASE}/atp.png",
    "CHINA OPEN": f"{REPO_ASSETS_BASE}/wta.png",
    "PREMIER PADEL": f"{REPO_ASSETS_BASE}/premier_padel.png",
    "PADEL": f"{REPO_ASSETS_BASE}/premier_padel.png",

    # Deportes Americanos y Globales
    "NBA": f"{REPO_ASSETS_BASE}/nba.png",
    "WNBA": f"{REPO_ASSETS_BASE}/wnba.png",
    "MLB": f"{REPO_ASSETS_BASE}/mlb.png",
    "NFL": f"{REPO_ASSETS_BASE}/nfl.png",
    "NHL": f"{REPO_ASSETS_BASE}/nhl.png",
    "NCAA": f"{REPO_ASSETS_BASE}/ncaa.png",
    "ACB": f"{REPO_ASSETS_BASE}/liga_endesa.png",
    "LIGA ENDESA": f"{REPO_ASSETS_BASE}/liga_endesa.png",
    "EUROLEAGUE": f"{REPO_ASSETS_BASE}/euroleague.png",
    "EUROLIGA": f"{REPO_ASSETS_BASE}/euroleague.png",

    # Motor
    "FORMULA 1": f"{REPO_ASSETS_BASE}/f1.png",
    "F1": f"{REPO_ASSETS_BASE}/f1.png",
    "NASCAR": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0f/NASCAR_logo.svg/512px-NASCAR_logo.svg.png",
    "INDYCAR": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b2/IndyCar_Series_logo.svg/512px-IndyCar_Series_logo.svg.png",
    "SUPERBIKE": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e0/WorldSBK_logo.svg/512px-WorldSBK_logo.svg.png",
    "MOTOGP": f"{REPO_ASSETS_BASE}/motogp.png",
    "WRC": "https://upload.wikimedia.org/wikipedia/commons/thumb/6/65/World_Rally_Championship_logo.svg/512px-World_Rally_Championship_logo.svg.png",

    # Ciclismo / Snooker / Combate / Golf
    "UCI": f"{REPO_ASSETS_BASE}/uci.png",
    "CAMPEONATO MUNDIAL DE CICLISMO": f"{REPO_ASSETS_BASE}/uci.png",
    "LA VUELTA": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/La_Vuelta_logo.svg/512px-La_Vuelta_logo.svg.png",
    "TOUR DE FRANCE": f"{REPO_ASSETS_BASE}/tour_de_france.png",
    "GIRO D ITALIA": "https://upload.wikimedia.org/wikipedia/en/thumb/8/82/Giro_d%27Italia_logo.svg/512px-Giro_d%27Italia_logo.svg.png",
    "WST": f"{REPO_ASSETS_BASE}/wst.png",
    "SNOOKER": f"{REPO_ASSETS_BASE}/wst.png",
    "SHENZHEN OPEN": f"{REPO_ASSETS_BASE}/wst.png",
    "UFC": f"{REPO_ASSETS_BASE}/ufc.png",
    "UFC FIGHT NIGHT": f"{REPO_ASSETS_BASE}/ufc.png",
    "PGA TOUR": f"{REPO_ASSETS_BASE}/pga_tour.png",
    "PGA": f"{REPO_ASSETS_BASE}/pga_tour.png",
}

FALLBACK_POR_CATEGORIA_RAW: dict[str, str] = {
    "Tenis": f"{REPO_ASSETS_BASE}/atp.png",
    "Padel": f"{REPO_ASSETS_BASE}/premier_padel.png",
    "Pádel": f"{REPO_ASSETS_BASE}/premier_padel.png",
    "Ciclismo": f"{REPO_ASSETS_BASE}/uci.png",
    "Snooker": f"{REPO_ASSETS_BASE}/wst.png",
    "Motor": f"{REPO_ASSETS_BASE}/f1.png",
    "Golf": f"{REPO_ASSETS_BASE}/pga_tour.png",
    "Combate": "",
    "Balonmano": f"{REPO_ASSETS_BASE}/liga_asobal.png",
    "Beisbol": f"{REPO_ASSETS_BASE}/mlb.png",
    "Béisbol": f"{REPO_ASSETS_BASE}/mlb.png",
    "Fútbol": "",
    "Futbol": "",
    "Baloncesto": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7a/Basketball.png/512px-Basketball.png",
    "Rugby": "https://upload.wikimedia.org/wikipedia/en/thumb/e/e0/World_Rugby_logo.svg/512px-World_Rugby_logo.svg.png",
    "Hockey": f"{REPO_ASSETS_BASE}/nhl.png",
    "Otros Deportes": "",
}

CIRCUITO_LOGOS = {k: envolver_cdn_proxy(v) for k, v in CIRCUITO_LOGOS_RAW.items() if envolver_cdn_proxy(v)}
FALLBACK_POR_CATEGORIA = {k: envolver_cdn_proxy(v) for k, v in FALLBACK_POR_CATEGORIA_RAW.items() if envolver_cdn_proxy(v)}

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
    'dj': ['YIBUTI', 'DJIBUTI'],
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
    """
    if not equipo_norm:
        return False, ""

    tokens = equipo_norm.split()
    if not tokens:
        return False, ""

    if equipo_norm in BANDERAS_PAISES:
        return True, BANDERAS_PAISES[equipo_norm]

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

# ==============================================================================
# DISAMBIGUACION POR ENTIDADES PARTICIPANTES (Universal y Sistemica)
# ==============================================================================
CLUBES_POR_PAIS: dict[str, set[str]] = {
    "BRASIL": {
        "CHAPECOENSE", "ATHLETICO PARANAENSE", "ATLETICO PARANAENSE", "PARANAENSE", "FLAMENGO", "PALMEIRAS",
        "CORINTHIANS", "SAO PAULO", "SANTOS", "GREMIO", "INTERNACIONAL", "FLUMINENSE", "VASCO DA GAMA",
        "VASCO", "BOTAFOGO", "ATLETICO MINEIRO", "CRUZEIRO", "CORITIBA", "BAHIA", "FORTALEZA", "CEARA",
        "GOIAS", "SPORT RECIFE", "VITORIA", "CUIABA", "JUVENTUDE", "CRICIUMA", "BRAGANTINO", "RED BULL BRAGANTINO",
        "AVAI", "PONTE PRETA", "VILA NOVA", "NOVORIZONTINO", "MIRASSOL", "OPERARIO", "PAYSANDU", "AMAZONAS", "CRB",
        "ITUANO", "BRUSQUE", "GUARANI", "BOTAFOGO SP", "TOMBENSE", "VOLTA REDONDA", "FERROVIARIA", "LONDRINA", "REMO"
    },
    "CHILE": {
        "COLO COLO", "UNIVERSIDAD DE CHILE", "U DE CHILE", "UNIVERSIDAD CATOLICA", "U CATOLICA", "COBRELOA",
        "UNION ESPANOLA", "PALESTINO", "EVERTON", "AUDAX ITALIANO", "O HIGGINS", "OHIGGINS", "HUACHIPATO",
        "COBRESAL", "COQUIMBO UNIDO", "COQUIMBO", "DEPORTES IQUIQUE", "IQUIQUE", "DEPORTES LA SERENA", "LA SERENA",
        "CURICO UNIDO", "CURICO", "SANTIAGO MORNING", "DEPORTES RECOLETA", "RECOLETA", "DEP RECOLETA", "MAGALLANES",
        "RANGERS DE TALCA", "RANGERS", "DEPORTES TEMUCO", "TEMUCO", "DEPORTES ANTOFAGASTA", "ANTOFAGASTA",
        "SAN MARCOS DE ARICA", "SAN MARCOS", "SAN LUIS DE QUILLOTA", "SAN LUIS", "DEPORTES SANTA CRUZ", "SANTA CRUZ",
        "UNIVERSIDAD DE CONCEPCION", "U DE CONCEPCION", "BARNECHEA", "LIMACHE", "DEPORTES LIMACHE", "UNION SAN FELIPE",
        "SAN FELIPE", "NUBLENSE"
    },
    "COLOMBIA": {
        "MILLONARIOS", "SANTA FE", "ATLETICO NACIONAL", "AMERICA DE CALI", "JUNIOR", "DEPORTIVO CALI",
        "INDEPENDIENTE MEDELLIN", "ONCE CALDAS", "DEPORTES TOLIMA", "LA EQUIDAD", "DEPORTIVO PASTO",
        "AGUILAS DORADAS", "BUCARAMANGA", "ENVIGADO", "JAGUARES", "DEPORTIVO PEREIRA", "ALIANZA PETROLERA",
        "ALIANZA FC", "BOYACA CHICO", "PATRIOTAS", "CUCUTA DEPORTIVO", "CUCUTA", "LLANEROS", "REAL SANTANDER",
        "REAL CARTAGENA", "HUILA", "ATLETICO HUILA", "QUINDIO", "DEPORTES QUINDIO", "BARRANQUILLA", "BOGOTA FC",
        "CORTULUA", "INTERNACIONAL DE PALMIRA", "ORSOMARSO", "LEONES", "TIGRES FC", "UNION MAGDALENA"
    },
    "ARGENTINA": {
        "BOCA JUNIORS", "BOCA", "RIVER PLATE", "RIVER", "RACING CLUB", "INDEPENDIENTE", "SAN LORENZO",
        "VELEZ SARSFIELD", "VELEZ", "ESTUDIANTES", "GIMNASIA", "NEWELLS", "ROSARIO CENTRAL", "TALLERES",
        "BELGRANO", "LANUS", "BANFIELD", "HURACAN", "ARGENTINOS JUNIORS", "DEFENSA Y JUSTICIA", "TIGRE",
        "GODOY CRUZ", "PLATENSE", "UNION", "COLON", "BARRACAS CENTRAL", "INSTITUTO", "SARMIENTO",
        "CENTRAL CORDOBA", "ATLETICO TUCUMAN", "FERRO CARRIL OESTE", "FERRO", "QUILMES", "CHACARITA",
        "SAN MARTIN DE TUCUMAN", "ALL BOYS", "ALMIRANTE BROWN", "DEPORTIVO MORON", "NUEVA CHICAGO"
    },
    "ITALIA": {
        "JUVENTUS", "INTER", "MILAN", "ROMA", "LAZIO", "NAPOLI", "FIORENTINA", "ATALANTA", "TORINO",
        "BOLOGNA", "MONZA", "GENOA", "UDINESE", "CAGLIARI", "HELLAS VERONA", "VERONA", "LECCE", "EMPOLI",
        "PARMA", "COMO", "VENEZIA", "SASSUOLO", "FROSINONE", "SALERNITANA", "SAMPDORIA", "PALERMO",
        "CREMONESE", "BARI", "SPEZIA", "BRESCIA", "PISA", "CESENA", "MODENA", "REGGIANA", "CATANZARO"
    },
    "ESPANA": {
        "REAL MADRID", "BARCELONA", "ATLETICO MADRID", "SEVILLA", "REAL BETIS", "BETIS", "REAL SOCIEDAD",
        "ATHLETIC CLUB", "ATHLETIC BILBAO", "VALENCIA", "VILLARREAL", "CELTA VIGO", "CELTA", "OSASUNA",
        "GETAFE", "RAYO VALLECANO", "MALLORCA", "GIRONA", "LAS PALMAS", "ALAVES", "ESPANYOL", "LEGANES",
        "REAL VALLADOLID", "VALLADOLID", "LEVANTE", "SPORTING GIJON", "REAL OVIEDO", "RACING SANTANDER",
        "REAL ZARAGOZA", "ZARAGOZA", "ELCHE", "EIBAR", "TENERIFE", "ALBACETE", "BURGOS", "CARTAGENA", "MIRANDES"
    },
    "MEXICO": {
        "AMERICA", "GUADALAJARA", "CHIVAS", "CRUZ AZUL", "PUMAS", "PUMAS UNAM", "TIGRES UANL", "TIGRES",
        "MONTERREY", "RAYADOS", "TOLUCA", "PACHUCA", "SANTOS LAGUNA", "LEON", "ATLAS", "PUEBLA",
        "TIJUANA", "XOLOS", "JUAREZ", "FC JUAREZ", "MAZATLAN", "NECAXA", "ATLETICO SAN LUIS", "QUERETARO"
    },
    "ECUADOR": {
        "LDU QUITO", "LIGA DE QUITO", "BARCELONA SC", "EMELEC", "INDEPENDIENTE DEL VALLE", "IDV", "AUCAS",
        "EL NACIONAL", "UNIVERSIDAD CATOLICA DEL ECUADOR", "DELFIN", "DEPORTIVO CUENCA", "MACARA", "ORENSE"
    },
    "URUGUAY": {
        "PENAROL", "NACIONAL DE MONTEVIDEO", "DEFENSOR SPORTING", "DANUBIO", "LIVERPOOL DE MONTEVIDEO",
        "MONTEVIDEO WANDERERS", "BOSTON RIVER", "CERRO LARGO", "RACING DE MONTEVIDEO", "PROGRESO"
    }
}

def deducir_pais_equipos(local: str, visitante: str) -> str:
    """Deduce el pais de los clubes participantes utilizando tokens normalizados exactos."""
    t_local = _normalizar(local) if local else ""
    t_vis = _normalizar(visitante) if visitante else ""
    if not t_local and not t_vis:
        return ""

    for pais, clubes in CLUBES_POR_PAIS.items():
        for c in clubes:
            patron = r"\b" + re.escape(c) + r"\b"
            if (t_local and re.search(patron, t_local)) or (t_vis and re.search(patron, t_vis)):
                return pais
    return ""

DISCRIMINADORES_PAISES: set[str] = {
    "BRASIL", "BRAZIL", "CHILE", "COLOMBIA", "ARGENTINA", "ITALIA", "ITALY",
    "ESPANA", "SPAIN", "MEXICO", "ECUADOR", "URUGUAY", "PERU", "INGLATERRA", "ENGLAND",
    "ALEMANIA", "GERMANY", "FRANCIA", "FRANCE"
}

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

def son_torneos_compatibles(torneo_norm: str, clave: str, pais_deducido: str = "") -> bool:
    if pais_deducido:
        mods_clave = _normalizar_mods_torneo({d for d in DISCRIMINADORES_TORNEO if re.search(r"\b" + re.escape(d) + r"\b", clave)})
        pais_norm = SINONIMOS_DISCRIMINADORES.get(pais_deducido, pais_deducido)
        if clave in ["SERIE A", "SERIE A TIM", "SERIE A ENILIVE", "SERIE B", "SERIE BKT"] and pais_deducido != "ITALIA":
            return False
        if clave in ["PRIMERA B COLOMBIA", "TORNEO BETPLAY"] and pais_deducido != "COLOMBIA":
            return False
        if clave in ["PRIMERA B CHILE", "CAMPEONATO CHILENO", "PRIMERA DIVISION CHILE", "ASCENSO CHILE"] and pais_deducido != "CHILE":
            return False
        if any(m in DISCRIMINADORES_PAISES and m != pais_norm for m in mods_clave):
            return False

    if clave == torneo_norm:
        return True
    mods_torneo = _normalizar_mods_torneo({d for d in DISCRIMINADORES_TORNEO if re.search(r"\b" + re.escape(d) + r"\b", torneo_norm)})
    mods_clave = _normalizar_mods_torneo({d for d in DISCRIMINADORES_TORNEO if re.search(r"\b" + re.escape(d) + r"\b", clave)})
    return mods_torneo == mods_clave

def es_logo_compatible_con_categoria(url: str, categoria: str) -> bool:
    """Verifica de forma estricta e invariable que el logo no pertenezca a otra disciplina deportiva."""
    if not url or not categoria:
        return True
    url_low = url.lower()
    cat_norm = _normalizar(categoria)

    LOGOS_FUTBOL = [
        "fifa", "uefa", "conmebol", "concacaf", "laliga", "premier_league", "serie_a",
        "bundesliga", "ligue_1", "libertadores", "sudamericana", "champions_league",
        "europa_league", "conference_league", "nations_league", "copa_del_rey",
        "fa_cup", "efl_championship", "copa_africana", "copa_oro", "torneo_betano",
        "liga_betplay", "copa_betplay", "torneo_betplay", "copa_argentina"
    ]
    if any(k in url_low for k in LOGOS_FUTBOL):
        if cat_norm not in ["FUTBOL", "FUTBOL SALA", "SOCCER"]:
            return False

    LOGOS_BEISBOL = ["mlb", "major_league_baseball", "lmb", "kbo", "npb"]
    if any(k in url_low for k in LOGOS_BEISBOL):
        if cat_norm not in ["BEISBOL", "BASEBALL"]:
            return False

    LOGOS_BASKET = ["nba", "wnba", "fiba", "euroleague", "acb", "basketball", "liga_endesa"]
    if any(k in url_low for k in LOGOS_BASKET):
        if cat_norm not in ["BALONCESTO", "BASKETBALL", "BASKET"]:
            return False

    LOGOS_NFL = ["nfl", "cfl", "ufl"]
    if any(k in url_low for k in LOGOS_NFL):
        if cat_norm not in ["FUTBOL AMERICANO", "NFL"]:
            return False

    LOGOS_TENIS = ["atp", "wta", "itf", "davis_cup", "us_open_tennis", "roland_garros", "wimbledon"]
    if any(k in url_low for k in LOGOS_TENIS):
        if cat_norm not in ["TENIS", "TENNIS"]:
            return False

    LOGOS_RUGBY = ["world_rugby", "six_nations", "rugby_championship", "top_14"]
    if any(k in url_low for k in LOGOS_RUGBY):
        if cat_norm not in ["RUGBY"]:
            return False

    LOGOS_HOCKEY = ["nhl", "iihf"]
    if any(k in url_low for k in LOGOS_HOCKEY):
        if cat_norm not in ["HOCKEY"]:
            return False

    LOGOS_COMBATE = ["ufc", "bellator", "one_championship", "pfl"]
    if any(k in url_low for k in LOGOS_COMBATE):
        if cat_norm not in ["COMBATE", "MMA", "BOXEO"]:
            return False

    LOGOS_MOTOR = ["f1", "formula1", "motogp", "moto_gp", "indycar", "wrc", "nascar", "worldsbk"]
    if any(k in url_low for k in LOGOS_MOTOR):
        if cat_norm not in ["MOTOR", "FORMULA 1", "MOTOGP"]:
            return False

    return True

def resolver_logo_torneo(torneo: str, categoria: str, permitir_red: bool = False, local: str = "", visitante: str = "") -> str:
    """Resuelve el logo oficial del torneo con precedencia estricta por limites de palabra (\b), compatibilidad deportiva y desambiguacion geografica por participantes."""
    if not torneo and not categoria:
        return ""

    torneo_norm = _normalizar(torneo)
    cat_clean = _normalizar(categoria or "")

    # Deducir pais por participantes y/o titulo de torneo usando límites de palabra \b
    pais_deducido = ""
    if local or visitante:
        pais_deducido = deducir_pais_equipos(local, visitante)
    if not pais_deducido:
        for pais_cand in ["BRASIL", "BRAZIL", "CHILE", "COLOMBIA", "ARGENTINA", "ITALIA", "ITALY", "ESPANA", "SPAIN", "MEXICO", "ECUADOR", "URUGUAY", "PERU"]:
            if re.search(rf"\b{re.escape(pais_cand)}\b", torneo_norm):
                pais_deducido = "BRASIL" if pais_cand == "BRAZIL" else ("ITALIA" if pais_cand == "ITALY" else ("ESPANA" if pais_cand == "SPAIN" else pais_cand))
                break

    claves_prioritarias: list[str] = []
    if pais_deducido:
        if any(re.search(rf"\b{re.escape(x)}\b", torneo_norm) for x in ["SERIE A", "SERIE B"]):
            if pais_deducido == "BRASIL":
                if "SERIE B" in torneo_norm:
                    claves_prioritarias.extend(["SERIE B BRASIL", "BRASILEIRAO B"])
                else:
                    claves_prioritarias.extend(["SERIE A BRASIL", "BRASILEIRAO", "CAMPEONATO BRASILEIRO"])
            elif pais_deducido == "ITALIA":
                if "SERIE B" in torneo_norm:
                    claves_prioritarias.extend(["SERIE B", "SERIE BKT"])
                else:
                    claves_prioritarias.extend(["SERIE A", "SERIE A TIM", "SERIE A ENILIVE"])
            elif pais_deducido == "ECUADOR":
                if "SERIE B" in torneo_norm:
                    claves_prioritarias.append("LIGA PRO SERIE B")
                else:
                    claves_prioritarias.append("LIGA PRO ECUADOR")

        elif any(re.search(rf"\b{re.escape(x)}\b", torneo_norm) for x in ["PRIMERA B", "SEGUNDA DIVISION", "ASCENSO"]):
            if pais_deducido == "CHILE":
                claves_prioritarias.extend(["PRIMERA B CHILE", "ASCENSO CHILE"])
            elif pais_deducido == "COLOMBIA":
                claves_prioritarias.extend(["PRIMERA B COLOMBIA", "TORNEO BETPLAY", "TORNEO BETPLAY DIMAYOR"])
            elif pais_deducido == "ARGENTINA":
                claves_prioritarias.extend(["PRIMERA B METROPOLITANA", "PRIMERA NACIONAL"])

        elif any(re.search(rf"\b{re.escape(x)}\b", torneo_norm) for x in ["PRIMERA DIVISION", "PRIMERA A", "LIGA PROFESIONAL"]):
            if pais_deducido == "CHILE":
                claves_prioritarias.extend(["PRIMERA DIVISION CHILE", "CAMPEONATO CHILENO", "CAMPEONATO BETSSON"])
            elif pais_deducido == "COLOMBIA":
                claves_prioritarias.extend(["LIGA BETPLAY", "LIGA BETPLAY DIMAYOR", "PRIMERA A"])
            elif pais_deducido == "ARGENTINA":
                claves_prioritarias.extend(["LIGA PROFESIONAL ARGENTINA", "TORNEO BETANO", "PRIMERA DIVISION ARGENTINA", "LIGA PROFESIONAL"])
            elif pais_deducido == "ESPANA":
                claves_prioritarias.extend(["PRIMERA DIVISION ESPANA", "LALIGA"])

        claves_prioritarias.append(f"{torneo_norm} {pais_deducido}")

    sufijo_cache = f"_{pais_deducido}" if pais_deducido else ""
    cache = _cargar_cache()
    clave_cache = f"torneo_{cat_clean}_{torneo_norm}{sufijo_cache}" if cat_clean else f"torneo_{torneo_norm}{sufijo_cache}"

    # 0. Prioridades explícitas en CIRCUITO_LOGOS y catálogo local
    if CIRCUITO_LOGOS and claves_prioritarias:
        for cp in claves_prioritarias:
            if cp in CIRCUITO_LOGOS:
                url = CIRCUITO_LOGOS[cp]
                if es_logo_compatible_con_categoria(url, categoria):
                    cache[clave_cache] = url
                    _guardar_cache(cache)
                    return url

    catalogo = _obtener_catalogo_torneos()
    if catalogo and claves_prioritarias:
        for cp in claves_prioritarias:
            if cp in catalogo and not es_logo_basura(catalogo[cp]):
                url = catalogo[cp]
                if es_logo_compatible_con_categoria(url, categoria):
                    cache[clave_cache] = url
                    _guardar_cache(cache)
                    return url

    # 1. Catalogo maestro de insignias oficiales (Precedencia Maxima por longitud de clave con \b)
    claves_ordenadas_circ = sorted(CIRCUITO_LOGOS.keys(), key=len, reverse=True)
    for clave in claves_ordenadas_circ:
        patron = rf"\b{re.escape(clave)}\b"
        if (clave == torneo_norm) or re.search(patron, torneo_norm):
            if not son_torneos_compatibles(torneo_norm, clave, pais_deducido):
                continue
            url = CIRCUITO_LOGOS[clave]
            if not es_logo_compatible_con_categoria(url, categoria):
                continue
            cache[clave_cache] = url
            _guardar_cache(cache)
            return url

    # 1.5 Catalogo de torneos de API-Sports en disco (con \b)
    if catalogo:
        claves_ordenadas_cat = sorted(catalogo.keys(), key=len, reverse=True)
        for clave in claves_ordenadas_cat:
            patron = rf"\b{re.escape(clave)}\b"
            if (clave == torneo_norm) or re.search(patron, torneo_norm):
                if not son_torneos_compatibles(torneo_norm, clave, pais_deducido):
                    continue
                url = catalogo[clave]
                if es_logo_basura(url) or not es_logo_compatible_con_categoria(url, categoria):
                    continue
                cache[clave_cache] = url
                _guardar_cache(cache)
                return url

    # 2. Cache previo en disco con invalidacion de falsos positivos y de disciplina
    if clave_cache in cache and cache[clave_cache] and not es_logo_basura(cache[clave_cache]):
        cached_url = cache[clave_cache]
        if not es_logo_compatible_con_categoria(cached_url, categoria):
            pass
        elif "uefa_champions_league" in cached_url.lower() and any(k in torneo_norm for k in ["AFC", "CAF", "CONCACAF", "OFC", "EHF"]):
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
                    if badge and not es_logo_basura(badge) and es_logo_compatible_con_categoria(badge, categoria):
                        cdn_url = envolver_cdn_proxy(badge)
                        cache[clave_cache] = cdn_url
                        _guardar_cache(cache)
                        return cdn_url
        except Exception:
            pass

    # 4. Fallback por categoria solo si esta explicitamente listado y es compatible
    cat_limpia = categoria.strip()
    if cat_limpia in FALLBACK_POR_CATEGORIA:
        url_fb = FALLBACK_POR_CATEGORIA[cat_limpia]
        if url_fb and es_logo_compatible_con_categoria(url_fb, categoria):
            return url_fb

    return ""

TRADUCCIONES_CIUDADES_EQUIPOS = {
    "MOSCU": "MOSCOW",
    "MUNICH": "MUNCHEN",
    "ESTAMBUL": "ISTANBUL",
    "PRAGA": "PRAGUE",
    "VARSOVIA": "WARSAW",
    "BELGRADO": "BELGRADE",
    "BUCAREST": "BUCHAREST",
    "ATENAS": "ATHENS",
    "LISBOA": "LISBON",
    "NAPOLES": "NAPOLI",
    "MILAN": "MILANO",
    "TURIN": "TORINO",
    "COLONIA": "KOLN",
    "BASILEA": "BASEL",
    "GINEBRA": "GENEVE",
    "AMBERES": "ANTWERP",
    "BRUJAS": "BRUGGE",
}

def _generar_variantes_busqueda_equipo(equipo_limpio: str) -> list[str]:
    """Genera variantes universales (sin sufijos deportivos/juveniles y con topónimos en inglés) para maximizar match en catálogo y TheSportsDB."""
    variantes = [equipo_limpio]
    norm = _normalizar(equipo_limpio)

    # Traducir topónimos comunes (ej. CSKA Moscú -> CSKA Moscow)
    norm_trad = norm
    for es_c, en_c in TRADUCCIONES_CIUDADES_EQUIPOS.items():
        norm_trad = re.sub(rf"\b{es_c}\b", en_c, norm_trad)
    if norm_trad != norm:
        variantes.append(norm_trad.title())

    # Quitar sufijos o prefijos genéricos de disciplina/patrocinio (ej. Surne Bilbao Basket -> Bilbao Basket / Bilbao)
    sin_suf = re.sub(
        r"\b(BASKET|BASKETS|BASKETBALL|BALONCESTO|HANDBALL|BALONMANO|FUTSAL|VOLLEY|VOLEIBOL|FC|CF|CD|BC|CB|U19|U20|U21|U23|RES|RESERVE)\b",
        "",
        norm_trad
    )
    sin_suf = " ".join(sin_suf.split()).strip()
    if sin_suf and len(sin_suf) >= 3 and sin_suf.title() not in variantes:
        variantes.append(sin_suf.title())

    return variantes

def resolver_logo_equipo(equipo: str, deporte: str = "Fútbol", torneo: str = "", permitir_red: bool = True, es_seleccion: Optional[bool] = None) -> str:
    """
    Resuelve el escudo/logo o bandera de un equipo.
    Principio Universal:
    - Si es selección nacional (validado gramaticalmente o por bandera es_seleccion), asigna bandera oficial HD.
    - Si es club, busca primero en catalogo_maestro_equipos.json (alimentado por la descarga diaria de los 12 deportes de API-Sports),
      luego en caché local y finalmente en TheSportsDB (cero llamadas 1 a 1 a API-Sports para cuidar cuota Free).
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
            torneo_norm = _normalizar(torneo)
            es_liga_domestica = any(l in torneo_norm for l in [
                "SEGUNDA", "PRIMERA DIVISION", "PREMIER LEAGUE", "LALIGA", "SERIE A", "BUNDESLIGA",
                "LIGUE 1", "BETPLAY", "LIGA MX", "BRASILEIRAO"
            ])
            if es_seleccion is True or not es_liga_domestica:
                return flag_url

    # 1. Catálogo maestro permanente de equipos en disco (Prioridad Absoluta + variantes normalizadas)
    cat_equipos = _obtener_catalogo_equipos()
    variantes = _generar_variantes_busqueda_equipo(equipo_limpio)
    for var in variantes:
        k_var = f"equipo_{_normalizar(var)}"
        if k_var in cat_equipos and cat_equipos[k_var] and not es_logo_basura(cat_equipos[k_var]):
            if k_var != clave_cache:
                _guardar_catalogo_equipos({clave_cache: cat_equipos[k_var]})
            return cat_equipos[k_var]

    # Limpieza de ranking universitario americano ("24 Harvard" -> "Harvard")
    equipo_sin_ranking = re.sub(r"^\d+\s+", "", equipo_limpio).strip()
    if equipo_sin_ranking != equipo_limpio:
        clave_sin_rank = f"equipo_{_normalizar(equipo_sin_ranking)}"
        if clave_sin_rank in cat_equipos and cat_equipos[clave_sin_rank] and not es_logo_basura(cat_equipos[clave_sin_rank]):
            cat_equipos[clave_cache] = cat_equipos[clave_sin_rank]
            _guardar_catalogo_equipos({clave_cache: cat_equipos[clave_sin_rank]})
            return cat_equipos[clave_cache]

    # Búsqueda por inclusión de raíz en catálogo maestro de equipos (>= 5 caracteres)
    for var in variantes:
        var_n = _normalizar(var)
        if len(var_n) >= 5:
            patron_v = rf"\b{re.escape(var_n)}\b"
            for k_cat, url_cat in cat_equipos.items():
                if not url_cat or es_logo_basura(url_cat):
                    continue
                nom_cat = k_cat.replace("equipo_", "")
                if re.search(patron_v, nom_cat) or (len(nom_cat) >= 5 and re.search(rf"\b{re.escape(nom_cat)}\b", var_n)):
                    _guardar_catalogo_equipos({clave_cache: url_cat})
                    return url_cat

    # 2. Caché previo en disco
    cache = _cargar_cache()
    if clave_cache in cache and cache[clave_cache] and not es_logo_basura(cache[clave_cache]):
        return cache[clave_cache]

    if not permitir_red:
        return ""

    # 3. Consulta a TheSportsDB probando las variantes del nombre (sin tocar API-Sports 1 a 1)
    for var in variantes:
        time.sleep(0.30)
        try:
            tsdb_url = f"https://www.thesportsdb.com/api/v1/json/{THESPORTSDB_KEY}/searchteams.php?t={urllib.parse.quote(var)}"
            data = _http_get_json(tsdb_url, timeout=3)
            if data and data.get("teams"):
                teams = data["teams"]
                if teams:
                    badge = teams[0].get("strBadge") or teams[0].get("strLogo")
                    if badge and not es_logo_basura(badge):
                        cdn_url = envolver_cdn_proxy(badge)
                        cache[clave_cache] = cdn_url
                        _guardar_cache(cache)
                        _guardar_catalogo_equipos({clave_cache: cdn_url})
                        return cdn_url
        except Exception:
            pass

    return ""