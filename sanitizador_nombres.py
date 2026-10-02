# -*- coding: utf-8 -*-
"""
Sanitizador Deportivo Inteligente:
- Extrae de forma limpia Duelos y Circuitos.
- No destruye fechas ni canchas delimitadas por barras.
- Clasifica deportes por patrones directos (WNBA -> Baloncesto, NFL -> Fútbol Americano, NHL -> Hockey, ATP/WTA -> Tenis).
- Traduce sesiones de F1, rondas y pistas.
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
    'NFL': 'Fútbol Americano',
    'NCAAF': 'Fútbol Americano',
    'NHL': 'Hockey',
    'HOCKEY': 'Hockey',
    'MLB': 'Béisbol',
    'BASEBALL': 'Béisbol',
    'BÉISBOL': 'Béisbol',
    'ATP': 'Tenis',
    'WTA': 'Tenis',
    'TENNIS': 'Tenis',
    'TENIS': 'Tenis',
    'JAPAN OPEN': 'Tenis',
    'CHINA OPEN': 'Tenis',
    'PADEL': 'Pádel',
    'PÁDEL': 'Pádel',
    'GOLF': 'Golf',
    'PGA': 'Golf',
    'LIV': 'Golf',
    'DP WORLD': 'Golf',
    'RUGBY': 'Rugby',
    'NPC': 'Rugby',
    'F1': 'Fórmula 1',
    'FORMULA 1': 'Fórmula 1',
    'MOTOGP': 'MotoGP',
    'MMA': 'MMA',
    'UFC': 'MMA',
    'EFC': 'MMA',
    'BOXING': 'Boxeo',
    'BOXEO': 'Boxeo',
    'SNOOKER': 'Snooker',
    'CYCLING': 'Ciclismo',
    'CICLISMO': 'Ciclismo',
    'POLO': 'Polo',
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
    r'\b(SHOW|EL SHOW|PREVIA|PREVIO|DEBATE|NOTICIAS|NOTICIERO|RESUMEN|CRONICA|CRONICAS|MAGAZINE|TERTULIA|ESPECIAL|HIGHLIGHTS|RECAP|REVIEW|CONTENDER\s*SERIES|ULTIMATE\s*FIGHTER|COUNTDOWN|REWIND)\b',
    re.I
)

PATRONES_TORNEO_CONOCIDOS = [
    (r'\bPREMIER\s*PADEL\b', 'Premier Padel'),
    (r'\bBUNNINGS\s*NPC\b|\bNPC\b', 'Bunnings NPC Rugby'),
    (r'\bSUPER\s*RUGBY\b', 'Super Rugby Pacific'),
    (r'\bUEFA\s*NATIONS\s*LEAGUE\b', 'UEFA Nations League'),
    (r'\bUEFA\s*WOMEN\'?S\s*CHAMPIONS\s*LEAGUE\b|\bUCL\s*FEM\b', "UEFA Women's Champions League"),
    (r'\bCHAMPIONS\s*LEAGUE\b|\bUCL\b', 'UEFA Champions League'),
    (r'\bEUROPA\s*LEAGUE\b|\bUEL\b', 'UEFA Europa League'),
    (r'\bCONCACAF\s*NATIONS\s*LEAGUE\b', 'CONCACAF Nations League'),
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
    (r'\bMLS\b', 'MLS'),
    (r'\bNCAA\s*MEN\'?S\s*SOCCER\b', "NCAA Men's Soccer"),
    (r'\bNCAA\s*WOMEN\'?S\s*SOCCER\b', "NCAA Women's Soccer"),
    (r'\bNCAA\s*F[ÚU]TBOL\s*AMERICANO\b|\bNCAA\s*FOOTBALL\b', "NCAA Fútbol Americano"),
    (r'\bNCAA\s*BASKETBALL\b|\bNCAA\s*BASKET\b', "NCAA Baloncesto"),
    (r'\bNCAA\b', 'NCAA'),
    (r'\bEXTREME\s*FIGHTING\s*CHAMPIONSHIP\b|\bEFC\b', 'Extreme Fighting Championship'),
    (r'\bALFRED\s*DUNHILL\s*LINKS\b', 'Alfred Dunhill Links Championship'),
    (r'\bBANK\s*OF\s*UTAH\b', 'Bank of Utah Championship'),
    (r'\bUSL\s*CHAMPIONSHIP\b', 'USL Championship'),
    (r'\bJAPAN\s*OPEN\b', 'ATP Tokio (Japan Open)'),
    (r'\bCHINA\s*OPEN\b', 'China Open'),
    (r'\bAMISTOSO\b', 'Amistoso Internacional'),
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

def limpiar_fragmento(s: str) -> str:
    s = re.sub(r'[◘•*~#]+', ' ', s)
    s = re.sub(r'\b(En español|Español|Spanish|OP\d+|FHD|HD|SD|4K|ES|EN|LIVE|EN VIVO)\b', '', s, flags=re.I)
    s = re.sub(r'\s+', ' ', s)
    return s.strip(' -:|')

def sanitizar_evento_crudo(nombre_stream: str, grupo_stream: str = '') -> Optional[Dict[str, Any]]:
    """
    Parsea de manera inteligente y rigurosa el stream de IPTV.
    """
    texto_completo = f'{nombre_stream} {grupo_stream}'

    # 1. Filtro estricto: Descartar programas de estudio / revistas / noticias
    if PATRONES_BASURA_PROGRAMAS.search(nombre_stream):
        return None

    # 2. Detección de Deporte Precisa (sin forzar fútbol por 'vs')
    deporte = 'Otros Deportes'
    for k in sorted(DEPORTES_MAP.keys(), key=len, reverse=True):
        if re.search(rf'\b{re.escape(k)}\b', texto_completo.upper()):
            deporte = DEPORTES_MAP[k]
            break

    # Si aún no tiene deporte, buscar indicios inequívocos
    if deporte == 'Otros Deportes':
        if any(k in texto_completo.upper() for k in ['FC', 'CF', 'DEPORTIVO', 'ATLETICO', 'SOCCER', 'BALOMPIE']):
            deporte = 'Fútbol'

    # 3. Detección de Torneo por Patrones Conocidos
    torneo_detectado = ''
    for pat, nom_torneo in PATRONES_TORNEO_CONOCIDOS:
        if re.search(pat, texto_completo, re.I):
            torneo_detectado = nom_torneo
            break

    # 4. Limpieza PREVIA: Eliminar fechas pegadas (ej. 01/10 o 1/10) para no romper en "01"
    nombre_sin_fechas = re.sub(r'\b[0-3]?[0-9]/[0-1]?[0-9]\b', '', nombre_stream)

    # 5. Dividir ÚNICAMENTE por ◘ o | (NUNCA por / para preservar canchas y torneos)
    bloques = [limpiar_fragmento(b) for b in re.split(r'[◘|]+', nombre_sin_fechas) if b.strip()]

    # Filtrar bloques que solo son horas (ej. 17:55, 07:00 PM) o números residuales
    bloques_utiles = []
    for b in bloques:
        b_clean = re.sub(r'^[0-2]?[0-9][:.:][0-5][0-9]\s*(?:AM|PM)?\s*', '', b, flags=re.I).strip(' -:.')
        if b_clean and not re.match(r'^(?:[0-2]?[0-9][:.:][0-5][0-9]\s*(?:AM|PM)?|\d{1,2})$', b_clean, re.I):
            bloques_utiles.append(b_clean)

    if not bloques_utiles:
        bloques_utiles = [limpiar_fragmento(nombre_sin_fechas)]

    # 6. Localizar el bloque del enfrentamiento (el que tiene vs, v, @ o - en deportes de combate)
    bloque_duelo = None
    bloques_contexto = []
    patron_duelo = r'\s+(?:vs\.?|versus|\bv\b|@)\s+'
    if deporte in ['Boxeo', 'Combate', 'MMA'] or any(k in texto_completo.upper() for k in ['BOXEO', 'BOXING', 'UFC', 'COMBATE']):
        patron_duelo = r'\s+(?:vs\.?|versus|\bv\b|@|-|–)\s+'

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
        local = re.sub(r'^[0-2]?[0-9][:.:][0-5][0-9]\s*(?:AM|PM)?\s*', '', partes[0], flags=re.I).strip(' -:.')
        visitante = partes[1].strip(' -:.')

        # Limpiar palabras residuales de deporte en local/vis
        local = re.sub(rf'\b({deporte}|Rugby|Golf|MMA|Padel|Baseball|Soccer|WNBA|NBA|NHL|MLB|NFL)\b.*$', '', local, flags=re.I).strip(' -:.')
        visitante = re.sub(rf'\b({deporte}|Rugby|Golf|MMA|Padel|Baseball|Soccer|WNBA|NBA|NHL|MLB|NFL)\b.*$', '', visitante, flags=re.I).strip(' -:.')

        titulo = f'{local} vs {visitante}'
        torneo_final = torneo_detectado or (bloques_contexto[0] if bloques_contexto else deporte)
        subtitulo = torneo_final
    else:
        tipo = 'circuito'
        local = ''
        visitante = ''
        segmento_principal = bloques_utiles[0]
        
        # Casos especiales de Fórmula 1: "GP Japón - Free Practice 1"
        if 'GP' in segmento_principal.upper() or deporte == 'Fórmula 1':
            m_gp = re.search(r'(GP\s+[A-Za-zÁÉÍÓÚáéíóúñ]+)', segmento_principal, re.I)
            nom_gp = m_gp.group(1).title() if m_gp else 'Gran Premio'
            torneo_final = 'Fórmula 1'
            titulo = f'F1: {nom_gp}'
            
            # Buscar sesión
            sesion = 'Sesión en Vivo'
            seg_upper = segmento_principal.upper()
            for en_s, es_s in SUBTITULOS_TRADUCCION.items():
                if en_s in seg_upper:
                    sesion = es_s
                    break
            subtitulo = sesion
        else:
            # Tenis / Golf / Padel
            sub = 'Directo'
            t_base = torneo_detectado or segmento_principal

            # Subdividir cancha o ronda si viene con slash / o guión
            partes_sub = re.split(r'[/:\-–]', segmento_principal)
            partes_sub = [p.strip() for p in partes_sub if p.strip()]

            if len(partes_sub) >= 2:
                sub_candidato = partes_sub[-1]
                sub_upper = sub_candidato.upper()
                for en_t, es_t in SUBTITULOS_TRADUCCION.items():
                    if en_t in sub_upper:
                        sub = es_t
                        break
                if sub == 'Directo':
                    sub = sub_candidato

            torneo_final = torneo_detectado or t_base
            titulo = torneo_final
            subtitulo = sub

    return {
        'tipo': tipo,
        'deporte': deporte,
        'titulo': titulo,
        'local': local,
        'visitante': visitante,
        'torneo': torneo_final,
        'subtitulo': subtitulo,
    }
