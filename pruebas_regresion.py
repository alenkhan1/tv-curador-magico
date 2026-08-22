# -*- coding: utf-8 -*-
"""Pruebas deterministas de regresión para el curador AllStreamTV v9."""

from datetime import datetime
from zoneinfo import ZoneInfo

from curador_eventos import (
    emparejar_sencillo,
    extraer_sesion,
    inferir_deporte,
    normalizar_texto,
    tokenizar,
)
from inyector_epg import buscar_evento_oficial, construir_evento_epg

TZ = ZoneInfo("America/Bogota")


def canal(nombre: str, categoria: str | None) -> dict:
    return {
        "id_xtream": "prueba",
        "nombre_ui": nombre,
        "texto_normalizado": normalizar_texto(nombre),
        "tokens": tokenizar(nombre),
        "hora_local": datetime(2026, 8, 22, 10, 0, tzinfo=TZ),
        "categoria_inferida": categoria,
        "sesion": extraer_sesion(nombre),
    }


def evento(event_id: str, titulo: str, torneo: str, categoria: str) -> dict:
    return {
        "id": event_id,
        "titulo": titulo,
        "torneo": torneo,
        "categoria": categoria,
        "tipo_evento": "sencillo",
        "equipo_local": "",
        "equipo_visitante": "",
        "subtitulo": titulo,
        "hora_utc": "2026-08-22T15:00:00Z",
        "duracion_min": 210,
        "logo_torneo": "",
        "logo_local": "",
        "logo_visitante": "",
        "tier": 1,
    }


def ejecutar() -> None:
    vuelta = evento("vuelta", "Vuelta a España", "UCI World Tour", "Ciclismo")
    dp_world = canal("05:30 | DP World Tour | Nexo Championship | HD", inferir_deporte("DP World Tour Golf"))
    match, _, _ = emparejar_sencillo(dp_world, [vuelta])
    assert match is None, "No debe relacionarse ciclismo con un torneo de golf."

    ufc = evento("ufc", "UFC Fight Night Hernandez vs Rodrigues", "UFC", "Combate")
    bkfc = canal("16:00 | BKFC | BKFC Fight Night | HD", inferir_deporte("BKFC Fight Night"))
    match, _, _ = emparejar_sencillo(bkfc, [ufc])
    assert match is None, "No debe relacionarse UFC con BKFC por palabras genéricas."

    sprint = evento("f1", "Dutch Grand Prix Sprint", "Formula 1", "Motor")
    clasificacion = canal("08:00 | Formula 1 | GP Países Bajos Clasificación Sprint", inferir_deporte("Formula 1"))
    match, _, _ = emparejar_sencillo(clasificacion, [sprint])
    assert match is None, "Clasificación sprint no es carrera sprint."

    carrera_sprint = canal("10:00 | Formula 1 | Dutch Grand Prix Sprint Race", inferir_deporte("Formula 1"))
    match, _, _ = emparejar_sencillo(carrera_sprint, [sprint])
    assert match is sprint, "Una carrera sprint sí debe asociarse al evento sprint oficial."

    cycling = evento("cycling", "World Tour Vancouver", "UCI World Tour", "Ciclismo")
    oficial, puntuacion, _ = buscar_evento_oficial("Mundial de hockey hierba · Alemania - España", [cycling])
    assert oficial is None and puntuacion == 0, "Hockey no puede heredar categoría Cycling."

    probable = construir_evento_epg(
        "DIRECTO · Mundial de hockey hierba · Alemania - España",
        "",
        datetime(2026, 8, 22, 18, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2026, 8, 22, 20, 0, tzinfo=ZoneInfo("UTC")),
        [{"nombre": "TELEDEPORTE", "id_xtream": "1"}],
        [cycling],
    )
    assert probable is not None, "Una emisión de hockey en directo puede conservarse como probable."
    assert probable["categoria"] == "Hockey", "La emisión probable debe conservar su propia categoría."
    assert probable["estado"] == "probable", "La emisión sin agenda oficial debe quedar marcada como probable."

    requeridos = {"id", "titulo", "torneo", "categoria", "hora_utc", "duracion_min", "fuentes"}
    assert requeridos.issubset(probable), "La salida debe mantener los campos esenciales de Android TV."


if __name__ == "__main__":
    ejecutar()
    print("PRUEBAS_DE_REGRESION_OK")
