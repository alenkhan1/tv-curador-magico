# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

HEADERS_WEB = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

OFFSETS_FIJOS = {
    "America/Bogota": timezone(timedelta(hours=-5)),
    "America/Argentina/Buenos_Aires": timezone(timedelta(hours=-3)),
    "America/Mexico_City": timezone(timedelta(hours=-6)),
    "America/Santiago": timezone(timedelta(hours=-3)),
    "Europe/Madrid": timezone(timedelta(hours=2)),
    "Europe/London": timezone(timedelta(hours=1)),
    "America/New_York": timezone(timedelta(hours=-4)),
}

def obtener_tz(nombre: str) -> Any:
    """Devuelve ZoneInfo si tzdata esta disponible; fallback seguro a offset fijo."""
    try:
        return ZoneInfo(nombre)
    except Exception:
        return OFFSETS_FIJOS.get(nombre, timezone.utc)

def normalizar_texto(s: str) -> str:
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    sin_tildes = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin_tildes).strip().upper()

def limpiar_nombre_equipo(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"^[0-9]{1,2}[:.][0-9]{2}\s*(?:AM|PM|am|pm)?\s*", "", s)
    s = re.sub(r"\b(HD|FHD|SD|4K|OP\d+|LIVE|DIRECTO|EN VIVO|ES|SPAIN|LATAM)\b", "", s, flags=re.I)
    return " ".join(s.split()).strip(" -:|/\t\r\n")


DEPORTES_ELASTICOS = {
    "SNOOKER", "BILLAR", "GOLF", "TENIS", "TENNIS", "CICLISMO", "CYCLING",
    "BEISBOL", "BÉISBOL", "BASEBALL", "MOTOR", "FORMULA 1", "FÓRMULA 1", "F1",
    "MOTOGP", "COMBATE", "BOXEO", "MMA", "UFC"
}

def es_deporte_elastico(deporte: str, titulo: str = "") -> bool:
    texto = normalizar_texto(f"{deporte} {titulo}")
    return any(k in texto for k in [
        "SNOOKER", "BILLAR", "GOLF", "TENIS", "TENNIS", "CICLISMO", "CYCLING",
        "BEISBOL", "BASEBALL", "MOTOR", "FORMULA 1", "F1", "MOTOGP", "COMBATE", "BOXEO", "MMA", "UFC"
    ])

def calcular_duracion_evento(deporte: str, titulo: str = "", duracion_detectada_min: Optional[int] = None) -> int:
    elastico = es_deporte_elastico(deporte, titulo)
    if duracion_detectada_min and duracion_detectada_min > 20:
        return duracion_detectada_min + (15 if elastico else 10)

    if elastico:
        return 240
    return 125

@dataclass
class EventoAgenda:
    titulo: str
    deporte: str
    torneo: str
    local: str
    visitante: str
    hora_utc: str          # Formato ISO 8601 UTC: 2026-10-01T15:00:00Z
    canales: List[str]     # Nombres canónicos de canales donde se emite
    duracion_min: int = 120
    fuente: str = ""
    referencia: str = ""
    tipo_evento: str = "duelo"  # "duelo" o "circuito"

    def __post_init__(self):
        if self.duracion_min == 120:
            self.duracion_min = calcular_duracion_evento(self.deporte, self.titulo, 120)

    def clave_deduplicacion(self) -> str:
        try:
            dt = datetime.fromisoformat(self.hora_utc.replace("Z", "+00:00"))
            hora_bloque = dt.strftime("%Y%m%d_%H")
        except Exception:
            hora_bloque = self.hora_utc[:13]

        if self.tipo_evento == "duelo" and self.local and self.visitante:
            k1 = normalizar_texto(self.local)[:10]
            k2 = normalizar_texto(self.visitante)[:10]
            equipos = "_".join(sorted([k1, k2]))
            return f"DUELO_{equipos}_{hora_bloque}"
        tor_k = normalizar_texto(self.torneo or self.titulo)[:20]
        return f"EVENTO_{tor_k}_{hora_bloque}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "titulo": self.titulo,
            "deporte": self.deporte,
            "torneo": self.torneo,
            "local": self.local,
            "visitante": self.visitante,
            "hora_utc": self.hora_utc,
            "canales": self.canales,
            "duracion_min": self.duracion_min,
            "fuente": self.fuente,
            "referencia": self.referencia,
            "tipo_evento": self.tipo_evento,
        }
