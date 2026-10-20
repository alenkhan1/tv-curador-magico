# -*- coding: utf-8 -*-
"""
Post-Procesador y Normalizador Semántico Deportivo:
Ejecuta la Fase 2 de Curación Inteligente sobre todos los eventos del catálogo:
1. Respeta la Bóveda Diaria: si un evento ya tiene 'curado_integro': True, no altera sus textos ni logos; solo fusiona fuentes nuevas.
2. Descarta falsos circuitos en deportes que son estrictamente de duelo (Fútbol, Baloncesto, Béisbol, NFL, NHL, etc.).
3. Normaliza tipografía (Smart Title Case) y elimina redundancias entre Línea 1 (Torneo/Evento) y Línea 2 (Sede/Cancha) en circuitos.
4. Deduplicación inteligente por entidad deportiva (equipos normalizados + hora + división).
5. Resuelve escudos, banderas oficiales y logos de torneos validando compatibilidad estricta de deporte.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from typing import Any, Dict, List, Tuple

from resolvedor_logos import (
    deducir_deporte_desde_logo,
    resolver_logo_equipo,
    resolver_logo_torneo,
    es_logo_compatible_con_categoria,
    BANDERAS_PAISES,
    _normalizar,
)
from conector_api_sports import extraer_marcadores_division
from sanitizador_nombres import formatear_titulo_inteligente, es_texto_no_vivo_o_aplazado, es_estado_aplazado_o_repeticion

log = logging.getLogger("curador_semantico")

DEPORTES_ESTRICTO_DUELO = {
    "Fútbol", "Baloncesto", "Béisbol", "Fútbol Americano", "Hockey", "Balonmano", "Rugby", "Voleibol", "Polo"
}

DEPORTES_CIRCUITO = {
    "Tenis", "P?del", "Padel", "Golf", "MMA", "Boxeo", "Combate",
    "Atletismo", "Nataci?n", "Natacion", "Ciclismo", "Motor", "Snooker", "H?pica", "Hipica", "Tiro",
    "Gimnasia", "Remo", "Vela", "Pirag?ismo", "Piraguismo", "Triatl?n", "Triatlon", "Patinaje",
    "Esqu?", "Esqui", "Escalada", "Dardos", "Surf", "Esgrima", "B?dminton", "Badminton", "Curling", "Tejo"
}

DEDUCCIONES_DEPORTE = [
    ("Balonmano", ["ASOBAL", "BALONMANO", "HANDBALL", "EHF"]),
    ("Baloncesto", ["ACB", "LIGA ENDESA", "EUROLEAGUE", "EUROLIGA", "NBA", "WNBA", "BASKET", "BALONCESTO", "NCAA BASKET", "CALVARY", "NEXT LEVEL", "BRISTOL FLYERS", "LONDON LIONS", "NEWCASTLE EAGLES", "BIG BLUE MADNESS"]),
    ("Snooker", ["SNOOKER", "BILLAR", "SHENZHEN", "NORTHERN IRELAND OPEN", "NORTHERN IRELAND", "UK CHAMPIONSHIP", "WORLD SNOOKER TOUR", "WST", "CRUCIBLE"]),
    ("Ciclismo", ["CICLISMO", "CYCLING", "VUELTA", "GIRO", "TOUR DE FRANCE", "CROSS COUNTRY", "LAKE PLACID", "BENELUX TOUR"]),
    ("Pádel", ["PADEL", "PÁDEL", "PREMIER PADEL", "FIP"]),
    ("Tenis", ["ATP", "WTA", "TENIS", "TENNIS", "CHALLENGER", "ROLAND GARROS", "WIMBLEDON", "US OPEN", "CHINA OPEN", "DAVIS", "JAPAN OPEN", "SHANGHAI MASTERS", "WUHAN OPEN"]),
    ("Motor", ["F1", "FORMULA 1", "FÓRMULA 1", "MOTOGP", "MOTO GP", "WRC", "RALLY", "NASCAR", "INDYCAR", "MOTOR", "DIRTVISION", "SPEEDWAY", "WORLD SSP", "SSP", "SUPERBIKE", "WSBK", "SUPERSPORT"]),
    ("Combate", ["UFC", "BOXEO", "BOXING", "COMBATE", "MMA", "BELLATOR", "BKFC", "ONE FRIDAY", "ONE CHAMPIONSHIP", "FIGHT NIGHT", "SMACKDOWN", "WWE", "AEW"]),
    ("Béisbol", ["MLB", "LMB", "LMP", "MEXICAN LEAGUE", "LIGA MEXICANA DEL PACIFICO", "LIDOM", "LIGA DOMINICANA", "LICEY", "ESCOGIDO", "LIDOM", "LIGA DOMINICANA", "LICEY", "ESCOGIDO", "TOMATEROS", "BEISBOL", "BÉISBOL", "BASEBALL"]),
    ("Fútbol Americano", ["NFL", "FUTBOL AMERICANO", "NCAA FOOTBALL", "CFL", "SPRINT LEAGUE", "F AMERICANO", "F AUSTRALIANO"]),
    ("Hockey", ["NHL", "HOCKEY"]),
    ("Rugby", ["RUGBY", "U20 RUGBY", "TOP 14", "SIX NATIONS", "SOUTHLAND", "NORTHLAND"]),
    ("Golf", ["GOLF", "PGA", "DP WORLD", "LIV GOLF", "DUNHILL", "BANK OF UTAH"]),
    ("Polo", ["POLO", "PALERMO", "LA IRENITA", "VELAY", "ELLERSTINA", "DOLFINA"]),
    ("Natación", ["NATACION", "NATACIÓN", "AQUATICS", "WATERPOLO"]),
    ("Hípica", ["HIPICA", "HÍPICA", "GRAN PREMIO CIUTAT DE BARCELONA", "CSIO BARCELONA", "EQUESTRIAN"]),
    ("Atletismo", ["ATLETISMO", "ATHLETICS"]),
    ("Gimnasia", ["GIMNASIA ARTISTICA", "GIMNASIA ARTÍSTICA", "GIMNASIA RITMICA", "GIMNASIA RÍTMICA", "GYMNASTICS", "GIMNASTAS", "GIMNASTA", "GIMNASIA DEPORTIVA"]),
    ("Remo", ["REMO", "ROWING", "BEACH SPRINT", "BEACHS SPRINT"]),
    ("Vela", ["VELA", "SAILING", "REGATA"]),
    ("Tiro", ["MALAKASA", "TIRO AL PLATO", "SHOOTING"]),
    ("Fútbol", [
        "LALIGA", "PREMIER LEAGUE", "SERIE A", "BUNDESLIGA", "LIGUE 1", "NATIONS LEAGUE", "CHAMPIONS LEAGUE", "UEFA CHAMPIONS", "CHAMPIONS", "EUROPA LEAGUE", "CONFERENCE LEAGUE", "LIGA DE CAMPEONES", "FASE LIGA", "PARIS SAINT-GERMAIN", "PSG", "BARCELONA", "REAL MADRID", "MANCHESTER CITY", "LIVERPOOL", "BAYERN", "JUVENTUS", "INTER DE MILAN",
        "LIBERTADORES", "SUDAMERICANA", "BETPLAY", "FUTBOL", "FÚTBOL", "COPA ARGENTINA",
        "LIGA MX", "NWSL", "NSL", "CHILE", "ARG", "BOCA JUNIORS", "SANTOS", "UNION",
        "SOACHA", "INDEPENDIENTE", "RIVADAVIA", "GIMNASIA LP", "GIMNASIA LA PLATA", "GIMNASIA JUJUY", "GIMNASIA (J)", "GIMNASIA (M)", "GIMNASIA MENDOZA", "DEPORTIVO CALI", "ALIANZA",
        "SMARTBANK", "BRASILEIRAO", "EREDIVISIE", "SEGUNDA URUGUAY", "LIGA HONDURAS", "KINGS LEAGUE", "QUEENS LEAGUE", "POWERCHAIR", "FIPFA", "BRASILEIRÃO", "CONCACAF", "SOCCER", "EXPANSION MX",
        "CORRECAMINOS", "CRUZ AZUL", "TEPATITLAN", "TAMPICO", "VENADOS", "AMERICA", "USL", "1RA NACIONAL", "PRIMERA NACIONAL", "NUEVA CHICAGO"
    ]),
]

TORNEOS_GENERICOS_CIRCUITO = {
    "WTA", "WTA 1000", "WTA 500", "WTA 250", "WTA TOUR",
    "ATP", "ATP MASTERS 1000", "ATP 1000", "ATP 500", "ATP 250", "ATP TOUR",
    "TENIS", "PADEL", "PÁDEL", "MOTOR", "CICLISMO", "COMBATE", "BOXEO", "MMA", "GOLF",
    "ALL ELITE WRESTLING", "AEW", "WWE", "UFC", "UCI", "UCI WORLDTOUR", "UCI PROSERIES"
}

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
    "INTER BOGOTA": "INTERNACIONAL DE BOGOTA",
    "INTER DE BOGOTA": "INTERNACIONAL DE BOGOTA",
    "INTERNACIONAL BOGOTA": "INTERNACIONAL DE BOGOTA",
    "FORTALEZA CEIF": "FORTALEZA",
    "FORTALEZA FC": "FORTALEZA",
    "SPORTING CP": "SPORTING LISBOA",
    "SPORTING CLUB DE PORTUGAL": "SPORTING LISBOA",
    "BAYERN MUNCHEN": "BAYERN MUNICH",
    "FC BAYERN": "BAYERN MUNICH",
    "INTERNAZIONALE": "INTER",
    "INTER MILAN": "INTER",
    "CLUB BRUGGE KV": "CLUB BRUGGE",
    "BOCA JRS": "BOCA JUNIORS",
    "RED BULL NEW YORK": "NEW YORK RB",
    "NEW YORK RED BULLS": "NEW YORK RB",
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
    s = re.sub(r"\b(CD|CF|FC|SD|UD|AD|CA|CSD|REAL|ATLETICO|ATL|DEPORTIVO|DEP|CLUB|DEPORTES|FUTBOL CLUB|SAD|BALOMPIE|BADALONA|CUNDINAMARCA|DE BOGOTA|BOGOTA|CEIF)\b", "", s)
    s = re.sub(r"\bINTER\b", "INTERNACIONAL", s)
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
    if cat_actual:
        c_up = _normalizar(cat_actual)
        if c_up == "FUTBOL":
            return "Fútbol"
        if c_up == "BEISBOL":
            return "Béisbol"
        if c_up == "PADEL":
            return "Pádel"
        if c_up == "NATACION":
            return "Natación"
        if c_up in ["FORMULA 1", "MOTOGP"]:
            return "Motor"
        if c_up == "LUCHA LIBRE":
            return "Combate"
    return cat_actual if cat_actual and cat_actual != "Otros Deportes" else "Otros Deportes"

def desmontar_duelo(titulo: str, categoria: str) -> Tuple[str, str, str, str, str]:
    tit = limpiar_texto(titulo)
    ronda = ""
    torneo_extraido = ""

    partes = re.split(r"\s+(?:vs\.?|v\.?|-)\s+", tit, flags=re.I)
    if len(partes) == 2:
        p0, vis = partes[0].strip(), partes[1].strip()
        # Extraccion de prefijos de torneo y canal (ej. "J003: 1ra Nacional: Nueva Chicago" -> torneo="1ra Nacional", loc="Nueva Chicago")
        if ":" in p0:
            subpartes = [sp.strip() for sp in p0.split(":") if sp.strip()]
            subpartes = [sp for sp in subpartes if not re.match(r"^J\d+$", sp, re.I)]
            if len(subpartes) >= 2:
                torneo_extraido = subpartes[0]
                p0 = subpartes[-1]

        # Expansion de abreviaturas tradicionales de clubes argentinos ('Gim.' -> 'Gimnasia', '(J)' -> 'Jujuy')
        vis = re.sub(r'\bGim\.?\s+', 'Gimnasia ', vis, flags=re.I)
        vis = re.sub(r'\s*\([Jj]\)', ' Jujuy', vis)
        vis = re.sub(r'\s*\([Mm]\)', ' Mendoza', vis)
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
        m_ronda = re.search(r"\b(\d+[ªºa]?\s+Jornada|Jornada\s+\d+|Fase\s+de\s+grupos|Fase\s+Liga|Fecha\s+\d+|Round\s+\d+|Cuartos\s+de\s+final|Semifinal(?:es)?|Final)\b[:\s]*", p0, re.I)
        if m_ronda:
            torneo_pref = p0[:m_ronda.start()].strip()
            ronda = m_ronda.group(1).strip()
            loc = formatear_titulo_inteligente(p0[m_ronda.end():].strip())
            vis_f = formatear_titulo_inteligente(vis)
            torneo_limpio = re.sub(r"^(?:Fútbol\s+(?:Sala\s+)?|Baloncesto\s+|Balonmano\s+)", "", torneo_pref, flags=re.I).strip()
            return f"{loc} vs {vis_f}", loc, vis_f, ronda, torneo_limpio or torneo_pref
        elif categoria in DEPORTES_ESTRICTO_DUELO or categoria in ["Pádel", "Tenis"]:
            loc = formatear_titulo_inteligente(limpiar_texto(p0))
            vis_limpio = formatear_titulo_inteligente(limpiar_texto(vis))
            return f"{loc} vs {vis_limpio}", loc, vis_limpio, ronda, torneo_extraido

    return formatear_titulo_inteligente(tit), "", "", ronda, ""

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
        div1 = extraer_marcadores_division(f"{ev1.get('titulo','')} {ev1.get('torneo','')} {loc1} {vis1}")
        div2 = extraer_marcadores_division(f"{ev2.get('titulo','')} {ev2.get('torneo','')} {loc2} {vis2}")
        # Solo son incompatibles si AMBOS especifican marcadores explícitos y son contradictorios (ej. Sub-20 vs Sub-17)
        if div1 and div2 and div1 != div2:
            return False

        # Validación estricta de país/liga: prohibir fusionar duelos de ligas nacionales distintas
        t1_norm = _normalizar(ev1.get("torneo", ""))
        t2_norm = _normalizar(ev2.get("torneo", ""))
        if t1_norm and t2_norm:
            grupos_paises = [
                ("ITALIA", "SERIE A"),
                ("PAISES BAJOS", "HOLANDA", "EREDIVISIE"),
                ("ESPANA", "LALIGA", "LA LIGA"),
                ("INGLATERRA", "PREMIER LEAGUE"),
                ("ALEMANIA", "BUNDESLIGA"),
                ("FRANCIA", "LIGUE 1"),
                ("ARGENTINA", "LIGA PROFESIONAL", "TORNEO BETANO"),
                ("COLOMBIA", "LIGA BETPLAY", "COPA BETPLAY"),
                ("BRASIL", "BRASILEIRAO"),
            ]
            p1, p2 = None, None
            for g in grupos_paises:
                if any(x in t1_norm for x in g):
                    p1 = g[0]
                if any(x in t2_norm for x in g):
                    p2 = g[0]
            if p1 and p2 and p1 != p2:
                return False

        l1_n = normalizar_nombre_equipo(loc1)
        v1_n = normalizar_nombre_equipo(vis1)
        l2_n = normalizar_nombre_equipo(loc2)
        v2_n = normalizar_nombre_equipo(vis2)

        match_exact = ((l1_n == l2_n and v1_n == v2_n) or (l1_n == v2_n and v1_n == l2_n))
        # Si es el duelo exacto el mismo día dentro de 12h, es el mismo partido:
        # Permite fusionar canales y evita duplicar la tarjeta si una fuente trae un diferido posterior
        if match_exact and diff_min <= 720:
            return True

        toks_v1 = {w for w in v1_n.split() if len(w) >= 5}
        toks_v2 = {w for w in v2_n.split() if len(w) >= 5}
        toks_l1 = {w for w in l1_n.split() if len(w) >= 5}
        toks_l2 = {w for w in l2_n.split() if len(w) >= 5}
        match_keywords_dir = bool(toks_l1.intersection(toks_l2)) and bool(toks_v1.intersection(toks_v2))
        match_keywords_inv = bool(toks_l1.intersection(toks_v2)) and bool(toks_v1.intersection(toks_l2))
        if (match_keywords_dir or match_keywords_inv) and diff_min <= 120:
            return True

        # Invariante Físico de Canal Lineal: Un mismo canal no transmite 2 partidos distintos a la misma hora
        canales_1 = {f.get("canal_lineal") for f in ev1.get("fuentes", []) if isinstance(f, dict) and f.get("canal_lineal")}
        canales_2 = {f.get("canal_lineal") for f in ev2.get("fuentes", []) if isinstance(f, dict) and f.get("canal_lineal")}
        if canales_1 and canales_2 and canales_1.intersection(canales_2) and diff_min <= 15:
            if l1_n == l2_n or v1_n == v2_n or toks_l1.intersection(toks_l2) or toks_v1.intersection(toks_v2):
                return True

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
        if cat1 in ["TENIS", "PADEL"] or cat2 in ["TENIS", "PADEL"]:
            ref1 = _normalizar(f"{ev1.get('subtitulo', '')} {ev1.get('ronda', '')} {ev1.get('texto_original_crudo', '')}")
            ref2 = _normalizar(f"{ev2.get('subtitulo', '')} {ev2.get('ronda', '')} {ev2.get('texto_original_crudo', '')}")
            pat_cancha = r"\b(STADIUM|CENTRE|CENTER|COURT\s*\d+|PISTA\s+\d+|CANCHA\s*\d+|GRANDSTAND|SHOW\s*COURT)\b"
            cancha1 = re.search(pat_cancha, ref1)
            cancha2 = re.search(pat_cancha, ref2)
            if cancha1 and cancha2 and cancha1.group(0) != cancha2.group(0):
                return False

            if (tit1 == tit2 or (tit1 in tit2 and len(tit1) >= 8) or (tit2 in tit1 and len(tit2) >= 8)) and diff_min <= 45:
                return True
            return False

        if cat1 in ["COMBATE", "BOXEO", "MMA"] or cat2 in ["COMBATE", "BOXEO", "MMA"]:
            if tit1 == tit2 and diff_min <= 180:
                return True
            return False

        if tit1 == tit2 and diff_min <= 60:
            return True
        return False

    if tit1 and tit2 and tit1 == tit2 and diff_min <= 90:
        return True

    return False

PAT_CANCHA_Y_SESION = re.compile(
    r'(?<!en\s)(?<!de\s)\b('
    r'court\s*\d*|'
    r'pista\s+(?:\d+|central|principal|secundaria)|'
    r'cancha\s*(?:\d+|central|principal)|'
    r'stadium(?:\s*court)?|'
    r'centre\s*court|center\s*court|'
    r'tennis\s*center|'
    r'speedway|raceway|circuit|aut[oó]dromo|'
    r'arena|apex|coliseo|estadio|'
    r'octavos(?:\s+de\s+final)?|cuartos(?:\s+de\s+final)?|semifinal(?:es)?|final(?:es)?|'
    r'ronda\s*\d*|round\s*\d*|primera\s+ronda|segunda\s+ronda|tercera\s+ronda|'
    r'sesi[oó]n\s+(?:matinal|matutina|vespertina|nocturna|diurna|\d+)|'
    r'clasificaci[oó]n|pr[aá]ctica\s*\d*|carrera(?:\s+sprint)?|etapa\s*\d*'
    r')\b',
    re.I
)

def compactar_subtitulo_boton_circuito(titulo_principal: str, subtitulo_crudo: str) -> str:
    """
    Limpia y compacta el subtítulo para la Línea 2 del botón de Circuito:
    - Elimina cualquier palabra o frase que ya esté en el título principal (ej. evita 'Dirtvision Millbridge' arriba y 'Millbridge' abajo).
    - Si hay bloques separados por '•', elige el bloque más conciso e informativo (Sede/Pista/Cancha/Ronda) que quepa en el botón.
    """
    if not subtitulo_crudo:
        return ""
    tit_norm = _normalizar(titulo_principal)
    bloques = [b.strip() for b in re.split(r'\s*[•|–—-]\s*', subtitulo_crudo) if b.strip()]
    bloques_limpios = []
    for b in bloques:
        # Quitar sufijos de estado/país largos después de coma si el bloque supera 24 caracteres (ej. 'Millbridge Speedway, Salisbury, NC' -> 'Millbridge Speedway')
        if "," in b and len(b) > 22:
            b = b.split(",")[0].strip()
        b_norm = _normalizar(b)
        if not b_norm or b_norm == tit_norm:
            continue
        if b_norm in tit_norm and len(bloques) > 1:
            continue
        bloques_limpios.append(formatear_titulo_inteligente(b))

    if not bloques_limpios:
        return ""
    # Preferir el bloque que mencione explícitamente cancha, pista, speedway, ronda o sesión
    for bl in bloques_limpios:
        if PAT_CANCHA_Y_SESION.search(bl) and len(bl) <= 28:
            return bl
    return bloques_limpios[0][:30].strip()

def extraer_metadatos_circuito(tit_raw: str, categoria: str, texto_crudo_completo: str = "") -> Dict[str, str]:
    s = re.sub(r'\[.*?\]|\(.*?\)', '', tit_raw)
    s = re.sub(r'\s*[-–—]\s*(?:Português|Portugues|English|Español|Spanish|HD|FHD|4K|Directo|En Vivo).*$', '', s, flags=re.I)
    s = re.sub(r'\s*\d{2,}>\s*', ' > ', s)
    bloques = [b.strip() for b in re.split(r'[:|>/]|\s+[-–—]\s+', s) if b.strip()]

    cat_patt = rf'^(?:{categoria}|Deportes?|Directo|En Vivo|Combate|Boxeo|Tenis|Golf|Motor|Ciclismo)(?!\s+en\s+pista|\s+en\s+ruta)\s*[:\-–—]?\s*'

    idx_duelo = -1
    duelo_str = ''
    for i, b in enumerate(bloques):
        if re.search(r'\b(?:vs\.?|contra|v\.)\b', b, re.I):
            idx_duelo = i
            m_vs = re.split(r'\s+(?:vs\.?|contra|v\.)\s+', b, flags=re.I)
            if len(m_vs) == 2:
                p1 = re.sub(cat_patt, '', m_vs[0], flags=re.I).strip()
                p2 = m_vs[1].strip()
                duelo_str = f'{formatear_titulo_inteligente(p1)} vs. {formatear_titulo_inteligente(p2)}'
            else:
                duelo_str = formatear_titulo_inteligente(b)
            break

    torneo = ''
    sede = ''
    bloques_restantes = [b for i, b in enumerate(bloques) if i != idx_duelo]

    for b in bloques_restantes:
        b_clean = re.sub(cat_patt, '', b, flags=re.I).strip()
        b_clean = formatear_titulo_inteligente(b_clean)
        if not b_clean:
            continue
        m_ses = PAT_CANCHA_Y_SESION.search(b_clean)
        if m_ses and torneo:
            sede = b_clean if not sede else f'{sede} • {b_clean}'
        elif m_ses and not torneo:
            m_ses_esp = re.search(r'\b(Sesi[oó]n\s+(?:Matinal|Matutina|Vespertina|Nocturna|Diurna|\d+)|Etapa\s+\d+|Clasificaci[oó]n|Pr[aá]ctica\s*\d*|Octavos\s+de\s+final|Cuartos\s+de\s+final|Semifinal(?:es)?)\b', b_clean, re.I)
            if m_ses_esp and m_ses_esp.start() > 5:
                parte_torneo = b_clean[:m_ses_esp.start()].strip(' -–—,:•')
                parte_sede = b_clean[m_ses_esp.start():].strip(' -–—,:•')
                if parte_torneo:
                    torneo = parte_torneo
                if parte_sede:
                    sede = parte_sede
            else:
                torneo = b_clean
        else:
            if not torneo:
                torneo = b_clean
            elif not sede and b_clean.lower() != torneo.lower():
                sede = b_clean

    if not sede and texto_crudo_completo and texto_crudo_completo != tit_raw:
        primera_oracion = texto_crudo_completo.split(".")[0].strip()
        if primera_oracion and len(primera_oracion) <= 95 and PAT_CANCHA_Y_SESION.search(primera_oracion):
            if primera_oracion.lower() != (torneo or tit_raw).lower():
                sede = formatear_titulo_inteligente(primera_oracion)

    if not torneo and duelo_str:
        torneo = f'Velada de {categoria}' if categoria in ['Boxeo', 'MMA', 'Combate'] else f'{categoria} Profesional'
    elif not torneo:
        torneo = formatear_titulo_inteligente(tit_raw.strip())

    return {
        'torneo': torneo,
        'subtitulo': sede,
        'participantes': duelo_str
    }

def post_procesar_y_curar_eventos(eventos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    eventos_pre = []
    for ev in eventos:
        # BÓVEDA DIARIA: Si el evento ya está íntegramente curado y sellado hoy, no se altera
        if ev.get("curado_integro") is True:
            eventos_pre.append(ev)
            continue

        tit_original = limpiar_texto(ev.get("titulo", ""))
        torneo_original = limpiar_texto(ev.get("torneo", ""))
        cat_original = ev.get("categoria", "")
        tit_raw = ev.get("texto_original_crudo") or ev.get("titulo_original_crudo") or ev.get("titulo_original") or tit_original
        ev["titulo_original_crudo"] = tit_raw

        if es_texto_no_vivo_o_aplazado(tit_original) or es_estado_aplazado_o_repeticion(tit_raw):
            log.info("Descartando evento no-vivo / programa multicanal: '%s'", tit_original)
            continue

        # Preservar la categoría si ya fue auditada por AGY o API-Sports y es un deporte real
        cat_existente = ev.get("categoria", "")
        if cat_existente and cat_existente not in ["Otros Deportes", "Deportes", "Deportes en Vivo", ""]:
            categoria_real = cat_existente
        elif (ev.get("auditado_agy") or ev.get("contrastado_api")) and cat_existente:
            categoria_real = cat_existente
        else:
            categoria_real = deducir_categoria(tit_original, torneo_original, cat_original)
        ev["categoria"] = categoria_real

        if ev.get("auditado_agy") is True:
            ev["titulo"] = formatear_titulo_inteligente(ev.get("titulo", ""))
            ev["torneo"] = formatear_titulo_inteligente(ev.get("torneo", ""))
            # Si AGY dejó torneo genérico ('WTA 1000', 'ATP Masters 1000') pero título específico ('Wuhan Open'), usar el específico
            if _normalizar(ev["torneo"]) in TORNEOS_GENERICOS_CIRCUITO and ev["titulo"]:
                ev["torneo"] = re.sub(r'^(?:WTA\s*\d*|ATP\s*(?:Masters\s*)?\d*)\s+', '', ev["titulo"], flags=re.I).strip() or ev["titulo"]
                ev["titulo"] = ev["torneo"]
            if ev.get("tipo_evento") == "circuito" and categoria_real in DEPORTES_ESTRICTO_DUELO:
                log.info("Descartando falso circuito en deporte de duelo (%s): '%s'", categoria_real, ev["titulo"])
                continue
            eventos_pre.append(ev)
            continue

        tit_limpio, loc, vis, ronda, torneo_extra = desmontar_duelo(tit_original, categoria_real)
        if torneo_extra and (not torneo_original or torneo_original == tit_original or torneo_original.lower() in ["deportes", "deportes en vivo", "fútbol", "futbol"]):
            torneo_original = torneo_extra

        if not loc and ev.get("equipo_local"):
            loc = formatear_titulo_inteligente(limpiar_texto(ev.get("equipo_local", "")))
        if not vis and ev.get("equipo_visitante"):
            vis = formatear_titulo_inteligente(limpiar_texto(ev.get("equipo_visitante", "")))

        m_torneo_ronda = re.search(r"\b(Fase\s+de\s+grupos|Fase\s+Liga|Jornada\s+\d+|\d+[ªa]?\s+Jornada|Round\s+\d+|Cuartos\s+de\s+final|Semifinal(?:es)?|Final)\b", torneo_original, re.I)
        if m_torneo_ronda:
            if not ronda:
                ronda = m_torneo_ronda.group(1).strip()
            torneo_original = torneo_original[:m_torneo_ronda.start()].strip().rstrip(":-–— ")

        if categoria_real in DEPORTES_CIRCUITO and not (loc and vis):
            tipo_ev = "circuito"
            sub_previo = limpiar_texto(ev.get("subtitulo", ""))
            meta_c = extraer_metadatos_circuito(tit_original, categoria_real, texto_crudo_completo=tit_raw)
            tit_limpio = formatear_titulo_inteligente(meta_c["torneo"])
            if not torneo_original or _normalizar(torneo_original) in TORNEOS_GENERICOS_CIRCUITO:
                torneo_original = tit_limpio
            else:
                torneo_original = formatear_titulo_inteligente(torneo_original)
                # Evitar que torneo_original pegue la sede junto al nombre del torneo (ej. 'DIRTVISION Millbridge Speedway')
                if tit_limpio and tit_limpio.lower() in torneo_original.lower() and len(torneo_original) > len(tit_limpio) + 4:
                    resto_sede = torneo_original[len(tit_limpio):].strip(" -–—•|:")
                    torneo_original = tit_limpio
                    if resto_sede and not meta_c["subtitulo"]:
                        meta_c["subtitulo"] = resto_sede

            nuevo_sub = meta_c["subtitulo"]
            if not nuevo_sub and sub_previo and sub_previo.lower() not in [tit_limpio.lower(), categoria_real.lower()]:
                nuevo_sub = sub_previo
            ronda = nuevo_sub
            ev["subtitulo"] = nuevo_sub
            if meta_c["participantes"]:
                ev["referencia"] = meta_c["participantes"]
            loc = ""
            vis = ""
        elif loc and vis:
            tipo_ev = "duelo"
            tit_limpio = f"{loc} vs {vis}"
            torneo_original = formatear_titulo_inteligente(torneo_original)
        else:
            # REGLA UNIVERSAL: En deportes de duelo (Fútbol, Baloncesto, Béisbol, NFL, NHL, etc.),
            # si no hay equipo_local y equipo_visitante, se descarta por ser programa/carrusel/canal genérico.
            es_lineal = any(o in ["inyector_lineal", "mitv_colombia", "portada", "deporte", "rtve_teledeporte_oficial", "mundodeportivo_eurosport_1", "mundodeportivo_eurosport_2"] for o in ev.get("origenes", [])) or (ev.get("origen") == "inyector_lineal")
            if not es_lineal and categoria_real in DEPORTES_ESTRICTO_DUELO:
                log.info("Descartando evento sin duelo en deporte de equipos (%s): '%s'", categoria_real, tit_limpio)
                continue
            tipo_ev = ev.get("tipo_evento") or ("duelo" if (loc and vis) else "circuito")

        ev["titulo"] = tit_limpio
        ev["torneo"] = torneo_original or tit_limpio
        ev["categoria"] = categoria_real
        ev["tipo_evento"] = tipo_ev
        ev["equipo_local"] = loc
        ev["equipo_visitante"] = vis
        ev["ronda"] = ronda

        eventos_pre.append(ev)

    # Fusión inteligente de eventos idénticos (por entidad deportiva)
    FAMILIAS_LINEALES_EXCLUYENTES = [
        {"WIN SPORTS+", "WIN SPORTS"},
        {"ESPN PREMIUM ARGENTINA", "ESPN AR", "ESPN 2 AR", "ESPN 3 AR", "ESPN 4", "ESPN 5", "ESPN 6", "ESPN 7", "ESPN", "ESPN 2", "ESPN 3", "ESPN CO", "ESPN 2 CO", "ESPN 3 CO"},
        {"DSPORTS AR", "DSPORTS CO", "DSPORTS", "DSPORTS 2", "DSPORTS +"},
        {"EUROSPORT 1", "EUROSPORT 2"},
    ]

    def _conflicto_familia_lineal(canal_cand: str, canales_exist: set[str]) -> bool:
        if not canal_cand:
            return False
        for fam in FAMILIAS_LINEALES_EXCLUYENTES:
            if canal_cand in fam and (canales_exist & fam):
                return canal_cand not in canales_exist
        return False

    # Fusión inteligente de eventos idénticos (por entidad deportiva)
    eventos_fusionados = []
    for ev in eventos_pre:
        fusionado = False
        for exist in eventos_fusionados:
            if son_mismo_evento(ev, exist):
                ids_existentes = {f.get("id_xtream") for f in exist.get("fuentes", []) if f.get("id_xtream") is not None}
                canales_lin_exist = {f.get("canal_lineal") for f in exist.get("fuentes", []) if f.get("canal_lineal")}
                for f in ev.get("fuentes", []):
                    c_lin_nuevo = f.get("canal_lineal")
                    # Priorizar WIN SPORTS+ sobre WIN SPORTS si ambas señales emiten exactamente el mismo evento en vivo
                    if c_lin_nuevo == "WIN SPORTS+" and "WIN SPORTS" in canales_lin_exist:
                        exist["fuentes"] = [f for f in exist["fuentes"] if f.get("canal_lineal") != "WIN SPORTS"]
                        canales_lin_exist.remove("WIN SPORTS")
                    elif c_lin_nuevo == "WIN SPORTS" and "WIN SPORTS+" in canales_lin_exist:
                        continue

                    # Permitir sumar canales de distintas regiones (ej. Teledeporte + RCN + ESPN AR),
                    # pero bloquear mezclar dos canales distintos de la misma cadena (ej. ESPN AR + ESPN 3 AR)
                    if c_lin_nuevo and _conflicto_familia_lineal(c_lin_nuevo, canales_lin_exist):
                        continue
                    if f.get("id_xtream") not in ids_existentes and len(exist["fuentes"]) < 10:
                        exist["fuentes"].append(f)
                        ids_existentes.add(f.get("id_xtream"))
                        if c_lin_nuevo:
                            canales_lin_exist.add(c_lin_nuevo)

                # Ordenar fuentes: Primero los streams efímeros dedicados de Xtream (sin canal_lineal) y detrás los canales lineales de respaldo
                exist["fuentes"].sort(key=lambda x: 1 if x.get("canal_lineal") else 0)

                # Aglutinar origenes y canales candidatos para no perder ninguna referencia de transmision
                for orig in ev.get("origenes", []):
                    if orig not in exist.setdefault("origenes", []):
                        exist["origenes"].append(orig)
                for cand in ev.get("canales_candidatos", []):
                    if cand not in exist.setdefault("canales_candidatos", []):
                        exist["canales_candidatos"].append(cand)
                for menc in ev.get("canales_mencionados_fuente", []):
                    if menc not in exist.setdefault("canales_mencionados_fuente", []):
                        exist["canales_mencionados_fuente"].append(menc)

                if exist.get("curado_integro") is True:
                    fusionado = True
                    break

                if ev.get("subtitulo") and (not exist.get("subtitulo") or exist.get("subtitulo") in ["MLB", "Rugby", "Fútbol", "Futbol", exist.get("titulo")]):
                    exist["subtitulo"] = ev["subtitulo"]
                if ev.get("referencia") and (not exist.get("referencia") or exist.get("referencia") in ["MLB", "Rugby", "Fútbol", "Futbol", exist.get("titulo")]):
                    exist["referencia"] = ev["referencia"]
                if ev.get("ronda") and not exist.get("ronda"):
                    exist["ronda"] = ev["ronda"]

                if not exist.get("logo_local") and ev.get("logo_local"):
                    exist["logo_local"] = ev["logo_local"]
                if not exist.get("logo_visitante") and ev.get("logo_visitante"):
                    exist["logo_visitante"] = ev["logo_visitante"]
                if not exist.get("logo_torneo") and ev.get("logo_torneo"):
                    exist["logo_torneo"] = ev["logo_torneo"]
                if not exist.get("banner") and ev.get("banner"):
                    exist["banner"] = ev["banner"]
                if ev.get("contrastado_api"):
                    exist["contrastado_api"] = True
                if ev.get("auditado_agy"):
                    exist["auditado_agy"] = True
                if ev.get("curado_integro"):
                    exist["curado_integro"] = True
                if ev.get("canales_mencionados_fuente") and not exist.get("canales_mencionados_fuente"):
                    exist["canales_mencionados_fuente"] = ev["canales_mencionados_fuente"]

                for o in ev.get("origenes", []):
                    if o not in exist.get("origenes", []):
                        exist["origenes"].append(o)

                fusionado = True
                break

        if not fusionado:
            ev["fuentes"] = sorted(ev.get("fuentes", []), key=lambda x: 1 if x.get("canal_lineal") else 0)
            eventos_fusionados.append(ev)

    eventos_pulidos = []
    for ev in eventos_fusionados:
        if ev.get("curado_integro") is True:
            eventos_pulidos.append(ev)
            continue

        tit_limpio = formatear_titulo_inteligente(ev["titulo"])
        torneo_original = formatear_titulo_inteligente(ev["torneo"])
        if ev.get("auditado_agy") is True and ev.get("categoria") and ev.get("categoria") != "Otros Deportes":
            categoria_real = ev["categoria"]
        else:
            categoria_real = deducir_categoria(tit_limpio, torneo_original, ev.get("categoria", ""))
        ev["categoria"] = categoria_real
        loc = ev.get("equipo_local", "")
        vis = ev.get("equipo_visitante", "")
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
            # Sincronizar título y torneo en circuitos para que nunca quede un genérico 'WTA 1000' ocultando 'Wuhan Open'
            if _normalizar(torneo_original) in TORNEOS_GENERICOS_CIRCUITO and tit_limpio:
                tit_limpio = re.sub(r'^(?:WTA\s*\d*|ATP\s*(?:Masters\s*)?\d*)\s+', '', tit_limpio, flags=re.I).strip() or tit_limpio
                torneo_original = tit_limpio
            elif tit_limpio and len(torneo_original) > len(tit_limpio) + 4 and tit_limpio.lower() in torneo_original.lower():
                torneo_original = tit_limpio

            sub_actual = (ev.get("subtitulo") or ronda or "").strip()
            subtitulo_corto = compactar_subtitulo_boton_circuito(tit_limpio, sub_actual)
            subtitulo = subtitulo_corto
            ev["ronda"] = subtitulo_corto

            # En referencia (bajo el preview) conservar el detalle completo sin repetir exactamente el título
            if ref_existente and sub_actual and ref_existente.lower() != sub_actual.lower() and sub_actual.lower() not in ref_existente.lower():
                referencia = f"{sub_actual} • {ref_existente}"
            else:
                referencia = ref_existente or sub_actual or torneo_original

        ev["titulo"] = tit_limpio
        ev["torneo"] = torneo_original or tit_limpio

        torneo_busqueda_logo = f"{torneo_original} {tit_limpio}".strip()
        if categoria_real == "Tenis" and "WTA" in (ev.get("texto_original_crudo") or "").upper() and "WTA" not in torneo_busqueda_logo.upper():
            torneo_busqueda_logo = f"WTA {torneo_busqueda_logo}"

        logo_torneo = ev.get("logo_torneo")
        if not logo_torneo or not es_logo_compatible_con_categoria(logo_torneo, categoria_real):
            logo_torneo = resolver_logo_torneo(torneo_busqueda_logo, categoria_real, local=loc, visitante=vis)

        logo_loc = ev.get("logo_local")
        if loc and (not logo_loc or not es_logo_compatible_con_categoria(logo_loc, categoria_real)):
            logo_loc = resolver_logo_equipo(loc, categoria_real, torneo_original)

        logo_vis = ev.get("logo_visitante")
        if vis and (not logo_vis or not es_logo_compatible_con_categoria(logo_vis, categoria_real)):
            logo_vis = resolver_logo_equipo(vis, categoria_real, torneo_original)

        # Si la categoría actual era 'Otros Deportes' o genérica y se resolvió un escudo oficial, ascender la categoría
        if categoria_real in ["Otros Deportes", "Deportes", "Deportes en Vivo", ""]:
            # Verificar si el titulo o torneo ya contiene una disciplina explicita antes de sobreescribir por logo
            u_tit_comp = f"{ev.get('titulo', '')} {ev.get('torneo', '')}".upper()
            if any(k in u_tit_comp for k in ["REMO", "ROWING"]):
                categoria_real = "Remo"
                ev["categoria"] = "Remo"
            elif any(k in u_tit_comp for k in ["SNOOKER", "BILLAR"]):
                categoria_real = "Snooker"
                ev["categoria"] = "Snooker"
            elif any(k in u_tit_comp for k in ["CICLISMO", "CYCLING", "VUELTA", "TOUR", "GIRO"]):
                categoria_real = "Ciclismo"
                ev["categoria"] = "Ciclismo"
            else:
                dep_detectado = deducir_deporte_desde_logo(logo_loc) or deducir_deporte_desde_logo(logo_vis) or deducir_deporte_desde_logo(logo_torneo)
                if dep_detectado:
                    # Protección: No ascender a Balonmano si el texto menciona clubes de fútbol de élite o torneos de fútbol
                    if dep_detectado == "Balonmano" and any(k in u_tit_comp for k in ["PSG", "BARCELONA", "REAL MADRID", "CHAMPIONS", "FASE LIGA"]):
                        dep_detectado = "Fútbol"
                    categoria_real = dep_detectado
                    ev["categoria"] = dep_detectado
                    log.info("Categoría ascendida de 'Otros Deportes' a '%s' por escudo oficial: '%s'", dep_detectado, ev.get("titulo"))

        ev["subtitulo"] = subtitulo
        ev["referencia"] = referencia
        ev["logo_torneo"] = logo_torneo or ""
        ev["logo_local"] = logo_loc or ""
        ev["logo_visitante"] = logo_vis or ""
        ev["banner"] = ev["logo_torneo"]
        eventos_pulidos.append(ev)

    log.info("Pase de Curación Semántica completado: %d eventos procesados y deduplicados", len(eventos_pulidos))
    return eventos_pulidos