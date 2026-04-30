import os
import re
import json
import time
import logging
import requests
import unicodedata
from datetime import datetime, timezone, timedelta

# ─── LOGGING ESTRUCTURADO ────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("curador")

# ─── CONFIGURACIÓN DE ENTORNO ────────────────────────────────────────────────
XTREAM_URL   = os.environ.get("XTREAM_URL")
XTREAM_USER  = os.environ.get("XTREAM_USER")
XTREAM_PASS  = os.environ.get("XTREAM_PASS")
RAPIDAPI_HOST = "sofasport.p.rapidapi.com"

# Colombia como zona principal (UTC-5)
ZONA_COLOMBIA = timezone(timedelta(hours=-5))
FECHA_HOY     = datetime.now(ZONA_COLOMBIA).strftime("%Y-%m-%d")

LLAVES_API = [
    os.environ.get("RAPIDAPI_KEY_1"),
    os.environ.get("RAPIDAPI_KEY_2"),
    os.environ.get("RAPIDAPI_KEY_3"),
]
LLAVES_API = [k for k in LLAVES_API if k]
indice_llave_actual = 0

# ─── CDN DE IMÁGENES SOFASCORE ───────────────────────────────────────────────
SOFA_IMG_EQUIPO = "https://api.sofascore.app/api/v1/team/{id}/image"
SOFA_IMG_TORNEO = "https://api.sofascore.app/api/v1/unique-tournament/{id}/image/dark"

# ─── DICCIONARIOS Y ALIAS ────────────────────────────────────────────────────
LEET_DICT = {"@": "A", "€": "E", "¡": "I", "|": "I", "Ø": "O", "$": "S", "ñ": "N", "Ñ": "N"}

ALIAS_EQUIPOS = {
    "MAN CITY": "MANCHESTER CITY", "MAN UTD": "MANCHESTER UNITED", "MAN U": "MANCHESTER UNITED",
    "PARIS SG": "PARIS SAINT GERMAIN", "PSG": "PARIS SAINT GERMAIN", "INTER MILAN": "INTERNAZIONALE",
    "AC MILAN": "MILAN", "SPURS": "TOTTENHAM", "JUVE": "JUVENTUS", "NEWCASTLE UTD": "NEWCASTLE",
    "ATM": "ATLETICO MADRID", "BARCA": "BARCELONA", "FCB": "BARCELONA", "BETIS": "REAL BETIS",
    "SANTA FE": "INDEPENDIENTE SANTA FE", "NACIONAL": "ATLETICO NACIONAL", "AMERICA": "AMERICA CALI",
    "DEP CALI": "DEPORTIVO CALI", "LAKERS": "LOS ANGELES LAKERS", "LAL": "LOS ANGELES LAKERS",
    "WARRIORS": "GOLDEN STATE WARRIORS", "GSW": "GOLDEN STATE WARRIORS", "CELTICS": "BOSTON CELTICS",
    "BOS": "BOSTON CELTICS", "BUCKS": "MILWAUKEE BUCKS", "NETS": "BROOKLYN NETS",
    "CLIPPERS": "LOS ANGELES CLIPPERS", "RED BULL RACING": "RED BULL", "REDBULL": "RED BULL",
    "RBR": "RED BULL", "MERCEDES AMG": "MERCEDES", "FERRARI F1": "FERRARI",
}

PALABRAS_GENERICAS = {"WOMEN", "MEN", "CUP", "LEAGUE", "LIVE", "FHD", "4K", "1080P", "720P", "1080", "720", "480", "CHAMPIONSHIP", "TOUR", "QUALIFICATION", "TV", "SPORTS", "VS", "STADIUM", "CANCHA", "HD", "SD", "UHD", "PREMIUM"}
STOP_WORDS = {"FC", "SC", "CF", "AC", "AS", "US", "CS", "RC", "CD", "SD", "UD", "THE", "DE", "LA", "LAS", "LOS", "EL", "Y", "E", "AND", "OF", "DEL", "CENTRAL", "CITY", "UNITED", "REAL", "CLUB", "ATLETICO", "DEPORTIVO", "SPORTING"}

# ─── REGLAS DE NEGOCIO Y FILTROS ESTRICTOS ───────────────────────────────────
TIER_1_ELITE = ["CHAMPIONS LEAGUE", "UEFA CHAMPIONS", "LIGA BETPLAY", "LA LIGA", "PREMIER LEAGUE", "SERIE A", "FORMULA 1", "NBA", "UFC", "LIBERTADORES", "COPA AMERICA", "MUNDIAL", "WORLD CUP", "ATP", "WTA", "GRAND SLAM", "WIMBLEDON", "ROLAND GARROS", "US OPEN", "AUSTRALIAN OPEN", "MOTO GP", "MOTOGP", "SIX NATIONS", "RUGBY CHAMPIONSHIP", "COPA DEL REY", "FA CUP", "SUPER BOWL", "BOXING WORLD"]
TIER_2_NICHO = ["NFL", "MLB", "F2", "F3", "COPA SUDAMERICANA", "EREDIVISIE", "BUNDESLIGA", "LIGUE 1", "CHAMPIONS CUP", "SUPER RUGBY", "NATIONS LEAGUE", "RECOPA", "SUPERLIGA"]
PAISES_HEROE_LOCAL = {"Colombia", "Spain", "CO", "ES"}

DEPORTES_IDS = {"Fútbol": 1, "Baloncesto": 2, "Tenis": 5, "Motor": 22, "Béisbol": 64, "Rugby": 12, "Boxeo": 9, "Voleibol": 23, "MMA": 30}
DURACION_POR_DEPORTE = {"Fútbol": 120, "Baloncesto": 150, "Tenis": 180, "Motor": 210, "Béisbol": 210, "Rugby": 120, "Boxeo": 180, "Voleibol": 150, "MMA": 200}
UMBRAL_POR_DEPORTE = {"Fútbol": 70, "Baloncesto": 65, "Tenis": 60, "Motor": 50, "Béisbol": 65, "Rugby": 65, "Boxeo": 60, "Voleibol": 65, "MMA": 55}

# FILTRO ESCUDO: Evita que el script consulte endpoints de ligas irrelevantes
CATEGORIAS_PERMITIDAS_POR_DEPORTE = {
    "Fútbol": {"Colombia", "Spain", "World", "Europe", "South America", "North & Central America", "England", "Italy", "Germany", "France", "USA", "Argentina", "Brazil", "Mexico", "Saudi Arabia"},
    "Baloncesto": {"USA", "World", "Europe", "Spain"},
    "Tenis": {"ATP", "WTA", "World"},
    "Béisbol": {"USA", "World"},
    "Rugby": {"World", "Europe", "England", "France", "New Zealand", "Australia", "South Africa", "Argentina"},
    # Para deportes de nicho, dejamos un set vacío que actuará como comodín (se permiten todas)
    "Motor": set(), "Boxeo": set(), "Voleibol": set(), "MMA": set()
}

# ─── UTILIDADES ──────────────────────────────────────────────────────────────
def normalizar_texto(texto: str) -> str:
    if not texto: return ""
    texto = str(texto).upper()
    for leet, real in LEET_DICT.items(): texto = texto.replace(leet, real)
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^A-Z0-9\s:]", " ", texto).split())

def aplicar_alias(texto: str) -> str:
    for alias, expansion in sorted(ALIAS_EQUIPOS.items(), key=lambda x: -len(x[0])):
        texto = re.sub(r"\b" + re.escape(alias) + r"\b", expansion, texto)
    return texto

def extraer_keywords(texto: str) -> list:
    return [p for p in normalizar_texto(texto).split() if p not in STOP_WORDS and p not in PALABRAS_GENERICAS and len(p) > 2]

def url_logo_equipo(team_id) -> str: return SOFA_IMG_EQUIPO.format(id=team_id) if team_id else ""
def url_logo_torneo(unique_id) -> str: return SOFA_IMG_TORNEO.format(id=unique_id) if unique_id else ""

# ─── RED ─────────────────────────────────────────────────────────────────────
def hacer_peticion_rotativa(url: str, params: dict):
    global indice_llave_actual
    intentos = 0
    while intentos < len(LLAVES_API):
        llave = LLAVES_API[indice_llave_actual]
        try:
            r = requests.get(url, headers={"x-rapidapi-key": llave, "x-rapidapi-host": RAPIDAPI_HOST}, params=params, timeout=15)
            if r.status_code == 429:
                log.warning(f"Llave {indice_llave_actual + 1} agotada. Rotando...")
                indice_llave_actual = (indice_llave_actual + 1) % len(LLAVES_API)
                intentos += 1
                time.sleep(1)
                continue
            return r
        except Exception as e:
            log.error(f"Error de red: {e}")
            return None
    return None

# ─── XTREAM ──────────────────────────────────────────────────────────────────
def obtener_datos_xtream() -> list:
    log.info("Descargando Xtream...")
    url_base = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
    try:
        r_cat = requests.get(f"{url_base}&action=get_live_categories", timeout=15)
        r_str = requests.get(f"{url_base}&action=get_live_streams", timeout=30)
        categorias = {str(c.get("category_id", "")): c.get("category_name", "") for c in r_cat.json()}
        
        streams = []
        for s in r_str.json():
            cat_name = categorias.get(str(s.get("category_id", "")), "")
            stream_name = s.get("name", "")
            texto_final = aplicar_alias(normalizar_texto(f"{cat_name} {stream_name}"))
            streams.append({"id": s.get("stream_id"), "nombre_ui": stream_name.strip(), "texto_analisis": texto_final})
        log.info(f"Cargados {len(streams)} streams.")
        return streams
    except Exception as e:
        log.error(f"Error parseando Xtream: {e}")
        return []

# ─── SOFASCORE ───────────────────────────────────────────────────────────────
def obtener_eventos_api() -> list:
    if not LLAVES_API: return []
    eventos_procesados = []

    for deporte, sport_id in DEPORTES_IDS.items():
        # 1. Obtenemos las categorías que tienen eventos HOY para este deporte
        r_cat = hacer_peticion_rotativa(f"https://{RAPIDAPI_HOST}/v1/calendar/categories", {"sport_id": sport_id, "date": FECHA_HOY, "timezone": -5})
        if not r_cat or r_cat.status_code != 200: continue
        
        categorias_activas = r_cat.json().get("data", [])
        filtro_permitidas = CATEGORIAS_PERMITIDAS_POR_DEPORTE.get(deporte, set())

        for cat in categorias_activas:
            cat_id = cat.get("category", {}).get("id")
            cat_nombre = cat.get("category", {}).get("name", "")

            # ESCUDO: Si el filtro no está vacío y la categoría no está en la lista, la saltamos.
            if filtro_permitidas and cat_nombre not in filtro_permitidas:
                continue

            # 2. Consultamos solo los eventos de las categorías aprobadas
            r_ev = hacer_peticion_rotativa(f"https://{RAPIDAPI_HOST}/v1/events/schedule/category", {"category_id": cat_id, "date": FECHA_HOY})
            if not r_ev or r_ev.status_code != 200: continue

            for ev in r_ev.json().get("data", []):
                try:
                    torneo_data = ev.get("tournament", {})
                    cat_data = torneo_data.get("category", {})
                    torneo_nombre = torneo_data.get("name", "")
                    pais_evento = cat_data.get("name", "")
                    pais_codigo = cat_data.get("alpha2", "")
                    unique_id = torneo_data.get("uniqueTournament", {}).get("id")

                    torneo_norm = normalizar_texto(torneo_nombre)
                    if deporte == "Tenis" and "ITF" in torneo_norm and not any(t in torneo_norm for t in TIER_1_ELITE + TIER_2_NICHO):
                        continue

                    home_data, away_data = ev.get("homeTeam", {}), ev.get("awayTeam", {})
                    eq_local, eq_visit = home_data.get("name", ""), away_data.get("name", "")
                    
                    unix_time = ev.get("startTimestamp")
                    if not unix_time: continue

                    dt_utc = datetime.fromtimestamp(unix_time, timezone.utc)
                    hora_utc = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                    hora_corta = dt_utc.astimezone(ZONA_COLOMBIA).strftime("%H:%M")

                    tier = 3
                    if any(t in torneo_norm for t in TIER_1_ELITE): tier = 1
                    elif any(t in torneo_norm for t in TIER_2_NICHO): tier = 2
                    if pais_evento in PAISES_HEROE_LOCAL or pais_codigo in PAISES_HEROE_LOCAL: tier = min(tier, 2)

                    es_duelo = bool(eq_local and eq_visit)
                    eventos_procesados.append({
                        "id": str(ev.get("id")),
                        "titulo": f"{eq_local} vs {eq_visit}" if es_duelo else torneo_nombre,
                        "torneo": torneo_nombre,
                        "categoria": deporte,
                        "hora_utc": hora_utc,
                        "logo_local": url_logo_equipo(home_data.get("id")) if es_duelo else url_logo_torneo(unique_id),
                        "logo_visitante": url_logo_equipo(away_data.get("id")) if es_duelo else "",
                        "banner": url_logo_torneo(unique_id),
                        "tier": tier,
                        "duracion_min": DURACION_POR_DEPORTE.get(deporte, 150),
                        "_hora_corta": hora_corta,
                        "_kws_local": extraer_keywords(eq_local),
                        "_kws_visit": extraer_keywords(eq_visit),
                        "_kws_torneo": extraer_keywords(torneo_nombre),
                    })
                except Exception:
                    continue
            time.sleep(0.3)
        time.sleep(0.5)

    log.info(f"Total API: {len(eventos_procesados)} eventos relevantes.")
    return eventos_procesados

# ─── MATCHING ────────────────────────────────────────────────────────────────
def evaluar_vinculo(evento: dict, texto_canal: str) -> int:
    puntaje, texto_pad = 0, f" {texto_canal} "
    if f" {evento['_hora_corta']} " in texto_pad: puntaje += 30
    
    c_local = sum(1 for kw in evento["_kws_local"] if f" {kw} " in texto_pad)
    c_visit = sum(1 for kw in evento["_kws_visit"] if f" {kw} " in texto_pad)
    c_torneo = sum(1 for kw in evento["_kws_torneo"] if f" {kw} " in texto_pad)

    if c_local > 0: puntaje += 35
    if c_visit > 0: puntaje += 35
    if c_torneo > 0: puntaje += 15

    if c_local == 0 and c_visit == 0 and puntaje >= 30 and c_torneo >= 2: puntaje += 40
    if len(evento["_kws_torneo"]) > 1 and c_torneo == 1 and c_local == 0 and c_visit == 0: puntaje -= 15
    return puntaje

def buscar_fuentes(evento: dict, streams: list) -> list:
    umbral = UMBRAL_POR_DEPORTE.get(evento["categoria"], 70)
    return [{"nombre": s["nombre_ui"], "url": f"{XTREAM_URL}/live/{XTREAM_USER}/{XTREAM_PASS}/{s['id']}.ts"} 
            for s in streams if evaluar_vinculo(evento, s["texto_analisis"]) >= umbral]

# ─── ORQUESTADOR ─────────────────────────────────────────────────────────────
def main():
    log.info(f"Iniciando Curador Optimizado | Fecha: {FECHA_HOY}")
    streams = obtener_datos_xtream()
    eventos = obtener_eventos_api()
    if not streams or not eventos: return

    resultados = []
    desglose = {d: 0 for d in DEPORTES_IDS}

    for ev in eventos:
        fuentes = buscar_fuentes(ev, streams)
        if fuentes:
            desglose[ev["categoria"]] += 1
            resultados.append({
                "id": ev["id"], "titulo": ev["titulo"], "torneo": ev["torneo"],
                "categoria": ev["categoria"], "hora_utc": ev["hora_utc"],
                "logo_local": ev["logo_local"], "logo_visitante": ev["logo_visitante"],
                "banner": ev["banner"], "tier": ev["tier"], "duracion_min": ev["duracion_min"],
                "fuentes": fuentes
            })

    resultados.sort(key=lambda x: x["hora_utc"])

    with open("eventos_hoy.json", "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)

    with open("meta_curador.json", "w", encoding="utf-8") as f:
        json.dump({"generado_en": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "eventos": len(resultados), "desglose": desglose}, f, indent=2)

    log.info(f"Proceso completado: {len(resultados)} eventos vinculados con éxito.")

if __name__ == "__main__":
    main()
