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

DEPORTES_IDS = {"Fútbol": 1, "Baloncesto": 2, "Tenis": 5, "Motor": 22, "Béisbol": 64, "Boxeo": 9, "MMA": 30, "Golf": 18}

TRADUCTOR_JERGA = {
    "F1": "FORMULA 1", "MOTO GP": "MOTOGP", "UCL": "CHAMPIONS LEAGUE", 
    "LIV": "LIV GOLF", "PGA": "PGA TOUR", "PREMIER": "PREMIER LEAGUE"
}

LOGOS_GENERICOS = {
    "Boxeo": "https://img.icons8.com/color/512/boxing-glove.png",
    "Lucha": "https://img.icons8.com/color/512/wrestling.png",
    "Motor": "https://img.icons8.com/color/512/f1-car.png",
    "Default": "https://img.icons8.com/color/512/stadium.png"
}

# ─── UTILIDADES ──────────────────────────────────────────────────────────────
def normalizar_texto(texto: str) -> str:
    if not texto: return ""
    texto = str(texto).upper()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^A-Z0-9\s]", " ", texto)
    
    for corto, largo in TRADUCTOR_JERGA.items():
        texto = re.sub(rf"\b{corto}\b", largo, texto)
        
    return " ".join(texto.split())

def similitud(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() * 100

def extraer_hora(texto: str):
    match = re.search(r"\b(\d{1,2}):(\d{2})\b", texto)
    return match.group(0) if match else None

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
            log.info("Cargando Agenda Maestra desde Caché Local (Ahorrando API)...")
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
                    hora_corta = dt_utc.astimezone(ZONA_COLOMBIA).strftime("%H:%M")
                    
                    torneo = ev.get("tournament", {}).get("name", "")
                    nombre_evento = ev.get("name", ev.get("description", ""))
                    eq_local = ev.get("homeTeam", {}).get("name", "")
                    eq_visit = ev.get("awayTeam", {}).get("name", "")
                    
                    titulo = f"{eq_local} vs {eq_visit}" if eq_local and eq_visit else nombre_evento
                    firma_texto = normalizar_texto(f"{torneo} {titulo}")

                    eventos_api.append({
                        "id": str(ev.get("id")),
                        "titulo": titulo,
                        "torneo": torneo,
                        "categoria": deporte,
                        "hora_corta": hora_corta,
                        "firma_texto": firma_texto,
                        "hora_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
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

# ─── XTREAM Y EMPAREJAMIENTO ─────────────────────────────────────────────────
def procesar_cubo_a() -> list:
    log.info("Descargando Xtream y filtrando Cubo A...")
    url_base = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
    
    try:
        r_str = requests.get(f"{url_base}&action=get_live_streams", timeout=30)
        cubo_a = []
        
        for s in r_str.json():
            nombre_canal = s.get("name", "").strip()
            hora_canal = extraer_hora(nombre_canal)
            
            if hora_canal:
                texto_limpio = normalizar_texto(nombre_canal)
                cubo_a.append({
                    "id_xtream": s.get("stream_id"),
                    "nombre_ui": nombre_canal,
                    "hora_extraida": hora_canal,
                    "texto_limpio": texto_limpio
                })
        log.info(f"Cubo A listo: {len(cubo_a)} eventos detectados.")
        return cubo_a
    except Exception as e:
        log.error(f"Error procesando Xtream: {e}")
        return []

def asignar_logo_generico(texto: str) -> str:
    texto = texto.upper()
    if "LUCHA" in texto or "WWE" in texto or "AEW" in texto: return LOGOS_GENERICOS["Lucha"]
    if "BOXEO" in texto or "BOXING" in texto: return LOGOS_GENERICOS["Boxeo"]
    if "F1" in texto or "MOTOR" in texto or "RALLY" in texto: return LOGOS_GENERICOS["Motor"]
    return LOGOS_GENERICOS["Default"]

def adivinar_categoria(texto: str) -> str:
    texto = texto.upper()
    if "LUCHA" in texto or "WWE" in texto: return "Lucha Libre"
    if "BOX" in texto: return "Boxeo"
    if "F1" in texto or "GP" in texto: return "Motor"
    return "Deportes"

# ─── ORQUESTADOR ─────────────────────────────────────────────────────────────
def main():
    log.info(f"--- Iniciando Curador Mágico V2 ---")
    
    agenda_api = obtener_agenda_maestra()
    cubo_a = procesar_cubo_a()
    
    if not cubo_a: return

    resultados_finales = []

    for canal in cubo_a:
        hora_canal = canal["hora_extraida"]
        texto_canal = canal["texto_limpio"]
        
        eventos_candidatos = [ev for ev in agenda_api if ev["hora_corta"] == hora_canal]
        
        match_encontrado = False
        mejor_evento = None
        mejor_puntaje = 0
        
        # Corrección 1: Construir URL sin "/live/" para adaptarse al proveedor
        fuente_limpia = {"nombre": canal["nombre_ui"], "url": f"{XTREAM_URL}/{XTREAM_USER}/{XTREAM_PASS}/{canal['id_xtream']}.ts"}
        
        # 2. RUTA A (Emparejamiento API)
        if eventos_candidatos:
            for ev in eventos_candidatos:
                puntaje = similitud(texto_canal, ev["firma_texto"])
                if puntaje > mejor_puntaje:
                    mejor_puntaje = puntaje
                    mejor_evento = ev
            
            if mejor_puntaje > 45.0 and mejor_evento:
                match_encontrado = True
                evento_existente = next((item for item in resultados_finales if item["id"] == mejor_evento["id"]), None)
                if evento_existente:
                    evento_existente["fuentes"].append(fuente_limpia)
                else:
                    evento_clon = mejor_evento.copy()
                    evento_clon["fuentes"] = [fuente_limpia]
                    del evento_clon["firma_texto"]
                    del evento_clon["hora_corta"]
                    resultados_finales.append(evento_clon)

        # 3. RUTA B (Modo Rescate Agrupado)
        if not match_encontrado:
            # Corrección 2: Eliminamos textos entre paréntesis y corchetes para agrupar (Ej: quita "(On Board Antonelli)")
            titulo_limpio = re.sub(r'\(.*?\)', '', canal["nombre_ui"])
            titulo_limpio = titulo_limpio.replace(hora_canal, "").strip(" -|▫✨[]")
            
            # Buscamos si ya creamos un evento de rescate con ese mismo título base
            evento_existente = next((item for item in resultados_finales if item["titulo"] == titulo_limpio), None)
            
            if evento_existente:
                evento_existente["fuentes"].append(fuente_limpia)
            else:
                categoria_adivinada = adivinar_categoria(texto_canal)
                logo = asignar_logo_generico(texto_canal)
                
                try:
                    hora, minuto = map(int, hora_canal.split(":"))
                    dt_evento = datetime.now(ZONA_COLOMBIA).replace(hour=hora, minute=minuto, second=0, microsecond=0)
                    hora_utc_falsa = dt_evento.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                except:
                    hora_utc_falsa = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                evento_rescate = {
                    "id": f"rescate_{hash(titulo_limpio)}", # ID basado en el título, no en el canal
                    "titulo": titulo_limpio,
                    "torneo": "Evento Especial (PPV)",
                    "categoria": categoria_adivinada,
                    "hora_utc": hora_utc_falsa,
                    "logo_local": logo,
                    "logo_visitante": "",
                    "banner": logo,
                    "tier": 2,
                    "fuentes": [fuente_limpia]
                }
                resultados_finales.append(evento_rescate)

    resultados_finales.sort(key=lambda x: x["hora_utc"])

    with open("eventos_hoy.json", "w", encoding="utf-8") as f:
        json.dump(resultados_finales, f, ensure_ascii=False, indent=2)

    log.info(f"¡Proceso Terminado! Eventos exportados y agrupados: {len(resultados_finales)}")

if __name__ == "__main__":
    main()
