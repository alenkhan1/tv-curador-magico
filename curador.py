import os
import time
import json
import requests

# 1. Leer las credenciales ocultas desde GitHub Secrets
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
XTREAM_URL = os.environ.get("XTREAM_URL")
XTREAM_USER = os.environ.get("XTREAM_USER")
XTREAM_PASS = os.environ.get("XTREAM_PASS")

# Mapeo básico de IDs de géneros de TMDB a nombres en español
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

def buscar_genero_tmdb(titulo):
    url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={titulo}&language=es-ES"
    try:
        req = requests.get(url, timeout=10)
        data = req.json()
        if data.get("results") and len(data["results"]) > 0:
            # Tomamos el primer resultado
            genre_ids = data["results"][0].get("genre_ids", [])
            if genre_ids:
                # Devolvemos el nombre del primer género que coincida
                return TMDB_GENRES.get(genre_ids[0], "🍿 Otros")
    except Exception as e:
        pass
    return "🍿 Otros"

def main():
    peliculas = obtener_peliculas_xtream()
    if not peliculas:
        return

    # Estructura del mapa final
    mapa_curado = {"movies": {}, "series": {}}
    
    # PARA LA PRUEBA: Solo procesaremos las primeras 50 películas para no tardar 1 hora
    # En la versión final quitaremos este límite
    peliculas_a_procesar = peliculas[:50]
    
    print(f"Procesando {len(peliculas_a_procesar)} películas con TMDB...")
    
    for vod in peliculas_a_procesar:
        stream_id = int(vod.get("stream_id", 0))
        nombre = vod.get("name", "").strip()
        
        # Limpiar años del titulo ej: "Buscando a Nemo (2003)" -> "Buscando a Nemo"
        titulo_limpio = nombre.split("(")[0].strip()
        
        genero = buscar_genero_tmdb(titulo_limpio)
        
        if genero not in mapa_curado["movies"]:
            mapa_curado["movies"][genero] = []
            
        mapa_curado["movies"][genero].append(stream_id)
        
        # Respetar el límite de TMDB (aprox 4 peticiones por segundo)
        time.sleep(0.3)

    # Guardar el JSON resultante
    with open("catalogo_curado.json", "w", encoding="utf-8") as f:
        json.dump(mapa_curado, f, ensure_ascii=False, indent=2)
        
    print("¡Catálogo curado generado exitosamente!")

if __name__ == "__main__":
    main()
