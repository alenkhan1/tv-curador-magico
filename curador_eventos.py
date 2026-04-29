import os
import re
import json
import time
import requests
import unicodedata
from datetime import datetime, timezone

# ─── CONFIGURACIÓN DE ENTORNO Y LLAVES ───────────────────────────────────────
XTREAM_URL       = os.environ.get("XTREAM_URL")
XTREAM_USER      = os.environ.get("XTREAM_USER")
XTREAM_PASS      = os.environ.get("XTREAM_PASS")
RAPIDAPI_HOST    = "sofasport.p.rapidapi.com"
FECHA_HOY        = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# Cargamos el arsenal de llaves
LLAVES_API = [
    os.environ.get("RAPIDAPI_KEY_1"),
    os.environ.get("RAPIDAPI_KEY_2"),
    os.environ.get("RAPIDAPI_KEY_3")
]
LLAVES_API = [k for k in LLAVES_API if k] # Filtramos nulas por si acaso
indice_llave_actual = 0

# ─── DICCIONARIOS Y REGLAS DE NEGOCIO ────────────────────────────────────────
LEET_DICT = {
    "4": "A", "@": "A", "3": "E", "€": "E", "1": "I", "¡": "I", "|": "I",
    "0": "O", "Ø": "O", "5": "S", "$": "S", "7": "T", "8": "B", "ñ": "N", "Ñ": "N"
}

PALABRAS_GENERICAS = {
    "WOMEN", "MEN", "CUP", "LEAGUE", "LIVE", "FHD", "4K", "1080P", "720P", 
    "CHAMPIONSHIP", "TOUR", "QUALIFICATION", "TV", "SPORTS", "VS"
}

STOP_WORDS = {
    "FC", "SC", "CF", "AC", "AS", "US", "CS", "RC", "CD", "SD", "UD",
    "THE", "DE", "LA", "LAS", "LOS", "EL", "Y", "E", "AND", "OF", "DEL",
    "CENTRAL", "CITY", "UNITED", "REAL", "CLUB", "ATLETICO", "DEPORTIVO", "SPORTING"
}

TIER_1_ELITE = ["CHAMPIONS LEAGUE", "LIGA BETPLAY", "LA LIGA", "PREMIER LEAGUE", "SERIE A", "FORMULA 1", "NBA", "UFC", "LIBERTADORES", "MUNDIAL"]
TIER_2_NICHO = ["NFL", "MLB", "F2", "F3", "MOTO GP", "COPA SUDAMERICANA", "EREDIVISIE", "BUNDESLIGA"]
PAISES_HEROE_LOCAL = ["Colombia", "Spain", "CO", "ES"]

# NUEVO: Filtro prematuro para no gastar peticiones en ligas irrelevantes
CATEGORIAS_PERMITIDAS = {
    "Colombia", "Spain", "World", "Europe", "South America", 
    "North & Central America", "England", "Italy", "Germany", 
    "France", "USA", "Argentina", "Brazil", "Mexico", "ATP", "WTA"
}

# ─── UTILIDADES ──────────────────────────────────────────────────────────────
def normalizar_texto(texto):
    if not texto: return ""
    texto = str(texto).upper()
    for leet, real in LEET_DICT.items():
        texto = texto.replace(leet, real)
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^A-Z0-9\s:]", " ", texto)
    return " ".join(texto.split())

def extraer_keywords(texto):
    palabras = normalizar_texto(texto).split()
    return [p for p in palabras if p not in STOP_WORDS and p not in PALABRAS_GENERICAS and len(p) > 2]

# ─── CAPA DE RED MULTI-LLAVE ─────────────────────────────────────────────────
def hacer_peticion_rotativa(url, params):
    """Ejecuta la petición y rota la llave automáticamente si recibe un 429."""
    global indice_llave_actual
    intentos = 0
    
    while intentos < len(LLAVES_API):
        llave_en_uso = LLAVES_API[indice_llave_actual]
        headers = {
            "x-rapidapi-key": llave_en_uso,
            "x-rapidapi-host": RAPIDAPI_HOST
        }
        
        try:
            r = requests.get(url, headers=headers, params=params, timeout=15)
            
            if r.status_code == 429:
                print(f"🔄 Llave {indice_llave_actual + 1} agotada. Rotando a la siguiente...")
                indice_llave_actual = (indice_llave_actual + 1) % len(LLAVES_API)
                intentos += 1
                time.sleep(1) # Pausa antes de reintentar con la nueva llave
                continue
            
            return r # Si es 200 (o cualquier otro error que no sea 429), lo devolvemos
        except Exception as e:
            print(f"⚠️ Error de red: {e}")
            return None
            
    print("❌ ERROR CRÍTICO: Todas las llaves se han agotado.")
    return None

def obtener_datos_xtream():
    print("📡 Descargando estructura de Xtream...")
    url_base = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
    try:
        r_cat = requests.get(f"{url_base}&action=get_live_categories", timeout=15)
        r_str = requests.get(f"{url_base}&action=get_live_streams", timeout=25)
        if r_cat.status_code != 200 or r_str.status_code != 200: return []

        categorias = {str(c.get("category_id", "")): c.get("category_name", "") for c in r_cat.json()}
        streams_listos = []
        for s in r_str.json():
            cat_name = categorias.get(str(s.get("category_id", "")), "")
            stream_name = s.get("name", "")
            texto_completo = f"{cat_name} {stream_name}"
            streams_listos.append({
                "id": s.get("stream_id"),
                "nombre_ui": stream_name.strip(),
                "texto_analisis": normalizar_texto(texto_completo)
            })
        return streams_listos
    except: return []

def obtener_eventos_api():
    if not LLAVES_API:
        print("❌ No se detectaron llaves API configuradas.")
        return []

    print("📅 Consultando SofaScore API (Rotación Automática)...")
    DEPORTES_IDS = {"Fútbol": 1, "Baloncesto": 2, "Tenis": 5, "Motor": 22, "Béisbol": 64}
    eventos_procesados = []
    
    for deporte, sport_id in DEPORTES_IDS.items():
        url_categorias = f"https://{RAPIDAPI_HOST}/v1/calendar/categories"
        r_cat = hacer_peticion_rotativa(url_categorias, {"sport_id": sport_id, "date": FECHA_HOY, "timezone": 0})
        
        if not r_cat or r_cat.status_code != 200: continue
            
        categorias_activas = r_cat.json().get("data", [])
        
        for cat in categorias_activas:
            cat_id = cat.get("category", {}).get("id")
            cat_nombre = cat.get("category", {}).get("name", "")
            
            # NUEVO: FILTRADO PREMATURO (Ahorro Masivo de Peticiones)
            if cat_nombre not in CATEGORIAS_PERMITIDAS:
                continue

            url_eventos = f"https://{RAPIDAPI_HOST}/v1/events/schedule/category"
            r_ev = hacer_peticion_rotativa(url_eventos, {"category_id": cat_id, "date": FECHA_HOY})
            
            if not r_ev or r_ev.status_code != 200: continue
            
            datos = r_ev.json().get("data", [])
            for ev in datos:
                torneo_data = ev.get("tournament", {})
                cat_data = torneo_data.get("category", {})
                torneo_nombre = torneo_data.get("name", "")
                pais_evento = cat_data.get("name", "")
                pais_codigo = cat_data.get("alpha2", "")
                eq_local = ev.get("homeTeam", {}).get("name", "")
                eq_visit = ev.get("awayTeam", {}).get("name", "")
                unix_time = ev.get("startTimestamp")
                
                if not unix_time: continue
                
                dt_obj = datetime.fromtimestamp(unix_time, timezone.utc)
                hora_utc_str = dt_obj.strftime("%Y-%m-%dT%H:%M:%SZ")
                hora_corta = dt_obj.strftime("%H:%M")
                tier = 3
                torneo_norm = normalizar_texto(torneo_nombre)
                
                if any(t in torneo_norm for t in TIER_1_ELITE): tier = 1
                elif any(t in torneo_norm for t in TIER_2_NICHO): tier = 2
                
                if pais_evento in PAISES_HEROE_LOCAL or pais_codigo in PAISES_HEROE_LOCAL:
                    tier = 1
                
                if tier == 3 and deporte == "Tenis" and "ITF" in torneo_norm: continue
                
                eventos_procesados.append({
                    "id": str(ev.get("id")),
                    "titulo": f"{eq_local} vs {eq_visit}" if eq_local and eq_visit else torneo_nombre,
                    "torneo": torneo_nombre,
                    "categoria": deporte,
                    "hora_utc": hora_utc_str,
                    "hora_corta": hora_corta,
                    "tier": tier,
                    "_kws_local": extraer_keywords(eq_local),
                    "_kws_visit": extraer_keywords(eq_visit),
                    "_kws_torneo": extraer_keywords(torneo_nombre)
                })
            time.sleep(0.3)
        time.sleep(1)
    return eventos_procesados

# ─── MOTOR DE PUNTUACIÓN Y ORQUESTADOR ───────────────────────────────────────
def evaluar_vinculo(evento, texto_canal):
    puntaje = 0
    if evento["hora_corta"] in texto_canal: puntaje += 30
    clocal = sum(1 for kw in evento["_kws_local"] if kw in texto_canal)
    cvisit = sum(1 for kw in evento["_kws_visit"] if kw in texto_canal)
    if clocal > 0: puntaje += 35
    if cvisit > 0: puntaje += 35
    ctorneo = sum(1 for kw in evento["_kws_torneo"] if kw in texto_canal)
    if ctorneo > 0: puntaje += 15
    if len(evento["_kws_torneo"]) > 1 and ctorneo == 1: puntaje -= 15 
    return puntaje

def buscar_fuentes(evento, streams_procesados):
    fuentes = []
    UMBRAL = 70 
    for s in streams_procesados:
        if evaluar_vinculo(evento, s["texto_analisis"]) >= UMBRAL:
            fuentes.append({"nombre": s["nombre_ui"], "url": f"{XTREAM_URL}/live/{XTREAM_USER}/{XTREAM_PASS}/{s['id']}.ts"})
    return fuentes

def main():
    print(f"🚀 Iniciando Curador Inteligente — {FECHA_HOY}")
    streams = obtener_datos_xtream()
    if not streams:
        print("❌ Error: No se pudieron cargar los streams de IPTV.")
        return
        
    eventos = obtener_eventos_api()
    print(f"🗓️ {len(eventos)} eventos de interés pre-filtrados encontrados.")

    if len(eventos) == 0:
        print("⚠️ CUIDADO: La API no devolvió eventos hoy o las llaves fallaron. Abortando curación.")
        return

    resultados = []
    for ev in eventos:
        fuentes = buscar_fuentes(ev, streams)
        if fuentes:
            resultados.append({
                "id": ev["id"], "titulo": ev["titulo"], "torneo": ev["torneo"],
                "categoria": ev["categoria"], "hora_utc": ev["hora_utc"], "tier": ev["tier"], "fuentes": fuentes
            })
            
    alta_prioridad = [e for e in resultados if e.get("tier") == 1 or e.get("tier") == 2]
    if len(alta_prioridad) > 25: resultados = alta_prioridad
    
    resultados.sort(key=lambda x: x["hora_utc"])
    for r in resultados: r.pop("tier", None)

    with open("eventos_hoy.json", "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)

    print(f"🏁 Curación finalizada. {len(resultados)} eventos guardados.")

if __name__ == "__main__":
    main()
