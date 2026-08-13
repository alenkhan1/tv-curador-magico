#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
INYECTOR EPG EUROSPORT — AllStreamTV
======================================
Enriquece eventos_hoy.json con la parrilla real de Eurosport 1/2 (EPG
Europa, fuente EPG_dobleM), usando el huso horario de España porque ese
es el huso en que ese canal realmente transmite (independiente de que
la app se use en Colombia).

Cambios de este rediseño (acordado 13-ago-2026):
1. Se ELIMINA por completo el bloque de EPG America (Win Sports/TyC/
   DSports). No existia fuente EPG confiable para esos canales; el
   validador de Google nunca se activaba en el workflow real (falta la
   API key en el YAML) y el bloque completo era trabajo inoficioso que
   ademas ensuciaba el archivo de eventos.
2. IDs verdaderamente unicos: se reemplaza el esquema
   "epg_{timestamp}_{primeras 5 letras}" (colisionaba cuando dos eventos
   se procesaban en el mismo segundo con titulo similar, causando el
   crash fatal de LazyVerticalGrid en la app por claves duplicadas) por
   un hash MD5 del titulo completo + hora + canal, que es deterministico
   y libre de colisiones.
3. Ventana diaria estricta: antes de inyectar, se descarta de
   eventos_hoy.json cualquier evento remanente de una corrida anterior
   que ya no cae dentro de la ventana "hoy" vigente en este momento, para
   no acumular basura entre las 3 corridas diarias del workflow.
4. Consume eventos_hoy.json en el formato nuevo
   {"generado_utc","base_media","eventos":[...]} y usa "id_xtream" (no
   URL completa) en cada fuente, igual que el curador base.
"""

import os
import json
import re
import time
import hashlib
import logging
import requests
import gzip
import io
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("inyector")

XTREAM_URL = os.environ.get("XTREAM_URL")
XTREAM_USER = os.environ.get("XTREAM_USER")
XTREAM_PASS = os.environ.get("XTREAM_PASS")
PUENTE_URL = os.environ.get("PUENTE_URL", "https://mi-dashboard-tv.onrender.com/api/puente_xtream")

ARCHIVO_EVENTOS = "eventos_hoy.json"
URL_EPG_EUROPA = "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml"

contadores = {
    "europa_programmes_totales": 0,
    "europa_channel_id_reconocido": 0,
    "europa_dentro_de_ventana_hoy": 0,
    "europa_no_es_basura": 0,
    "europa_aprobados_final": 0,
}

# ─── FUNCIONES DE APOYO ─────────────────────────────────────────────────────

def es_basura(titulo):
    """Excluye contenido claramente diferido/enlatado por patron de texto
    conocido. No se usa la palabra DIRECTO como criterio (no es confiable
    en la fuente disponible)."""
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
    if re.search(r'\bE\d+\b', t) or re.search(r'\bEPISODIO\s*\d+\b', t):
        return True
    return False

def parse_time_epg_a_dt(time_str):
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

def calcular_ventana_hoy_espana():
    """Eurosport transmite fisicamente desde España: 'hoy' se calcula en
    huso español (CEST UTC+2 en verano), no en huso Colombia."""
    ahora_utc = datetime.now(timezone.utc)
    tz_espana = timezone(timedelta(hours=2))
    hoy_espana = ahora_utc.astimezone(tz_espana).date()
    inicio_ventana = datetime.combine(hoy_espana, datetime.min.time(), tzinfo=tz_espana).astimezone(timezone.utc)
    fin_ventana = datetime.combine(hoy_espana, datetime.max.time(), tzinfo=tz_espana).astimezone(timezone.utc)
    return inicio_ventana, fin_ventana

def obtener_canal_logico(cid):
    c = str(cid)
    if c in ["Eurosport 1 HD", "Eurosport 2"]:
        return "Eurosport"
    return None

def limpiar_titulo_eurosport(titulo):
    t = re.sub(r'\[COLOR.*?\]', '', titulo)
    t = re.sub(r'\[/COLOR\]', '', t)
    t = re.sub(r'(?i)\bDIRECTO\b', '', t).strip(" -:")
    return ' '.join(t.split())

def crear_id_unico(titulo: str, hora_utc: str, canal: str) -> str:
    """ID deterministico y libre de colisiones (13-ago-2026): hash MD5
    del titulo completo + hora exacta + canal. Reemplaza el esquema viejo
    'epg_{timestamp}_{primeras 5 letras}' que colisionaba cuando dos
    eventos distintos se procesaban en el mismo segundo y compartian las
    primeras letras del titulo (causa confirmada del crash fatal en la
    app: LazyVerticalGrid con claves duplicadas)."""
    base = f"{titulo.strip().upper()}|{hora_utc}|{canal}"
    return f"epg_{hashlib.md5(base.encode('utf-8')).hexdigest()[:16]}"

# ─── PROCESADOR EPG ─────────────────────────────────────────────────────────

def extraer_eventos_eurosport(inicio_ventana, fin_ventana):
    eventos = []
    log.info(f"Ventana 'hoy' España (UTC): {inicio_ventana.isoformat()} -> {fin_ventana.isoformat()}")
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
                                "duracion_min": 120,
                            })
                elem.clear()
    except Exception as e:
        log.error(f"Error leyendo EPG Europa: {e}")

    return eventos

def deduplicar_eventos(eventos):
    """Agrupa repeticiones del mismo evento (mismo canal + mismo titulo
    exacto) que aparecen varias veces el mismo dia en la parrilla."""
    ahora = datetime.now(timezone.utc)
    grupos = {}
    for ev in eventos:
        clave = (ev["canal"], ev["titulo"].strip().upper())
        grupos.setdefault(clave, []).append(ev)

    resultado = []
    for clave, ocurrencias in grupos.items():
        ocurrencias.sort(key=lambda e: e["hora_utc"])
        futuras = [e for e in ocurrencias
                   if datetime.strptime(e["hora_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) >= ahora]
        elegido = futuras[0] if futuras else ocurrencias[-1]
        resultado.append(elegido)

    return resultado

def extraer_eventos():
    inicio_esp, fin_esp = calcular_ventana_hoy_espana()
    eventos = extraer_eventos_eurosport(inicio_esp, fin_esp)
    eventos = deduplicar_eventos(eventos)
    return eventos, inicio_esp, fin_esp

def podar_eventos_vencidos(data_eventos: list, inicio_ventana_espana, fin_ventana_espana):
    """Ventana diaria estricta (13-ago-2026): el curador captura SOLO
    eventos en vivo de hoy. Antes de inyectar, se descarta cualquier
    evento de Eurosport que quedo de una corrida anterior y que ya no
    cae en la ventana 'hoy' vigente en este momento (evita que las 3
    corridas diarias vayan acumulando basura de eventos vencidos)."""
    conservados = []
    podados = 0
    for ev in data_eventos:
        if ev.get("torneo") == "Eurosport" or "eurosport" in str(ev.get("torneo", "")).lower():
            try:
                hora_ev = datetime.strptime(ev["hora_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except Exception:
                conservados.append(ev)
                continue
            if not (inicio_ventana_espana <= hora_ev < fin_ventana_espana):
                podados += 1
                continue
        conservados.append(ev)
    if podados:
        log.info(f"🧹 Podados {podados} eventos de Eurosport vencidos de corridas anteriores.")
    return conservados

# ─── MAPEO DE STREAMS EUROSPORT ─────────────────────────────────────────────

def mapear_streams_eurosport():
    mapa = []
    try:
        url_x = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
        req_x = requests.get(PUENTE_URL, params={"url": url_x}, timeout=60)
        streams = req_x.json() if req_x.status_code == 200 else []
        log.info(f"Streams Xtream recibidos: {len(streams)}")
        for s in streams:
            n = (s.get("name") or "").upper()
            if "EUROSPORT" in n:
                mapa.append({"nombre": s.get("name"), "id_xtream": str(s.get("stream_id"))})
        log.info(f"Canal 'Eurosport': {len(mapa)} streams Xtream mapeados.")
    except Exception as e:
        log.error(f"Error mapeando Xtream: {e}")
    return mapa

# ─── INYECCIÓN ──────────────────────────────────────────────────────────────

def main():
    log.info("🚀 INICIANDO INYECTOR EUROSPORT (huso España, ventana diaria estricta, IDs unicos)")

    if not os.path.exists(ARCHIVO_EVENTOS):
        log.error("No se encontró eventos_hoy.json. Ejecuta primero el curador.")
        return

    with open(ARCHIVO_EVENTOS, "r", encoding="utf-8") as f:
        data_raw = json.load(f)

    # Compatibilidad con el formato nuevo {"eventos":[...]}
    if isinstance(data_raw, dict) and "eventos" in data_raw:
        contenedor = data_raw
        data = data_raw["eventos"]
    else:
        contenedor = {"generado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "base_media": "", "eventos": data_raw}
        data = contenedor["eventos"]

    log.info("🔗 Mapeando streams de Eurosport a través del puente...")
    fuentes_eurosport = mapear_streams_eurosport()

    eventos_nuevos, inicio_esp, fin_esp = extraer_eventos()
    log.info(f"✅ Eventos Eurosport aprobados (dentro de ventana de hoy, sin basura): {contadores['europa_aprobados_final']}")
    log.info("── RESUMEN DE EMBUDO ──")
    for k, v in contadores.items():
        log.info(f"  {k}: {v}")

    data = podar_eventos_vencidos(data, inicio_esp, fin_esp)

    if not fuentes_eurosport:
        log.warning("No hay streams de Eurosport disponibles en el proveedor. No se inyecta nada nuevo.")
        contenedor["eventos"] = data
        with open(ARCHIVO_EVENTOS, "w", encoding="utf-8") as f:
            json.dump(contenedor, f, ensure_ascii=False, indent=2)
        return

    creados = 0
    para_inyeccion = []

    for en in eventos_nuevos:
        id_nuevo = crear_id_unico(en["titulo"], en["hora_utc"], en["canal"])

        existente = next((item for item in data if item.get("id") == id_nuevo), None)
        if existente:
            urls_actuales = {f["id_xtream"] for f in existente.get("fuentes", [])}
            for fn in fuentes_eurosport:
                if fn["id_xtream"] not in urls_actuales:
                    existente["fuentes"].append(fn)
            continue

        para_inyeccion.append({
            "id": id_nuevo,
            "titulo": en["titulo"], "torneo": "Eurosport", "categoria": "Deportes",
            "hora_utc": en["hora_utc"], "duracion_min": en["duracion_min"],
            "logo_local": "", "logo_visitante": "", "banner": "", "tier": 2,
            "fuentes": list(fuentes_eurosport),
        })
        creados += 1

    data.extend(para_inyeccion)
    data.sort(key=lambda x: x.get("hora_utc", ""))

    contenedor["eventos"] = data
    with open(ARCHIVO_EVENTOS, "w", encoding="utf-8") as f:
        json.dump(contenedor, f, ensure_ascii=False, indent=2)

    log.info(f"💾 Inyección terminada. Eventos nuevos inyectados: {creados}")

if __name__ == "__main__":
    main()
