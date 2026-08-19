"""Máquina de estados completa, sin micrófono ni tarjeta de sonido.

Estos tests recorren el ciclo real —dormido, despertar, escuchar, transcribir,
pensar, hablar— sustituyendo únicamente lo que toca hardware. La lógica que se
ejercita es exactamente la que correrá en Windows.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.audio.player import NullPlayer
from jarvis.audio.stt import FakeTranscriber
from jarvis.audio.vad import Endpointer
from jarvis.audio.wakeword import NullWakeWord
from jarvis.core.agent import Delta, Done, ToolCall
from jarvis.core.core import JarvisCore
from jarvis.events import EventType, State

from .conftest import ControlledMic, FakeAgent, FakeTTS, ScriptedDetector, TriggerWakeWord


def construir(settings, *, agent=None, tts=None, mic=None, wakeword=None,
              transcriber=None, probabilidades=None, frames=400):
    """Monta un núcleo con dobles. Devuelve (core, eventos)."""
    detector = ScriptedDetector(probabilidades or [0.0])
    core = JarvisCore(
        settings,
        agent=agent or FakeAgent(),
        tts=tts or FakeTTS(),
        player=NullPlayer(),
        mic=mic or ControlledMic(frames),
        transcriber=transcriber or FakeTranscriber(["abre el informe"]),
        wakeword=wakeword or NullWakeWord(),
        endpointer=Endpointer(
            detector,
            threshold=0.5,
            silence_ms=settings.vad.silence_ms,
            min_speech_ms=settings.vad.min_speech_ms,
        ),
        barge_detector=ScriptedDetector([0.0]),
    )
    eventos = []
    core.bus.on(eventos.append)
    return core, eventos


def tipos(eventos) -> list[str]:
    return [e.type.value for e in eventos]


async def esperar_hasta(condicion, timeout: float = 5.0, intervalo: float = 0.005):
    """Espera a que se cumpla una condición, o falla el test.

    El bucle de audio corre en segundo plano igual que en producción, así que
    los tests no pueden asumir un orden de ejecución: tienen que esperar a
    que ocurra lo que esperan.
    """
    limite = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < limite:
        if condicion():
            return
        await asyncio.sleep(intervalo)
    raise AssertionError("La condición no se cumplió a tiempo")


class NucleoEnMarcha:
    """Arranca el núcleo con su bucle de audio y lo para al salir."""

    def __init__(self, core) -> None:
        self.core = core
        self._bucle = None

    async def __aenter__(self):
        await self.core.start()
        self._bucle = asyncio.create_task(self.core.run())
        return self.core

    async def __aexit__(self, *exc):
        if self._bucle is not None:
            self._bucle.cancel()
            try:
                await self._bucle
            except (asyncio.CancelledError, Exception):
                pass
        await self.core.stop()


class TestResponder:
    """`responder()` es el camino que también usará la interfaz web."""

    async def test_ciclo_completo_de_respuesta(self, settings):
        tts = FakeTTS()
        core, eventos = construir(settings, tts=tts)
        await core.start()
        try:
            await core.responder("hola")
        finally:
            await core.stop()

        assert core.agent.preguntas == ["hola"]
        assert "Hola. ¿Qué necesita?" in " ".join(tts.dicho)
        assert core.state is State.DORMIDO

        t = tipos(eventos)
        assert "assistant_delta" in t
        assert "assistant_sentence" in t
        assert "assistant_done" in t
        assert "cost_update" in t

    async def test_habla_por_frases_no_de_golpe(self, settings):
        """Lo que hace que arranque a hablar antes de terminar de pensar."""
        agent = FakeAgent([
            Delta("Primera frase bastante larga. "),
            Delta("Segunda frase bastante larga. "),
            Delta("Tercera y última frase larga."),
            Done(cost_usd=0.02),
        ])
        tts = FakeTTS()
        core, _ = construir(settings, agent=agent, tts=tts)
        await core.start()
        try:
            await core.responder("cuéntame")
        finally:
            await core.stop()

        assert len(tts.dicho) >= 3, f"debería trocear en frases, dijo: {tts.dicho}"

    async def test_las_herramientas_se_publican(self, settings):
        agent = FakeAgent([
            Delta("Voy a mirarlo. "),
            ToolCall("Read", {"file_path": "/tmp/x.txt"}),
            Delta("Ya está revisado y todo correcto."),
            Done(cost_usd=0.01),
        ])
        core, eventos = construir(settings, agent=agent)
        await core.start()
        try:
            await core.responder("revisa el archivo")
        finally:
            await core.stop()

        herramientas = [e for e in eventos if e.type is EventType.TOOL_USE]
        assert len(herramientas) == 1
        assert herramientas[0].data["name"] == "Read"

    async def test_el_markdown_no_llega_al_tts(self, settings):
        agent = FakeAgent([
            Delta("Está en el archivo **importante** de configuración."),
            Done(),
        ])
        tts = FakeTTS()
        core, _ = construir(settings, agent=agent, tts=tts)
        await core.start()
        try:
            await core.responder("dónde está")
        finally:
            await core.stop()

        assert all("**" not in frase for frase in tts.dicho)

    async def test_un_error_del_agente_se_dice_en_voz_alta(self, settings):
        agent = FakeAgent([Done(error="No hay saldo en la cuenta.")])
        tts = FakeTTS()
        core, eventos = construir(settings, agent=agent, tts=tts)
        await core.start()
        try:
            await core.responder("hola")
        finally:
            await core.stop()

        assert "error" in tipos(eventos)
        assert any("saldo" in f for f in tts.dicho), "el error debe oírse, no sólo verse"
        assert core.state is State.DORMIDO, "tras un error debe volver a estar listo"


class TestCicloDeVoz:
    """El camino completo: te oye, te entiende, piensa y contesta."""

    # Guion del detector: voz sostenida y luego silencio, que es lo que cierra
    # la frase. Sólo avanza mientras el núcleo está escuchando.
    HABLA_Y_CALLA = [0.9] * 10 + [0.0] * 40

    async def test_el_wake_word_dispara_la_escucha(self, settings):
        core, eventos = construir(
            settings,
            wakeword=TriggerWakeWord(en_frame=3),
            probabilidades=self.HABLA_Y_CALLA,
            transcriber=FakeTranscriber(["qué hora es"]),
        )
        async with NucleoEnMarcha(core):
            await esperar_hasta(lambda: core.agent.preguntas)

        t = tipos(eventos)
        assert "wake_detected" in t
        assert "final_transcript" in t
        assert core.agent.preguntas == ["qué hora es"]

    async def test_estados_en_el_orden_correcto(self, settings):
        core, eventos = construir(
            settings,
            wakeword=TriggerWakeWord(3),
            probabilidades=self.HABLA_Y_CALLA,
        )
        async with NucleoEnMarcha(core):
            await esperar_hasta(
                lambda: any(e.type is EventType.ASSISTANT_DONE for e in eventos)
            )

        estados = [e.data["state"] for e in eventos if e.type is EventType.STATE_CHANGED]
        for esperado in ("escuchando", "transcribiendo", "pensando"):
            assert esperado in estados, f"falta el estado {esperado}: {estados}"
        assert estados.index("escuchando") < estados.index("transcribiendo")
        assert estados.index("transcribiendo") < estados.index("pensando")

    async def test_si_no_entiende_nada_lo_dice_y_vuelve_a_dormir(self, settings):
        tts = FakeTTS()
        core, eventos = construir(
            settings,
            tts=tts,
            wakeword=TriggerWakeWord(3),
            probabilidades=self.HABLA_Y_CALLA,
            transcriber=FakeTranscriber([""]),  # no se entendió nada
        )
        async with NucleoEnMarcha(core):
            await esperar_hasta(
                lambda: any("entendido" in f or "escapado" in f for f in tts.dicho)
            )
            await esperar_hasta(lambda: core.state is State.DORMIDO)

        assert core.agent.preguntas == [], "no debe molestar a Claude con audio vacío"

    async def test_el_microfono_se_vacia_tras_hablar(self, settings):
        """Si no, transcribiría el eco de su propia voz como si fuera tuyo."""
        mic = ControlledMic()
        core, _ = construir(settings, mic=mic)
        await core.start()
        try:
            await core.responder("hola")
        finally:
            await core.stop()

        assert mic.vaciados > 0


class TestConfirmacionPorVoz:
    """La última barrera antes de que J.A.R.V.I.S. toque tus archivos."""

    HABLA_Y_CALLA = [0.9] * 10 + [0.0] * 40

    async def test_un_si_autoriza(self, settings):
        core, _ = construir(
            settings,
            probabilidades=self.HABLA_Y_CALLA,
            transcriber=FakeTranscriber(["sí, adelante"]),
        )
        async with NucleoEnMarcha(core):
            resultado = await asyncio.wait_for(
                core.confirmar_por_voz("¿Borro el archivo?"), timeout=8
            )
        assert resultado is True

    async def test_un_no_deniega(self, settings):
        core, _ = construir(
            settings,
            probabilidades=self.HABLA_Y_CALLA,
            transcriber=FakeTranscriber(["no, cancela"]),
        )
        async with NucleoEnMarcha(core):
            resultado = await asyncio.wait_for(
                core.confirmar_por_voz("¿Borro el archivo?"), timeout=8
            )
        assert resultado is False

    async def test_el_silencio_deniega(self, settings):
        """La regla más importante del proyecto: callarse nunca autoriza nada."""
        settings.permissions.confirm_timeout_s = 0.15
        core, _ = construir(settings, probabilidades=[0.0])  # nadie habla nunca

        async with NucleoEnMarcha(core):
            resultado = await asyncio.wait_for(
                core.confirmar_por_voz("¿Ejecuto el comando?"), timeout=8
            )
        assert resultado is False

    async def test_una_respuesta_ambigua_provoca_repregunta(self, settings):
        # Habla, calla, vuelve a hablar: dos turnos de confirmación.
        probs = [0.9] * 10 + [0.0] * 10 + [0.9] * 10 + [0.0] * 40
        tts = FakeTTS()
        core, _ = construir(
            settings,
            tts=tts,
            probabilidades=probs,
            transcriber=FakeTranscriber(["mmm no sé", "sí"]),
        )
        async with NucleoEnMarcha(core):
            resultado = await asyncio.wait_for(
                core.confirmar_por_voz("¿Lo hago?"), timeout=8
            )

        assert resultado is True
        assert any("¿Sí o no?" in f for f in tts.dicho), "debería haber repreguntado"

    async def test_al_denegar_lo_dice_en_voz_alta(self, settings):
        tts = FakeTTS()
        core, _ = construir(
            settings,
            tts=tts,
            probabilidades=self.HABLA_Y_CALLA,
            transcriber=FakeTranscriber(["no"]),
        )
        async with NucleoEnMarcha(core):
            await asyncio.wait_for(core.confirmar_por_voz("¿Lo hago?"), timeout=8)

        assert any("no lo hago" in f.lower() for f in tts.dicho)


class TestParadaLimpia:
    async def test_stop_es_idempotente(self, settings):
        core, _ = construir(settings)
        await core.start()
        await core.stop()
        await core.stop()  # no debe reventar

    async def test_un_frame_malo_no_tumba_el_bucle(self, settings):
        """Un fallo procesando audio no puede dejar sordo a J.A.R.V.I.S."""
        core, eventos = construir(settings, mic=ControlledMic(20))

        original = core._procesar_frame
        llamadas = {"n": 0}

        def roto(frame):
            llamadas["n"] += 1
            if llamadas["n"] == 3:
                raise ValueError("frame corrupto")
            return original(frame)

        core._procesar_frame = roto

        await core.start()
        try:
            await asyncio.wait_for(core.run(), timeout=8)
        finally:
            await core.stop()

        assert llamadas["n"] == 20, "el bucle debe seguir tras el fallo"
        assert any(e.type is EventType.ERROR for e in eventos)


@pytest.mark.parametrize("estado", list(State))
def test_todos_los_estados_tienen_nombre_legible(estado):
    assert estado.value and estado.value.islower()
