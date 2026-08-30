"""Voz de alta calidad: XTTS-v2 (Coqui), corriendo en local.

Gratis y sin cuenta, a cambio de ser pesado: necesita PyTorch y un modelo de
~2 GB que se descarga una sola vez. Sólo tiene sentido con GPU —en CPU, cada
frase tarda varios segundos, mucho más que el silencio incómodo que se quiere
evitar—, así que se activa a propósito, nunca por defecto.

El proyecto original de Coqui cerró como empresa; el paquete que se instala
(``coqui-tts``) es el fork que mantiene la comunidad, no el original
descontinuado.

Soporta clonación de voz a partir de un WAV de referencia corto (``speaker_wav``
en la configuración). Sin uno, usa un hablante preentrenado del propio modelo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..player import TTS_SAMPLE_RATE

if TYPE_CHECKING:
    import numpy as np

    from ...config import Settings

# XTTS-v2 entrega audio a 24 kHz de fábrica: coincide con TTS_SAMPLE_RATE, así
# que no hace falta remuestrear salvo que alguien cambie esa constante.
_TASA_NATIVA_XTTS = 24_000


class XttsTTS:
    """Síntesis local con XTTS-v2. Carga el modelo una vez, no por frase."""

    nombre = "xtts"

    def __init__(self, settings: Settings) -> None:
        # Import a nivel de método, no de módulo: PyTorch y TTS son pesados y
        # opcionales (extra `xtts`). Si faltan, esto lanza y `crear_motor()`
        # cae a edge-tts en vez de reventar el arranque.
        import torch
        from TTS.api import TTS

        self._idioma = settings.tts.xtts_idioma
        self._speaker_wav = settings.tts.xtts_speaker_wav or None

        dispositivo = settings.tts.xtts_dispositivo
        if dispositivo == "auto":
            dispositivo = "cuda" if torch.cuda.is_available() else "cpu"
        self._dispositivo = dispositivo

        self._modelo = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
        self._modelo.to(self._dispositivo)
        # Pascal (compute capability 6.1, como la GTX 10xx) no tiene tensor
        # cores y su fp16 es más lento que su fp32: al contrario que en GPUs
        # modernas, aquí NO conviene `.half()`. Se deja en fp32 explícito en
        # vez de heredar lo que traiga el modelo por defecto.
        if self._dispositivo == "cuda":
            self._modelo.synthesizer.tts_model.float()

    async def sintetizar(self, texto: str) -> np.ndarray:
        import asyncio

        import numpy as np

        texto = texto.strip()
        if not texto:
            return np.zeros(0, dtype=np.int16)

        # La inferencia es síncrona y puede tardar segundos: a un hilo aparte,
        # igual que SapiTTS con su llamada COM, para no congelar el loop ni
        # el frame de audio de 32 ms.
        return await asyncio.to_thread(self._sintetizar_sync, texto)

    def _sintetizar_sync(self, texto: str) -> np.ndarray:
        import numpy as np

        kwargs: dict = {"text": texto, "language": self._idioma}
        if self._speaker_wav:
            kwargs["speaker_wav"] = self._speaker_wav
        else:
            # Sin audio de referencia, XTTS exige un hablante preentrenado
            # por nombre. "Claribel Dervla" es uno de los que trae el modelo.
            kwargs["speaker"] = "Claribel Dervla"

        muestras = self._modelo.tts(**kwargs)
        pcm = (np.asarray(muestras, dtype=np.float32) * 32767).astype(np.int16)

        if self._modelo.synthesizer.output_sample_rate != TTS_SAMPLE_RATE:
            import io
            import wave

            from .base import decodificar_a_pcm

            # Camino de emergencia si algún día el modelo cambia su tasa
            # nativa: reutiliza el mismo remuestreador que ya usa el resto de
            # motores, en vez de improvisar uno nuevo.
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(self._modelo.synthesizer.output_sample_rate)
                wav.writeframes(pcm.tobytes())
            pcm = decodificar_a_pcm(buffer.getvalue(), TTS_SAMPLE_RATE)

        return pcm

    async def cerrar(self) -> None: ...


assert TTS_SAMPLE_RATE == _TASA_NATIVA_XTTS, (
    "XttsTTS asume que XTTS-v2 entrega audio a la misma tasa que "
    "TTS_SAMPLE_RATE; si esto cambia, revisa el camino de remuestreo."
)
