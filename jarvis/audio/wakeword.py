"""Wake word: "Hey Jarvis".

openWakeWord trae ese modelo entrenado de fábrica, lo cual es una suerte poco
común: normalmente hay que grabar cientos de muestras para entrenar una
palabra clave propia.

Corre en local sobre ONNX y consume una fracción de un núcleo, así que puede
estar escuchando permanentemente sin que se note. El audio nunca sale de la
máquina mientras está dormido: sólo cuando te oye, empieza a grabar de verdad.

En Windows hay que forzar el backend ONNX; el de tflite no está soportado ahí.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

    from ..config import Settings


class WakeWordDetector:
    """Escucha permanente a la espera de la palabra clave."""

    def __init__(self, settings: Settings) -> None:
        cfg = settings.wakeword
        self.enabled = cfg.enabled
        self._nombre = cfg.model_name
        self._threshold = cfg.threshold
        self._refractory = cfg.refractory_s
        self._modelo = None
        self._ultima_activacion = 0.0

    def cargar(self) -> None:
        """Descarga (la primera vez) y carga el modelo."""
        if self._modelo is not None or not self.enabled:
            return

        import openwakeword
        from openwakeword.model import Model

        # La primera ejecución necesita bajarse los modelos preentrenados.
        try:
            openwakeword.utils.download_models([self._nombre])
        except Exception:  # noqa: BLE001 - si ya están, o no hay red, seguimos
            pass

        self._modelo = Model(
            wakeword_models=[self._nombre],
            inference_framework="onnx",  # obligatorio en Windows
        )

    def feed(self, frame: np.ndarray) -> bool:
        """Procesa un frame. ``True`` si acaba de oír "Hey Jarvis".

        Tras una activación se queda sordo unos segundos (`refractory_s`) para
        no dispararse dos veces con la misma palabra.
        """
        if not self.enabled:
            return False

        import numpy as np

        self.cargar()
        if self._modelo is None:
            return False

        ahora = time.monotonic()
        if ahora - self._ultima_activacion < self._refractory:
            return False

        # openWakeWord espera PCM int16, no float.
        pcm = (np.asarray(frame, dtype=np.float32) * 32767).astype(np.int16)
        puntuaciones = self._modelo.predict(pcm)

        mejor = max(puntuaciones.values()) if puntuaciones else 0.0
        if mejor >= self._threshold:
            self._ultima_activacion = ahora
            self.reset()
            return True
        return False

    def reset(self) -> None:
        """Limpia el búfer interno tras una activación."""
        if self._modelo is not None:
            try:
                self._modelo.reset()
            except Exception:  # noqa: BLE001 - según versión puede no existir
                pass


class NullWakeWord:
    """No detecta nada. Para cuando el wake word está desactivado o en tests."""

    enabled = False

    def __init__(self, *_a: object, **_k: object) -> None: ...
    def cargar(self) -> None: ...
    def feed(self, frame: np.ndarray) -> bool:  # noqa: ARG002
        return False

    def reset(self) -> None: ...
