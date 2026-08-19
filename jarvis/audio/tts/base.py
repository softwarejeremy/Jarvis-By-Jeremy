"""Interfaz común de los motores de voz.

Todos los motores devuelven lo mismo —PCM 16 bits, mono, 24 kHz— para que el
reproductor no tenga que saber cuál está en uso y se pueda cambiar de motor
con una línea de configuración.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Protocol

from ..player import TTS_SAMPLE_RATE

if TYPE_CHECKING:
    import numpy as np


class TTSEngine(Protocol):
    """Convierte texto en audio listo para reproducir."""

    nombre: str

    async def sintetizar(self, texto: str) -> np.ndarray:
        """Devuelve PCM int16 mono a :data:`TTS_SAMPLE_RATE`."""
        ...

    async def cerrar(self) -> None: ...


def decodificar_a_pcm(datos: bytes, sample_rate: int = TTS_SAMPLE_RATE) -> np.ndarray:
    """Decodifica audio comprimido (MP3, etc.) a PCM int16 mono.

    Usa PyAV, que ya viene con faster-whisper: así no hace falta instalar
    ffmpeg aparte en Windows, que es una fuente clásica de dolores de cabeza.
    """
    import av
    import numpy as np

    if not datos:
        return np.zeros(0, dtype=np.int16)

    trozos: list[np.ndarray] = []
    with av.open(io.BytesIO(datos)) as contenedor:
        remuestreador = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)
        for frame in contenedor.decode(audio=0):
            for salida in remuestreador.resample(frame):
                trozos.append(salida.to_ndarray().reshape(-1))
        # Vaciar lo que quede en el remuestreador.
        for salida in remuestreador.resample(None):
            trozos.append(salida.to_ndarray().reshape(-1))

    if not trozos:
        return np.zeros(0, dtype=np.int16)
    return np.concatenate(trozos).astype(np.int16)


def crear_motor(settings) -> TTSEngine:  # noqa: ANN001 - evita un import circular
    """Instancia el motor de voz que diga la configuración.

    Si el motor pedido no está disponible (falta una dependencia o una API
    key), se cae con elegancia al siguiente en vez de reventar: quedarse mudo
    es peor que sonar peor.
    """
    from .edge import EdgeTTS

    engine = settings.tts.engine

    if engine == "elevenlabs":
        if settings.elevenlabs_api_key:
            from .elevenlabs import ElevenLabsTTS

            return ElevenLabsTTS(settings)
        # Sin key no hay ElevenLabs; seguimos con edge, que es gratis.

    if engine == "sapi":
        try:
            from .sapi import SapiTTS

            return SapiTTS(settings)
        except Exception:  # noqa: BLE001 - pywin32 no está o no es Windows
            pass

    return EdgeTTS(settings)
