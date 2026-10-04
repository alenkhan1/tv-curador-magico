# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
import ssl
import urllib.request
import html as html_lib
from datetime import datetime, timezone
from typing import List, Dict, Any

from adaptadores.modelos import EventoAgenda, HEADERS_WEB, obtener_tz

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("test_suramerica")

def _crear_contexto_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

CANAL_MAPPING = [
    (r"\bWIN\s*(?:F[UÚ]TBOL\s*\+|SPORTS\s*\+)", "WIN SPORTS+"),
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
        # Exclusiones estrictas: no USA, no Brasil, no Mexico, no Apps OTT residuales
        if any(exc in u for exc in ["USA", "US", "MEX", "MEXICO", "BRASIL", "BRAZIL", "ESPNU", "NEWS", "YOUTUBE", "TIKTOK"]):
            continue
        for patron, canon in CANAL_MAPPING:
            if re.search(patron, u):
                if canon not in resultado:
                    resultado.append(canon)
                break
    return resultado

def extraer_directos_url(url: str, tz_name: str, fecha_hoy_iso: str) -> List[EventoAgenda]:
    req = urllib.request.Request(url, headers=HEADERS_WEB)
    try:
        with urllib.request.urlopen(req, timeout=16, context=_crear_contexto_ssl()) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log.warning("No se pudo descargar %s: %s", url, e)
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
                    m_otra = re.search(r'([0-3]?[0-9]/[0-1]?[0-9])', txt_l)
                    if m_otra:
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

        m_comp = re.search(r'class=["\']ajusteDoslineas["\'][^>]*title=["\']([^"\']+)["\']', f)
        torneo = html_lib.unescape(m_comp.group(1).strip()) if m_comp else ""

        equipos = [html_lib.unescape(re.sub(r"<[^>]+>", "", e).strip()) for e in re.findall(r'<td class=["\']equipo["\'][^>]*>(.*?)</td>', f, flags=re.S)]
        equipos = [e for e in equipos if e]

        m_ev = re.search(r'class=["\']eventoUnico["\'][^>]*>(.*?)<', f)
        if len(equipos) == 2:
            loc, vis = equipos[0], equipos[1]
            titulo = f"{loc} vs {vis}"
            tipo = "duelo"
        else:
            loc, vis = "", ""
            if m_ev:
                titulo = html_lib.unescape(m_ev.group(1).strip())
            else:
                titulo = torneo or "Evento Deportivo"
            tipo = "circuito"

        u_todo = f"{torneo} {titulo}".upper()
        if any(k in u_todo for k in ["F1", "FÓRMULA 1", "FORMULA 1", "MOTOGP", "MOTO2", "MOTO3", "NASCAR", "INDYCAR"]):
            deporte = "Motor"
        elif any(k in u_todo for k in ["TENIS", "ATP", "WTA", "PEKÍN", "TOKIO", "SHANGHAI", "WIMBLEDON", "ROLAND GARROS"]):
            deporte = "Tenis"
        elif any(k in u_todo for k in ["BÁSQUET", "BASQUET", "BASKETBALL", "NBA", "EUROLIGA", "LIGA ENDESA"]):
            deporte = "Baloncesto"
        elif any(k in u_todo for k in ["CICLISMO", "GIRO", "TOUR", "VUELTA"]):
            deporte = "Ciclismo"
        elif any(k in u_todo for k in ["BÉISBOL", "BEISBOL", "BASEBALL", "MLB"]):
            deporte = "Béisbol"
        elif any(k in u_todo for k in ["BALONMANO", "HANDBALL", "ASOBAL"]):
            deporte = "Balonmano"
        elif any(k in u_todo for k in ["RUGBY", "TOP 12", "SEIS NACIONES"]):
            deporte = "Rugby"
        elif any(k in u_todo for k in ["PÁDEL", "PADEL"]):
            deporte = "Pádel"
        else:
            deporte = "Fútbol"

        try:
            h, mi = [int(x) for x in hora_str.split(":")]
            dt_local = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz)
            hora_utc = dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        ev = EventoAgenda(
            titulo=titulo,
            deporte=deporte,
            torneo=torneo or deporte,
            local=loc,
            visitante=vis,
            hora_utc=hora_utc,
            canales=canales_validos,
            duracion_min=180 if deporte in ["Motor", "Béisbol", "Tenis"] else 120,
            fuente=url.split("/")[-2] if "/" in url else "deporte",
            tipo_evento=tipo,
        )
        eventos.append(ev)

    return eventos

def obtener_directos_suramerica(fecha_hoy_iso: str) -> List[EventoAgenda]:
    evs_col = extraer_directos_url("https://www.futbolenvivocolombia.com/deporte", "America/Bogota", fecha_hoy_iso)
    evs_arg = extraer_directos_url("https://www.futbolenvivoargentina.com/deporte", "America/Argentina/Buenos_Aires", fecha_hoy_iso)
    
    todos = evs_col + evs_arg
    # Deduplicar por (titulo, hora_utc)
    vistos = {}
    dedup = []
    for ev in todos:
        k = (ev.titulo.strip().lower(), ev.hora_utc[:16])
        if k in vistos:
            existente = vistos[k]
            for c in ev.canales:
                if c not in existente.canales:
                    existente.canales.append(c)
        else:
            vistos[k] = ev
            dedup.append(ev)
            
    return dedup

if __name__ == "__main__":
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    res = obtener_directos_suramerica(hoy)
    print(f"Total eventos obtenidos para hoy ({hoy}): {len(res)}")
    for ev in res:
        print(f"[{ev.hora_utc}] ({ev.deporte}) {ev.titulo} | Torneo: {ev.torneo} | Canales: {ev.canales}")
