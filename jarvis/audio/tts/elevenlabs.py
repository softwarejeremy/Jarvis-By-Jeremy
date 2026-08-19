"""Voz premium: ElevenLabs.

Es lo más cerca que se puede estar del J.A.R.V.I.S. de la película. Cuesta
dinero (hay plan gratuito con unos 10.000 caracteres al mes) y necesita una
API key, así que es opcional: se activa poniendo ``ELEVENLABS_API_KEY`` en
el `.env` y ``engine = "elevenlabs"`` en la configuración.

Se pide el audio ya en PCM a 24 kHz, así que no hay que decodificar nada.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..player import TTS_SAMPLE_RATE

if TYPE_CHECKING:
    import numpy as np

    from ...config import Settings

_API = "https://api.elevenlabs.io/v1/text-to-speech"

# Voz por defecto: "George", grave y británica, la más parecida al original.
_VOZ_POR_DEFECTO = "JBFqnCBsd6RMkjVDRZzb"


class ElevenLabsTTS:
    nombre = "elevenlabs"

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.elevenlabs_api_key
        self._voice_id = settings.tts.elevenlabs_voice_id or _VOZ_POR_DEFECTO
        self._model = settings.tts.elevenlabs_model
        self._cliente = None

    def _get_cliente(self):  # noqa: ANN202
        if self._cliente is None:
            import httpx

            self._cliente = httpx.AsyncClient(timeout=30.0)
        return self._cliente

    async def sintetizar(self, texto: str) -> np.ndarray:
        import numpy as np

        texto = texto.strip()
        if not texto:
            return np.zeros(0, dtype=np.int16)

        cliente = self._get_cliente()
        respuesta = await cliente.post(
            f"{_API}/{self._voice_id}",
            params={"output_format": f"pcm_{TTS_SAMPLE_RATE}"},
            headers={"xi-api-key": self._api_key, "Content-Type": "application/json"},
            json={
                "text": texto,
                "model_id": self._model,
                "voice_settings": {
                    "stability": 0.45,
                    "similarity_boost": 0.75,
                    "style": 0.15,
                },
            },
        )
        respuesta.raise_for_status()
        return np.frombuffer(respuesta.content, dtype=np.int16)

    async def cerrar(self) -> None:
        if self._cliente is not None:
            await self._cliente.aclose()
            self._cliente = None
