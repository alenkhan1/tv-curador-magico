import os
import re
import json
import time
import requests
import unicodedata
from datetime import datetime, timezone

# ─── CONFIGURACIÓN DE ENTORNO ────────────────────────────────────────────────
XTREAM_URL       = os.environ.get("XTREAM_URL")
XTREAM_USER      = os.environ.get("XTREAM_USER")
XTREAM_PASS      = os.environ.get("XTREAM_PASS")
RAPIDAPI_KEY     = os.environ.get("RAPIDAPI_KEY") 
RAPIDAPI_HOST    = "sofasport.p.rapidapi.com"
FECHA_HOY        = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ─── DICCIONARIOS Y REGLAS DE NEGOCIO ────────────────────────────────────────

LEET_DICT = {
    "4": "A", "@": "A", "3": "E", "€": "E", "1": "I", "¡": "I", "|": "I",
    "0": "O", "Ø": "O", "5": "S", "$": "S", "7": "T", "8": "B", "ñ": "N", "Ñ": "N"
}

# Palabras que no suman puntos en el motor de búsqueda (Anti-Basura)
PALABRAS_GENERICAS = {
    "WOMEN", "MEN", "CUP", "LEAGUE", "LIVE", "FHD", "4K", "1080P", "720P", 
    "CHAMPIONSHIP", "TOUR", "QUALIFICATION", "TV", "SPORTS", "VS"
}

STOP_WORDS = {
    "FC", "SC", "CF", "AC", "AS", "US", "CS", "RC", "CD", "SD", "UD",
    "THE", "DE", "LA", "LAS", "LOS", "EL", "Y", "E", "AND", "OF", "DEL"
}

# Sistema de Capas (Tiers)
TIER_1_ELITE = ["CHAMPIONS LEAGUE", "LIGA BETPLAY", "LA LIGA", "PREMIER LEAGUE", "SERIE A", "FORMULA 1", "NBA", "UFC", "LIBERTADORES", "MUNDIAL"]
TIER_2_NICHO = ["NFL", "MLB", "F2", "F3", "MOTO GP", "COPA SUDAMERICANA", "EREDIVISIE", "BUNDESLIGA"]

PAISES_HEROE_LOCAL = ["Colombia", "Spain", "CO", "ES"]

# ─── UTILIDADES DE TEXTO ─────────────────────────────────────────────────────

def normalizar_texto(texto):
    """Limpia, quita tildes, resuelve leetspeak y pasa a mayúsculas."""
    if not texto: return ""
    texto = str(texto).upper()
    for leet, real in LEET_DICT.items():
        texto = texto.replace(leet, real)
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^A-Z0-9\s:]", " ", texto) # Mantenemos los dos puntos para las horas
    return " ".join(texto.split())

def extraer_keywords(texto):
    """Extrae palabras clave ignorando Stop Words y Genéricas."""
    palabras = normalizar_texto(texto).split()
    return [p for p in palabras if p not in STOP_WORDS and p not in PALABRAS_GENERICAS and len(p) > 2]

# ─── CAPA DE RED (API Y XTREAM) ──────────────────────────────────────────────

def obtener_datos_xtream():
    """Obtiene y preprocesa los canales de IPTV en una sola pasada."""
    print("📡 Descargando estructura de Xtream...")
    url_base = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
    
    try:
        r_cat = requests.get(f"{url_base}&action=get_live_categories", timeout=15)
        r_str = requests.get(f"{url_base}&action=get_live_streams", timeout=25)
        
        if r_cat.status_code != 200 or r_str.status_code != 200:
            return []

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
    except Exception as e:
        print(f"❌ Error conectando a Xtream: {e}")
        return []

def obtener_eventos_api():
    """Descarga los eventos del día y extrae datos clave de nacionalidad y horas."""
    if not RAPIDAPI_KEY:
        return []

    print("📅 Consultando SofaScore API...")
    url = f"https://{RAPIDAPI_HOST}/v1/events/schedule/date"
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST
    }
    
    # Reducimos los IDs a los deportes estrictamente necesarios para evitar basura
    DEPORTES_IDS = {"Fútbol": 1, "Baloncesto": 2, "Motor": 10, "Tenis": 3, "Béisbol": 5}
    eventos_procesados = []
    
    for deporte, sport_id in DEPORTES_IDS.items():
        try:
            r = requests.get(url, headers=headers, params={"date": FECHA_HOY, "sport_id": sport_id}, timeout=15)
            if r.status_code != 200: continue
            
            datos = r.json().get("events", [])
            for ev in datos:
                # Extracción segura de datos anidados
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
                hora_corta = dt_obj.strftime("%H:%M") # Formato HH:MM para cruzar con IPTV
                
                # REGLA: El Héroe Local y Tiers
                tier = 3 # Por defecto: Relleno
                torneo_norm = normalizar_texto(torneo_nombre)
                
                if any(t in torneo_norm for t in TIER_1_ELITE): tier = 1
                elif any(t in torneo_norm for t in TIER_2_NICHO): tier = 2
                
                if pais_evento in PAISES_HEROE_LOCAL or pais_codigo in PAISES_HEROE_LOCAL:
                    tier = 1 # Promoción automática
                
                # REGLA: Filtro de popularidad básico (Evita torneos ITF o juveniles)
                if tier == 3 and deporte == "Tenis" and "ITF" in torneo_norm:
                    continue # Descartar basura conocida de inmediato
                
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
        except Exception as e:
            pass
        time.sleep(1)
        
    return eventos_procesados

# ─── MOTOR DE PUNTUACIÓN (SCORING) ───────────────────────────────────────────

def evaluar_vinculo(evento, texto_canal):
    """Asigna un puntaje de confianza a un vínculo IPTV basado en reglas estrictas."""
    puntaje = 0
    
    # 1. Triaje de Hora (Si tiene la hora exacta del evento, gana un bonus masivo)
    if evento["hora_corta"] in texto_canal:
        puntaje += 30

    # 2. Análisis de Entidades Fuertes (Equipos)
    coincidencias_local = sum(1 for kw in evento["_kws_local"] if kw in texto_canal)
    coincidencias_visit = sum(1 for kw in evento["_kws_visit"] if kw in texto_canal)
    
    if coincidencias_local > 0: puntaje += 35
    if coincidencias_visit > 0: puntaje += 35

    # 3. Análisis de Entidades Débiles (Torneo)
    coincidencias_torneo = sum(1 for kw in evento["_kws_torneo"] if kw in texto_canal)
    if coincidencias_torneo > 0: puntaje += 15
    
    # Regla Anti-Basura: Si el torneo tiene 2+ palabras pero el canal solo empareja 1, no damos puntos de torneo
    if len(evento["_kws_torneo"]) > 1 and coincidencias_torneo == 1:
        puntaje -= 15 

    return puntaje

def buscar_fuentes(evento, streams_procesados):
    fuentes = []
    # UMBRAL DE CONFIANZA: Mínimo 70 puntos para aprobar. 
    # Esto exige que o bien encuentra ambos equipos (70pts), 
    # o encuentra un equipo + hora exacta (65pts) + torneo (15pts).
    UMBRAL = 70 

    for s in streams_procesados:
        score = evaluar_vinculo(evento, s["texto_analisis"])
        if score >= UMBRAL:
            fuentes.append({
                "nombre": s["nombre_ui"],
                "url": f"{XTREAM_URL}/live/{XTREAM_USER}/{XTREAM_PASS}/{s['id']}.ts"
            })
            
    return fuentes

# ─── ORQUESTADOR PRINCIPAL ───────────────────────────────────────────────────

def main():
    print(f"🚀 Iniciando Curador Inteligente — {FECHA_HOY}")

    streams = obtener_datos_xtream()
    if not streams:
        print("❌ Error: No se pudieron cargar los streams de IPTV.")
        return
        
    eventos = obtener_eventos_api()
    print(f"🗓️ {len(eventos)} eventos pre-filtrados encontrados.")

    resultados = []
    for ev in eventos:
        fuentes = buscar_fuentes(ev, streams)
        if fuentes:
            resultados.append({
                "id": ev["id"],
                "titulo": ev["titulo"],
                "torneo": ev["torneo"],
                "categoria": ev["categoria"],
                "hora_utc": ev["hora_utc"],
                "tier": ev["tier"],
                "fuentes": fuentes
            })
            
    # REGLA DE DENSIDAD: Evitar la estantería vacía, pero no saturar de basura
    eventos_alta_prioridad = [e for e in resultados if e["tier"] in]
    
    if len(eventos_alta_prioridad) > 25:
        # Si hay muchos eventos buenos (ej. Fin de semana), eliminamos la Capa 3 (Relleno)
        print("📊 Alta densidad detectada. Descartando eventos de Tier 3.")
        resultados = eventos_alta_prioridad
    else:
        # Si hay pocos eventos, dejamos la Capa 3 para que el usuario tenga opciones
        print("📊 Baja densidad. Manteniendo eventos de Tier 3 para rellenar.")

    # Ordenar por fecha y limpiar el campo tier temporal
    resultados.sort(key=lambda x: x["hora_utc"])
    for r in resultados:
        r.pop("tier", None)

    with open("eventos_hoy.json", "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)

    print(f"🏁 Curación finalizada. {len(resultados)} eventos guardados con alta precisión.")

if __name__ == "__main__":
    main()
