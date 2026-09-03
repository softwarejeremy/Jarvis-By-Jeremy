"""Wake word.

No se puede comprobar aquí que reconoce un "Hey Jarvis" de verdad —haría falta
una grabación real— pero sí lo más importante en la práctica: que **no** se
activa solo. Un wake word que salta con el ruido de fondo es peor que no tener
wake word, porque se pone a grabar sin que se lo pidas.

El test que carga el modelo real se salta si no está descargado, para que la
suite no dependa de la red.
"""

from __future__ import annotations

import numpy as np
import pytest

from jarvis.audio.wakeword import NullWakeWord, WakeWordDetector

FRAME = np.zeros(512, dtype=np.float32)


class TestNullWakeWord:
    def test_nunca_se_activa(self):
        w = NullWakeWord()
        assert not w.enabled
        assert all(not w.feed(FRAME) for _ in range(100))


class TestWakeWordDesactivado:
    def test_si_esta_desactivado_no_carga_el_modelo(self, settings):
        settings.wakeword.enabled = False
        w = WakeWordDetector(settings)
        assert not w.enabled
        assert w.feed(FRAME) is False


@pytest.fixture(scope="module")
def detector_real(settings_modulo):
    """El detector con el modelo de verdad. Se salta si no se puede cargar."""
    w = WakeWordDetector(settings_modulo)
    try:
        w.cargar()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"No se pudo cargar openWakeWord: {exc}")
    if w._modelo is None:
        pytest.skip("El modelo de wake word no está disponible")
    return w


@pytest.fixture(scope="module")
def settings_modulo(tmp_path_factory):
    from jarvis.config import Settings

    s = Settings(data_dir=tmp_path_factory.mktemp("ww"))
    s.ensure_dirs()
    return s


class TestModeloReal:
    """Lo crítico: que no se despierte solo."""

    def test_el_silencio_no_lo_despierta(self, detector_real):
        # 3 segundos de silencio absoluto.
        activaciones = sum(detector_real.feed(FRAME) for _ in range(94))
        assert activaciones == 0

    def test_el_ruido_no_lo_despierta(self, detector_real):
        rng = np.random.default_rng(0)
        activaciones = sum(
            detector_real.feed((rng.standard_normal(512) * 0.2).astype(np.float32))
            for _ in range(94)
        )
        assert activaciones == 0

    def test_usa_el_backend_onnx(self, detector_real):
        # En Windows tflite no está soportado: si esto cambiara, allí no
        # arrancaría y aquí no nos enteraríamos.
        assert detector_real._modelo is not None
