# -*- coding: utf-8 -*-
"""
Sanitizador Deportivo Inteligente:
- Extrae de forma limpia Duelos y Circuitos.
- No destruye fechas ni canchas delimitadas por barras.
- Clasifica deportes por patrones directos y términos comunes.
- Limpia prefijos de idioma (English:, Spanish:, etc.), numeración de diales (01, 02) y sufijos de audio/país.
- Aplica normalización tipográfica inteligente (Smart Title Case) respetando siglas oficiales.
- Preserva el 100% del texto crudo original en texto_original_crudo y titulo_original_crudo.
"""
from __future__ import annotations

import re
from typing import Dict, Any, Optional

DEPORTES_MAP = {
    'FÚTBOL AMERICANO': 'Fútbol Americano',
    'FUTBOL AMERICANO': 'Fútbol Americano',
    'F AMERICANO': 'Fútbol Americano',
    'COLLEGE FOOTBALL': 'Fútbol Americano',
    'NCAA FOOTBALL': 'Fútbol Americano',
    'NCAA FÚTBOL AMERICANO': 'Fútbol Americano',
    'NCAA FUTBOL AMERICANO': 'Fútbol Americano',
    'NFL': 'Fútbol Americano',
    'NCAAF': 'Fútbol Americano',
    'CFL': 'Fútbol Americano',
    'AFL': 'Fútbol Americano',
    'F AUSTRALIANO': 'Fútbol Americano',
    'WNBA': 'Baloncesto',
    'NBA': 'Baloncesto',
    'BASKETBALL': 'Baloncesto',
    'BALONCESTO': 'Baloncesto',
    'NHL': 'Hockey',
    'HOCKEY': 'Hockey',
    'MLB': 'Béisbol',
    'LMB': 'Béisbol',
    'LMP': 'Béisbol',
    'MEXICAN LEAGUE': 'Béisbol',
    'BASEBALL': 'Béisbol',
    'BÉISBOL': 'Béisbol',
    'ATP': 'Tenis',
    'WTA': 'Tenis',
    'TENNIS': 'Tenis',
    'TENIS': 'Tenis',
    'JAPAN OPEN': 'Tenis',
    'CHINA OPEN': 'Tenis',
    'WUHAN OPEN': 'Tenis',
    'SHANGHAI MASTERS': 'Tenis',
    'PADEL': 'Pádel',
    'PÁDEL': 'Pádel',
    'PREMIER PADEL': 'Pádel',
    'A1 PADEL': 'Pádel',
    'GOLF': 'Golf',
    'PGA': 'Golf',
    'LIV GOLF': 'Golf',
    'LIV': 'Golf',
    'DP WORLD': 'Golf',
    'EUROPEAN TOUR': 'Golf',
    'RUGBY': 'Rugby',
    'NPC': 'Rugby',
    'F1': 'Motor',
    'FORMULA 1': 'Motor',
    'FÓRMULA 1': 'Motor',
    'MOTOGP': 'Motor',
    'MOTO GP': 'Motor',
    'MOTO2': 'Motor',
    'MOTO3': 'Motor',
    'DIRTVISION': 'Motor',
    'NASCAR': 'Motor',
    'INDYCAR': 'Motor',
    'SPEEDWAY': 'Motor',
    'MMA': 'Combate',
    'UFC': 'Combate',
    'EFC': 'Combate',
    'BKFC': 'Combate',
    'BARE KNUCKLE': 'Combate',
    'BOXING': 'Combate',
    'BOXEO': 'Combate',
    'WWE': 'Combate',
    'SMACKDOWN': 'Combate',
    'RAW': 'Combate',
    'AEW': 'Combate',
    'TEJO': 'Tejo',
    'SNOOKER': 'Snooker',
    'CYCLING': 'Ciclismo',
    'CICLISMO': 'Ciclismo',
    'POLO': 'Polo',
    'EREDIVISIE': 'Fútbol',
    'LIDOM': 'Béisbol',
    'LIGA DOMINICANA': 'Béisbol',
    'SEGUNDA URUGUAY': 'Fútbol',
    'LIGA URUGUAYA': 'Fútbol',
    'LIGA HONDURAS': 'Fútbol',
    'HONDURAS': 'Fútbol',
    'KINGS LEAGUE': 'Fútbol',
    'QUEENS LEAGUE': 'Fútbol',
    'POWERCHAIR FOOTBALL': 'Fútbol',
    'POWERCHAIR': 'Fútbol',
    'PRIMEIRA LIGA': 'Fútbol',
    'SUPERLIG': 'Fútbol',
    'PRO LEAGUE': 'Fútbol',
    'EFL': 'Fútbol',
    'BETPLAY': 'Fútbol',
    'LALIGA': 'Fútbol',
    'PREMIER': 'Fútbol',
    'CHAMPIONS': 'Fútbol',
    'CONCACAF': 'Fútbol',
    'CONMEBOL': 'Fútbol',
    'UEFA': 'Fútbol',
    'SOCCER': 'Fútbol',
    'FUTBOL': 'Fútbol',
    'FÚTBOL': 'Fútbol',
    'MLS': 'Fútbol',
}

PATRONES_BASURA_PROGRAMAS = re.compile(
    r'\b(SHOW|EL SHOW|PREVIA|PREVIO|DEBATE|NOTICIAS|NOTICIERO|RESUMEN|CRONICA|CRONICAS|MAGAZINE|'
    r'TERTULIA|ESPECIAL|HIGHLIGHTS|RECAP|REVIEW|CONTENDER\s*SERIES|ULTIMATE\s*FIGHTER|COUNTDOWN|'
    r'REWIND|GOLES DE LA|CAMPE[OÓ]N\s*-\s*CARABAO|'
    r'AL\s+INSTANTE|SIMULCAST|MULTICANAL|COBERTURA\s+MULTICANAL|CARRUSEL|REDZONE|GOAL\s*SHOW|ZONA\s+MIXTA|SORTEO|'
    r'APLAZADO|POSTERGADO|SUSPENDIDO|CANCELADO|REPROGRAMADO|DIFERIDO|REPETICI[OÓ]N|REPLAY|COMPACTO|REVIVE)\b',
    re.I
)

PATRONES_ESTADO_CANCELADO_O_DIFERIDO = re.compile(
    r'\b(APLAZADO|POSTERGADO|SUSPENDIDO|CANCELADO|REPROGRAMADO|DIFERIDO|EN\s+DIFERIDO|REPETICI[OÓ]N|REPLAY|REVIVE)\b',
    re.I
)

def es_texto_no_vivo_o_aplazado(texto: str) -> bool:
    """Evalúa títulos cortos de streams o eventos para descartar programas, carruseles, previas o aplazados."""
    if not texto:
        return False
    return bool(PATRONES_BASURA_PROGRAMAS.search(texto))

def es_estado_aplazado_o_repeticion(texto_largo: str) -> bool:
    """Evalúa textos crudos o sinopsis largas buscando únicamente estados de partido aplazado/suspendido/diferido (sin falsos positivos por la palabra 'previa' en descripciones de mi.tv)."""
    if not texto_largo:
        return False
    return bool(PATRONES_ESTADO_CANCELADO_O_DIFERIDO.search(texto_largo))

ACRONIMOS_MAYUSCULAS = {
    "UEFA", "FIFA", "CONMEBOL", "CONCACAF", "AFC", "CAF", "OFC", "ATP", "WTA", "ITF",
    "NBA", "WNBA", "NFL", "NHL", "MLB", "LMB", "LMP", "MLS", "USL", "NCAA", "NCAAF",
    "UFC", "WWE", "AEW", "MMA", "BKFC", "PFL", "EFC", "WST", "UCI", "FIP", "EHF",
    "ACB", "ASOBAL", "EFL", "FA", "DFB", "PSG", "CSKA", "LDU", "IDV", "DIM", "RCN",
    "ESPN", "TYC", "TNT", "DAZN", "F1", "GP", "WRC", "PGA", "LIV", "FC", "CF", "CD",
    "SC", "AC", "AS", "RB", "SK", "FK", "BK", "IF", "UD", "SD", "CA", "DC", "LA",
    "NY", "SJ", "UC", "UCF", "LSU", "USC", "BYU", "SMU", "TCU", "UNAM", "UANL",
    "U17", "U19", "U20", "U21", "U23", "II", "III", "IV", "VI", "USA", "UK"
}

PALABRAS_MINUSCULAS_INTERMEDIAS = {"de", "del", "la", "las", "los", "el", "en", "y", "e", "a", "al", "por", "con", "vs", "vs."}

def formatear_titulo_inteligente(texto: str) -> str:
    """
    Normalizador tipográfico universal:
    - Elimina numeración de diales de eventos IPTV (ej. 'DIRTVISION 01' -> 'Dirtvision', 'MLS 04' -> 'MLS').
    - Convierte palabras largas en MAYÚSCULAS sostenidas (>= 4 letras no siglas) a Title Case ('DIRTVISION' -> 'Dirtvision').
    - Preserva intactos acrónimos oficiales ('UEFA', 'CONMEBOL', 'WTA', 'ATP', 'NBA', 'MLS', 'UFC').
    """
    if not texto:
        return ""
    s = re.sub(r'\b([A-Za-z]{3,})\s+0?\d{1,2}\b', r'\1', texto.strip())
    s = re.sub(r'^(?:EVENTO|EVENTS?|CANAL|STREAM|FEED|CH)\s*\d+\s*[:|\-–—]?\s*', '', s, flags=re.I).strip()

    palabras = s.split()
    resultado = []
    for idx, p in enumerate(palabras):
        pref = ""
        suf = ""
        m_match = re.match(r'^([^\wÁÉÍÓÚÜÑáéíóúüñ]*)(.*?)([^\wÁÉÍÓÚÜÑáéíóúüñ]*)$', p)
        if m_match:
            pref, nucleo, suf = m_match.group(1), m_match.group(2), m_match.group(3)
        else:
            nucleo = p

        if not nucleo:
            resultado.append(p)
            continue

        nucleo_up = nucleo.upper()
        if nucleo_up in ACRONIMOS_MAYUSCULAS:
            nuevo_nucleo = nucleo_up
        elif idx > 0 and nucleo.lower() in PALABRAS_MINUSCULAS_INTERMEDIAS:
            nuevo_nucleo = nucleo.lower()
        elif nucleo.isupper() and len(nucleo) >= 4:
            nuevo_nucleo = nucleo.capitalize()
        elif nucleo.islower() and len(nucleo) >= 3:
            nuevo_nucleo = nucleo.capitalize()
        else:
            nuevo_nucleo = nucleo

        resultado.append(f"{pref}{nuevo_nucleo}{suf}")

    return " ".join(resultado).strip()

PATRONES_TORNEO_CONOCIDOS = [
    (r'\bPREMIER\s*PADEL\b', 'Premier Padel'),
    (r'\bBUNNINGS\s*NPC\b|\bNPC\b', 'Bunnings NPC Rugby'),
    (r'\bSUPER\s*RUGBY\b', 'Super Rugby Pacific'),
    (r'\bAFC\s*CHAMPIONS\s*LEAGUE(?:\s*ELITE|\s*TWO)?\b', 'AFC Champions League'),
    (r'\bCAF\s*CHAMPIONS\s*LEAGUE\b', 'CAF Champions League'),
    (r'\bCONCACAF\s*CHAMPIONS\s*(?:CUP|LEAGUE)\b', 'CONCACAF Champions Cup'),
    (r'\bCONCACAF\s*CARIBBEAN\s*(?:CLUB|CUP)\b', 'CONCACAF Caribbean Cup'),
    (r'\bCANADIAN\s*PREMIER\s*LEAGUE\b', 'Canadian Premier League'),
    (r'\bEHF\s*CHAMPIONS\s*LEAGUE\b', 'EHF Champions League'),
    (r'\bUEFA\s*NATIONS\s*LEAGUE\b', 'UEFA Nations League'),
    (r'\bUEFA\s*YOUTH\s*LEAGUE\b|\bYOUTH\s*LEAGUE\b', 'UEFA Youth League'),
    (r'\bUEFA\s*WOMEN\'?S\s*CHAMPIONS\s*LEAGUE\b|\bUCL\s*FEM\b', "UEFA Women's Champions League"),
    (r'(?<!\bAFC\s)(?<!\bCAF\s)(?<!\bCONCACAF\s)(?<!\bEHF\s)\b(?:UEFA\s*CHAMPIONS\s*LEAGUE|CHAMPIONS\s*LEAGUE|UCL)\b', 'UEFA Champions League'),
    (r'\bEUROPA\s*LEAGUE\b|\bUEL\b', 'UEFA Europa League'),
    (r'\bCONCACAF\s*NATIONS\s*LEAGUE\b', 'CONCACAF Nations League'),
    (r'\bCONMEBOL\s*SUDAMERICANA\b|\bSUDAMERICANA\b', 'CONMEBOL Sudamericana'),
    (r'\bCONMEBOL\s*LIBERTADORES\b|\bLIBERTADORES\b', 'CONMEBOL Libertadores'),
    (r'\bLIGA\s*BETPLAY\b', 'Liga BetPlay DIMAYOR'),
    (r'\bTORNEO\s*BETPLAY\b', 'Torneo BetPlay DIMAYOR'),
    (r'\bCOPA\s*BETPLAY\b', 'Copa BetPlay DIMAYOR'),
    (r'\bCOPA\s*ARGENTINA\b', 'Copa Argentina'),
    (r'\bLIGA\s*PROFESIONAL\b', 'Liga Profesional Argentina'),
    (r'\bWNBA\b', 'WNBA'),
    (r'\bNBA\b', 'NBA'),
    (r'\bNFL\b', 'NFL'),
    (r'\bNHL\b', 'NHL'),
    (r'\bMLB\b', 'MLB'),
    (r'\bLMB\b', 'Liga Mexicana de Béisbol'),
    (r'\bMLS\b', 'Major League Soccer'),
    (r'\bNCAA\s*MEN\'?S\s*SOCCER\b', "NCAA Men's Soccer"),
    (r'\bNCAA\s*WOMEN\'?S\s*SOCCER\b', "NCAA Women's Soccer"),
    (r'\bNCAA\s*F[ÚU]TBOL\s*AMERICANO\b|\bNCAA\s*FOOTBALL\b', "NCAA Fútbol Americano"),
    (r'\bNCAA\s*BASKETBALL\b|\bNCAA\s*BASKET\b', "NCAA Baloncesto"),
    (r'\bNCAA\b', 'NCAA'),
    (r'\bROLEX\s*SHANGHAI\s*MASTERS\b|\bSHANGHAI\s*MASTERS\b', 'Rolex Shanghai Masters'),
    (r'\bWUHAN\s*OPEN\b', 'Wuhan Open'),
    (r'\bUFC\b', 'UFC'),
    (r'\bWWE\b', 'WWE'),
]

SUBTITULOS_TRADUCCION = {
    'FREE PRACTICE 1': 'Práctica Libre 1',
    'FREE PRACTICE 2': 'Práctica Libre 2',
    'FREE PRACTICE 3': 'Práctica Libre 3',
    'QUALIFYING': 'Clasificación',
    'SPRINT': 'Carrera Sprint',
    'RACE': 'Carrera Principal',
    'CENTRE COURT': 'Cancha Central',
    'CENTER COURT': 'Cancha Central',
    'MAIN COURT': 'Pista Principal',
    'SECONDARY COURT': 'Pista Secundaria',
    'COURT 1': 'Court 1',
    'COURT 2': 'Court 2',
    'COURT 3': 'Court 3',
    'FEATURED GROUPS': 'Grupos Destacados',
    'MAIN CARD': 'Cartelera Principal',
    'PRELIMS': 'Preliminares',
    'ROUND 1': 'Ronda 1', 'ROUND 2': 'Ronda 2', 'ROUND 3': 'Ronda 3', 'ROUND 4': 'Ronda 4',
    'DAY 1': 'Día 1', 'DAY 2': 'Día 2', 'DAY 3': 'Día 3', 'DAY 4': 'Día 4',
    'FINAL': 'Final', 'FINALS': 'Finales', 'SEMIFINAL': 'Semifinales',
    'OCTAVOS DE FINAL': 'Octavos de Final',
    'CUARTOS DE FINAL': 'Cuartos de Final',
    'SEGUNDA RONDA': 'Segunda Ronda',
    'PRIMERA RONDA': 'Primera Ronda',
}

def limpiar_prefijos_idioma(s: str) -> str:
    s = re.sub(r'^(?:English|Spanish|Español|Portugues|Português|French|Italian|German|HD|FHD|4K|OP\d+|VIP|LIVE|DIRECTO|EN VIVO)\s*[:|-]\s*', '', s, flags=re.I)
    s = re.sub(r'\b(?:English|Spanish|Español|Portugues|Português)\s*[:|-]\s*', '', s, flags=re.I)
    s = re.sub(r'\[(?:ENG|ESP|POR|FRA|ITA|GER|HD|FHD|4K)\]', '', s, flags=re.I)
    s = re.sub(r'\((?:ENG|ESP|POR|FRA|ITA|GER|HD|FHD|4K)\)', '', s, flags=re.I)
    s = re.sub(r'\s*[-–—|]\s*(?:Português|English|Español|Spanish|HD|FHD|4K|EN|ES)\s*$', '', s, flags=re.I)
    return s.strip()

def limpiar_sufijo_pais_club(s: str) -> str:
    return re.sub(r'\s*\((?:Arg|Bra|Nor|Ale|Col|Esp|Eng|Fra|Ita|Mex|Usa|Por|Chi|Uru|Per|Ecu|Par|Bol|Ven)\)', '', s, flags=re.I).strip()

def extraer_contexto_y_equipos(cadena_pre_vs: str):
    partes = [p.strip() for p in re.split(r'[-–—,]\s*', cadena_pre_vs) if p.strip()]
    if len(partes) > 1:
        local = partes[-1]
        contexto = ' - '.join(partes[:-1])
        return local, contexto
    return cadena_pre_vs, ''

def limpiar_fragmento(s: str) -> str:
    s = re.sub(r'[\u2010-\u2015\u2022\u2212*~#◘■♦►●▼▲]+', ' ', s)
    s = limpiar_prefijos_idioma(s)
    s = re.sub(r'\b(En español|Español|Spanish|OP\d+|FHD|HD|SD|4K|ES|EN|LIVE|EN VIVO|ET|PT|CT)\b', '', s, flags=re.I)
    s = re.sub(r'\b([A-Za-z]{3,})\s+0?\d{1,2}\b', r'\1', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip(' -:|◘■♦►●▼▲')

def sanitizar_evento_crudo(nombre_stream: str, grupo_stream: str = '') -> Optional[Dict[str, Any]]:
    """
    Parsea de manera inteligente y rigurosa el stream de IPTV.
    Entrega campos operativos completamente limpios sin perder el texto original crudo.
    """
    if PATRONES_BASURA_PROGRAMAS.search(nombre_stream):
        return None

    texto_completo = f"{nombre_stream} {grupo_stream}"

    # 1. Detección de Deporte Precisa
    deporte = 'Otros Deportes'
    for k in sorted(DEPORTES_MAP.keys(), key=len, reverse=True):
        if re.search(rf'\b{re.escape(k)}\b', texto_completo.upper()):
            deporte = DEPORTES_MAP[k]
            break

    if deporte == 'Otros Deportes':
        if any(k in texto_completo.upper() for k in [
            'FC', 'CF', 'DEPORTIVO', 'ATLETICO', 'SOCCER', 'BALOMPIE', 'LENS', 'SPORTING',
            'VIKING', 'BAYERN', 'BOCA', 'VASCO', 'INTER', 'BRUGGE', 'ARSENAL', 'LILLE',
            'GALATASARAY', 'BARCELONA', 'NAPOLI', 'VILLARREAL', 'SLAVIA', 'SABAH', 'U19'
        ]):
            deporte = 'Fútbol'

    # 2. Detección de Torneo por Patrones Conocidos
    torneo_detectado = ''
    for pat, nom_torneo in PATRONES_TORNEO_CONOCIDOS:
        if re.search(pat, texto_completo, re.I):
            torneo_detectado = nom_torneo
            break

    # 3. Limpieza de fechas (DD/MM o MM.DD) y prefijos de idioma
    nombre_sin_fechas = re.sub(r'\b[0-3]?[0-9][/.][0-3]?[0-9]\b', '', nombre_stream)
    nombre_limpio = limpiar_prefijos_idioma(nombre_sin_fechas)

    # 4. Dividir por separadores de bloques (incluyendo | y ◘)
    bloques = [limpiar_fragmento(b) for b in re.split(r'[\u2010-\u2015|◘■♦►●▼▲]+', nombre_limpio) if b.strip()]

    bloques_utiles = []
    for b in bloques:
        b_clean = re.sub(r'^[0-2]?[0-9][:.:][0-5][0-9]\s*(?:AM|PM)?\s*(?:ET|PT|CT)?\s*', '', b, flags=re.I).strip(' -:.')
        if b_clean and not re.match(r'^(?:[0-2]?[0-9][:.:][0-5][0-9]\s*(?:AM|PM)?|\d{1,2})$', b_clean, re.I):
            bloques_utiles.append(b_clean)

    GENERICOS_PREFIJO = {
        'MOTOR', 'DEPORTES', 'LIVE', 'DIRECTO', 'EVENTO', 'EN VIVO', 'SPORTS', 'SPORT',
        'FUTBOL', 'SOCCER', 'ATP', 'WTA', 'PGA', 'PGA TOUR', 'BOXEO', 'COMBATE', 'UFC',
        'TENIS', 'TENNIS', 'GOLF', 'BASKET', 'BALONCESTO', 'BASEBALL', 'BEISBOL'
    }
    if len(bloques_utiles) > 1 and bloques_utiles[0].upper() in GENERICOS_PREFIJO:
        bloques_utiles = bloques_utiles[1:]

    if not bloques_utiles:
        bloques_utiles = [limpiar_fragmento(nombre_limpio)]

    # 5. Localizar si es un Duelo
    bloque_duelo = None
    bloques_contexto = []
    patron_duelo = r'\s+(?:vs\.?|versus|\bv\.?\b|@)\s+'
    if deporte in ['Boxeo', 'Combate', 'MMA'] or any(k in texto_completo.upper() for k in ['BOXEO', 'BOXING', 'UFC', 'COMBATE', 'BKFC']):
        patron_duelo = r'\s+(?:vs\.?|versus|\bv\.?\b|@|-|\u2013)\s+'

    for b in bloques_utiles:
        if re.search(patron_duelo, b, re.I):
            if not bloque_duelo:
                bloque_duelo = b
            else:
                bloques_contexto.append(b)
        else:
            bloques_contexto.append(b)

    if bloque_duelo:
        tipo = 'duelo'
        partes = re.split(patron_duelo, bloque_duelo, flags=re.I)
        pre_local = re.sub(r'^[0-2]?[0-9][:.:][0-5][0-9]\s*(?:AM|PM)?\s*', '', partes[0], flags=re.I).strip(' -:.')
        local_raw, contexto_duelo = extraer_contexto_y_equipos(pre_local)
        visitante_raw = partes[1].strip(' -:.')

        local = limpiar_sufijo_pais_club(local_raw)
        visitante = limpiar_sufijo_pais_club(visitante_raw)

        local = re.sub(rf'\b({deporte}|Rugby|Golf|MMA|Padel|Baseball|Soccer|WNBA|NBA|NHL|MLB|NFL)\b.*$', '', local, flags=re.I).strip(' -:.')
        visitante = re.sub(rf'\b({deporte}|Rugby|Golf|MMA|Padel|Baseball|Soccer|WNBA|NBA|NHL|MLB|NFL)\b.*$', '', visitante, flags=re.I).strip(' -:.')

        local = formatear_titulo_inteligente(local)
        visitante = formatear_titulo_inteligente(visitante)

        titulo = f"{local} vs. {visitante}"
        torneo_final = formatear_titulo_inteligente(torneo_detectado or (bloques_contexto[0] if bloques_contexto else deporte))
        subtitulo = formatear_titulo_inteligente(contexto_duelo or torneo_final)
        referencia = subtitulo
    else:
        # REGLA UNIVERSAL: En deportes estrictamente de equipos/duelo, si no hay duelo (Local vs Visitante),
        # es un programa multicanal, carrusel o canal comodín (ej. 'UCL al instante') -> descartar
        if deporte in ['Fútbol', 'Baloncesto', 'Béisbol', 'Hockey', 'Fútbol Americano', 'Balonmano', 'Rugby']:
            return None

        tipo = 'circuito'
        local = ''
        visitante = ''
        segmento_principal = bloques_utiles[0]

        if 'GP' in segmento_principal.upper() or any(k in texto_completo.upper() for k in ['FORMULA 1', 'FÓRMULA 1', 'MOTOGP', 'MOTO GP']):
            m_gp = re.search(r'(GP\s+[A-Za-zÁÉÍÓÚáéíóúñ]+)', segmento_principal, re.I)
            nom_gp = m_gp.group(1).title() if m_gp else 'Gran Premio'
            es_f1 = any(k in texto_completo.upper() for k in ['F1', 'FORMULA 1', 'FÓRMULA 1'])
            torneo_final = 'Fórmula 1' if es_f1 else 'MotoGP'
            titulo = f"{'F1' if es_f1 else 'MotoGP'}: {nom_gp}"

            sesion = 'Sesión en Vivo'
            seg_upper = segmento_principal.upper()
            for en_s, es_s in SUBTITULOS_TRADUCCION.items():
                if en_s in seg_upper:
                    sesion = es_s
                    break
            subtitulo = sesion
            referencia = sesion
        else:
            partes_circ = []
            for bu in bloques_utiles:
                for p in re.split(r'\s*(?:[-–—/]|•)\s*', bu):
                    p_str = p.strip()
                    if p_str and p_str.upper() not in GENERICOS_PREFIJO:
                        partes_circ.append(formatear_titulo_inteligente(p_str))

            torneo = formatear_titulo_inteligente(torneo_detectado) if torneo_detectado else ''
            subtitulo = ''
            referencia = ''

            for p in partes_circ:
                if not torneo:
                    torneo = p
                elif p.lower() != torneo.lower() and not subtitulo:
                    subtitulo = p
                elif p.lower() not in [torneo.lower(), subtitulo.lower()] and not referencia:
                    referencia = p

            titulo_boton = re.sub(r'^(?:ATP World Tour Masters \d+|ATP \d+|WTA \d+)\s*[-–—]?\s*', '', torneo, flags=re.I).strip()
            titulo = formatear_titulo_inteligente(titulo_boton or torneo or deporte)
            torneo_final = formatear_titulo_inteligente(torneo_detectado or titulo)
            subtitulo = formatear_titulo_inteligente(subtitulo) if subtitulo and subtitulo.lower() != titulo.lower() else ''
            referencia = formatear_titulo_inteligente(referencia or subtitulo or torneo_final)

    return {
        'titulo_original_crudo': nombre_stream.strip(),
        'texto_original_crudo': texto_completo.strip(),
        'tipo': tipo,
        'deporte': deporte,
        'titulo': titulo,
        'local': local,
        'visitante': visitante,
        'torneo': torneo_final,
        'subtitulo': subtitulo,
        'referencia': referencia,
    }