# -*- coding: utf-8 -*-
"""
Conector Integral API-Sports (12 Deportes):
1. Descarga y cacheo diario de la cartelera oficial multideporte (Fútbol, Baloncesto, Béisbol,
   Hockey, Rugby, Fútbol Americano, Balonmano, Voleibol, Formula 1, MMA, AFL).
2. Garantía de bajo consumo de API:
   - 1 sola lectura por deporte al día.
   - Guardado en disco en 'fixtures_api_sports_YYYY-MM-DD.json'.
   - Las ejecuciones subsiguientes del día consumen 0 lecturas.
3. Actualización de 'catalogo_maestro_equipos.json' y 'catalogo_maestro_torneos.json'
   con los escudos y logos oficiales en HD con proxy anti-403 (wsrv.nl).
4. Emparejamiento inteligente de eventos lineales y Xtream para:
   - Corroborar fecha y hora UTC oficial.
   - Inyectar nombres oficiales de equipos y torneo.
   - Asignar escudos HD oficiales de local, visitante y torneo.
"""
from __future__ import annotations

import json
import logging
import os
import re
import ssl
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("conector_api_sports")

REPO_DIR = Path(__file__).resolve().parent
API_SPORTS_KEY_DEFAULT = "8b4421b0de525a42888c4dbe24f8d825"

def _crear_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def normalizar_clave(s: str) -> str:
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(s))
    sin_tildes = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]+", " ", sin_tildes.upper()).strip()

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
    ("Fútbol", "https://v3.football.api-sports.io/fixtures?date={fecha}", "fixture", "teams", "league"),
    ("Baloncesto", "https://v1.basketball.api-sports.io/games?date={fecha}", None, "teams", "league"),
    ("Béisbol", "https://v1.baseball.api-sports.io/games?date={fecha}", None, "teams", "league"),
    ("Hockey", "https://v1.hockey.api-sports.io/games?date={fecha}", None, "teams", "league"),
    ("Rugby", "https://v1.rugby.api-sports.io/games?date={fecha}", None, "teams", "league"),
    ("Fútbol Americano", "https://v1.american-football.api-sports.io/games?date={fecha}", None, "teams", "league"),
    ("Balonmano", "https://v1.handball.api-sports.io/games?date={fecha}", None, "teams", "league"),
    ("Voleibol", "https://v1.volleyball.api-sports.io/games?date={fecha}", None, "teams", "league"),
    ("AFL", "https://v1.afl.api-sports.io/games?date={fecha}", None, "teams", "league"),
]

def descargar_fixtures_dia(fecha_iso: str, api_key: str = "") -> List[Dict[str, Any]]:
    """Descarga o carga desde caché local los fixtures de los deportes cubiertos por API-Sports para hoy."""
    cache_file = REPO_DIR / f"fixtures_api_sports_{fecha_iso}.json"
    if cache_file.exists():
        try:
            fixtures = json.loads(cache_file.read_text(encoding="utf-8"))
            log.info("Cargados %d fixtures de API-Sports desde cache local (%s)", len(fixtures), fecha_iso)
            return fixtures
        except Exception as e:
            log.warning("No se pudo leer cache de fixtures: %s", e)

    key_actual = (api_key or os.environ.get("API_SPORTS_KEY") or API_SPORTS_KEY_DEFAULT).strip()
    if not key_actual:
        log.warning("API_SPORTS_KEY no configurada. Omitiendo descarga de fixtures.")
        return []

    headers = {"x-apisports-key": key_actual, "User-Agent": "Mozilla/5.0"}
    todos_fixtures = []

    # 1. Deportes estándar de duelos
    for deporte, url_tpl, root_fixture, teams_key, league_key in ENDPOINTS_CONFIG:
        url = url_tpl.format(fecha=fecha_iso)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10, context=_crear_ssl()) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data.get("response", [])
                log.info("API-Sports [%s]: %d eventos obtenidos", deporte, len(items))
                for item in items:
                    if root_fixture and root_fixture in item:
                        f_date = item[root_fixture].get("date", "")
                        f_id = str(item[root_fixture].get("id", ""))
                    else:
                        f_date = item.get("date", "")
                        f_id = str(item.get("id", ""))

                    teams_obj = item.get(teams_key, {})
                    home_obj = teams_obj.get("home", {})
                    away_obj = teams_obj.get("away", {})
                    loc = home_obj.get("name", "").strip()
                    vis = away_obj.get("name", "").strip()
                    loc_logo = home_obj.get("logo", "")
                    vis_logo = away_obj.get("logo", "")

                    league_obj = item.get(league_key, {})
                    torneo = league_obj.get("name", "").strip()
                    torneo_logo = league_obj.get("logo", "")

                    if loc and vis:
                        todos_fixtures.append({
                            "id_api": f_id,
                            "deporte": deporte,
                            "local": loc,
                            "visitante": vis,
                            "titulo": f"{loc} vs {vis}",
                            "torneo": torneo,
                            "hora_utc": f_date,
                            "logo_local": loc_logo,
                            "logo_visitante": vis_logo,
                            "logo_torneo": torneo_logo,
                            "tipo_evento": "duelo",
                        })
        except Exception as e:
            log.warning("Error consultando API-Sports [%s]: %s", deporte, e)

    # 2. Formula 1 (Carreras / Sesiones de Hoy)
    try:
        url_f1 = f"https://v1.formula-1.api-sports.io/races?date={fecha_iso}"
        req_f1 = urllib.request.Request(url_f1, headers=headers)
        with urllib.request.urlopen(req_f1, timeout=10, context=_crear_ssl()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("response", [])
            log.info("API-Sports [Motor F1]: %d sesiones obtenidas", len(items))
            for item in items:
                comp = item.get("competition", {})
                circ = item.get("circuit", {})
                nombre_gp = comp.get("name", "Formula 1")
                sesion = item.get("type", "")
                f_date = item.get("date", "")
                img_circuito = circ.get("image", "")
                f_id = str(item.get("id", ""))
                todos_fixtures.append({
                    "id_api": f_id,
                    "deporte": "Motor",
                    "local": "",
                    "visitante": "",
                    "titulo": f"F1 {nombre_gp}",
                    "torneo": nombre_gp,
                    "ronda": sesion,
                    "hora_utc": f_date,
                    "logo_local": "",
                    "logo_visitante": "",
                    "logo_torneo": img_circuito,
                    "tipo_evento": "circuito",
                })
    except Exception as e:
        log.warning("Error consultando API-Sports [Formula 1]: %s", e)

    # 3. MMA / Combate
    try:
        url_mma = f"https://v1.mma.api-sports.io/fights?date={fecha_iso}"
        req_mma = urllib.request.Request(url_mma, headers=headers)
        with urllib.request.urlopen(req_mma, timeout=10, context=_crear_ssl()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("response", [])
            log.info("API-Sports [MMA]: %d combates obtenidos", len(items))
            for item in items:
                f1_n = item.get("fighters", {}).get("first", {}).get("name", "").strip()
                f2_n = item.get("fighters", {}).get("second", {}).get("name", "").strip()
                f_date = item.get("date", "")
                f_id = str(item.get("id", ""))
                torneo_mma = item.get("league", {}).get("name", "UFC")
                if f1_n and f2_n:
                    todos_fixtures.append({
                        "id_api": f_id,
                        "deporte": "Combate",
                        "local": f1_n,
                        "visitante": f2_n,
                        "titulo": f"{f1_n} vs {f2_n}",
                        "torneo": torneo_mma,
                        "hora_utc": f_date,
                        "logo_local": "",
                        "logo_visitante": "",
                        "logo_torneo": "",
                        "tipo_evento": "duelo",
                    })
    except Exception as e:
        log.warning("Error consultando API-Sports [MMA]: %s", e)

    if todos_fixtures:
        try:
            cache_file.write_text(json.dumps(todos_fixtures, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("Guardados %d fixtures de API-Sports en cache local para hoy (%s)", len(todos_fixtures), fecha_iso)
        except Exception as e:
            log.warning("No se pudo escribir cache de fixtures: %s", e)

    # Guardar permanentemente equipos y torneos en los catálogos en disco
    guardar_equipos_en_catalogo(todos_fixtures)

    return todos_fixtures

def guardar_equipos_en_catalogo(fixtures: List[Dict[str, Any]]) -> None:
    """Almacena permanentemente en disco (Append-Only) todo equipo o torneo verificado en la API."""
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
        loc = f.get("local")
        loc_logo = f.get("logo_local")
        if loc and loc_logo:
            k = f"equipo_{normalizar_clave(loc)}"
            if k not in cat_equipos:
                cat_equipos[k] = envolver_proxy_wsrv(loc_logo)
                nuevos_eq += 1

        vis = f.get("visitante")
        vis_logo = f.get("logo_visitante")
        if vis and vis_logo:
            k = f"equipo_{normalizar_clave(vis)}"
            if k not in cat_equipos:
                cat_equipos[k] = envolver_proxy_wsrv(vis_logo)
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

def construir_indice_fixtures(fixtures: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    indice = {}
    for f in fixtures:
        loc = f.get("local")
        vis = f.get("visitante")
        if loc and vis:
            l_norm = normalizar_clave(loc)
            v_norm = normalizar_clave(vis)
            k1 = f"{l_norm}__VS__{v_norm}"
            k2 = f"{v_norm}__VS__{l_norm}"
            indice[k1] = f
            indice[k2] = f
        elif f.get("deporte") == "Motor":
            k_f1 = normalizar_clave(f.get("torneo", ""))
            if k_f1:
                indice[f"F1_{k_f1}"] = f
    return indice

def emparejar_con_api_sports(
    evento: Dict[str, Any], 
    indice_fixtures: Dict[str, Dict[str, Any]],
    fixtures_lista: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Empareja un evento (lineal o Xtream) contra la cartelera oficial de API-Sports."""
    # Caso Motor / Formula 1
    cat = (evento.get("categoria") or "").upper()
    tit = normalizar_clave(evento.get("titulo") or "")
    if "MOTOR" in cat or "F1" in tit or "FORMULA 1" in tit:
        for f in fixtures_lista:
            if f.get("deporte") == "Motor":
                t_f1 = normalizar_clave(f.get("torneo", ""))
                if t_f1 and (t_f1 in tit or tit in t_f1 or "SINGAPORE" in tit):
                    return f

    loc = normalizar_clave(evento.get("equipo_local") or "")
    vis = normalizar_clave(evento.get("equipo_visitante") or "")

    # 1. Coincidencia exacta de claves de duelo
    if loc and vis:
        k = f"{loc}__VS__{vis}"
        if k in indice_fixtures:
            return indice_fixtures[k]

    # 2. Coincidencia por subcadenas si los nombres son largos (ej. 'Águilas Doradas Rionegro' vs 'Águilas Doradas')
    if loc and vis:
        for f in fixtures_lista:
            f_l = normalizar_clave(f.get("local", ""))
            f_v = normalizar_clave(f.get("visitante", ""))
            if not f_l or not f_v:
                continue
            match_l = (loc == f_l) or (len(loc) >= 5 and loc in f_l) or (len(f_l) >= 5 and f_l in loc)
            match_v = (vis == f_v) or (len(vis) >= 5 and vis in f_v) or (len(f_v) >= 5 and f_v in vis)
            if match_l and match_v:
                return f

    # 3. Coincidencia por título completo ('EQUIPO A VS EQUIPO B')
    if " VS " in tit:
        partes = tit.split(" VS ")
        if len(partes) == 2:
            p_l, p_v = partes[0].strip(), partes[1].strip()
            k = f"{p_l}__VS__{p_v}"
            if k in indice_fixtures:
                return indice_fixtures[k]
            for f in fixtures_lista:
                f_l = normalizar_clave(f.get("local", ""))
                f_v = normalizar_clave(f.get("visitante", ""))
                if not f_l or not f_v:
                    continue
                if (len(p_l) >= 5 and (p_l in f_l or f_l in p_l)) and (len(p_v) >= 5 and (p_v in f_v or f_v in p_v)):
                    return f

    return None
