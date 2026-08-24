"""Los motores de voz, sin haberlos probado nunca.

`crear_motor`, `decodificar_a_pcm` y `ElevenLabsTTS` no son borradores: hacen
peticiones HTTP de verdad y tienen lógica real de caída al motor gratuito.
Es justo la clase de código "debería funcionar" donde se esconden bugs
silenciosos — el propio arreglo del Ctrl+C nació de uno igual.

Nada aquí toca la red ni SAPI real: `ElevenLabsTTS` se prueba con un doble del
cliente HTTP, y la caída a SAPI se fuerza vía `sys.modules` en vez de confiar
en que esta máquina no sea Windows por casualidad.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

from jarvis.audio.player import TTS_SAMPLE_RATE
from jarvis.audio.tts.base import crear_motor, decodificar_a_pcm
from jarvis.audio.tts.edge import EdgeTTS
from jarvis.audio.tts.elevenlabs import _VOZ_POR_DEFECTO, ElevenLabsTTS

# El remuestreo real necesita PyAV (extra `voice`); la CI no lo instala, así
# que estos tests se saltan en vez de fallar. Mismo patrón que test_icono.py
# con Pillow y test_server.py con fastapi.
av = pytest.importorskip("av", reason="el remuestreo real necesita PyAV (extra `voice`)")


class TestCrearMotor:
    def test_por_defecto_da_edge(self, settings):
        assert isinstance(crear_motor(settings), EdgeTTS)

    def test_elevenlabs_con_key_da_elevenlabs(self, settings):
        settings.tts.engine = "elevenlabs"
        settings.elevenlabs_api_key = "una-clave"
        assert isinstance(crear_motor(settings), ElevenLabsTTS)

    def test_elevenlabs_sin_key_cae_a_edge(self, settings):
        # Sin key no hay forma de hablar con la API: quedarse mudo sería peor
        # que sonar con la voz gratuita.
        settings.tts.engine = "elevenlabs"
        settings.elevenlabs_api_key = ""
        assert isinstance(crear_motor(settings), EdgeTTS)

    def test_sapi_sin_pywin32_cae_a_edge(self, settings, monkeypatch):
        # Se fuerza la ausencia en vez de confiar en que este sandbox no sea
        # Windows: así el test es igual de fiable en la CI, aquí y en el
        # propio Windows de Jeremy si alguna vez le faltara pywin32.
        monkeypatch.setitem(sys.modules, "win32com", None)
        monkeypatch.setitem(sys.modules, "win32com.client", None)
        settings.tts.engine = "sapi"
        assert isinstance(crear_motor(settings), EdgeTTS)


class TestDecodificarAPcm:
    def test_bytes_vacios_da_array_vacio(self):
        resultado = decodificar_a_pcm(b"")
        assert resultado.dtype == np.int16
        assert len(resultado) == 0

    def _codificar_tono(self, *, tasa: int, segundos: float = 0.5) -> bytes:
        """Un tono corto codificado a MP3 en memoria, para no depender de red."""
        import io

        import av

        muestras = int(tasa * segundos)
        t = np.arange(muestras) / tasa
        tono = (np.sin(2 * np.pi * 440 * t) * 0.5 * 32767).astype(np.int16)

        buffer = io.BytesIO()
        with av.open(buffer, mode="w", format="mp3") as contenedor:
            flujo = contenedor.add_stream("mp3", rate=tasa)
            frame = av.AudioFrame.from_ndarray(
                tono.reshape(1, -1), format="s16", layout="mono"
            )
            frame.rate = tasa
            for paquete in flujo.encode(frame):
                contenedor.mux(paquete)
            for paquete in flujo.encode(None):
                contenedor.mux(paquete)
        return buffer.getvalue()

    def test_decodifica_un_tono_real(self):
        mp3 = self._codificar_tono(tasa=TTS_SAMPLE_RATE)
        resultado = decodificar_a_pcm(mp3, TTS_SAMPLE_RATE)

        assert resultado.dtype == np.int16
        assert len(resultado) > 0
        duracion = len(resultado) / TTS_SAMPLE_RATE
        assert 0.4 < duracion < 0.7, f"duración rara: {duracion:.2f}s"

    def test_remuestrea_si_la_fuente_tiene_otra_tasa(self):
        # Codificado a 16 kHz, pedido a 24 kHz: si el remuestreador no
        # actuara, la duración en muestras saldría mal (contaría muestras de
        # 16 kHz como si fueran de 24 kHz).
        mp3 = self._codificar_tono(tasa=16_000, segundos=0.5)
        resultado = decodificar_a_pcm(mp3, TTS_SAMPLE_RATE)

        duracion = len(resultado) / TTS_SAMPLE_RATE
        assert 0.4 < duracion < 0.7, f"parece no haberse remuestreado: {duracion:.2f}s"


class _RespuestaFalsa:
    def __init__(self, contenido: bytes) -> None:
        self.content = contenido
        self._reventar = False

    def hacer_que_falle(self) -> None:
        self._reventar = True

    def raise_for_status(self) -> None:
        if self._reventar:
            raise RuntimeError("el servidor de ElevenLabs dijo que no")


class _ClienteFalso:
    def __init__(self, contenido: bytes = b"") -> None:
        self._respuesta = _RespuestaFalsa(contenido)
        self.llamadas: list[dict] = []
        self.cerrado = False

    async def post(self, url, *, params, headers, json):  # noqa: ANN001
        self.llamadas.append({"url": url, "params": params, "headers": headers, "json": json})
        return self._respuesta

    async def aclose(self) -> None:
        self.cerrado = True


class TestElevenLabsTTS:
    def _motor(self, settings, *, voice_id: str = "voz-de-prueba") -> ElevenLabsTTS:
        settings.tts.engine = "elevenlabs"
        settings.elevenlabs_api_key = "clave-de-prueba"
        settings.tts.elevenlabs_voice_id = voice_id
        return ElevenLabsTTS(settings)

    async def test_texto_vacio_no_llama_a_la_api(self, settings):
        motor = self._motor(settings)
        cliente = _ClienteFalso()
        motor._cliente = cliente

        resultado = await motor.sintetizar("   ")

        assert len(resultado) == 0
        assert cliente.llamadas == [], "no debía llamar a la API para texto vacío"

    async def test_construye_la_peticion_correcta(self, settings):
        motor = self._motor(settings, voice_id="mi-voz")
        cliente = _ClienteFalso()
        motor._cliente = cliente

        await motor.sintetizar("hola, señor")

        assert len(cliente.llamadas) == 1
        llamada = cliente.llamadas[0]
        assert llamada["url"].endswith("/mi-voz")
        assert llamada["headers"]["xi-api-key"] == "clave-de-prueba"
        assert llamada["json"]["text"] == "hola, señor"

    async def test_usa_la_voz_por_defecto_si_no_hay_una_configurada(self, settings):
        motor = self._motor(settings, voice_id="")
        cliente = _ClienteFalso()
        motor._cliente = cliente

        await motor.sintetizar("hola")

        assert cliente.llamadas[0]["url"].endswith(f"/{_VOZ_POR_DEFECTO}")

    async def test_devuelve_el_pcm_tal_cual(self, settings):
        # ElevenLabs ya devuelve PCM: sintetizar() no debe decodificar nada.
        esperado = np.array([1, -2, 300, -4000], dtype=np.int16)
        motor = self._motor(settings)
        cliente = _ClienteFalso(contenido=esperado.tobytes())
        motor._cliente = cliente

        resultado = await motor.sintetizar("hola")

        np.testing.assert_array_equal(resultado, esperado)

    async def test_un_fallo_http_se_propaga(self, settings):
        motor = self._motor(settings)
        cliente = _ClienteFalso()
        cliente._respuesta.hacer_que_falle()
        motor._cliente = cliente

        with pytest.raises(RuntimeError):
            await motor.sintetizar("hola")

    async def test_cerrar_libera_el_cliente_creado(self, settings):
        motor = self._motor(settings)
        cliente = _ClienteFalso()
        motor._cliente = cliente
        await motor.sintetizar("hola")

        await motor.cerrar()

        assert cliente.cerrado is True
        assert motor._cliente is None

    async def test_cerrar_sin_haber_sintetizado_no_revienta(self, settings):
        motor = self._motor(settings)
        await motor.cerrar()  # nunca se creó cliente; no debe lanzar
