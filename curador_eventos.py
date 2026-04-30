import os
import re
import json
import time
import logging
import requests
import unicodedata
from datetime import datetime, timezone, timedelta

# ─── LOGGING ESTRUCTURADO ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("curador")

# ─── CONFIGURACIÓN DE ENTORNO ────────────────────────────────────────────────
XTREAM_URL  = os.environ.get("XTREAM_URL")
XTREAM_USER = os.environ.get("XTREAM_USER")
XTREAM_PASS = os.environ.get("XTREAM_PASS")

ZONA_COLOMBIA = timezone(timedelta(hours=-5))
FECHA_HOY     = datetime.now(ZONA_COLOMBIA).strftime("%Y-%m-%d")

# ─── SOFASCORE SCRAPING ──────────────────────────────────────────────────────
SOFA_BASE = "https://www.sofascore.com/api/v1"
SOFA_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Referer":         "https://www.sofascore.com/",
    "Origin":          "https://www.sofascore.com",
    "Cache-Control":   "no-cache",
}

# ─── CDN DE IMÁGENES SOFASCORE ───────────────────────────────────────────────
SOFA_IMG_EQUIPO = "https://api.sofascore.app/api/v1/team/{id}/image"
SOFA_IMG_TORNEO = "https://api.sofascore.app/api/v1/unique-tournament/{id}/image/dark"

# ─── LEET / OFUSCACIONES ─────────────────────────────────────────────────────
LEET_DICT = {
    "@": "A", "€": "E", "¡": "I", "|": "I",
    "Ø": "O", "$": "S", "ñ": "N", "Ñ": "N",
}

# ─── ALIAS DE EQUIPOS ────────────────────────────────────────────────────────
ALIAS_EQUIPOS = {
    "MAN CITY":        "MANCHESTER CITY",
    "MAN UTD":         "MANCHESTER UNITED",
    "MAN U":           "MANCHESTER UNITED",
    "PARIS SG":        "PARIS SAINT GERMAIN",
    "PSG":             "PARIS SAINT GERMAIN",
    "INTER MILAN":     "INTERNAZIONALE",
    "AC MILAN":        "MILAN",
    "SPURS":           "TOTTENHAM",
    "JUVE":            "JUVENTUS",
    "NEWCASTLE UTD":   "NEWCASTLE",
    "ATM":             "ATLETICO MADRID",
    "BARCA":           "BARCELONA",
    "FCB":             "BARCELONA",
    "BETIS":           "REAL BETIS",
    "SANTA FE":        "INDEPENDIENTE SANTA FE",
    "NACIONAL":        "ATLETICO NACIONAL",
    "AMERICA":         "AMERICA CALI",
    "DEP CALI":        "DEPORTIVO CALI",
    "LAKERS":          "LOS ANGELES LAKERS",
    "LAL":             "LOS ANGELES LAKERS",
    "WARRIORS":        "GOLDEN STATE WARRIORS",
    "GSW":             "GOLDEN STATE WARRIORS",
    "CELTICS":         "BOSTON CELTICS",
    "BOS":             "BOSTON CELTICS",
    "BUCKS":           "MILWAUKEE BUCKS",
    "NETS":            "BROOKLYN NETS",
    "CLIPPERS":        "LOS ANGELES CLIPPERS",
    "RED BULL RACING": "RED BULL",
    "REDBULL":         "RED BULL",
    "RBR":             "RED BULL",
    "MERCEDES AMG":    "MERCEDES",
    "FERRARI F1":      "FERRARI",
}

PALABRAS_GENERICAS = {
    "WOMEN", "MEN", "CUP", "LEAGUE", "LIVE", "FHD", "4K", "1080P", "720P",
    "1080", "720", "480", "CHAMPIONSHIP", "TOUR", "QUALIFICATION", "TV",
    "SPORTS", "VS", "STADIUM", "CANCHA", "HD", "SD", "UHD", "PREMIUM",
}

STOP_WORDS = {
    "FC", "SC", "CF", "AC", "AS", "US", "CS", "RC", "CD", "SD", "UD",
    "THE", "DE", "LA", "LAS", "LOS", "EL", "Y", "E", "AND", "OF", "DEL",
    "CENTRAL", "CITY", "UNITED", "REAL", "CLUB", "ATLETICO",
    "DEPORTIVO", "SPORTING",
}

# ─── TIERS DE RELEVANCIA ─────────────────────────────────────────────────────
TIER_1_ELITE = [
    "CHAMPIONS LEAGUE", "UEFA CHAMPIONS", "LIGA BETPLAY", "LA LIGA",
    "PREMIER LEAGUE", "SERIE A", "FORMULA 1", "NBA", "UFC", "LIBERTADORES",
    "COPA AMERICA", "MUNDIAL", "WORLD CUP", "ATP", "WTA", "GRAND SLAM",
    "WIMBLEDON", "ROLAND GARROS", "US OPEN", "AUSTRALIAN OPEN",
    "MOTO GP", "MOTOGP", "SIX NATIONS", "RUGBY CHAMPIONSHIP",
    "COPA DEL REY", "FA CUP", "SUPER BOWL", "BOXING WORLD",
]

TIER_2_NICHO = [
    "NFL", "MLB", "F2", "F3", "COPA SUDAMERICANA", "EREDIVISIE",
    "BUNDESLIGA", "LIGUE 1", "CHAMPIONS CUP", "SUPER RUGBY",
    "NATIONS LEAGUE", "RECOPA", "SUPERLIGA",
]

PAISES_HEROE_LOCAL = {"Colombia", "Spain", "CO", "ES"}

CATEGORIAS_FUTBOL_PERMITIDAS = {
    "Colombia", "Spain", "World", "Europe", "South America",
    "North & Central America", "England", "Italy", "Germany",
    "France", "USA", "Argentina", "Brazil", "Mexico",
    "Saudi Arabia", "Asia", "Africa",
}

# ─── CONFIGURACIÓN POR DEPORTE ───────────────────────────────────────────────
DEPORTES_SLUGS = {
    "Fútbol":     "football",
    "Baloncesto": "basketball",
    "Tenis":      "tennis",
    "Motor":      "motorsport",
    "Béisbol":    "baseball",
    "Rugby":      "rugby",
    "Boxeo":      "box",
    "Voleibol":   "volleyball",
    "MMA":        "mma",
}

DURACION_POR_DEPORTE = {
    "Fútbol": 120, "Baloncesto": 150, "Tenis": 180, "Motor": 210,
    "Béisbol": 210, "Rugby": 120, "Boxeo": 180, "Voleibol": 150, "MMA": 200,
}

UMBRAL_POR_DEPORTE = {
    "Fútbol": 70, "Baloncesto": 65, "Tenis": 60, "Motor": 50,
    "Béisbol": 65, "Rugby": 65, "Boxeo": 60, "Voleibol": 65, "MMA": 55,
}

# ─── UTILIDADES ──────────────────────────────────────────────────────────────
def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    texto = str(texto).upper()
    for leet, real in LEET_DICT.items():
        texto = texto.replace(leet, real)
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^A-Z0-9\s:]", " ", texto)
    return " ".join(texto.split())

def aplicar_alias(texto: str) -> str:
    for alias, expansion in sorted(ALIAS_EQUIPOS.items(), key=lambda x: -len(x[0])):
        texto = re.sub(r"\b" + re.escape(alias) + r"\b", expansion, texto)
    return texto

def extraer_keywords(texto: str) -> list:
    palabras = normalizar_texto(texto).split()
    return [
        p for p in palabras
        if p not in STOP_WORDS
        and p not in PALABRAS_GENERICAS
        and len(p) > 2
    ]

def url_logo_equipo(team_id) -> str:
    return SOFA_IMG_EQUIPO.format(id=team_id) if team_id else ""

def url_logo_torneo(unique_id) -> str:
    return SOFA_IMG_TORNEO.format(id=unique_id) if unique_id else ""

# ─── OBTENER STREAMS XTREAM ───────────────────────────────────────────────────
def obtener_datos_xtream() -> list:
    log.info("Descargando estructura de Xtream...")
    url_base = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
    try:
        r_cat = requests.get(f"{url_base}&action=get_live_categories", timeout=15)
        r_str = requests.get(f"{url_base}&action=get_live_streams",    timeout=30)
        r_cat.raise_for_status()
        r_str.raise_for_status()

        categorias = {
            str(c.get("category_id", "")): c.get("category_name", "")
            for c in r_cat.json()
        }

        streams_listos = []
        for s in r_str.json():
            cat_name    = categorias.get(str(s.get("category_id", "")), "")
            stream_name = s.get("name", "")
            texto_norm  = normalizar_texto(f"{cat_name} {stream_name}")
            texto_final = aplicar_alias(texto_norm)
            streams_listos.append({
                "id":             s.get("stream_id"),
                "nombre_ui":      stream_name.strip(),
                "texto_analisis": texto_final,
            })

        log.info(f"Xtream: {len(streams_listos)} streams cargados.")
        return streams_listos

    except requests.exceptions.RequestException as e:
        log.error(f"Error en Xtream [{type(e).__name__}]: {e}")
        return []
    except (ValueError, KeyError) as e:
        log.error(f"Error parseando Xtream [{type(e).__name__}]: {e}")
        return []

# ─── OBTENER EVENTOS SOFASCORE (scraping directo, sin API key) ───────────────
def obtener_eventos_api() -> list:
    log.info(f"Consultando SofaScore para: {FECHA_HOY}")
    eventos_procesados = []

    for deporte, slug in DEPORTES_SLUGS.items():
        url = f"{SOFA_BASE}/sport/{slug}/scheduled-events/{FECHA_HOY}"
        log.info(f"  [{deporte}] {slug}...")

        try:
            r = requests.get(url, headers=SOFA_HEADERS, timeout=20)
            if r.status_code == 429:
                log.warning(f"  [{deporte}] Rate limit. Esperando 20s...")
                time.sleep(20)
                r = requests.get(url, headers=SOFA_HEADERS, timeout=20)
            if r.status_code != 200:
                log.warning(f"  [{deporte}] HTTP {r.status_code}. Saltando.")
                time.sleep(2)
                continue
            datos = r.json().get("events", [])
        except Exception as e:
            log.error(f"  [{deporte}] {type(e).__name__}: {e}")
            time.sleep(2)
            continue

        cnt = 0
        for ev in datos:
            try:
                torneo_data        = ev.get("tournament", {})
                unique_torneo_data = torneo_data.get("uniqueTournament", {})
                cat_data           = torneo_data.get("category", {})

                torneo_nombre    = torneo_data.get("name", "")
                pais_evento      = cat_data.get("name", "")
                pais_codigo      = cat_data.get("alpha2", "")
                unique_torneo_id = unique_torneo_data.get("id")

                if deporte == "Fútbol" and pais_evento not in CATEGORIAS_FUTBOL_PERMITIDAS:
                    continue

                torneo_norm = normalizar_texto(torneo_nombre)

                if deporte == "Tenis" and "ITF" in torneo_norm:
                    if not any(t in torneo_norm for t in TIER_1_ELITE) and \
                       not any(t in torneo_norm for t in TIER_2_NICHO):
                        continue

                home_data = ev.get("homeTeam", {})
                away_data = ev.get("awayTeam", {})
                home_id   = home_data.get("id")
                away_id   = away_data.get("id")
                eq_local  = home_data.get("name", "")
                eq_visit  = away_data.get("name", "")

                unix_time = ev.get("startTimestamp")
                if not unix_time:
                    continue

                dt_utc     = datetime.fromtimestamp(unix_time, timezone.utc)
                hora_utc   = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                hora_corta = dt_utc.astimezone(ZONA_COLOMBIA).strftime("%H:%M")

                tier = 3
                if any(t in torneo_norm for t in TIER_1_ELITE):
                    tier = 1
                elif any(t in torneo_norm for t in TIER_2_NICHO):
                    tier = 2
                if pais_evento in PAISES_HEROE_LOCAL or pais_codigo in PAISES_HEROE_LOCAL:
                    tier = min(tier, 2)

                es_duelo = bool(eq_local and eq_visit)
                titulo   = f"{eq_local} vs {eq_visit}" if es_duelo else torneo_nombre

                if es_duelo:
                    logo_local     = url_logo_equipo(home_id)
                    logo_visitante = url_logo_equipo(away_id)
                else:
                    logo_local     = url_logo_torneo(unique_torneo_id)
                    logo_visitante = ""

                banner       = url_logo_torneo(unique_torneo_id)
                duracion_min = DURACION_POR_DEPORTE.get(deporte, 150)

                eventos_procesados.append({
                    "id":             str(ev.get("id")),
                    "titulo":         titulo,
                    "torneo":         torneo_nombre,
                    "categoria":      deporte,
                    "hora_utc":       hora_utc,
                    "logo_local":     logo_local,
                    "logo_visitante": logo_visitante,
                    "banner":         banner,
                    "tier":           tier,
                    "duracion_min":   duracion_min,
                    "_hora_corta":    hora_corta,
                    "_kws_local":     extraer_keywords(eq_local),
                    "_kws_visit":     extraer_keywords(eq_visit),
                    "_kws_torneo":    extraer_keywords(torneo_nombre),
                })
                cnt += 1

            except Exception as e:
                log.warning(f"  [{deporte}] Evento {ev.get('id','?')}: {type(e).__name__}: {e}")
                continue

        log.info(f"  [{deporte}] {cnt} eventos.")
        time.sleep(2)

    log.info(f"Total API: {len(eventos_procesados)} eventos.")
    return eventos_procesados

# ─── MOTOR DE SCORING ─────────────────────────────────────────────────────────
def evaluar_vinculo(evento: dict, texto_canal: str) -> int:
    puntaje   = 0
    texto_pad = f" {texto_canal} "

    if f" {evento['_hora_corta']} " in texto_pad:
        puntaje += 30

    c_local  = sum(1 for kw in evento["_kws_local"]  if f" {kw} " in texto_pad)
    c_visit  = sum(1 for kw in evento["_kws_visit"]  if f" {kw} " in texto_pad)
    c_torneo = sum(1 for kw in evento["_kws_torneo"] if f" {kw} " in texto_pad)

    if c_local  > 0: puntaje += 35
    if c_visit  > 0: puntaje += 35
    if c_torneo > 0: puntaje += 15

    if c_local == 0 and c_visit == 0 and puntaje >= 30 and c_torneo >= 2:
        puntaje += 40

    if len(evento["_kws_torneo"]) > 1 and c_torneo == 1 and c_local == 0 and c_visit == 0:
        puntaje -= 15

    return puntaje

def buscar_fuentes(evento: dict, streams: list) -> list:
    deporte = evento.get("categoria", "Fútbol")
    umbral  = UMBRAL_POR_DEPORTE.get(deporte, 70)
    return [
        {
            "nombre": s["nombre_ui"],
            "url":    f"{XTREAM_URL}/live/{XTREAM_USER}/{XTREAM_PASS}/{s['id']}.ts",
        }
        for s in streams
        if evaluar_vinculo(evento, s["texto_analisis"]) >= umbral
    ]

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    log.info("╔══ Curador de Eventos v2 ════════════════════════╗")
    log.info(f"║ Fecha: {FECHA_HOY} | Zona: Colombia (UTC-5) ║")
    log.info("╚═════════════════════════════════════════════════╝")

    streams = obtener_datos_xtream()
    if not streams:
        log.critical("Sin streams de Xtream. Abortando.")
        return

    eventos = obtener_eventos_api()
    if not eventos:
        log.critical("Sin eventos de API. Abortando.")
        return

    try:
        with open("eventos_raw_backup.json", "w", encoding="utf-8") as f:
            json.dump(
                [{k: v for k, v in e.items() if not k.startswith("_")} for e in eventos],
                f, ensure_ascii=False, indent=2
            )
        log.info(f"Backup guardado: {len(eventos)} eventos crudos.")
    except IOError as e:
        log.warning(f"No se pudo guardar backup: {e}")

    resultados = []
    desglose   = {d: 0 for d in DEPORTES_SLUGS}

    for ev in eventos:
        fuentes = buscar_fuentes(ev, streams)
        if fuentes:
            desglose[ev["categoria"]] = desglose.get(ev["categoria"], 0) + 1
            resultados.append({
                "id":             ev["id"],
                "titulo":         ev["titulo"],
                "torneo":         ev["torneo"],
                "categoria":      ev["categoria"],
                "hora_utc":       ev["hora_utc"],
                "logo_local":     ev["logo_local"],
                "logo_visitante": ev["logo_visitante"],
                "banner":         ev["banner"],
                "tier":           ev["tier"],
                "duracion_min":   ev["duracion_min"],
                "fuentes":        fuentes,
            })

    resultados.sort(key=lambda x: x["hora_utc"])

    try:
        with open("eventos_hoy.json", "w", encoding="utf-8") as f:
            json.dump(resultados, f, ensure_ascii=False, indent=2)
        log.info(f"eventos_hoy.json escrito: {len(resultados)} eventos.")
    except IOError as e:
        log.critical(f"Error escribiendo eventos_hoy.json: {e}")
        return

    meta = {
        "generado_en":          datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fecha_eventos":        FECHA_HOY,
        "total_streams_xtream": len(streams),
        "total_eventos_api":    len(eventos),
        "total_con_fuentes":    len(resultados),
        "tasa_matching_pct":    round(len(resultados) / len(eventos) * 100, 1) if eventos else 0,
        "desglose_por_deporte": desglose,
    }
    try:
        with open("meta_curador.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except IOError:
        pass

    log.info("═" * 53)
    log.info(f"  Streams analizados : {len(streams)}")
    log.info(f"  Eventos de la API  : {len(eventos)}")
    log.info(f"  Con fuentes        : {len(resultados)} ({meta['tasa_matching_pct']}%)")
    log.info(f"  Desglose           : {desglose}")
    log.info("═" * 53)

if __name__ == "__main__":
    main()
