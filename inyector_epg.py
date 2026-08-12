import os
import json
import re
import time
import logging
import requests
import gzip
import io
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher

# ─── CONFIGURACIÓN DE LOGS ───────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("inyector")

XTREAM_URL = os.environ.get("XTREAM_URL")
XTREAM_USER = os.environ.get("XTREAM_USER")
XTREAM_PASS = os.environ.get("XTREAM_PASS")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
GOOGLE_CX = os.environ.get("GOOGLE_CX")

ARCHIVO_EVENTOS = "eventos_hoy.json"

# ─── FUENTES EPG LIGERAS ─────────────────────────────────────────────────────
URL_EPG_EUROPA = "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml"
URLS_AMERICA = {
    "Colombia": "https://epgshare01.online/epgshare01/epg_ripper_CO1.xml.gz",
    "Argentina": "https://epgshare01.online/epgshare01/epg_ripper_AR1.xml.gz"
}

# ─── CONTADORES DE EMBUDO (diagnóstico) ──────────────────────────────────────
contadores = {
    "europa_programmes_totales": 0,
    "europa_channel_id_reconocido": 0,
    "europa_dentro_de_ventana_hoy": 0,
    "europa_no_es_basura": 0,
    "europa_aprobados_final": 0,
}

# ─── FUNCIONES DE APOYO ──────────────────────────────────────────────────────
def calcular_similitud(t1, t2):
    t1, t2 = str(t1).upper(), str(t2).upper()
    return SequenceMatcher(None, t1, t2).ratio() * 100

def es_basura(titulo):
    """Excluye contenido claramente diferido/enlatado por patrón de texto conocido.
    NO se usa la palabra DIRECTO como criterio (confirmado que no es confiable
    en ninguna fuente disponible: ni EPG_dobleM, ni la web pública de Movistar+,
    ni iptv-org/epg tienen una señal estructurada de en-vivo-vs-repeticion).
    En su lugar se excluye lo que sabemos con certeza que es diferido/magazine,
    y se acepta todo lo demás como evento en vivo real, para no perder ninguno."""
    t = titulo.upper()
    basura = [
        "REPETICIÓN", "REPETICION", "RESUMEN", "NOTICIAS", "MAGAZINE",
        "SPORT CENTER", "SPORTSCENTER", "SAQUE LARGO", "PROGRAMACION",
        "CONEXIÓN", "LA RUTA DEL MUNDIAL", "DESPIERTA WIN", "PLANETA FÚTBOL",
        "FÚTBOL TOTAL", "DE FÚTBOL SE HABLA ASÍ", "PUEDE PASAR", "SUPERFÚTBOL",
        "DALE AL MEDIO", "SPORTIA", "DOMINGOL", "LÍBERO", "PRESIÓN ALTA",
    ]
    if any(b in t for b in basura) or len(t) < 5:
        return True

    # Patrón genérico de contenido episódico/enlatado (series, docu-realities,
    # magazines de recopilación), detectado por " E" + número o "EPISODIO" +
    # número en el título. Un evento deportivo real en este feed se marca por
    # día ("Día 5"), etapa ("Etapa 19") o sesión ("Sesión de tarde"), nunca por
    # número de episodio -- por eso este patrón es seguro y generalizable, en
    # vez de excluir un programa por su nombre propio (ej. "poker").
    if re.search(r'\bE\d+\b', t) or re.search(r'\bEPISODIO\s*\d+\b', t):
        return True

    return False

def parse_time_epg_a_dt(time_str):
    """Convierte start/stop del XMLTV a datetime real en UTC (para comparar ventanas)."""
    try:
        dt = datetime.strptime(time_str[:14], "%Y%m%d%H%M%S")
        offset = time_str[15:]
        if len(offset) >= 5:
            sign = 1 if offset[0] == '+' else -1
            h, m = int(offset[1:3]), int(offset[3:5])
            tz = timezone(timedelta(hours=sign*h, minutes=sign*m))
        else:
            tz = timezone.utc
        return dt.replace(tzinfo=tz).astimezone(timezone.utc)
    except Exception:
        return None

def parse_time(time_str):
    dt = parse_time_epg_a_dt(time_str)
    if dt is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def calcular_ventana_hoy():
    """Ventana 'hoy' unificada: desde el inicio del día en Colombia (UTC-5)
    hasta el final del día en España (UTC+1/+2), en UTC. Así cubrimos ambos
    husos horarios sin perder eventos de ninguno de los dos."""
    ahora_utc = datetime.now(timezone.utc)
    hoy_colombia = (ahora_utc - timedelta(hours=5)).date()
    inicio_ventana = datetime.combine(hoy_colombia, datetime.min.time(), tzinfo=timezone(timedelta(hours=-5))).astimezone(timezone.utc)
    fin_ventana = inicio_ventana + timedelta(days=2, hours=3)
    return inicio_ventana, fin_ventana

def obtener_canal_logico(cid):
    """Detecta el canal basándose estrictamente en los identificadores reales confirmados."""
    c = str(cid)
    c_upper = c.upper()

    if c in ["Eurosport 1 HD", "Eurosport 2"]:
        return "Eurosport"

    if "WIN.SPORTS" in c_upper or "WIN+" in c_upper:
        return "Win Sports"
    if "TYC.SPORTS" in c_upper:
        return "TyC Sports"
    if "DSPORTS" in c_upper or "DIRECTV.SPORTS" in c_upper:
        return "DSports"

    return None

def limpiar_titulo_eurosport(titulo):
    """Limpia las etiquetas [COLOR] y la palabra DIRECTO (residuo editorial,
    no se usa como filtro, solo se limpia del texto visible al usuario)."""
    t = re.sub(r'\[COLOR.*?\]', '', titulo)
    t = re.sub(r'\[/COLOR\]', '', t)
    t = re.sub(r'(?i)\bDIRECTO\b', '', t).strip(" -:")
    return ' '.join(t.split())

# ─── VALIDADOR GOOGLE (América) ──────────────────────────────────────────────
def validar_google(titulo):
    if not GOOGLE_API_KEY:
        return False
    titulo_limpio = re.sub(r'\b(HD|SD|FHD|4K|VIVO|DIRECTO|REPETICION|RESUMEN)\b', '', titulo, flags=re.IGNORECASE).strip()

    url = "https://www.googleapis.com/customsearch/v1"
    params = {"key": GOOGLE_API_KEY, "cx": GOOGLE_CX, "q": f'"{titulo_limpio}" (hoy OR previa OR vivo)', "dateRestrict": "d"}
    try:
        time.sleep(1)
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return False

        items = r.json().get("items", [])
        if not items:
            return False

        text = " ".join([i.get("snippet", "") + " " + i.get("title", "") for i in items]).lower()
        verdes = ["hoy", "vivo", "directo", "transmisión", "transmision", "alineaciones", "dónde ver", "donde ver", "recibe a", "se enfrentan"]
        rojas = ["goles", "resumen", "resultado", "derrotó", "derroto", "empató", "empato", "ayer", "polémica", "venció", "vencio"]

        puntos_verdes = sum(1 for v in verdes if v in text)
        puntos_rojas = sum(1 for r in rojas if r in text)

        es_vivo = puntos_verdes > puntos_rojas
        if es_vivo:
            log.info(f"[+] GOOGLE APRUEBA: {titulo}")
        return es_vivo
    except Exception:
        return False

# ─── PROCESADOR EPG ──────────────────────────────────────────────────────────
def extraer_eventos_eurosport(inicio_ventana, fin_ventana):
    """Extrae eventos de Eurosport 1/2 cuyo horario real (start) cae dentro de
    la ventana de 'hoy' (union Colombia+España), excluyendo solo lo que sabemos
    con certeza que es contenido diferido/magazine por patrón de texto (es_basura).
    NO se filtra por la palabra DIRECTO: confirmamos, revisando el XML real y la
    web pública de Movistar+, que ninguna fuente disponible marca de forma
    confiable en-vivo-vs-repeticion. Esta es la estrategia que maximiza la
    captura de todo evento en vivo real (snooker, atletismo, ciclismo, MTB,
    motocross, lo que sea), aceptando como costo residual que una sesión
    reemitida el mismo día (ej. snooker repetido 3-4 veces) pueda aparecer
    más de una vez -- la UI debe ocultar lo que ya finalizó por horario."""
    eventos = []
    log.info(f"Ventana 'hoy' unificada (UTC): {inicio_ventana.isoformat()} -> {fin_ventana.isoformat()}")
    log.info("📡 Leyendo EPG Europa (EPG_dobleM) — Eurosport 1 y 2...")
    try:
        r = requests.get(URL_EPG_EUROPA, timeout=20)
        it = ET.iterparse(io.BytesIO(r.content), events=("end",))
        for _, elem in it:
            if elem.tag == "programme":
                contadores["europa_programmes_totales"] += 1
                cid = elem.attrib.get("channel")
                canal_asignado = obtener_canal_logico(cid)

                if canal_asignado == "Eurosport":
                    contadores["europa_channel_id_reconocido"] += 1
                    dt_inicio = parse_time_epg_a_dt(elem.attrib.get("start"))

                    if dt_inicio is not None and inicio_ventana <= dt_inicio < fin_ventana:
                        contadores["europa_dentro_de_ventana_hoy"] += 1
                        tit = elem.findtext("title", "")
                        tit_limpio = limpiar_titulo_eurosport(tit)
                        if not es_basura(tit_limpio):
                            contadores["europa_no_es_basura"] += 1
                            contadores["europa_aprobados_final"] += 1
                            eventos.append({
                                "titulo": tit_limpio,
                                "canal": "Eurosport",
                                "hora_utc": dt_inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "duracion_min": 120
                            })
                elem.clear()
    except Exception as e:
        log.error(f"Error leyendo EPG Europa: {e}")

    return eventos

def extraer_eventos_america(inicio_ventana, fin_ventana):
    eventos = []
    for pais, url in URLS_AMERICA.items():
        log.info(f"📡 Leyendo EPG {pais} (epgshare01)...")
        try:
            r = requests.get(url, timeout=20)
            xml_data = gzip.decompress(r.content)
            it = ET.iterparse(io.BytesIO(xml_data), events=("end",))
            for _, elem in it:
                if elem.tag == "programme":
                    cid = elem.attrib.get("channel")
                    canal_asignado = obtener_canal_logico(cid)

                    if canal_asignado in ["Win Sports", "TyC Sports", "DSports"]:
                        dt_inicio = parse_time_epg_a_dt(elem.attrib.get("start"))
                        if dt_inicio is not None and inicio_ventana <= dt_inicio < fin_ventana:
                            tit = elem.findtext("title", "")
                            if not es_basura(tit):
                                if validar_google(tit):
                                    eventos.append({
                                        "titulo": tit.strip(),
                                        "canal": canal_asignado,
                                        "hora_utc": dt_inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                        "duracion_min": 120
                                    })
                    elem.clear()
        except Exception as e:
            log.error(f"Error {pais}: {e}")
    return eventos

def extraer_eventos():
    inicio_ventana, fin_ventana = calcular_ventana_hoy()
    eventos = []
    eventos += extraer_eventos_eurosport(inicio_ventana, fin_ventana)
    eventos += extraer_eventos_america(inicio_ventana, fin_ventana)
    return eventos

# ─── INYECCIÓN ───────────────────────────────────────────────────────────────
def main():
    log.info("🚀 INICIANDO INYECTOR (ventana real de fecha + exclusión por patrones de basura, sin filtro DIRECTO)")

    if not os.path.exists(ARCHIVO_EVENTOS):
        log.error("No se encontró eventos_hoy.json. Ejecuta primero el curador.")
        return

    log.info("🔗 Mapeando links de Xtream a través del puente...")
    mapa_xtream = {k: [] for k in ["Win Sports", "DSports", "TyC Sports", "Eurosport"]}
    try:
        url_x = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
        req_x = requests.get("https://mi-dashboard-tv.onrender.com/api/puente_xtream", params={"url": url_x}, timeout=60)
        streams = req_x.json() if req_x.status_code == 200 else []
        log.info(f"Streams Xtream recibidos: {len(streams)}")
        for s in streams:
            n = s.get("name", "").upper()
            stream_id = s.get('stream_id')
            u = f"{XTREAM_URL}/{XTREAM_USER}/{XTREAM_PASS}/{stream_id}.ts"

            if "WIN SPORT" in n or "WIN+" in n:
                mapa_xtream["Win Sports"].append({"nombre": s.get("name"), "url": u})
            elif "DSPORTS" in n or "DIRECTV SPORTS" in n or "D SPORTS" in n:
                mapa_xtream["DSports"].append({"nombre": s.get("name"), "url": u})
            elif "TYC" in n:
                mapa_xtream["TyC Sports"].append({"nombre": s.get("name"), "url": u})
            elif "EUROSPORT" in n:
                mapa_xtream["Eurosport"].append({"nombre": s.get("name"), "url": u})
        for canal, lista in mapa_xtream.items():
            log.info(f"Canal '{canal}': {len(lista)} streams Xtream mapeados.")
    except Exception as e:
        log.error(f"Error mapeando Xtream: {e}")

    eventos_nuevos = extraer_eventos()
    log.info(f"✅ Eventos Eurosport aprobados (dentro de ventana de hoy, sin basura): {contadores['europa_aprobados_final']}")
    log.info("── RESUMEN DE EMBUDO ──")
    for k, v in contadores.items():
        log.info(f"  {k}: {v}")
    log.info(f"✅ Total eventos aprobados (Europa + América): {len(eventos_nuevos)}")

    with open(ARCHIVO_EVENTOS, "r", encoding="utf-8") as f:
        data = json.load(f)

    creados = 0
    para_inyeccion = []

    for en in eventos_nuevos:
        fuentes_iptv = mapa_xtream.get(en["canal"], [])
        if not fuentes_iptv:
            continue

        match = False
        for ex in data:
            if calcular_similitud(en["titulo"], ex["titulo"]) > 75:
                match = True
                urls_actuales = [f["url"] for f in ex["fuentes"]]
                for fn in fuentes_iptv:
                    if fn["url"] not in urls_actuales:
                        ex["fuentes"].append(fn)
                break

        if not match:
            cat = "Ciclismo" if "ETAPA" in en["titulo"].upper() else "Fútbol" if " VS " in en["titulo"].upper() else "Deportes"
            para_inyeccion.append({
                "id": f"epg_{int(time.time())}_{en['titulo'][:5]}",
                "titulo": en["titulo"], "torneo": en["canal"], "categoria": cat,
                "hora_utc": en["hora_utc"], "duracion_min": 120,
                "logo_local": "", "logo_visitante": "", "banner": "", "tier": 2,
                "fuentes": fuentes_iptv
            })
            creados += 1

    data.extend(para_inyeccion)
    data.sort(key=lambda x: x.get("hora_utc", ""))

    with open(ARCHIVO_EVENTOS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    log.info(f"💾 Inyección terminada. Eventos nuevos inyectados: {creados}")

if __name__ == "__main__":
    main()
