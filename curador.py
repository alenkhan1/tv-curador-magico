import os
import time
import json
import requests
import re
from datetime import datetime, timedelta

# Credenciales
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
XTREAM_URL = os.environ.get("XTREAM_URL")
XTREAM_USER = os.environ.get("XTREAM_USER")
XTREAM_PASS = os.environ.get("XTREAM_PASS")

# Diccionarios de Géneros (Símbolos Temáticos Minimalistas)
TMDB_GENRES = {
    28: "🎬 Acción", 12: "🗺️ Aventura", 16: "🎨 Animación", 35: "😂 Comedia", 
    80: "🕵️ Crimen", 99: "🎞️ Documental", 18: "🎭 Drama", 10751: "👨‍👩‍👧‍👦 Familia", 
    14: "✨ Fantasía", 36: "🏛️ Historia", 27: "💀 Terror", 10402: "🎵 Música", 
    9648: "🔍 Misterio", 10749: "❤️ Romance", 878: "🚀 Ciencia Ficción", 
    10770: "📺 Película de TV", 53: "😱 Suspense", 10752: "⚔️ Bélica", 37: "🤠 Western"
}

TMDB_SERIES_GENRES = {
    10759: "🎬 Acción y Aventura", 16: "🎨 Animación", 35: "😂 Comedia", 
    80: "🕵️ Crimen", 99: "🎞️ Documental", 18: "🎭 Drama", 10751: "👨‍👩‍👧‍👦 Familia", 
    10762: "🧸 Infantil", 9648: "🔍 Misterio", 
    10764: "📺 Reality", 10765: "🚀 Sci-Fi & Fantasy", 10766: "🧼 Telenovela", 
    10767: "🗣️ Talk Show", 10768: "⚔️ Guerra y Política", 37: "🤠 Western"
}

# Listas Negras (Filtro Implacable)
BANNED_CATEGORY_KEYWORDS = [
    "XXX", "+18", "18+", "ADULTO", "HENTAI", "ONLYFANS", "BRAZZER", "PORN",
    "GANGBANG", "CAM |", "VOD CAM", "XXXCOLOMBIA", "XXXESPAÑA", "XXXMEXICO",
    "XXXMIX", "XXXPERU", "24/7"
]

BANNED_STREAM_KEYWORDS = [
    " HDCAM", " TS-SCREENER", " CAMRIP", " TELESYNC", " TS "
]

# El orden estricto (Símbolos Temáticos Minimalistas)
ORDEN_CATEGORIAS = [
    "✧ Estrenos", "★ Clásicos", "📼 Retro",
    "🎬 Acción", "🎬 Acción y Aventura", "🚀 Ciencia Ficción", "🚀 Sci-Fi & Fantasy",
    "💀 Terror", "😱 Suspense", "🔍 Misterio", "🕵️ Crimen",
    "😂 Comedia", "❤️ Romance", "🎭 Drama", "🧼 Telenovela",
    "🎨 Animación", "👨‍👩‍👧‍👦 Familia", "🧸 Infantil", "✨ Fantasía", "🗺️ Aventura",
    "🎞️ Documental", "🏛️ Historia", "⚔️ Bélica", "⚔️ Guerra y Política",
    "🎵 Música", "📺 Película de TV", "📺 Reality", "🗣️ Talk Show", "📰 Noticias", "🤠 Western"
]

# Cálculos de fechas (se calculan en tiempo de ejecución, no de importación)
HOY = None
FECHA_ESTRENOS_LIMITE = None
AÑO_ACTUAL = None


def obtener_categorias_xtream(action):
    print(f"Descargando nombres de carpetas ({action}) de Xtream...")
    url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action={action}"
    mapa = {}
    try:
        req = requests.get(url, timeout=15)
        if req.status_code == 200:
            categorias = req.json()
            for cat in categorias:
                cat_id = str(cat.get("category_id", ""))
                cat_name = cat.get("category_name", "")
                mapa[cat_id] = cat_name
    except Exception as e:
        print(f"[ERROR] No se pudo obtener categorías de Xtream ({action}): {e}. El filtro de contenido adulto puede estar inactivo.")
    return mapa

def es_contenido_prohibido(nombre_crudo, category_id, mapa_categorias):
    # 1. Revisar nombre de la carpeta
    cat_name = mapa_categorias.get(str(category_id), "").upper()
    for kw in BANNED_CATEGORY_KEYWORDS:
        if kw in cat_name:
            return True
            
    # 2. Revisar nombre del video (con espacios para no bloquear palabras como "Cameron")
    nombre_upper = nombre_crudo.upper()
    for kw in BANNED_STREAM_KEYWORDS:
        if kw in nombre_upper:
            return True
            
    return False


def obtener_xtream(action):
    print(f"Descargando catálogo ({action}) de Xtream...")
    url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action={action}"
    try:
        req = requests.get(url, timeout=15)
        return req.json()
    except Exception as e:
        print(f"Error conectando a Xtream: {e}")
        return []

def limpiar_titulo(nombre):
    # Quitar años y contenido entre corchetes
    limpio = re.sub(r'\(\d{4}\)|\(.*?\)|\[.*?\]', '', nombre)
    limpio = re.sub(r'\.mp4|\.mkv|\.avi', '', limpio, flags=re.IGNORECASE)
    # Limpieza súper agresiva de tags piratas
    tags = ["1080p", "720p", "4k", "fhd", "hd", "latino", "español", "castellano", "dual", "vod", "subtitulado", "audio"]
    for tag in tags:
        limpio = re.sub(rf'\b{tag}\b', '', limpio, flags=re.IGNORECASE)
    limpio = limpio.replace("-", " ").replace("|", " ").replace("_", " ")
    return " ".join(limpio.split())

_tmdb_cache = {}

def buscar_info_tmdb(titulo, año_original=0, es_serie=False):
    cache_key = (titulo.lower(), año_original, es_serie)
    if cache_key in _tmdb_cache:
        return _tmdb_cache[cache_key]
    tipo = "tv" if es_serie else "movie"
    url = f"https://api.themoviedb.org/3/search/{tipo}?api_key={TMDB_API_KEY}&query={titulo}&language=es-ES"
    
    # MAGIA: Exigir año si lo tenemos para evitar posters falsos
    if año_original > 0 and not es_serie:
        url += f"&year={año_original}"
    elif año_original > 0 and es_serie:
        url += f"&first_air_date_year={año_original}"
        
    try:
        req = requests.get(url, timeout=10)
        if req.status_code == 200:
            data = req.json()
            if data.get("results") and len(data["results"]) > 0:
                item = data["results"][0]
                
                # 1. Póster HD Estricto (Sin póster no hay fiesta)
                poster_path = item.get("poster_path")
                if not poster_path:
                    return None # Ignorar por completo
                poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}"
                
                # 2. Buscar Género Válido (Adiós "Otros")
                genre_ids = item.get("genre_ids", [])
                diccionario = TMDB_SERIES_GENRES if es_serie else TMDB_GENRES
                genero_principal = None
                for gid in genre_ids:
                    if gid in diccionario:
                        genero_principal = diccionario[gid]
                        break
                if not genero_principal:
                    return None # Si no encaja en géneros, se descarta
                
                # 3. Extraer Fecha y Año
                fecha_str = item.get("first_air_date") if es_serie else item.get("release_date")
                año = 0
                fecha_obj = None
                if fecha_str and "-" in fecha_str:
                    año = int(fecha_str.split("-")[0])
                    try:
                        fecha_obj = datetime.strptime(fecha_str, "%Y-%m-%d")
                    except ValueError:
                        pass
                        
                vote_avg = float(item.get("vote_average", 0))
                vote_count = int(item.get("vote_count", 0))
                titulo_oficial = item.get("name") if es_serie else item.get("title")
                tmdb_id = str(item.get("id", ""))
                
                resultado = genero_principal, fecha_obj, año, vote_avg, vote_count, titulo_oficial, poster_url, tmdb_id
        _tmdb_cache[cache_key] = resultado
        return resultado
    except Exception:
        pass
    
    return None

def organizar_diccionario(diccionario_desordenado):
    # Ordena el diccionario final según la lista ORDEN_CATEGORIAS
    diccionario_ordenado = {}
    for cat in ORDEN_CATEGORIAS:
        if cat in diccionario_desordenado and len(diccionario_desordenado[cat]) > 0:
            diccionario_ordenado[cat] = diccionario_desordenado[cat]
            
    # Añadir cualquier categoría que se nos haya escapado de la lista maestra
    for cat, ids in diccionario_desordenado.items():
        if cat not in diccionario_ordenado and len(ids) > 0:
            diccionario_ordenado[cat] = ids
            
    return diccionario_ordenado

def procesar_catalogo(items, es_serie, mapa_curado, mapa_categorias):
    tipo_str = "series" if es_serie else "movies"
    id_key = "series_id" if es_serie else "stream_id"
    print(f"Procesando {len(items)} {tipo_str}...")
    
    temp_map = {}
    contadores = {"adulto": 0, "sin_tmdb": 0, "sin_poster": 0, "sin_genero": 0, "sin_regla": 0, "aceptados": 0}
    
    for item in items:
        stream_id = int(item.get(id_key, 0))
        category_id = str(item.get("category_id", ""))
        nombre_original = item.get("name", "").strip()
        
        # EL CADENERO: Bloqueo de origen
        if es_contenido_prohibido(nombre_original, category_id, mapa_categorias):
            contadores["adulto"] += 1
            continue
            
        titulo_limpio = limpiar_titulo(nombre_original)
        
        año_original = 0
        match_año = re.search(r'\((\d{4})\)', nombre_original)
        if match_año:
            año_original = int(match_año.group(1))
            
        resultado_tmdb = buscar_info_tmdb(titulo_limpio, año_original, es_serie)
        if not resultado_tmdb:
            contadores["sin_tmdb"] += 1
            continue
            
        genero, fecha_obj, año, rating, votos, titulo_oficial, poster_url, tmdb_id = resultado_tmdb
        nombre_final = titulo_oficial if titulo_oficial else titulo_limpio
        año_final = año if año > 0 else año_original
        
        categorias_asignadas = []
        es_clasico = False
        es_retro = False
        
        # REGLAS DEL TIEMPO
        # 1. 👑 Clásicos (Estricto: Año <= 2000, > 7.8 nota, > 2000 votos)
        if año_final <= 2000 and rating >= 7.8 and votos >= 2000:
            categorias_asignadas.append("★ Clásicos")
            es_clasico = True
            
        # 2. 📻 Retro (Exclusivo: 1970 - 1999, que NO sea Clásico)
        if 1970 <= año_final <= 1999 and not es_clasico:
            categorias_asignadas.append("📼 Retro")
            es_retro = True
            
        # 3. 🆕 Estrenos (Últimos 6 meses reales)
        if fecha_obj and fecha_obj >= FECHA_ESTRENOS_LIMITE:
            if año_original == 0 or año_original >= (AÑO_ACTUAL - 1):
                categorias_asignadas.append("✧ Estrenos")
                
        # 4. Vitrinas de Género (Solo de los últimos 5 años, o si es retro/clásico se deja en su propia sección)
        if año_final >= (AÑO_ACTUAL - 5):
            categorias_asignadas.append(genero)
            
        if not categorias_asignadas:
            contadores["sin_regla"] += 1
            continue
        contadores["aceptados"] += 1
            
        nuevo_item = {
            "id": stream_id,
            "clean_name": nombre_final,
            "year": año_final,
            "poster": poster_url,
            "tmdb_id": tmdb_id
        }
            
        for cat in categorias_asignadas:
            if cat not in temp_map:
                temp_map[cat] = []
            if not any(obj["clean_name"] == nombre_final for obj in temp_map[cat]):
                temp_map[cat].append(nuevo_item)
                
                    time.sleep(0.2)
        
    mapa_curado[tipo_str] = organizar_diccionario(temp_map)
    print(f"  ✅ Aceptados: {contadores['aceptados']} | 🔞 Adulto: {contadores['adulto']} | ❌ Sin TMDB/póster/género: {contadores['sin_tmdb']} | 📭 Sin regla temporal: {contadores['sin_regla']}")

def main():
    credenciales_requeridas = {
        "TMDB_API_KEY": TMDB_API_KEY,
        "XTREAM_URL": XTREAM_URL,
        "XTREAM_USER": XTREAM_USER,
        "XTREAM_PASS": XTREAM_PASS,
    }
    for nombre, valor in credenciales_requeridas.items():
        if not valor:
            print(f"[ERROR FATAL] Falta el secreto '{nombre}'. Abortando ejecución.")
            return

    global HOY, FECHA_ESTRENOS_LIMITE, AÑO_ACTUAL
    HOY = datetime.now()
    FECHA_ESTRENOS_LIMITE = HOY - timedelta(days=180)
    AÑO_ACTUAL = HOY.year
    
    mapa_curado = {"movies": {}, "series": {}}
    
    # 1. Procesar Películas
    cat_peliculas = obtener_categorias_xtream("get_vod_categories")
    peliculas = obtener_xtream("get_vod_streams")
    if peliculas:
        procesar_catalogo(peliculas, False, mapa_curado, cat_peliculas)
        
    # 2. Procesar Series
    cat_series = obtener_categorias_xtream("get_series_categories")
    series = obtener_xtream("get_series")
    if series:
        procesar_catalogo(series, True, mapa_curado, cat_series)

    # 3. Guardar el archivo
    with open("catalogo_curado.json", "w", encoding="utf-8") as f:
        json.dump(mapa_curado, f, ensure_ascii=False, indent=2)
        
    print("¡Catálogo curado y ordenado exitosamente!")

if __name__ == "__main__":
    main()
