#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CURADOR DE EVENTOS DEPORTIVOS EN VIVO — AllStreamTV
=====================================================
.
"""

import os
import re
import json
import time
import logging
import requests
import unicodedata
from collections import Counter
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from urllib.parse import urlparse

# ─── LOGGING ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("curador")

# ─── CONFIGURACION DE ENTORNO ───────────────────────────────────────────────
XTREAM_URL = os.environ.get("XTREAM_URL")
XTREAM_USER = os.environ.get("XTREAM_USER")
XTREAM_PASS = os.environ.get("XTREAM_PASS")

THESPORTSDB_KEY = os.environ.get("THESPORTSDB_KEY", "123")
THESPORTSDB_BASE = f"https://www.thesportsdb.com/api/v1/json/{THESPORTSDB_KEY}"
PUENTE_URL = os.environ.get("PUENTE_URL", "https://mi-dashboard-tv.onrender.com/api/puente_xtream")

# Huso de referencia SOLO para saber que dia es "hoy" al consultar TheSportsDB.
# NO se usa para interpretar la hora de los canales (eso se calibra solo).
ZONA_COLOMBIA = timezone(timedelta(hours=-5))
FECHA_HOY = datetime.now(ZONA_COLOMBIA).strftime("%Y-%m-%d")

ARCHIVO_CACHE = "agenda_api_v8.json"
HORAS_CACHE = 4
ARCHIVO_SALIDA = "eventos_hoy.json"

# ─── UMBRALES DE MATCHING ───────────────────────────────────────────────────
UMBRAL_FUSION = 55.0       # confianza suficiente para fusionar como el mismo evento
UMBRAL_PRESTAMO = 30.0     # confianza insuficiente para fusionar, pero sirve para "prestar" logo/torneo
MIN_EVENTOS_POR_CATEGORIA = 3  # bajo este umbral de eventos/dia, la categoria se agrupa en "Otros Deportes"
CATEGORIA_AGRUPADORA = "Otros Deportes"

# ─── DEPORTES A CONSULTAR EN THESPORTSDB ────────────────────────────────────
DEPORTES_MAP = {
    "Fútbol": "Soccer", "Baloncesto": "Basketball", "Tenis": "Tennis",
    "Motor": "Motorsport", "Béisbol": "Baseball", "Hockey": "Ice Hockey",
    "Voleibol": "Volleyball", "Rugby": "Rugby", "Fútbol Americano": "American Football",
    "Combate": "Fighting", "Ciclismo": "Cycling",
}

DURACION_POR_DEPORTE = {
    "Fútbol": 120, "Baloncesto": 150, "Tenis": 180, "Motor": 210,
    "Béisbol": 210, "Hockey": 150, "Combate": 180, "Deportes": 180,
    "Ciclismo": 300, "Voleibol": 150, "Rugby": 150, "Fútbol Americano": 210,
    "Otros Deportes": 150,
}

TRADUCTOR_JERGA = {
    "F1": "FORMULA 1", "MOTO GP": "MOTOGP", "UCL": "CHAMPIONS LEAGUE",
    "LIV": "LIV GOLF", "PGA": "PGA TOUR", "PREMIER": "PREMIER LEAGUE",
    "MLB": "MAJOR LEAGUE BASEBALL", "NBA": "NATIONAL BASKETBALL ASSOCIATION",
    "NFL": "NATIONAL FOOTBALL LEAGUE", "NHL": "NATIONAL HOCKEY LEAGUE",
    "UFC": "ULTIMATE FIGHTING CHAMPIONSHIP", "BKFC": "BARE KNUCKLE FIGHTING CHAMPIONSHIP",
    "USAC": "UNITED STATES AUTO CLUB",
}

# NOTA (13-ago-2026): se retiro "OPEN" de Tenis. Era ambiguo (US Open de
# Golf, Tenis, incluso Ajedrez comparten esa palabra) y el orden accidental
# del diccionario hacia que TODO "Open" cayera en Tenis. Ahora Golf se
# detecta por presencia de "GOLF"/"PGA"/"LIV"/"MASTERS" (sin depender de
# la palabra suelta "OPEN"), y Tenis se detecta por sus propios torneos.
# El ORDEN de este diccionario si importa: las categorias mas especificas
# van primero para evitar que una palabra generica se robe el match de una
# categoria con señal mas fuerte.
CATEGORIAS_RESCATE = {
    "Motor": ["F1", "F2", "F3", "FORMULA", "NASCAR", "GP ", "MOTOGP", "RALLY",
              "INDYCAR", "SPRINT CAR", "USAC", "SILVER CROWN", "IRC", "IMSA",
              "WRC", "AMA ", "MOTOCROSS", "SUPERCROSS", "PDRA", "NHRA", "DRAG RACING"],
    "Béisbol": ["MLB", "LMB", "BEISBOL", "BASEBALL", "DIAMONDBACKS", "CUBS",
                "YANKEES", "RED SOX", "DODGERS", "PIRATES", "REDS", "ASTROS",
                "RANGERS", "PADRES", "GIANTS", "MARINERS"],
    "Baloncesto": ["NBA", "WNBA", "BASKETBALL", "LAKERS", "CELTICS", "BULLS",
                   "CAVALIERS", "PISTONS", "MAGIC", "RAPTORS", "ENDESA", "EUROLEAGUE"],
    "Combate": ["WWE", "SMACKDOWN", "RAW", "AEW", "LUCHA", "WRESTLING",
                "WRESTLEMANIA", "BOXEO", "BOXING", "PESAJE", "FIGHT", "COMBATE",
                "RING", "UFC", "MMA", "BELLATOR", "ONE CHAMPIONSHIP", "FIGHT PASS",
                "BKFC", "MVPW"],
    "Hockey": ["NHL", "SABRES", "BRUINS", "CANADIENS", "LIGHTNING", "HOCKEY", "STANLEY"],
    "Ciclismo": ["CICLISMO", "CYCLING", "TOUR DE FRANCE", "TOUR COLOMBIA",
                 "VUELTA ESPAÑA", "VUELTA A ESPAÑA", "GIRO DE ITALIA",
                 "GIRO D ITALIA", "UCI", "ETAPA", "BERNAL", "CARAPAZ", "NAIRO"],
    "Fútbol Americano": ["NFL", "SUPER BOWL"],
    "Tenis": ["ATP", "WTA", "TENIS", "TENNIS", "WIMBLEDON", "ROLAND GARROS",
              "US OPEN TENNIS", "AUSTRALIAN OPEN", "PADEL"],
    "Deportes": ["PGA", "LIV GOLF", "GOLF", "MASTERS DE GOLF", "NCAA", "DERBY",
                 "KENTUCKY", "NATACION", "SWIMMING", "ATLETISMO", "AJEDREZ", "CHESS"],
    "Fútbol": ["LIGA", "SERIE A", "PREMIER", "FUTBOL", "SOCCER", "NWSL", "MLS",
               "CHAMPIONS", "LEAGUE", "SUDAMERICANA", "COPA", "BUNDESLIGA",
               "LIGUE 1", "LEAGUES CUP", "BETPLAY"],
}

LOGOS_RESCATE = {
    "Motor": "https://img.icons8.com/color/512/f1-car.png",
    "Béisbol": "https://img.icons8.com/color/512/baseball.png",
    "Baloncesto": "https://img.icons8.com/color/512/basketball.png",
    "Fútbol": "https://img.icons8.com/color/512/football2.png",
    "Combate": "https://img.icons8.com/color/512/boxing-glove.png",
    "Deportes": "https://img.icons8.com/color/512/stadium.png",
    "Hockey": "https://img.icons8.com/color/512/ice-hockey.png",
    "Tenis": "https://img.icons8.com/color/512/tennis.png",
    "Ciclismo": "https://img.icons8.com/color/512/cycling.png",
    "Fútbol Americano": "https://img.icons8.com/color/512/football2.png",
    "Otros Deportes": "https://img.icons8.com/color/512/stadium.png",
}

RUIDO_CANAL = [
    r"\bPPV\b", r"\bLIVE\s*EVENT\s*\d*\b", r"\bLIVE\s*\d+\b",
    r"\bHD\b", r"\bSD\b", r"\bFHD\b", r"\b4K\b", r"\bOP\d+\b",
    r"\bENG\b", r"\bESP\b", r"\bGER\b", r"\bPEA\s*\d+\b", r"\bPARA\+\s*\d+\b",
    r"\bVIP\b", r"\bAM\b", r"\bUS\|", r"^US\|", r"\bREPETICION\b", r"\bRESUMEN\b",
]

# ─── UTILIDADES DE FECHA Y TEXTO ────────────────────────────────────────────

def obtener_variaciones_fecha_hoy() -> list:
    dt = datetime.now(ZONA_COLOMBIA)
    d, m = dt.strftime("%d"), dt.strftime("%m")
    meses_es = {"01":"ENE","02":"FEB","03":"MAR","04":"ABR","05":"MAY","06":"JUN",
                "07":"JUL","08":"AGO","09":"SEP","10":"OCT","11":"NOV","12":"DIC"}
    meses_en = {"01":"JAN","02":"FEB","03":"MAR","04":"APR","05":"MAY","06":"JUN",
                "07":"JUL","08":"AUG","09":"SEP","10":"OCT","11":"NOV","12":"DEC"}
    m_es, m_en = meses_es[m], meses_en[m]
    return [
        f"{d}/{m}", f"{d}-{m}", f"{d} {m}", f"{d}.{m}",
        f"{m}/{d}", f"{m}-{d}", f"{m} {d}", f"{m}.{d}",
        f"{d} {m_es}", f"{m_es} {d}", f"{d} {m_en}", f"{m_en} {d}",
        f"{m_en} {int(d)}",
    ]

def limpiar_nombre_categoria(nombre: str) -> str:
    nombre = nombre.upper()
    nombre = re.sub(r'[^\w\s\/\-\.]', ' ', nombre)
    return ' '.join(nombre.split())

def normalizar_base(texto: str) -> str:
    if not texto:
        return ""
    t = str(texto).upper()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return t

def extraer_hora_evento(texto_original: str):
    """
    Extrae la hora del evento como datetime NAIVE (sin zona horaria) de
    HOY, sin asumir a que huso corresponde. La calibracion de huso se hace
    en una fase separada (calibrar_offset_horario), porque el curador no
    conoce de antemano el huso del proveedor de la lista.
    Soporta: HH:MM (24h), HH:MM AM/PM, "Aug 6 12:35 PM", y hora suelta sin
    minutos tipo "8am" / "8 AM" (comun en proveedores tipo PPV/US).
    """
    t = texto_original
    meses_en_map = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,
                     "aug":8,"sep":9,"oct":10,"nov":11,"dec":12}

    m = re.search(r'\b([A-Za-z]{3,})\s+(\d{1,2})\s+(\d{1,2}):(\d{2})\s*([AP]M)?\b', t, re.IGNORECASE)
    if m:
        mes_txt = m.group(1)[:3].lower()
        if mes_txt in meses_en_map:
            try:
                dia = int(m.group(2)); h = int(m.group(3)); mi = int(m.group(4))
                pm = m.group(5)
                if pm and pm.upper() == "PM" and h < 12: h += 12
                if pm and pm.upper() == "AM" and h == 12: h = 0
                return datetime.now().replace(month=meses_en_map[mes_txt], day=dia,
                                               hour=h, minute=mi, second=0, microsecond=0)
            except Exception:
                pass

    for m in re.finditer(r'\b(\d{1,2}):(\d{2})\b\s*([AaPp][Mm])?', t):
        try:
            h, mi = int(m.group(1)), int(m.group(2))
            ampm = m.group(3)
            if ampm and ampm.upper() == "PM" and h < 12: h += 12
            if ampm and ampm.upper() == "AM" and h == 12: h = 0
            if 0 <= h <= 23 and 0 <= mi <= 59:
                return datetime.now().replace(hour=h, minute=mi, second=0, microsecond=0)
        except Exception:
            continue

    # Hora suelta sin minutos: "8am", "8 AM", "10pm" (proveedores tipo PPV/US)
    for m in re.finditer(r'\b(\d{1,2})\s*([AaPp][Mm])\b', t):
        try:
            h = int(m.group(1))
            ampm = m.group(2).upper()
            if ampm == "PM" and h < 12: h += 12
            if ampm == "AM" and h == 12: h = 0
            if 0 <= h <= 23:
                return datetime.now().replace(hour=h, minute=0, second=0, microsecond=0)
        except Exception:
            continue

    return None

def extraer_enfrentamiento(texto: str):
    """
    Detecta patron de enfrentamiento binario: 'X vs Y', 'X vs. Y', 'X v Y',
    'X @ Y'. Devuelve (participante_a, participante_b) o (None, None).
    """
    patrones = [
        r'^(.*?)\s+vs\.?\s+(.*?)$',
        r'^(.*?)\s+@\s+(.*?)$',
        r'^(.*?)\s+\bv\b\s+(.*?)$',
    ]
    for patron in patrones:
        m = re.search(patron, texto, re.IGNORECASE)
        if m:
            a, b = m.group(1).strip(), m.group(2).strip()
            if len(a) > 1 and len(b) > 1:
                return a, b
    return None, None

def limpiar_ruido_canal(texto: str) -> str:
    t = texto
    for patron in RUIDO_CANAL:
        t = re.sub(patron, ' ', t, flags=re.IGNORECASE)
    # Separadores decorativos variados de proveedores -> pipe uniforme
    t = re.sub(r'[▫•‣●]+', ' | ', t)
    return t

def limpiar_texto_para_match(texto: str) -> str:
    if not texto:
        return ""
    t = normalizar_base(texto)
    t = re.sub(r'\(.*?\)', '', t)
    t = re.sub(r'\[.*?\]', '', t)
    t = re.sub(r'\b\d{1,2}:\d{2}\b(\s*[AP]M)?(\s*ET)?', '', t)
    t = re.sub(r'\b(HD|SD|FHD|4K|ENG|ESP|GER|OPC\.\d+|OP\d+|PEA\s*\d+|PARA\+\s*\d+|VIVO|LIVE|COMPACTO|REPETICION|RESUMEN|PPV)\b', '', t)
    t = re.sub(r"[^A-Z0-9\s]", " ", t)
    for corto, largo in TRADUCTOR_JERGA.items():
        t = re.sub(rf"\b{corto}\b", largo, t)
    palabras = [p for p in t.split() if len(p) > 2 and p not in
                ["THE","AND","DEL","LAS","LOS","VS","EN","EL","DE","LA"]]
    return " ".join(palabras)

def descomponer_canal(nombre_canal: str) -> dict:
    # Extraer hora original ANTES de manipular el texto
    hora_dt = extraer_hora_evento(nombre_canal)
    
    # 1. Aplanadora de símbolos: convertimos separadores comunes a |
    t = re.sub(r'[▫•‣●\-]+', '|', nombre_canal)
    
    # 2. Barredora de basura
    t = limpiar_ruido_canal(t)
    t = re.sub(r'\b\d{1,2}:\d{2}\b\s*([AaPp][Mm])?', ' ', t)
    t = re.sub(r'\b(\d{1,2})\s*([AaPp][Mm])\b', ' ', t)
    t = re.sub(r'\b[A-Za-z]{3,}\s+\d{1,2}\b', ' ', t)
    
    # Separar en segmentos reales
    segmentos_brutos = [s.strip() for s in t.split('|')]
    segmentos = [s for s in segmentos_brutos if len(s) > 1]
    
    tipo_evento = "sencillo"
    participante_a, participante_b = None, None
    torneo_contexto, subtitulo = "", ""
    
    idx_enfrentamiento = -1
    for i, seg in enumerate(segmentos):
        a, b = extraer_enfrentamiento(seg)
        if a and b:
            idx_enfrentamiento = i
            participante_a, participante_b = a, b
            tipo_evento = "duelo"
            break
            
    if tipo_evento == "duelo":
        resto = [s for i, s in enumerate(segmentos) if i != idx_enfrentamiento]
        # Nos quedamos con el primer segmento que contenga el nombre del torneo (ej: "Liga Betplay")
        torneo_contexto = resto[0] if resto else ""
    else:
        if segmentos:
            # En tenis es común que usen / para separar la cancha (ej: Cincinnati Open / Court 4)
            if '/' in segmentos[0]:
                partes = [p.strip() for p in segmentos[0].split('/', 1)]
                torneo_contexto = partes[0]
                subtitulo = partes[1] if len(partes) > 1 else ""
            else:
                torneo_contexto = segmentos[0]
                if len(segmentos) > 1:
                    subtitulo = segmentos[1]

    return {
        "hora_dt": hora_dt,
        "tipo_evento": tipo_evento,
        "torneo_display": torneo_contexto,
        "subtitulo_display": subtitulo,
        "equipo_local_display": participante_a,
        "equipo_visitante_display": participante_b,
        # Mantener los campos internos intactos para el motor de matching (Retrocompatibilidad)
        "participante_a": limpiar_texto_para_match(participante_a) if participante_a else None,
        "participante_b": limpiar_texto_para_match(participante_b) if participante_b else None,
        "contexto": limpiar_texto_para_match(torneo_contexto),
        "texto_completo": limpiar_texto_para_match(" ".join(segmentos))
    }
    """
    Extraccion universal, agnostica a proveedor/orden de campos.
    Estrategia por SEGMENTOS (13-ago-2026): el nombre del canal casi
    siempre trae sus partes separadas por pipes/bullets (hora | torneo |
    enfrentamiento | calidad, en CUALQUIER orden segun el proveedor). En
    vez de aplicar el patron "vs" sobre el texto completo (lo que hacia
    que el torneo quedara pegado al participante_a), se parte primero por
    segmentos y se identifica CUAL segmento contiene el enfrentamiento;
    el resto de segmentos no vacios se conserva como torneo/contexto real.
    Devuelve: hora_dt (naive, sin huso todavia), participante_a,
    participante_b, contexto (torneo real si se detecto), texto_completo.
    """
    texto = limpiar_ruido_canal(nombre_canal)
    hora_dt = extraer_hora_evento(texto)

    texto_sin_hora = re.sub(r'\b\d{1,2}:\d{2}\b\s*([AaPp][Mm])?', ' ', texto)
    texto_sin_hora = re.sub(r'\b(\d{1,2})\s*([AaPp][Mm])\b', ' ', texto_sin_hora)
    texto_sin_hora = re.sub(r'\b[A-Za-z]{3,}\s+\d{1,2}\b', ' ', texto_sin_hora)  # "Aug 6"

    segmentos = [s.strip(" -:") for s in texto_sin_hora.split('|')]
    segmentos = [s for s in segmentos if s]

    if len(segmentos) > 1:
        idx_enfrentamiento = None
        participante_a, participante_b = None, None
        for i, seg in enumerate(segmentos):
            a, b = extraer_enfrentamiento(seg)
            if a and b:
                idx_enfrentamiento = i
                participante_a, participante_b = a, b
                break
        if idx_enfrentamiento is not None:
            resto = [s for i, s in enumerate(segmentos) if i != idx_enfrentamiento]
            contexto = limpiar_texto_para_match(" ".join(resto))
            return {
                "hora_dt": hora_dt,
                "participante_a": limpiar_texto_para_match(participante_a),
                "participante_b": limpiar_texto_para_match(participante_b),
                "contexto": contexto,
                "texto_completo": limpiar_texto_para_match(" ".join(segmentos)),
            }
        # Ningun segmento individual tenia "vs": puede que el separador
        # de proveedor coincida justo donde estaba el "vs" (raro, pero
        # cubrir el caso). Fallback: intentar sobre el texto unido.
        texto_unido = " | ".join(segmentos)
        a, b = extraer_enfrentamiento(texto_unido)
        if a and b:
            resto = texto_unido.replace(a, '').replace(f"vs {b}", '').replace(f"vs. {b}", '').replace(b, '')
            return {
                "hora_dt": hora_dt,
                "participante_a": limpiar_texto_para_match(a),
                "participante_b": limpiar_texto_para_match(b),
                "contexto": limpiar_texto_para_match(resto),
                "texto_completo": limpiar_texto_para_match(texto_unido),
            }
        return {
            "hora_dt": hora_dt, "participante_a": None, "participante_b": None,
            "contexto": limpiar_texto_para_match(texto_unido),
            "texto_completo": limpiar_texto_para_match(texto_unido),
        }

    # Sin separadores reconocibles: proveedor plano, se procesa como bloque unico
    texto_sin_hora = re.sub(r'[|]+', ' | ', texto_sin_hora)
    participante_a, participante_b = extraer_enfrentamiento(texto_sin_hora)
    if participante_a and participante_b:
        resto = texto_sin_hora.replace(participante_a, '').replace(f"vs {participante_b}", '') \
                              .replace(f"vs. {participante_b}", '').replace(participante_b, '')
        contexto = limpiar_texto_para_match(resto)
        return {
            "hora_dt": hora_dt,
            "participante_a": limpiar_texto_para_match(participante_a),
            "participante_b": limpiar_texto_para_match(participante_b),
            "contexto": contexto,
            "texto_completo": limpiar_texto_para_match(texto_sin_hora),
        }
    return {
        "hora_dt": hora_dt, "participante_a": None, "participante_b": None,
        "contexto": limpiar_texto_para_match(texto_sin_hora),
        "texto_completo": limpiar_texto_para_match(texto_sin_hora),
    }

def buscar_evento_rescate_duplicado(resultados_finales, canal_info, titulo_base, hora_dt):
    tiene_participantes = canal_info["participante_a"] and canal_info["participante_b"]
    for item in resultados_finales:
        if not item["id"].startswith("rescate_"):
            continue
        if tiene_participantes and item.get("_participante_a") and item.get("_participante_b"):
            set_nuevo = {canal_info["participante_a"], canal_info["participante_b"]}
            set_existente = {item["_participante_a"], item["_participante_b"]}
            if set_nuevo != set_existente:
                continue
            if hora_dt and item.get("_hora_dt_rescate"):
                diff_min = abs((hora_dt - item["_hora_dt_rescate"]).total_seconds()) / 60.0
                if diff_min > 90:
                    continue
            return item
        elif not tiene_participantes and item["titulo"] == titulo_base:
            return item
    return None

def calcular_similitud_universal(canal_info: dict, evento_api: dict, ignorar_hora=False) -> float:
    """
    Combina proximidad horaria + similitud de texto.
    Si ignorar_hora=True, se usa solo la señal de texto (util para la
    calibracion de huso, donde todavia no sabemos convertir la hora).
    """
    puntaje = 0.0

    if not ignorar_hora and canal_info["hora_dt"] and evento_api.get("hora_utc"):
        try:
            hora_evento_api = datetime.strptime(evento_api["hora_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            hora_canal_utc = canal_info["hora_dt"]
            if hora_canal_utc.tzinfo is None:
                hora_canal_utc = hora_canal_utc.replace(tzinfo=timezone.utc)
            diff_min = abs((hora_evento_api - hora_canal_utc.astimezone(timezone.utc)).total_seconds()) / 60.0
            # Colchon ajustado (13-ago-2026): un partido real puede
            # arrancar unos minutos tarde, pero 2-4 horas de diferencia
            # ya no es el mismo evento. Ensanchar esto generaria falsos
            # positivos, no resuelve nada.
            if diff_min <= 15: puntaje += 40
            elif diff_min <= 30: puntaje += 25
            elif diff_min <= 45: puntaje += 10
        except Exception:
            pass

    firma_api = evento_api.get("firma_texto", "")
    if canal_info["participante_a"] and canal_info["participante_b"]:
        words_a = set(canal_info["participante_a"].split())
        words_b = set(canal_info["participante_b"].split())
        words_api = set(firma_api.split())
        comunes_a = len(words_a.intersection(words_api))
        comunes_b = len(words_b.intersection(words_api))
        if comunes_a >= 1 and comunes_b >= 1:
            puntaje += 45
        elif comunes_a >= 1 or comunes_b >= 1:
            puntaje += 20
        ratio_txt = SequenceMatcher(None, canal_info["texto_completo"], firma_api).ratio() * 100
        puntaje += ratio_txt * 0.15
    else:
        ratio_txt = SequenceMatcher(None, canal_info["contexto"], firma_api).ratio() * 100
        puntaje += ratio_txt * 0.6

    return min(puntaje, 100.0)

def calibrar_offset_horario(canales_info: list, agenda_api: list) -> int:
    """
    El curador es universal: no sabe en que huso horario viene la hora de
    la lista del proveedor. En vez de asumir un huso fijo, se calibra
    AUTOMATICAMENTE por corrida: se buscan canales con enfrentamiento
    claro (participante_a/b) cuyo texto coincide fuerte con un evento de
    la Agenda Maestra (independiente de la hora), y se calcula que
    desplazamiento horario entero (en horas) hace que la hora del canal
    coincida con la hora real UTC de ese evento. El offset mas frecuente
    entre todos los matches de alta confianza se adopta para TODA la
    corrida. Si no hay suficientes matches de referencia, se usa -5
    (Colombia) como ultimo recurso razonable.
    """
    votos = []
    for canal_info in canales_info:
        if not (canal_info["participante_a"] and canal_info["participante_b"] and canal_info["hora_dt"]):
            continue
        wa = set(canal_info["participante_a"].split())
        wb = set(canal_info["participante_b"].split())
        mejor_score_texto = 0.0
        mejor_evento = None
        for ev in agenda_api:
            wapi = set(ev.get("firma_texto", "").split())
            if len(wa & wapi) >= 1 and len(wb & wapi) >= 1:
                score = calcular_similitud_universal(canal_info, ev, ignorar_hora=True)
                if score > mejor_score_texto:
                    mejor_score_texto = score
                    mejor_evento = ev
        if mejor_evento is None or mejor_score_texto < 60.0:
            continue
        try:
            hora_evento_utc = datetime.strptime(mejor_evento["hora_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        hora_canal_naive = canal_info["hora_dt"]
        mejor_offset, mejor_diff = None, 999999
        for off in range(-12, 15):
            candidata_utc = hora_canal_naive.replace(tzinfo=timezone(timedelta(hours=off))).astimezone(timezone.utc)
            diff = abs((hora_evento_utc - candidata_utc).total_seconds()) / 60.0
            if diff < mejor_diff:
                mejor_diff, mejor_offset = diff, off
        if mejor_diff <= 20:
            votos.append(mejor_offset)

    if votos:
        offset_final = Counter(votos).most_common(1)[0][0]
        log.info(f"Huso horario de la lista calibrado automaticamente: UTC{offset_final:+d} "
                 f"(basado en {len(votos)} eventos de referencia de alta confianza).")
        return offset_final

    log.warning("No hubo suficientes eventos de referencia para calibrar el huso horario. "
                "Se usa UTC-5 (Colombia) como ultimo recurso.")
    return -5

def adivinar_categoria_y_logo(texto: str):
    texto_upper = normalizar_base(texto)
    for categoria, keywords in CATEGORIAS_RESCATE.items():
        if any(kw in texto_upper for kw in keywords):
            return categoria, LOGOS_RESCATE.get(categoria, LOGOS_RESCATE["Deportes"])
    return "Deportes", LOGOS_RESCATE.get("Deportes", "")

def crear_id_seguro(titulo: str) -> str:
    import hashlib
    hash_obj = hashlib.md5(titulo.encode('utf-8'))
    return hash_obj.hexdigest()[:12]

def reasignar_categorias_dinamicas(resultados_finales: list):
    """
    Categorias dinamicas por volumen (13-ago-2026): el deporte que hoy
    tenga pocos eventos no merece su propia seccion vacia/pobre en la
    app. Se recalcula CADA corrida: categorias con menos de
    MIN_EVENTOS_POR_CATEGORIA eventos se agrupan en "Otros Deportes".
    """
    conteo = Counter(ev["categoria"] for ev in resultados_finales)
    for ev in resultados_finales:
        if conteo[ev["categoria"]] < MIN_EVENTOS_POR_CATEGORIA:
            ev["categoria"] = CATEGORIA_AGRUPADORA
            if not ev.get("logo_local"):
                ev["logo_local"] = LOGOS_RESCATE[CATEGORIA_AGRUPADORA]

# ─── THESPORTSDB: AGENDA MAESTRA ────────────────────────────────────────────

def obtener_agenda_maestra() -> list:
    if os.path.exists(ARCHIVO_CACHE):
        tiempo_modificacion = os.path.getmtime(ARCHIVO_CACHE)
        if (time.time() - tiempo_modificacion) < (HORAS_CACHE * 3600):
            try:
                with open(ARCHIVO_CACHE, "r", encoding="utf-8") as f:
                    agenda = json.load(f)
                if agenda:
                    return agenda
            except Exception:
                pass

    log.info("Descargando Agenda Maestra de TheSportsDB...")
    eventos_api = []

    for deporte_es, deporte_api in DEPORTES_MAP.items():
        log.info(f"Consultando agenda de {deporte_es}...")
        url = f"{THESPORTSDB_BASE}/eventsday.php"
        params = {"d": FECHA_HOY, "s": deporte_api}
        try:
            time.sleep(1.2)
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 429:
                log.warning(f"Rate limit alcanzado en {deporte_es}, esperando 5s...")
                time.sleep(5)
                r = requests.get(url, params=params, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json().get("events") or []
        except requests.exceptions.RequestException as e:
            log.warning(f"Error consultando {deporte_es}: {e}")
            continue

        for ev in data:
            try:
                fecha_str = ev.get("dateEvent")
                hora_str = ev.get("strTime") or "00:00:00"
                if not fecha_str:
                    continue
                dt_naive = datetime.strptime(f"{fecha_str} {hora_str[:8]}", "%Y-%m-%d %H:%M:%S")
                dt_utc = dt_naive.replace(tzinfo=timezone.utc)

                torneo = ev.get("strLeague", "") or ""
                eq_local = ev.get("strHomeTeam", "") or ""
                eq_visit = ev.get("strAwayTeam", "") or ""

                if eq_local and eq_visit:
                    titulo = f"{eq_local} vs {eq_visit}"
                else:
                    titulo = ev.get("strEvent", "") or ev.get("strFilename", "") or torneo

                firma_texto = limpiar_texto_para_match(f"{torneo} {titulo}")

                eventos_api.append({
                    "id": str(ev.get("idEvent")),
                    "titulo": titulo, "torneo": torneo, "categoria": deporte_es,
                    "firma_texto": firma_texto,
                    "hora_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "duracion_min": DURACION_POR_DEPORTE.get(deporte_es, 150),
                    "logo_torneo": ev.get("strLeagueBadge", "") or ev.get("strBadge", "") or "",
                    "logo_local": ev.get("strHomeTeamBadge", "") or ev.get("strThumb", "") or "",
                    "logo_visitante": ev.get("strAwayTeamBadge", "") or "",
                    "tier": 2,
                })
            except Exception:
                continue

    if eventos_api:
        with open(ARCHIVO_CACHE, "w", encoding="utf-8") as f:
            json.dump(eventos_api, f, ensure_ascii=False, indent=2)
    return eventos_api

# ─── XTREAM ──────────────────────────────────────────────────────────────

def procesar_cubo_a() -> list:
    log.info("Analizando servidor Xtream a través del puente...")
    base_url = XTREAM_URL.rstrip('/')
    api_url = f"{base_url}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
    cubo_a = []

    fechas_hoy = obtener_variaciones_fecha_hoy()
    palabras_principales = ["HOY", "TODAY", "DAILY", "DIARIO", "DIA", "VIVO", "LIVE", "EVENTS", "EVENTOS"]
    palabras_contexto = ["EVENTOS", "EVENTS", "AGENDA", "PARTIDOS", "CARTELERA", "CALENDARIO", "PPV", "SPORT"]

    try:
        url_cat_original = f"{api_url}&action=get_live_categories"
        r_cat = requests.get(PUENTE_URL, params={"url": url_cat_original}, timeout=45)
        categorias_hoy_ids = set()

        if r_cat.status_code == 200:
            for cat in r_cat.json():
                nombre_cat = cat.get("category_name", "")
                nombre_limpio = limpiar_nombre_categoria(nombre_cat)
                match_fecha_directa = any(f in nombre_limpio for f in fechas_hoy)
                tiene_principal = any(p in nombre_limpio for p in palabras_principales)
                tiene_contexto = any(c in nombre_limpio for c in palabras_contexto)
                if match_fecha_directa or (tiene_principal and tiene_contexto):
                    categorias_hoy_ids.add(str(cat.get("category_id")))
            log.info(f"Categorias candidatas a 'hoy' detectadas: {len(categorias_hoy_ids)}")
        else:
            log.warning(f"get_live_categories devolvio HTTP {r_cat.status_code}, se continua sin filtro de categoria.")

        url_streams = f"{api_url}&action=get_live_streams"
        r_str = requests.get(PUENTE_URL, params={"url": url_streams}, timeout=60)

        if r_str.status_code != 200:
            log.error(f"get_live_streams devolvio HTTP {r_str.status_code}. Abortando cubo_a.")
            return []

        streams_totales = r_str.json()
        log.info(f"Total streams live recibidos del panel (sin filtrar): {len(streams_totales)}")

        vistos = set()
        for s in streams_totales:
            stream_id = str(s.get("stream_id"))
            if stream_id in vistos:
                continue
            vistos.add(stream_id)

            nombre_canal = s.get("name", "").strip()
            if not nombre_canal:
                continue

            cat_id_stream = str(s.get("category_id"))
            pertenece_a_categoria_hoy = (not categorias_hoy_ids) or (cat_id_stream in categorias_hoy_ids)
            tiene_hora = extraer_hora_evento(nombre_canal) is not None
            tiene_kw_evento = any(kw in nombre_canal.upper() for kw in ["PPV", "LIVE EVENT", "PEA ", "PARA+"])

            if not categorias_hoy_ids:
                match_fecha = re.search(r'\b(\d{1,2})[-/](\d{1,2})\b', nombre_canal)
                if match_fecha:
                    d_str, m_str = match_fecha.group(1), match_fecha.group(2)
                    if not any(f in f"{d_str}/{m_str}" or f in f"{m_str}/{d_str}" for f in fechas_hoy):
                        continue

            if pertenece_a_categoria_hoy and (tiene_hora or tiene_kw_evento):
                cubo_a.append({"id_xtream": stream_id, "nombre_ui": nombre_canal})

        log.info(f"Eventos candidatos detectados en cubo_a: {len(cubo_a)}")
        return cubo_a
    except Exception as e:
        log.error(f"Error procesando Xtream: {e}")
        return []

# ─── RESOLUCIÓN DE HOST DE MEDIA ────────────────────────────────────────────

def detectar_base_media_m3u() -> str:
    """
    El host de login (XTREAM_URL) no siempre coincide con el host real
    donde se sirven los .ts. Se toma una muestra del M3U (que ya trae
    URLs completas con el host real) para detectarlo, igual que ya se
    hace de forma probada en el curador de VOD.
    """
    base_api = XTREAM_URL.rstrip("/")
    url_m3u = f"{base_api}/get.php?username={XTREAM_USER}&password={XTREAM_PASS}&type=m3u&output=ts"
    candidatos = []
    try:
        with requests.get(url_m3u, headers={"Range": "bytes=0-65535"}, stream=True, timeout=(15, 30)) as respuesta:
            if respuesta.status_code not in (200, 206):
                log.warning(f"No se pudo leer la muestra M3U: HTTP {respuesta.status_code}. Se conserva XTREAM_URL.")
                return base_api
            for linea_bytes in respuesta.iter_lines(chunk_size=8192):
                linea = linea_bytes.decode("utf-8", errors="ignore").strip()
                if not linea.startswith(("http://", "https://")):
                    continue
                parsed = urlparse(linea)
                partes = [p for p in parsed.path.split("/") if p]
                if len(partes) < 3:
                    continue
                usuario, password = partes[-3], partes[-2]
                stream_id = partes[-1].split("?")[0]
                if usuario == XTREAM_USER and password == XTREAM_PASS and stream_id:
                    candidatos.append(f"{parsed.scheme}://{parsed.netloc}")
                if len(candidatos) >= 3:
                    break

        if len(candidatos) >= 3 and len(set(candidatos)) == 1:
            base_media = candidatos[0]
            if base_media != base_api:
                log.info(f"Base de media detectada desde M3U: {base_media}")
            else:
                log.info("El M3U confirma XTREAM_URL como base de media.")
            return base_media

        log.warning("No se obtuvo una muestra M3U consistente. Se conserva XTREAM_URL como base de media.")
        return base_api
    except requests.exceptions.RequestException as e:
        log.warning(f"No se pudo detectar la base de media desde M3U: {e}. Se conserva XTREAM_URL.")
        return base_api

# ─── ORQUESTADOR ────────────────────────────────────────────────────────────

def main():
    log.info("=== Iniciando Curador Base (TheSportsDB + Xtream) ===")
    agenda_api = obtener_agenda_maestra()
    cubo_a = procesar_cubo_a()

    if not cubo_a:
        log.warning("No se detectaron eventos temporales en Xtream para procesar.")
        return

    base_media = detectar_base_media_m3u()

    canales_info = [descomponer_canal(c["nombre_ui"]) for c in cubo_a]

    offset_horario = -5
    if agenda_api:
        offset_horario = calibrar_offset_horario(canales_info, agenda_api)

    for info in canales_info:
        if info["hora_dt"] is not None:
            info["hora_dt"] = info["hora_dt"].replace(tzinfo=timezone(timedelta(hours=offset_horario)))

    resultados_finales = []

    for canal, canal_info in zip(cubo_a, canales_info):
        fuente_limpia = {"nombre": canal["nombre_ui"], "id_xtream": canal["id_xtream"]}

        match_encontrado = False
        mejor_evento, mejor_puntaje = None, 0.0

        if agenda_api:
            for ev in agenda_api:
                puntaje = calcular_similitud_universal(canal_info, ev)
                if puntaje > mejor_puntaje:
                    mejor_puntaje, mejor_evento = puntaje, ev

        if mejor_puntaje > UMBRAL_FUSION and mejor_evento:
            match_encontrado = True
            evento_existente = next((item for item in resultados_finales if item["id"] == mejor_evento["id"]), None)
            if evento_existente:
                if not any(f["id_xtream"] == fuente_limpia["id_xtream"] for f in evento_existente["fuentes"]):
                    evento_existente["fuentes"].append(fuente_limpia)
            else:
                evento_clon = mejor_evento.copy()
                evento_clon["fuentes"] = [fuente_limpia]
                evento_clon.pop("firma_texto", None)
                
                # Inyectar las nuevas cajas estructuradas
                evento_clon["tipo_evento"] = canal_info["tipo_evento"]
                evento_clon["subtitulo"] = canal_info["subtitulo_display"].title() if canal_info["subtitulo_display"] else ""
                evento_clon["equipo_local"] = canal_info["equipo_local_display"].title() if canal_info["equipo_local_display"] else ""
                evento_clon["equipo_visitante"] = canal_info["equipo_visitante_display"].title() if canal_info["equipo_visitante_display"] else ""
                
                # Ajustar el título para que sea limpio (Y por retrocompatibilidad si la app no lee cajas aún)
                if canal_info["tipo_evento"] == "duelo":
                    evento_clon["titulo"] = f"{evento_clon['equipo_local']} vs {evento_clon['equipo_visitante']}"
                else:
                    evento_clon["titulo"] = canal_info["torneo_display"].title() if canal_info["torneo_display"] else mejor_evento.get("titulo", "")
                
                resultados_finales.append(evento_clon)

        if not match_encontrado:
            if canal_info["tipo_evento"] == "duelo":
                titulo_base = f"{canal_info['equipo_local_display'].title()} Vs {canal_info['equipo_visitante_display'].title()}"
            else:
                titulo_base = canal_info["torneo_display"].title() if canal_info["torneo_display"] else "Evento Deportivo"

            if not titulo_base:
                continue

            hora_dt_rescate = canal_info["hora_dt"] or datetime.now(timezone(timedelta(hours=offset_horario)))
            hora_dt_rescate_utc = hora_dt_rescate.astimezone(timezone.utc)

            evento_existente = buscar_evento_rescate_duplicado(resultados_finales, canal_info, titulo_base, hora_dt_rescate_utc)

            if evento_existente:
                if not any(f["id_xtream"] == fuente_limpia["id_xtream"] for f in evento_existente["fuentes"]):
                    evento_existente["fuentes"].append(fuente_limpia)
            else:
                categoria_adivinada, logo_adivinado = adivinar_categoria_y_logo(canal["nombre_ui"])
                torneo_final = canal_info["torneo_display"].title() if canal_info["torneo_display"] else categoria_adivinada
                logo_torneo_final = logo_adivinado

                if mejor_evento and mejor_puntaje >= UMBRAL_PRESTAMO:
                    logo_adivinado = mejor_evento.get("logo_local") or logo_adivinado
                    logo_torneo_final = mejor_evento.get("logo_torneo") or logo_adivinado
                    if mejor_evento.get("torneo"):
                        torneo_final = mejor_evento["torneo"]

                hora_utc_calculada = hora_dt_rescate_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

                resultados_finales.append({
                    "id": f"rescate_{crear_id_seguro(titulo_base)}",
                    "titulo": titulo_base,
                    "tipo_evento": canal_info["tipo_evento"],
                    "torneo": torneo_final,
                    "subtitulo": canal_info["subtitulo_display"].title() if canal_info["subtitulo_display"] else "",
                    "equipo_local": canal_info["equipo_local_display"].title() if canal_info["equipo_local_display"] else "",
                    "equipo_visitante": canal_info["equipo_visitante_display"].title() if canal_info["equipo_visitante_display"] else "",
                    "logo_torneo": logo_torneo_final,
                    "categoria": categoria_adivinada,
                    "hora_utc": hora_utc_calculada,
                    "duracion_min": DURACION_POR_DEPORTE.get(categoria_adivinada, 240),
                    "logo_local": logo_adivinado,
                    "logo_visitante": "",
                    "banner": logo_adivinado,
                    "tier": 2,
                    "fuentes": [fuente_limpia],
                    "_participante_a": canal_info["participante_a"],
                    "_participante_b": canal_info["participante_b"],
                    "_hora_dt_rescate": hora_dt_rescate_utc,
                })

    reasignar_categorias_dinamicas(resultados_finales)

    resultados_finales.sort(key=lambda x: x["hora_utc"])

    for ev in resultados_finales:
        ev.pop("_participante_a", None)
        ev.pop("_participante_b", None)
        ev.pop("_hora_dt_rescate", None)

    salida = {
        "generado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base_media": base_media,
        "eventos": resultados_finales,
    }

    try:
        with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as f:
            json.dump(salida, f, ensure_ascii=False, indent=2)
        log.info(f"¡Proceso Terminado! Total de eventos listos: {len(resultados_finales)}")
    except Exception as e:
        log.error(f"Error crítico guardando el archivo JSON: {e}")

if __name__ == "__main__":
    main()
