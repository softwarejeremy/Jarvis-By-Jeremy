"""Registro de conversaciones en disco.

Antes de esto, el historial que se repone al conectar vivía sólo en memoria
del proceso del servidor: cerrar J.A.R.V.I.S. lo perdía todo, y en modo voz
puro (sin `--web`) no se registraba nada en absoluto.
"""

from __future__ import annotations

from datetime import date

import pytest

from jarvis.core.historial import Historial
from jarvis.events import EventBus, EventType


@pytest.fixture
def historial(tmp_path):
    return Historial(tmp_path / "conversaciones")


class TestRegistrar:
    def test_un_turno_se_puede_leer_de_vuelta(self, historial):
        historial.registrar("usuario", "hola")

        hoy = date.today().isoformat()
        assert historial.leer(hoy) == [
            {"hora": historial.leer(hoy)[0]["hora"], "quien": "usuario", "texto": "hola"}
        ]

    def test_texto_vacio_no_se_registra(self, historial):
        historial.registrar("usuario", "   ")
        assert historial.dias() == []

    def test_varios_turnos_mantienen_el_orden(self, historial):
        historial.registrar("usuario", "hola")
        historial.registrar("jarvis", "¿En qué le ayudo?")

        turnos = historial.leer(date.today().isoformat())

        assert [t["texto"] for t in turnos] == ["hola", "¿En qué le ayudo?"]
        assert [t["quien"] for t in turnos] == ["usuario", "jarvis"]

    def test_cada_turno_lleva_hora(self, historial):
        historial.registrar("usuario", "hola")
        hora = historial.leer(date.today().isoformat())[0]["hora"]
        assert len(hora) == 8 and hora.count(":") == 2  # "HH:MM:SS"


class TestLeer:
    def test_un_dia_sin_archivo_da_vacio(self, historial):
        assert historial.leer("2020-01-01") == []

    def test_una_linea_corrupta_no_tira_el_dia_entero(self, historial, tmp_path):
        historial.registrar("usuario", "hola")
        hoy = date.today().isoformat()
        archivo = historial.dir / f"{hoy}.jsonl"
        with archivo.open("a", encoding="utf-8") as f:
            f.write("esto no es json\n")
        historial.registrar("jarvis", "sigo aquí")

        turnos = historial.leer(hoy)

        assert [t["texto"] for t in turnos] == ["hola", "sigo aquí"]

    def test_el_limite_se_queda_con_los_mas_recientes(self, historial):
        for i in range(10):
            historial.registrar("usuario", f"mensaje {i}")

        turnos = historial.leer(date.today().isoformat(), limite=3)

        assert [t["texto"] for t in turnos] == ["mensaje 7", "mensaje 8", "mensaje 9"]


class TestDias:
    def test_sin_conversacion_no_hay_dias(self, historial):
        assert historial.dias() == []

    def test_mas_reciente_primero(self, historial, tmp_path):
        # Se escribe directamente en archivos de días concretos: `registrar()`
        # sólo sabe escribir en el día de hoy.
        for dia in ("2024-01-01", "2024-06-15", "2024-03-10"):
            (historial.dir / f"{dia}.jsonl").write_text(
                '{"hora": "00:00:00", "quien": "usuario", "texto": "x"}\n',
                encoding="utf-8",
            )

        assert historial.dias() == ["2024-06-15", "2024-03-10", "2024-01-01"]


class TestEscuchar:
    def test_registra_lo_que_pasa_por_el_bus(self, historial):
        bus = EventBus()
        historial.escuchar(bus)

        bus.emit(EventType.FINAL_TRANSCRIPT, text="hola")
        bus.emit(EventType.ASSISTANT_DONE, text="¿En qué le ayudo?")

        turnos = historial.leer(date.today().isoformat())
        assert [(t["quien"], t["texto"]) for t in turnos] == [
            ("usuario", "hola"),
            ("jarvis", "¿En qué le ayudo?"),
        ]

    def test_las_confirmaciones_no_se_registran(self, historial):
        bus = EventBus()
        historial.escuchar(bus)

        bus.emit(EventType.FINAL_TRANSCRIPT, text="sí", kind="confirmacion")

        assert historial.dias() == []

    def test_las_respuestas_vacias_no_se_registran(self, historial):
        bus = EventBus()
        historial.escuchar(bus)

        bus.emit(EventType.ASSISTANT_DONE, text="")

        assert historial.dias() == []
