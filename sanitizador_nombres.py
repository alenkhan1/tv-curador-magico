# -*- coding: utf-8 -*-
"""
Sanitizador Quirúrgico de Metadatos Deportivos:
Convierte títulos desastrosos de listas IPTV en entidades limpias:
- Separa duelos (vs, v, @) de eventos de circuito (padel, golf, f1, mma, tenis).
- Elimina horas pegadas, fechas, artefactos (◘, •, |, ||, *), calidades (FHD, 4K) y sufijos basura.
- Extrae el deporte exacto y traduce subtítulos comunes (Centre Court -> Cancha Central, Round 1 -> Ronda 1).
"""
from __future__ import annotations

import re
from typing import Dict, Any, List

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

PATRONES_TORNEO_CONOCIDOS = [
    (r'\bPREMIER\s*PADEL\b', 'Premier Padel'),
    (r'\bBUNNINGS\s*NPC\b|\bNPC\b', 'Bunnings NPC Rugby'),
    (r'\bSUPER\s*RUGBY\b', 'Super Rugby Pacific'),
    (r'\bUEFA\s*NATIONS\s*LEAGUE\b', 'UEFA Nations League'),
    (r'\bUEFA\s*WOMEN\'?S\s*CHAMPIONS\s*LEAGUE\b', 'Champions League Femenina'),
    (r'\bCHAMPIONS\s*LEAGUE\b', 'UEFA Champions League'),
    (r'\bEUROPA\s*LEAGUE\b', 'UEFA Europa League'),
    (r'\bCONCACAF\s*NATIONS\s*LEAGUE\b', 'CONCACAF Nations League'),
    (r'\bLIGA\s*BETPLAY\b', 'Liga BetPlay DIMAYOR'),
    (r'\bTORNEO\s*BETPLAY\b', 'Torneo BetPlay DIMAYOR'),
    (r'\bCOPA\s*ARGENTINA\b', 'Copa Argentina'),
    (r'\bLIGA\s*PROFESIONAL\b', 'Liga Profesional Argentina'),
    (r'\bEXTREME\s*FIGHTING\s*CHAMPIONSHIP\b|\bEFC\b', 'Extreme Fighting Championship'),
    (r'\bALFRED\s*DUNHILL\s*LINKS\b', 'Alfred Dunhill Links Championship'),
    (r'\bBANK\s*OF\s*UTAH\b', 'Bank of Utah Championship'),
    (r'\bUSL\s*CHAMPIONSHIP\b', 'USL Championship'),
    (r'\bMLB\b', 'MLB'),
    (r'\bNBA\b', 'NBA'),
    (r'\bNHL\b', 'NHL'),
    (r'\bNFL\b', 'NFL'),
]

def limpiar_texto_basico(s: str) -> str:
    s = re.sub(r'[◘•*~#]+', ' ', s)
    s = re.sub(r'\b(En español|Español|OP\d+|FHD|HD|SD|4K|ES|EN|LIVE|EN VIVO)\b', '', s, flags=re.I)
    s = re.sub(r'\s+', ' ', s)
    return s.strip(' -:|')

def sanitizar_evento_crudo(nombre_stream: str, grupo_stream: str = '') -> Dict[str, Any]:
    """
    Parsea un nombre sucio de stream y retorna estructura canónica limpia y profesional.
    """
    texto_completo = f'{nombre_stream} {grupo_stream}'

    # 1. Detección de Deporte
    deporte = 'Otros Deportes'
    for k, v in DEPORTES_MAP.items():
        if re.search(rf'\b{k}\b', texto_completo.upper()):
            deporte = v
            break
    if deporte == 'Otros Deportes' and any(k in texto_completo.upper() for k in ['LEAGUE', 'FC', 'CF', 'CLUB', 'DEPORTIVO', 'ATLETICO', 'VS', 'CHAMPIONS']):
        deporte = 'Fútbol'

    # 2. Detección de Torneo por Patrones Conocidos
    torneo_detectado = ''
    for pat, nom_torneo in PATRONES_TORNEO_CONOCIDOS:
        if re.search(pat, texto_completo, re.I):
            torneo_detectado = nom_torneo
            break

    # 3. Separar por pipes | para aislar segmentos
    partes_pipe = [limpiar_texto_basico(p) for p in nombre_stream.split('|') if p.strip()]

    partes_utiles = []
    for p in partes_pipe:
        p_clean = re.sub(r'^[0-2]?[0-9][:.:][0-5][0-9]\s*(?:[0-3]?[0-9][/-][0-1]?[0-9])?\s*', '', p).strip()
        if p_clean and not re.match(r'^[0-2]?[0-9][:.:][0-5][0-9]$', p_clean):
            partes_utiles.append(p_clean)

    if not partes_utiles:
        partes_utiles = [limpiar_texto_basico(nombre_stream)]

    segmento_principal = partes_utiles[0]
    segmentos_extra = partes_utiles[1:] if len(partes_utiles) > 1 else []

    if not torneo_detectado:
        for seg in segmentos_extra:
            if seg.upper() in DEPORTES_MAP or seg.capitalize() == deporte:
                continue
            if len(seg) > 3 and not re.match(r'^\d+$', seg):
                torneo_detectado = seg
                break

    # 4. Clasificar Duelo vs Circuito
    m_duelo = re.split(r'\s+(?:vs\.?|versus|\bv\b|@)\s+', segmento_principal, flags=re.I)
    if len(m_duelo) == 2 and m_duelo[0].strip() and m_duelo[1].strip():
        tipo = 'duelo'
        local = re.sub(r'^[0-2]?[0-9][:.:][0-5][0-9]\s*', '', m_duelo[0]).strip(' -:|')
        visitante = m_duelo[1].strip(' -:|')

        local = re.sub(rf'\b({deporte}|Rugby|Golf|MMA|Padel|Baseball|Soccer)\b.*$', '', local, flags=re.I).strip(' -:|')
        visitante = re.sub(rf'\b({deporte}|Rugby|Golf|MMA|Padel|Baseball|Soccer)\b.*$', '', visitante, flags=re.I).strip(' -:|')

        titulo = f'{local} vs {visitante}'
        torneo_final = torneo_detectado or (deporte if deporte != 'Otros Deportes' else 'En Directo')
        subtitulo = f'{torneo_final}'
    else:
        tipo = 'circuito'
        local = ''
        visitante = ''
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
            sub = deporte

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
