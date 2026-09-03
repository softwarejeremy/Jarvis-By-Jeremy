"""Dobles de prueba.

Todo lo que toca hardware o red tiene aquí un sustituto. Es lo que permite
ejercitar la máquina de estados completa —despertar, escuchar, transcribir,
pensar, hablar— en una máquina sin micrófono ni tarjeta de sonido.
"""

from __future__ import annotations

import asyncio
import threading
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


# ── dobles de la bandeja del sistema ────────────────────────────────────
class IconoFalso:
    """Sustituto de `pystray.Icon`: apunta lo que le hacen y no dibuja nada.

    `run()` bloquea igual que el de verdad —es la bomba de mensajes— para que
    los tests ejerciten el hilo real y detecten los fallos de cierre.
    """

    def __init__(self, nombre, icon=None, title=None, menu=None) -> None:  # noqa: ANN001
        self.nombre = nombre
        self._icon = icon
        self.title = title
        self.menu = menu
        self.visible = False
        self.parado = False
        self.notificaciones: list[tuple[str, str]] = []
        # Para comprobar que el repintado NO ocurre en el hilo del loop.
        self.hilos_de_pintado: list[int] = []
        self._suelto = threading.Event()

    @property
    def icon(self):  # noqa: ANN201
        return self._icon

    @icon.setter
    def icon(self, valor) -> None:  # noqa: ANN001
        self.hilos_de_pintado.append(threading.get_ident())
        self._icon = valor

    def run(self, setup=None) -> None:  # noqa: ANN001
        if setup is not None:
            setup(self)
        self._suelto.wait(timeout=5)

    def stop(self) -> None:
        self.parado = True
        self._suelto.set()

    def notify(self, mensaje: str, titulo: str = "") -> None:
        self.notificaciones.append((mensaje, titulo))


class _MenuItemFalso:
    def __init__(self, etiqueta, accion, checked=None, default=False) -> None:  # noqa: ANN001
        self.etiqueta = etiqueta
        self.accion = accion
        self._checked = checked
        self.default = default

    @property
    def marcado(self) -> bool:
        return bool(self._checked(self)) if self._checked else False

    @property
    def es_conmutador(self) -> bool:
        return self._checked is not None


class _MenuFalso:
    SEPARATOR = "───"

    def __init__(self, *elementos) -> None:  # noqa: ANN002
        self.elementos = list(elementos)

    @property
    def entradas(self) -> list[_MenuItemFalso]:
        return [e for e in self.elementos if isinstance(e, _MenuItemFalso)]

    def por_etiqueta(self, etiqueta: str) -> _MenuItemFalso:
        for e in self.entradas:
            if e.etiqueta == etiqueta:
                return e
        raise KeyError(f"no hay ninguna entrada «{etiqueta}» en el menú")


class PystrayFalso:
    """El módulo pystray, de mentira. Se inyecta en `Bandeja(backend=...)`."""

    Icon = IconoFalso
    MenuItem = _MenuItemFalso
    Menu = _MenuFalso


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
    "IconoFalso",
    "PystrayFalso",
    "ScriptedDetector",
    "ToolCall",
    "TriggerWakeWord",
]
