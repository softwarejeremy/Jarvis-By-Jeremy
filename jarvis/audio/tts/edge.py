"""Voz por defecto: edge-tts.

Es el motor neuronal que usa Microsoft Edge para leer páginas en voz alta.
Gratis, sin cuenta y sin API key, con voces en español muy por encima de las
de SAPI. Lo único que pide es conexión a internet, que de todos modos hace
falta para hablar con Claude.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import decodificar_a_pcm

if TYPE_CHECKING:
    import numpy as np

    from ...config import Settings


class EdgeTTS:
    """Síntesis con las voces neuronales de Microsoft Edge."""

    nombre = "edge"

    def __init__(self, settings: Settings) -> None:
        self._voz = settings.tts.voice
        self._rate = settings.tts.rate
        self._pitch = settings.tts.pitch

    async def sintetizar(self, texto: str) -> np.ndarray:
        import numpy as np

        texto = texto.strip()
        if not texto:
            return np.zeros(0, dtype=np.int16)

        import edge_tts

        comunicacion = edge_tts.Communicate(
            texto, self._voz, rate=self._rate, pitch=self._pitch
        )

        mp3 = bytearray()
        async for trozo in comunicacion.stream():
            if trozo["type"] == "audio":
                mp3.extend(trozo["data"])

        return decodificar_a_pcm(bytes(mp3))

    async def cerrar(self) -> None: ...
