# -*- coding: utf-8 -*-
"""Pruebas de regresión del curador e inyector multi-feed."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import curador_eventos as curador
import inyector_epg as epg
from resolvedor_logos import resolver_logo_torneo


class PruebasSistemaCurador(unittest.TestCase):
    def setUp(self) -> None:
        self.tz = curador.obtener_zona_aplicacion()
        self.hoy = datetime.now(self.tz).replace(hour=10, minute=0, second=0, microsecond=0)

    def test_snooker_formato_sencillo_sin_duelo_artificial(self) -> None:
        ahora = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
        tarjeta = epg.construir_evento_epg(
            "Snooker : Wuhan Open - Día 1", "Torneo WST", ahora, ahora + timedelta(hours=2),
            [{"nombre": "Eurosport 1 ES", "id_xtream": "10"}], [], ahora, consenso_directo=True
        )
        self.assertIsNotNone(tarjeta)
        self.assertEqual(tarjeta["categoria"], "Snooker")
        self.assertEqual(tarjeta["tipo_evento"], "sencillo")
        self.assertEqual(tarjeta["equipo_local"], "")
        self.assertEqual(tarjeta["equipo_visitante"], "")
        self.assertEqual(tarjeta["modo_presentacion"], "competicion")
        self.assertIn("World_Snooker_Tour", tarjeta["logo_torneo"])

    def test_consenso_multi_feed_valida_directo_sin_palabra_en_espanol(self) -> None:
        inicio = datetime(2026, 8, 23, 14, 0, tzinfo=timezone.utc)
        fin = inicio + timedelta(hours=2)
        programas = [
            {"clave": "E1", "canal": "ES | Eurosport 1", "inicio": inicio, "fin": fin, "titulo": "Ciclismo: Renewi Tour", "descripcion": ""},
            {"clave": "E1", "canal": "UK | Eurosports 1", "inicio": inicio, "fin": fin, "titulo": "LIVE Cycling: Renewi Tour", "descripcion": ""},
        ]
        consenso = epg.construir_matriz_consenso(programas)
        slot = inicio.strftime("%Y%m%d%H%M")
        self.assertTrue(consenso.get(("E1", slot)))

    def test_descarte_de_archivos_historicos(self) -> None:
        self.assertTrue(epg.es_historico_o_veto("Ciclismo 2023"))
        self.assertTrue(epg.es_historico_o_veto("Mundial de Superbike T2025"))
        self.assertTrue(epg.es_historico_o_veto("Fórmula E T24"))
        self.assertFalse(epg.es_historico_o_veto("Copa del Mundo 2026"))

    def test_prevencion_de_falsos_positivos_por_token_generico_united(self) -> None:
        evento_karonga = {
            "titulo": "Karonga United vs Chitipa United", "torneo": "Super League", "categoria": "Fútbol",
            "equipo_local": "Karonga United", "equipo_visitante": "Chitipa United",
            "hora_utc": curador.iso_utc(self.hoy)
        }
        canal_newcastle = {
            "nombre_ui": "EPL | Newcastle United vs. Liverpool | HD",
            "texto_normalizado": "EPL NEWCASTLE UNITED VS LIVERPOOL HD",
            "tokens": curador.tokenizar("EPL Newcastle United vs Liverpool HD"),
            "hora_local": self.hoy, "categoria_inferida": "Fútbol", "sesion": None
        }
        evento, puntos, _, _ = curador.emparejar_evento(canal_newcastle, [evento_karonga])
        self.assertIsNone(evento)

    def test_resolucion_logo_circuito_oficial(self) -> None:
        logo_cincinnati = resolver_logo_torneo("WTA Cincinnati Open", "Tenis")
        self.assertIn("Cincinnati_Open", logo_cincinnati)

        logo_vuelta = resolver_logo_torneo("La Vuelta Etapa 2", "Ciclismo")
        self.assertIn("La_Vuelta", logo_vuelta)


if __name__ == "__main__":
    unittest.main(verbosity=2)
