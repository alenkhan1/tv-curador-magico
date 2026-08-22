# -*- coding: utf-8 -*-
"""Pruebas sin red del contrato de fecha y correlación del curador v10."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import curador_eventos as curador
import inyector_epg as epg


class PruebasJornadaColombia(unittest.TestCase):
    def setUp(self) -> None:
        self.tz = curador.obtener_zona_aplicacion()
        self.hoy = datetime.now(self.tz).replace(hour=10, minute=0, second=0, microsecond=0)

    def test_fecha_xtream_de_ayer_es_rechazada(self) -> None:
        ayer = self.hoy - timedelta(days=1)
        aceptado, motivo = curador.es_candidato(
            {"name": f"{ayer.strftime('%d/%m')} 19:00 Fútbol Equipo A vs Equipo B", "category_id": ""},
            set(), self.hoy,
        )
        self.assertFalse(aceptado)
        self.assertEqual(motivo, "fecha_xtream_fuera_de_jornada")

    def test_categoria_xtream_de_ayer_es_rechazada_aunque_el_nombre_tenga_hora(self) -> None:
        aceptado, motivo = curador.es_candidato(
            {"name": "19:00 Fútbol Equipo A vs Equipo B", "category_id": "ayer"},
            set(), self.hoy, {"ayer"},
        )
        self.assertFalse(aceptado)
        self.assertEqual(motivo, "categoria_xtream_fuera_de_jornada")

    def test_fecha_xtream_de_hoy_es_aceptada(self) -> None:
        aceptado, motivo = curador.es_candidato(
            {"name": f"{self.hoy.strftime('%d/%m')} 19:00 Fútbol Equipo A vs Equipo B", "category_id": ""},
            set(), self.hoy,
        )
        self.assertTrue(aceptado)
        self.assertEqual(motivo, "fecha_xtream_hoy")

    def test_desfase_de_veintitres_horas_no_puede_confirmar(self) -> None:
        canal = {"hora_local": self.hoy, "categoria_inferida": "Fútbol", "sesion": None}
        evento = {"hora_utc": curador.iso_utc(self.hoy - timedelta(hours=23))}
        cercano, diferencia = curador._proximidad_horaria(canal, evento)
        self.assertFalse(cercano)
        self.assertGreater(diferencia or 0, curador.MAX_DIFERENCIA_MIN)

    def test_epg_de_madrugada_espanola_puede_ser_tarde_colombiana(self) -> None:
        # 23 de agosto, 00:30 CEST corresponde a 22 de agosto, 17:30 en Bogotá.
        inicio = epg.parse_timestamp_epg("20260823003000 +0200")
        self.assertIsNotNone(inicio)
        self.assertEqual(inicio.astimezone(self.tz).date().isoformat(), "2026-08-22")
        self.assertEqual(inicio.astimezone(self.tz).strftime("%H:%M"), "17:30")

    def test_epg_de_madrugada_espanola_puede_pertenecer_a_ayer_colombia(self) -> None:
        # 22 de agosto, 06:30 CEST corresponde a 21 de agosto, 23:30 en Bogotá.
        inicio = epg.parse_timestamp_epg("20260822063000 +0200")
        self.assertIsNotNone(inicio)
        self.assertEqual(inicio.astimezone(self.tz).date().isoformat(), "2026-08-21")

    def test_sesion_formula_uno_no_confunde_sprint_con_clasificacion(self) -> None:
        canal = {"sesion": "clasificacion_sprint", "categoria_inferida": "Motor"}
        carrera = {"titulo": "Gran Premio de Países Bajos Carrera Sprint", "subtitulo": "", "categoria": "Motor"}
        self.assertFalse(curador._sesiones_compatibles(canal, carrera))

    def test_normalizador_api_y_match_xtream_crean_evento_confirmado(self) -> None:
        inicio = self.hoy.replace(hour=19)
        bruto = {
            "fixture": {"id": 501, "timestamp": int(inicio.timestamp()), "status": {"short": "NS"}},
            "league": {"name": "Liga de Prueba", "logo": "liga.png"},
            "teams": {"home": {"name": "Equipo Azul", "logo": "azul.png"}, "away": {"name": "Equipo Rojo", "logo": "rojo.png"}},
        }
        evento = curador.normalizar_evento_api("football", bruto, curador.API_CONFIGS["football"], self.tz)
        self.assertIsNotNone(evento)
        canal = {
            "id_xtream": "99", "nombre_ui": f"{self.hoy.strftime('%d/%m')} 19:00 Fútbol Equipo Azul vs Equipo Rojo",
            "texto_normalizado": "FUTBOL EQUIPO AZUL VS EQUIPO ROJO", "tokens": curador.tokenizar("Fútbol Equipo Azul vs Equipo Rojo"),
            "hora_local": inicio, "categoria_inferida": "Fútbol", "sesion": None, "motivo_vigencia": "fecha_xtream_hoy",
        }
        eventos, descartados, _ = curador.curar_eventos([evento], [canal], self.hoy.date())
        self.assertEqual(len(descartados), 0)
        self.assertEqual(len(eventos), 1)
        self.assertEqual(eventos[0]["estado"], "confirmado")
        self.assertEqual(eventos[0]["fuentes"][0]["id_xtream"], "99")

    def test_tarjeta_probable_conserva_hora_colombia(self) -> None:
        canal = {
            "id_xtream": "77", "nombre_ui": f"{self.hoy.strftime('%d/%m')} 19:00 UFC Fight Night",
            "texto_normalizado": "UFC FIGHT NIGHT", "hora_local": self.hoy.replace(hour=19),
            "categoria_inferida": "Combate", "motivo_vigencia": "fecha_xtream_hoy",
        }
        tarjeta = curador.crear_probable_xtream(canal, self.tz)
        self.assertIsNotNone(tarjeta)
        self.assertEqual(tarjeta["estado"], "probable")
        self.assertEqual(tarjeta["hora_local_producto"], "19:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
