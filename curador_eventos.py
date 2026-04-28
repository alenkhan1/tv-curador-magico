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
RAPIDAPI_KEY     = os.environ.get("RAPIDAPI_KEY", "b17398907amshc7271e57c364f89p1ef63ajsn80a1eec7990c")
RAPIDAPI_HOST    = "sofasport.p.rapidapi.com"
FECHA_HOY        = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ─── EL ROMPECÓDIGOS (Diccionario Anti-Leetspeak) ────────────────────────────
LEET_DICT = {
    "4": "A", "@": "A", 
    "3": "E", "€": "E", 
    "1": "I", "¡": "I", "|": "I",
    "0": "O", "Ø": "O",
    "5": "S", "$": "S",
    "7": "T",
    "8": "B",
    "ñ": "N", "Ñ": "N"
}

# ─── PALABRAS CLAVE UNIVERSALES (Red de Arrastre para Deportes Individuales) ─
# Si TheSportsDB dice que hay un evento de estas categorías, tomaremos TODOS 
# los canales que contengan alguna de estas palabras en su nombre o carpeta.
KEYWORDS_DEPORTES_INDIVIDUALES = {
    "Tenis": ["ATP", "WTA", "TENIS", "TENNIS", "ROLAND GARROS", "WIMBLEDON", "GRAND SLAM"],
    "Motor": ["F1", "FORMULA 1", "MOTO GP", "MOTOGP", "NASCAR", "INDYCAR", "MOTOR", "RALLY", "DAKAR"],
    "Combate": ["UFC", "MMA", "BOX", "BOXEO", "BOXING", "WWE", "VELADA"],
    "Ciclismo": ["CICLISMO", "TOUR DE FRANCE", "GIRO DE ITALIA", "VUELTA A ESPANA", "UCI"],
    "Golf": ["PGA", "GOLF", "MASTERS", "RYDER CUP", "LIV GOLF"],
    "Olimpiadas": ["JUEGOS OLIMPICOS", "OLYMPICS", "PARIS 2024"] # Adaptable al año
}

# Deportes que requieren match estricto de equipos
DEPORTES_DE_EQUIPO = ["Fútbol", "Baloncesto", "Béisbol", "Hockey"]

# ─── LIGAS EN SEGUIMIENTO ────────────────────────────────────────────────────
LIGAS_SEGUIMIENTO = {
    "4480": ("UEFA Champions League", "Fútbol"),
    "4481": ("UEFA Europa League", "Fútbol"),
    "5071": ("UEFA Conference League", "Fútbol"),
    "4328": ("Premier League", "Fútbol"),
    "4335": ("La Liga", "Fútbol"),
    "4332": ("Serie A", "Fútbol"),
    "4334": ("Ligue 1", "Fútbol"),
    "4331": ("Bundesliga", "Fútbol"),
    "4501": ("Copa Libertadores", "Fútbol"),
    "4724": ("Copa Sudamericana", "Fútbol"),
    "4346": ("MLS", "Fútbol"),
    "4350": ("Liga MX", "Fútbol"),
    "4351": ("Brasileirao Serie A", "Fútbol"),
    "4406": ("Argentina Primera División", "Fútbol"),
    "4497": ("Liga BetPlay", "Fútbol"),
    "4951": ("Torneo BetPlay", "Fútbol"),
    "5183": ("Copa Colombia", "Fútbol"),
    "4686": ("Liga Pro Ecuador", "Fútbol"),
    "4687": ("Paraguay Primera División", "Fútbol"),
    "4688": ("Perú Liga 1", "Fútbol"),
    "4429": ("FIFA World Cup", "Fútbol"),
    "4499": ("Copa América", "Fútbol"),
    "4496": ("Copa África de Naciones", "Fútbol"),
    "4502": ("UEFA Euro", "Fútbol"),
    "4498": ("Copa Confederaciones", "Fútbol"),
    "4873": ("CONCACAF Gold Cup", "Fútbol"),
    "4503": ("FIFA Club World Cup", "Fútbol"),
    "4387": ("NBA", "Baloncesto"),
    "4516": ("WNBA", "Baloncesto"),
    "4607": ("NCAAB", "Baloncesto"),
    "4408": ("Liga Endesa ACB", "Baloncesto"),
    "4370": ("Formula 1", "Motor"),
    "4393": ("NASCAR", "Motor"),
    "4373": ("IndyCar", "Motor"),
    "4407": ("MotoGP", "Motor"),
    "4409": ("WRC", "Motor"),
    "4447": ("Dakar Rally", "Motor"),
    "4464": ("ATP", "Tenis"),
    "4517": ("WTA", "Tenis"),
    "4424": ("MLB", "Béisbol"),
    "5064": ("Liga Mexicana de Béisbol", "Béisbol"),
    "4380": ("NHL", "Hockey"),
    "4425": ("PGA Tour", "Golf"),
    "4465": ("Ciclismo UCI", "Ciclismo"),
    "4443": ("UFC", "Combate"),
    "4445": ("Boxeo", "Combate"),
    "4975": ("Juegos Olímpicos", "Olimpiadas"),
}

STOP_WORDS = {
    "FC", "SC", "CF", "AC", "AS", "US", "CS", "RC", "CD", "SD", "UD",
    "RCD", "SSD", "SSC", "GD", "AF", "THE", "DE", "LA", "LAS", "LOS",
    "EL", "Y", "E", "AND", "OF", "DEL", "SAN", "SANTA", "DOS", "DU"
}

# ─── UTILIDADES DE TEXTO ─────────────────────────────────────────────────────

def desencriptar_texto(texto):
    """Convierte Leetspeak a español, quita acentos y caracteres raros."""
    texto = texto.upper()
    for leet, real in LEET_DICT.items():
        texto = texto.replace(leet, real)
        
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    # Dejamos solo letras y números, el resto se vuelve espacios
    texto = re.sub(r"[^A-Z0-9\s]", " ", texto)
    return " ".join(texto.split())

def extraer_palabras_clave_equipo(nombre_equipo):
    """Extrae palabras de búsqueda sólidas para equipos deportivos."""
    palabras = desencriptar_texto(nombre_equipo).split()
    filtradas = [p for p in palabras if p not in STOP_WORDS and len(p) > 2]
    # Retornamos hasta las 2 palabras más significativas
    return filtradas[:2]

# ─── CAPA DE RED (XTREAM + API) ──────────────────────────────────────────────

def obtener_categorias_xtream():
    url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_categories"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            return {str(c.get("category_id", "")): c.get("category_name", "") for c in r.json()}
    except Exception as e:
        print(f"❌ Error al obtener categorías de Xtream: {e}")
    return {}

def obtener_streams_xtream():
    url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
    try:
        r = requests.get(url, timeout=20)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f"❌ Error al obtener streams de Xtream: {e}")
        return []

def obtener_eventos_del_dia():
    url = f"https://{RAPIDAPI_HOST}/v1/events/schedule/popular"
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST,
        "Content-Type": "application/json"
    }
    
    eventos = []
    ids_vistos = set()
    
    # Usamos la fecha central del script
    querystring = {"locale": "ES", "date": FECHA_HOY}
    
    try:
        r = requests.get(url, headers=headers, params=querystring, timeout=15)
        if r.status_code == 200:
            data = r.json()
            # Sofasport agrupa los eventos en 'data' o directamente en la raíz dependiendo del endpoint,
            # el schedule popular usa 'data' para el arreglo principal de eventos
            lista_eventos = data.get("data", [])
            
            for ev in lista_eventos:
                id_ev = str(ev.get("id", ""))
                if not id_ev or id_ev in ids_vistos: continue
                ids_vistos.add(id_ev)

                # Extracción de campos basada en la estructura general de SofaScore
                home_team = ev.get("homeTeam", {}).get("name", "")
                away_team = ev.get("awayTeam", {}).get("name", "")
                torneo = ev.get("tournament", {}).get("name", "Evento Deportivo")
                categoria = ev.get("tournament", {}).get("category", {}).get("sport", {}).get("name", "Deporte")

                unix_time = ev.get("startTimestamp")
                if not unix_time: continue
                iso_time = datetime.fromtimestamp(unix_time, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                titulo = f"{home_team} vs {away_team}" if home_team and away_team else ev.get("customId", torneo)

                eventos.append({
                    "id": id_ev,
                    "titulo": titulo,
                    "torneo": torneo,
                    "categoria": categoria,
                    "hora_utc": iso_time,
                    "logo_local": "", 
                    "logo_visitante": "",
                    "banner": "",
                    "_equipo_local": home_team,
                    "_equipo_visitante": away_team,
                })
    except Exception as e:
        print(f"❌ Error consultando Sofasport: {e}")

    return eventos

# ─── EL MOTOR DE BÚSQUEDA GLOBAL ─────────────────────────────────────────────

def preprocesar_lista_iptv(streams_raw, categorias_map):
    """
    Crea un super-índice con la 'Fusión de Contexto' (Carpeta + Canal)
    totalmente desencriptado, listo para búsquedas masivas.
    """
    streams_listos = []
    for s in streams_raw:
        cat_id = str(s.get("category_id", ""))
        cat_name = categorias_map.get(cat_id, "")
        stream_name_raw = s.get("name", "")
        stream_id = s.get("stream_id")
        
        # Fusión de contexto y desencriptado global
        fusion_texto = f"{cat_name} {stream_name_raw}"
        texto_rastreable = desencriptar_texto(fusion_texto)
        
        # Limpiar el nombre visible para la UI (quitamos los emojis raros pero conservamos la ofuscación original si la hay)
        titulo_ui = stream_name_raw.replace("▫", " ").strip()
        
        streams_listos.append({
            "id": stream_id,
            "nombre_ui": titulo_ui,
            "texto_rastreable": texto_rastreable
        })
    return streams_listos

def buscar_fuentes_universales(evento, streams_procesados):
    fuentes = []
    categoria = evento.get("categoria", "")
    
    # 1. MODO DEPORTES DE EQUIPO (Búsqueda por contrincantes)
    if categoria in DEPORTES_DE_EQUIPO:
        kw_local = extraer_palabras_clave_equipo(evento.get("_equipo_local", ""))
        kw_visit = extraer_palabras_clave_equipo(evento.get("_equipo_visitante", ""))
        
        if not kw_local or not kw_visit: return []

        for s in streams_procesados:
            texto = s["texto_rastreable"]
            # Deben estar presentes TODAS las palabras clave de ambos equipos en la fusión (Carpeta + Canal)
            if all(kw in texto for kw in kw_local) and all(kw in texto for kw in kw_visit):
                fuentes.append({
                    "nombre": s["nombre_ui"],
                    "url": f"{XTREAM_URL}/live/{XTREAM_USER}/{XTREAM_PASS}/{s['id']}.ts"
                })

    # 2. MODO DEPORTES INDIVIDUALES (Red de arrastre por categoría)
    else:
        palabras_trampa = KEYWORDS_DEPORTES_INDIVIDUALES.get(categoria, [])
        if not palabras_trampa: return []
        
        for s in streams_procesados:
            texto = s["texto_rastreable"]
            # Si ALGUNA palabra trampa coincide, lo asignamos.
            if any(kw in texto for kw in palabras_trampa):
                fuentes.append({
                    "nombre": s["nombre_ui"],
                    "url": f"{XTREAM_URL}/live/{XTREAM_USER}/{XTREAM_PASS}/{s['id']}.ts"
                })

    return fuentes

# ─── PROCESO PRINCIPAL ───────────────────────────────────────────────────────

def main():
    print(f"🚀 Iniciando Curador Universal de Eventos — {FECHA_HOY}")

    print("📡 Descargando estructura de Xtream...")
    categorias_map = obtener_categorias_xtream()
    streams_raw = obtener_streams_xtream()
    
    if not streams_raw:
        print("❌ No hay streams en Xtream. Abortando.")
        return

    # Paso 1: Crear el Índice Global Desencriptado
    print("🧠 Procesando y desencriptando lista iptv completa...")
    streams_procesados = preprocesar_lista_iptv(streams_raw, categorias_map)
    
    # Paso 2: Descargar Cartelera Oficial
    print("📅 Consultando TheSportsDB...")
    eventos_api = obtener_eventos_del_dia()
    print(f"🗓️  {len(eventos_api)} eventos encontrados en la API.")

    # Paso 3: Cruce Masivo de Datos
    eventos_finales = []
    for evento in eventos_api:
        fuentes = buscar_fuentes_universales(evento, streams_procesados)
        if not fuentes:
            continue

        eventos_finales.append({
            "id":            evento["id"],
            "titulo":        evento["titulo"],
            "torneo":        evento["torneo"],
            "categoria":     evento["categoria"],
            "hora_utc":      evento["hora_utc"],
            "logo_local":    evento["logo_local"],
            "logo_visitante": evento["logo_visitante"],
            "banner":        evento["banner"],
            "fuentes":       fuentes, # Ahora acumulará todas las fuentes posibles
        })
        print(f"✅ {evento['titulo']} ({evento['torneo']}) — {len(fuentes)} fuente(s) extraídas")

    eventos_finales.sort(key=lambda e: e["hora_utc"])

    with open("eventos_hoy.json", "w", encoding="utf-8") as f:
        json.dump(eventos_finales, f, ensure_ascii=False, indent=2)

    print(f"🏁 Finalizado. {len(eventos_finales)} eventos guardados con éxito.")

if __name__ == "__main__":
    main()
