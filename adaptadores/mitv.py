# -*- coding: utf-8 -*-
from __future__ import annotations

import gzip
import html as html_lib
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

PROGRAMAS_ESTUDIO_NOTICIAS = [
    # Win Sports
    "NOTICIAS", "PRIMER TOQUE", "SAQUE LARGO", "LINEA DE 4", "LÍNEA DE 4",
    "SHOW", "LO MEJOR", "DESPIERTA WIN", "PLANETA FÚTBOL", "PLANETA",
    "MEDIO TIEMPO", "WHAT THE FUN", "ESPECIAL", "DETRÁS DE LA GLORIA", "DETRAS DE LA GLORIA",
    "CAMPAÑAS", "CAMPANAS", "DOCUMENTAL", "HISTORIA", "UNA CANCHA LLAMADA COLOMBIA", "EL CHIRINGUITO",
    "MUCHO + FÚTBOL", "MUCHO + FUTBOL",
    # ESPN
    "SPORTSCENTER", "F90", "F360", "BALON DIVIDIDO", "BALÓN DIVIDIDO",
    "EQUIPO F", "GENERACION F", "GENERACIÓN F", "FSHOW", "ESPN KNOCKOUT MAGAZINE",
    "NEXO", "ESPN FC", "DEBATE", "CRONOMETRO", "CRONÓMETRO", "FUERA DE JUEGO",
    "BUEN DÍA ESPN", "BUEN DIA ESPN", "ESPN F12", "ESPN F10", "ESPN F3",
    "EQUIPO SCRUM", "GOLES DE LA SERIE A", "GOLES DE LA BUNDESLIGA", "GOLES DE LA LIGUE 1",
    "LO MEJOR DE MOTOGP", "CHASING THE DREAM", "CHAMPIONS LEAGUE WEEKLY", "EUROPA LEAGUE MAGAZINE",
    "ATP TOUR UNCOVERED", "ESPN FÚTBOL 1", "ESPN FUTBOL 1", "CAMPEÓN SR AMERICAS",
    # TyC Sports
    "SPORTIA", "PASO A PASO", "SUPERFÚTBOL", "SUPERFUTBOL", "DALE AL MEDIO", "LÍBERO", "LIBERO",
    "PRESIÓN ALTA", "PRESION ALTA", "LA NOCHE DE TYC SPORTS", "CARBURANDO",
]

RE_REPETICIONES = re.compile(
    r"\b(?:REVIVE|MEJORES\s+MOMENTOS|REPETICI[OÓ]N|DIFERIDO|EN\s+DIFERIDO|RESUMEN|FLASHBACK|HISTORIA|HISTORIAS|RETRO|REPLAY|COMPACTO|APLAZADO|SUSPENDIDO)\b",
    re.I
)

# Jurisdicción estricta:
# - Colombia (mi.tv/co) SOLO para Win Sports+ y Win Sports.
# - Argentina (mi.tv/ar) para toda la familia ESPN (Cono Sur) y TyC Sports.
CANALES_MITV = [
    ("WIN SPORTS", "https://mi.tv/co/async/channel/win-sports-hd/-300", "America/Bogota"),
    ("ESPN AR", "https://mi.tv/ar/async/channel/espn/-180", "America/Argentina/Buenos_Aires"),
    ("ESPN 2 AR", "https://mi.tv/ar/async/channel/espn-2/-180", "America/Argentina/Buenos_Aires"),
    ("ESPN 3 AR", "https://mi.tv/ar/async/channel/espn-3/-180", "America/Argentina/Buenos_Aires"),
    ("TYC SPORTS", "https://mi.tv/ar/async/channel/tyc-sports/-180", "America/Argentina/Buenos_Aires"),
]

FAMILIAS_MITV = [
    {"WIN SPORTS+", "WIN SPORTS"},
    {"ESPN PREMIUM ARGENTINA", "ESPN AR", "ESPN 2 AR", "ESPN 3 AR", "ESPN 4", "ESPN 5", "ESPN 6", "ESPN 7"},
]

def _tiene_canal_misma_familia(canal_nuevo: str, canales_existentes: List[str]) -> bool:
    for fam in FAMILIAS_MITV:
        if canal_nuevo in fam and any(c in fam for c in canales_existentes):
            return True
    return False

def obtener_directos_lineales_mitv(
    fecha_hoy_iso: str,
    eventos_referencia_futbolenvivo: Optional[List[EventoAgenda]] = None
) -> List[EventoAgenda]:
    nuevos: List[EventoAgenda] = []

    ref_eventos = []
    if eventos_referencia_futbolenvivo:
        for ev in eventos_referencia_futbolenvivo:
            ref_eventos.append({
                "ev": ev,
                "loc": (ev.local or "").lower(),
                "vis": (ev.visitante or "").lower(),
                "titulo": (ev.titulo or "").lower(),
                "canales": [c.upper() for c in ev.canales],
                "hora_utc": ev.hora_utc,
            })

    for canon_canal, url, tz_name in CANALES_MITV:
        tz = obtener_tz(tz_name)
        html = _descargar_html(url, timeout=8)
        if not html:
            continue

        progs = re.findall(
            r'<a[^>]*href=["\']([^"\']+)["\'][^>]*class=["\']program-link["\'][^>]*>(.*?)</a>',
            html,
            flags=re.S
        )

        # Pre-parsear horarios de inicio para calcular la duración real en la grilla lineal
        lista_horarios = []
        for _, cont_item in progs:
            m_t = re.search(r"""<span[^>]*class=["']time["'][^>]*>(.*?)</span>""", cont_item)
            t_s = m_t.group(1).strip() if m_t else ""
            m_h = re.search(r"([0-1]?[0-9]|2[0-3]):([0-5][0-9])\s*(am|pm)?", t_s, re.I)
            if m_h:
                h_p = int(m_h.group(1))
                mi_p = int(m_h.group(2))
                ap_p = (m_h.group(3) or "").lower()
                if ap_p == "pm" and h_p < 12: h_p += 12
                elif ap_p == "am" and h_p == 12: h_p = 0
                try:
                    dt_p = datetime.fromisoformat(f"{fecha_hoy_iso}T{h_p:02d}:{mi_p:02d}:00").replace(tzinfo=tz)
                    lista_horarios.append(dt_p)
                except Exception:
                    lista_horarios.append(None)
            else:
                lista_horarios.append(None)

        for idx_prog, (href, contenido) in enumerate(progs):
            m_time = re.search(r'<span[^>]*class=["\']time["\'][^>]*>(.*?)</span>', contenido)
            m_h2 = re.search(r'<h2[^>]*>(.*?)</h2>', contenido, flags=re.S)
            m_sub = re.search(r'<span[^>]*class=["\']sub-title["\'][^>]*>(.*?)</span>', contenido, flags=re.S)
            m_syn = re.search(r'<p[^>]*class=["\']synopsis["\'][^>]*>(.*?)</p>', contenido, flags=re.S)

            time_str = m_time.group(1).strip() if m_time else ""
            h2_str = html_lib.unescape(" ".join(re.sub(r'<[^>]+>', ' ', m_h2.group(1)).split()).strip()) if m_h2 else ""
            sub_str = html_lib.unescape(" ".join(re.sub(r'<[^>]+>', ' ', m_sub.group(1)).split()).strip()) if m_sub else ""
            syn_str = html_lib.unescape(" ".join(re.sub(r'<[^>]+>', ' ', m_syn.group(1)).split()).strip()) if m_syn else ""

            todo = f"{h2_str} {sub_str} {syn_str}".upper()

            if any(w in todo for w in PROGRAMAS_ESTUDIO_NOTICIAS):
                continue

            if RE_REPETICIONES.search(todo):
                continue

            if re.search(r"\b(200\d|201\d|202[0-4])\b", href):
                continue

            m_hora = re.search(r"([0-1]?[0-9]|2[0-3]):([0-5][0-9])\s*(am|pm)?", time_str, re.I)
            if not m_hora:
                continue

            h = int(m_hora.group(1))
            mi = int(m_hora.group(2))
            ampm = (m_hora.group(3) or "").lower()
            if ampm == "pm" and h < 12:
                h += 12
            elif ampm == "am" and h == 12:
                h = 0

            try:
                dt_local = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz)
                hora_utc = dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                continue

            # Contraste estricto contra título y subtítulo (sin usar la sinopsis genérica)
            contrastado = False
            ev_ref_match = None
            if ref_eventos:
                tit_sub_lower = f"{h2_str} {sub_str}".lower()
                for r in ref_eventos:
                    if r["loc"] and r["vis"] and r["loc"] in tit_sub_lower and r["vis"] in tit_sub_lower:
                        try:
                            dt_ref = datetime.fromisoformat(r["hora_utc"].replace("Z", "+00:00"))
                            diff_min = abs((dt_local.astimezone(timezone.utc) - dt_ref).total_seconds()) / 60
                            if diff_min <= 90:
                                contrastado = True
                                ev_ref_match = r["ev"]
                                break
                        except Exception:
                            pass

            if contrastado and ev_ref_match:
                if canon_canal not in ev_ref_match.canales and not _tiene_canal_misma_familia(canon_canal, ev_ref_match.canales):
                    ev_ref_match.canales.append(canon_canal)
                continue

            m_vs = re.search(r"([A-Za-z0-9\.\sÁÉÍÓÚáéíóúñÑ]+)\s+(?:vs\.?|v\.?)\s+([A-Za-z0-9\.\sÁÉÍÓÚáéíóúñÑ]+)", f"{h2_str} {sub_str}", re.I)
            if m_vs:
                loc = m_vs.group(1).strip()
                vis = re.split(r"\s+(?:Fecha|Estadio|Jornada|Juego|Semifinal|Final)\b", m_vs.group(2), flags=re.I)[0].strip()
                titulo = f"{loc} vs {vis}"
                tipo = "duelo"
                torneo = h2_str if m_vs.group(0) not in h2_str else (sub_str or f"{canon_canal} en Vivo")
            else:
                es_deporte_circuito = any(k in todo for k in [
                    "TENIS", "ATP", "WTA", "SHANGHAI", "ROLAND GARROS", "WIMBLEDON", "US OPEN",
                    "MOTOR", "F1", "FÓRMULA 1", "FORMULA 1", "MOTOGP", "INDYCAR", "NASCAR",
                    "PADEL", "PÁDEL", "PREMIER PADEL", "GOLF", "PGA", "LIV GOLF",
                    "CICLISMO", "TOUR", "GIRO", "VUELTA", "SNOOKER", "BILLAR", "BOXEO", "UFC", "COMBATE"
                ])
                if not es_deporte_circuito:
                    continue

                loc, vis = "", ""
                tipo = "circuito"
                if sub_str and h2_str:
                    titulo = f"{sub_str} - {h2_str}" if h2_str.lower() not in sub_str.lower() else sub_str
                    torneo = sub_str
                else:
                    titulo = h2_str or sub_str
                    torneo = titulo or f"{canon_canal} en Vivo"

            u_txt = todo.upper()
            if any(k in u_txt for k in ["TENIS", "ATP", "WTA", "SHANGHAI", "ROLAND GARROS", "WIMBLEDON", "US OPEN"]):
                deporte = "Tenis"
            elif any(k in u_txt for k in ["NFL", "FÚTBOL AMERICANO", "FUTBOL AMERICANO"]):
                deporte = "Fútbol Americano"
            elif any(k in u_txt for k in ["MLB", "BÉISBOL", "BEISBOL", "BASEBALL"]):
                deporte = "Béisbol"
            elif any(k in u_txt for k in ["NBA", "WNBA", "BASKET", "BALONCESTO"]):
                deporte = "Baloncesto"
            elif any(k in u_txt for k in ["MOTOR", "F1", "FÓRMULA 1", "FORMULA 1", "MOTOGP", "INDYCAR"]):
                deporte = "Motor"
            elif any(k in u_txt for k in ["PADEL", "PÁDEL"]):
                deporte = "Pádel"
            elif any(k in u_txt for k in ["RUGBY"]):
                deporte = "Rugby"
            elif any(k in u_txt for k in ["BOXEO", "BOXING", "UFC", "MMA", "KNOCKOUT"]):
                deporte = "Combate"
            elif any(k in u_txt for k in ["CICLISMO"]):
                deporte = "Ciclismo"
            elif any(k in u_txt for k in ["FÚTBOL", "FUTBOL", "BETPLAY", "LALIGA", "SERIE A", "PREMIER LEAGUE", "BRASILEIRÃO", "BRASILEIRAO"]):
                deporte = "Fútbol"
            elif tipo == "duelo":
                deporte = "Fútbol"
            else:
                deporte = "Deportes en Vivo"

            texto_crudo = f"{h2_str}. {sub_str}. {syn_str}".strip(". ")

            # Duración exacta según la grilla lineal hasta que comience el siguiente programa
            duracion_lineal = None
            if idx_prog + 1 < len(lista_horarios) and lista_horarios[idx_prog + 1] and dt_local:
                diff_m = int((lista_horarios[idx_prog + 1] - dt_local).total_seconds() / 60)
                if diff_m < 0:
                    diff_m += 1440
                if 25 <= diff_m <= 360:
                    duracion_lineal = diff_m

            ev = EventoAgenda(
                titulo=titulo,
                deporte=deporte,
                torneo=torneo,
                local=loc,
                visitante=vis,
                hora_utc=hora_utc,
                canales=[canon_canal],
                duracion_min=duracion_lineal or (180 if deporte in ["Motor", "Béisbol", "Tenis", "Fútbol Americano"] else 120),
                fuente=f"mitv_{canon_canal.lower().replace(' ', '_').replace('+', 'plus')}",
                tipo_evento=tipo,
                texto_original_crudo=texto_crudo,
            )
            nuevos.append(ev)

    log.info("Canales lineales mi.tv (Win Sports CO, ESPN AR, TyC AR): %d eventos rescatados/contrastados", len(nuevos))
    return nuevos

def obtener_directos_win_sports(
    fecha_hoy_iso: str,
    eventos_referencia_futbolenvivo: Optional[List[EventoAgenda]] = None
) -> List[EventoAgenda]:
    return obtener_directos_lineales_mitv(fecha_hoy_iso, eventos_referencia_futbolenvivo)