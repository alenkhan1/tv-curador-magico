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
    r"\b(?:REVIVE|MEJORES\s+MOMENTOS|REPETICI[OÓ]N|DIFERIDO|EN\s+DIFERIDO|RESUMEN|FLASHBACK|HISTORIA|HISTORIAS|RETRO|REPLAY|COMPACTO)\b",
    re.I
)

CANALES_MITV = [
    # Colombia (Aislamiento territorial estricto CO)
    ("WIN SPORTS+", "https://mi.tv/co/async/channel/win-sports-hd/-300", "America/Bogota"),
    ("WIN SPORTS", "https://mi.tv/co/async/channel/win-sports/-300", "America/Bogota"),
    ("ESPN CO", "https://mi.tv/co/async/channel/espn/-300", "America/Bogota"),
    ("ESPN 2 CO", "https://mi.tv/co/async/channel/espn-2/-300", "America/Bogota"),
    ("ESPN 3 CO", "https://mi.tv/co/async/channel/espn-3/-300", "America/Bogota"),
    # Argentina (Aislamiento territorial estricto AR)
    ("ESPN AR", "https://mi.tv/ar/async/channel/espn/-180", "America/Argentina/Buenos_Aires"),
    ("ESPN 2 AR", "https://mi.tv/ar/async/channel/espn-2/-180", "America/Argentina/Buenos_Aires"),
    ("ESPN 3 AR", "https://mi.tv/ar/async/channel/espn-3/-180", "America/Argentina/Buenos_Aires"),
    ("TYC SPORTS", "https://mi.tv/ar/async/channel/tyc-sports/-180", "America/Argentina/Buenos_Aires"),
]

def obtener_directos_lineales_mitv(
    fecha_hoy_iso: str,
    eventos_referencia_futbolenvivo: Optional[List[EventoAgenda]] = None
) -> List[EventoAgenda]:
    """
    Consulta la programación oficial canal por canal en mi.tv para:
    - Win Sports+ y Win Sports (Colombia)
    - ESPN, ESPN 2 y ESPN 3 (Señales de Colombia y Argentina)
    - TyC Sports (Argentina)

    Filtro estricto inicial:
    - Descarta noticieros, magazines y programas de debate de estudio.
    - Excluye repeticiones explícitas, diferidos, compactos y resúmenes.
    - Excluye repeticiones históricas con años anteriores en la URL.

    Para todos los eventos deportivos sobrevivientes:
    - Si empareja con la cartelera de referencia (futbolenvivo), enriquece el canal 1 a 1.
    - Si no empareja con referencia, se conserva con su texto original crudo para que AGY
      en la VM certifique su veracidad temporal (descartando si fue de ayer y confirmando
      si es un directo genuino de hoy, incluyendo deportes y torneos locales/minoritarios).
    """
    nuevos: List[EventoAgenda] = []

    # Extraer eventos de referencia para contraste
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

        for href, contenido in progs:
            m_time = re.search(r'<span[^>]*class=["\']time["\'][^>]*>(.*?)</span>', contenido)
            m_h2 = re.search(r'<h2[^>]*>(.*?)</h2>', contenido, flags=re.S)
            m_sub = re.search(r'<span[^>]*class=["\']sub-title["\'][^>]*>(.*?)</span>', contenido, flags=re.S)
            m_syn = re.search(r'<p[^>]*class=["\']synopsis["\'][^>]*>(.*?)</p>', contenido, flags=re.S)

            time_str = m_time.group(1).strip() if m_time else ""
            h2_str = html_lib.unescape(" ".join(re.sub(r'<[^>]+>', ' ', m_h2.group(1)).split()).strip()) if m_h2 else ""
            sub_str = html_lib.unescape(" ".join(re.sub(r'<[^>]+>', ' ', m_sub.group(1)).split()).strip()) if m_sub else ""
            syn_str = html_lib.unescape(" ".join(re.sub(r'<[^>]+>', ' ', m_syn.group(1)).split()).strip()) if m_syn else ""

            todo = f"{h2_str} {sub_str} {syn_str}".upper()

            # 1. Filtro estricto: Descarte de noticieros, magazines y programas de estudio
            if any(w in todo for w in PROGRAMAS_ESTUDIO_NOTICIAS):
                continue

            # 1.1 Descarte estricto de repeticiones, revives, resúmenes y compactos
            if RE_REPETICIONES.search(todo):
                continue

            # 2. Descarte de repeticiones históricas por año en la URL
            if re.search(r"\b(200\d|201\d|202[0-4])\b", href):
                continue

            # Hora local
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

            # 3. Contraste riguroso con la cartelera de referencia
            contrastado = False
            ev_ref_match = None
            if ref_eventos:
                todo_lower = todo.lower()
                for r in ref_eventos:
                    palabras_clave = [p for p in (r["loc"] + " " + r["vis"]).split() if len(p) > 3]
                    coincidencias = [p for p in palabras_clave if p in todo_lower]
                    if len(coincidencias) >= 2 or (r["loc"] and r["loc"] in todo_lower and r["vis"] and r["vis"] in todo_lower):
                        try:
                            dt_ref = datetime.fromisoformat(r["hora_utc"].replace("Z", "+00:00"))
                            diff_min = abs((dt_local.astimezone(timezone.utc) - dt_ref).total_seconds()) / 60
                            if diff_min <= 90:
                                contrastado = True
                                ev_ref_match = r["ev"]
                                break
                        except Exception:
                            pass

            # Si contrastó con un evento de referencia, enriquecer el canal exacto 1 a 1
            if contrastado and ev_ref_match:
                if canon_canal not in ev_ref_match.canales:
                    ev_ref_match.canales.append(canon_canal)
                continue

            # 4. Si no contrastó, extraer como evento deportivo candidato para auditoría AGY
            m_vs = re.search(r"([A-Za-z0-9\.\s]+)\s+(?:vs\.?|v\.?)\s+([A-Za-z0-9\.\s]+)", h2_str, re.I)
            if m_vs:
                loc = m_vs.group(1).strip()
                vis = re.split(r"\s+(?:Fecha|Estadio|Jornada)\b", m_vs.group(2), flags=re.I)[0].strip()
                titulo = f"{loc} vs {vis}"
                tipo = "duelo"
                torneo = sub_str or f"{canon_canal} en Vivo"
            else:
                loc, vis = "", ""
                tipo = "circuito"
                if sub_str and h2_str:
                    titulo = f"{sub_str} - {h2_str}" if h2_str.lower() not in sub_str.lower() else sub_str
                    torneo = sub_str
                else:
                    titulo = h2_str or sub_str
                    torneo = titulo or f"{canon_canal} en Vivo"

            # Detección tentativa de disciplina
            u_txt = todo.upper()
            if any(k in u_txt for k in ["TENIS", "ATP", "WTA", "SHANGHAI", "ROLAND GARROS", "WIMBLEDON", "US OPEN"]):
                deporte = "Tenis"
            elif any(k in u_txt for k in ["NFL", "FÚTBOL AMERICANO", "FUTBOL AMERICANO"]):
                deporte = "Fútbol Americano"
            elif any(k in u_txt for k in ["MLB", "BÉISBOL", "BEISBOL", "BASEBALL"]):
                deporte = "Béisbol"
            elif any(k in u_txt for k in ["NBA", "BASKET", "BALONCESTO"]):
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

            ev = EventoAgenda(
                titulo=titulo,
                deporte=deporte,
                torneo=torneo,
                local=loc,
                visitante=vis,
                hora_utc=hora_utc,
                canales=[canon_canal],
                duracion_min=180 if deporte in ["Motor", "Béisbol", "Tenis", "Fútbol Americano"] else 120,
                fuente=f"mitv_{canon_canal.lower().replace(' ', '_').replace('+', 'plus')}",
                tipo_evento=tipo,
                texto_original_crudo=texto_crudo,
            )
            nuevos.append(ev)

    log.info("Canales lineales mi.tv (Win Sports, ESPN, TyC Sports): %d eventos rescatados/contrastados", len(nuevos))
    return nuevos

# Alias de compatibilidad
def obtener_directos_win_sports(
    fecha_hoy_iso: str,
    eventos_referencia_futbolenvivo: Optional[List[EventoAgenda]] = None
) -> List[EventoAgenda]:
    return obtener_directos_lineales_mitv(fecha_hoy_iso, eventos_referencia_futbolenvivo)
