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

# ─── CONFIGURACIÓN DE ENTORNO ────────────────────────────────────────────────
XTREAM_URL   = os.environ.get("XTREAM_URL")
XTREAM_USER  = os.environ.get("XTREAM_USER")
XTREAM_PASS  = os.environ.get("XTREAM_PASS")
RAPIDAPI_HOST = "sofasport.p.rapidapi.com"

ZONA_COLOMBIA = timezone(timedelta(hours=-5))
FECHA_HOY     = datetime.now(ZONA_COLOMBIA).strftime("%Y-%m-%d")
ARCHIVO_CACHE = "agenda_api_final.json" # Asegura una ejecución limpia
HORAS_CACHE   = 4

# Soporta hasta 10 llaves
LLAVES_ENV = [
    os.environ.get("RAPIDAPI_KEY_1"), os.environ.get("RAPIDAPI_KEY_2"),
    os.environ.get("RAPIDAPI_KEY_3"), os.environ.get("RAPIDAPI_KEY_4"),
    os.environ.get("RAPIDAPI_KEY_5"), os.environ.get("RAPIDAPI_KEY_6"),
    os.environ.get("RAPIDAPI_KEY_7"), os.environ.get("RAPIDAPI_KEY_8"),
    os.environ.get("RAPIDAPI_KEY_9"), os.environ.get("RAPIDAPI_KEY_10")
]
LLAVES_API = [k for k in LLAVES_ENV if k]
indice_llave_actual = 0

# ─── DICCIONARIOS Y CONFIGURACIÓN ────────────────────────────────────────────
SOFA_IMG_EQUIPO = "https://api.sofascore.app/api/v1/team/{id}/image"
SOFA_IMG_TORNEO = "https://api.sofascore.app/api/v1/unique-tournament/{id}/image/dark"

DEPORTES_IDS = {
    "Fútbol": 1, "Baloncesto": 2, "Tenis": 5, "Motor": 22, 
    "Béisbol": 64, "Boxeo": 9, "MMA": 30, "Golf": 18, 
    "Hockey": 4, "Voleibol": 23, "Rugby": 12
}

# Filtro protector: Solo aplica a los masivos. Los demás se descargan completos.
CATEGORIAS_PERMITIDAS = {
    "Fútbol": {"Colombia", "Spain", "World", "Europe", "South America", "North & Central America", "England", "Italy", "Germany", "France", "USA", "Argentina", "Brazil", "Mexico"},
    "Baloncesto": {"USA", "World", "Europe", "Spain"}
}

DURACION_POR_DEPORTE = {
    "Fútbol": 120, "Baloncesto": 150, "Tenis": 180, "Motor": 210, 
    "Béisbol": 210, "Rugby": 120, "Boxeo": 180, "Voleibol": 150, "MMA": 200, "Golf": 240, "Hockey": 150
}

TRADUCTOR_JERGA = {
    "F1": "FORMULA 1", "MOTO GP": "MOTOGP", "UCL": "CHAMPIONS LEAGUE", 
    "LIV": "LIV GOLF", "PGA": "PGA TOUR", "PREMIER": "PREMIER LEAGUE"
}

CATEGORIAS_RESCATE = {
    "Motor": ["F1", "F2", "F3", "FORMULA", "NASCAR", "CARRERA", "GP ", "MOTOGP", "RALLY"],
    "Béisbol": ["MLB", "LMB", "BEISBOL", "BASEBALL", "DIAMONDBACKS", "CUBS", "YANKEES", "RED SOX", "DODGERS", "PIRATES", "REDS", "ASTROS", "RANGERS"],
    "Baloncesto": ["NBA", "WNBA", "BASKETBALL", "LAKERS", "CELTICS", "BULLS", "CAVALIERS", "PISTONS", "MAGIC", "RAPTORS", "ENDESA"],
    "Fútbol": ["LIGA", "SERIE A", "PREMIER", "FUTBOL", "SOCCER", "NWSL", "MLS", "CHAMPIONS", "LEAGUE", "SUDAMERICANA", "COPA"],
    "Lucha Libre": ["WWE", "SMACKDOWN", "RAW", "AEW", "LUCHA"],
    "Boxeo": ["BOXEO", "BOXING", "PESAJE"],
    "Hockey": ["NHL", "SABRES", "BRUINS", "CANADIENS", "LIGHTNING"],
    "Tenis": ["ATP", "WTA", "TENIS", "TENNIS", "WIMBLEDON", "OPEN", "PADEL", "PING PONG"]
}

LOGOS_RESCATE = {
    "Motor": "https://img.icons8.com/color/512/f1-car.png",
    "Béisbol": "https://img.icons8.com/color/512/baseball.png",
    "Baloncesto": "https://img.icons8.com/color/512/basketball.png",
    "Fútbol": "https://img.icons8.com/color/512/football2.png",
    "Lucha Libre": "https://img.icons8.com/color/512/wrestling.png",
    "Boxeo": "https://img.icons8.com/color/512/boxing-glove.png",
    "Hockey": "https://img.icons8.com/color/512/ice-hockey.png",
    "Tenis": "https://img.icons8.com/color/512/tennis.png",
    "Deportes": "https://img.icons8.com/color/512/stadium.png"
}

# ─── UTILIDADES ──────────────────────────────────────────────────────────────
def limpiar_texto_para_match(texto: str) -> str:
    if not texto: return ""
    texto = str(texto).upper()
    texto = re.sub(r'\(.*?\)', '', texto)
    texto = re.sub(r'\[.*?\]', '', texto)
    texto = re.sub(r'\b\d{1,2}:\d{2}\b(\s*[AP]M)?(\s*ET)?', '', texto)
    texto = re.sub(r'\b(HD|SD|FHD|4K|ENG|ESP|GER|OPC\.\d+|OP\d+|PEA \d+|PARA\+ \d+)\b', '', texto)
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^A-Z0-9\s]", " ", texto)
    
    for corto, largo in TRADUCTOR_JERGA.items():
        texto = re.sub(rf"\b{corto}\b", largo, texto)
        
    # Filtrar palabras demasiado comunes que generan falsos positivos
    palabras = [p for p in texto.split() if len(p) > 2 and p not in ["THE", "AND", "DEL", "LAS", "LOS", "VS"]]
    return " ".join(palabras)

def calcular_similitud_interseccion(xtream_limpio: str, api_limpio: str) -> float:
    """Evalúa la coincidencia basándose en palabras clave compartidas."""
    words_x = set(xtream_limpio.split())
    words_a = set(api_limpio.split())
    
    if not words_x or not words_a: return 0.0
    
    comunes = words_x.intersection(words_a)
    
    # MATCH FUERTE: Si comparten 2 o más palabras clave significativas
    if len(comunes) >= 2:
        return 85.0
    
    # MATCH MEDIO: Si comparten 1 palabra muy específica/larga
    if len(comunes) == 1 and len(list(comunes)[0]) >= 5:
        # Prevenimos falsos positivos exigiendo cierta similitud fonética general
        ratio = SequenceMatcher(None, xtream_limpio, api_limpio).ratio() * 100
        if ratio > 35.0:
            return 70.0
            
    return 0.0

def adivinar_categoria_y_logo(texto: str):
    texto_upper = texto.upper()
    for categoria, keywords in CATEGORIAS_RESCATE.items():
        if any(kw in texto_upper for kw in keywords):
            return categoria, LOGOS_RESCATE[categoria]
    return "Deportes", LOGOS_RESCATE["Deportes"]

def calcular_hora_utc_falsa(nombre_canal: str) -> str:
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
    """Genera un ID único consistente basado en el texto para la Ruta B."""
    import hashlib
    hash_obj = hashlib.md5(titulo.encode('utf-8'))
    return hash_obj.hexdigest()[:12]

# ─── RED Y CACHÉ ─────────────────────────────────────────────────────────────
def hacer_peticion_rotativa(url: str, params: dict):
    global indice_llave_actual
    if not LLAVES_API:
        log.error("No hay llaves de RapidAPI configuradas.")
        return None
        
    intentos = 0
    while intentos < len(LLAVES_API):
        llave = LLAVES_API[indice_llave_actual]
        try:
            r = requests.get(url, headers={"x-rapidapi-key": llave, "x-rapidapi-host": RAPIDAPI_HOST}, params=params, timeout=15)
            
            if r.status_code in [429, 403]: # Rate Limit o Quota Exceeded
                log.warning(f"Llave {indice_llave_actual + 1} agotada o bloqueada temporalmente. Rotando...")
                indice_llave_actual = (indice_llave_actual + 1) % len(LLAVES_API)
                intentos += 1
                time.sleep(2) # Pausa obligatoria al rotar
                continue
                
            return r
        except requests.exceptions.RequestException as e:
            log.error(f"Error de conexión en llave {indice_llave_actual + 1}: {e}")
            indice_llave_actual = (indice_llave_actual + 1) % len(LLAVES_API)
            intentos += 1
            time.sleep(2)
            
    log.error("Todas las llaves fallaron o se agotaron.")
    return None

def obtener_agenda_maestra() -> list:
    if os.path.exists(ARCHIVO_CACHE):
        tiempo_modificacion = os.path.getmtime(ARCHIVO_CACHE)
        if (time.time() - tiempo_modificacion) < (HORAS_CACHE * 3600):
            log.info("Cargando Agenda Maestra desde Caché Local...")
            try:
                with open(ARCHIVO_CACHE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                log.warning("Caché corrupto. Descargando de nuevo...")

    log.info("Descargando Agenda Maestra de SofaScore (Protegida contra bloqueos)...")
    eventos_api = []
    
    for deporte, sport_id in DEPORTES_IDS.items():
        log.info(f"Consultando categorías para: {deporte}...")
        r_cat = hacer_peticion_rotativa(f"https://{RAPIDAPI_HOST}/v1/calendar/categories", {"sport_id": sport_id, "date": FECHA_HOY, "timezone": -5})
        
        if not r_cat or r_cat.status_code != 200: 
            time.sleep(1)
            continue
            
        categorias_data = r_cat.json().get("data", [])
        filtro_paises = CATEGORIAS_PERMITIDAS.get(deporte, set())
        
        for cat in categorias_data:
            cat_nombre = cat.get("category", {}).get("name", "")
            
            # Si es Fútbol/Basket y no está en la lista de países élite, lo ignoramos para no quemar llaves
            if filtro_paises and cat_nombre not in filtro_paises:
                continue
                
            cat_id = cat.get("category", {}).get("id")
            r_ev = hacer_peticion_rotativa(f"https://{RAPIDAPI_HOST}/v1/events/schedule/category", {"category_id": cat_id, "date": FECHA_HOY})
            
            if not r_ev or r_ev.status_code != 200:
                time.sleep(1) # PAUSA VITAL SI HAY ERROR
                continue

            for ev in r_ev.json().get("data", []):
                try:
                    unix_time = ev.get("startTimestamp")
                    if not unix_time: continue
                    
                    dt_utc = datetime.fromtimestamp(unix_time, timezone.utc)
                    torneo = ev.get("tournament", {}).get("name", "")
                    nombre_evento = ev.get("name", ev.get("description", ""))
                    eq_local = ev.get("homeTeam", {}).get("name", "")
                    eq_visit = ev.get("awayTeam", {}).get("name", "")
                    
                    titulo = f"{eq_local} vs {eq_visit}" if eq_local and eq_visit else nombre_evento
                    firma_texto = limpiar_texto_para_match(f"{torneo} {titulo}")

                    eventos_api.append({
                        "id": str(ev.get("id")),
                        "titulo": titulo,
                        "torneo": torneo,
                        "categoria": deporte,
                        "firma_texto": firma_texto,
                        "hora_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "duracion_min": DURACION_POR_DEPORTE.get(deporte, 180),
                        "logo_local": url_logo_equipo(ev.get("homeTeam", {}).get("id")) if eq_local else url_logo_torneo(ev.get("tournament", {}).get("uniqueTournament", {}).get("id")),
                        "logo_visitante": url_logo_equipo(ev.get("awayTeam", {}).get("id")) if eq_visit else "",
                        "tier": 2
                    })
                except Exception:
                    continue
            
            # PAUSA OBLIGATORIA ANTIBLOQUEO (Rate Limit Protection)
            time.sleep(1.5) 
            
    if eventos_api:
        log.info(f"API descargada exitosamente: {len(eventos_api)} eventos consolidados.")
        with open(ARCHIVO_CACHE, "w", encoding="utf-8") as f:
            json.dump(eventos_api, f, ensure_ascii=False, indent=2)
    else:
        log.error("Fallo crítico: No se descargaron eventos de la API.")
        
    return eventos_api

# ─── XTREAM (CUBO A) ─────────────────────────────────────────────────────────
def procesar_cubo_a() -> list:
    log.info("Descargando Xtream y detectando canales temporales (Cubo A)...")
    
    # CORRECCIÓN ENLACE: Asegurarse de que XTREAM_URL no termine en slash
    base_url = XTREAM_URL.rstrip('/')
    api_url = f"{base_url}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
    
    try:
        r_str = requests.get(f"{api_url}&action=get_live_streams", timeout=30)
        cubo_a = []
        
        for s in r_str.json():
            nombre_canal = s.get("name", "").strip()
            
            # Regla de captura de eventos: Si tiene hora O si tiene etiquetas típicas de eventos especiales
            if re.search(r'\b\d{1,2}:\d{2}\b', nombre_canal) or any(kw in nombre_canal.upper() for kw in ["PEA ", "PARA+", "LMB", "MLB"]):
                texto_limpio = limpiar_texto_para_match(nombre_canal)
                if len(texto_limpio) > 3:
                    cubo_a.append({
                        "id_xtream": str(s.get("stream_id")),
                        "nombre_ui": nombre_canal,
                        "texto_limpio": texto_limpio
                    })
        return cubo_a
    except Exception as e:
        log.error(f"Error procesando Xtream: {e}")
        return []

# ─── ORQUESTADOR ─────────────────────────────────────────────────────────────
def main():
    log.info(f"--- Iniciando Curador Mágico V6 (Estabilidad y Precisión) ---")
    
    agenda_api = obtener_agenda_maestra()
    cubo_a = procesar_cubo_a()
    
    if not cubo_a: 
        log.warning("No se detectaron eventos temporales en Xtream.")
        return

    resultados_finales = []
    base_url = XTREAM_URL.rstrip('/')

    for canal in cubo_a:
        texto_canal = canal["texto_limpio"]
        
        # CORRECCIÓN DE LA RUTA DEL VIDEO: Estructura nativa sin /live/
        url_video = f"{base_url}/{XTREAM_USER}/{XTREAM_PASS}/{canal['id_xtream']}.ts"
        fuente_limpia = {"nombre": canal["nombre_ui"], "url": url_video}
        
        match_encontrado = False
        mejor_evento = None
        mejor_puntaje = 0
        
        # 1. RUTA A (Emparejamiento Seguro API)
        for ev in agenda_api:
            puntaje = calcular_similitud_interseccion(texto_canal, ev["firma_texto"])
            if puntaje > mejor_puntaje:
                mejor_puntaje = puntaje
                mejor_evento = ev
        
        if mejor_puntaje > 60.0 and mejor_evento:
            match_encontrado = True
            evento_existente = next((item for item in resultados_finales if item["id"] == mejor_evento["id"]), None)
            
            if evento_existente:
                # Evitar duplicar exactamente la misma URL
                if not any(f["url"] == url_video for f in evento_existente["fuentes"]):
                    evento_existente["fuentes"].append(fuente_limpia)
            else:
                evento_clon = mejor_evento.copy()
                evento_clon["fuentes"] = [fuente_limpia]
                if "firma_texto" in evento_clon: del evento_clon["firma_texto"]
                resultados_finales.append(evento_clon)

        # 2. RUTA B (Modo Rescate Seguro y Categorizado)
        if not match_encontrado:
            # Título limpio y presentable para la App (Quitando horas y paréntesis)
            titulo_base = re.sub(r'\(.*?\)', '', canal["nombre_ui"])
            titulo_base = re.sub(r'\[.*?\]', '', titulo_base)
            titulo_base = re.sub(r'\b\d{1,2}:\d{2}\b(\s*[AP]M)?(\s*ET)?', '', titulo_base)
            titulo_base = re.sub(r'\b(HD|SD|FHD|4K|ENG|ESP|GER)\b', '', titulo_base, flags=re.IGNORECASE)
            titulo_base = titulo_base.strip(" -|▫✨[]").title()
            
            if not titulo_base: continue
            
            evento_existente = next((item for item in resultados_finales if item["titulo"] == titulo_base), None)
            
            if evento_existente:
                if not any(f["url"] == url_video for f in evento_existente["fuentes"]):
                    evento_existente["fuentes"].append(fuente_limpia)
            else:
                categoria_adivinada, logo_adivinado = adivinar_categoria_y_logo(canal["nombre_ui"])
                hora_utc_calculada = calcular_hora_utc_falsa(canal["nombre_ui"])
                
                # Generamos ID basado en MD5 para evitar crasheos de Jetpack Compose
                id_unico = crear_id_seguro(titulo_base)

                evento_rescate = {
                    "id": f"rescate_{id_unico}", 
                    "titulo": titulo_base,
                    "torneo": "Evento Especial",
                    "categoria": categoria_adivinada,
                    "hora_utc": hora_utc_calculada,
                    "duracion_min": 240, 
                    "logo_local": logo_adivinado,
                    "logo_visitante": "",
                    "banner": logo_adivinado,
                    "tier": 2,
                    "fuentes": [fuente_limpia]
                }
                resultados_finales.append(evento_rescate)

    resultados_finales.sort(key=lambda x: x["hora_utc"])

    with open("eventos_hoy.json", "w", encoding="utf-8") as f:
        json.dump(resultados_finales, f, ensure_ascii=False, indent=2)

    log.info(f"¡Proceso Terminado! Total de eventos finales listos para la App: {len(resultados_finales)}")

if __name__ == "__main__":
    main()
