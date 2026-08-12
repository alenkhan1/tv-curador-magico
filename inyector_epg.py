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

# ─── FUENTE EPG: SOLO EUROSPORT (España) ─────────────────────────────────────
URL_EPG_EUROPA = "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml"

# ─── CONTADORES DE EMBUDO (diagnostico) ──────────────────────────────────────
contadores = {
    "europa_programmes_totales": 0,
    "europa_channel_id_reconocido": 0,
    "europa_tiene_directo": 0,
    "europa_no_es_basura": 0,
    "europa_aprobados_final": 0,
}
canales_vistos_sin_reconocer = set()

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

def parse_time(time_str):
    try:
        dt = datetime.strptime(time_str[:14], "%Y%m%d%H%M%S")
        offset = time_str[15:]
        if len(offset) >= 5:
            sign = 1 if offset[0] == '+' else -1
            h, m = int(offset[1:3]), int(offset[3:5])
            tz = timezone(timedelta(hours=sign*h, minutes=sign*m))
        else:
            tz = timezone.utc
        return dt.replace(tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def obtener_canal_logico(cid):
    """Unico proposito de esta funcion: reconocer Eurosport 1 y 2. Nada mas."""
    c = str(cid)
    if c in ["Eurosport 1 HD", "Eurosport 2"]:
        return "Eurosport"
    canales_vistos_sin_reconocer.add(c)
    return None

def limpiar_titulo_eurosport(titulo):
    t = re.sub(r'\[COLOR.*?\]', '', titulo)
    t = re.sub(r'\[/COLOR\]', '', t)
    t = t.replace("DIRECTO", "").strip(" -:")
    return ' '.join(t.split())

def extraer_eventos_eurosport():
    """Extrae eventos EXCLUSIVAMENTE de Eurosport 1/2, validados solo con el propio EPG.
    Sin dependencias externas de ningun tipo (sin Google, sin otras fuentes)."""
    eventos = []
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
                        tit_limpio = limpiar_titulo_eurosport(tit)
                        if not es_basura(tit_limpio):
                            contadores["europa_no_es_basura"] += 1
                            contadores["europa_aprobados_final"] += 1
                            eventos.append({
                                "titulo": tit_limpio,
                                "canal": "Eurosport",
                                "hora_utc": parse_time(elem.attrib.get("start")),
                                "duracion_min": 120
                            })
                elem.clear()
    except Exception as e:
        log.error(f"Error leyendo EPG Europa: {e}")

    return eventos

def main():
    log.info("🚀 INICIANDO INYECTOR (solo Eurosport 1/2, sin Google, sin fuentes externas)")

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

    eventos_nuevos = extraer_eventos_eurosport()
    log.info(f"✅ Eventos Eurosport aprobados por EPG: {len(eventos_nuevos)}")

    log.info("── RESUMEN DE EMBUDO ──")
    for k, v in contadores.items():
        log.info(f"  {k}: {v}")
    if canales_vistos_sin_reconocer:
        muestra = list(canales_vistos_sin_reconocer)[:15]
        log.info(f"  channel_id vistos SIN reconocer (muestra de {len(canales_vistos_sin_reconocer)}): {muestra}")

    with open(ARCHIVO_EVENTOS, "r", encoding="utf-8") as f:
        data = json.load(f)

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
