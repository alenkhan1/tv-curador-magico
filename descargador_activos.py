# -*- coding: utf-8 -*-
"""
Descargador e Ingestor de Activos Oficiales (Asset Ingestion Worker).
Descarga y valida los logos oficiales de los principales torneos deportivos del mundo,
guardándolos en assets/logos/torneos/ y generando el catálogo maestro local.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('ingestor_activos')

BASE_DIR = Path(__file__).resolve().parent
DIR_TORNEOS = BASE_DIR / 'assets' / 'logos' / 'torneos'
DIR_TORNEOS.mkdir(parents=True, exist_ok=True)

URL_BASE_GITHUB = 'https://raw.githubusercontent.com/alenkhan1/tv-curador-magico/main/assets/logos/torneos'

TORNEOS_CONFIG = [
    # FIFA / Amistosos Internacionales
    {
        'slug': 'fifa.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/FIFA_logo_without_slogan.svg?width=400',
        'aliases': ['FIFA', 'AMISTOSO', 'AMISTOSOS', 'AMISTOSO INTERNACIONAL', 'AMISTOSOS INTERNACIONALES', 'FECHA FIFA', 'PARTIDOS AMISTOSOS', 'INTERNATIONAL FRIENDLIES', 'FRIENDLY MATCH']
    },
    # NCAA / Deportes Universitarios USA
    {
        'slug': 'ncaa.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/NCAA_logo.svg?width=400',
        'aliases': ['NCAA', 'NCAA FUTBOL AMERICANO', 'NCAA FOOTBALL', 'NCAA MEN S SOCCER', 'NCAA WOMEN S SOCCER', 'COLLEGE FOOTBALL', 'NCAA BASKETBALL']
    },
    # Segundas Divisiones Relevantes
    {
        'slug': 'liga_expansion_mx.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/2h0yz01615330546.png',
        'aliases': ['LIGA DE EXPANSION MX', 'LIGA EXPANSION MX', 'EXPANSION MX', 'EXPANSION', 'SEGUNDA MEXICO']
    },
    {
        'slug': 'efl_championship.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/ty5a681688770169.png',
        'aliases': ['EFL CHAMPIONSHIP', 'CHAMPIONSHIP', 'SKY BET CHAMPIONSHIP', 'SEGUNDA DIVISION INGLATERRA']
    },
    {
        'slug': 'serie_b.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/uf5kph1598011132.png',
        'aliases': ['SERIE B', 'SERIE BKT', 'ITALIAN SERIE B', 'SEGUNDA ITALIA']
    },
    {
        'slug': 'bundesliga_2.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/hl40981534764789.png',
        'aliases': ['2. BUNDESLIGA', '2 BUNDESLIGA', 'GERMAN 2. BUNDESLIGA', 'SEGUNDA BUNDESLIGA']
    },
    {
        'slug': 'primera_nacional.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/u5tbaw1762466392.png',
        'aliases': ['PRIMERA NACIONAL', 'PRIMERA B NACIONAL', 'B NACIONAL', 'SEGUNDA DIVISION ARGENTINA']
    },
    {
        'slug': 'torneo_betplay.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/l2h1ao1615832028.png',
        'aliases': ['TORNEO BETPLAY', 'TORNEO BETPLAY DIMAYOR', 'PRIMERA B COLOMBIA', 'SEGUNDA COLOMBIA']
    },
    # Ftbol Femenino
    {
        'slug': 'liga_mx_femenil.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/Liga_MX_Femenil.png?width=400',
        'aliases': ['LIGA MX FEMENIL', 'LIGA BBVA MX FEMENIL', 'MEXICO FEMENIL']
    },
    {
        'slug': 'liga_f.png',
        'url': 'https://en.wikipedia.org/wiki/Special:FilePath/Ligafwomen.svg?width=400',
        'aliases': ['LIGA F', 'PRIMERA DIVISION FEMENINA', 'LIGA FEMENINA ESPANA']
    },

    # Fútbol Internacional
    {
        'slug': 'uefa_nations_league.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/cwsp321698386224.png',
        'aliases': ['UEFA NATIONS LEAGUE', 'NATIONS LEAGUE', 'LIGA DE NACIONES UEFA']
    },
    {
        'slug': 'uefa_champions_league.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/facv1u1742998896.png',
        'aliases': ['UEFA CHAMPIONS LEAGUE', 'CHAMPIONS LEAGUE', 'CHAMPIONS LEAGUE FEMENINA', 'UEFA WOMEN S CHAMPIONS LEAGUE']
    },
    {
        'slug': 'uefa_europa_league.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/mlsr7d1718774547.png',
        'aliases': ['UEFA EUROPA LEAGUE', 'EUROPA LEAGUE']
    },
    {
        'slug': 'uefa_conference_league.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/UEFA_Europa_Conference_League_logo.svg?width=400',
        'aliases': ['UEFA CONFERENCE LEAGUE', 'CONFERENCE LEAGUE', 'UECL']
    },
    {
        'slug': 'uefa_euro.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/bivzlu1635869135.png',
        'aliases': ['UEFA EURO', 'EUROCOPA']
    },
    {
        'slug': 'copa_america.png',
        'url': 'https://en.wikipedia.org/wiki/Special:FilePath/2024_Copa_Am%C3%A9rica_logo.svg?width=400',
        'aliases': ['COPA AMERICA', 'CONMEBOL COPA AMERICA']
    },
    {
        'slug': 'copa_libertadores.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/9shr931685425181.png',
        'aliases': ['COPA LIBERTADORES', 'LIBERTADORES', 'CONMEBOL LIBERTADORES']
    },
    {
        'slug': 'copa_sudamericana.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/Conmebol-sudamericana.svg?width=400',
        'aliases': ['COPA SUDAMERICANA', 'SUDAMERICANA', 'CONMEBOL SUDAMERICANA']
    },
    {
        'slug': 'recopa_sudamericana.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/Conmebol-recopa.svg?width=400',
        'aliases': ['RECOPA SUDAMERICANA', 'RECOPA']
    },
    {
        'slug': 'concacaf_nations_league.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/Concacaf_Nations_League_logo.svg?width=400',
        'aliases': ['CONCACAF NATIONS LEAGUE', 'LIGA DE NACIONES CONCACAF']
    },
    {
        'slug': 'copa_oro.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/Concacaf_Gold_Cup_2021.svg?width=400',
        'aliases': ['COPA ORO', 'GOLD CUP', 'CONCACAF GOLD CUP']
    },
    {
        'slug': 'copa_africana.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/2025_Africa_Cup_of_Nations_logo.svg?width=400',
        'aliases': ['COPA AFRICANA', 'AFRICA CUP OF NATIONS', 'AFCON', 'CLASIFICACION COPA AFRICANA']
    },
    {
        'slug': 'fifa_club_world_cup.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/yesbil1731546197.png',
        'aliases': ['FIFA CLUB WORLD CUP', 'MUNDIAL DE CLUBES']
    },
    # Ligas Nacionales
    {
        'slug': 'laliga.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/ja4it51687628717.png',
        'aliases': ['LALIGA', 'LA LIGA', 'LALIGA EA SPORTS', 'PRIMERA DIVISION ESPANA']
    },
    {
        'slug': 'laliga_hypermotion.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/r7u6821688425700.png',
        'aliases': ['LALIGA HYPERMOTION', 'LALIGA SMARTBANK', 'SEGUNDA DIVISION ESPANA']
    },
    {
        'slug': 'copa_del_rey.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/2ikh3a1671782958.png',
        'aliases': ['COPA DEL REY']
    },
    {
        'slug': 'supercopa_espana.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/sp4q7d1641378531.png',
        'aliases': ['SUPERCOPA DE ESPANA', 'SUPERCOPA ESPANA']
    },
    {
        'slug': 'premier_league.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/gasy9d1737743125.png',
        'aliases': ['PREMIER LEAGUE', 'ENGLISH PREMIER LEAGUE', 'EPL']
    },
    {
        'slug': 'fa_cup.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/vk7isd1598802862.png',
        'aliases': ['FA CUP', 'THE EMIRATES FA CUP']
    },
    {
        'slug': 'carabao_cup.png',
        'url': 'https://en.wikipedia.org/wiki/Special:FilePath/EFL_(Carabao)_Cup_Logo.svg?width=400',
        'aliases': ['CARABAO CUP', 'EFL CUP']
    },
    {
        'slug': 'serie_a.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/67q3q21679951383.png',
        'aliases': ['SERIE A', 'SERIE A ENILIVE', 'SERIE A TIM']
    },
    {
        'slug': 'coppa_italia.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/hrm1vo1692679408.png',
        'aliases': ['COPPA ITALIA']
    },
    {
        'slug': 'bundesliga.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/teqh1b1679952008.png',
        'aliases': ['BUNDESLIGA', 'GERMAN BUNDESLIGA']
    },
    {
        'slug': 'dfb_pokal.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/tlczpm1780941454.png',
        'aliases': ['DFB POKAL', 'COPA DE ALEMANIA']
    },
    {
        'slug': 'ligue_1.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/9f7z9d1742983155.png',
        'aliases': ['LIGUE 1', 'LIGUE 1 MCDONALDS', 'FRENCH LIGUE 1']
    },
    {
        'slug': 'liga_betplay.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/sdz1351580833297.png',
        'aliases': ['LIGA BETPLAY', 'LIGA BETPLAY DIMAYOR', 'BETPLAY', 'COLOMBIAN LIGA DIMAYOR']
    },
    {
        'slug': 'copa_betplay.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/w0pay41645457852.png',
        'aliases': ['COPA BETPLAY', 'COPA BETPLAY DIMAYOR', 'COPA COLOMBIA']
    },
    {
        'slug': 'torneo_betano.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/rk9xhx1768238251.png',
        'aliases': ['TORNEO BETANO', 'LIGA PROFESIONAL', 'LIGA PROFESIONAL DE FUTBOL', 'PRIMERA DIVISION ARGENTINA', 'COPA DE LA LIGA', 'ARGENTINIAN PRIMERA DIVISION']
    },
    {
        'slug': 'copa_argentina.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/welbig1655924428.png',
        'aliases': ['COPA ARGENTINA']
    },
    {
        'slug': 'mls.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/MLS_crest_logo_RGB_gradient.svg?width=400',
        'aliases': ['MLS', 'MAJOR LEAGUE SOCCER']
    },
    {
        'slug': 'liga_mx.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/mav5rx1686157960.png',
        'aliases': ['LIGA MX', 'LIGA BBVA MX', 'MEXICAN LIGA MX']
    },
    {
        'slug': 'primeira_liga.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/3tgdke1782689102.png',
        'aliases': ['PRIMEIRA LIGA', 'LIGA PORTUGAL', 'LIGA BETCLIC']
    },
    {
        'slug': 'brasileirao.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/lywv7t1766787179.png',
        'aliases': ['BRASILEIRAO', 'SERIE A BRASIL', 'CAMPEONATO BRASILEIRO', 'BRAZILIAN SERIE A']
    },
    # Baloncesto
    {
        'slug': 'nba.png',
        'url': 'https://en.wikipedia.org/wiki/Special:FilePath/National_Basketball_Association_logo.svg?width=400',
        'aliases': ['NBA', 'NATIONAL BASKETBALL ASSOCIATION']
    },
    {
        'slug': 'wnba.png',
        'url': 'https://en.wikipedia.org/wiki/Special:FilePath/WNBA_logo.svg?width=400',
        'aliases': ['WNBA']
    },
    {
        'slug': 'liga_endesa.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/4n3h6z1572778356.png',
        'aliases': ['LIGA ENDESA', 'ACB', 'LIGA ACB', 'SPANISH LIGA ACB']
    },
    {
        'slug': 'euroleague.png',
        'url': 'https://en.wikipedia.org/wiki/Special:FilePath/Euroleague_Basketball_logo.svg?width=400',
        'aliases': ['EUROLEAGUE', 'EUROLIGA', 'TURKISH AIRLINES EUROLEAGUE']
    },
    # Motor
    {
        'slug': 'f1.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/F1.svg?width=400',
        'aliases': ['FORMULA 1', 'F1']
    },
    {
        'slug': 'motogp.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/Moto_Gp_logo.svg?width=400',
        'aliases': ['MOTOGP', 'MOTO GP', 'MOTO2', 'MOTO3']
    },
    # Tenis / Pádel
    {
        'slug': 'atp.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/ATP_Tour_logo.svg?width=400',
        'aliases': ['ATP', 'ATP TOUR', 'ATP 1000', 'ATP 500', 'ATP 250', 'MASTERS 1000', 'JAPAN OPEN', 'CHINA OPEN']
    },
    {
        'slug': 'wta.png',
        'url': 'https://r2.thesportsdb.com/images/media/league/badge/bddhun1768230678.png',
        'aliases': ['WTA', 'WTA TOUR', 'WTA 1000', 'WTA 500', 'WTA 250']
    },
    {
        'slug': 'premier_padel.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/Premier_Padel_logo.png',
        'aliases': ['PREMIER PADEL', 'PADEL']
    },
    # Balonmano
    {
        'slug': 'liga_asobal.png',
        'url': 'https://fr.wikipedia.org/wiki/Special:FilePath/Liga_Asobal_2016_logo.svg?width=400',
        'aliases': ['ASOBAL', 'LIGA ASOBAL', 'LIGA NEXUS ENERGIA ASOBAL', 'LIGA PLENITUDE ASOBAL']
    },
    # Ciclismo
    {
        'slug': 'tour_de_france.png',
        'url': 'https://en.wikipedia.org/wiki/Special:FilePath/Tour_de_France_logo.svg?width=400',
        'aliases': ['TOUR DE FRANCE', 'LE TOUR']
    },
    {
        'slug': 'uci.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/Union_Cycliste_Internationale_logo.svg?width=400',
        'aliases': ['UCI', 'UCI WORLD TOUR', 'CICLISMO']
    },
    # Deportes Americanos / Contacto / Otros
    {
        'slug': 'mlb.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/Major_League_Baseball_logo.svg?width=400',
        'aliases': ['MLB', 'MAJOR LEAGUE BASEBALL', 'BEISBOL']
    },
    {
        'slug': 'nfl.png',
        'url': 'https://en.wikipedia.org/wiki/Special:FilePath/National_Football_League_logo.svg?width=400',
        'aliases': ['NFL', 'NATIONAL FOOTBALL LEAGUE', 'FUTBOL AMERICANO']
    },
    {
        'slug': 'nhl.png',
        'url': 'https://en.wikipedia.org/wiki/Special:FilePath/05_NHL_Shield.svg?width=400',
        'aliases': ['NHL', 'NATIONAL HOCKEY LEAGUE', 'HOCKEY']
    },
    {
        'slug': 'ufc.png',
        'url': 'https://commons.wikimedia.org/wiki/Special:FilePath/UFC_logo.svg?width=400',
        'aliases': ['UFC', 'ULTIMATE FIGHTING CHAMPIONSHIP', 'COMBATE', 'MMA']
    },
    {
        'slug': 'wst.png',
        'url': 'https://en.wikipedia.org/wiki/Special:FilePath/World_Snooker_Tour_logo.svg?width=400',
        'aliases': ['WST', 'WORLD SNOOKER TOUR', 'SNOOKER', 'SHENZHEN OPEN']
    },
    {
        'slug': 'pga_tour.png',
        'url': 'https://en.wikipedia.org/wiki/Special:FilePath/PGA_Tour_logo.svg?width=400',
        'aliases': ['PGA TOUR', 'PGA', 'GOLF']
    }
]

def descargar_logo(url: str, destino: Path) -> bool:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            if resp.status == 200:
                contenido = resp.read()
                if len(contenido) > 500:
                    destino.write_bytes(contenido)
                    return True
    except Exception as e:
        log.warning('Fallo descargando %s: %s', url, e)
    return False

def ejecutar_ingesta():
    log.info('=== INICIANDO INGESTA DE LOGOS OFICIALES DE TORNEOS ===')
    exitos = 0
    catalogo_maestro = {}

    for cfg in TORNEOS_CONFIG:
        slug = cfg['slug']
        destino = DIR_TORNEOS / slug
        url_descarga = cfg['url']
        url_publica = f'{URL_BASE_GITHUB}/{slug}'

        if not destino.exists() or destino.stat().st_size < 500:
            log.info('Descargando %s ...', slug)
            ok = descargar_logo(url_descarga, destino)
            if ok:
                log.info('OK -> %s (%d bytes)', slug, destino.stat().st_size)
                exitos += 1
            else:
                log.error('ERROR al descargar %s', slug)
            time.sleep(0.3)
        else:
            log.info('Ya existe en disco: %s (%d bytes)', slug, destino.stat().st_size)
            exitos += 1

        for alias in cfg['aliases']:
            alias_norm = unicodedata.normalize('NFKD', alias).encode('ASCII', 'ignore').decode('utf-8').upper().strip()
            catalogo_maestro[alias_norm] = url_publica

    archivo_catalogo = BASE_DIR / 'catalogo_maestro_torneos.json'
    archivo_catalogo.write_text(json.dumps(catalogo_maestro, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info('Catálogo maestro generado con éxito: %s (%d alias mapeados)', archivo_catalogo.name, len(catalogo_maestro))
    log.info('=== INGESTA COMPLETADA: %d/%d torneos descargados con éxito ===', exitos, len(TORNEOS_CONFIG))

if __name__ == '__main__':
    ejecutar_ingesta()
