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

KEYWORDS_DEPORTES_INDIVIDUALES = {
    "Tenis": ["ATP", "WTA", "TENIS", "TENNIS", "ROLAND GARROS", "WIMBLEDON", "GRAND SLAM"],
    "Motor": ["F1", "FORMULA 1", "MOTO GP", "MOTOGP", "NASCAR", "INDYCAR", "MOTOR", "RALLY", "DAKAR"],
    "Combate": ["UFC", "MMA", "BOX", "BOXEO", "BOXING", "WWE", "VELADA"],
    "Ciclismo": ["CICLISMO", "TOUR DE FRANCE", "GIRO DE ITALIA", "VUELTA A ESPANA", "UCI"],
    "Golf": ["PGA", "GOLF", "MASTERS", "RYDER CUP", "LIV GOLF"],
    "Olimpiadas": ["JUEGOS OLIMPICOS", "OLYMPICS", "PARIS 2024"]
}

DEPORTES_DE_EQUIPO = ["Fútbol", "Baloncesto", "Béisbol", "Hockey"]

STOP_WORDS = {
    "FC", "SC", "CF", "AC", "AS", "US", "CS", "RC", "CD", "SD", "UD",
    "RCD", "SSD", "SSC", "GD", "AF", "THE", "DE", "LA", "LAS", "LOS",
    "EL", "Y", "E", "AND", "OF", "DEL", "SAN", "SANTA", "DOS", "DU"
}

# ─── UTILIDADES DE TEXTO ─────────────────────────────────────────────────────

def desencriptar_texto(texto):
    if not texto: return ""
    texto = texto.upper()
    for leet, real in LEET_DICT.items():
        texto = texto.replace(leet, real)
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^A-Z0-9\s]", " ", texto)
    return " ".join(texto.split())

def extraer_palabras_clave_equipo(nombre_equipo):
    palabras = desencriptar_texto(nombre_equipo).split()
    filtradas = [p for p in palabras if p not in STOP_WORDS and len(p) > 2]
    return filtradas[:2]

# ─── CAPA DE RED ─────────────────────────────────────────────────────────────

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
    if not RAPIDAPI_KEY:
        print("❌ Error: No se encontró RAPIDAPI_KEY en las variables de entorno.")
        return []

    url = f"https://{RAPIDAPI_HOST}/v1/events/schedule/popular"
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST,
        "Content-Type": "application/json"
    }
    
    # Mantenemos el parámetro locale
    querystring = {"locale": "ES"}
    
    try:
        r = requests.get(url, headers=headers, params=querystring, timeout=15)
        
        if r.status_code != 200:
            print(f"❌ Error de API ({r.status_code}): {r.text}")
            return []

        data = r.json()

        eventos = []
        ids_vistos = set()
        
        if isinstance(data, list):
            lista_base = data
        elif isinstance(data, dict):
            lista_base = data.get("data", data.get("events", data.get("tournaments", [])))
        else:
            lista_base = []
                
        eventos_planos = []
        for item in lista_base:
            if isinstance(item, dict) and "events" in item and isinstance(item["events"], list):
                eventos_planos.extend(item["events"])
            else:
                eventos_planos.append(item)
            
        for ev_raw in eventos_planos:
            if not isinstance(ev_raw, dict): continue
            
            # 🛠️ EL PARCHE: Si el evento viene envuelto en la llave "event", lo extraemos.
            ev = ev_raw.get("event", ev_raw)
            
            id_ev = str(ev.get("id", ""))
            if not id_ev or id_ev in ids_vistos: continue
            ids_vistos.add(id_ev)

            home_team = ev.get("homeTeam", {}).get("name", "")
            away_team = ev.get("awayTeam", {}).get("name", "")
            torneo = ev.get("tournament", {}).get("name", "Evento Deportivo")
            categoria = ev.get("tournament", {}).get("category", {}).get("sport", {}).get("name", "Deporte")

            # Ahora sí encontrará el timestamp
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
        return eventos
    except Exception as e:
        print(f"❌ Error crítico consultando SofaScore: {e}")
        return []

# ─── MOTOR DE BÚSQUEDA ───────────────────────────────────────────────────────

def preprocesar_lista_iptv(streams_raw, categorias_map):
    streams_listos = []
    for s in streams_raw:
        cat_id = str(s.get("category_id", ""))
        cat_name = categorias_map.get(cat_id, "")
        stream_name_raw = s.get("name", "")
        stream_id = s.get("stream_id")
        
        fusion_texto = f"{cat_name} {stream_name_raw}"
        texto_rastreable = desencriptar_texto(fusion_texto)
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
    
    if categoria in DEPORTES_DE_EQUIPO:
        kw_local = extraer_palabras_clave_equipo(evento.get("_equipo_local", ""))
        kw_visit = extraer_palabras_clave_equipo(evento.get("_equipo_visitante", ""))
        
        if not kw_local or not kw_visit: return []

        for s in streams_procesados:
            texto = s["texto_rastreable"]
            if all(kw in texto for kw in kw_local) and all(kw in texto for kw in kw_visit):
                fuentes.append({
                    "nombre": s["nombre_ui"],
                    "url": f"{XTREAM_URL}/live/{XTREAM_USER}/{XTREAM_PASS}/{s['id']}.ts"
                })
    else:
        palabras_trampa = KEYWORDS_DEPORTES_INDIVIDUALES.get(categoria, [])
        if not palabras_trampa: return []
        for s in streams_procesados:
            texto = s["texto_rastreable"]
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

    print("🧠 Procesando y desencriptando lista iptv completa...")
    streams_procesados = preprocesar_lista_iptv(streams_raw, categorias_map)
    
    print("📅 Consultando SofaScore (RapidAPI)...")
    eventos_api = obtener_eventos_del_dia()
    print(f"🗓️  {len(eventos_api)} eventos encontrados en la API.")

    eventos_finales = []
    for evento in eventos_api:
        fuentes = buscar_fuentes_universales(evento, streams_procesados)
        if not fuentes: continue

        eventos_finales.append({
            "id":            evento["id"],
            "titulo":        evento["titulo"],
            "torneo":        evento["torneo"],
            "categoria":     evento["categoria"],
            "hora_utc":      evento["hora_utc"],
            "logo_local":    evento["logo_local"],
            "logo_visitante": evento["logo_visitante"],
            "banner":        evento["banner"],
            "fuentes":       fuentes,
        })
        print(f"✅ {evento['titulo']} — {len(fuentes)} fuente(s)")

    eventos_finales.sort(key=lambda e: e["hora_utc"])

    with open("eventos_hoy.json", "w", encoding="utf-8") as f:
        json.dump(eventos_finales, f, ensure_ascii=False, indent=2)

    print(f"🏁 Finalizado. {len(eventos_finales)} eventos guardados.")

if __name__ == "__main__":
    main()
