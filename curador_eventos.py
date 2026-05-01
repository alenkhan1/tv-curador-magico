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
ARCHIVO_CACHE = "agenda_guardada.json"
HORAS_CACHE   = 4 # Tiempo de vida del caché de la API

LLAVES_API = [
    os.environ.get("RAPIDAPI_KEY_1"),
    os.environ.get("RAPIDAPI_KEY_2"),
    os.environ.get("RAPIDAPI_KEY_3"),
]
LLAVES_API = [k for k in LLAVES_API if k]
indice_llave_actual = 0

# ─── DICCIONARIOS Y CONFIGURACIÓN ────────────────────────────────────────────
SOFA_IMG_EQUIPO = "https://api.sofascore.app/api/v1/team/{id}/image"
SOFA_IMG_TORNEO = "https://api.sofascore.app/api/v1/unique-tournament/{id}/image/dark"

DEPORTES_IDS = {
    "Fútbol": 1, "Baloncesto": 2, "Tenis": 5, "Motor": 22, 
    "Béisbol": 64, "Boxeo": 9, "MMA": 30, "Golf": 18, 
    "Hockey": 4, "Voleibol": 23, "Rugby": 12
}

# Categorizador heurístico para la Ruta B (Eventos Rescatados)
CATEGORIAS_RESCATE = {
    "Motor": ["F1", "F2", "F3", "FORMULA", "NASCAR", "CARRERA", "GP ", "MOTOGP"],
    "Béisbol": ["MLB", "LMB", "BEISBOL", "BASEBALL", "DIAMONDBACKS", "CUBS", "YANKEES", "RED SOX", "DODGERS", "PIRATES", "REDS", "ASTROS", "RANGERS"],
    "Baloncesto": ["NBA", "WNBA", "BASKETBALL", "LAKERS", "CELTICS", "BULLS", "CAVALIERS", "PISTONS", "MAGIC", "RAPTORS"],
    "Fútbol": ["LIGA", "SERIE A", "PREMIER", "FUTBOL", "SOCCER", "NWSL", "MLS", "CHAMPIONS", "LEAGUE"],
    "Lucha Libre": ["WWE", "SMACKDOWN", "RAW", "AEW", "LUCHA"],
    "Boxeo": ["BOXEO", "BOXING", "PESAJE"],
    "Hockey": ["NHL", "SABRES", "BRUINS", "CANADIENS", "LIGHTNING"],
    "Tenis": ["ATP", "WTA", "TENIS", "TENNIS", "WIMBLEDON", "OPEN"]
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
    "Deportes": "https://img.icons8.com/color/512/stadium.png" # Fallback
}

# ─── UTILIDADES ──────────────────────────────────────────────────────────────
def limpiar_texto_para_match(texto: str) -> str:
    """Limpia el texto de un canal eliminando horas, tags, y contenido en paréntesis para agrupar."""
    if not texto: return ""
    texto = str(texto).upper()
    
    # 1. Quitar contenido entre paréntesis y corchetes (ej. "On Board", "Con Tornello", "May 01")
    texto = re.sub(r'\(.*?\)', '', texto)
    texto = re.sub(r'\[.*?\]', '', texto)
    
    # 2. Quitar horas y zonas horarias (ej. 14:00, 2:35 PM, ET)
    texto = re.sub(r'\b\d{1,2}:\d{2}\b(\s*[AP]M)?(\s*ET)?', '', texto)
    
    # 3. Quitar tags basura de IPTV
    texto = re.sub(r'\b(HD|SD|FHD|4K|ENG|ESP|GER|OPC\.\d+|OP\d+|PEA \d+|PARA\+ \d+)\b', '', texto)
    
    # 4. Quitar caracteres especiales y dejar solo letras y números
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^A-Z0-9\s]", " ", texto)
    
    return " ".join(texto.split())

def similitud(a: str, b: str) -> float:
    """Devuelve un porcentaje de similitud entre dos textos (0.0 a 100.0)"""
    return SequenceMatcher(None, a, b).ratio() * 100

def adivinar_categoria_y_logo(texto: str):
    """Analiza el título limpio para asignarle la categoría correcta en Modo Rescate."""
    texto_upper = texto.upper()
    for categoria, keywords in CATEGORIAS_RESCATE.items():
        if any(kw in texto_upper for kw in keywords):
            return categoria, LOGOS_RESCATE[categoria]
    return "Deportes", LOGOS_RESCATE["Deportes"]

def calcular_hora_utc_falsa(nombre_canal: str) -> str:
    """Extrae la primera hora que vea en el canal. Si no hay, usa la hora actual UTC."""
    match = re.search(r'\b(\d{1,2}):(\d{2})\b\s*(PM)?', nombre_canal, re.IGNORECASE)
    if match:
        try:
            h = int(match.group(1))
            m = int(match.group(2))
            pm = match.group(3)
            if pm and h < 12: h += 12
            # Asumimos que la hora extraída es de hoy en zona Colombia como base aproximada
            dt_evento = datetime.now(ZONA_COLOMBIA).replace(hour=h, minute=m, second=0, microsecond=0)
            return dt_evento.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except:
            pass
    # Si falla o no hay hora, devolvemos la hora actual UTC para que esté "En Vivo" ahora mismo
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ─── RED Y CACHÉ (API SOFASCORE) ─────────────────────────────────────────────
def hacer_peticion_rotativa(url: str, params: dict):
    global indice_llave_actual
    intentos = 0
    while intentos < len(LLAVES_API):
        try:
            r = requests.get(url, headers={"x-rapidapi-key": LLAVES_API[indice_llave_actual], "x-rapidapi-host": RAPIDAPI_HOST}, params=params, timeout=10)
            if r.status_code == 429:
                indice_llave_actual = (indice_llave_actual + 1) % len(LLAVES_API)
                intentos += 1
                time.sleep(1)
                continue
            return r
        except Exception:
            return None
    return None

def obtener_agenda_maestra() -> list:
    if os.path.exists(ARCHIVO_CACHE):
        tiempo_modificacion = os.path.getmtime(ARCHIVO_CACHE)
        if (time.time() - tiempo_modificacion) < (HORAS_CACHE * 3600):
            log.info("Cargando Agenda de SofaScore desde Caché Local...")
            with open(ARCHIVO_CACHE, "r", encoding="utf-8") as f:
                return json.load(f)

    log.info("Descargando Agenda Maestra de SofaScore (Todas las categorías)...")
    eventos_api = []
    
    for deporte, sport_id in DEPORTES_IDS.items():
        r_cat = hacer_peticion_rotativa(f"https://{RAPIDAPI_HOST}/v1/calendar/categories", {"sport_id": sport_id, "date": FECHA_HOY, "timezone": -5})
        if not r_cat or r_cat.status_code != 200: continue
        
        for cat in r_cat.json().get("data", []):
            cat_id = cat.get("category", {}).get("id")
            r_ev = hacer_peticion_rotativa(f"https://{RAPIDAPI_HOST}/v1/events/schedule/category", {"category_id": cat_id, "date": FECHA_HOY})
            if not r_ev or r_ev.status_code != 200: continue

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
                    # La firma se usará para el Fuzzy Matching
                    firma_texto = limpiar_texto_para_match(f"{torneo} {titulo}")

                    eventos_api.append({
                        "id": str(ev.get("id")),
                        "titulo": titulo,
                        "torneo": torneo,
                        "categoria": deporte,
                        "firma_texto": firma_texto,
                        "hora_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "duracion_min": DURACION_POR_DEPORTE.get(deporte, 150),
                        "logo_local": url_logo_equipo(ev.get("homeTeam", {}).get("id")) if eq_local else url_logo_torneo(ev.get("tournament", {}).get("uniqueTournament", {}).get("id")),
                        "logo_visitante": url_logo_equipo(ev.get("awayTeam", {}).get("id")) if eq_visit else "",
                        "tier": 2
                    })
                except Exception:
                    continue
        time.sleep(0.5)

    with open(ARCHIVO_CACHE, "w", encoding="utf-8") as f:
        json.dump(eventos_api, f, ensure_ascii=False, indent=2)
    
    return eventos_api

# ─── XTREAM (CUBO A) ─────────────────────────────────────────────────────────
def procesar_cubo_a() -> list:
    log.info("Descargando Xtream y detectando canales temporales (Cubo A)...")
    url_base = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
    
    try:
        r_str = requests.get(f"{url_base}&action=get_live_streams", timeout=30)
        cubo_a = []
        
        for s in r_str.json():
            nombre_canal = s.get("name", "").strip()
            
            # Filtro amplio: Si tiene hora (ej. 14:00, 2:30 PM), entra al Cubo A. 
            # También dejamos entrar canales de PPV o ligas específicas que suelen ser temporales.
            if re.search(r'\b\d{1,2}:\d{2}\b', nombre_canal) or any(kw in nombre_canal.upper() for kw in ["PEA ", "PARA+", "LMB", "MLB"]):
                texto_limpio = limpiar_texto_para_match(nombre_canal)
                if len(texto_limpio) > 5: # Evitar canales basura demasiado cortos
                    cubo_a.append({
                        "id_xtream": s.get("stream_id"),
                        "nombre_ui": nombre_canal,
                        "texto_limpio": texto_limpio
                    })
                    
        log.info(f"Cubo A listo: {len(cubo_a)} canales temporales detectados.")
        return cubo_a
    except Exception as e:
        log.error(f"Error procesando Xtream: {e}")
        return []

# ─── ORQUESTADOR (EL CEREBRO EMPAREJADOR) ────────────────────────────────────
def main():
    log.info(f"--- Iniciando Curador Mágico V3 (Fuzzy Match & Grouping) ---")
    
    agenda_api = obtener_agenda_maestra()
    cubo_a = procesar_cubo_a()
    
    if not cubo_a: return

    resultados_finales = []

    for canal in cubo_a:
        texto_canal = canal["texto_limpio"]
        
        match_encontrado = False
        mejor_evento = None
        mejor_puntaje = 0
        
        # Corrección CRÍTICA: Enlace Directo, sin carpeta "/live/"
        fuente_limpia = {"nombre": canal["nombre_ui"], "url": f"{XTREAM_URL}/{XTREAM_USER}/{XTREAM_PASS}/{canal['id_xtream']}.ts"}
        
        # 1. RUTA A (Emparejamiento API por Similitud de Texto)
        # Comparamos contra toda la agenda de hoy, el texto manda sobre la hora.
        for ev in agenda_api:
            puntaje = similitud(texto_canal, ev["firma_texto"])
            if puntaje > mejor_puntaje:
                mejor_puntaje = puntaje
                mejor_evento = ev
        
        # Umbral del 55%. Si el texto de Xtream y el de la API se parecen más de un 55%, es un match.
        if mejor_puntaje > 55.0 and mejor_evento:
            match_encontrado = True
            
            # Agrupación: Si este evento de la API ya lo metimos al JSON, solo agregamos la fuente (camara 2, etc)
            evento_existente = next((item for item in resultados_finales if item["id"] == mejor_evento["id"]), None)
            if evento_existente:
                evento_existente["fuentes"].append(fuente_limpia)
            else:
                evento_clon = mejor_evento.copy()
                evento_clon["fuentes"] = [fuente_limpia]
                del evento_clon["firma_texto"] # Limpieza de datos internos
                resultados_finales.append(evento_clon)

        # 2. RUTA B (Modo Rescate Agrupado) - Para Lucha Libre, PPV, etc.
        if not match_encontrado:
            # Agrupación por título limpio. Esto fusiona las 12 F1 en 1 solo evento.
            titulo_limpio = texto_canal
            
            # Buscar si ya creamos un evento rescatado con este mismo título
            evento_existente = next((item for item in resultados_finales if item["titulo"] == titulo_limpio), None)
            
            if evento_existente:
                evento_existente["fuentes"].append(fuente_limpia)
            else:
                categoria_adivinada, logo_adivinado = adivinar_categoria_y_logo(texto_canal)
                hora_utc_calculada = calcular_hora_utc_falsa(canal["nombre_ui"])

                evento_rescate = {
                    "id": f"rescate_{hash(titulo_limpio)}", 
                    "titulo": titulo_limpio.title(), # Formato bonito (Capitalizado)
                    "torneo": "Evento Especial (PPV)",
                    "categoria": categoria_adivinada,
                    "hora_utc": hora_utc_calculada,
                    "duracion_min": 240, # FUNDAMENTAL: 4 horas de duración para que aparezca "En Vivo" en la app
                    "logo_local": logo_adivinado,
                    "logo_visitante": "",
                    "banner": logo_adivinado,
                    "tier": 2,
                    "fuentes": [fuente_limpia]
                }
                resultados_finales.append(evento_rescate)

    # Ordenar cronológicamente
    resultados_finales.sort(key=lambda x: x["hora_utc"])

    with open("eventos_hoy.json", "w", encoding="utf-8") as f:
        json.dump(resultados_finales, f, ensure_ascii=False, indent=2)

    log.info(f"¡Proceso Terminado! Eventos exportados y agrupados: {len(resultados_finales)}")

if __name__ == "__main__":
    main()
