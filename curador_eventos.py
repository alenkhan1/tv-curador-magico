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
SPORTSDB_API_KEY = os.environ.get("SPORTSDB_API_KEY", "123")
FECHA_HOY        = datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ─── LIGAS EN SEGUIMIENTO ────────────────────────────────────────────────────
LIGAS_SEGUIMIENTO = {
    # Fútbol
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
    # Baloncesto
    "4387": ("NBA", "Baloncesto"),
    "4516": ("WNBA", "Baloncesto"),
    "4607": ("NCAAB", "Baloncesto"),
    "4408": ("Liga Endesa ACB", "Baloncesto"),
    # Motor
    "4370": ("Formula 1", "Motor"),
    "4393": ("NASCAR", "Motor"),
    "4373": ("IndyCar", "Motor"),
    "4407": ("MotoGP", "Motor"),
    "4409": ("WRC", "Motor"),
    "4447": ("Dakar Rally", "Motor"),
    # Tenis / Raqueta
    "4464": ("ATP", "Tenis"),
    "4517": ("WTA", "Tenis"),
    # Béisbol
    "4424": ("MLB", "Béisbol"),
    "5064": ("Liga Mexicana de Béisbol", "Béisbol"),
    # Otros
    "4380": ("NHL", "Hockey"),
    "4425": ("PGA Tour", "Golf"),
    "4465": ("Ciclismo UCI", "Ciclismo"),
    "4443": ("UFC", "Combate"),
    "4445": ("Boxeo", "Combate"),
    "4975": ("Juegos Olímpicos", "Olimpiadas"),
}

# Palabras que no aportan al matching de nombres de equipos
STOP_WORDS = {
    "FC", "SC", "CF", "AC", "AS", "US", "CS", "RC", "CD", "SD", "UD",
    "RCD", "SSD", "SSC", "GD", "AF", "THE", "DE", "LA", "LAS", "LOS",
    "EL", "Y", "E", "AND", "OF", "DEL", "SAN", "SANTA", "DOS", "DU"
}


# ─── UTILIDADES ──────────────────────────────────────────────────────────────

def normalizar(texto):
    """Mayúsculas, sin acentos, sin caracteres especiales."""
    texto = texto.upper()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^A-Z0-9\s]", " ", texto)
    return " ".join(texto.split())


def palabras_clave(nombre_equipo):
    """
    Extrae hasta 2 palabras significativas del nombre de un equipo.
    Ignora STOP_WORDS y palabras de 1-2 caracteres.
    Ej: "Real Madrid CF" -> ["MADRID"]
        "Manchester City" -> ["MANCHESTER", "CITY"]
        "Atlético Nacional" -> ["ATLETICO", "NACIONAL"]
    """
    palabras = normalizar(nombre_equipo).split()
    filtradas = [p for p in palabras if p not in STOP_WORDS and len(p) > 2]
    return filtradas[:2]


# ─── CAPA DE RED ─────────────────────────────────────────────────────────────

def obtener_streams_xtream():
    """Una sola llamada: descarga todos los streams en vivo de Xtream."""
    url = (f"{XTREAM_URL}/player_api.php"
           f"?username={XTREAM_USER}&password={XTREAM_PASS}"
           f"&action=get_live_streams")
    try:
        r = requests.get(url, timeout=20)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f"❌ Error al obtener streams de Xtream: {e}")
        return []


def obtener_eventos_del_dia():
    """
    FUENTE DE VERDAD: consulta TheSportsDB por fecha para cada liga.
    Retorna lista de eventos con datos oficiales + campos internos de trabajo.
    """
    base_url = (f"https://www.thesportsdb.com/api/v1/json"
                f"/{SPORTSDB_API_KEY}/eventsday.php")
    eventos = []
    ids_vistos = set()

    for liga_id, (liga_nombre, categoria) in LIGAS_SEGUIMIENTO.items():
        try:
            r = requests.get(base_url, params={"d": FECHA_HOY, "l": liga_id}, timeout=10)
            if r.status_code != 200:
                continue

            data = r.json()
            if not data or not data.get("events"):
                continue

            for ev in data["events"]:
                id_ev = ev.get("idEvent", "")

                if id_ev in ids_vistos:
                    continue
                ids_vistos.add(id_ev)

                raw_time = ev.get("strTimestamp")
                if not raw_time:
                    continue
                
                # REPARACIÓN MILITAR DE LA HORA (ISO 8601)
                iso_time = raw_time.replace(" ", "T") + "Z"

                eventos.append({
                    "id":               id_ev,
                    "titulo":           ev.get("strEvent", ""),
                    "torneo":           liga_nombre, # Forzamos el nombre comercial limpio
                    "categoria":        categoria,   # Inyectamos la macro-categoría
                    "hora_utc":         iso_time,    # Hora legible para Android
                    "logo_local":       ev.get("strHomeTeamBadge") or "",
                    "logo_visitante":   ev.get("strAwayTeamBadge") or "",
                    "banner":           ev.get("strThumb") or "",
                    "_equipo_local":    ev.get("strHomeTeam", ""),
                    "_equipo_visitante": ev.get("strAwayTeam", ""),
                })

            time.sleep(0.3)

        except Exception as e:
            print(f"⚠️  Error en liga {liga_nombre} ({liga_id}): {e}")
            continue

    return eventos


# ─── MATCHING ────────────────────────────────────────────────────────────────

def buscar_fuentes_en_xtream(evento, streams):
    fuentes = []
    equipo_local     = evento.get("_equipo_local", "")
    equipo_visitante = evento.get("_equipo_visitante", "")
    torneo           = evento.get("torneo", "")
    categoria        = evento.get("categoria", "")

    kw_local     = palabras_clave(equipo_local) if equipo_local else []
    kw_visitante = palabras_clave(equipo_visitante) if equipo_visitante else []
    kw_torneo    = palabras_clave(torneo)

    for s in streams:
        nombre_crudo = s.get("name", "")
        nombre_norm = normalizar(nombre_crudo)
        es_match = False

        # ESTRATEGIA 1: Match Híbrido por Torneo (Para canales que dicen "Liga Betplay", "ATP", "F1")
        if kw_torneo and any(kw in nombre_norm for kw in kw_torneo):
            if categoria not in ["Fútbol", "Baloncesto", "Béisbol"]:
                # Deportes individuales/motor: Si dice el torneo, es nuestro.
                es_match = True
            else:
                # Deportes de equipo: Exige que diga el torneo Y al menos el nombre de un equipo.
                if (kw_local and any(kw in nombre_norm for kw in kw_local)) or \
                   (kw_visitante and any(kw in nombre_norm for kw in kw_visitante)):
                    es_match = True

        # ESTRATEGIA 2: Match Clásico Implacable (El estándar de equipos)
        if not es_match and kw_local and kw_visitante:
            if (all(kw in nombre_norm for kw in kw_local) and
                all(kw in nombre_norm for kw in kw_visitante)):
                es_match = True

        if es_match:
            stream_id = s.get("stream_id")
            # Usamos el nombre original de la lista, limpiando caracteres raros para la UI
            titulo_limpio = nombre_crudo.replace("▫", " ").strip()
            fuentes.append({
                "nombre": titulo_limpio,
                "url": f"{XTREAM_URL}/live/{XTREAM_USER}/{XTREAM_PASS}/{stream_id}.ts"
            })

    return fuentes


# ─── PROCESO PRINCIPAL ───────────────────────────────────────────────────────

def main():
    print(f"🚀 Curador de Eventos — {FECHA_HOY}")

    # 1. Una sola llamada a Xtream (toda la lista de streams)
    streams = obtener_streams_xtream()
    if not streams:
        print("❌ No se pudo obtener la lista de Xtream. Abortando.")
        return
    print(f"📡 {len(streams)} streams obtenidos de Xtream.")

    # 2. Obtener eventos del día desde TheSportsDB (fuente de verdad)
    print(f"📅 Consultando {len(LIGAS_SEGUIMIENTO)} ligas en TheSportsDB...")
    eventos_api = obtener_eventos_del_dia()
    print(f"🗓️  {len(eventos_api)} eventos encontrados en la API.")

    # 3. Para cada evento, buscar fuentes disponibles en Xtream
    eventos_finales = []
    for evento in eventos_api:
        fuentes = buscar_fuentes_en_xtream(evento, streams)
        if not fuentes:
            continue  # Evento sin cobertura en esta lista IPTV: se omite

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
        print(f"✅ {evento['titulo']} ({evento['torneo']}) — {len(fuentes)} fuente(s)")

    # 4. Ordenar por hora (más próximos primero)
    eventos_finales.sort(key=lambda e: e["hora_utc"])

    # 5. Guardar JSON
    with open("eventos_hoy.json", "w", encoding="utf-8") as f:
        json.dump(eventos_finales, f, ensure_ascii=False, indent=2)

    print(f"🏁 Finalizado. {len(eventos_finales)} eventos exportados a eventos_hoy.json")


if __name__ == "__main__":
    main()
