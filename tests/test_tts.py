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
import types

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

    def test_xtts_sin_dependencia_cae_a_edge(self, settings, monkeypatch):
        # La CI no instala PyTorch/TTS (extra `xtts`, pesado a propósito):
        # este es el único camino de XTTS que se puede probar allí de verdad.
        monkeypatch.setitem(sys.modules, "torch", None)
        monkeypatch.setitem(sys.modules, "TTS", None)
        monkeypatch.setitem(sys.modules, "TTS.api", None)
        settings.tts.engine = "xtts"
        assert isinstance(crear_motor(settings), EdgeTTS)

    def test_xtts_con_dependencia_da_xtts(self, settings, monkeypatch):
        _instalar_xtts_falso(monkeypatch)
        settings.tts.engine = "xtts"

        from jarvis.audio.tts.xtts import XttsTTS

        assert isinstance(crear_motor(settings), XttsTTS)


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


class _ModeloXttsFalso:
    """Sustituye a `TTS.api.TTS`: nunca se carga un modelo real en la suite."""

    instancias = 0

    def __init__(self, _nombre: str) -> None:
        _ModeloXttsFalso.instancias += 1
        self.dispositivo: str | None = None
        self.llamadas: list[dict] = []
        self.reventar = False
        tts_model = types.SimpleNamespace()
        tts_model.float = lambda: setattr(tts_model, "fp32_forzado", True)
        self.synthesizer = types.SimpleNamespace(
            output_sample_rate=24_000, tts_model=tts_model
        )

    def to(self, dispositivo: str) -> _ModeloXttsFalso:
        self.dispositivo = dispositivo
        return self

    def tts(self, **kwargs) -> list[float]:  # noqa: ANN003
        self.llamadas.append(kwargs)
        if self.reventar:
            raise RuntimeError("el modelo no pudo sintetizar")
        return [0.0, 0.5, -0.5, 1.0]


def _instalar_xtts_falso(monkeypatch, *, cuda_disponible: bool = False) -> None:
    """Registra `torch` y `TTS.api` falsos en `sys.modules`.

    Cargar el modelo real de XTTS-v2 (~2 GB, necesita PyTorch) no tiene
    sentido en una suite que corre en cada commit: esto prueba la lógica de
    `XttsTTS` (carga única, texto vacío, fp32 en CUDA, propagación de
    errores) sin tocar nada pesado.
    """
    _ModeloXttsFalso.instancias = 0

    torch_falso = types.ModuleType("torch")
    torch_falso.cuda = types.SimpleNamespace(is_available=lambda: cuda_disponible)
    monkeypatch.setitem(sys.modules, "torch", torch_falso)

    tts_api_falso = types.ModuleType("TTS.api")
    tts_api_falso.TTS = _ModeloXttsFalso
    tts_falso = types.ModuleType("TTS")
    tts_falso.api = tts_api_falso
    monkeypatch.setitem(sys.modules, "TTS", tts_falso)
    monkeypatch.setitem(sys.modules, "TTS.api", tts_api_falso)


class TestXttsTTS:
    def _motor(self, settings, **cambios):  # noqa: ANN001, ANN201
        from jarvis.audio.tts.xtts import XttsTTS

        settings.tts.engine = "xtts"
        for clave, valor in cambios.items():
            setattr(settings.tts, clave, valor)
        return XttsTTS(settings)

    async def test_texto_vacio_no_llama_al_modelo(self, settings, monkeypatch):
        _instalar_xtts_falso(monkeypatch)
        motor = self._motor(settings)

        resultado = await motor.sintetizar("   ")

        assert len(resultado) == 0
        assert motor._modelo.llamadas == []

    async def test_la_salida_es_pcm_int16(self, settings, monkeypatch):
        _instalar_xtts_falso(monkeypatch)
        motor = self._motor(settings)

        resultado = await motor.sintetizar("hola, señor")

        assert resultado.dtype == np.int16
        assert len(resultado) == 4  # las 4 muestras que devuelve el doble

    async def test_el_modelo_se_carga_una_sola_vez(self, settings, monkeypatch):
        _instalar_xtts_falso(monkeypatch)
        motor = self._motor(settings)

        await motor.sintetizar("uno")
        await motor.sintetizar("dos")
        await motor.sintetizar("tres")

        assert _ModeloXttsFalso.instancias == 1
        assert len(motor._modelo.llamadas) == 3

    async def test_un_fallo_del_modelo_no_se_traga_en_silencio(self, settings, monkeypatch):
        _instalar_xtts_falso(monkeypatch)
        motor = self._motor(settings)
        motor._modelo.reventar = True

        with pytest.raises(RuntimeError):
            await motor.sintetizar("hola")

    async def test_sin_speaker_wav_usa_hablante_preentrenado(self, settings, monkeypatch):
        _instalar_xtts_falso(monkeypatch)
        motor = self._motor(settings, xtts_speaker_wav="")

        await motor.sintetizar("hola")

        llamada = motor._modelo.llamadas[0]
        assert "speaker" in llamada
        assert "speaker_wav" not in llamada

    async def test_con_speaker_wav_clona_esa_voz(self, settings, monkeypatch, tmp_path):
        _instalar_xtts_falso(monkeypatch)
        referencia = tmp_path / "mi_voz.wav"
        motor = self._motor(settings, xtts_speaker_wav=str(referencia))

        await motor.sintetizar("hola")

        llamada = motor._modelo.llamadas[0]
        assert llamada["speaker_wav"] == str(referencia)
        assert "speaker" not in llamada

    def test_dispositivo_auto_usa_cpu_sin_cuda(self, settings, monkeypatch):
        _instalar_xtts_falso(monkeypatch, cuda_disponible=False)
        motor = self._motor(settings, xtts_dispositivo="auto")

        assert motor._dispositivo == "cpu"
        assert motor._modelo.dispositivo == "cpu"

    def test_dispositivo_auto_usa_cuda_si_hay(self, settings, monkeypatch):
        _instalar_xtts_falso(monkeypatch, cuda_disponible=True)
        motor = self._motor(settings, xtts_dispositivo="auto")

        assert motor._dispositivo == "cuda"

    def test_en_cuda_fuerza_fp32(self, settings, monkeypatch):
        # Pascal (GTX 10xx, sin tensor cores) es más lento en fp16 que en
        # fp32: al revés que en GPUs modernas, aquí NO toca hacer `.half()`.
        _instalar_xtts_falso(monkeypatch, cuda_disponible=True)
        motor = self._motor(settings, xtts_dispositivo="cuda")

        assert motor._modelo.synthesizer.tts_model.fp32_forzado is True

    def test_en_cpu_no_toca_precision(self, settings, monkeypatch):
        _instalar_xtts_falso(monkeypatch, cuda_disponible=False)
        motor = self._motor(settings, xtts_dispositivo="cpu")

        assert not hasattr(motor._modelo.synthesizer.tts_model, "fp32_forzado")
