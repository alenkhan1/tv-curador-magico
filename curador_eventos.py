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
ARCHIVO_CACHE = "agenda_api_v4.json" # CAMBIO CLAVE: Fuerza la descarga limpia de la API hoy
HORAS_CACHE   = 4

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

TRADUCTOR_JERGA = {
    "F1": "FORMULA 1", "MOTO GP": "MOTOGP", "UCL": "CHAMPIONS LEAGUE", 
    "LIV": "LIV GOLF", "PGA": "PGA TOUR", "PREMIER": "PREMIER LEAGUE"
}

# EL SÚPER CATEGORIZADOR (Para eventos que la API no reconozca)
CATEGORIAS_RESCATE = {
    "Motor": ["F1", "F2", "F3", "FORMULA", "NASCAR", "CARRERA", "GP ", "MOTOGP"],
    "Béisbol": ["MLB", "LMB", "BEISBOL", "BASEBALL", "DIAMONDBACKS", "CUBS", "YANKEES", "RED SOX", "DODGERS", "PIRATES", "REDS", "ASTROS", "RANGERS"],
    "Baloncesto": ["NBA", "WNBA", "BASKETBALL", "LAKERS", "CELTICS", "BULLS", "CAVALIERS", "PISTONS", "MAGIC", "RAPTORS"],
    "Fútbol": ["LIGA", "SERIE A", "PREMIER", "FUTBOL", "SOCCER", "NWSL", "MLS", "CHAMPIONS", "LEAGUE", "SUDAMERICANA", "COPA"],
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
        
    return " ".join(texto.split())

def similitud(a: str, b: str) -> float:
    # Si uno de los textos está contenido completamente dentro del otro, aseguramos el match
    if a in b or b in a:
        return 80.0
    return SequenceMatcher(None, a, b).ratio() * 100

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
            h = int(match.group(1))
            m = int(match.group(2))
            pm = match.group(3)
            if pm and h < 12: h += 12
            dt_evento = datetime.now(ZONA_COLOMBIA).replace(hour=h, minute=m, second=0, microsecond=0)
            return dt_evento.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except:
            pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ─── RED Y CACHÉ ─────────────────────────────────────────────────────────────
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
            log.info("Cargando Agenda desde Caché Local...")
            with open(ARCHIVO_CACHE, "r", encoding="utf-8") as f:
                return json.load(f)

    log.info("Descargando Agenda Maestra de SofaScore...")
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
            if re.search(r'\b\d{1,2}:\d{2}\b', nombre_canal) or any(kw in nombre_canal.upper() for kw in ["PEA ", "PARA+", "LMB", "MLB"]):
                texto_limpio = limpiar_texto_para_match(nombre_canal)
                if len(texto_limpio) > 5:
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
    log.info(f"--- Iniciando Curador Mágico V4 (Anti-Crash & Full Categorization) ---")
    
    agenda_api = obtener_agenda_maestra()
    cubo_a = procesar_cubo_a()
    
    if not cubo_a: return

    resultados_finales = []

    for canal in cubo_a:
        texto_canal = canal["texto_limpio"]
        fuente_limpia = {"nombre": canal["nombre_ui"], "url": f"{XTREAM_URL}/{XTREAM_USER}/{XTREAM_PASS}/{canal['id_xtream']}.ts"}
        
        match_encontrado = False
        mejor_evento = None
        mejor_puntaje = 0
        
        # 1. RUTA A (Emparejamiento API)
        for ev in agenda_api:
            puntaje = similitud(texto_canal, ev["firma_texto"])
            if puntaje > mejor_puntaje:
                mejor_puntaje = puntaje
                mejor_evento = ev
        
        # Flexibilizamos el Match a 45% para que atrape variaciones de títulos sin depender de la hora
        if mejor_puntaje > 45.0 and mejor_evento:
            match_encontrado = True
            evento_existente = next((item for item in resultados_finales if item["id"] == mejor_evento["id"]), None)
            if evento_existente:
                evento_existente["fuentes"].append(fuente_limpia)
            else:
                evento_clon = mejor_evento.copy()
                evento_clon["fuentes"] = [fuente_limpia]
                del evento_clon["firma_texto"] 
                resultados_finales.append(evento_clon)

        # 2. RUTA B (Modo Rescate Agrupado y Categorizado)
        if not match_encontrado:
            # Extraemos un título base limpio para no crear duplicados ni hacer crashear la App
            titulo_base = re.sub(r'\(.*?\)', '', canal["nombre_ui"])
            titulo_base = re.sub(r'\[.*?\]', '', titulo_base)
            titulo_base = re.sub(r'\b\d{1,2}:\d{2}\b(\s*[AP]M)?(\s*ET)?', '', titulo_base)
            titulo_base = titulo_base.strip(" -|▫✨[]").title()
            
            evento_existente = next((item for item in resultados_finales if item["titulo"] == titulo_base), None)
            
            if evento_existente:
                evento_existente["fuentes"].append(fuente_limpia)
            else:
                categoria_adivinada, logo_adivinado = adivinar_categoria_y_logo(canal["nombre_ui"])
                hora_utc_calculada = calcular_hora_utc_falsa(canal["nombre_ui"])

                evento_rescate = {
                    "id": f"rescate_{canal['id_xtream']}", # SOLUCIÓN AL CRASH: ID único basado en el stream original
                    "titulo": titulo_base,
                    "torneo": "Evento Especial",
                    "categoria": categoria_adivinada,
                    "hora_utc": hora_utc_calculada,
                    "duracion_min": 240, # FUNDAMENTAL para que los eventos rescatados salgan "En Vivo" en tu app
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

    log.info(f"¡Proceso Terminado! Eventos exportados: {len(resultados_finales)}")

if __name__ == "__main__":
    main()
