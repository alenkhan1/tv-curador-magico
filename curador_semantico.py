# -*- coding: utf-8 -*-
"""
Post-Procesador y Normalizador Semántico Deportivo:
Ejecuta la Fase 2 de Curación Inteligente sobre todos los eventos del catálogo:
1. Deduce la categoría real de eventos huérfanos ('Asobal' -> 'Balonmano', 'Shenzhen' -> 'Snooker', 'China Open' -> 'Tenis').
2. Desmonta y limpia prefijos de jornadas ('Jornada 4: Ciudad Real - Ademar León' -> 'Ciudad Real vs Ademar León').
3. Deduplicación inteligente por entidad deportiva (equipos normalizados + hora):
   - Fusiona 'CD Eldense vs Real Oviedo' y 'Eldense vs Real Oviedo' en 1 sola tarjeta con múltiples fuentes.
   - Fusiona 'Real Avilés vs CD Lugo' y 'Avilés vs Lugo'.
   - Fusiona 'Joventut Badalona vs Unicaja' y 'Liga U: Joventut vs Unicaja'.
4. Genera subtítulos deportivos limpios y acordes a EventoCard.kt:
   - Para duelos: Torneo oficial.
   - Para circuitos: Nombre institucional + ronda/sede (cero nombres individuales en el botón).
5. Resuelve escudos, banderas oficiales y logos de torneos.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from typing import Any, Dict, List, Tuple

from resolvedor_logos import (
    resolver_logo_equipo,
    resolver_logo_torneo,
    BANDERAS_PAISES,
    _normalizar,
)

log = logging.getLogger("curador_semantico")

DEDUCCIONES_DEPORTE = [
    ("Balonmano", ["ASOBAL", "BALONMANO", "HANDBALL", "EHF"]),
    ("Baloncesto", ["ACB", "LIGA ENDESA", "EUROLEAGUE", "EUROLIGA", "NBA", "WNBA", "BASKET", "BALONCESTO", "NCAA BASKET", "CALVARY", "NEXT LEVEL", "BRISTOL FLYERS", "LONDON LIONS", "NEWCASTLE EAGLES", "BIG BLUE MADNESS"]),
    ("Snooker", ["SNOOKER", "BILLAR", "SHENZHEN"]),
    ("Ciclismo", ["CICLISMO", "CYCLING", "VUELTA", "GIRO", "TOUR DE FRANCE", "CROSS COUNTRY", "LAKE PLACID"]),
    ("Pádel", ["PADEL", "PÁDEL", "PREMIER PADEL", "FIP"]),
    ("Tenis", ["ATP", "WTA", "TENIS", "TENNIS", "CHALLENGER", "ROLAND GARROS", "WIMBLEDON", "US OPEN", "CHINA OPEN", "DAVIS", "JAPAN OPEN"]),
    ("Motor", ["F1", "FORMULA 1", "FÓRMULA 1", "MOTOGP", "MOTO GP", "WRC", "RALLY", "NASCAR", "INDYCAR", "MOTOR"]),
    ("Combate", ["UFC", "BOXEO", "BOXING", "COMBATE", "MMA", "BELLATOR", "BKFC", "ONE FRIDAY", "ONE CHAMPIONSHIP", "FIGHT NIGHT", "SMACKDOWN", "WWE"]),
    ("Béisbol", ["MLB", "BEISBOL", "BÉISBOL", "BASEBALL"]),
    ("Fútbol Americano", ["NFL", "FUTBOL AMERICANO", "NCAA FOOTBALL", "CFL", "SPRINT LEAGUE", "F AMERICANO", "F AUSTRALIANO"]),
    ("Hockey", ["NHL", "HOCKEY"]),
    ("Rugby", ["RUGBY", "U20 RUGBY", "TOP 14", "SIX NATIONS", "SOUTHLAND", "NORTHLAND"]),
    ("Golf", ["GOLF", "PGA", "DP WORLD", "LIV GOLF", "DUNHILL", "BANK OF UTAH"]),
    ("Polo", ["POLO", "PALERMO", "LA IRENITA", "VELAY", "ELLERSTINA", "DOLFINA"]),
    ("Natación", ["NATACION", "NATACIÓN", "AQUATICS", "WATERPOLO"]),
    ("Hípica", ["HIPICA", "HÍPICA", "GRAN PREMIO CIUTAT DE BARCELONA", "CSIO BARCELONA", "EQUESTRIAN"]),
    ("Atletismo", ["ATLETISMO", "ATHLETICS"]),
    ("Tiro", ["MALAKASA", "TIRO AL PLATO", "SHOOTING"]),
    ("Fútbol", [
        "LALIGA", "PREMIER LEAGUE", "SERIE A", "BUNDESLIGA", "LIGUE 1", "NATIONS LEAGUE",
        "LIBERTADORES", "SUDAMERICANA", "BETPLAY", "FUTBOL", "FÚTBOL", "COPA ARGENTINA",
        "LIGA MX", "NWSL", "NSL", "CHILE", "ARG", "BOCA JUNIORS", "SANTOS", "UNION",
        "SOACHA", "INDEPENDIENTE", "RIVADAVIA", "GIMNASIA", "DEPORTIVO CALI", "ALIANZA",
        "SMARTBANK", "BRASILEIRAO", "BRASILEIRÃO", "CONCACAF", "SOCCER", "EXPANSION MX",
        "CORRECAMINOS", "CRUZ AZUL", "TEPATITLAN", "TAMPICO", "VENADOS", "AMERICA", "USL"
    ]),
]

PREFIJOS_RONDA = re.compile(
    r"^(?:Jornada\s+\d+|Fase\s+de\s+grupos|Cuartos\s+de\s+final.*?|Semifinal.*?|Final|Round\s+\d+|2ª\s+Ronda.*?|1ª\s+Ronda.*?):\s*",
    re.I
)

def limpiar_texto(s: str) -> str:
    if not s:
        return ""
    desescapado = html_lib.unescape(s)
    desescapado = desescapado.replace("\x96", "-").replace("\u2013", "-").replace("\u2014", "-")
    return " ".join(desescapado.split()).strip()

SINONIMOS_EQUIPOS = {
    "KAZAKHSTAN": "KAZAJISTAN",
    "MOLDOVA": "MOLDAVIA",
    "CYPRUS": "CHIPRE",
    "BELARUS": "BIELORRUSIA",
    "NORTHERN IRELAND": "IRLANDA DEL NORTE",
    "IRELAND": "IRLANDA",
    "SCOTLAND": "ESCOCIA",
    "WALES": "GALES",
    "NETHERLANDS": "PAISES BAJOS",
    "GERMANY": "ALEMANIA",
    "FRANCE": "FRANCIA",
    "SPAIN": "ESPANA",
    "ITALY": "ITALIA",
    "SWITZERLAND": "SUIZA",
    "SWEDEN": "SUECIA",
    "NORWAY": "NORUEGA",
    "DENMARK": "DINAMARCA",
    "POLAND": "POLONIA",
    "CZECH REPUBLIC": "REPUBLICA CHECA",
    "CZECHIA": "REPUBLICA CHECA",
    "CROATIA": "CROACIA",
    "GREECE": "GRECIA",
    "TURKEY": "TURQUIA",
    "BELGIUM": "BELGICA",
    "AUSTRIA": "AUSTRIA",
    "HUNGARY": "HUNGRIA",
    "ROMANIA": "RUMANIA",
    "BULGARIA": "BULGARIA",
    "ICELAND": "ISLANDIA",
    "LITHUANIA": "LITUANIA",
    "LATVIA": "LETONIA",
    "ESTONIA": "ESTONIA",
    "SLOVAKIA": "ESLOVAQUIA",
    "SLOVENIA": "ESLOVENIA",
    "ALBANIA": "ALBANIA",
    "BOSNIA": "BOSNIA",
    "AZERBAIJAN": "AZERBAIYAN",
    "GEORGIA": "GEORGIA",
    "FINLAND": "FINLANDIA",
    "LA PLATA": "LP",
    "SANTA FE": "SF",
}

def normalizar_nombre_equipo(nombre: str) -> str:
    """Remueve prefijos, sufijos y sinonimia de clubes/paises para emparejamiento estricto."""
    s = _normalizar(nombre)
    for k, v in SINONIMOS_EQUIPOS.items():
        s = re.sub(rf"\b{k}\b", v, s)
    s = re.sub(r"\b(CD|CF|FC|SD|UD|AD|CA|CSD|REAL|ATLETICO|ATL|DEPORTIVO|DEP|CLUB|DEPORTES|FUTBOL CLUB|SAD|BALOMPIE|BADALONA|CUNDINAMARCA|DE BOGOTA)\b", "", s)
    s = re.sub(r"\b(DE|DEL|LA|LAS|LOS|EL)\b", "", s)
    s = re.sub(r"\bUNIV\.?\b|\bU\.?\b", "UNIVERSIDAD", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split()).strip()

def deducir_categoria(titulo: str, torneo: str, cat_actual: str) -> str:
    texto = _normalizar(f"{titulo} {torneo}")
    texto_padded = f" {texto} "
    for deporte, keywords in DEDUCCIONES_DEPORTE:
        for kw in keywords:
            kw_norm = _normalizar(kw)
            if f" {kw_norm} " in texto_padded:
                return deporte
    return cat_actual if cat_actual and cat_actual != "Otros Deportes" else "Otros Deportes"

def desmontar_duelo(titulo: str, categoria: str) -> Tuple[str, str, str, str, str]:
    """
    Desmonta de forma universal enfrentamientos deportivos con ligas, rondas o jornadas.
    Soporta todos los separadores Unicode (-, –, —, vs, v).
    Retorna: (tit_limpio, loc, vis, ronda, torneo_extraido)
    """
    tit = limpiar_texto(titulo)
    ronda = ""
    torneo_extraido = ""

    partes = re.split(r"\s+(?:vs\.?|v\.?|-)\s+", tit, flags=re.I)
    if len(partes) == 2:
        p0, vis = partes[0].strip(), partes[1].strip()
        m_ronda = re.search(r"\b(\d+[ªºa]?\s+Jornada|Jornada\s+\d+|Fase\s+de\s+grupos|Fecha\s+\d+|Round\s+\d+|Cuartos\s+de\s+final|Semifinal(?:es)?|Final)\b[:\s]*", p0, re.I)
        if m_ronda:
            torneo_pref = p0[:m_ronda.start()].strip()
            ronda = m_ronda.group(1).strip()
            loc = p0[m_ronda.end():].strip()
            torneo_limpio = re.sub(r"^(?:Fútbol\s+(?:Sala\s+)?|Baloncesto\s+|Balonmano\s+)", "", torneo_pref, flags=re.I).strip()
            return f"{loc} vs {vis}", loc, vis, ronda, torneo_limpio or torneo_pref
        elif categoria in ["Fútbol", "Baloncesto", "Balonmano", "Pádel", "Tenis", "Béisbol", "Fútbol Americano", "Rugby", "Polo", "Hockey"]:
            loc = limpiar_texto(p0)
            vis_limpio = limpiar_texto(vis)
            return f"{loc} vs {vis_limpio}", loc, vis_limpio, ronda, ""

    return tit, "", "", ronda, ""

def _minutos_utc(iso_utc: str) -> int:
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        return int(dt.timestamp() // 60)
    except Exception:
        return 0

def son_mismo_evento(ev1: Dict[str, Any], ev2: Dict[str, Any]) -> bool:
    """Determina si dos eventos corresponden a la misma cita deportiva."""
    t1 = _minutos_utc(ev1.get("hora_utc", ""))
    t2 = _minutos_utc(ev2.get("hora_utc", ""))
    diff_min = abs(t1 - t2)

    loc1, vis1 = ev1.get("equipo_local", ""), ev1.get("equipo_visitante", "")
    loc2, vis2 = ev2.get("equipo_local", ""), ev2.get("equipo_visitante", "")

    if loc1 and vis1 and loc2 and vis2:
        l1_n = normalizar_nombre_equipo(loc1)
        v1_n = normalizar_nombre_equipo(vis1)
        l2_n = normalizar_nombre_equipo(loc2)
        v2_n = normalizar_nombre_equipo(vis2)

        # Si los dos equipos coinciden exactamente, toleramos hasta 180 min de desfase
        # para absorber discrepancias de huso horario entre feeds (ej. Arg UTC-3 vs Col UTC-5 o previas)
        if ((l1_n == l2_n and v1_n == v2_n) or (l1_n == v2_n and v1_n == l2_n)) and diff_min <= 180:
            return True
        if l1_n and l2_n and v1_n and v2_n and diff_min <= 90:
            if (l1_n in l2_n or l2_n in l1_n) and (v1_n in v2_n or v2_n in v1_n):
                return True
            # Tolerancia para transliteraciones árabes/asiáticas (ej. Al Quadisiya vs Al Qadsiah)
            from difflib import SequenceMatcher
            sim_loc = SequenceMatcher(None, l1_n, l2_n).ratio()
            sim_vis = SequenceMatcher(None, v1_n, v2_n).ratio()
            if (sim_loc >= 0.85 and sim_vis >= 0.65) or (sim_loc >= 0.65 and sim_vis >= 0.85):
                return True

    if diff_min > 90:
        return False

    # Coincidencia por título y categoría
    tit1 = _normalizar(ev1.get("titulo", ""))
    tit2 = _normalizar(ev2.get("titulo", ""))
    cat1 = _normalizar(ev1.get("categoria", ""))
    cat2 = _normalizar(ev2.get("categoria", ""))

    es_circuito = (
        ev1.get("tipo_evento") == "circuito" or ev2.get("tipo_evento") == "circuito" or
        cat1 in ["TENIS", "MOTOR", "COMBATE", "BOXEO", "MMA", "GOLF", "CICLISMO", "PADEL"] or
        cat2 in ["TENIS", "MOTOR", "COMBATE", "BOXEO", "MMA", "GOLF", "CICLISMO", "PADEL"]
    )

    if es_circuito:
        # REGLA SAGRADA DE CIRCUITOS Y TENIS:
        # NUNCA fusionar canchas distintas (Stadium Court vs Show Court 3 vs Court 4).
        # Cada cancha activa en cada horario es una tarjeta independiente con su botón.
        ref1 = _normalizar(ev1.get("subtitulo", "") or ev1.get("referencia", ""))
        ref2 = _normalizar(ev2.get("subtitulo", "") or ev2.get("referencia", ""))
        pat_cancha = r"\b(STADIUM|CENTRE|CENTER|COURT\s*\d+|PISTA\s*\d+|CANCHA\s*\d+|GRANDSTAND|SHOW\s*COURT)\b"
        cancha1 = re.search(pat_cancha, f"{tit1} {ref1}")
        cancha2 = re.search(pat_cancha, f"{tit2} {ref2}")
        if cancha1 and cancha2 and cancha1.group(0) != cancha2.group(0):
            return False

        # Solo fusionar si tienen idéntico título y horario muy cercano
        if tit1 == tit2 and diff_min <= 60:
            return True
        return False

    if tit1 == tit2:
        return True

    if cat1 and cat2 and cat1 == cat2:
        palabras1 = set(tit1.split())
        palabras2 = set(tit2.split())
        inter = palabras1.intersection(palabras2)
        if len(inter) >= 2 and any(k in tit1 for k in ["OPEN", "PRIX", "FIGHT", "NIGHT", "UFC", "F1", "TOUR", "CUP"]):
            return True

    return False

def post_procesar_y_curar_eventos(eventos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    eventos_pre = []
    for ev in eventos:
        tit_original = limpiar_texto(ev.get("titulo", ""))
        torneo_original = limpiar_texto(ev.get("torneo", ""))
        cat_original = ev.get("categoria", "")

        categoria_real = deducir_categoria(tit_original, torneo_original, cat_original)
        tit_limpio, loc, vis, ronda, torneo_extra = desmontar_duelo(tit_original, categoria_real)
        if torneo_extra and (not torneo_original or torneo_original == tit_original or torneo_original.lower() in ["deportes", "deportes en vivo", "fútbol", "futbol"]):
            torneo_original = torneo_extra

        if not loc and ev.get("equipo_local"):
            loc = limpiar_texto(ev.get("equipo_local", ""))
        if not vis and ev.get("equipo_visitante"):
            vis = limpiar_texto(ev.get("equipo_visitante", ""))

        DEPORTES_CIRCUITO = {
            "Tenis", "Golf", "MMA", "Boxeo", "Combate", "Atletismo", "Natación", "Natacion",
            "Ciclismo", "Motor", "Snooker", "Hípica", "Hipica", "Tiro"
        }

                # Limpieza universal de torneos que arrastran rondas (ej. 'UEFA Nations League Fase de grupos')
        m_torneo_ronda = re.search(r"\b(Fase\s+de\s+grupos|Jornada\s+\d+|\d+[ªºa]?\s+Jornada|Round\s+\d+|Cuartos\s+de\s+final|Semifinal(?:es)?|Final)\b", torneo_original, re.I)
        if m_torneo_ronda:
            if not ronda:
                ronda = m_torneo_ronda.group(1).strip()
            torneo_original = torneo_original[:m_torneo_ronda.start()].strip().rstrip(":-–— ")

        if categoria_real in DEPORTES_CIRCUITO:
            tipo_ev = "circuito"
            # 1. Quitar la categoría del inicio del título si es redundante (ej. 'Hípica Concurso...' -> 'Concurso...')
            t_base = re.sub(rf"^(?:{categoria_real}|Deportes?|Directo)\s*[:\-–—]?\s*", "", tit_original, flags=re.I).strip()
            if not t_base:
                t_base = tit_original

            # 2. Si tiene separador de disciplina y sede/sesion con dos puntos (ej. 'Cross Country: Lake Placid')
            if ":" in t_base:
                partes_c = t_base.split(":", 1)
                disciplina = partes_c[0].strip()
                detalle_c = partes_c[1].strip()
                tit_limpio = disciplina
                ronda = detalle_c
                torneo_original = disciplina
            else:
                tit_limpio = t_base
                if not torneo_original or torneo_original.lower() in ["deportes", "deportes en vivo", categoria_real.lower()]:
                    torneo_original = tit_limpio
        elif loc and vis:
            tipo_ev = "duelo"
            tit_limpio = f"{loc} vs {vis}"
        else:
            tipo_ev = ev.get("tipo_evento", "circuito")

        ev["titulo"] = tit_limpio
        ev["torneo"] = torneo_original or tit_limpio
        ev["categoria"] = categoria_real
        ev["tipo_evento"] = tipo_ev
        ev["equipo_local"] = loc
        ev["equipo_visitante"] = vis
        ev["ronda"] = ronda

        if categoria_real in ["Fútbol", "Baloncesto", "Balonmano", "Rugby", "Hockey"] and tipo_ev == "circuito":
            if len(tit_limpio.split()) <= 2 and not any(k in tit_limpio.upper() for k in ["CUP", "TOUR", "OPEN", "CIRCUITO"]):
                log.info("Descartando evento huérfano de %s: '%s'", categoria_real, tit_limpio)
                continue

        ev["titulo"] = tit_limpio
        ev["torneo"] = torneo_original or tit_limpio
        ev["categoria"] = categoria_real
        ev["tipo_evento"] = tipo_ev
        ev["equipo_local"] = loc
        ev["equipo_visitante"] = vis
        ev["ronda"] = ronda
        eventos_pre.append(ev)

    # Fusión inteligente de eventos idénticos (por entidad deportiva)
    eventos_fusionados = []
    for ev in eventos_pre:
        fusionado = False
        for exist in eventos_fusionados:
            if son_mismo_evento(ev, exist):
                ids_existentes = {f.get("id_xtream") for f in exist.get("fuentes", [])}
                for f in ev.get("fuentes", []):
                    if f.get("id_xtream") not in ids_existentes and len(exist["fuentes"]) < 4:
                        exist["fuentes"].append(f)
                        ids_existentes.add(f.get("id_xtream"))

                # Si el evento entrante es de TV lineal oficial, priorizar sus metadatos limpios
                if "inyector_lineal" in ev.get("origenes", []) and "inyector_lineal" not in exist.get("origenes", []):
                    exist["titulo"] = ev["titulo"]
                    exist["torneo"] = ev["torneo"]
                    exist["subtitulo"] = ev.get("subtitulo", exist["subtitulo"])
                    exist["hora_local_producto"] = ev["hora_local_producto"]
                    exist["hora_utc"] = ev["hora_utc"]
                    exist["equipo_local"] = ev["equipo_local"]
                    exist["equipo_visitante"] = ev["equipo_visitante"]
                    exist["tier"] = ev.get("tier", exist.get("tier", 2))
                    exist["confianza"] = "alta"
                    exist["puntuacion_confianza"] = 0.95
                elif exist.get("torneo", "").lower() in ["deportes", "fútbol", "futbol", "deportes en vivo"] and ev.get("torneo", "").lower() not in ["deportes", "fútbol", "futbol", "deportes en vivo"]:
                    exist["torneo"] = ev["torneo"]
                    exist["subtitulo"] = ev.get("subtitulo", exist["subtitulo"])

                if not exist.get("equipo_local") and ev.get("equipo_local"):
                    exist["equipo_local"] = ev["equipo_local"]
                    exist["equipo_visitante"] = ev["equipo_visitante"]
                    exist["titulo"] = ev["titulo"]
                    exist["tipo_evento"] = ev["tipo_evento"]

                for o in ev.get("origenes", []):
                    if o not in exist.get("origenes", []):
                        exist["origenes"].append(o)

                fusionado = True
                break

        if not fusionado:
            eventos_fusionados.append(ev)

    eventos_pulidos = []
    for ev in eventos_fusionados:
        tit_limpio = ev["titulo"]
        torneo_original = ev["torneo"]
        categoria_real = ev["categoria"]
        loc = ev["equipo_local"]
        vis = ev["equipo_visitante"]
        ronda = ev.get("ronda", "")
        tipo_ev = ev.get("tipo_evento", "duelo" if loc and vis else "circuito")

        if tipo_ev == "duelo":
            if ronda and torneo_original and ronda.lower() not in torneo_original.lower():
                subtitulo = f"{torneo_original} • {ronda}"
            elif torneo_original and torneo_original.lower() != tit_limpio.lower() and torneo_original.lower() not in ["deportes", "fútbol", "futbol"]:
                subtitulo = f"{torneo_original}"
            else:
                subtitulo = tit_limpio
            referencia = subtitulo
        else:
            # En circuitos: Línea 2 es Sede / Sesión / Ronda. NUNCA la categoría repetida.
            subtitulo = ronda if ronda and ronda.lower() != categoria_real.lower() and ronda.lower() != tit_limpio.lower() else ""
            referencia = subtitulo

        logo_torneo = resolver_logo_torneo(torneo_original or tit_limpio, categoria_real)
        logo_loc = resolver_logo_equipo(loc, categoria_real, torneo_original) if loc else ""
        logo_vis = resolver_logo_equipo(vis, categoria_real, torneo_original) if vis else ""

        ev["subtitulo"] = subtitulo
        ev["referencia"] = referencia
        ev["logo_torneo"] = logo_torneo
        ev["logo_local"] = logo_loc
        ev["logo_visitante"] = logo_vis
        ev["banner"] = logo_torneo
        eventos_pulidos.append(ev)

    log.info("Pase de Curación Semántica completado: %d eventos procesados y deduplicados", len(eventos_pulidos))
    return eventos_pulidos
