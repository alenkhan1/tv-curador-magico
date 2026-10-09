# -*- coding: utf-8 -*-
from __future__ import annotations

import gzip
import logging
import re
import ssl
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .modelos import EventoAgenda, obtener_tz

log = logging.getLogger("adaptador_mitv")

def _crear_contexto_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

HEADERS_COMPLETOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

def _descargar_html(url: str, timeout: int = 8) -> str:
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

def obtener_directos_win_sports(
    fecha_hoy_iso: str,
    eventos_referencia_futbolenvivo: Optional[List[EventoAgenda]] = None
) -> List[EventoAgenda]:
    """
    Consulta la programación de Win Sports y Win Sports+ en mi.tv aplicando
    filtro estricto: solo acepta eventos explícitamente etiquetados como 'En Vivo' / 'Directo',
    o eventos contrastados contra la cartelera de hoy de futbolenvivocolombia,
    descartando categóricamente programas de estudio y repeticiones nocturnas/madrugada.
    """
    canales_mitv = [
        ("WIN SPORTS+", "https://mi.tv/co/async/channel/win-sports-hd/-300"),
        ("WIN SPORTS", "https://mi.tv/co/async/channel/win-sports/-300"),
    ]
    tz_col = obtener_tz("America/Bogota")
    nuevos: List[EventoAgenda] = []

    # Extraer eventos de Win Sports de referencia para contraste
    ref_win = []
    if eventos_referencia_futbolenvivo:
        for ev in eventos_referencia_futbolenvivo:
            if any("WIN" in c.upper() for c in ev.canales):
                try:
                    dt = datetime.fromisoformat(ev.hora_utc.replace("Z", "+00:00")).astimezone(tz_col)
                    min_dia = dt.hour * 60 + dt.minute
                except Exception:
                    min_dia = -1
                ref_win.append({
                    "ev": ev,
                    "loc": ev.local.lower(),
                    "vis": ev.visitante.lower(),
                    "titulo": ev.titulo.lower(),
                    "min_dia": min_dia,
                })

    for canon_canal, url in canales_mitv:
        html = _descargar_html(url, timeout=8)
        if not html:
            continue

        progs = re.findall(
            r'<a[^>]*href=["\']([^"\']+)["\'][^>]*class=["\']program-link["\'][^>]*>(.*?)</a>',
            html,
            flags=re.S
        )

        for href, contenido in progs:
            txt = " ".join(re.sub(r"<[^>]+>", " ", contenido).split()).strip()
            u_txt = txt.upper()

            # 1. Filtro estricto: Descarte de programas fijos de estudio, noticias y debate
            if any(w in u_txt for w in [
                "NOTICIAS", "PRIMER TOQUE", "SAQUE LARGO", "LINEA DE 4", "LÍNEA DE 4",
                "SHOW", "LO MEJOR", "RESUMEN", "DESPIERTA WIN", "PLANETA FÚTBOL", "PLANETA",
                "MEDIO TIEMPO", "WHAT THE FUN", "ESPECIAL", "DETRÁS DE LA GLORIA", "DETRAS DE LA GLORIA",
                "CAMPAÑAS", "CAMPANAS", "DOCUMENTAL", "HISTORIA"
            ]):
                continue

            # 2. Descarte de repeticiones históricas por año en la URL
            if re.search(r"\b(200\d|201\d|202[0-4])\b", href):
                continue

            # Hora local
            m_hora = re.search(r"([0-1]?[0-9]|2[0-3]):([0-5][0-9])\s*(am|pm)?", txt, re.I)
            if not m_hora:
                continue

            h = int(m_hora.group(1))
            mi = int(m_hora.group(2))
            ampm = (m_hora.group(3) or "").lower()
            if ampm == "pm" and h < 12:
                h += 12
            elif ampm == "am" and h == 12:
                h = 0
            minutos_mitv = h * 60 + mi

            # 3. ¿Tiene etiqueta explícita de En Vivo / Directo?
            es_en_vivo_tag = any(w in u_txt for w in ["EN VIVO", "DIRECTO", "EN DIRECTO", "LIVE", "VIVO"])

            # 4. Contraste riguroso con futbolenvivocolombia
            contrastado = False
            ev_ref_match = None
            if ref_win:
                txt_lower = txt.lower()
                for r in ref_win:
                    palabras_clave = [p for p in (r["loc"] + " " + r["vis"]).split() if len(p) > 3]
                    coincidencias = [p for p in palabras_clave if p in txt_lower]
                    if len(coincidencias) >= 2 or (r["loc"] and r["loc"] in txt_lower) or (r["vis"] and r["vis"] in txt_lower):
                        # Descartar repeticiones nocturnas verificando cercanía horaria (+-60 min)
                        if r["min_dia"] >= 0 and abs(minutos_mitv - r["min_dia"]) <= 60:
                            contrastado = True
                            ev_ref_match = r["ev"]
                            break

            # Si no tiene etiqueta explícita de En Vivo Y tampoco contrasta con la cartelera de hoy -> DESCARTAR (repetición)
            if not es_en_vivo_tag and not contrastado:
                continue

            # Si contrastó con un evento de futbolenvivo, enriquecer el canal exacto de Win
            if contrastado and ev_ref_match:
                if canon_canal not in ev_ref_match.canales:
                    ev_ref_match.canales.append(canon_canal)
                continue

            # Si es un evento nuevo con etiqueta explícita En Vivo
            tit_raw = re.sub(r"^[0-1]?[0-9]:[0-5][0-9]\s*(?:am|pm)?\s*", "", txt, flags=re.I).strip()
            m_vs = re.search(r"([A-Za-z0-9\.\s]+)\s+(?:vs\.?|v\.?|-)\s+([A-Za-z0-9\.\s]+)", tit_raw, re.I)
            if m_vs:
                loc = m_vs.group(1).strip()
                vis = re.split(r"\s+(?:Fecha|Estadio|Jornada)\b", m_vs.group(2), flags=re.I)[0].strip()
                titulo = f"{loc} vs {vis}"
                tipo = "duelo"
            else:
                loc, vis = "", ""
                titulo = re.split(r"\s+(?:Fecha|Estadio|Jornada)\b", tit_raw, flags=re.I)[0].strip()
                tipo = "circuito"

            try:
                dt_local = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_col)
                hora_utc = dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                continue

            ev = EventoAgenda(
                titulo=titulo,
                deporte="Fútbol" if tipo == "duelo" else "Deportes en Vivo",
                torneo=f"{canon_canal} en Vivo",
                local=loc,
                visitante=vis,
                hora_utc=hora_utc,
                canales=[canon_canal],
                duracion_min=120,
                fuente="mitv_colombia",
                tipo_evento=tipo,
            )
            nuevos.append(ev)

    log.info("Win Sports (mi.tv contrastado): %d eventos nuevos confirmados en vivo", len(nuevos))
    return nuevos
