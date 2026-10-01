# -*- coding: utf-8 -*-
"""
Sanitizador Quirúrgico de Metadatos Deportivos:
- Descarte de programas que no son deporte en vivo (Shows, Noticias, Debates).
- Partición por bloques delimitados por ◘ y | para aislar enfrentamientos y torneos limpios.
"""
from __future__ import annotations

import re
from typing import Dict, Any, Optional

DEPORTES_MAP = {
    'PADEL': 'Pádel', 'PÁDEL': 'Pádel',
    'GOLF': 'Golf',
    'RUGBY': 'Rugby',
    'MMA': 'MMA', 'UFC': 'MMA', 'EFC': 'MMA', 'FIGHTING': 'MMA', 'BELLATOR': 'MMA',
    'BOXING': 'Boxeo', 'BOXEO': 'Boxeo',
    'MLB': 'Béisbol', 'BASEBALL': 'Béisbol',
    'NBA': 'Baloncesto', 'BASKETBALL': 'Baloncesto',
    'TENNIS': 'Tenis', 'TENIS': 'Tenis',
    'F1': 'Fórmula 1', 'FORMULA 1': 'Fórmula 1', 'MOTOGP': 'MotoGP',
    'HOCKEY': 'Hockey', 'NHL': 'Hockey',
    'SNOOKER': 'Snooker', 'BILLAR': 'Snooker',
    'CYCLING': 'Ciclismo', 'CICLISMO': 'Ciclismo',
    'FUTBOL': 'Fútbol', 'SOCCER': 'Fútbol', 'UEFA': 'Fútbol', 'CONCACAF': 'Fútbol',
    'CONMEBOL': 'Fútbol', 'BETPLAY': 'Fútbol', 'LALIGA': 'Fútbol', 'PREMIER': 'Fútbol'
}

PATRONES_BASURA_PROGRAMAS = re.compile(
    r'\b(SHOW|EL SHOW|PREVIA|PREVIO|DEBATE|NOTICIAS|NOTICIERO|RESUMEN|CRONICA|CRONICAS|MAGAZINE|TERTULIA|ESPECIAL|HIGHLIGHTS|RECAP|REVIEW)\b',
    re.I
)

PATRONES_TORNEO_CONOCIDOS = [
    (r'\bPREMIER\s*PADEL\b', 'Premier Padel'),
    (r'\bBUNNINGS\s*NPC\b|\bNPC\b', 'Bunnings NPC Rugby'),
    (r'\bSUPER\s*RUGBY\b', 'Super Rugby Pacific'),
    (r'\bUEFA\s*NATIONS\s*LEAGUE\b', 'UEFA Nations League'),
    (r'\bUEFA\s*WOMEN\'?S\s*CHAMPIONS\s*LEAGUE\b|\bUCL\s*FEM\b', 'Champions League Femenina'),
    (r'\bCHAMPIONS\s*LEAGUE\b|\bUCL\b', 'UEFA Champions League'),
    (r'\bEUROPA\s*LEAGUE\b|\bUEL\b', 'UEFA Europa League'),
    (r'\bCONCACAF\s*NATIONS\s*LEAGUE\b', 'CONCACAF Nations League'),
    (r'\bLIGA\s*BETPLAY\b', 'Liga BetPlay DIMAYOR'),
    (r'\bTORNEO\s*BETPLAY\b', 'Torneo BetPlay DIMAYOR'),
    (r'\bCOPA\s*BETPLAY\b', 'Copa BetPlay DIMAYOR'),
    (r'\bCOPA\s*ARGENTINA\b', 'Copa Argentina'),
    (r'\bLIGA\s*PROFESIONAL\b', 'Liga Profesional Argentina'),
    (r'\bNCAA\b', 'NCAA'),
    (r'\bEXTREME\s*FIGHTING\s*CHAMPIONSHIP\b|\bEFC\b', 'Extreme Fighting Championship'),
    (r'\bALFRED\s*DUNHILL\s*LINKS\b', 'Alfred Dunhill Links Championship'),
    (r'\bBANK\s*OF\s*UTAH\b', 'Bank of Utah Championship'),
    (r'\bUSL\s*CHAMPIONSHIP\b', 'USL Championship'),
    (r'\bMLB\b', 'MLB'),
    (r'\bNBA\b', 'NBA'),
    (r'\bNHL\b', 'NHL'),
    (r'\bNFL\b', 'NFL'),
    (r'\bAMISTOSO\b', 'Amistoso Internacional'),
]

SUBTITULOS_TRADUCCION = {
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
}

def limpiar_fragmento(s: str) -> str:
    s = re.sub(r'[◘•*~#]+', ' ', s)
    s = re.sub(r'\b(En español|Español|Spanish|OP\d+|FHD|HD|SD|4K|ES|EN|LIVE|EN VIVO)\b', '', s, flags=re.I)
    s = re.sub(r'\s+', ' ', s)
    return s.strip(' -:|')

def sanitizar_evento_crudo(nombre_stream: str, grupo_stream: str = '') -> Optional[Dict[str, Any]]:
    """
    Parsea un nombre sucio de stream dividiendo en bloques independientes.
    Retorna None si se trata de un programa de estudio (no en vivo).
    """
    texto_completo = f'{nombre_stream} {grupo_stream}'

    # 1. Filtro estricto: Descartar programas de estudio / revistas / noticias
    if PATRONES_BASURA_PROGRAMAS.search(nombre_stream):
        return None

    # 2. Detección de Deporte
    deporte = 'Otros Deportes'
    for k, v in DEPORTES_MAP.items():
        if re.search(rf'\b{k}\b', texto_completo.upper()):
            deporte = v
            break
    if deporte == 'Otros Deportes' and any(k in texto_completo.upper() for k in ['LEAGUE', 'FC', 'CF', 'CLUB', 'DEPORTIVO', 'ATLETICO', 'VS', 'CHAMPIONS']):
        deporte = 'Fútbol'

    # 3. Detección de Torneo por Patrones Conocidos
    torneo_detectado = ''
    for pat, nom_torneo in PATRONES_TORNEO_CONOCIDOS:
        if re.search(pat, texto_completo, re.I):
            torneo_detectado = nom_torneo
            break

    # 4. Dividir por bloques delimitados por ◘, |, o //
    bloques = [limpiar_fragmento(b) for b in re.split(r'[◘|/]+', nombre_stream) if b.strip()]

    # Filtrar bloques que solo son horas o fechas
    bloques_utiles = []
    for b in bloques:
        b_clean = re.sub(r'^[0-2]?[0-9][:.:][0-5][0-9]\s*(?:AM|PM)?\s*(?:[0-3]?[0-9][/-][0-1]?[0-9])?\s*', '', b, flags=re.I).strip()
        if b_clean and not re.match(r'^[0-2]?[0-9][:.:][0-5][0-9]\s*(?:AM|PM)?$', b_clean, re.I):
            bloques_utiles.append(b_clean)

    if not bloques_utiles:
        bloques_utiles = [limpiar_fragmento(nombre_stream)]

    # 5. Localizar el bloque del enfrentamiento (el que tiene vs, v, @)
    bloque_duelo = None
    bloques_contexto = []
    for b in bloques_utiles:
        if re.search(r'\s+(?:vs\.?|versus|\bv\b|@)\s+', b, re.I):
            if not bloque_duelo:
                bloque_duelo = b
            else:
                bloques_contexto.append(b)
        else:
            bloques_contexto.append(b)

    if bloque_duelo:
        tipo = 'duelo'
        partes = re.split(r'\s+(?:vs\.?|versus|\bv\b|@)\s+', bloque_duelo, flags=re.I)
        local = re.sub(r'^[0-2]?[0-9][:.:][0-5][0-9]\s*(?:AM|PM)?\s*', '', partes[0], flags=re.I).strip(' -:.')
        visitante = partes[1].strip(' -:.')

        # Limpiar palabras residuales de deporte o etiquetas en local/vis
        local = re.sub(rf'\b({deporte}|Rugby|Golf|MMA|Padel|Baseball|Soccer)\b.*$', '', local, flags=re.I).strip(' -:.')
        visitante = re.sub(rf'\b({deporte}|Rugby|Golf|MMA|Padel|Baseball|Soccer)\b.*$', '', visitante, flags=re.I).strip(' -:.')

        titulo = f'{local} vs {visitante}'
        torneo_final = torneo_detectado or (bloques_contexto[0] if bloques_contexto else deporte)
        subtitulo = torneo_final
    else:
        tipo = 'circuito'
        local = ''
        visitante = ''
        segmento_principal = bloques_utiles[0]
        
        # Subdividir torneo y pista/ronda si vienen con dos puntos o guión
        partes_sub = re.split(r'[:\-–]', segmento_principal)
        partes_sub = [p.strip() for p in partes_sub if p.strip()]

        if len(partes_sub) >= 3:
            t_base = f'{partes_sub[0]} {partes_sub[1]}'
            sub = partes_sub[2]
        elif len(partes_sub) == 2:
            t_base = partes_sub[0]
            sub = partes_sub[1]
        else:
            t_base = torneo_detectado or segmento_principal
            sub = bloques_contexto[0] if bloques_contexto else deporte

        sub_upper = sub.upper()
        for en_t, es_t in SUBTITULOS_TRADUCCION.items():
            if en_t in sub_upper:
                sub = es_t
                break

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
