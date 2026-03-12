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

# Diccionarios de Géneros
TMDB_GENRES = {
    28: "💥 Acción", 12: "🗺️ Aventura", 16: "🎨 Animación", 35: "😂 Comedia", 
    80: "🕵️ Crimen", 99: "🎬 Documental", 18: "🎭 Drama", 10751: "👨‍👩‍👧‍👦 Familia", 
    14: "🧙‍♂️ Fantasía", 36: "🏛️ Historia", 27: "👻 Terror", 10402: "🎵 Música", 
    9648: "🔍 Misterio", 10749: "❤️ Romance", 878: "🚀 Ciencia Ficción", 
    10770: "📺 Película de TV", 53: "😱 Suspense", 10752: "⚔️ Bélica", 37: "🤠 Western"
}

TMDB_SERIES_GENRES = {
    10759: "💥 Acción y Aventura", 16: "🎨 Animación", 35: "😂 Comedia", 
    80: "🕵️ Crimen", 99: "🎬 Documental", 18: "🎭 Drama", 10751: "👨‍👩‍👧‍👦 Familia", 
    10762: "🧸 Infantil", 9648: "🔍 Misterio", 10763: "📰 Noticias", 
    10764: "📺 Reality", 10765: "🚀 Sci-Fi & Fantasy", 10766: "🧼 Telenovela", 
    10767: "🗣️ Talk Show", 10768: "⚔️ Guerra y Política", 37: "🤠 Western"
}

# El orden estricto en el que quieres que aparezcan en la TV
ORDEN_CATEGORIAS = [
    "🆕 Estrenos", "👑 Clásicos", "📻 Retro",
    "💥 Acción", "💥 Acción y Aventura", "🚀 Ciencia Ficción", "🚀 Sci-Fi & Fantasy",
    "👻 Terror", "😱 Suspense", "🔍 Misterio", "🕵️ Crimen",
    "😂 Comedia", "❤️ Romance", "🎭 Drama", "🧼 Telenovela",
    "🎨 Animación", "👨‍👩‍👧‍👦 Familia", "🧸 Infantil", "🧙‍♂️ Fantasía", "🗺️ Aventura",
    "🎬 Documental", "🏛️ Historia", "⚔️ Bélica", "⚔️ Guerra y Política",
    "🎵 Música", "📺 Película de TV", "📺 Reality", "🗣️ Talk Show", "📰 Noticias", "🤠 Western",
    "🍿 Otros"
]

# Cálculos de fechas
HOY = datetime.now()
FECHA_ESTRENOS_LIMITE = HOY - timedelta(days=180) # Hace 6 meses
AÑO_ACTUAL = HOY.year

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

def buscar_info_tmdb(titulo, es_serie=False):
    tipo = "tv" if es_serie else "movie"
    url = f"https://api.themoviedb.org/3/search/{tipo}?api_key={TMDB_API_KEY}&query={titulo}&language=es-ES"
    
    try:
        req = requests.get(url, timeout=10)
        if req.status_code == 200:
            data = req.json()
            if data.get("results") and len(data["results"]) > 0:
                item = data["results"][0]
                
                # 1. Buscar el primer género válido en nuestra lista
                genre_ids = item.get("genre_ids", [])
                diccionario = TMDB_SERIES_GENRES if es_serie else TMDB_GENRES
                genero_principal = "🍿 Otros"
                for gid in genre_ids:
                    if gid in diccionario:
                        genero_principal = diccionario[gid]
                        break
                
                # 2. Extraer Fecha, Año, Rating y Votos
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
                
                # 3. NUEVO: Obtener póster HD y título oficial limpio directamente de TMDB
                poster_path = item.get("poster_path")
                poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else ""
                titulo_oficial = item.get("name") if es_serie else item.get("title")
                
                return genero_principal, fecha_obj, año, vote_avg, vote_count, titulo_oficial, poster_url
    except Exception:
        pass
    
    return "🍿 Otros", None, 0, 0.0, 0, "", ""

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

def procesar_catalogo(items, es_serie, mapa_curado):
    tipo_str = "series" if es_serie else "movies"
    id_key = "series_id" if es_serie else "stream_id"
    print(f"Procesando {len(items)} {tipo_str}...")
    
    # Diccionario temporal
    temp_map = {}
    
    for item in items:
        stream_id = int(item.get(id_key, 0))
        nombre_original = item.get("name", "").strip()
        titulo_limpio = limpiar_titulo(nombre_original)
        
        # NUEVO: Extraer año original para evitar que TMDB nos engañe con remakes o documentales
        año_original = 0
        match_año = re.search(r'\((\d{4})\)', nombre_original)
        if match_año:
            año_original = int(match_año.group(1))
            
        genero, fecha_obj, año, rating, votos, titulo_oficial, poster_url = buscar_info_tmdb(titulo_limpio, es_serie)
        
        # Consolidar los datos finales a guardar
        nombre_final = titulo_oficial if titulo_oficial else titulo_limpio
        año_final = año if año > 0 else año_original
        
        categorias_asignadas = []
        
        # A) Categoría de Género Principal
        categorias_asignadas.append(genero)
        
        # B) Regla 🆕 Estrenos (Últimos 6 meses) ESTRICTA
        es_estreno_valido = False
        if fecha_obj and fecha_obj >= FECHA_ESTRENOS_LIMITE:
            # Si TMDB dice que es estreno, pero el título original de Xtream decía (1998), lo descartamos
            if año_original == 0 or año_original >= (AÑO_ACTUAL - 1):
                es_estreno_valido = True
                
        if es_estreno_valido:
            categorias_asignadas.append("🆕 Estrenos")
            
        # C) Regla 📻 Retro (1970 - 1999)
        if 1970 <= año_final <= 1999:
            categorias_asignadas.append("📻 Retro")
            
        # D) Regla 👑 Clásicos (Nota > 7.8, Votos > 2000, Más de 15 años)
        if rating >= 7.8 and votos >= 2000 and año_final <= (AÑO_ACTUAL - 15) and año_final > 0:
            categorias_asignadas.append("👑 Clásicos")
            
        # NUEVO: Crear el objeto JSON enriquecido (ya no es solo un número ID)
        nuevo_item = {
            "id": stream_id,
            "clean_name": nombre_final,
            "year": año_final,
            "poster": poster_url
        }
            
        # Guardar el objeto en todas las categorías que le tocaron
        for cat in categorias_asignadas:
            if cat not in temp_map:
                temp_map[cat] = []
            # Evitar duplicados comprobando si el ID ya existe en esta categoría
            if not any(obj["id"] == stream_id for obj in temp_map[cat]):
                temp_map[cat].append(nuevo_item)
                
        time.sleep(0.2)
        
    # Aplicar el orden estricto antes de guardar
    mapa_curado[tipo_str] = organizar_diccionario(temp_map)

def main():
    mapa_curado = {"movies": {}, "series": {}}
    
    # 1. Procesar Películas
    peliculas = obtener_xtream("get_vod_streams")
    if peliculas:
        procesar_catalogo(peliculas, False, mapa_curado)
        
    # 2. Procesar Series
    series = obtener_xtream("get_series")
    if series:
        procesar_catalogo(series, True, mapa_curado)

    # 3. Guardar el archivo
    with open("catalogo_curado.json", "w", encoding="utf-8") as f:
        json.dump(mapa_curado, f, ensure_ascii=False, indent=2)
        
    print("¡Catálogo curado y ordenado exitosamente!")

if __name__ == "__main__":
    main()
