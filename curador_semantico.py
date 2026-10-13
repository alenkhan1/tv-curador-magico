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
    "RIONEGRO AGUILAS": "AGUILAS DORADAS",
    "AGUILAS RIONEGRO": "AGUILAS DORADAS",
    "TALENTO DORADO": "AGUILAS DORADAS",
    "SPORTING CP": "SPORTING LISBOA",
    "SPORTING CLUB DE PORTUGAL": "SPORTING LISBOA",
    "BAYERN MUNCHEN": "BAYERN MUNICH",
    "FC BAYERN": "BAYERN MUNICH",
    "INTERNAZIONALE": "INTER",
    "INTER MILAN": "INTER",
    "CLUB BRUGGE KV": "CLUB BRUGGE",
    "BOCA JRS": "BOCA JUNIORS",
}

RE_PAREN_DETALLE = re.compile(
    r"\s*\((?:ALCS|NLCS|Game|Juego|Round|Ronda|Fase|Match|Leg|Ida|Vuelta|Partido|Serie|\d+).*?\)",
    re.I
)

def normalizar_nombre_equipo(nombre: str) -> str:
    """Remueve prefijos, sufijos, sinonimia y parentesis de clubes/paises para emparejamiento estricto."""
    s = re.sub(r"\(.*?\)", "", nombre)
    s = _normalizar(s)
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
        m_vis_par = RE_PAREN_DETALLE.search(vis)
        if m_vis_par:
            if not ronda:
                ronda = m_vis_par.group(0).strip(" ()")
            vis = (vis[:m_vis_par.start()] + vis[m_vis_par.end():]).strip()
        m_loc_par = RE_PAREN_DETALLE.search(p0)
        if m_loc_par:
            if not ronda:
                ronda = m_loc_par.group(0).strip(" ()")
            p0 = (p0[:m_loc_par.start()] + p0[m_loc_par.end():]).strip()
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
    if not iso_utc:
        return 0
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(iso_utc).replace("Z", "+00:00"))
        return int(dt.timestamp() // 60)
    except Exception:
        pass
    try:
        m = re.search(r"(\d{1,2}):(\d{2})", str(iso_utc))
        if m:
            return int(m.group(1)) * 60 + int(m.group(2))
    except Exception:
        pass
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

        # 1. Emparejamiento exacto invariante al orden (Home vs Away o Away vs Home)
        match_exact = ((l1_n == l2_n and v1_n == v2_n) or (l1_n == v2_n and v1_n == l2_n))
        if match_exact and diff_min <= 180:
            return True

        # 1.1 Coincidencia por palabra clave distintiva (>= 5 letras en ambos bandos)
        toks_v1 = {w for w in v1_n.split() if len(w) >= 5}
        toks_v2 = {w for w in v2_n.split() if len(w) >= 5}
        toks_l1 = {w for w in l1_n.split() if len(w) >= 5}
        toks_l2 = {w for w in l2_n.split() if len(w) >= 5}
        match_keywords_dir = bool(toks_l1.intersection(toks_l2)) and bool(toks_v1.intersection(toks_v2))
        match_keywords_inv = bool(toks_l1.intersection(toks_v2)) and bool(toks_v1.intersection(toks_l2))
        if (match_keywords_dir or match_keywords_inv) and diff_min <= 120:
            return True

        # 2. Emparejamiento por subcadenas o aproximacion difusa (invariante al orden)
        if l1_n and l2_n and v1_n and v2_n and diff_min <= 120:
            from difflib import SequenceMatcher

            sub_dir = (l1_n in l2_n or l2_n in l1_n) and (v1_n in v2_n or v2_n in v1_n)
            sim_l_dir = SequenceMatcher(None, l1_n, l2_n).ratio()
            sim_v_dir = SequenceMatcher(None, v1_n, v2_n).ratio()
            fuzzy_dir = ((sim_l_dir >= 0.80 and sim_v_dir >= 0.60) or 
                         (sim_l_dir >= 0.60 and sim_v_dir >= 0.80) or 
                         (sim_l_dir >= 0.70 and sim_v_dir >= 0.70))

            sub_inv = (l1_n in v2_n or v2_n in l1_n) and (v1_n in l2_n or l2_n in v1_n)
            sim_l_inv = SequenceMatcher(None, l1_n, v2_n).ratio()
            sim_v_inv = SequenceMatcher(None, v1_n, l2_n).ratio()
            fuzzy_inv = ((sim_l_inv >= 0.80 and sim_v_inv >= 0.60) or 
                         (sim_l_inv >= 0.60 and sim_v_inv >= 0.80) or 
                         (sim_l_inv >= 0.70 and sim_v_inv >= 0.70))

            if sub_dir or fuzzy_dir or sub_inv or fuzzy_inv:
                cat1_raw = ev1.get("categoria", "")
                cat2_raw = ev2.get("categoria", "")
                if cat1_raw and cat2_raw and cat1_raw != "Otros Deportes" and cat2_raw != "Otros Deportes":
                    if cat1_raw != cat2_raw and not (("Fútbol" in cat1_raw or "Futbol" in cat1_raw) and ("Fútbol" in cat2_raw or "Futbol" in cat2_raw)):
                        return False
                return True

    # Coincidencia para Circuitos y Transmisiones sin Duelo
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
        # En tenis / padel: Cada partido/sesion es un evento independiente.
        # NUNCA fusionar partidos distintos. Solo se fusionan si comparten el mismo titulo,
        # la misma cancha/pista (si esta disponible) y la misma hora exacta (+-30 min).
        if cat1 in ["TENIS", "PADEL"] or cat2 in ["TENIS", "PADEL"]:
            part1 = _normalizar(ev1.get("referencia", "") or ev1.get("participantes", ""))
            part2 = _normalizar(ev2.get("referencia", "") or ev2.get("participantes", ""))

            # Si ambos tienen participantes definidos:
            if part1 and part2:
                if part1 != part2:
                    words1 = set(w for w in part1.split() if len(w) >= 4 and w not in ["CONTRA", "VERSUS"])
                    words2 = set(w for w in part2.split() if len(w) >= 4 and w not in ["CONTRA", "VERSUS"])
                    if not (words1 and words2 and (words1.issubset(words2) or words2.issubset(words1) or len(words1 & words2) >= 2)):
                        return False
            elif (part1 and not part2) or (part2 and not part1):
                return False

            # Verificar canchas si estan especificadas en subtitulo o ronda
            ref1 = _normalizar(f"{ev1.get('subtitulo', '')} {ev1.get('ronda', '')}")
            ref2 = _normalizar(f"{ev2.get('subtitulo', '')} {ev2.get('ronda', '')}")
            pat_cancha = r"\b(STADIUM|CENTRE|CENTER|COURT\s*\d+|PISTA\s*\d+|CANCHA\s*\d+|GRANDSTAND|SHOW\s*COURT)\b"
            cancha1 = re.search(pat_cancha, ref1)
            cancha2 = re.search(pat_cancha, ref2)
            if cancha1 and cancha2 and cancha1.group(0) != cancha2.group(0):
                return False

            if tit1 == tit2 and diff_min <= 30:
                return True
            return False

        # En combates / veladas (UFC, WWE, Boxeo):
        # Mismo titulo raiz y misma fecha (+-180 min) se fusionan para consolidar feeds de audio (ESP, ENG, POR).
        if cat1 in ["COMBATE", "BOXEO", "MMA"] or cat2 in ["COMBATE", "BOXEO", "MMA"]:
            if tit1 == tit2 and diff_min <= 180:
                return True
            return False

        # Otros circuitos generales (F1, Ciclismo):
        if tit1 == tit2 and diff_min <= 60:
            return True
        return False

    if tit1 and tit2 and tit1 == tit2 and diff_min <= 90:
        return True

    return False

def extraer_metadatos_circuito(tit_raw: str, categoria: str) -> Dict[str, str]:
    """
    Extrae de forma limpia y profesional los metadatos de eventos de circuito, combate y deportes individuales:
    - Torneo / Velada / Organizador -> Linea 1 de la tarjeta en TV (titulo / torneo)
    - Sede / Cancha / Pista / Sesion -> Linea 2 de la tarjeta en TV (subtitulo)
    - Contrincantes / Protagonistas -> Linea 3 bajo la preview del reproductor (referencia)
    """
    s = re.sub(r'\[.*?\]|\(.*?\)', '', tit_raw)
    s = re.sub(r'\s*[-–—]\s*(?:Português|Portugues|English|Español|Spanish|HD|FHD|4K|Directo|En Vivo).*$', '', s, flags=re.I)
    s = re.sub(r'\s*\d{2,}>\s*', ' > ', s)
    bloques = [b.strip() for b in re.split(r'[:|>]|\s+[-–—]\s+', s) if b.strip()]

    cat_patt = rf'^(?:{categoria}|Deportes?|Directo|En Vivo|Combate|Boxeo|Tenis|Golf|Motor|Ciclismo)\s*[:\-–—]?\s*'

    idx_duelo = -1
    duelo_str = ''
    for i, b in enumerate(bloques):
        if re.search(r'\b(?:vs\.?|contra|v\.)\b', b, re.I):
            idx_duelo = i
            m_vs = re.split(r'\s+(?:vs\.?|contra|v\.)\s+', b, flags=re.I)
            if len(m_vs) == 2:
                p1 = re.sub(cat_patt, '', m_vs[0], flags=re.I).strip()
                p2 = m_vs[1].strip()
                duelo_str = f'{p1} vs. {p2}'
            else:
                duelo_str = b
            break

    torneo = ''
    sede = ''
    pat_cancha = re.compile(r'\b(court\s*\d*|pista\s*\d*|cancha\s*\d*|stadium|centre|center|apex|coliseo|estadio|ronda\s*\d*|round\s*\d*|semifinal|cuartos|final|clasificaci[oó]n|pr[aá]ctica\s*\d*|carrera|etapa\s*\d*)\b', re.I)

    bloques_restantes = [b for i, b in enumerate(bloques) if i != idx_duelo]

    for b in bloques_restantes:
        b_clean = re.sub(cat_patt, '', b, flags=re.I).strip()
        if not b_clean:
            continue
        if pat_cancha.search(b_clean):
            sede = b_clean if not sede else f'{sede} • {b_clean}'
        else:
            torneo = b_clean if not torneo else f'{torneo} • {b_clean}'

    if not torneo and duelo_str:
        torneo = f'Velada de {categoria}' if categoria in ['Boxeo', 'MMA', 'Combate'] else f'{categoria} Profesional'
    elif not torneo:
        torneo = tit_raw.strip()

    return {
        'torneo': torneo,
        'subtitulo': sede,
        'participantes': duelo_str
    }

def post_procesar_y_curar_eventos(eventos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    eventos_pre = []
    for ev in eventos:
        tit_original = limpiar_texto(ev.get("titulo", ""))
        torneo_original = limpiar_texto(ev.get("torneo", ""))
        cat_original = ev.get("categoria", "")
        tit_raw = ev.get("titulo_original_crudo") or ev.get("titulo_original") or tit_original
        ev["titulo_original_crudo"] = tit_raw

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
        m_torneo_ronda = re.search(r"\b(Fase\s+de\s+grupos|Jornada\s+\d+|\d+[ªa]?\s+Jornada|Round\s+\d+|Cuartos\s+de\s+final|Semifinal(?:es)?|Final)\b", torneo_original, re.I)
        if m_torneo_ronda:
            if not ronda:
                ronda = m_torneo_ronda.group(1).strip()
            torneo_original = torneo_original[:m_torneo_ronda.start()].strip().rstrip(":-–— ")

        if categoria_real in DEPORTES_CIRCUITO:
            tipo_ev = "circuito"
            meta_c = extraer_metadatos_circuito(tit_original, categoria_real)
            tit_limpio = meta_c["torneo"]
            torneo_original = meta_c["torneo"]
            ronda = meta_c["subtitulo"]
            ev["subtitulo"] = meta_c["subtitulo"]
            if meta_c["participantes"]:
                ev["referencia"] = meta_c["participantes"]
            loc = ""
            vis = ""
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

        eventos_pre.append(ev)

    # Fusión inteligente de eventos idénticos (por entidad deportiva)
    eventos_fusionados = []
    for ev in eventos_pre:
        fusionado = False
        for exist in eventos_fusionados:
            if son_mismo_evento(ev, exist):
                ids_existentes = {f.get("id_xtream") for f in exist.get("fuentes", [])}
                for f in ev.get("fuentes", []):
                    if f.get("id_xtream") not in ids_existentes and len(exist["fuentes"]) < 10:
                        exist["fuentes"].append(f)
                        ids_existentes.add(f.get("id_xtream"))

                if ev.get("subtitulo") and (not exist.get("subtitulo") or exist.get("subtitulo") in ["MLB", "Rugby", "Fútbol", "Futbol"]):
                    exist["subtitulo"] = ev["subtitulo"]
                if ev.get("referencia") and (not exist.get("referencia") or exist.get("referencia") in ["MLB", "Rugby", "Fútbol", "Futbol"]):
                    exist["referencia"] = ev["referencia"]
                if ev.get("ronda") and not exist.get("ronda"):
                    exist["ronda"] = ev["ronda"]

                # Preservar metadatos oficiales y logos HD
                if not exist.get("logo_local") and ev.get("logo_local"):
                    exist["logo_local"] = ev["logo_local"]
                if not exist.get("logo_visitante") and ev.get("logo_visitante"):
                    exist["logo_visitante"] = ev["logo_visitante"]
                if not exist.get("logo_torneo") and ev.get("logo_torneo"):
                    exist["logo_torneo"] = ev["logo_torneo"]
                if not exist.get("banner") and ev.get("banner"):
                    exist["banner"] = ev["banner"]
                if not exist.get("referencia") and ev.get("referencia"):
                    exist["referencia"] = ev["referencia"]
                if not exist.get("titulo_original_crudo") and ev.get("titulo_original_crudo"):
                    exist["titulo_original_crudo"] = ev["titulo_original_crudo"]
                if ev.get("contrastado_api"):
                    exist["contrastado_api"] = True

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
        ref_existente = ev.get("referencia", "").strip()

        if tipo_ev == "duelo":
            if ronda and torneo_original and ronda.lower() not in torneo_original.lower():
                subtitulo = f"{torneo_original} • {ronda}"
            elif torneo_original and torneo_original.lower() != tit_limpio.lower() and torneo_original.lower() not in ["deportes", "fútbol", "futbol"]:
                subtitulo = f"{torneo_original}"
            else:
                subtitulo = tit_limpio
            referencia = ref_existente or subtitulo
        else:
            # En circuitos: Línea 2 es Sede / Sesión / Ronda. NUNCA la categoría repetida.
            subtitulo = ev.get("subtitulo") or (ronda if ronda and ronda.lower() != categoria_real.lower() and ronda.lower() != tit_limpio.lower() else "")
            referencia = ref_existente or subtitulo

        # PROTECCIÓN ABSOLUTA DE LOGOS OFICIALES DE LA API Y RESOLUCIÓN ROBUSTA
        logo_torneo = ev.get("logo_torneo") or resolver_logo_torneo(torneo_original or tit_limpio, categoria_real)
        logo_loc = ev.get("logo_local") or (resolver_logo_equipo(loc, categoria_real, torneo_original) if loc else "")
        logo_vis = ev.get("logo_visitante") or (resolver_logo_equipo(vis, categoria_real, torneo_original) if vis else "")

        ev["subtitulo"] = subtitulo
        ev["referencia"] = referencia
        ev["logo_torneo"] = logo_torneo
        ev["logo_local"] = logo_loc
        ev["logo_visitante"] = logo_vis
        ev["banner"] = ev.get("banner") or logo_torneo
        eventos_pulidos.append(ev)

    log.info("Pase de Curación Semántica completado: %d eventos procesados y deduplicados", len(eventos_pulidos))
    return eventos_pulidos

