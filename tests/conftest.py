"""Dobles de prueba.

Todo lo que toca hardware o red tiene aquí un sustituto. Es lo que permite
ejercitar la máquina de estados completa —despertar, escuchar, transcribir,
pensar, hablar— en una máquina sin micrófono ni tarjeta de sonido.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from jarvis.audio.capture import FRAME_SAMPLES
from jarvis.config import Settings
from jarvis.core.agent import Delta, Done, ToolCall


# ── dobles de audio ─────────────────────────────────────────────────────
class ScriptedDetector:
    """Detector de voz con probabilidades escritas a mano.

    Sustituye a Silero para poder guionizar exactamente dónde empieza y
    acaba el habla, sin depender de audio real ni de umbrales.
    """

    def __init__(self, probabilidades: list[float]) -> None:
        self.probabilidades = probabilidades
        self.i = 0

    def probabilidad(self, frame: np.ndarray) -> float:
        del frame
        p = self.probabilidades[min(self.i, len(self.probabilidades) - 1)]
        self.i += 1
        return p

    def reset(self) -> None:
        # A propósito NO reinicia el índice: el guion avanza de forma continua
        # a lo largo del test, como haría el tiempo real.
        pass


class ControlledMic:
    """Micrófono que entrega frames indefinidamente hasta que se le corta.

    Entrega con una pausa mínima real, no con `sleep(0)`: un micrófono de
    verdad produce un frame cada 32 ms, y sin esa pausa el bucle de audio
    consumiría todos los frames antes de que la tarea del turno llegue
    siquiera a ponerse a escuchar. El test pasaría o fallaría según el orden
    del planificador, que es justo lo que no queremos.
    """

    def __init__(self, n_frames: int = 100_000, intervalo: float = 0.001) -> None:
        self.samplerate = 16_000
        self._n = n_frames
        self._intervalo = intervalo
        self.vaciados = 0

    def start(self) -> None: ...
    def stop(self) -> None: ...

    async def frames(self):
        for _ in range(self._n):
            await asyncio.sleep(self._intervalo)
            yield np.zeros(FRAME_SAMPLES, dtype=np.float32)

    def pre_roll(self) -> list[np.ndarray]:
        return []

    def vaciar(self) -> None:
        self.vaciados += 1


class FakeTTS:
    """Devuelve audio silencioso proporcional a la longitud del texto."""

    nombre = "fake"

    def __init__(self) -> None:
        self.dicho: list[str] = []

    async def sintetizar(self, texto: str) -> np.ndarray:
        self.dicho.append(texto)
        return np.zeros(max(1, len(texto) * 100), dtype=np.int16)

    async def cerrar(self) -> None: ...


class FakeAgent:
    """Cerebro guionizado: devuelve los trozos que se le pasen."""

    def __init__(self, guion: list | None = None) -> None:
        self.guion = guion or [Delta("Hola. "), Delta("¿Qué necesita?"), Done(cost_usd=0.01)]
        self.preguntas: list[str] = []
        self.interrumpido = 0

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    async def interrupt(self) -> None:
        self.interrumpido += 1

    async def ask(self, prompt: str):
        self.preguntas.append(prompt)
        for trozo in self.guion:
            await asyncio.sleep(0)
            yield trozo


class TriggerWakeWord:
    """Wake word que se dispara en el frame N y nunca más."""

    enabled = True

    def __init__(self, en_frame: int = 3) -> None:
        self._objetivo = en_frame
        self._i = 0

    def cargar(self) -> None: ...
    def reset(self) -> None: ...

    def feed(self, frame: np.ndarray) -> bool:
        del frame
        self._i += 1
        return self._i == self._objetivo


# ── fixtures ────────────────────────────────────────────────────────────
@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Configuración aislada: nada toca el equipo real ni el home del usuario."""
    s = Settings(
        data_dir=tmp_path / "datos",
        workspace=tmp_path / "trabajo",
    )
    s.workspace.mkdir(parents=True, exist_ok=True)
    s.vad.silence_ms = 96      # 3 frames, para que los tests vuelen
    s.vad.min_speech_ms = 64   # 2 frames
    s.permissions.confirm_timeout_s = 0.5
    s.ensure_dirs()
    return s


@pytest.fixture
def fake_tts() -> FakeTTS:
    return FakeTTS()


__all__ = [
    "ControlledMic",
    "Delta",
    "Done",
    "FakeAgent",
    "FakeTTS",
    "ScriptedDetector",
    "ToolCall",
    "TriggerWakeWord",
]
