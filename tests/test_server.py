"""Servidor web.

El HUD no tiene lógica propia: sólo traduce eventos del bus a pantalla y
clics a órdenes. Lo que hay que fijar aquí es ese contrato, más el fallo real
que costó encontrar: el WebSocket devolvía 403 porque, con
`from __future__ import annotations`, FastAPI no podía resolver el tipo
`WebSocket` importado dentro de una función.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from jarvis.audio.capture import FakeMicStream, MicStream
from jarvis.audio.player import NullPlayer
from jarvis.audio.stt import FakeTranscriber
from jarvis.audio.wakeword import NullWakeWord
from jarvis.core.core import JarvisCore
from jarvis.events import EventType
from jarvis.server.app import _atender, crear_app

from .conftest import FakeAgent, FakeTTS

fastapi_testclient = pytest.importorskip("fastapi.testclient")


def construir_core(settings, *, mic=None):
    return JarvisCore(
        settings,
        agent=FakeAgent(),
        tts=FakeTTS(),
        player=NullPlayer(),
        mic=mic if mic is not None else FakeMicStream(np.zeros(0, dtype=np.float32)),
        transcriber=FakeTranscriber(),
        wakeword=NullWakeWord(),
    )


@pytest.fixture
def cliente(settings):
    core = construir_core(settings)
    with fastapi_testclient.TestClient(crear_app(core)) as c:
        c.core = core
        yield c


class TestPaginas:
    def test_sirve_el_hud(self, cliente):
        r = cliente.get("/")
        assert r.status_code == 200
        assert "J.A.R.V.I.S." in r.text

    def test_sirve_los_estaticos(self, cliente):
        for ruta in ("/estaticos/estilo.css", "/estaticos/hud.js"):
            assert cliente.get(ruta).status_code == 200, ruta


class TestApiEstado:
    def test_devuelve_lo_que_el_hud_necesita(self, cliente):
        datos = cliente.get("/api/estado").json()
        for clave in ("state", "coste_usd", "modelo", "voz", "wake_word", "microfono"):
            assert clave in datos, f"falta {clave}"
        assert datos["state"] == "dormido"

    def test_avisa_de_que_no_hay_microfono(self, cliente):
        # Con un micrófono falso, el HUD desactiva el botón de escuchar; si
        # mintiéramos aquí, el usuario dejaría el núcleo colgado esperando voz.
        assert cliente.get("/api/estado").json()["microfono"] is False

    def test_reconoce_un_microfono_real(self, settings):
        core = construir_core(settings, mic=MicStream())
        with fastapi_testclient.TestClient(crear_app(core)) as c:
            assert c.get("/api/estado").json()["microfono"] is True


class TestWebSocket:
    def test_conecta_y_manda_el_estado_inicial(self, cliente):
        """El fallo del 403 vivía justo aquí."""
        with cliente.websocket_connect("/ws") as ws:
            primero = ws.receive_json()
        assert primero["type"] == "state_changed"
        assert primero["data"]["state"] == "dormido"

    def test_reenvia_los_eventos_del_bus(self, cliente):
        with cliente.websocket_connect("/ws") as ws:
            ws.receive_json()  # el estado inicial
            cliente.core.bus.emit(EventType.LOG, message="hola")
            evento = ws.receive_json()
        assert evento["type"] == "log"
        assert evento["data"]["message"] == "hola"


class TestOrdenes:
    """Lo que el navegador puede pedirle al núcleo."""

    async def test_texto_lanza_un_turno(self, settings):
        core = construir_core(settings)
        await core.start()
        try:
            await _atender(core, {"type": "texto", "text": "hola"})
            await asyncio.sleep(0.25)
        finally:
            await core.stop()
        assert core.agent.preguntas == ["hola"]

    async def test_el_texto_vacio_se_ignora(self, settings):
        core = construir_core(settings)
        await core.start()
        try:
            await _atender(core, {"type": "texto", "text": "   "})
            await asyncio.sleep(0.1)
        finally:
            await core.stop()
        assert core.agent.preguntas == []

    async def test_interrumpir_corta_el_audio(self, settings):
        core = construir_core(settings)
        cortes = {"n": 0}
        core.player.interrumpir = lambda: cortes.__setitem__("n", cortes["n"] + 1)

        await _atender(core, {"type": "interrumpir"})
        assert cortes["n"] == 1

    async def test_una_orden_desconocida_no_revienta(self, settings):
        # El navegador es código que no controlamos del todo; un mensaje
        # inesperado no puede tumbar la conexión.
        core = construir_core(settings)
        await _atender(core, {"type": "algo_que_no_existe"})
        await _atender(core, {})
