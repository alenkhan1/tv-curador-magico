import os
import time
import json
import requests
import re

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
XTREAM_URL = os.environ.get("XTREAM_URL")
XTREAM_USER = os.environ.get("XTREAM_USER")
XTREAM_PASS = os.environ.get("XTREAM_PASS")

TMDB_GENRES = {
    28: "💥 Acción", 12: "🗺️ Aventura", 16: "🎨 Animación", 35: "😂 Comedia", 
    80: "🕵️ Crimen", 99: "🎬 Documental", 18: "🎭 Drama", 10751: "👨‍👩‍👧‍👦 Familia", 
    14: "🧙‍♂️ Fantasía", 36: "🏛️ Historia", 27: "👻 Terror", 10402: "🎵 Música", 
    9648: "🔍 Misterio", 10749: "❤️ Romance", 878: "🚀 Ciencia Ficción", 
    10770: "📺 Película de TV", 53: "😱 Suspense", 10752: "⚔️ Bélica", 37: "🤠 Western"
}

def obtener_peliculas_xtream():
    print("Descargando catálogo de Xtream...")
    url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_vod_streams"
    try:
        req = requests.get(url, timeout=15)
        return req.json()
    except Exception as e:
        print(f"Error conectando a Xtream: {e}")
        return []

def limpiar_titulo(nombre):
    # Eliminar textos entre paréntesis () y corchetes []
    limpio = re.sub(r'\(.*?\)|\[.*?\]', '', nombre)
    # Eliminar extensiones
    limpio = re.sub(r'\.mp4|\.mkv|\.avi', '', limpio, flags=re.IGNORECASE)
    # Eliminar resoluciones o tags sueltos comunes
    tags = ["1080p", "720p", "4k", "fhd", "hd", "latino", "español", "castellano", "dual", "vod"]
    for tag in tags:
        limpio = re.sub(rf'\b{tag}\b', '', limpio, flags=re.IGNORECASE)
    # Quitar guiones o barras extras que hayan quedado
    limpio = limpio.replace("-", " ").replace("|", " ").replace("_", " ")
    # Limpiar espacios dobles
    return " ".join(limpio.split())

def buscar_genero_tmdb(titulo):
    url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={titulo}&language=es-ES"
    try:
        req = requests.get(url, timeout=10)
        if req.status_code != 200:
            print(f"  [!] Error de API TMDB ({req.status_code}): Revise su API KEY.")
            return "🍿 Otros"
            
        data = req.json()
        if data.get("results") and len(data["results"]) > 0:
            genre_ids = data["results"][0].get("genre_ids", [])
            if genre_ids:
                return TMDB_GENRES.get(genre_ids[0], "🍿 Otros")
    except Exception as e:
        print(f"  [!] Error de red con TMDB: {e}")
    return "🍿 Otros"

def main():
    peliculas = obtener_peliculas_xtream()
    if not peliculas:
        return

    mapa_curado = {"movies": {}, "series": {}}
    peliculas_a_procesar = peliculas
    
    print(f"Procesando {len(peliculas_a_procesar)} películas con TMDB...")
    
    for vod in peliculas_a_procesar:
        stream_id = int(vod.get("stream_id", 0))
        nombre_original = vod.get("name", "").strip()
        
        titulo_limpio = limpiar_titulo(nombre_original)
        genero = buscar_genero_tmdb(titulo_limpio)
        
        print(f"Original: '{nombre_original}' | Buscado: '{titulo_limpio}' -> Género: {genero}")
        
        if genero not in mapa_curado["movies"]:
            mapa_curado["movies"][genero] = []
            
        mapa_curado["movies"][genero].append(stream_id)
        time.sleep(0.3)

    with open("catalogo_curado.json", "w", encoding="utf-8") as f:
        json.dump(mapa_curado, f, ensure_ascii=False, indent=2)
        
    print("¡Catálogo curado generado exitosamente!")

if __name__ == "__main__":
    main()
