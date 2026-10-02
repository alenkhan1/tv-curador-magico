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

def desmontar_duelo(titulo: str, categoria: str) -> Tuple[str, str, str, str]:
    tit = limpiar_texto(titulo)
    ronda = ""
    m_ronda = PREFIJOS_RONDA.search(tit)
    if m_ronda:
        ronda = m_ronda.group(0).rstrip(": ").strip()
        tit = tit[m_ronda.end():].strip()

    partes = re.split(r"\s+(?:vs\.?|v\.?|-)\s+", tit, flags=re.I)
    if len(partes) == 2 and categoria in ["Fútbol", "Baloncesto", "Balonmano", "Pádel", "Tenis", "Béisbol", "Fútbol Americano", "Rugby", "Polo", "Hockey"]:
        loc = limpiar_texto(partes[0])
        vis = limpiar_texto(partes[1])
        return f"{loc} vs {vis}", loc, vis, ronda

    return tit, "", "", ronda

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

    if diff_min > 90:
        return False

    # Coincidencia por título y categoría
    tit1 = _normalizar(ev1.get("titulo", ""))
    tit2 = _normalizar(ev2.get("titulo", ""))
    cat1 = _normalizar(ev1.get("categoria", ""))
    cat2 = _normalizar(ev2.get("categoria", ""))

    if tit1 == tit2:
        return True

    if cat1 and cat2 and cat1 == cat2:
        palabras1 = set(tit1.split())
        palabras2 = set(tit2.split())
        inter = palabras1.intersection(palabras2)
        if len(inter) >= 2 and any(k in tit1 for k in ["OPEN", "PRIX", "FIGHT", "NIGHT", "UFC", "F1", "TOUR", "CUP", "LAKE PLACID"]):
            return True

    return False

def post_procesar_y_curar_eventos(eventos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    eventos_pre = []
    for ev in eventos:
        tit_original = limpiar_texto(ev.get("titulo", ""))
        torneo_original = limpiar_texto(ev.get("torneo", ""))
        cat_original = ev.get("categoria", "")

        categoria_real = deducir_categoria(tit_original, torneo_original, cat_original)
        tit_limpio, loc, vis, ronda = desmontar_duelo(tit_original, categoria_real)

        if not loc and ev.get("equipo_local"):
            loc = limpiar_texto(ev.get("equipo_local", ""))
        if not vis and ev.get("equipo_visitante"):
            vis = limpiar_texto(ev.get("equipo_visitante", ""))

        DEPORTES_CIRCUITO = {
            "Tenis", "Golf", "MMA", "Boxeo", "Combate", "Atletismo", "Natación", "Natacion",
            "Ciclismo", "Motor", "Snooker", "Hípica", "Hipica", "Tiro"
        }

        if categoria_real in DEPORTES_CIRCUITO:
            tipo_ev = "circuito"
            # En deportes de circuito para TV, el título de la tarjeta es la competición/organización
            nombre_circuito = torneo_original if torneo_original and torneo_original != "Deportes en Vivo" else tit_original
            # Si el torneo era el nombre del duelo individual, limpiarlo
            if any(sep in nombre_circuito.lower() for sep in [" vs ", " v ", " - "]):
                nombre_circuito = torneo_original if torneo_original and " vs " not in torneo_original.lower() else categoria_real
            tit_limpio = nombre_circuito
        elif loc and vis:
            tipo_ev = "duelo"
            tit_limpio = f"{loc} vs {vis}"
        else:
            tipo_ev = ev.get("tipo_evento", "circuito")

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
            if ronda and torneo_original:
                subtitulo = f"{torneo_original} • {ronda}"
            elif torneo_original and torneo_original.lower() != tit_limpio.lower() and torneo_original.lower() not in ["deportes", "fútbol", "futbol"]:
                subtitulo = f"{torneo_original}"
            else:
                subtitulo = f"{categoria_real} en Directo"
            referencia = subtitulo
        else:
            subtitulo = ronda if ronda else (torneo_original if torneo_original != tit_limpio else categoria_real)
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
