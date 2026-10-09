# -*- coding: utf-8 -*-
from __future__ import annotations

import gzip
import html as html_lib
import logging
import re
import ssl
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List

from .mitv import obtener_directos_win_sports
from .modelos import EventoAgenda, obtener_tz

log = logging.getLogger("canales_suramerica")

def _crear_contexto_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

CANAL_MAPPING = [
    (r"\bWIN\s*(?:F[Uú]TBOL\s*\+|SPORTS\s*\+)", "WIN SPORTS+"),
    (r"\bWIN\s*SPORTS\b(?!\s*\+)", "WIN SPORTS"),
    (r"\bESPN\s*PREMIUM\b", "ESPN PREMIUM ARGENTINA"),
    (r"\bESPN\s*2\b", "ESPN 2"),
    (r"\bESPN\s*3\b", "ESPN 3"),
    (r"\bESPN\s*4\b", "ESPN 4"),
    (r"\bESPN\s*5\b", "ESPN 5"),
    (r"\bESPN\s*6\b", "ESPN 6"),
    (r"\bESPN\s*7\b", "ESPN 7"),
    (r"\bESPN\b(?!\s*(?:[234567]|PREMIUM))", "ESPN"),
    (r"\bTYC\s*SPORTS\b", "TYC SPORTS"),
    (r"\bTNT\s*SPORTS\b", "TNT SPORTS"),
    (r"\b(?:DSPORTS|DIRECTV\s*SPORTS)\s*2\b", "DSPORTS 2"),
    (r"\b(?:DSPORTS|DIRECTV\s*SPORTS)\s*\+", "DSPORTS +"),
    (r"\b(?:DSPORTS|DIRECTV\s*SPORTS)\b(?!\s*[\+2])", "DSPORTS"),
    (r"\bCARACOL\b", "CARACOL"),
    (r"\b(?:CANAL\s*)?RCN\b", "RCN"),
    (r"\bFOX\s*SPORTS\s*2\b", "FOX SPORTS 2"),
    (r"\bFOX\s*SPORTS\s*3\b", "FOX SPORTS 3"),
    (r"\bFOX\s*SPORTS\b(?!\s*[23])", "FOX SPORTS"),
]

def normalizar_canales(raw_canales: List[str]) -> List[str]:
    resultado = []
    for raw in raw_canales:
        u = raw.upper()
        if any(exc in u for exc in ["USA", "US", "MEX", "MEXICO", "BRASIL", "BRAZIL", "ESPNU", "NEWS", "YOUTUBE", "TIKTOK"]):
            continue
        for patron, canon in CANAL_MAPPING:
            if re.search(patron, u):
                if canon not in resultado:
                    resultado.append(canon)
                break
    return resultado

HEADERS_COMPLETOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

def _descargar_html(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers=HEADERS_COMPLETOS)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_crear_contexto_ssl()) as resp:
            raw = resp.read()
            enc = resp.headers.get("Content-Encoding")
            if enc == "gzip":
                return gzip.decompress(raw).decode("utf-8", errors="ignore")
            return raw.decode("utf-8", errors="ignore")
    except Exception as e:
        log.debug("No se pudo descargar %s: %s", url, e)
        return ""

def extraer_directos_html(html: str, url: str, tz_name: str, fecha_hoy_iso: str) -> List[EventoAgenda]:
    if not html:
        return []
    tz = obtener_tz(tz_name)
    try:
        d_obj = datetime.fromisoformat(fecha_hoy_iso)
        pat_dd_mm = d_obj.strftime("%d/%m")
    except Exception:
        pat_dd_mm = ""

    filas = re.findall(r"<tr[^>]*>.*?</tr>", html, flags=re.S)
    eventos = []
    dentro_de_hoy = False

    for f in filas:
        m_start = re.search(r'itemprop=["\']startDate["\']\s+content=["\']([0-9]{4}-[0-9]{2}-[0-9]{2})', f)
        if m_start:
            dentro_de_hoy = (m_start.group(1) == fecha_hoy_iso)
            if not dentro_de_hoy:
                continue
        else:
            txt_l = re.sub(r"<[^>]+>", " ", f).lower()
            if "cabeceratabla" in f.lower() or "partidos de hoy" in txt_l:
                if (pat_dd_mm and pat_dd_mm in txt_l) or "partidos de hoy" in txt_l:
                    dentro_de_hoy = True
                    continue
                else:
                    if re.search(r'([0-3]?[0-9]/[0-1]?[0-9])', txt_l):
                        dentro_de_hoy = False
                        continue

        if not dentro_de_hoy:
            continue

        raw_canales = re.findall(r'<li[^>]*title=["\']([^"\']+)["\']', f)
        canales_validos = normalizar_canales(raw_canales)
        if not canales_validos:
            continue

        m_h = re.search(r'class=["\']hora\s*["\'][^>]*>\s*([0-9]{1,2}:[0-9]{2})', f)
        if not m_h:
            continue
        hora_str = m_h.group(1).strip()

        # Disciplina
        m_dep = re.search(r'<div class=["\']contenedorImgCompeticion["\'][^>]*>.*?title=["\']([^"\']+)["\']', f)
        dep_raw = m_dep.group(1).strip() if m_dep else ""
        u_dep = dep_raw.upper()
        if any(k in u_dep for k in ["MOTOCICLISMO", "AUTOMOVILISMO", "MOTOR"]):
            deporte = "Motor"
        elif "TENIS" in u_dep:
            deporte = "Tenis"
        elif any(k in u_dep for k in ["BALONCESTO", "BASKET", "BÁSQUET"]):
            deporte = "Baloncesto"
        elif "CICLISMO" in u_dep:
            deporte = "Ciclismo"
        elif any(k in u_dep for k in ["BÉISBOL", "BEISBOL", "BASEBALL"]):
            deporte = "Béisbol"
        elif any(k in u_dep for k in ["BALONMANO", "HANDBALL"]):
            deporte = "Balonmano"
        elif "PÁDEL" in u_dep or "PADEL" in u_dep:
            deporte = "Pádel"
        elif "RUGBY" in u_dep:
            deporte = "Rugby"
        elif "MMA" in u_dep or "UFC" in u_dep:
            deporte = "MMA"
        else:
            deporte = "Fútbol"

        # Torneo
        m_tor = re.search(r'<span class=["\']ajusteDoslineas["\'][^>]*>.*?<label title=["\']([^"\']+)["\']', f)
        if not m_tor:
            m_tor = re.search(r'<span class=["\']ajusteDoslineas["\'][^>]*title=["\']([^"\']+)["\']', f)
        torneo = html_lib.unescape(m_tor.group(1).strip()) if m_tor else deporte

        # Equipos / Duelo
        m_loc = re.search(r'<td class=["\']local["\'][^>]*>.*?<span title=["\']([^"\']+)["\']', f)
        m_vis = re.search(r'<td class=["\']visitante["\'][^>]*>.*?<span title=["\']([^"\']+)["\']', f)

        if m_loc and m_vis:
            loc = html_lib.unescape(m_loc.group(1).strip())
            vis = html_lib.unescape(m_vis.group(1).strip())
            titulo = f"{loc} vs {vis}"
            tipo = "duelo"
        else:
            loc, vis = "", ""
            m_ev = re.search(r'<span class=["\']eventoUnico["\'][^>]*>(.*?)</span>', f, flags=re.S)
            if m_ev:
                raw_ev = html_lib.unescape(m_ev.group(1))
                clean_ev = " ".join(re.sub(r'<[^>]+>', ' ', raw_ev).split()).strip()
                titulo = f"{torneo} - {clean_ev}" if torneo and torneo not in clean_ev else clean_ev
            else:
                titulo = torneo or "Evento en Vivo"
            tipo = "circuito"

        u_todo = f"{torneo} {titulo}".upper()
        if any(k in u_todo for k in ["NBA", "BASKET", "BALONCESTO", "EUROLEAGUE"]):
            deporte = "Baloncesto"
        elif any(k in u_todo for k in ["NHL", "HOCKEY"]):
            deporte = "Hockey"
        elif any(k in u_todo for k in ["NFL", "NCAA FOOTBALL", "FÚTBOL AMERICANO", "FUTBOL AMERICANO"]):
            deporte = "Fútbol Americano"
        elif any(k in u_todo for k in ["MLB", "BÉISBOL", "BEISBOL", "BASEBALL"]):
            deporte = "Béisbol"
        elif any(k in u_todo for k in ["F1", "FÓRMULA 1", "FORMULA 1", "MOTOGP", "MOTO2", "MOTO3", "SUPERBIKE", "SUPERSPORT", "MOTOR", "AUTOMOVILISMO", "MOTOCICLISMO", "INDYCAR"]):
            deporte = "Motor"
        elif any(k in u_todo for k in ["ATP", "WTA", "ROLAND GARROS", "WIMBLEDON", "US OPEN", "SHANGHAI", "PEKIN", "TENIS", "TENNIS"]):
            deporte = "Tenis"
        elif any(k in u_todo for k in ["UFC", "MMA", "BOXEO", "BOXING"]):
            deporte = "MMA"
        elif any(k in u_todo for k in ["CICLISMO", "CYCLING", "TOUR DE FRANCE", "GIRO", "VUELTA"]):
            deporte = "Ciclismo"
        elif any(k in u_todo for k in ["PADEL", "PÁDEL"]):
            deporte = "Pádel"
        elif any(k in u_todo for k in ["RUGBY"]):
            deporte = "Rugby"

        try:
            h, mi = [int(x) for x in hora_str.split(":")]
            dt_local = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz)
            hora_utc = dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        ev = EventoAgenda(
            titulo=titulo,
            deporte=deporte,
            torneo=torneo,
            local=loc,
            visitante=vis,
            hora_utc=hora_utc,
            canales=canales_validos,
            duracion_min=180 if deporte in ["Motor", "Béisbol", "Tenis"] else 120,
            fuente=url.split("/")[-1] or "portada",
            tipo_evento=tipo,
        )
        eventos.append(ev)

    return eventos

def obtener_directos_suramerica(fecha_hoy_iso: str) -> List[EventoAgenda]:
    """
    Descarga la agenda deportiva completa de directos de hoy para Suramérica:
    Cubre Colombia (Win Sports+, Win Sports, ESPN 1..7, DSports, Caracol, RCN)
    y Argentina (ESPN Premium, TyC Sports, TNT Sports, FOX Sports 1..3).
    Garantía de multideporte en vivo, contraste riguroso con mi.tv para Win Sports,
    cero magazines y cero repeticiones.
    """
    colombia_urls = [
        ("https://www.futbolenvivocolombia.com/", "America/Bogota"),
        ("https://www.futbolenvivocolombia.com/deporte/motociclismo", "America/Bogota"),
        ("https://www.futbolenvivocolombia.com/deporte/automovilismo", "America/Bogota"),
        ("https://www.futbolenvivocolombia.com/deporte/tenis", "America/Bogota"),
        ("https://www.futbolenvivocolombia.com/deporte/baloncesto", "America/Bogota"),
        ("https://www.futbolenvivocolombia.com/deporte/mma", "America/Bogota"),
        ("https://www.futbolenvivocolombia.com/deporte/ciclismo", "America/Bogota"),
        ("https://www.futbolenvivocolombia.com/deporte/beisbol", "America/Bogota"),
    ]
    argentina_urls = [
        ("https://www.futbolenvivoargentina.com/deporte", "America/Argentina/Buenos_Aires"),
        ("https://www.futbolenvivoargentina.com/", "America/Argentina/Buenos_Aires"),
    ]

    todas_urls = colombia_urls + argentina_urls
    eventos_crudos: List[EventoAgenda] = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futs = {
            executor.submit(
                lambda u=url, tz=tz_name: extraer_directos_html(
                    _descargar_html(u), u, tz, fecha_hoy_iso
                )
            ): url
            for url, tz_name in todas_urls
        }
        for fut in as_completed(futs):
            try:
                res = fut.result()
                eventos_crudos.extend(res)
            except Exception:
                pass

    # Contraste riguroso de Win Sports en las dos webs: futbolenvivocolombia y mi.tv
    try:
        evs_mitv = obtener_directos_win_sports(fecha_hoy_iso, eventos_referencia_futbolenvivo=eventos_crudos)
        eventos_crudos.extend(evs_mitv)
    except Exception as e:
        log.warning("Fallo en contraste con mi.tv: %s", e)

    # Deduplicación y fusión de canales para un mismo evento
    vistos: Dict[tuple, EventoAgenda] = {}
    dedup: List[EventoAgenda] = []

    for ev in eventos_crudos:
        k = (ev.titulo.strip().lower(), ev.hora_utc[:16])
        if k in vistos:
            existente = vistos[k]
            for c in ev.canales:
                if c not in existente.canales:
                    existente.canales.append(c)
        else:
            vistos[k] = ev
            dedup.append(ev)

    log.info("Canales Suramérica: %d directos deportivos confirmados para hoy (%s)", len(dedup), fecha_hoy_iso)
    return dedup
