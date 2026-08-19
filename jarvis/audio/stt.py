"""Transcripción con faster-whisper.

Corre en local: tu voz no sale de la máquina. El modelo se carga una sola vez
al arrancar (tarda unos segundos la primera vez, que además incluye la
descarga) y a partir de ahí cada frase se transcribe en unas décimas.

La transcripción es de por sí bloqueante y usa CPU a tope, así que va siempre
en un hilo aparte: si corriera en el loop de asyncio congelaría la captura de
audio justo mientras el usuario sigue hablando.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

    from ..config import Settings


def _resolver_dispositivo(device: str) -> tuple[str, str]:
    """Elige dispositivo y precisión. ``auto`` usa GPU si la hay."""
    if device == "auto":
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda", "float16"
        except Exception:  # noqa: BLE001 - sin CUDA, sin drama
            pass
        return "cpu", "int8"
    return device, "float16" if device == "cuda" else "int8"


class Transcriber:
    """Whisper local, envuelto para uso asíncrono."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings.stt
        self._modelo = None
        self.device = ""
        self.compute_type = ""

    def cargar(self) -> None:
        """Carga el modelo. Conviene llamarlo al arrancar, no en la primera
        frase, para que el primer "Hey Jarvis" no tarde diez segundos."""
        if self._modelo is not None:
            return

        from faster_whisper import WhisperModel

        device, compute = _resolver_dispositivo(self._s.device)
        if self._s.compute_type != "auto":
            compute = self._s.compute_type

        self.device, self.compute_type = device, compute
        self._modelo = WhisperModel(self._s.model_size, device=device, compute_type=compute)

    async def transcribir(self, audio: np.ndarray) -> str:
        """Transcribe audio float32 mono a 16 kHz. Devuelve texto limpio."""
        if audio.size == 0:
            return ""
        return await asyncio.to_thread(self._transcribir_sync, audio)

    def _transcribir_sync(self, audio: np.ndarray) -> str:
        self.cargar()
        assert self._modelo is not None

        segmentos, _info = self._modelo.transcribe(
            audio,
            language=self._s.language,
            initial_prompt=self._s.initial_prompt or None,
            beam_size=1,          # greedy: la mitad de latencia, calidad casi igual
            temperature=0.0,      # sin muestreo: resultados reproducibles
            condition_on_previous_text=False,  # evita que arrastre alucinaciones
            vad_filter=True,      # descarta los silencios que se colaron
        )
        return " ".join(s.text.strip() for s in segmentos).strip()


class FakeTranscriber:
    """Devuelve textos predefinidos. Para tests y modo demostración."""

    def __init__(self, respuestas: list[str] | None = None) -> None:
        self._respuestas = respuestas or ["hola jarvis"]
        self._i = 0
        self.device = "fake"
        self.compute_type = "fake"

    def cargar(self) -> None: ...

    async def transcribir(self, audio: np.ndarray) -> str:
        del audio
        texto = self._respuestas[min(self._i, len(self._respuestas) - 1)]
        self._i += 1
        return texto
