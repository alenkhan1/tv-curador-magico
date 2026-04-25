import os
import re
import json
import requests
import time
from datetime import datetime

# ─── CONFIGURACIÓN DE ENTORNO ────────────────────────────────────────────────
XTREAM_URL = os.environ.get("XTREAM_URL")
XTREAM_USER = os.environ.get("XTREAM_USER")
XTREAM_PASS = os.environ.get("XTREAM_PASS")
# La clave 123 es funcional para usuarios gratuitos en TheSportsDB
SPORTSDB_API_KEY = os.environ.get("SPORTSDB_API_KEY", "123")

# ─── LISTA BLANCA DE INTERÉS (HARDCODEADA) ──────────────────────────────────
# Solo procesaremos canales que contengan estas palabras clave (Equipos o Ligas Top)
WHITELIST = [
    "REAL MADRID", "BARCELONA", "ATLETICO", "NAPOLI", "INTER", "MILAN", "JUVENTUS",
    "LIVERPOOL", "MANCHESTER", "ARSENAL", "CHELSEA", "BAYERN", "PSG", "BENFICA",
    "NACIONAL", "MILLONARIOS", "AMERICA", "JUNIOR", "SANTA FE", "MEDELLIN",
    "LIBERTADORES", "SUDAMERICANA", "CHAMPIONS", "EUROPA LEAGUE", "LALIGA", "PREMIER",
    "UFC", "F1", "FORMULA 1", "MOTOGP", "NBA", "MLB", "NFL", "TOUR", "VUELTA", "GIRO"
]

# ─── FUNCIONES DE RED (XTREAM) ───────────────────────────────────────────────
def obtener_streams_xtream():
    url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
    try:
        r = requests.get(url, timeout=20)
        return r.json() if r.status_code == 200 else []
    except:
        return []

# ─── LÓGICA DE EXTRACCIÓN Y LIMPIEZA ─────────────────────────────────────────
def extraer_evento(nombre_canal):
    """
    Usa Regex para extraer competidores ignorando el ruido del proveedor.
    Ej: "EVENTO E$PN 13:45 Napoli vs Cremonese" -> "Napoli vs Cremonese"
    """
    nombre_up = nombre_canal.upper()
    if not any(word in nombre_up for word in WHITELIST):
        return None

    # Buscamos el patrón [Equipo A] vs/v/- [Equipo B]
    # Ignoramos cualquier hora HH:MM previa y basura como "EVENTO", "ESPN", etc.
    patron = r'(?:\d{2}:\d{2})?\s*(.*?)\s+(?:VS|V|-)\s+(.*)'
    match = re.search(patron, nombre_canal, re.IGNORECASE)
    
    if match:
        # Limpieza de prefijos comunes de proveedores
        local = re.sub(r'^(?i)(EVENTO|LIVE|VIVO|E\$PN|D\$PORTS|FOX|DAZN|DIRECTV)[\s\d\W]*', '', match.group(1)).strip()
        visitante = match.group(2).strip()
        return f"{local} vs {visitante}"
    
    # Si no hay "vs", devolvemos el nombre limpio (para F1, UFC, etc.)
    return re.sub(r'^(?i)(EVENTO|LIVE|VIVO)[\s\d\W]*', '', nombre_canal).strip()

# ─── CONSULTA A THESPORTSDB (LA VERDAD ABSOLUTA) ─────────────────────────────
def consultar_api_deportes(query):
    """
    Busca el evento en TheSportsDB para obtener hora UTC, logos y torneo.
    """
    # Formateamos para la URL (espacios por guiones bajos)
    query_url = query.replace(" ", "_")
    url = f"https://www.thesportsdb.com/api/v1/json/{SPORTSDB_API_KEY}/searchevents.php?e={query_url}"
    
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("event"):
                ev = data["event"]
                # Filtro Estricto: Si no hay timestamp, no nos sirve para la app
                if not ev.get("strTimestamp"): return None
                
                return {
                    "id": ev["idEvent"],
                    "titulo": ev["strEvent"],
                    "torneo": ev["strLeague"],
                    "hora_utc": ev["strTimestamp"], # Formato: 2026-04-26T18:00:00Z
                    "logo_local": ev.get("strHomeTeamBadge", ""),
                    "logo_visitante": ev.get("strAwayTeamBadge", ""),
                    "banner": ev.get("strThumb", "")
                }
    except:
        pass
    return None

# ─── PROCESO PRINCIPAL ───────────────────────────────────────────────────────
def main():
    print("🚀 Iniciando Curador de Eventos Deportivos...")
    streams = obtener_streams_xtream()
    if not streams:
        print("❌ No se pudo obtener la lista de Xtream.")
        return

    eventos_finales = {} # Usamos dict para agrupar fuentes por ID de evento

    for s in streams:
        nombre_raw = s.get("name", "")
        stream_id = s.get("stream_id")
        
        # 1. Intentar extraer nombre de evento limpio
        query_evento = extraer_evento(nombre_raw)
        if not query_evento: continue

        # 2. Si ya lo procesamos, solo añadimos la fuente (Agrupación)
        ya_existe = next((id_ev for id_ev, info in eventos_finales.items() if query_evento in info['titulo']), None)
        
        if ya_existe:
            eventos_finales[ya_existe]["fuentes"].append({
                "nombre": nombre_raw,
                "url": f"{XTREAM_URL}/live/{XTREAM_USER}/{XTREAM_PASS}/{stream_id}.ts"
            })
            continue

        # 3. Consultar API (Filtro Estricto)
        info_oficial = consultar_api_deportes(query_evento)
        
        if info_oficial:
            id_ev = info_oficial["id"]
            info_oficial["fuentes"] = [{
                "nombre": nombre_raw,
                "url": f"{XTREAM_URL}/live/{XTREAM_USER}/{XTREAM_PASS}/{stream_id}.ts"
            }]
            eventos_finales[id_ev] = info_oficial
            print(f"✅ Evento Validado: {info_oficial['titulo']} ({info_oficial['torneo']})")
            time.sleep(0.5) # Respeto a la API gratuita

    # 4. Guardar JSON
    resultado = list(eventos_finales.values())
    with open("eventos_hoy.json", "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    
    print(f"🏁 Proceso finalizado. {len(resultado)} eventos exportados a eventos_hoy.json")

if __name__ == "__main__":
    main()