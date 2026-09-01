# -*- coding: utf-8 -*-
"""Pruebas de regresión integrales del sistema curador e inyector."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone, date

import curador_eventos as curador
import inyector_epg as epg
from resolvedor_logos import resolver_logo_torneo


class PruebasSistemaCurador(unittest.TestCase):
    def setUp(self) -> None:
        self.tz = curador.obtener_zona_aplicacion()
        self.hoy = datetime.now(self.tz).replace(hour=10, minute=0, second=0, microsecond=0)

    def test_logos_envueltos_en_cdn_proxy_para_evitar_error_403(self) -> None:
        logo = resolver_logo_torneo("WTA Cincinnati Open", "Tenis")
        self.assertTrue(logo.startswith("https://wsrv.nl/?url="))

    def test_epg_sin_marca_de_directo_es_rechazado_estrictamente(self) -> None:
        ahora = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
        tarjeta = epg.construir_evento_epg(
            "UFC 330: Makhachev vs Machado Garry", "", ahora, ahora + timedelta(hours=2),
            [{"nombre": "SP: ES: Eurosport 2 HD", "id_xtream": "10"}], [], ahora, consenso_directo=False,
            canal_epg="E2"
        )
        self.assertIsNone(tarjeta)

    def test_epg_con_consenso_directo_crea_evento_competicion(self) -> None:
        ahora = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
        tarjeta = epg.construir_evento_epg(
            "Snooker : Wuhan Open - Día 1", "", ahora, ahora + timedelta(hours=2),
            [{"nombre": "SP: ES: Eurosport 1 HD", "id_xtream": "10"}], [], ahora, consenso_directo=True,
            canal_epg="E1"
        )
        self.assertIsNotNone(tarjeta)
        self.assertEqual(tarjeta["categoria"], "Snooker")
        self.assertEqual(tarjeta["tipo_evento"], "sencillo")
        self.assertEqual(tarjeta["modo_presentacion"], "competicion")
        self.assertEqual(tarjeta["canal_epg"], "E1")
        self.assertTrue(tarjeta["logo_torneo"].startswith("https://wsrv.nl/?url="))

    def test_clave_canal_epg_no_confunde_primera_federacion_con_eurosport(self) -> None:
        canal_rfef = "SP: ES: 1ª Federación FHD"
        self.assertIsNone(epg.clave_canal_epg(canal_rfef))

        canal_e1 = "SP: ES: Eurosport 1 FHD"
        canal_e2 = "SP: ES: Eurosport 2 HD"
        canal_tdp = "SP: ES: Teledeporte FHD"

        self.assertEqual(epg.clave_canal_epg(canal_e1), "E1")
        self.assertEqual(epg.clave_canal_epg(canal_e2), "E2")
        self.assertEqual(epg.clave_canal_epg(canal_tdp), "TDP")

    def test_filtro_canales_espana_descarta_senales_extranjeras(self) -> None:
        self.assertTrue(epg.es_canal_espana("SP: ES: Eurosport 1 FHD"))
        self.assertTrue(epg.es_canal_espana("SP: ES: Teledeporte (Movil)"))
        self.assertFalse(epg.es_canal_espana("SP: DE: Eurosport 1"))
        self.assertFalse(epg.es_canal_espana("SP: FR: Eurosport 2"))
        self.assertFalse(epg.es_canal_espana("SP: UK: Eurosport 1 HD"))
        self.assertFalse(epg.es_canal_espana("SP: PL: Eurosport 2 HD"))

    def test_consolidar_eventos_epg_mantiene_aislados_e1_y_e2(self) -> None:
        ahora = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)
        evento_e1 = epg.construir_evento_epg(
            "DIRECTO Snooker: Wuhan Open", "", ahora, ahora + timedelta(hours=2),
            [{"nombre": "SP: ES: Eurosport 1 FHD", "id_xtream": "101"}], [], ahora, consenso_directo=True,
            canal_epg="E1"
        )
        evento_e2 = epg.construir_evento_epg(
            "DIRECTO Snooker: Wuhan Open", "", ahora, ahora + timedelta(hours=2),
            [{"nombre": "SP: ES: Eurosport 2 HD", "id_xtream": "202"}], [], ahora, consenso_directo=True,
            canal_epg="E2"
        )
        self.assertIsNotNone(evento_e1)
        self.assertIsNotNone(evento_e2)

        consolidados = epg.consolidar_eventos_epg([evento_e1, evento_e2])
        self.assertEqual(len(consolidados), 2)
        self.assertEqual(len(consolidados[0]["fuentes"]), 1)
        self.assertEqual(len(consolidados[1]["fuentes"]), 1)
        self.assertEqual(consolidados[0]["fuentes"][0]["id_xtream"], "101")
        self.assertEqual(consolidados[1]["fuentes"][0]["id_xtream"], "202")

    def test_canal_xtream_con_duelo_en_subtitulo_se_analiza_correctamente(self) -> None:
        canal = {
            "id_xtream": "530165",
            "nombre_ui": "13:00 ▪ Little League Baseball ▪ Willemstad Curacao vs. Seoul South Korea ▪ HD ▪▪",
            "texto_normalizado": "13 00 LITTLE LEAGUE BASEBALL WILLEMSTAD CURACAO VS SEOUL SOUTH KOREA HD",
            "tokens": curador.tokenizar("Little League Baseball Willemstad Curacao vs Seoul South Korea"),
            "hora_local": self.hoy.replace(hour=13),
            "categoria_inferida": "Béisbol",
            "sesion": None,
            "motivo_vigencia": "fecha_xtream_hoy"
        }
        evento = curador.crear_probable_xtream(canal, self.tz, {})
        self.assertIsNotNone(evento)
        self.assertEqual(evento["tipo_evento"], "duelo")
        self.assertEqual(evento["equipo_local"], "Willemstad Curacao")
        self.assertEqual(evento["equipo_visitante"], "Seoul South Korea")

    def test_contraste_negativo_descarta_partido_de_ayer_y_semaforo_detecta_stale(self) -> None:
        ayer_hora = self.hoy - timedelta(days=1)
        agenda_ayer = [{
            "id": "apisports_football_1001",
            "titulo": "Real Madrid vs Barcelona",
            "torneo": "LaLiga",
            "categoria": "Fútbol",
            "tipo_evento": "duelo",
            "equipo_local": "Real Madrid",
            "equipo_visitante": "Barcelona",
            "hora_utc": curador.iso_utc(ayer_hora),
        }]
        agenda_hoy = [{
            "id": "apisports_football_2001",
            "titulo": "Arsenal vs Chelsea",
            "torneo": "Premier League",
            "categoria": "Fútbol",
            "tipo_evento": "duelo",
            "equipo_local": "Arsenal",
            "equipo_visitante": "Chelsea",
            "hora_utc": curador.iso_utc(self.hoy),
        }]
        candidatos = [
            {
                "id_xtream": "99901",
                "nombre_ui": "20:00 - LaLiga - Real Madrid vs Barcelona",
                "texto_normalizado": "20 00 LALIGA REAL MADRID VS BARCELONA",
                "tokens": curador.tokenizar("LaLiga Real Madrid vs Barcelona"),
                "hora_local": self.hoy.replace(hour=20),
                "categoria_inferida": "Fútbol",
                "sesion": None,
                "motivo_vigencia": "hora_y_identidad_sin_fecha"
            }
        ]
        eventos, cuarentena, metricas = curador.curar_eventos(agenda_hoy, agenda_ayer, candidatos, self.hoy.date())
        self.assertEqual(len(eventos), 0)
        self.assertEqual(metricas["descartado_partido_de_ayer"], 1)
        self.assertTrue(any(c.get("motivo") == "residuo_partido_de_ayer" for c in cuarentena))

    def test_deporte_independiente_no_presente_en_api_se_conserva(self) -> None:
        agenda_hoy = []
        agenda_ayer = []
        candidatos = [
            {
                "id_xtream": "77701",
                "nombre_ui": "18:00 - Tejo - Campeonato Nacional de Tejo",
                "texto_normalizado": "18 00 TEJO CAMPEONATO NACIONAL DE TEJO",
                "tokens": curador.tokenizar("Tejo Campeonato Nacional de Tejo"),
                "hora_local": self.hoy.replace(hour=18),
                "categoria_inferida": "Tejo",
                "sesion": None,
                "motivo_vigencia": "hora_y_identidad_sin_fecha"
            }
        ]
        eventos, cuarentena, metricas = curador.curar_eventos(agenda_hoy, agenda_ayer, candidatos, self.hoy.date())
        self.assertEqual(len(eventos), 1)
        self.assertEqual(eventos[0]["categoria"], "Tejo")
        self.assertEqual(eventos[0]["metodo_correlacion"], "evento_independiente_verificado")
        self.assertEqual(eventos[0]["fuentes"][0]["id_xtream"], "77701")

    def test_canal_lineal_sin_hora_es_descartado_estrictamente(self) -> None:
        canales_lineales = [
            {"stream_id": "377473", "name": "Surfing+", "category_id": "10"},
            {"stream_id": "377474", "name": "Golf Channel HD", "category_id": "10"},
            {"stream_id": "377475", "name": "USA: ESPN 24/7", "category_id": "10"},
            {"stream_id": "377476", "name": "Tennis TV Direct", "category_id": "10"},
        ]
        for stream in canales_lineales:
            aceptado, motivo = curador.es_candidato(stream, set(), self.hoy)
            self.assertFalse(aceptado, f"El canal lineal '{stream['name']}' no debió ser aceptado")
            self.assertEqual(motivo, "sin_hora_programada")

    def test_formatos_variables_de_hora(self) -> None:
        base = datetime(2026, 9, 1, 0, 0, tzinfo=self.tz)
        casos = [
            ("12:30 - LES - Osasuna vs. Getafe", 12, 30),
            ("12.30 - Serie A - Lecce vs Roma", 12, 30),
            ("08:15 AM - Formula 1 - GP Monza", 8, 15),
            ("02:45 PM - Champions League - Final", 14, 45),
            ("12H30 - Tour de France", 12, 30),
            ("20h45 - Ligue 1 - PSG vs Monaco", 20, 45),
            ("18:00hs - Torneo Apertura - River vs Boca", 18, 0),
            ("19:30 hrs - LMB - Sultanes vs Toros", 19, 30),
            ("[15:00] Tennis - US Open", 15, 0),
        ]
        for texto, hora_esp, min_esp in casos:
            dt = curador.extraer_hora_canal(texto, base)
            self.assertIsNotNone(dt, f"Falló extracción de hora para '{texto}'")
            self.assertEqual(dt.hour, hora_esp, f"Hora incorrecta en '{texto}'")
            self.assertEqual(dt.minute, min_esp, f"Minuto incorrecto en '{texto}'")

    def test_deteccion_fechas_textuales_en_categorias_y_canales(self) -> None:
        hoy_fecha = date(2026, 9, 1)
        # Fecha de ayer: debe retornar False (fuera de jornada)
        self.assertFalse(curador.fecha_xtream_explicita("EVENTOS FUTBOL 31 DE AGOSTO", hoy_fecha))
        self.assertFalse(curador.fecha_xtream_explicita("EVENTOS 31 AGO", hoy_fecha))
        self.assertFalse(curador.fecha_xtream_explicita("EVENTOS 31 AUGUST", hoy_fecha))
        self.assertFalse(curador.fecha_xtream_explicita("EVENTOS 31/08", hoy_fecha))
        self.assertFalse(curador.fecha_xtream_explicita("EVENTOS 31-08-2026", hoy_fecha))

        # Fecha de hoy: debe retornar True
        self.assertTrue(curador.fecha_xtream_explicita("EVENTOS FUTBOL 01 DE SEPTIEMBRE", hoy_fecha))
        self.assertTrue(curador.fecha_xtream_explicita("EVENTOS 1 SEP", hoy_fecha))
        self.assertTrue(curador.fecha_xtream_explicita("EVENTOS 01/09", hoy_fecha))

        # Sin fecha: debe retornar None
        self.assertIsNone(curador.fecha_xtream_explicita("EVENTOS TENNIS", hoy_fecha))
        self.assertIsNone(curador.fecha_xtream_explicita("EVENTOS VARIOS", hoy_fecha))


if __name__ == "__main__":
    unittest.main(verbosity=2)
