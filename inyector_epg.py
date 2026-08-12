import os
import json
import re
import time
import logging
import requests
import io
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("inyector")

XTREAM_URL = os.environ.get("XTREAM_URL")
XTREAM_USER = os.environ.get("XTREAM_USER")
XTREAM_PASS = os.environ.get("XTREAM_PASS")

ARCHIVO_EVENTOS = "eventos_hoy.json"
URL_EPG_EUROPA = "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml"

# ─── VENTANA DE "HOY" UNIFICADA (Colombia UTC-5 + España CEST UTC+2) ────────
OFFSET_COLOMBIA = timedelta(hours=-5)
OFFSET_ESPANA = timedelta(hours=2)  # CEST. Cambiar a +1 en horario de invierno europeo.

def calcular_ventana_hoy_utc():
    ahora_utc = datetime.now(timezone.utc)
    fecha_colombia = (ahora_utc + OFFSET_COLOMBIA).date()
    fecha_espana = (ahora_utc + OFFSET_ESPANA).date()

    inicio_colombia_utc = datetime.combine(fecha_colombia, datetime.min.time(), tzinfo=timezone.utc) - OFFSET_COLOMBIA
    fin_colombia_utc = inicio_colombia_utc + timedelta(days=1)

    inicio_espana_utc = datetime.combine(fecha_espana, datetime.min.time(), tzinfo=timezone.utc) - OFFSET_ESPANA
    fin_espana_utc = inicio_espana_utc + timedelta(days=1)

    inicio_ventana = min(inicio_colombia_utc, inicio_espana_utc)
    fin_ventana = max(fin_colombia_utc, fin_espana_utc)
    return inicio_ventana, fin_ventana

def calcular_similitud(t1, t2):
    t1, t2 = str(t1).upper(), str(t2).upper()
    return SequenceMatcher(None, t1, t2).ratio() * 100

def es_basura(titulo):
    t = titulo.upper()
    basura = ["REPETICIÓN", "REPETICION", "RESUMEN", "NOTICIAS", "MAGAZINE", "SPORT CENTER", "SPORTSCENTER",
              "SAQUE LARGO", "PROGRAMACION", "CONEXIÓN", "LA RUTA DEL MUNDIAL", "DESPIERTA WIN",
              "PLANETA FÚTBOL", "FÚTBOL TOTAL", "DE FÚTBOL SE HABLA ASÍ", "PUEDE PASAR", "SUPERFÚTBOL",
              "DALE AL MEDIO", "SPORTIA", "DOMINGOL", "LÍBERO", "PRESIÓN ALTA"]
    return any(b in t for b in basura) or len(t) < 5

def parse_time_epg_a_dt(time_str):
    """Parsea el formato de fecha del XML del EPG (YYYYMMDDHHMMSS +ZZZZ) a datetime UTC."""
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

def parse_iso_a_dt(iso_str):
    """Parsea el formato ISO 8601 que usamos en eventos_hoy.json (ej: 2026-08-15T10:45:00Z) a datetime UTC."""
    if not iso_str:
        return None
    try:
        return datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None

def obtener_canal_logico(cid):
    c = str(cid)
    if c in ["Eurosport 1 HD", "Eurosport 2"]:
        return "Eurosport"
    return None

def limpiar_titulo_eurosport(titulo):
    t = re.sub(r'\[COLOR.*?\]', '', titulo)
    t = re.sub(r'\[/COLOR\]', '', t)
    t = t.replace("DIRECTO", "").strip(" -:")
    return ' '.join(t.split())

contadores = {
    "europa_programmes_totales": 0,
    "europa_channel_id_reconocido": 0,
    "europa_tiene_directo": 0,
    "europa_dentro_de_ventana_hoy": 0,
    "europa_no_es_basura": 0,
    "europa_aprobados_final": 0,
}

def extraer_eventos_eurosport(inicio_ventana, fin_ventana):
    """Extrae eventos de Eurosport 1/2 que cumplen AMBAS condiciones:
    1) El titulo lleva la palabra DIRECTO (marca editorial confirmada del
       feed EPG_dobleM para indicar transmision en vivo, sea el deporte que
       sea -- snooker, atletismo, ciclismo en ruta, lo que sea). Contenido
       enlatado/diferido como reposiciones de MTB de meses atras o programas
       tipo 'World Series of Poker' NUNCA llevan esta palabra en este feed,
       verificado directamente contra el XML real.
    2) El horario de inicio real (atributo start) cae dentro de la ventana
       de 'hoy' (union Colombia+España). Esto es necesario porque el feed
       pre-marca como DIRECTO sesiones de dias futuros ya confirmadas en el
       calendario oficial de un torneo (ej. Snooker Open de China), no solo
       la sesion de hoy.
    Ninguno de los dos filtros por si solo es suficiente; se necesitan los
    dos en conjunto."""
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
                    tit = elem.findtext("title", "")

                    if "DIRECTO" in tit.upper():
                        contadores["europa_tiene_directo"] += 1
                        dt_inicio = parse_time_epg_a_dt(elem.attrib.get("start"))

                        if dt_inicio is not None and inicio_ventana <= dt_inicio < fin_ventana:
                            contadores["europa_dentro_de_ventana_hoy"] += 1
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

def main():
    log.info("🚀 INICIANDO INYECTOR (Eurosport 1/2, filtro por ventana real de fecha)")

    if not os.path.exists(ARCHIVO_EVENTOS):
        log.error("No se encontró eventos_hoy.json. Ejecuta primero el curador.")
        return

    log.info("🔗 Mapeando canales Eurosport en Xtream a través del puente...")
    fuentes_eurosport = []
    try:
        url_x = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
        req_x = requests.get("https://mi-dashboard-tv.onrender.com/api/puente_xtream", params={"url": url_x}, timeout=60)
        streams = req_x.json() if req_x.status_code == 200 else []
        log.info(f"Streams Xtream recibidos: {len(streams)}")
        for s in streams:
            n = s.get("name", "").upper()
            stream_id = s.get('stream_id')
            u = f"{XTREAM_URL}/{XTREAM_USER}/{XTREAM_PASS}/{stream_id}.ts"
            if "EUROSPORT" in n:
                fuentes_eurosport.append({"nombre": s.get("name"), "url": u})
    except Exception as e:
        log.warning(f"Error mapeando Xtream: {e}")

    log.info(f"Canal 'Eurosport': {len(fuentes_eurosport)} streams Xtream mapeados.")

    inicio_ventana, fin_ventana = calcular_ventana_hoy_utc()
    eventos_nuevos = extraer_eventos_eurosport(inicio_ventana, fin_ventana)
    log.info(f"✅ Eventos Eurosport aprobados (dentro de ventana de hoy): {len(eventos_nuevos)}")

    log.info("── RESUMEN DE EMBUDO ──")
    for k, v in contadores.items():
        log.info(f"  {k}: {v}")

    with open(ARCHIVO_EVENTOS, "r", encoding="utf-8") as f:
        data = json.load(f)

    # ─── PURGA DE EVENTOS VENCIDOS/FUERA DE VENTANA ─────────────────────────
    # Al correr el pipeline varias veces al dia, aprovechamos para eliminar
    # del archivo cualquier evento (de cualquier fuente, no solo Eurosport)
    # cuyo horario de inicio ya quedo fuera de la ventana unificada de "hoy".
    total_antes = len(data)
    data_filtrada = []
    for ev in data:
        dt_ev = parse_iso_a_dt(ev.get("hora_utc", ""))
        if dt_ev is None:
            data_filtrada.append(ev)  # si no se puede parsear, se conserva para no perder datos por error de formato
            continue
        if inicio_ventana <= dt_ev < fin_ventana:
            data_filtrada.append(ev)
    data = data_filtrada
    purgados = total_antes - len(data)
    log.info(f"🗑️ Eventos purgados (fuera de ventana de hoy o ya finalizados): {purgados}")

    creados = 0
    para_inyeccion = []

    for en in eventos_nuevos:
        if not fuentes_eurosport:
            continue

        match = False
        for ex in data:
            if calcular_similitud(en["titulo"], ex["titulo"]) > 75:
                match = True
                urls_actuales = [f["url"] for f in ex["fuentes"]]
                for fn in fuentes_eurosport:
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
                "fuentes": fuentes_eurosport
            })
            creados += 1

    data.extend(para_inyeccion)
    data.sort(key=lambda x: x.get("hora_utc", ""))

    with open(ARCHIVO_EVENTOS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    log.info(f"💾 Inyección terminada. Eventos nuevos inyectados: {creados}")

if __name__ == "__main__":
    main()
