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
# IDs verificados contra TheSportsDB. Clave: idLeague. Valor: nombre display.
LIGAS_SEGUIMIENTO = {
    # Fútbol – Competiciones de Clubes Europa
    "4480": "UEFA Champions League",
    "4481": "UEFA Europa League",
    "5071": "UEFA Conference League",
    "4328": "Premier League",
    "4335": "La Liga",
    "4332": "Serie A",
    "4334": "Ligue 1",
    "4331": "Bundesliga",
    # Fútbol – América / Internacional
    "4501": "Copa Libertadores",
    "4724": "Copa Sudamericana",
    "4346": "MLS",
    "4350": "Liga MX",
    "4351": "Brasileirao Serie A",
    "4406": "Argentina Primera División",
    "4497": "Liga BetPlay Colombia",
    "4951": "Torneo BetPlay (Segunda)",
    "5183": "Copa Colombia",
    "4686": "Liga Pro Ecuador",
    "4687": "Paraguay Primera División",
    "4688": "Perú Liga 1",
    # Fútbol – Selecciones
    "4429": "FIFA World Cup",
    "4499": "Copa América",
    "4496": "Copa África de Naciones",
    "4502": "UEFA Euro",
    "4498": "Copa Confederaciones",
    "4873": "CONCACAF Gold Cup",
    "4503": "FIFA Club World Cup",
    # Baloncesto
    "4387": "NBA",
    "4516": "WNBA",
    "4607": "NCAAB",
    "4408": "Liga Endesa ACB",
    # Béisbol
    "4424": "MLB",
    "5064": "Liga Mexicana de Béisbol",
    # Hockey sobre hielo
    "4380": "NHL",
    # Tenis
    "4464": "ATP World Tour",
    "4517": "WTA Tour",
    # Golf
    "4425": "PGA Tour",
    # Ciclismo
    "4465": "UCI World Tour",
    # Automovilismo
    "4370": "Formula 1",
    "4393": "NASCAR Cup Series",
    "4373": "IndyCar Series",
    "4407": "MotoGP",
    "4409": "WRC",
    "4447": "Dakar Rally",
    # Artes Marciales / Boxeo
    "4443": "UFC",
    "4445": "Boxeo",
    # Olimpiadas
    "4975": "Juegos Olímpicos",
    "5039": "Olimpiadas Fútbol",
    "5020": "Olimpiadas Baloncesto",
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

    for liga_id, liga_nombre in LIGAS_SEGUIMIENTO.items():
        try:
            r = requests.get(base_url, params={"d": FECHA_HOY, "l": liga_id}, timeout=10)
            if r.status_code != 200:
                continue

            data = r.json()
            if not data or not data.get("events"):
                continue

            for ev in data["events"]:
                id_ev = ev.get("idEvent", "")

                # Deduplicación: una liga puede aparecer en varias consultas
                if id_ev in ids_vistos:
                    continue
                ids_vistos.add(id_ev)

                # Filtro estricto: sin timestamp no es útil para la app
                if not ev.get("strTimestamp"):
                    continue

                eventos.append({
                    # Campos del modelo final (Evento.kt)
                    "id":               id_ev,
                    "titulo":           ev.get("strEvent", ""),
                    "torneo":           ev.get("strLeague") or liga_nombre,
                    "hora_utc":         ev.get("strTimestamp", ""),
                    "logo_local":       ev.get("strHomeTeamBadge") or "",
                    "logo_visitante":   ev.get("strAwayTeamBadge") or "",
                    "banner":           ev.get("strThumb") or "",
                    # Campos internos de trabajo (no van al JSON final)
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
    """
    Dado un evento oficial de TheSportsDB, busca en los streams de Xtream
    cuáles corresponden. Retorna lista de {nombre, url}.

    Estrategia:
    - Evento con dos equipos: el stream debe contener palabras clave de AMBOS.
    - Evento sin equipos (F1, ciclismo, etc.): match por palabras del título.
    """
    fuentes = []
    equipo_local     = evento.get("_equipo_local", "")
    equipo_visitante = evento.get("_equipo_visitante", "")

    if equipo_local and equipo_visitante:
        kw_local     = palabras_clave(equipo_local)
        kw_visitante = palabras_clave(equipo_visitante)

        # Si no se extrajeron palabras clave, no podemos hacer matching fiable
        if not kw_local or not kw_visitante:
            return []

        for s in streams:
            nombre_norm = normalizar(s.get("name", ""))
            if (all(kw in nombre_norm for kw in kw_local) and
                    all(kw in nombre_norm for kw in kw_visitante)):
                stream_id = s.get("stream_id")
                fuentes.append({
                    "nombre": s.get("name", ""),
                    "url": f"{XTREAM_URL}/live/{XTREAM_USER}/{XTREAM_PASS}/{stream_id}.ts"
                })
    else:
        # Evento sin equipos definidos: usar palabras del título
        kw_titulo = palabras_clave(evento.get("titulo", ""))
        if not kw_titulo:
            return []

        for s in streams:
            nombre_norm = normalizar(s.get("name", ""))
            if all(kw in nombre_norm for kw in kw_titulo):
                stream_id = s.get("stream_id")
                fuentes.append({
                    "nombre": s.get("name", ""),
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
