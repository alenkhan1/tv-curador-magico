import os
import re
import json
import time
import requests
import unicodedata
from datetime import datetime, timezone, timedelta

# ─── CONFIGURACIÓN DE ENTORNO ────────────────────────────────────────────────
XTREAM_URL       = os.environ.get("XTREAM_URL")
XTREAM_USER      = os.environ.get("XTREAM_USER")
XTREAM_PASS      = os.environ.get("XTREAM_PASS")
SPORTSDB_API_KEY = os.environ.get("SPORTSDB_API_KEY", "123")

# EL MOTOR DE TIEMPO: Ventana de 72 horas (Ayer, Hoy, Mañana)
HOY_UTC = datetime.now(timezone.utc)
FECHAS_API = [
    (HOY_UTC - timedelta(days=1)).strftime("%Y-%m-%d"),
    HOY_UTC.strftime("%Y-%m-%d"),
    (HOY_UTC + timedelta(days=1)).strftime("%Y-%m-%d")
]

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

# ─── PALABRAS GATILLO (Motor Autónomo para Deportes Individuales) ────────────
KEYWORDS_DEPORTES_INDIVIDUALES = {
    "Tenis": ["ATP", "WTA", "TENIS", "TENNIS", "ROLAND GARROS", "WIMBLEDON", "GRAND SLAM"],
    "Motor": ["F1", "FORMULA 1", "MOTO GP", "MOTOGP", "NASCAR", "INDYCAR", "MOTOR", "RALLY", "DAKAR"],
    "Combate": ["UFC", "MMA", "BOX", "BOXEO", "BOXING", "WWE", "VELADA"],
    "Ciclismo": ["CICLISMO", "TOUR DE FRANCE", "GIRO DE ITALIA", "VUELTA A ESPANA", "UCI"],
    "Golf": ["PGA", "GOLF", "MASTERS", "RYDER CUP", "LIV GOLF"],
    "Olimpiadas": ["JUEGOS OLIMPICOS", "OLYMPICS", "PARIS 2024"]
}

# Imágenes genéricas para inyectar en los eventos fantasma
METADATA_GENERICA = {
    "Tenis": {"logo": "https://r2.thesportsdb.com/images/media/league/badge/0o4x821698270146.png"},
    "Motor": {"logo": "https://r2.thesportsdb.com/images/media/league/badge/tvxxvr1421624647.png"},
    "Combate": {"logo": "https://r2.thesportsdb.com/images/media/league/badge/yvxqtv1421625204.png"},
    "Ciclismo": {"logo": "https://r2.thesportsdb.com/images/media/league/badge/9fnd5k1586852445.png"},
    "Golf": {"logo": "https://r2.thesportsdb.com/images/media/league/badge/y1csu11534005856.png"}
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
    "4406": ("Argentina Primera División", "Fútbol"),
    "4497": ("Liga BetPlay", "Fútbol"),
    "4429": ("FIFA World Cup", "Fútbol"),
    "4387": ("NBA", "Baloncesto"),
    "4424": ("MLB", "Béisbol"),
}

STOP_WORDS = {
    "FC", "SC", "CF", "AC", "AS", "US", "CS", "RC", "CD", "SD", "UD",
    "RCD", "SSD", "SSC", "GD", "AF", "THE", "DE", "LA", "LAS", "LOS",
    "EL", "Y", "E", "AND", "OF", "DEL", "SAN", "SANTA", "DOS", "DU"
}

# ─── UTILIDADES DE TEXTO ─────────────────────────────────────────────────────

def desencriptar_texto(texto):
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

# ─── CAPA DE RED (XTREAM + API) ──────────────────────────────────────────────

def obtener_categorias_xtream():
    url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_categories"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            return {str(c.get("category_id", "")): c.get("category_name", "") for c in r.json()}
    except:
        pass
    return {}

def obtener_streams_xtream():
    url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
    try:
        r = requests.get(url, timeout=20)
        return r.json() if r.status_code == 200 else []
    except:
        return []

def obtener_eventos_api():
    base_url = f"https://www.thesportsdb.com/api/v1/json/{SPORTSDB_API_KEY}/eventsday.php"
    eventos = []
    ids_vistos = set()

    # MOTOR 1: Consulta de Ventana Deslizante (72 horas)
    for fecha in FECHAS_API:
        for liga_id, (liga_nombre, categoria) in LIGAS_SEGUIMIENTO.items():
            try:
                r = requests.get(base_url, params={"d": fecha, "l": liga_id}, timeout=10)
                if r.status_code != 200: continue
                data = r.json()
                if not data or not data.get("events"): continue

                for ev in data["events"]:
                    id_ev = ev.get("idEvent", "")
                    if id_ev in ids_vistos: continue
                    ids_vistos.add(id_ev)

                    raw_time = ev.get("strTimestamp")
                    if not raw_time: continue
                    iso_time = raw_time.replace(" ", "T") + "Z"

                    eventos.append({
                        "id": id_ev,
                        "titulo": ev.get("strEvent", ""),
                        "torneo": liga_nombre,
                        "categoria": categoria,
                        "hora_utc": iso_time,
                        "logo_local": ev.get("strHomeTeamBadge") or "",
                        "logo_visitante": ev.get("strAwayTeamBadge") or "",
                        "banner": ev.get("strThumb") or "",
                        "_equipo_local": ev.get("strHomeTeam", ""),
                        "_equipo_visitante": ev.get("strAwayTeam", ""),
                    })
                time.sleep(0.3)
            except:
                continue
    return eventos

# ─── PREPROCESAMIENTO ────────────────────────────────────────────────────────

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

def buscar_fuentes_api(evento, streams_procesados):
    fuentes = []
    ids_usados = []
    kw_local = extraer_palabras_clave_equipo(evento.get("_equipo_local", ""))
    kw_visit = extraer_palabras_clave_equipo(evento.get("_equipo_visitante", ""))
    
    if not kw_local or not kw_visit: return [], []

    for s in streams_procesados:
        texto = s["texto_rastreable"]
        if all(kw in texto for kw in kw_local) and all(kw in texto for kw in kw_visit):
            fuentes.append({
                "nombre": s["nombre_ui"],
                "url": f"{XTREAM_URL}/live/{XTREAM_USER}/{XTREAM_PASS}/{s['id']}.ts"
            })
            ids_usados.append(s["id"])

    return fuentes, ids_usados

# ─── PROCESO PRINCIPAL ───────────────────────────────────────────────────────

def main():
    print(f"🚀 Iniciando Super-Curador de Eventos — Ventana Activa: {FECHAS_API} al {FECHAS_API[2]}")

    print("📡 Descargando estructura de Xtream...")
    categorias_map = obtener_categorias_xtream()
    streams_raw = obtener_streams_xtream()
    
    if not streams_raw:
        print("❌ No hay streams en Xtream. Abortando.")
        return

    print("🧠 Desencriptando lista IPTV global...")
    streams_procesados = preprocesar_lista_iptv(streams_raw, categorias_map)
    streams_asignados_globales = set()
    eventos_finales = []
    
    # =========================================================================
    # FASE 1: MOTOR PRINCIPAL (TheSportsDB para Deportes de Equipo)
    # =========================================================================
    print("📅 Consultando TheSportsDB (72 Horas)...")
    eventos_api = obtener_eventos_api()
    print(f"🗓️  {len(eventos_api)} eventos encontrados en la API oficial.")

    for evento in eventos_api:
        if evento["categoria"] not in DEPORTES_DE_EQUIPO: 
            continue # Dejamos los individuales al Motor Autónomo

        fuentes, ids_usados = buscar_fuentes_api(evento, streams_procesados)
        if not fuentes: continue

        streams_asignados_globales.update(ids_usados)
        evento["fuentes"] = fuentes
        eventos_finales.append(evento)
        print(f"⚽ [API] {evento['titulo']} — {len(fuentes)} fuente(s)")

    # =========================================================================
    # FASE 2: MOTOR DE RESCATE AUTÓNOMO (Creación al Vuelo para Individuales)
    # =========================================================================
    print("🕵️ Iniciando Rastreo Autónomo en canales huérfanos...")
    eventos_autonomos = {}
    
    for s in streams_procesados:
        # Si el canal ya fue asignado a un partido de fútbol/NBA, lo ignoramos
        if s["id"] in streams_asignados_globales: continue
        
        texto = s["texto_rastreable"]
        
        for categoria, palabras_gatillo in KEYWORDS_DEPORTES_INDIVIDUALES.items():
            if any(gatillo in texto for gatillo in palabras_gatillo):
                # ¡Gatillo detectado! Creamos el evento fantasma.
                titulo_limpio = s["nombre_ui"].replace(" | ", " - ")
                
                if titulo_limpio not in eventos_autonomos:
                    logo_cat = METADATA_GENERICA.get(categoria, {}).get("logo", "")
                    
                    eventos_autonomos[titulo_limpio] = {
                        "id": f"auto_{s['id']}",
                        "titulo": titulo_limpio,
                        "torneo": f"Evento de {categoria}",
                        "categoria": categoria,
                        # Falseamos la hora para forzar a la App a mostrarlo "EN VIVO"
                        "hora_utc": HOY_UTC.strftime("%Y-%m-%dT%H:00:00Z"), 
                        "logo_local": logo_cat,
                        "logo_visitante": logo_cat,
                        "banner": "",
                        "fuentes": []
                    }
                
                # Acumulamos las fuentes bajo el mismo título
                eventos_autonomos[titulo_limpio]["fuentes"].append({
                    "nombre": s["nombre_ui"],
                    "url": f"{XTREAM_URL}/live/{XTREAM_USER}/{XTREAM_PASS}/{s['id']}.ts"
                })
                break # Rompe el ciclo de categorías para pasar al siguiente stream

    for titulo, evento_auto in eventos_autonomos.items():
        eventos_finales.append(evento_auto)
        print(f"🎾 [AUTÓNOMO] {titulo} — {len(evento_auto['fuentes'])} fuente(s)")

    # =========================================================================
    # FINALIZACIÓN
    # =========================================================================
    eventos_finales.sort(key=lambda e: e["hora_utc"])

    with open("eventos_hoy.json", "w", encoding="utf-8") as f:
        json.dump(eventos_finales, f, ensure_ascii=False, indent=2)

    print(f"🏁 Finalizado. {len(eventos_finales)} eventos guardados con éxito.")

if __name__ == "__main__":
    main()
