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

# ─── EXCEPCIONES Y ALIAS DE TORNEOS ──────────────────────────────────────────
# Permite que palabras de 2 caracteres sobrevivan al filtro si son vitales
PALABRAS_CORTAS_PERMITIDAS = {"F1", "F2", "F3", "GP", "Q1", "Q2", "Q3", "M1", "M2", "M3"}

# Traductor universal: Nombre formal de la API -> Alias en los canales de TV
ALIAS_TORNEOS = {
    "FORMULA 1": ["F1"],
    "FORMULA 2": ["F2"],
    "FORMULA 3": ["F3"],
    "MOTO GP": ["GP"],
    "MOTOGP": ["GP"],
    "WORLD PADEL TOUR": ["WPT"],
    "CHAMPIONS LEAGUE": ["UCL"],
    "ALL ELITE WRESTLING": ["AEW"],
    "BARE KNUCKLE": ["BKFC"]
}

# ─── REGLAS DE NEGOCIO Y FILTROS ESTRICTOS ───────────────────────────────────
TIER_1_ELITE = ["CHAMPIONS LEAGUE", "UEFA CHAMPIONS", "LIGA BETPLAY", "LA LIGA", "PREMIER LEAGUE", "SERIE A", "FORMULA 1", "NBA", "UFC", "LIBERTADORES", "COPA AMERICA", "MUNDIAL", "WORLD CUP", "ATP", "WTA", "GRAND SLAM", "WIMBLEDON", "ROLAND GARROS", "US OPEN", "AUSTRALIAN OPEN", "MOTO GP", "MOTOGP", "SIX NATIONS", "RUGBY CHAMPIONSHIP", "COPA DEL REY", "FA CUP", "SUPER BOWL", "BOXING WORLD"]
TIER_2_NICHO = ["NFL", "MLB", "F2", "F3", "COPA SUDAMERICANA", "EREDIVISIE", "BUNDESLIGA", "LIGUE 1", "CHAMPIONS CUP", "SUPER RUGBY", "NATIONS LEAGUE", "RECOPA", "SUPERLIGA"]
PAISES_HEROE_LOCAL = {"Colombia", "Spain", "CO", "ES"}

DEPORTES_IDS = {"Fútbol": 1, "Baloncesto": 2, "Tenis": 5, "Motor": 22, "Béisbol": 64, "Rugby": 12, "Boxeo": 9, "Voleibol": 23, "MMA": 30}
DURACION_POR_DEPORTE = {"Fútbol": 120, "Baloncesto": 150, "Tenis": 180, "Motor": 210, "Béisbol": 210, "Rugby": 120, "Boxeo": 180, "Voleibol": 150, "MMA": 200}
UMBRAL_POR_DEPORTE = {"Fútbol": 70, "Baloncesto": 65, "Tenis": 60, "Motor": 65, "Béisbol": 65, "Rugby": 65, "Boxeo": 65, "Voleibol": 65, "MMA": 65} # Umbrales subidos para mayor seguridad

# FILTRO DE PAÍSES/REGIONES (Primer Escudo)
CATEGORIAS_PERMITIDAS_POR_DEPORTE = {
    "Fútbol": {"Colombia", "Spain", "World", "Europe", "South America", "North & Central America", "England", "Italy", "Germany", "France", "USA", "Argentina", "Brazil", "Mexico", "Saudi Arabia"},
    "Baloncesto": {"USA", "World", "Europe", "Spain"},
    "Tenis": {"ATP", "WTA", "World"},
    "Béisbol": {"USA", "World"},
    "Rugby": {"World", "Europe", "England", "France", "New Zealand", "Australia", "South Africa", "Argentina"},
    "Motor": set(), "Boxeo": set(), "Voleibol": set(), "MMA": set() # Se controlan por Listas Blancas
}

# FILTRO DE TORNEOS (Segundo Escudo - Listas Blancas)
WHITELIST_TORNEOS = {
    "Motor": ["FORMULA 1", "F1", "FORMULA 2", "F2", "FORMULA 3", "F3", "FORMULA E", "MOTOGP", "MOTO 2", "MOTO 3", "NASCAR", "INDYCAR", "WRC", "RALLY", "DAKAR", "WEC", "SUPERBIKE"],
    "MMA": ["UFC", "BELLATOR", "PFL", "ONE CHAMPIONSHIP", "KSW"],
    "Boxeo": ["BOXING", "BOXEO", "TOP RANK", "MATCHROOM", "GOLDEN BOY", "GLORY"],
    "Tenis": ["ATP", "WTA", "GRAND SLAM", "DAVIS CUP", "WIMBLEDON", "ROLAND GARROS", "US OPEN", "AUSTRALIAN OPEN", "PREMIER PADEL", "WORLD PADEL TOUR", "A1 PADEL", "WTT", "TABLE TENNIS", "PING PONG"]
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
    for alias, expansion in sorted(ALIAS_EQUIPOS.items(), key=lambda x: -len(x)):
        texto = re.sub(r"\b" + re.escape(alias) + r"\b", expansion, texto)
    return texto

def extraer_keywords(texto: str) -> list:
    return [
        p for p in normalizar_texto(texto).split() 
        if p not in STOP_WORDS 
        and p not in PALABRAS_GENERICAS 
        and (len(p) > 2 or p in PALABRAS_CORTAS_PERMITIDAS)
    ]

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
    ahora_utc = datetime.now(timezone.utc) # Referencia para el Recolector de Basura

    for deporte, sport_id in DEPORTES_IDS.items():
        r_cat = hacer_peticion_rotativa(f"https://{RAPIDAPI_HOST}/v1/calendar/categories", {"sport_id": sport_id, "date": FECHA_HOY, "timezone": -5})
        if not r_cat or r_cat.status_code != 200: continue
        
        categorias_activas = r_cat.json().get("data", [])
        filtro_paises = CATEGORIAS_PERMITIDAS_POR_DEPORTE.get(deporte, set())
        lista_blanca_torneos = WHITELIST_TORNEOS.get(deporte, [])

        for cat in categorias_activas:
            cat_id = cat.get("category", {}).get("id")
            cat_nombre = cat.get("category", {}).get("name", "")

            # ESCUDO 1: Países / Regiones
            if filtro_paises and cat_nombre not in filtro_paises:
                continue

            r_ev = hacer_peticion_rotativa(f"https://{RAPIDAPI_HOST}/v1/events/schedule/category", {"category_id": cat_id, "date": FECHA_HOY})
            if not r_ev or r_ev.status_code != 200: continue

            for ev in r_ev.json().get("data", []):
                try:
                    torneo_data = ev.get("tournament", {})
                    cat_data = torneo_data.get("category", {})
                    torneo_nombre = torneo_data.get("name", "")
                    torneo_norm = normalizar_texto(torneo_nombre)
                    
                    # Extraemos el país o la categoría principal (Vital porque aquí vienen "ATP", "WTA", etc.)
                    pais_evento = cat_data.get("name", "")
                    pais_evento_norm = normalizar_texto(pais_evento)

                    # ESCUDO 2: Listas Blancas (Busca en el nombre del torneo Y en el nombre de la categoría)
                    if lista_blanca_torneos and not any(kw in torneo_norm for kw in lista_blanca_torneos) and not any(kw in pais_evento_norm for kw in lista_blanca_torneos):
                        continue
                    
                    # Filtro específico para Tenis (Excluir ITF de bajo nivel)
                    if deporte == "Tenis" and "ITF" in torneo_norm and not any(t in torneo_norm for t in TIER_1_ELITE + TIER_2_NICHO):
                        continue

                    unix_time = ev.get("startTimestamp")
                    if not unix_time: continue

                    duracion = DURACION_POR_DEPORTE.get(deporte, 150)
                    dt_utc = datetime.fromtimestamp(unix_time, timezone.utc)
                    hora_fin_evento = dt_utc + timedelta(minutes=duracion)

                    # RECOLECTOR DE BASURA: Omitir eventos finalizados
                    if hora_fin_evento < ahora_utc:
                        continue

                    pais_codigo = cat_data.get("alpha2", "")
                    unique_id = torneo_data.get("uniqueTournament", {}).get("id")
                    
                    # Extracción del nombre específico de la carrera/pelea (Vital para deportes sin equipos)
                    nombre_evento_api = ev.get("name", "")
                    description_api = ev.get("description", "")
                    
                    home_data, away_data = ev.get("homeTeam", {}), ev.get("awayTeam", {})
                    eq_local, eq_visit = home_data.get("name", ""), away_data.get("name", "")
                    
                    hora_utc_str = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                    hora_corta = dt_utc.astimezone(ZONA_COLOMBIA).strftime("%H:%M")

                    tier = 3
                    if any(t in torneo_norm for t in TIER_1_ELITE): tier = 1
                    elif any(t in torneo_norm for t in TIER_2_NICHO): tier = 2
                    if pais_evento in PAISES_HEROE_LOCAL or pais_codigo in PAISES_HEROE_LOCAL: tier = min(tier, 2)

                    es_duelo = bool(eq_local and eq_visit)
                    
                    # TÍTULO INTELIGENTE: Si no hay equipos, usa el nombre de la carrera. Si no hay carrera, usa el torneo.
                    if es_duelo:
                        titulo_final = f"{eq_local} vs {eq_visit}"
                    elif nombre_evento_api:
                        titulo_final = nombre_evento_api
                    elif description_api:
                        titulo_final = description_api
                    else:
                        titulo_final = torneo_nombre

                    # GENERACIÓN DE PALABRAS CLAVE DINÁMICA
                    kws_locales = extraer_keywords(eq_local) if es_duelo else extraer_keywords(titulo_final)
                    kws_visitantes = extraer_keywords(eq_visit) if es_duelo else []
                    
                    # Inyectamos el nombre de la categoría ("ATP", "WTA") en las keywords del torneo
                    kws_torneo = extraer_keywords(torneo_nombre) + extraer_keywords(pais_evento)
                    
                    # INYECCIÓN DEL DICCIONARIO DE ALIAS PARA TORNEOS
                    for oficial, alias_list in ALIAS_TORNEOS.items():
                        if oficial in torneo_norm:
                            kws_torneo.extend(alias_list)

                    eventos_procesados.append({
                        "id": str(ev.get("id")),
                        "titulo": titulo_final,
                        "torneo": torneo_nombre,
                        "categoria": deporte,
                        "hora_utc": hora_utc_str,
                        "logo_local": url_logo_equipo(home_data.get("id")) if es_duelo else url_logo_torneo(unique_id),
                        "logo_visitante": url_logo_equipo(away_data.get("id")) if es_duelo else "",
                        "banner": url_logo_torneo(unique_id),
                        "tier": tier,
                        "duracion_min": duracion,
                        "_hora_corta": hora_corta,
                        "_kws_local": kws_locales,
                        "_kws_visit": kws_visitantes,
                        "_kws_torneo": kws_torneo,
                    })
                except Exception:
                    continue
            time.sleep(0.3)
        time.sleep(0.5)

    log.info(f"Total API: {len(eventos_procesados)} eventos vigentes y relevantes.")
    return eventos_procesados

# ─── MATCHING (MOTOR ANTI-CLONES) ─────────────────────────────────────────────
def evaluar_vinculo(evento: dict, texto_canal: str) -> int:
    puntaje = 0
    # Añadimos espacios para asegurar palabras completas
    texto_pad = f" {texto_canal} "
    
    # 1. Búsqueda de la Hora (No consume la palabra porque un canal puede tener "14:00 14:00")
    if f" {evento['_hora_corta']} " in texto_pad: 
        puntaje += 30
        
    # 2. Búsqueda y Consumo de Palabras del Local / Evento Individual
    c_local = 0
    for kw in evento["_kws_local"]:
        kw_str = f" {kw} "
        if kw_str in texto_pad:
            c_local += 1
            texto_pad = texto_pad.replace(kw_str, "   ", 1) 
            
    # 3. Búsqueda y Consumo de Palabras del Visitante (Si es deporte individual, esto valdrá 0)
    c_visit = 0
    for kw in evento["_kws_visit"]:
        kw_str = f" {kw} "
        if kw_str in texto_pad:
            c_visit += 1
            texto_pad = texto_pad.replace(kw_str, "   ", 1)
            
    # 4. Búsqueda de Torneo y Alias (Ej. F1, GP, ATP)
    c_torneo = 0
    for kw in evento["_kws_torneo"]:
        kw_str = f" {kw} "
        if kw_str in texto_pad:
            c_torneo += 1
            texto_pad = texto_pad.replace(kw_str, "   ", 1)

    # ─── ASIGNACIÓN DE PUNTOS (FÚTBOL / DUELOS) ───
    if evento["_kws_visit"]: # Es un duelo (Tiene equipo visitante)
        if c_local > 0: puntaje += 35
        if c_visit > 0: puntaje += 35
        if c_torneo > 0: puntaje += 15
        
        # Regla de canal contenedor para duelos (Sin equipos, pero con hora y nombre del torneo)
        if c_local == 0 and c_visit == 0 and puntaje >= 30 and c_torneo >= 2: 
            puntaje += 40

    # ─── ASIGNACIÓN DE PUNTOS (MOTOR / GOLF / TENIS INDIVIDUAL) ───
    else: 
        # En deportes individuales, "_kws_local" guarda el nombre de la carrera/pelea.
        # Si el canal tiene la hora exacta (+30) y al menos el alias del torneo (ej. F1) o una palabra de la carrera.
        if puntaje >= 30:
            # Multiplicamos el peso de las palabras encontradas para compensar la falta de equipos
            puntaje += (c_local * 20) + (c_torneo * 25)
            
            # Bonificación extra si encontró tanto el torneo como el evento (Match perfecto)
            if c_local > 0 and c_torneo > 0:
                puntaje += 20

    # Penalización por falso positivo de torneo en cualquier deporte
    if len(evento["_kws_torneo"]) > 1 and c_torneo == 1 and c_local == 0 and c_visit == 0: 
        puntaje -= 15
        
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
        json.dump({"generado_en": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "eventos_vigentes": len(resultados), "desglose": desglose}, f, indent=2)

    log.info(f"Proceso completado: {len(resultados)} eventos vinculados con éxito.")

if __name__ == "__main__":
    main()
