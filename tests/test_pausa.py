"""La pausa del micrófono.

Sirve para tener visita en casa sin cerrar J.A.R.V.I.S. Y como se activa desde
un icono de la bandeja, la parte delicada no es pausar: es que la pausa **no se
levante sola** por un camino que nadie miró.
"""

from __future__ import annotations

import asyncio

from jarvis.events import EventType, State

from .test_core import NucleoEnMarcha, construir, esperar_hasta, tipos


class TestAlternarLaPausa:
    async def test_arranca_escuchando(self, settings):
        core, _ = construir(settings)
        assert core.pausado is False
        assert core.state is State.DORMIDO

    async def test_pausar_lo_deja_en_pausa(self, settings):
        core, eventos = construir(settings)
        core.pausar()
        assert core.pausado is True
        assert core.state is State.PAUSADO
        assert "state_changed" in tipos(eventos)

    async def test_reanudar_lo_devuelve_al_reposo(self, settings):
        core, _ = construir(settings)
        core.pausar()
        core.reanudar()
        assert core.pausado is False
        assert core.state is State.DORMIDO

    async def test_alternar_dice_como_ha_quedado(self, settings):
        core, _ = construir(settings)
        assert core.alternar_pausa() is True
        assert core.alternar_pausa() is False

    async def test_pausar_dos_veces_no_hace_nada_raro(self, settings):
        core, _ = construir(settings)
        core.pausar()
        core.pausar()
        assert core.pausado is True
        core.reanudar()
        assert core.pausado is False


class TestNoEscuchaEnPausa:
    async def test_ignora_la_palabra_clave(self, settings):
        from .conftest import TriggerWakeWord

        core, eventos = construir(settings, wakeword=TriggerWakeWord(en_frame=3), frames=40)
        core.pausar()

        async with NucleoEnMarcha(core):
            await asyncio.sleep(0.2)

        assert "wake_detected" not in tipos(eventos), "se ha despertado estando en pausa"

    async def test_reanudado_vuelve_a_oir_la_palabra_clave(self, settings):
        from .conftest import TriggerWakeWord

        core, eventos = construir(settings, wakeword=TriggerWakeWord(en_frame=3), frames=40)
        core.pausar()
        core.reanudar()

        async with NucleoEnMarcha(core):
            await esperar_hasta(lambda: "wake_detected" in tipos(eventos))

    async def test_el_atajo_avisa_en_vez_de_callarse(self, settings):
        # Un fallo mudo es indistinguible de estar roto: quien pulsa el atajo
        # tiene que enterarse de por qué no le escucha.
        core, eventos = construir(settings)
        core.pausar()

        await core.escuchar_ahora()

        avisos = [e for e in eventos if e.type is EventType.LOG]
        assert avisos, "no ha dicho nada al ignorar el atajo"
        assert "pausa" in avisos[-1].data["message"].lower()
        assert core.state is State.PAUSADO

    async def test_reanudado_el_atajo_deja_de_quejarse(self, settings):
        core, eventos = construir(settings)
        core.pausar()
        core.reanudar()
        eventos.clear()

        await core.escuchar_ahora()

        quejas = [
            e for e in eventos
            if e.type is EventType.LOG and "pausa" in e.data.get("message", "").lower()
        ]
        assert not quejas, "sigue creyéndose en pausa después de reanudar"


class TestLaPausaSobrevive:
    async def test_un_turno_escrito_no_la_levanta(self, settings):
        # El agujero real: la pausa es del micrófono, no del asistente. Se le
        # puede escribir desde el HUD estando en pausa, y ese turno acaba
        # volviendo a reposo. Si el reposo fuera DORMIDO a secas, le habríamos
        # reabierto el micrófono a quien pidió que no le escucharan.
        core, _ = construir(settings)

        async with NucleoEnMarcha(core):
            core.pausar()
            await core.responder("hola")
            await esperar_hasta(lambda: core.state in (State.DORMIDO, State.PAUSADO))

            assert core.pausado is True
            assert core.state is State.PAUSADO

    async def test_el_vigilante_no_la_recupera(self, settings):
        # El vigilante existe para desatascar estados colgados. Una pausa no
        # está colgada: está esperando a que el usuario diga cuándo.
        settings.audio.watchdog_s = 0.1
        core, eventos = construir(settings)
        await core.start()
        try:
            core.pausar()
            await asyncio.sleep(0.4)
        finally:
            await core.stop()

        assert core.state is State.PAUSADO
        assert not [e for e in eventos if e.type is EventType.ERROR]

    async def test_recuperarse_de_un_atasco_respeta_la_pausa(self, settings):
        core, _ = construir(settings)
        core.pausar()
        await core._recuperarse()
        assert core.state is State.PAUSADO


class TestParidadConElHud:
    async def test_el_estado_tiene_color_en_la_consola(self):
        from jarvis.ui.console import _COLOR_ESTADO, _ICONO_ESTADO

        assert State.PAUSADO.value in _COLOR_ESTADO
        assert State.PAUSADO.value in _ICONO_ESTADO

    async def test_el_hud_web_sabe_pintarlo(self):
        from pathlib import Path

        estaticos = Path(__file__).resolve().parent.parent / "jarvis" / "server" / "static"
        assert "pausado:" in (estaticos / "hud.js").read_text(encoding="utf-8")
        assert 'data-estado="pausado"' in (estaticos / "estilo.css").read_text(encoding="utf-8")
