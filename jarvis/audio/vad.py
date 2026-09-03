"""Detección de actividad de voz (VAD) con Silero.

Responde a dos preguntas distintas:

- **¿Has terminado de hablar?**  (`Endpointer`)  Es lo que decide cuándo
  cerrar la grabación y mandarla a transcribir. Si se pasa de rápido, te corta
  a media frase; si se pasa de lento, la conversación se siente pesada.
- **¿Has empezado a hablar mientras yo hablaba?**  Es el barge-in.

Se reutiliza el modelo Silero que ya trae faster-whisper: es ONNX puro, pesa
poco más de un mega y no arrastra PyTorch.

Nota sobre el modelo: su `__call__` reinicia el estado recurrente en cada
llamada, así que no sirve para alimentarlo frame a frame. La solución es
evaluarlo sobre una ventana deslizante de medio segundo y quedarnos con la
probabilidad del último frame, que sí tiene contexto suficiente.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from .capture import FRAME_MS, FRAME_SAMPLES

if TYPE_CHECKING:
    import numpy as np

# 16 frames × 32 ms = 512 ms de contexto por evaluación.
VENTANA_FRAMES = 16


class VoiceDetector:
    """Envuelve el modelo Silero y devuelve probabilidad de voz por frame."""

    def __init__(self, ventana_frames: int = VENTANA_FRAMES) -> None:
        self._modelo = None
        self._ventana: deque[np.ndarray] = deque(maxlen=ventana_frames)
        self._ventana_frames = ventana_frames

    def _cargar(self):  # noqa: ANN202
        if self._modelo is None:
            from faster_whisper.vad import get_vad_model

            self._modelo = get_vad_model()
        return self._modelo

    def probabilidad(self, frame: np.ndarray) -> float:
        """Probabilidad de que el frame recién llegado contenga voz (0..1)."""
        import numpy as np

        self._ventana.append(np.asarray(frame, dtype=np.float32))

        # Hasta llenar la ventana, se rellena por delante con silencio.
        faltan = self._ventana_frames - len(self._ventana)
        trozos = [np.zeros(FRAME_SAMPLES, dtype=np.float32)] * faltan + list(self._ventana)
        audio = np.concatenate(trozos)

        salida = self._cargar()(audio).ravel()
        return float(salida[-1])

    def reset(self) -> None:
        self._ventana.clear()


class Endpointer:
    """Decide cuándo has terminado de hablar.

    Es una máquina de dos estados con histéresis: hace falta voz sostenida
    para dar por empezada la frase, y silencio sostenido para darla por
    terminada. Sin esa histéresis, una tos abre la grabación y una pausa para
    respirar la cierra.
    """

    def __init__(
        self,
        detector: VoiceDetector | None = None,
        *,
        threshold: float = 0.5,
        silence_ms: int = 700,
        min_speech_ms: int = 250,
        max_utterance_s: float = 30.0,
    ) -> None:
        self._det = detector or VoiceDetector()
        self._threshold = threshold
        self._frames_silencio_fin = max(1, silence_ms // FRAME_MS)
        self._frames_voz_min = max(1, min_speech_ms // FRAME_MS)
        self._frames_max = int(max_utterance_s * 1000 // FRAME_MS)

        self.hablando = False
        self.terminado = False
        self._frames_voz = 0
        self._frames_silencio = 0
        self._frames_total = 0

    def reset(self) -> None:
        self._det.reset()
        self.hablando = False
        self.terminado = False
        self._frames_voz = 0
        self._frames_silencio = 0
        self._frames_total = 0

    def feed(self, frame: np.ndarray) -> str:
        """Procesa un frame. Devuelve ``"nada"``, ``"inicio"`` o ``"fin"``.

        ``"fin"`` se emite **una sola vez** por frase. Sin ese pestillo, cada
        frame de silencio posterior volvería a anunciar el final y quien lo
        consuma dispararía la transcripción varias veces.
        """
        if self.terminado:
            return "nada"

        p = self._det.probabilidad(frame)
        hay_voz = p >= self._threshold
        self._frames_total += 1

        if not self.hablando:
            if hay_voz:
                self._frames_voz += 1
                if self._frames_voz >= self._frames_voz_min:
                    self.hablando = True
                    self._frames_silencio = 0
                    return "inicio"
            else:
                self._frames_voz = 0
            return "nada"

        # Ya está hablando: buscamos el final.
        if hay_voz:
            self._frames_silencio = 0
        else:
            self._frames_silencio += 1
            if self._frames_silencio >= self._frames_silencio_fin:
                self.terminado = True
                return "fin"

        # Freno de emergencia: nadie habla treinta segundos seguidos sin pausa,
        # y si el VAD se atasca no queremos grabar indefinidamente.
        if self._frames_total >= self._frames_max:
            self.terminado = True
            return "fin"

        return "nada"
