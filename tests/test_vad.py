"""El VAD decide cuándo has terminado de hablar.

Si se equivoca por rápido, te corta a media frase. Si se equivoca por lento,
la conversación se siente pesada. Estos tests fijan ese comportamiento con
probabilidades guionizadas, sin depender de audio real.

Hay además un test que sí carga el modelo Silero de verdad, para comprobar
que la integración con faster-whisper sigue viva.
"""

from __future__ import annotations

import numpy as np
import pytest

from jarvis.audio.capture import FRAME_SAMPLES
from jarvis.audio.vad import Endpointer, VoiceDetector

from .conftest import ScriptedDetector

FRAME = np.zeros(FRAME_SAMPLES, dtype=np.float32)


def correr(probabilidades, **kwargs) -> list[str]:
    """Pasa un guion de probabilidades por el endpointer y devuelve eventos."""
    ep = Endpointer(ScriptedDetector(probabilidades), **kwargs)
    return [ep.feed(FRAME) for _ in probabilidades]


class TestEndpointer:
    def test_detecta_inicio_y_fin(self):
        # 96 ms de silencio = 3 frames; 64 ms de voz = 2 frames.
        eventos = correr(
            [0.9] * 5 + [0.0] * 5, silence_ms=96, min_speech_ms=64
        )
        assert "inicio" in eventos
        assert "fin" in eventos
        assert eventos.index("inicio") < eventos.index("fin")

    def test_un_ruido_suelto_no_abre_la_grabacion(self):
        """Una tos o un golpe no deben hacer que empiece a escuchar."""
        eventos = correr([0.0, 0.9, 0.0, 0.0, 0.0], silence_ms=96, min_speech_ms=64)
        assert "inicio" not in eventos

    def test_una_pausa_para_respirar_no_corta_la_frase(self):
        eventos = correr(
            [0.9] * 4 + [0.0] * 2 + [0.9] * 4 + [0.0] * 5,
            silence_ms=96,   # 3 frames: la pausa de 2 no llega
            min_speech_ms=64,
        )
        assert eventos.count("fin") == 1, "sólo debe cerrar una vez, al final"
        assert eventos.index("fin") > 9, "no debe cortar en la pausa intermedia"

    def test_el_silencio_debe_ser_sostenido(self):
        eventos = correr([0.9] * 4 + [0.0] * 2, silence_ms=160, min_speech_ms=64)
        assert "fin" not in eventos, "2 frames de silencio no bastan para 160 ms"

    def test_freno_de_emergencia_por_duracion(self):
        """Si el VAD se atasca en 'hay voz', hay que cortar igualmente."""
        eventos = correr(
            [0.9] * 40, silence_ms=96, min_speech_ms=64, max_utterance_s=0.5
        )
        assert "fin" in eventos

    def test_reset_deja_el_endpointer_como_nuevo(self):
        ep = Endpointer(ScriptedDetector([0.9] * 10), silence_ms=96, min_speech_ms=64)
        for _ in range(4):
            ep.feed(FRAME)
        assert ep.hablando

        ep.reset()
        assert not ep.hablando

    def test_el_umbral_se_respeta(self):
        # 0.4 está por debajo de 0.5: no cuenta como voz.
        eventos = correr([0.4] * 10, threshold=0.5, min_speech_ms=64, silence_ms=96)
        assert "inicio" not in eventos

        eventos = correr([0.4] * 10, threshold=0.3, min_speech_ms=64, silence_ms=96)
        assert "inicio" in eventos


@pytest.fixture(scope="module")
def detector() -> VoiceDetector:
    """El modelo real de Silero. Se carga una sola vez para todo el módulo."""
    return VoiceDetector()


class TestSileroDeVerdad:
    """Comprueba que el modelo real sigue enchufado y con la forma esperada."""

    def test_el_silencio_da_probabilidad_baja(self, detector):
        p = detector.probabilidad(np.zeros(FRAME_SAMPLES, dtype=np.float32))
        assert 0.0 <= p < 0.3, f"el silencio no debería parecer voz (dio {p})"

    def test_el_ruido_blanco_no_es_voz(self, detector):
        detector.reset()
        rng = np.random.default_rng(0)
        p = 0.0
        for _ in range(16):
            ruido = (rng.standard_normal(FRAME_SAMPLES) * 0.3).astype(np.float32)
            p = detector.probabilidad(ruido)
        assert p < 0.5, f"el ruido blanco no es voz (dio {p})"

    def test_devuelve_una_probabilidad_valida(self, detector):
        detector.reset()
        rng = np.random.default_rng(1)
        for _ in range(20):
            frame = (rng.standard_normal(FRAME_SAMPLES) * 0.1).astype(np.float32)
            p = detector.probabilidad(frame)
            assert 0.0 <= p <= 1.0
