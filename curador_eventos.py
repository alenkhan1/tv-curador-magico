import os
import re
import json
import time
import logging
import requests
import unicodedata
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher

# ─── LOGGING ESTRUCTURADO ────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("curador")

# ─── CONFIGURACIÓN DE ENTORNO Y LLAVES DINÁMICAS ─────────────────────────────
XTREAM_URL   = os.environ.get("XTREAM_URL")
XTREAM_USER  = os.environ.get("XTREAM_USER")
XTREAM_PASS  = os.environ.get("XTREAM_PASS")
RAPIDAPI_HOST = "sofasport.p.rapidapi.com"

ZONA_COLOMBIA = timezone(timedelta(hours=-5))
FECHA_HOY     = datetime.now(ZONA_COLOMBIA).strftime("%Y-%m-%d")
ARCHIVO_CACHE = "agenda_api_v7.json"
HORAS_CACHE   = 4

# Lector Dinámico: Atrapa cualquier variable que empiece con "RAPIDAPI_KEY_"
LLAVES_ENV = [val for key, val in os.environ.items() if key.startswith("RAPIDAPI_KEY_") and val]
LLAVES_API = list(set(LLAVES_ENV)) # Elimina duplicados si los hay
indice_llave_actual = 0

if not LLAVES_API:
    log.warning("⚠️ No se han detectado llaves de RapidAPI en las variables de entorno.")
else:
    log.info(f"✅ Se han cargado {len(LLAVES_API)} llaves de RapidAPI para la rotación.")

# ─── DICCIONARIOS Y CONFIGURACIÓN ────────────────────────────────────────────
SOFA_IMG_EQUIPO = "https://api.sofascore.app/api/v1/team/{id}/image"
SOFA_IMG_TORNEO = "https://api.sofascore.app/api/v1/unique-tournament/{id}/image/dark"

DEPORTES_MAP = {
    "Fútbol": 1, "Baloncesto": 2, "Tenis": 5, "Motor": 22, 
    "Béisbol": 64, "Boxeo": 9, "MMA": 30, "Golf": 18, 
    "Hockey": 4, "Voleibol": 23, "Rugby": 12, "Fútbol Americano": 14
}

DURACION_POR_DEPORTE = {
    "Fútbol": 120, "Baloncesto": 150, "Tenis": 180, "Motor": 210, 
    "Béisbol": 210, "Rugby": 120, "Boxeo": 180, "Voleibol": 150, "MMA": 200, "Golf": 240, "Hockey": 150
}

TRADUCTOR_JERGA = {
    "F1": "FORMULA 1", "MOTO GP": "MOTOGP", "UCL": "CHAMPIONS LEAGUE", 
    "LIV": "LIV GOLF", "PGA": "PGA TOUR", "PREMIER": "PREMIER LEAGUE"
}

# (Paso 4) Ampliación de Categorías y Logos de Rescate
CATEGORIAS_RESCATE = {
    "Motor": ["F1", "F2", "F3", "FORMULA", "NASCAR", "CARRERA", "GP ", "MOTOGP", "RALLY", "INDYCAR", "SPRINT"],
    "Béisbol": ["MLB", "LMB", "BEISBOL", "BASEBALL", "DIAMONDBACKS", "CUBS", "YANKEES", "RED SOX", "DODGERS", "PIRATES", "REDS", "ASTROS", "RANGERS", "PADRES", "GIANTS", "MARINERS"],
    "Baloncesto": ["NBA", "WNBA", "BASKETBALL", "LAKERS", "CELTICS", "BULLS", "CAVALIERS", "PISTONS", "MAGIC", "RAPTORS", "ENDESA", "EUROLEAGUE"],
    "Fútbol": ["LIGA", "SERIE A", "PREMIER", "FUTBOL", "SOCCER", "NWSL", "MLS", "CHAMPIONS", "LEAGUE", "SUDAMERICANA", "COPA", "BUNDESLIGA", "LIGUE 1"],
    "Lucha Libre": ["WWE", "SMACKDOWN", "RAW", "AEW", "LUCHA", "WRESTLING", "WRESTLEMANIA"],
    "Boxeo": ["BOXEO", "BOXING", "PESAJE", "FIGHT", "COMBATE", "RING"],
    "MMA": ["UFC", "MMA", "BELLATOR", "ONE CHAMPIONSHIP", "FIGHT PASS"],
    "Golf": ["PGA", "LIV", "GOLF", "MASTERS", "OPEN"],
    "Hockey": ["NHL", "SABRES", "BRUINS", "CANADIENS", "LIGHTNING", "HOCKEY", "STANLEY"],
    "Tenis": ["ATP", "WTA", "TENIS", "TENNIS", "WIMBLEDON", "OPEN", "PADEL", "PING PONG"],
    "Fútbol Americano": ["NFL", "FOOTBALL", "SUPER BOWL", "NCAA"]
}

LOGOS_RESCATE = {
    "Motor": "https://img.icons8.com/color/512/f1-car.png",
    "Béisbol": "https://img.icons8.com/color/512/baseball.png",
    "Baloncesto": "https://img.icons8.com/color/512/basketball.png",
    "Fútbol": "https://img.icons8.com/color/512/football2.png",
    "Lucha Libre": "https://img.icons8.com/color/512/wrestling.png",
    "Boxeo": "https://img.icons8.com/color/512/boxing-glove.png",
    "MMA": "https://img.icons8.com/color/512/boxing-glove.png",
    "Golf": "https://img.icons8.com/color/512/golf.png",
    "Hockey": "https://img.icons8.com/color/512/ice-hockey.png",
    "Tenis": "https://img.icons8.com/color/512/tennis.png",
    "Fútbol Americano": "https://img.icons8.com/color/512/american-football.png",
    "Deportes": "https://img.icons8.com/color/512/stadium.png"
}

# ─── UTILIDADES ──────────────────────────────────────────────────────────────
def limpiar_texto_para_match(texto: str) -> str:
    if not texto: return ""
    texto = str(texto).upper()
    
    texto = re.sub(r'\(.*?\)', '', texto)
    texto = re.sub(r'\[.*?\]', '', texto)
    texto = re.sub(r'\b\d{1,2}:\d{2}\b(\s*[AP]M)?(\s*ET)?', '', texto)
    texto = re.sub(r'\b(HD|SD|FHD|4K|ENG|ESP|GER|OPC\.\d+|OP\d+|PEA \d+|PARA\+ \d+|VIVO|LIVE|COMPACTO|REPETICION|RESUMEN)\b', '', texto)
    
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^A-Z0-9\s]", " ", texto) 
    
    for corto, largo in TRADUCTOR_JERGA.items():
        texto = re.sub(rf"\b{corto}\b", largo, texto)
        
    palabras = [p for p in texto.split() if len(p) > 2 and p not in ["THE", "AND", "DEL", "LAS", "LOS", "VS", "EN", "EL", "DE", "LA"]]
    return " ".join(palabras)

def calcular_similitud_interseccion(xtream_limpio: str, api_limpio: str) -> float:
    if not xtream_limpio or not api_limpio: return 0.0
    words_x = set(xtream_limpio.split())
    words_a = set(api_limpio.split())
    if not words_x or not words_a: return 0.0
    
    comunes = words_x.intersection(words_a)
    if len(comunes) >= 2: return 85.0
    if len(comunes) == 1 and len(list(comunes)[0]) >= 5:
        if SequenceMatcher(None, xtream_limpio, api_limpio).ratio() * 100 > 40.0:
            return 70.0
            
    ratio_difuso = SequenceMatcher(None, xtream_limpio, api_limpio).ratio() * 100
    if ratio_difuso > 65.0: return ratio_difuso
    return 0.0

def adivinar_categoria_y_logo(texto: str):
    texto_upper = texto.upper()
    for categoria, keywords in CATEGORIAS_RESCATE.items():
        if any(kw in texto_upper for kw in keywords):
            return categoria, LOGOS_RESCATE[categoria]
    return "Deportes", LOGOS_RESCATE["Deportes"]

def calcular_hora_utc_falsa(nombre_canal: str) -> str:
    # Intenta atrapar la hora real del título
    match = re.search(r'\b(\d{1,2}):(\d{2})\b\s*(PM)?', nombre_canal, re.IGNORECASE)
    if match:
        try:
            h, m = int(match.group(1)), int(match.group(2))
            pm = match.group(3)
            if pm and h < 12: h += 12
            dt_evento = datetime.now(ZONA_COLOMBIA).replace(hour=h, minute=m, second=0, microsecond=0)
            return dt_evento.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except: pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def crear_id_seguro(titulo: str) -> str:
    import hashlib
    hash_obj = hashlib.md5(titulo.encode('utf-8'))
    return hash_obj.hexdigest()[:12]

# ─── RED Y CACHÉ (Paso 2: Anti-ráfagas y Auto-limpieza de Llaves) ────────────
def hacer_peticion_segura(url: str, params: dict):
    global LLAVES_API, indice_llave_actual
    if not LLAVES_API: return None
        
    intentos = 0
    max_intentos = len(LLAVES_API) * 2 
    backoff = 2 # Segundos de espera iniciales si hay Rate Limit
    
    while intentos < max_intentos and LLAVES_API:
        llave = LLAVES_API[indice_llave_actual]
        try:
            time.sleep(1.5) # Pausa base estricta para evitar bloqueos
            r = requests.get(url, headers={"x-rapidapi-key": llave, "x-rapidapi-host": RAPIDAPI_HOST}, params=params, timeout=15)
            
            if r.status_code == 200:
                return r
                
            elif r.status_code == 429: # Rate Limit
                log.warning(f"Llave {indice_llave_actual + 1} dio 429 (Límite vel.). Esperando {backoff}s...")
                time.sleep(backoff)
                backoff = min(backoff * 2, 8) # Retroceso exponencial
                indice_llave_actual = (indice_llave_actual + 1) % len(LLAVES_API)
                intentos += 1
                continue
                
            elif r.status_code == 403: # Cuota mensual agotada
                log.error(f"🚨 Llave {indice_llave_actual + 1} AGOTADA (403). Eliminándola de la rotación.")
                LLAVES_API.pop(indice_llave_actual) # Elimina la llave muerta
                if not LLAVES_API:
                    log.error("Todas las llaves se han agotado.")
                    return None
                indice_llave_actual = indice_llave_actual % len(LLAVES_API)
                continue
                
            else:
                return r # Devuelve el 404 o 500 para que lo maneje el llamador
                
        except requests.exceptions.RequestException as e:
            log.warning(f"Error de conexión en llave {indice_llave_actual + 1}. Rotando...")
            indice_llave_actual = (indice_llave_actual + 1) % len(LLAVES_API)
            intentos += 1
            time.sleep(2)
            
    return None

def obtener_agenda_maestra() -> list:
    if os.path.exists(ARCHIVO_CACHE):
        tiempo_modificacion = os.path.getmtime(ARCHIVO_CACHE)
        if (time.time() - tiempo_modificacion) < (HORAS_CACHE * 3600):
            try:
                with open(ARCHIVO_CACHE, "r", encoding="utf-8") as f:
                    agenda = json.load(f)
                    if agenda: return agenda
            except Exception: pass

    log.info("Descargando Agenda Maestra de SofaScore...")
    eventos_api = []
    
    for deporte, sport_id in DEPORTES_MAP.items():
        log.info(f"Consultando agenda de {deporte}...")
        url = f"https://{RAPIDAPI_HOST}/v1/events/schedule/date"
        params = {"date": FECHA_HOY, "sport_id": sport_id, "timezone": -5}
        
        r_agenda = hacer_peticion_segura(url, params)
        
        if not r_agenda: continue

        # (Paso 3) Manejo silencioso y limpio de los 404
        if r_agenda.status_code == 404:
            if deporte in ["Boxeo", "MMA", "Golf", "Fútbol Americano"]:
                log.info(f"  └─ Sin agenda regular para {deporte} hoy (Comportamiento normal, se delegará a Rescate).")
            else:
                log.warning(f"  └─ No se pudo descargar la agenda de {deporte} (Error 404).")
            continue
            
        data = r_agenda.json().get("data", [])
        
        for ev in data:
            try:
                unix_time = ev.get("startTimestamp")
                if not unix_time: continue
                dt_utc = datetime.fromtimestamp(unix_time, timezone.utc)
                torneo = ev.get("tournament", {}).get("name", "")
                home_team = ev.get("homeTeam", {})
                away_team = ev.get("awayTeam", {})
                eq_local = home_team.get("name", "")
                eq_visit = away_team.get("name", "")
                
                titulo = f"{eq_local} vs {eq_visit}" if eq_local and eq_visit else ev.get("name", ev.get("description", ""))
                firma_texto = limpiar_texto_para_match(f"{torneo} {titulo}")

                if eq_local:
                    logo_local = SOFA_IMG_EQUIPO.format(id=home_team.get("id"))
                else:
                    logo_local = SOFA_IMG_TORNEO.format(id=ev.get("tournament", {}).get("uniqueTournament", {}).get("id"))
                    
                logo_visitante = SOFA_IMG_EQUIPO.format(id=away_team.get("id")) if eq_visit else ""

                eventos_api.append({
                    "id": str(ev.get("id")),
                    "titulo": titulo, "torneo": torneo, "categoria": deporte,
                    "firma_texto": firma_texto,
                    "hora_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "duracion_min": DURACION_POR_DEPORTE.get(deporte, 150),
                    "logo_local": logo_local, "logo_visitante": logo_visitante,
                    "tier": 2
                })
            except Exception: continue

    if eventos_api:
        with open(ARCHIVO_CACHE, "w", encoding="utf-8") as f:
            json.dump(eventos_api, f, ensure_ascii=False, indent=2)
    return eventos_api

# ─── XTREAM (Paso 1: Extracción Inteligente + Filtro de Contención) ──────────
def procesar_cubo_a() -> list:
    log.info("Analizando servidor Xtream...")
    base_url = XTREAM_URL.rstrip('/')
    api_url = f"{base_url}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
    
    cubo_a = []
    fecha_corta = FECHA_HOY[-5:] # "05-02"
    fecha_inversa = f"{FECHA_HOY[8:10]}/{FECHA_HOY[5:7]}" # "02/05"
    
    try:
        # Intento Primario: Buscar la categoría del día
        r_cat = requests.get(f"{api_url}&action=get_live_categories", timeout=15)
        categoria_id_hoy = None
        
        if r_cat.status_code == 200:
            for cat in r_cat.json():
                nombre_cat = cat.get("category_name", "").upper()
                if fecha_corta in nombre_cat or fecha_inversa in nombre_cat or "EVENTOS DIARIOS" in nombre_cat:
                    categoria_id_hoy = cat.get("category_id")
                    log.info(f"🎯 Categoría del día detectada: {nombre_cat} (ID: {categoria_id_hoy})")
                    break
        
        # Modo de descarga
        if categoria_id_hoy:
            url_streams = f"{api_url}&action=get_live_streams&category_id={categoria_id_hoy}"
        else:
            log.warning("⚠️ No se detectó categoría del día. Entrando en Modo Supervivencia (Fuerza Bruta)...")
            url_streams = f"{api_url}&action=get_live_streams"
            
        r_str = requests.get(url_streams, timeout=30)
        if r_str.status_code != 200: return []
        
        for s in r_str.json():
            nombre_canal = s.get("name", "").strip()
            
            # Filtro de Contención: Si estamos en modo supervivencia y el canal tiene fecha explícita vieja, lo descartamos
            if not categoria_id_hoy:
                 match_fecha = re.search(r'\b(\d{1,2})[-/](\d{1,2})\b', nombre_canal)
                 if match_fecha:
                     fecha_en_canal = f"{int(match_fecha.group(1)):02d}/{int(match_fecha.group(2)):02d}"
                     if fecha_en_canal != fecha_inversa:
                         continue # Canal zombie detectado. Descartar.

            if re.search(r'\b\d{1,2}:\d{2}\b', nombre_canal) or any(kw in nombre_canal.upper() for kw in ["PEA ", "PARA+", "LMB ", "MLB ", "UFC "]):
                texto_limpio = limpiar_texto_para_match(nombre_canal)
                if len(texto_limpio) > 4:
                    cubo_a.append({
                        "id_xtream": str(s.get("stream_id")),
                        "nombre_ui": nombre_canal,
                        "texto_limpio": texto_limpio
                    })
                    
        log.info(f"Cubo A listo: {len(cubo_a)} canales procesables.")
        return cubo_a
    except Exception as e:
        log.error(f"Error procesando Xtream: {e}")
        return []

# ─── ORQUESTADOR (MOTOR DE EMPAREJAMIENTO) ───────────────────────────────────
def main():
    log.info(f"=== Iniciando Curador Mágico V8 (Arquitectura Adaptativa) ===")
    agenda_api = obtener_agenda_maestra()
    cubo_a = procesar_cubo_a()
    
    if not cubo_a: 
        log.warning("No se detectaron eventos temporales en Xtream para procesar.")
        return

    resultados_finales = []
    base_url = XTREAM_URL.rstrip('/')

    for canal in cubo_a:
        texto_canal = canal["texto_limpio"]
        url_video = f"{base_url}/{XTREAM_USER}/{XTREAM_PASS}/{canal['id_xtream']}.ts"
        fuente_limpia = {"nombre": canal["nombre_ui"], "url": url_video}
        
        match_encontrado = False
        mejor_evento, mejor_puntaje = None, 0
        
        # Ruta A
        if agenda_api:
            for ev in agenda_api:
                puntaje = calcular_similitud_interseccion(texto_canal, ev["firma_texto"])
                if puntaje > mejor_puntaje:
                    mejor_puntaje, mejor_evento = puntaje, ev
            
            if mejor_puntaje > 55.0 and mejor_evento:
                match_encontrado = True
                evento_existente = next((item for item in resultados_finales if item["id"] == mejor_evento["id"]), None)
                if evento_existente:
                    if not any(f["url"] == url_video for f in evento_existente["fuentes"]):
                        evento_existente["fuentes"].append(fuente_limpia)
                else:
                    evento_clon = mejor_evento.copy()
                    evento_clon["fuentes"] = [fuente_limpia]
                    if "firma_texto" in evento_clon: del evento_clon["firma_texto"]
                    resultados_finales.append(evento_clon)

        # Ruta B (Rescate Premium)
        if not match_encontrado:
            titulo_base = canal["nombre_ui"]
            titulo_base = re.sub(r'\(.*?\)|\[.*?\]', '', titulo_base)
            titulo_base = re.sub(r'\b\d{1,2}:\d{2}\b(\s*[AP]M)?(\s*ET)?', '', titulo_base)
            titulo_base = re.sub(r'\b(HD|SD|FHD|4K|ENG|ESP|GER)\b', '', titulo_base, flags=re.IGNORECASE)
            titulo_base = titulo_base.strip(" -|▫✨[]").title()
            
            if not titulo_base: continue
            
            evento_existente = next((item for item in resultados_finales if item["titulo"] == titulo_base and item["id"].startswith("rescate_")), None)
            
            if evento_existente:
                if not any(f["url"] == url_video for f in evento_existente["fuentes"]):
                    evento_existente["fuentes"].append(fuente_limpia)
            else:
                categoria_adivinada, logo_adivinado = adivinar_categoria_y_logo(canal["nombre_ui"])
                hora_utc_calculada = calcular_hora_utc_falsa(canal["nombre_ui"])
                
                resultados_finales.append({
                    "id": f"rescate_{crear_id_seguro(titulo_base)}", 
                    "titulo": titulo_base,
                    "torneo": "Evento Especial",
                    "categoria": categoria_adivinada,
                    "hora_utc": hora_utc_calculada,
                    "duracion_min": DURACION_POR_DEPORTE.get(categoria_adivinada, 240),
                    "logo_local": logo_adivinado,
                    "logo_visitante": "",
                    "banner": logo_adivinado,
                    "tier": 2,
                    "fuentes": [fuente_limpia]
                })

    resultados_finales.sort(key=lambda x: x["hora_utc"])

    try:
        with open("eventos_hoy.json", "w", encoding="utf-8") as f:
            json.dump(resultados_finales, f, ensure_ascii=False, indent=2)
        log.info(f"¡Proceso Terminado! Total de eventos listos: {len(resultados_finales)}")
    except Exception as e:
        log.error(f"Error crítico guardando el archivo JSON: {e}")

if __name__ == "__main__":
    main()
