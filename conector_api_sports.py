# -*- coding: utf-8 -*-
"""
Conector Integral API-Sports (12 Deportes):
1. Descarga y cacheo diario de la cartelera oficial multideporte (Fútbol, Baloncesto, Béisbol,
   Hockey, Rugby, Fútbol Americano, Balonmano, Voleibol, Formula 1, MMA, AFL, NBA).
2. Sincronización estricta con la zona horaria del producto (America/Bogota).
3. Actualización de 'catalogo_maestro_equipos.json' y 'catalogo_maestro_torneos.json'
   con los escudos y logos oficiales en HD con proxy anti-403 (wsrv.nl).
4. Emparejamiento inteligente con paridad estricta de DEPORTE, DIVISIÓN (Mayores vs U19/Reserva/Fem),
   VENTANA HORARIA y ESTADO ACTIVO.
"""
from __future__ import annotations

import json
import logging
import os
import re
import ssl
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger("conector_api_sports")

REPO_DIR = Path(__file__).resolve().parent
API_SPORTS_KEY_DEFAULT = "8b4421b0de525a42888c4dbe24f8d825"
APP_TIMEZONE_DEFAULT = "America/Bogota"

ESTADOS_INVALIDOS_API = {
    "PST", "CANC", "SUSP", "ABD", "AWD", "WO",
    "POST", "POSTPONED", "CANCELED", "CANCELLED", "SUSPENDED", "ABANDONED"
}

def _crear_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

SINONIMOS_EQUIPOS_API = {
    "RIONEGRO AGUILAS": "AGUILAS DORADAS",
    "AGUILAS PEREIRA": "AGUILAS DORADAS",
    "SPORTING CP": "SPORTING LISBOA",
    "BAYERN MUNCHEN": "BAYERN MUNICH",
    "INTERNAZIONALE": "INTER",
    "BOCA JRS": "BOCA JUNIORS",
    "CSKA MOSCU": "CSKA MOSCOW",
    "DINAMO MOSCU": "DYNAMO MOSCOW",
    "LOKOMOTIV MOSCU": "LOKOMOTIV MOSCOW",
    "SPARTAK MOSCU": "SPARTAK MOSCOW",
    "ESTRELLA ROJA": "CRVENA ZVEZDA",
    "PSV EINDHOVEN": "PSV",
    "PSV/EINDHOVEN": "PSV",
    "EINDHOVEN": "PSV",
}

SINONIMOS_PAISES_API = {
    "ESTADOS UNIDOS": "USA",
    "EEUU": "USA",
    "EE UU": "USA",
    "UNITED STATES": "USA",
    "ESPANA": "SPAIN",
    "ALEMANIA": "GERMANY",
    "DEUTSCHLAND": "GERMANY",
    "INGLATERRA": "ENGLAND",
    "PAISES BAJOS": "NETHERLANDS",
    "HOLANDA": "NETHERLANDS",
    "FRANCIA": "FRANCE",
    "ITALIA": "ITALY",
    "BRASIL": "BRAZIL",
    "ARGENTINA": "ARGENTINA",
    "COLOMBIA": "COLOMBIA",
    "MEXICO": "MEXICO",
    "URUGUAY": "URUGUAY",
    "CHILE": "CHILE",
    "PERU": "PERU",
    "ECUADOR": "ECUADOR",
    "VENEZUELA": "VENEZUELA",
    "PARAGUAY": "PARAGUAY",
    "BOLIVIA": "BOLIVIA",
    "PORTUGAL": "PORTUGAL",
    "BELGICA": "BELGIUM",
    "SUIZA": "SWITZERLAND",
    "SUECIA": "SWEDEN",
    "NORUEGA": "NORWAY",
    "DINAMARCA": "DENMARK",
    "POLONIA": "POLAND",
    "CROACIA": "CROATIA",
    "TURQUIA": "TURKEY",
    "JAPON": "JAPAN",
    "COREA DEL SUR": "SOUTH KOREA",
    "ARABIA SAUDITA": "SAUDI ARABIA",
    "MARRUECOS": "MOROCCO",
    "EGIPTO": "EGYPT",
    "SENEGAL": "SENEGAL",
    "NIGERIA": "NIGERIA",
    "CAMERUN": "CAMEROON",
    "AUSTRALIA": "AUSTRALIA",
    "NUEVA ZELANDA": "NEW ZEALAND",
}

def _quitar_tildes_upper(s: Any) -> str:
    if not s:
        return ""
    s_clean = re.sub(r'\b([A-Za-z])\.([A-Za-z])\.?([A-Za-z])?\.?', lambda m: ''.join(g for g in m.groups() if g), str(s))
    nfkd = unicodedata.normalize("NFKD", s_clean)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper()

def normalizar_clave(s: Any) -> str:
    if not s:
        return ""
    sin_tildes = _quitar_tildes_upper(s)
    limpio = re.sub(r"[^A-Z0-9]+", " ", sin_tildes).strip()
    limpio = re.sub(r"\b(W|WOMEN|FEMENINO|FEM|FEMENIL|DAMAS|SUB\s*\d+|U\d+|RES)\b", "", limpio).strip()

    for k, v in SINONIMOS_PAISES_API.items():
        limpio = re.sub(rf"\b{k}\b", v, limpio)
    for k, v in SINONIMOS_EQUIPOS_API.items():
        if k in limpio:
            limpio = limpio.replace(k, v)

    palabras = limpio.split()
    ignoradas = {"CLUB", "FC", "CF", "CD", "DEPORTIVO", "ATLETICO", "BALOMPIE", "SPORTING", "REAL", "OLYMPIQUE"}
    filtradas = [p for p in palabras if p not in ignoradas or len(palabras) == 1]
    return " ".join(filtradas) if filtradas else limpio

def extraer_marcadores_division(texto: str) -> Set[str]:
    """
    Extrae marcadores estructurales de división/edad/género ('U19', 'U20', 'U21', 'U23', 'U17', 'RESERVA', 'FEMENINO').
    """
    u = _quitar_tildes_upper(texto)
    marcadores: Set[str] = set()
    if re.search(r"\b(U-?19|SUB-?\s*19|YOUTH\s*LEAGUE|JUVENIL)\b", u):
        marcadores.add("U19")
    if re.search(r"\b(U-?20|SUB-?\s*20)\b", u):
        marcadores.add("U20")
    if re.search(r"\b(U-?21|SUB-?\s*21)\b", u):
        marcadores.add("U21")
    if re.search(r"\b(U-?23|SUB-?\s*23)\b", u):
        marcadores.add("U23")
    if re.search(r"\b(U-?17|SUB-?\s*17|U-?16|SUB-?\s*16|U-?15|SUB-?\s*15)\b", u):
        marcadores.add("U17")
    if re.search(r"\b(RES\.|RESERVE|RESERVAS|PROYECCION|TORNEO\s+PROYECCION)\b", u) or re.search(r"\bRES\b", u):
        marcadores.add("RESERVA")
    if re.search(r"\b(WOMEN|WOMENS|FEMENINO|FEMENINA|FEMENIL|FEM|DAMAS|LADIES|LIGA\s+F|NWSL|WNBA)\b", u) or re.search(r"\s+W$", u):
        marcadores.add("FEMENINO")
    return marcadores

def _normalizar_deporte_comparacion(dep: str) -> str:
    d = _quitar_tildes_upper(dep).strip()
    if d in {"FUTBOL", "SOCCER", "FUTBOL SALA"}:
        return "FUTBOL"
    if d in {"BALONCESTO", "BASKETBALL", "BASKET", "NBA", "WNBA"}:
        return "BALONCESTO"
    if d in {"BEISBOL", "BASEBALL", "MLB", "LMB", "LMP"}:
        return "BEISBOL"
    if d in {"HOCKEY", "NHL"}:
        return "HOCKEY"
    if d in {"FUTBOL AMERICANO", "NFL", "AFL", "NCAAF", "CFL"}:
        return "FUTBOL AMERICANO"
    if d in {"BALONMANO", "HANDBALL"}:
        return "BALONMANO"
    if d in {"RUGBY"}:
        return "RUGBY"
    if d in {"VOLEIBOL", "VOLLEYBALL"}:
        return "VOLEIBOL"
    if d in {"MOTOR", "FORMULA 1", "F1", "MOTOGP"}:
        return "MOTOR"
    if d in {"COMBATE", "MMA", "BOXEO", "UFC", "LUCHA LIBRE"}:
        return "COMBATE"
    return d

def _son_deportes_compatibles(evento: Dict[str, Any], fixture: Dict[str, Any]) -> bool:
    """Garantiza que jamás se cruce un evento de Fútbol con un fixture de Hockey, Baloncesto o Fútbol Americano."""
    cat_ev = _normalizar_deporte_comparacion(str(evento.get("categoria") or ""))
    dep_fix = _normalizar_deporte_comparacion(str(fixture.get("deporte") or ""))
    if not cat_ev or cat_ev in {"OTROS DEPORTES", "DEPORTES", "DEPORTES EN VIVO"}:
        return True
    return cat_ev == dep_fix

def _son_divisiones_compatibles(evento: Dict[str, Any], fixture: Dict[str, Any]) -> bool:
    """Garantiza que un partido de Primera/Champions no se cruce jamás con un partido U19, Reserva o Femenino."""
    txt_ev = " ".join([
        str(evento.get("titulo") or ""),
        str(evento.get("equipo_local") or ""),
        str(evento.get("equipo_visitante") or ""),
        str(evento.get("torneo") or ""),
        str(evento.get("subtitulo") or ""),
        str(evento.get("referencia") or ""),
        str(evento.get("texto_original_crudo") or ""),
    ])
    txt_fix = " ".join([
        str(fixture.get("titulo") or ""),
        str(fixture.get("local") or ""),
        str(fixture.get("visitante") or ""),
        str(fixture.get("torneo") or ""),
    ])
    div_ev = extraer_marcadores_division(txt_ev)
    div_fix = extraer_marcadores_division(txt_fix)
    return div_ev == div_fix

def _minutos_utc(iso_str: str) -> Optional[int]:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return int(dt.timestamp() // 60)
    except Exception:
        return None

def _horas_compatibles(hora_ev_utc: str, hora_fix_utc: str, max_diff_min: int = 180) -> bool:
    """Verifica que el evento y el fixture ocurran dentro de una ventana horaria coherente."""
    m1 = _minutos_utc(hora_ev_utc)
    m2 = _minutos_utc(hora_fix_utc)
    if m1 is None or m2 is None:
        return True
    return abs(m1 - m2) <= max_diff_min

def envolver_proxy_wsrv(url: str) -> str:
    if not url or not isinstance(url, str):
        return ""
    url_s = url.strip()
    if not url_s.startswith("http"):
        return ""
    if url_s.startswith("https://flagcdn.com/"):
        return url_s
    if "wsrv.nl" in url_s:
        return url_s if "&trim=" in url_s else f"{url_s}&trim=10"
    return f"https://wsrv.nl/?url={urllib.parse.quote(url_s, safe='')}&w=400&output=webp&trim=10"

ENDPOINTS_CONFIG = [
    ("Fútbol", "https://v3.football.api-sports.io/fixtures?date={fecha}&timezone={tz}", "fixture", "teams", "league"),
    ("Baloncesto", "https://v1.basketball.api-sports.io/games?date={fecha}&timezone={tz}", None, "teams", "league"),
    ("Béisbol", "https://v1.baseball.api-sports.io/games?date={fecha}&timezone={tz}", None, "teams", "league"),
    ("Hockey", "https://v1.hockey.api-sports.io/games?date={fecha}&timezone={tz}", None, "teams", "league"),
    ("Rugby", "https://v1.rugby.api-sports.io/games?date={fecha}&timezone={tz}", None, "teams", "league"),
    ("Fútbol Americano", "https://v1.american-football.api-sports.io/games?date={fecha}&timezone={tz}", None, "teams", "league"),
    ("Balonmano", "https://v1.handball.api-sports.io/games?date={fecha}&timezone={tz}", None, "teams", "league"),
    ("Voleibol", "https://v1.volleyball.api-sports.io/games?date={fecha}&timezone={tz}", None, "teams", "league"),
    ("AFL", "https://v1.afl.api-sports.io/games?date={fecha}&timezone={tz}", None, "teams", "league"),
]

def _hacer_request_con_reintento(url: str, headers: dict, timeout: int = 12) -> Optional[dict]:
    for intento in range(2):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_crear_ssl()) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as he:
            if he.code == 429 and intento == 0:
                log.warning("API-Sports Rate Limit (429). Esperando 3s antes de reintentar: %s", url)
                time.sleep(3.0)
                continue
            log.warning("HTTP Error %s en %s: %s", he.code, url, he)
            return None
        except Exception as e:
            log.warning("Error consultando %s: %s", url, e)
            return None
    return None

def _es_estado_fixture_valido(status_short: str) -> bool:
    if not status_short:
        return True
    return status_short.strip().upper() not in ESTADOS_INVALIDOS_API

def descargar_fixtures_dia(fecha_iso: str, api_key: str = "", tz_producto: str = "") -> List[Dict[str, Any]]:
    """Descarga o carga desde caché local los fixtures sincronizados con la zona horaria del producto (excluyendo aplazados/cancelados)."""
    cache_file = REPO_DIR / f"fixtures_api_sports_{fecha_iso}.json"
    if cache_file.exists():
        try:
            fixtures_raw = json.loads(cache_file.read_text(encoding="utf-8"))
            fixtures = [
                f for f in fixtures_raw
                if _es_estado_fixture_valido(str(f.get("status_short") or ""))
            ]
            log.info("Cargados %d fixtures activos de API-Sports desde cache local (%s)", len(fixtures), fecha_iso)
            guardar_equipos_en_catalogo(fixtures)
            return fixtures
        except Exception as e:
            log.warning("No se pudo leer cache de fixtures: %s", e)

    key_actual = (api_key or os.environ.get("API_SPORTS_KEY") or API_SPORTS_KEY_DEFAULT).strip()
    tz_actual = (tz_producto or os.environ.get("APP_TIMEZONE") or APP_TIMEZONE_DEFAULT).strip()
    if not key_actual:
        log.warning("API_SPORTS_KEY no configurada. Omitiendo descarga de fixtures.")
        return []

    headers = {"x-apisports-key": key_actual, "User-Agent": "Mozilla/5.0"}
    todos_fixtures = []

    for deporte, url_tpl, root_fixture, teams_key, league_key in ENDPOINTS_CONFIG:
        url = url_tpl.format(fecha=fecha_iso, tz=tz_actual)
        time.sleep(1.2)
        data = _hacer_request_con_reintento(url, headers)
        if not data:
            continue

        items = data.get("response", [])
        log.info("API-Sports [%s]: %d eventos obtenidos (tz: %s)", deporte, len(items), tz_actual)
        for item in items:
            if not isinstance(item, dict):
                continue

            ts = None
            status_short = ""
            if root_fixture and root_fixture in item:
                root_obj = item.get(root_fixture) or {}
                f_date = root_obj.get("date", "")
                f_id = str(root_obj.get("id", ""))
                ts = root_obj.get("timestamp")
                st_obj = root_obj.get("status")
                if isinstance(st_obj, dict):
                    status_short = str(st_obj.get("short") or "")
            else:
                f_date = item.get("date", "")
                f_id = str(item.get("id", ""))
                ts = item.get("timestamp")
                st_obj = item.get("status")
                if isinstance(st_obj, dict):
                    status_short = str(st_obj.get("short") or st_obj.get("long") or "")
                elif isinstance(st_obj, str):
                    status_short = st_obj

            if not _es_estado_fixture_valido(status_short):
                continue

            if ts:
                try:
                    f_utc = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    f_utc = f_date
            else:
                f_utc = f_date

            f_local_str = f_date[11:16] if len(f_date) >= 16 else ""

            teams_obj = item.get(teams_key) or {}
            home_obj = teams_obj.get("home") or teams_obj.get("home_team") or {}
            away_obj = teams_obj.get("away") or teams_obj.get("away_team") or {}
            loc = (home_obj.get("name") or "").strip()
            vis = (away_obj.get("name") or "").strip()
            loc_logo = home_obj.get("logo") or ""
            vis_logo = away_obj.get("logo") or ""

            league_obj = item.get(league_key) or {}
            torneo = (league_obj.get("name") or deporte).strip()
            torneo_logo = league_obj.get("logo") or ""

            if loc and vis:
                todos_fixtures.append({
                    "id_api": f_id,
                    "deporte": deporte,
                    "local": loc,
                    "visitante": vis,
                    "titulo": f"{loc} vs {vis}",
                    "torneo": torneo,
                    "hora_utc": f_utc,
                    "hora_local": f_local_str,
                    "logo_local": loc_logo,
                    "logo_visitante": vis_logo,
                    "logo_torneo": torneo_logo,
                    "tipo_evento": "duelo",
                    "status_short": status_short,
                })

    # API-NBA dedicada
    time.sleep(1.2)
    url_nba = f"https://v2.nba.api-sports.io/games?date={fecha_iso}"
    data_nba = _hacer_request_con_reintento(url_nba, headers)
    if data_nba:
        items_nba = data_nba.get("response", [])
        log.info("API-Sports [NBA]: %d partidos obtenidos", len(items_nba))
        for item in items_nba:
            if not isinstance(item, dict):
                continue
            teams_nba = item.get("teams") or {}
            home_nba = teams_nba.get("home") or {}
            vis_nba = teams_nba.get("visitors") or teams_nba.get("away") or {}
            loc = (home_nba.get("name") or "").strip()
            vis = (vis_nba.get("name") or "").strip()
            loc_logo = home_nba.get("logo") or ""
            vis_logo = vis_nba.get("logo") or ""
            date_obj = item.get("date") or {}
            f_utc = date_obj.get("start") if isinstance(date_obj, dict) else str(date_obj)
            f_id = str(item.get("id") or "")
            if loc and vis:
                todos_fixtures.append({
                    "id_api": f"nba_{f_id}",
                    "deporte": "Baloncesto",
                    "local": loc,
                    "visitante": vis,
                    "titulo": f"{loc} vs {vis}",
                    "torneo": "NBA",
                    "hora_utc": f_utc or f"{fecha_iso}T00:00:00Z",
                    "hora_local": "",
                    "logo_local": loc_logo,
                    "logo_visitante": vis_logo,
                    "logo_torneo": "",
                    "tipo_evento": "duelo",
                    "status_short": "NS",
                })

    # Formula 1
    time.sleep(1.2)
    url_f1 = f"https://v1.formula-1.api-sports.io/races?date={fecha_iso}&timezone={tz_actual}"
    data_f1 = _hacer_request_con_reintento(url_f1, headers)
    if data_f1:
        items_f1 = data_f1.get("response", [])
        log.info("API-Sports [Motor F1]: %d sesiones obtenidas", len(items_f1))
        for item in items_f1:
            if not isinstance(item, dict):
                continue
            st_f1 = str((item.get("status") or "") if not isinstance(item.get("status"), dict) else (item.get("status", {}).get("short") or ""))
            if not _es_estado_fixture_valido(st_f1):
                continue
            comp = item.get("competition") or {}
            circ = item.get("circuit") or {}
            nombre_gp = comp.get("name") or "Formula 1"
            sesion = item.get("type") or ""
            f_date = item.get("date") or ""
            img_circuito = circ.get("image") or ""
            f_id = str(item.get("id") or "")
            todos_fixtures.append({
                "id_api": f_id,
                "deporte": "Motor",
                "local": "",
                "visitante": "",
                "titulo": f"F1 {nombre_gp}",
                "torneo": nombre_gp,
                "ronda": sesion,
                "hora_utc": f_date,
                "hora_local": f_date[11:16] if len(f_date) >= 16 else "",
                "logo_local": "",
                "logo_visitante": "",
                "logo_torneo": img_circuito,
                "tipo_evento": "circuito",
                "status_short": st_f1,
            })

    # MMA / Combate
    time.sleep(1.2)
    url_mma = f"https://v1.mma.api-sports.io/fights?date={fecha_iso}&timezone={tz_actual}"
    data_mma = _hacer_request_con_reintento(url_mma, headers)
    if data_mma:
        items_mma = data_mma.get("response", [])
        log.info("API-Sports [MMA]: %d combates obtenidos", len(items_mma))
        for item in items_mma:
            if not isinstance(item, dict):
                continue
            st_mma = str((item.get("status") or {}).get("short") or "") if isinstance(item.get("status"), dict) else str(item.get("status") or "")
            if not _es_estado_fixture_valido(st_mma):
                continue
            fighters = item.get("fighters") or {}
            f1_n = (fighters.get("first") or {}).get("name") or ""
            f2_n = (fighters.get("second") or {}).get("name") or ""
            f_date = item.get("date") or ""
            f_id = str(item.get("id") or "")
            league_mma = item.get("league") or {}
            torneo_mma = league_mma.get("name") or "UFC"
            if f1_n and f2_n:
                todos_fixtures.append({
                    "id_api": f_id,
                    "deporte": "Combate",
                    "local": f1_n.strip(),
                    "visitante": f2_n.strip(),
                    "titulo": f"{f1_n.strip()} vs {f2_n.strip()}",
                    "torneo": torneo_mma.strip(),
                    "hora_utc": f_date,
                    "hora_local": f_date[11:16] if len(f_date) >= 16 else "",
                    "logo_local": "",
                    "logo_visitante": "",
                    "logo_torneo": "",
                    "tipo_evento": "duelo",
                    "status_short": st_mma,
                })

    if todos_fixtures:
        try:
            cache_file.write_text(json.dumps(todos_fixtures, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("Guardados %d fixtures de API-Sports en cache local para hoy (%s)", len(todos_fixtures), fecha_iso)
        except Exception as e:
            log.warning("No se pudo escribir cache de fixtures: %s", e)

    guardar_equipos_en_catalogo(todos_fixtures)
    return todos_fixtures

def guardar_equipos_en_catalogo(fixtures: List[Dict[str, Any]]) -> None:
    cat_path = REPO_DIR / "catalogo_maestro_equipos.json"
    tor_path = REPO_DIR / "catalogo_maestro_torneos.json"

    cat_equipos = {}
    if cat_path.exists():
        try:
            cat_equipos = json.loads(cat_path.read_text(encoding="utf-8"))
        except Exception:
            cat_equipos = {}

    cat_torneos = {}
    if tor_path.exists():
        try:
            cat_torneos = json.loads(tor_path.read_text(encoding="utf-8"))
        except Exception:
            cat_torneos = {}

    nuevos_eq = 0
    nuevos_tor = 0

    for f in fixtures:
        dep_norm = _normalizar_deporte_comparacion(str(f.get("deporte") or ""))
        loc = f.get("local")
        loc_logo = f.get("logo_local")
        if loc and loc_logo:
            url_w = envolver_proxy_wsrv(loc_logo)
            k_dep = f"equipo_{dep_norm}_{normalizar_clave(loc)}"
            if k_dep not in cat_equipos:
                cat_equipos[k_dep] = url_w
                nuevos_eq += 1
            k_gen = f"equipo_{normalizar_clave(loc)}"
            if k_gen not in cat_equipos:
                cat_equipos[k_gen] = url_w
                nuevos_eq += 1

        vis = f.get("visitante")
        vis_logo = f.get("logo_visitante")
        if vis and vis_logo:
            url_w = envolver_proxy_wsrv(vis_logo)
            k_dep = f"equipo_{dep_norm}_{normalizar_clave(vis)}"
            if k_dep not in cat_equipos:
                cat_equipos[k_dep] = url_w
                nuevos_eq += 1
            k_gen = f"equipo_{normalizar_clave(vis)}"
            if k_gen not in cat_equipos:
                cat_equipos[k_gen] = url_w
                nuevos_eq += 1

        tor = f.get("torneo")
        tor_logo = f.get("logo_torneo")
        if tor and tor_logo:
            k = normalizar_clave(tor)
            if k not in cat_torneos:
                cat_torneos[k] = envolver_proxy_wsrv(tor_logo)
                nuevos_tor += 1

    if nuevos_eq > 0:
        tmp_e = cat_path.with_suffix(".tmp")
        tmp_e.write_text(json.dumps(cat_equipos, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_e.replace(cat_path)
        log.info("Añadidos %d nuevos escudos a catalogo_maestro_equipos.json", nuevos_eq)

    if nuevos_tor > 0:
        tmp_t = tor_path.with_suffix(".tmp")
        tmp_t.write_text(json.dumps(cat_torneos, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_t.replace(tor_path)
        log.info("Añadidos %d nuevos logos de torneo a catalogo_maestro_torneos.json", nuevos_tor)

def construir_indice_fixtures(fixtures: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Construye un índice que soporta múltiples partidos de los mismos clubes en el día."""
    indice: Dict[str, List[Dict[str, Any]]] = {}
    for f in fixtures:
        if not _es_estado_fixture_valido(str(f.get("status_short") or "")):
            continue
        loc = f.get("local")
        vis = f.get("visitante")
        if loc and vis:
            l_norm = normalizar_clave(loc)
            v_norm = normalizar_clave(vis)
            k1 = f"{l_norm}__VS__{v_norm}"
            k2 = f"{v_norm}__VS__{l_norm}"
            indice.setdefault(k1, []).append(f)
            if k2 != k1:
                indice.setdefault(k2, []).append(f)
        elif f.get("deporte") == "Motor":
            k_f1 = normalizar_clave(f.get("torneo", ""))
            if k_f1:
                indice.setdefault(f"F1_{k_f1}", []).append(f)
    return indice

def _son_torneos_y_paises_compatibles(evento: Dict[str, Any], fixture: Dict[str, Any]) -> bool:
    """Garantiza que no se emparejen ligas de países contradictorios (ej. Eredivisie holandesa con Serie A italiana)."""
    tor_ev = normalizar_clave(str(evento.get("torneo") or evento.get("subtitulo") or ""))
    tor_fix = normalizar_clave(str(fixture.get("torneo") or ""))
    if not tor_ev or not tor_fix:
        return True

    genericos = {"FUTBOL", "DEPORTES", "DEPORTE", "DEPORTES EN VIVO", "LIVE", "EVENTO EN VIVO"}
    if tor_ev in genericos or tor_fix in genericos:
        return True

    grupos_paises = [
        ("ITALIA", "ITALY", "SERIE A"),
        ("PAISES BAJOS", "HOLANDA", "NETHERLANDS", "EREDIVISIE"),
        ("ESPANA", "SPAIN", "LALIGA", "LA LIGA"),
        ("INGLATERRA", "ENGLAND", "PREMIER LEAGUE"),
        ("ALEMANIA", "GERMANY", "BUNDESLIGA"),
        ("FRANCIA", "FRANCE", "LIGUE 1"),
        ("ARGENTINA", "LIGA PROFESIONAL", "TORNEO BETANO"),
        ("COLOMBIA", "LIGA BETPLAY", "COPA BETPLAY"),
        ("BRASIL", "BRAZIL", "BRASILEIRAO"),
    ]
    p_ev, p_fix = None, None
    for g in grupos_paises:
        if any(x in tor_ev for x in g):
            p_ev = g[0]
        if any(x in tor_fix for x in g):
            p_fix = g[0]

    if p_ev and p_fix and p_ev != p_fix:
        return False
    return True

def _seleccionar_fixture_compatible(
    evento: Dict[str, Any],
    candidatos: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Filtra candidatos exigiendo paridad estricta de DEPORTE, DIVISIÓN, PAÍS/LIGA y CERCANÍA HORARIA."""
    hora_ev = str(evento.get("hora_utc") or "")
    validos = []
    for f in candidatos:
        if not _es_estado_fixture_valido(str(f.get("status_short") or "")):
            continue
        if not _son_deportes_compatibles(evento, f):
            continue
        if not _son_divisiones_compatibles(evento, f):
            continue
        if not _son_torneos_y_paises_compatibles(evento, f):
            continue
        if not _horas_compatibles(hora_ev, str(f.get("hora_utc") or ""), max_diff_min=180):
            continue
        validos.append(f)

    if not validos:
        return None
    if len(validos) == 1 or not hora_ev:
        return validos[0]

    m_ev = _minutos_utc(hora_ev) or 0
    validos.sort(key=lambda x: abs((_minutos_utc(str(x.get("hora_utc") or "")) or 0) - m_ev))
    return validos[0]

def emparejar_con_api_sports(
    evento: Dict[str, Any], 
    indice_fixtures: Dict[str, Any],
    fixtures_lista: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Empareja un evento (lineal o Xtream) contra la cartelera oficial de API-Sports respetando deporte, división y horario."""
    cat = (evento.get("categoria") or "").upper()
    tit = normalizar_clave(evento.get("titulo") or "")
    if "MOTOR" in cat or "F1" in tit or "FORMULA 1" in tit:
        for f in fixtures_lista:
            if f.get("deporte") == "Motor":
                t_f1 = normalizar_clave(f.get("torneo", ""))
                if t_f1 and (t_f1 in tit or tit in t_f1):
                    return f

    loc = normalizar_clave(evento.get("equipo_local") or "")
    vis = normalizar_clave(evento.get("equipo_visitante") or "")

    # 1. Coincidencia exacta de claves normalizadas con validación de deporte, división y hora
    if loc and vis:
        k = f"{loc}__VS__{vis}"
        if k in indice_fixtures:
            cands = indice_fixtures[k] if isinstance(indice_fixtures[k], list) else [indice_fixtures[k]]
            elegido = _seleccionar_fixture_compatible(evento, cands)
            if elegido:
                return elegido

    # 2. Coincidencia por subcadenas o inclusión de nombres con validación de deporte, división y hora
    if loc and vis:
        cands_sub = []
        for f in fixtures_lista:
            f_l = normalizar_clave(f.get("local", ""))
            f_v = normalizar_clave(f.get("visitante", ""))
            if not f_l or not f_v:
                continue
            match_l = (loc == f_l) or (len(loc) >= 3 and loc in f_l) or (len(f_l) >= 3 and f_l in loc)
            match_v = (vis == f_v) or (len(vis) >= 3 and vis in f_v) or (len(f_v) >= 3 and f_v in vis)
            if match_l and match_v:
                cands_sub.append(f)
        elegido = _seleccionar_fixture_compatible(evento, cands_sub)
        if elegido:
            return elegido

    # 3. Coincidencia por desglose del título
    if " VS " in tit:
        partes = tit.split(" VS ")
        if len(partes) == 2:
            p_l, p_v = normalizar_clave(partes[0]), normalizar_clave(partes[1])
            k = f"{p_l}__VS__{p_v}"
            if k in indice_fixtures:
                cands = indice_fixtures[k] if isinstance(indice_fixtures[k], list) else [indice_fixtures[k]]
                elegido = _seleccionar_fixture_compatible(evento, cands)
                if elegido:
                    return elegido
            cands_tit = []
            for f in fixtures_lista:
                f_l = normalizar_clave(f.get("local", ""))
                f_v = normalizar_clave(f.get("visitante", ""))
                if not f_l or not f_v:
                    continue
                match_l = (p_l == f_l) or (len(p_l) >= 4 and p_l in f_l) or (len(f_l) >= 4 and f_l in p_l)
                match_v = (p_v == f_v) or (len(p_v) >= 4 and p_v in f_v) or (len(f_v) >= 4 and f_v in p_v)
                if match_l and match_v:
                    cands_tit.append(f)
            elegido = _seleccionar_fixture_compatible(evento, cands_tit)
            if elegido:
                return elegido

    return None