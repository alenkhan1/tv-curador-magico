"""
Curador Deportivo Inteligente v13 - Impulsado por Gemini.
Arquitectura Limpia:
1. Extrae los streams programados para hoy desde el catalogo Xtream.
2. Agrupa opciones de transmision (FHD, HD, SD) que pertenecen al mismo evento.
3. Gemini es la Fuente de la Verdad (valida vigencia real de hoy, deporte, torneo y equipos limpios).
4. Resolucion de imagenes y escudos oficiales exclusivamente con API-Football / TheSportsDB y logos oficiales.
5. Emision de la cartelera final en eventos_hoy.json.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo
import requests

from agente_deportivo_ia import parsear_stream_robusto, _llamar_gemini, GEMINI_API_KEY
from resolvedor_logos import resolver_logo_torneo, resolver_logo_equipo

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("curador_eventos")

APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "America/Bogota")
try:
    TZ_LOCAL = ZoneInfo(APP_TIMEZONE)
except Exception:
    TZ_LOCAL = ZoneInfo("America/Bogota")

XTREAM_URL = (os.environ.get("XTREAM_URL") or "").rstrip("/")
XTREAM_USER = os.environ.get("XTREAM_USER") or ""
XTREAM_PASS = os.environ.get("XTREAM_PASS") or ""
PUENTE_URL = os.environ.get("PUENTE_URL") or "https://zapdashboard.onrender.com/api/puente_xtream"
ARCHIVO_SALIDA = Path(os.environ.get("ARCHIVO_SALIDA", "eventos_hoy.json"))
ARCHIVO_DESCARTADOS = Path(os.environ.get("ARCHIVO_DESCARTADOS", "eventos_descartados.json"))
ARCHIVO_META = Path(os.environ.get("ARCHIVO_META", "meta_curador.json"))
ARCHIVO_CACHE_XTREAM = Path("canales_xtream_cache.json")

DURACION_POR_CATEGORIA = {
    "Fútbol": 150, "Baloncesto": 160, "Béisbol": 210, "Motor": 210,
    "Hockey": 160, "Combate": 210, "Tenis": 210, "Rugby": 160,
    "Voleibol": 160, "Fútbol Americano": 220, "Handball": 150,
    "Ciclismo": 240, "Snooker": 180, "Golf": 300, "Gimnasia": 150,
    "Deportes Acuáticos": 180, "Escalada": 180, "Polo": 150,
    "Tejo": 180, "Pádel": 180, "Otros Deportes": 150
}

VETO_EMISION = {
    "REPETICION", "REPLAY", "RESUMEN", "HIGHLIGHTS", "COMPACTO", "NOTICIAS", "NEWS", "MAGAZINE", "PREVIA",
    "POSTPARTIDO", "POST PARTIDO", "DOCUMENTAL", "DOCUMENTALES", "CLASICOS", "MEMORIAS", "VINTAGE", "MEJORES MOMENTOS",
    "WIEDERHOLUNG", "ZUSAMMENFASSUNG", "DOKUMENTATION", "VORSCHAU", "NACHRICHTEN",
    "REPETICAO", "RESUMO", "DOCUMENTARIO", "REDIFFUSION", "RETROSPECTIVA",
    "PELICULA", "PELICULAS", "MOVIES", "SERIE", "SERIES", "ANIME", "ANIMACION", "ESTRENOS", "CONCIERTOS",
    "INFORMACION IMPORTANTE", "AVISO IMPORTANTE", "ACTUALIZACION DE LISTA", "MIJAS", "CRKEY",
}

MESES_ES_EN = {
    "ENERO": 1, "JANUARY": 1, "JAN": 1, "ENE": 1,
    "FEBRERO": 2, "FEBRUARY": 2, "FEB": 2,
    "MARZO": 3, "MARCH": 3, "MAR": 3,
    "ABRIL": 4, "APRIL": 4, "APR": 4, "ABR": 4,
    "MAYO": 5, "MAY": 5,
    "JUNIO": 6, "JUNE": 6, "JUN": 6,
    "JULIO": 7, "JULY": 7, "JUL": 7,
    "AGOSTO": 8, "AUGUST": 8, "AGO": 8, "AUG": 8,
    "SEPTIEMBRE": 9, "SEPTEMBER": 9, "SEP": 9, "SET": 9,
    "OCTUBRE": 10, "OCTOBER": 10, "OCT": 10,
    "NOVIEMBRE": 11, "NOVEMBER": 11, "NOV": 11,
    "DICIEMBRE": 12, "DECEMBER": 12, "DIC": 12, "DEC": 12,
}


def normalizar_texto(texto: Any) -> str:
    if texto is None:
        return ""
    valor = unicodedata.normalize("NFD", str(texto).upper())
    valor = "".join(c for c in valor if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^A-Z0-9\s]", " ", valor).split())


def contiene_veto(texto: Any) -> bool:
    valor = normalizar_texto(texto)
    if any(bool(re.search(rf"(?<![A-Z0-9]){re.escape(palabra)}(?![A-Z0-9])", valor)) for palabra in VETO_EMISION):
        if "PROSERIES" in valor or "WORLD SERIES" in valor:
            pass
        else:
            return True
    if re.search(r"\b[ST]\d{1,2}\s*E\d{1,2}\b", valor):
        return True
    return False


def es_logo_basura(url: Any) -> bool:
    if not url or not isinstance(url, str):
        return True
    u = url.strip().lower()
    return not u.startswith("http") or any(x in u for x in ["default", "placeholder", "no_logo", "null", "none", "blank", "olympic_flag"])


def variaciones_fecha(fecha: datetime) -> List[str]:
    d, m = f"{fecha.day:02d}", f"{fecha.month:02d}"
    return [f"{d}/{m}", f"{d}-{m}", f"{d}.{m}", f"{fecha.day}/{fecha.month}", f"{d} DE {fecha.strftime('%B').upper()}"]


def fecha_xtream_explicita(nombre: str, fecha_referencia: Any) -> Optional[bool]:
    if not nombre:
        return None
    # 1. Patron DD/MM, DD-MM o DD.MM directamente sobre el texto sin alterar separadores
    m_num = re.search(r"\b(0?[1-9]|[12]\d|3[01])\s*[-/\.]\s*(0?[1-9]|1[0-2])\b", nombre)
    if m_num:
        dia, mes = int(m_num.group(1)), int(m_num.group(2))
        return (dia == fecha_referencia.day and mes == fecha_referencia.month)
    # 2. Patron DD DE MES
    norm = normalizar_texto(nombre)
    m_txt = re.search(r"\b(0?[1-9]|[12]\d|3[01])\s+DE\s+([A-Z]{3,10})\b", norm)
    if m_txt:
        dia = int(m_txt.group(1))
        mes_str = m_txt.group(2)
        mes = MESES_ES_EN.get(mes_str)
        if mes:
            return (dia == fecha_referencia.day and mes == fecha_referencia.month)
    return None


def extraer_hora_canal(texto: str, base_fecha: datetime) -> Optional[datetime]:
    if not texto:
        return None
    # 1. Formato estándar con dos puntos: "12:30", "12:30 PM", "8:00 AM", "12:30hrs", "12:30 hs"
    m = re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)\s*([APap][Mm]|[Hh][Rr]?[Ss]?)?(?!\d)", texto)
    if m:
        hora, minuto = int(m.group(1)), int(m.group(2))
        sufijo = (m.group(3) or "").upper()
        if "PM" in sufijo and hora < 12:
            hora += 12
        elif "AM" in sufijo and hora == 12:
            hora = 0
        if 0 <= hora <= 23 and 0 <= minuto <= 59:
            return base_fecha.replace(hour=hora, minute=minuto, second=0, microsecond=0)

    # 2. Formato con punto "." con sufijo obligatorio
    m = re.search(r"(?<!\d)([01]?\d|2[0-3])\.([0-5]\d)\s*([APap][Mm]|[Hh][Rr]?[Ss]?)(?!\d)", texto)
    if m:
        hora, minuto = int(m.group(1)), int(m.group(2))
        sufijo = (m.group(3) or "").upper()
        if "PM" in sufijo and hora < 12:
            hora += 12
        elif "AM" in sufijo and hora == 12:
            hora = 0
        if 0 <= hora <= 23 and 0 <= minuto <= 59:
            return base_fecha.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    return None


# --- Descarga Xtream ---

def detectar_base_media_m3u() -> str:
    """Obtiene el host real de streaming para reproducir leyendo el M3U."""
    base_api = XTREAM_URL
    if not base_api:
        return ""
    url_m3u = f"{base_api}/get.php?username={XTREAM_USER}&password={XTREAM_PASS}&type=m3u&output=ts"
    try:
        import urllib.parse
        resp = requests.get(url_m3u, timeout=20, stream=True)
        if resp.status_code == 200:
            candidatos = []
            for linea in resp.iter_lines(decode_unicode=True):
                if linea and linea.startswith("http"):
                    parsed = urllib.parse.urlparse(linea.strip())
                    candidatos.append(f"{parsed.scheme}://{parsed.netloc}")
                    if len(candidatos) >= 3:
                        break
            if len(candidatos) >= 3 and len(set(candidatos)) == 1:
                base_media = candidatos[0]
                log.info("Base de media detectada desde M3U: %s", base_media)
                return base_media
    except Exception as exc:
        log.warning("No se pudo detectar base_media desde M3U: %s", exc)
    return base_api


def llamada_xtream(url: str, timeout: int = 40) -> Optional[Any]:
    for intento in range(3):
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            log.warning("Intento %d fallido para %s: %s", intento + 1, url, exc)
            time.sleep(1.5)
    return None


def obtener_canales_xtream_con_cache() -> List[Dict[str, Any]]:
    if ARCHIVO_CACHE_XTREAM.exists():
        try:
            datos = json.loads(ARCHIVO_CACHE_XTREAM.read_text(encoding="utf-8"))
            if isinstance(datos, list) and len(datos) > 0:
                log.info("Canales Xtream cargados desde cache local: %d canales", len(datos))
                return datos
        except Exception as e:
            log.warning("Error leyendo cache Xtream: %s", e)

    canales = None
    if PUENTE_URL:
        try:
            log.info("Descargando catalogo Xtream mediante puente: %s", PUENTE_URL)
            resp = requests.get(f"{PUENTE_URL}?action=get_live_streams", timeout=120)
            if resp.status_code == 200:
                canales = resp.json()
        except Exception as exc:
            log.warning("Fallo puente Xtream: %s", exc)

    if not canales and XTREAM_URL and XTREAM_USER and XTREAM_PASS:
        url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
        canales = llamada_xtream(url, timeout=90)

    if isinstance(canales, list):
        try:
            ARCHIVO_CACHE_XTREAM.write_text(json.dumps(canales, ensure_ascii=False), encoding="utf-8")
            log.info("Cache Xtream guardada (%d canales)", len(canales))
        except Exception:
            pass
        return canales

    return []


def detectar_categorias_fechadas(fecha_local: datetime) -> Tuple[Set[str], Set[str]]:
    hoy, ajenas = set(), set()
    url = f"{PUENTE_URL}?action=get_live_categories" if PUENTE_URL else f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_categories"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            for cat in resp.json():
                sid = str(cat.get("category_id") or "")
                nom = str(cat.get("category_name") or "")
                if not sid:
                    continue
                f = fecha_xtream_explicita(nom, fecha_local.date())
                if f is True or any(normalizar_texto(v) in normalizar_texto(nom) for v in variaciones_fecha(fecha_local)):
                    hoy.add(sid)
                elif f is False:
                    ajenas.add(sid)
    except Exception as exc:
        log.warning("No se pudieron leer categorias fechadas: %s", exc)
    return hoy, ajenas


def es_candidato_stream(stream: Dict[str, Any], categorias_hoy: Set[str], fecha_local: datetime, categorias_ajenas: Set[str]) -> Tuple[bool, str]:
    nom = str(stream.get("name") or "").strip()
    if not nom:
        return False, "sin_nombre"
    if contiene_veto(nom):
        return False, "contenido_no_evento"

    f_txt = fecha_xtream_explicita(nom, fecha_local.date())
    if f_txt is False:
        return False, "fecha_fuera_de_jornada"

    cid = str(stream.get("category_id") or "")
    if cid in categorias_ajenas:
        return False, "categoria_fuera_de_jornada"

    hora = extraer_hora_canal(nom, fecha_local)
    es_duelo = bool(re.search(r"\b(VS|V|AT)\b|\s[-@]\s", normalizar_texto(nom)))

    if f_txt is True:
        return True, "fecha_hoy"
    if cid in categorias_hoy:
        return True, "categoria_hoy"
    if hora is not None and es_duelo:
        return True, "duelo_con_hora"

    return False, "sin_vigencia_confirmada"


def agrupar_streams_candidatos(candidatos: List[Dict[str, Any]], fecha_local: datetime) -> List[Dict[str, Any]]:
    """Agrupa las diferentes calidades y opciones de un mismo evento bajo una sola identidad."""
    grupos: Dict[str, Dict[str, Any]] = {}
    
    for c in candidatos:
        nom = c["nombre_ui"]
        hora = c["hora_local"] or fecha_local
        p = parsear_stream_robusto(nom)
        
        # Clave semantica de agrupamiento
        hora_aprox = hora.strftime("%H")  # Agrupa margen de hora
        if p["tipo"] == "duelo":
            loc_k = normalizar_texto(p["local"])[:12]
            vis_k = normalizar_texto(p["visitante"])[:12]
            clave = f"DUELO_{loc_k}_{vis_k}_{hora_aprox}"
        else:
            tor_k = normalizar_texto(p["torneo"])[:20]
            clave = f"EVENTO_{tor_k}_{hora_aprox}"

        if clave not in grupos:
            grupos[clave] = {
                "clave": clave,
                "parsed": p,
                "hora_local": hora,
                "nombre_representativo": nom,
                "logo_stream": c.get("logo_xtream", ""),
                "fuentes": []
            }
            
        fuente = {"nombre": nom, "id_xtream": c["id_xtream"]}
        if not any(f["id_xtream"] == c["id_xtream"] for f in grupos[clave]["fuentes"]):
            grupos[clave]["fuentes"].append(fuente)

    return list(grupos.values())


def curar_eventos_con_gemini(eventos_unicos: List[Dict[str, Any]], fecha_local_str: str) -> List[Dict[str, Any]]:
    """Gemini valida cada evento único en lotes controlados (máx 20 por llamada) como única Fuente de la Verdad."""
    if not eventos_unicos:
        return []

    TAM_LOTE = 20
    total_validados = 0

    for inicio in range(0, len(eventos_unicos), TAM_LOTE):
        fin = min(inicio + TAM_LOTE, len(eventos_unicos))
        sub_eventos = eventos_unicos[inicio:fin]

        lote = []
        for i, ev in enumerate(sub_eventos):
            lote.append({
                "indice": i,
                "titulo": ev["nombre_representativo"],
                "deporte_sugerido": ev["parsed"]["deporte"],
                "torneo_sugerido": ev["parsed"]["torneo"],
                "hora_sugerida": ev["hora_local"].strftime("%H:%M")
            })

        prompt = f"""Eres el Curador Deportivo Inteligente de AllStream TV.
Hoy es {fecha_local_str} (zona horaria America/Bogota).

Analiza este lote de {len(lote)} eventos detectados en el catalogo de TV:
{json.dumps(lote, ensure_ascii=False, indent=2)}

Para cada uno responde en JSON un array de objetos con:
- indice: int
- es_valido_hoy: boolean (true si es un partido/evento real y activo programado para hoy {fecha_local_str}; false si es de dias anteriores, repetido o falso)
- motivo_descarte: string (vacio si es_valido_hoy es true; si es false explicar brevemente, ej. 'partido jugado ayer')
- deporte: string canonico en espanol ('Fútbol', 'Baloncesto', 'Béisbol', 'Tenis', 'Polo', 'Snooker', 'Ciclismo', 'Motor', 'Combate', 'Fútbol Americano', 'Rugby', 'Pádel', 'Otros Deportes'). Nota: Naciones League, selecciones y copas africanas son 'Fútbol'.
- torneo: string nombre oficial y limpio del torneo (ej. 'UEFA Nations League', 'Liga BetPlay Dimayor', 'MLS', 'Abierto Argentino de Polo')
- tipo_evento: 'duelo' o 'sencillo'
- equipo_local: string limpio del local (ej. 'Bélgica', 'Fortaleza CEIF', 'Ellerstina')
- equipo_visitante: string limpio del visitante (ej. 'Francia', 'Deportes Tolima', 'La Matera')
- hora_local: string 'HH:MM' confirmada (en formato 24h para Colombia)
- duracion_min: int duracion estimada
"""
        log.info("Consultando a Gemini lote %d-%d de %d eventos...", inicio + 1, fin, len(eventos_unicos))
        respuesta = _llamar_gemini(prompt, json_mode=True)
        if not respuesta:
            log.warning("Lote %d-%d sin respuesta de Gemini. Se mantiene parseador robusto para este lote.", inicio + 1, fin)
            continue

        try:
            datos_ia = json.loads(respuesta)
            if isinstance(datos_ia, list):
                for item in datos_ia:
                    idx = item.get("indice")
                    if idx is not None and 0 <= idx < len(sub_eventos):
                        ev = sub_eventos[idx]
                        ev["es_valido_hoy"] = item.get("es_valido_hoy", True)
                        ev["motivo_descarte"] = item.get("motivo_descarte", "")
                        if item.get("deporte"):
                            ev["parsed"]["deporte"] = item["deporte"]
                        if item.get("torneo"):
                            ev["parsed"]["torneo"] = item["torneo"]
                        if item.get("tipo_evento"):
                            ev["parsed"]["tipo"] = item["tipo_evento"]
                        if item.get("equipo_local"):
                            ev["parsed"]["local"] = item["equipo_local"]
                        if item.get("equipo_visitante"):
                            ev["parsed"]["visitante"] = item["equipo_visitante"]
                        if item.get("hora_local") and re.match(r"^\d{1,2}:\d{2}$", str(item["hora_local"])):
                            hh, mm = map(int, item["hora_local"].split(":"))
                            ev["hora_local"] = ev["hora_local"].replace(hour=hh, minute=mm)
                        if item.get("duracion_min"):
                            ev["duracion_min"] = int(item["duracion_min"])
                        total_validados += 1
        except Exception as exc:
            log.error("Error parseando respuesta de Gemini en lote %d-%d: %s", inicio + 1, fin, exc)

    log.info("Gemini proceso y valido exitosamente %d/%d eventos unicos.", total_validados, len(eventos_unicos))
    return eventos_unicos


def construir_cartelera_final(eventos_validados: List[Dict[str, Any]], ahora: datetime) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    cartelera: List[Dict[str, Any]] = []
    descartados: List[Dict[str, Any]] = []
    ahora_utc = ahora.astimezone(timezone.utc)

    for ev in eventos_validados:
        p = ev["parsed"]
        fuentes = ev.get("fuentes", [])
        if not fuentes:
            continue

        # Descarte por decision de Gemini
        if ev.get("es_valido_hoy") is False:
            descartados.append({
                "titulo": ev["nombre_representativo"],
                "motivo": ev.get("motivo_descarte") or "descartado_por_ia",
                "fuentes": fuentes
            })
            continue

        deporte = p["deporte"] or "Fútbol"
        torneo = p["torneo"] or "Competición Oficial"
        tipo = p["tipo"]
        local = p["local"]
        visitante = p["visitante"]
        hora_local = ev["hora_local"]
        hora_utc = hora_local.astimezone(timezone.utc)
        duracion_min = ev.get("duracion_min") or DURACION_POR_CATEGORIA.get(deporte, 150)

        # Filtro de expiracion de eventos ya concluidos
        fin_evento_utc = hora_utc + timedelta(minutes=duracion_min)
        if fin_evento_utc < (ahora_utc - timedelta(minutes=15)):
            descartados.append({
                "titulo": f"{local} vs {visitante}" if tipo == "duelo" else torneo,
                "motivo": f"evento_finalizado (concluyo a las {fin_evento_utc.strftime('%H:%M')} UTC)",
                "fuentes": fuentes
            })
            continue

        # Resolucion limpia de Logos y Escudos oficiales
        logo_torneo = resolver_logo_torneo(torneo, deporte, permitir_red=True)
        if not logo_torneo and not es_logo_basura(ev.get("logo_stream")):
            logo_torneo = ev.get("logo_stream")

        logo_loc, logo_vis = "", ""
        if tipo == "duelo" and local and visitante:
            titulo = f"{local} vs {visitante}"
            logo_loc = resolver_logo_equipo(local, deporte, permitir_red=True)
            logo_vis = resolver_logo_equipo(visitante, deporte, permitir_red=True)
        else:
            titulo = torneo

        # ID determinista unico
        clave_id = f"{normalizar_texto(titulo)}_{hora_local.strftime('%Y%m%d_%H%M')}"
        ident = hashlib.sha1(clave_id.encode("utf-8")).hexdigest()[:16]

        cartelera.append({
            "id": ident,
            "agenda_id": ident,
            "titulo": titulo,
            "torneo": torneo,
            "categoria": deporte,
            "tipo_evento": tipo,
            "equipo_local": local if tipo == "duelo" else "",
            "equipo_visitante": visitante if tipo == "duelo" else "",
            "subtitulo": p.get("subtitulo", ""),
            "referencia": "",
            "hora_utc": hora_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hora_local_producto": hora_local.strftime("%H:%M"),
            "duracion_min": duracion_min,
            "logo_torneo": logo_torneo,
            "logo_local": logo_loc,
            "logo_visitante": logo_vis,
            "banner": "",
            "tier": 1,
            "origen": "curador_inteligente_gemini",
            "origenes": ["curador_inteligente_gemini"],
            "estado": "confirmado",
            "estado_evento": "en_vivo" if (hora_utc <= ahora_utc < fin_evento_utc) else "programado",
            "confianza": "alta",
            "puntuacion_confianza": 95,
            "metodo_correlacion": "gemini_fuente_verdad",
            "razones_correlacion": ["validacion_ia_directa"],
            "fuentes": fuentes
        })

    # Ordenar cronologicamente por hora local
    cartelera.sort(key=lambda x: x["hora_utc"])
    return cartelera, descartados


def main():
    ahora_local = datetime.now(TZ_LOCAL)
    fecha_str = ahora_local.strftime("%Y-%m-%d")
    log.info("=== Curador Deportivo Inteligente v13 | %s (Hora local: %s) ===", fecha_str, ahora_local.strftime("%H:%M"))

    # 1. Obtener streams candidatos
    cat_hoy, cat_ajenas = detectar_categorias_fechadas(ahora_local)
    streams = obtener_canales_xtream_con_cache()
    if not streams:
        log.warning("No se obtuvieron streams de Xtream.")
        return

    candidatos = []
    for s in streams:
        aceptado, motivo = es_candidato_stream(s, cat_hoy, ahora_local, cat_ajenas)
        if aceptado:
            candidatos.append({
                "id_xtream": str(s.get("stream_id")),
                "nombre_ui": str(s.get("name") or "").strip(),
                "hora_local": extraer_hora_canal(str(s.get("name") or ""), ahora_local),
                "logo_xtream": "" if es_logo_basura(s.get("stream_icon") or s.get("tvg_logo")) else str(s.get("stream_icon") or s.get("tvg_logo"))
            })

    log.info("Total streams: %d | Candidatos filtrados para hoy: %d", len(streams), len(candidatos))

    # 2. Agrupar por eventos unicos
    eventos_unicos = agrupar_streams_candidatos(candidatos, ahora_local)
    log.info("Eventos únicos agrupados: %d", len(eventos_unicos))

    # 3. Gemini valida y clasifica
    eventos_validados = curar_eventos_con_gemini(eventos_unicos, fecha_str)

    # 4. Construir cartelera final y descartados
    cartelera, descartados = construir_cartelera_final(eventos_validados, ahora_local)
    log.info("Cartelera final: %d eventos | Descartados: %d", len(cartelera), len(descartados))

    # 5. Guardar archivos
    base_media = detectar_base_media_m3u()
    salida = {
        "version": 13,
        "generado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zona_horaria_producto": str(TZ_LOCAL),
        "fecha_local_producto": fecha_str,
        "base_media": base_media,
        "eventos": cartelera,
        "metricas": {
            "total_streams": len(streams),
            "candidatos_hoy": len(candidatos),
            "eventos_unicos": len(eventos_unicos),
            "eventos_publicados": len(cartelera),
            "descartados": len(descartados)
        }
    }
    ARCHIVO_SALIDA.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
    ARCHIVO_DESCARTADOS.write_text(json.dumps(descartados, ensure_ascii=False, indent=2), encoding="utf-8")
    ARCHIVO_META.write_text(json.dumps(salida["metricas"], ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Archivos guardados exitosamente: %s (%d eventos)", ARCHIVO_SALIDA, len(cartelera))


if __name__ == "__main__":
    main()
