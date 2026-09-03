"""Voz de respaldo: SAPI, la que trae Windows de fábrica.

Suena claramente peor que edge-tts, pero tiene una virtud que ninguna otra
opción tiene: funciona **sin internet**. Es la red de seguridad para cuando
se cae la conexión, y la única forma de que J.A.R.V.I.S. siga respondiendo
algo en vez de quedarse mudo.

Sólo funciona en Windows y necesita ``pip install jarvis[windows]``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..player import TTS_SAMPLE_RATE

if TYPE_CHECKING:
    import numpy as np

    from ...config import Settings

# Constantes de SAPI (SpeechAudioFormatType). 24 kHz, 16 bits, mono: el mismo
# formato que produce edge-tts, así el reproductor no nota la diferencia.
_SAF_24KHZ_16BIT_MONO = 26


class SapiTTS:
    """Síntesis local con el motor de voz de Windows."""

    nombre = "sapi"

    def __init__(self, settings: Settings) -> None:
        import win32com.client  # noqa: F401 - se comprueba que existe

        self._settings = settings
        self._voz_preferida = settings.tts.voice

    async def sintetizar(self, texto: str) -> np.ndarray:
        import asyncio

        import numpy as np

        texto = texto.strip()
        if not texto:
            return np.zeros(0, dtype=np.int16)

        # SAPI es COM síncrono: va a un hilo aparte para no congelar el loop.
        return await asyncio.to_thread(self._sintetizar_sync, texto)

    def _sintetizar_sync(self, texto: str) -> np.ndarray:
        import numpy as np
        import win32com.client

        voz = win32com.client.Dispatch("SAPI.SpVoice")
        stream = win32com.client.Dispatch("SAPI.SpMemoryStream")
        stream.Format.Type = _SAF_24KHZ_16BIT_MONO

        salida_previa = voz.AudioOutputStream
        voz.AudioOutputStream = stream
        try:
            voz.Speak(texto)
        finally:
            voz.AudioOutputStream = salida_previa

        datos = bytes(bytearray(stream.GetData()))
        if not datos:
            return np.zeros(0, dtype=np.int16)
        # SAPI entrega PCM crudo, ya en el formato que pedimos: sin decodificar.
        return np.frombuffer(datos, dtype=np.int16)

    async def cerrar(self) -> None: ...


assert TTS_SAMPLE_RATE == 24_000, (
    "SapiTTS pide a SAPI audio a 24 kHz; si cambias TTS_SAMPLE_RATE, "
    "actualiza también _SAF_24KHZ_16BIT_MONO."
)
