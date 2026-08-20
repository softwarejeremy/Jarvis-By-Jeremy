"""Elección de dispositivo para la transcripción.

Estos tests nacen de un fallo real: en el Windows de Jeremy había una GPU
NVIDIA presente, el código dedujo que podía usar `float16`, y la carga del
modelo reventó con *"Requested float16 compute type, but the target device or
backend do not support efficient float16 computation"*. Sin transcriptor, todo
el modo voz queda inútil.

La lección es que la presencia de un dispositivo no dice nada de sus
capacidades. Aquí se simula lo que responde ctranslate2 para poder ejercitar
todas las combinaciones sin necesitar una GPU.
"""

from __future__ import annotations

import pytest

from jarvis.audio.stt import Transcriber, hay_gpu, resolver_dispositivo

# Lo que un CPU normal soporta de verdad. Nótese la ausencia de float16: pedirlo
# es justo lo que provocaba el error.
CPU_REAL = {"int8", "int16", "int8_float32", "float32"}
GPU_REAL = {"float16", "int8_float16", "float32", "int8"}


def simular_ct2(monkeypatch, *, gpus=0, soporte=None, cuenta_falla=False):
    """Sustituye a ctranslate2 para controlar qué dice soportar la máquina."""
    import ctranslate2

    soporte = soporte if soporte is not None else {"cpu": CPU_REAL}

    def get_supported_compute_types(device):
        if device not in soporte:
            # Es como se comporta de verdad con una GPU inservible: lanza,
            # no devuelve un conjunto vacío.
            raise RuntimeError(f"CUDA failed: {device} no disponible")
        return soporte[device]

    def get_cuda_device_count():
        if cuenta_falla:
            raise RuntimeError("driver de CUDA insuficiente")
        return gpus

    monkeypatch.setattr(ctranslate2, "get_supported_compute_types", get_supported_compute_types)
    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", get_cuda_device_count)


def simular_whisper(monkeypatch, fallan=()):
    """Sustituye a WhisperModel. Devuelve la lista de intentos realizados."""
    import faster_whisper

    intentos: list[tuple[str, str]] = []

    class FakeWhisper:
        def __init__(self, size, device=None, compute_type=None, **_kw):
            del size
            intentos.append((device, compute_type))
            if (device, compute_type) in fallan:
                raise ValueError(
                    f"Requested {compute_type} compute type, but the target device "
                    "or backend do not support efficient computation."
                )

    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeWhisper)
    return intentos


class TestResolverDispositivo:
    def test_sin_gpu_usa_cpu_con_int8(self, monkeypatch):
        simular_ct2(monkeypatch, gpus=0)
        assert resolver_dispositivo("auto", "auto") == ("cpu", "int8")

    def test_nunca_pide_float16_en_cpu(self, monkeypatch):
        """El error original, en su forma más directa."""
        simular_ct2(monkeypatch, gpus=0)
        _device, compute = resolver_dispositivo("auto", "auto")
        assert compute != "float16"
        assert compute in CPU_REAL

    def test_gpu_presente_pero_inservible_cae_a_cpu(self, monkeypatch):
        """El caso exacto del Windows de Jeremy.

        Hay tarjeta (`get_cuda_device_count` devuelve 1) pero consultar sus
        precisiones falla porque falta cuDNN. Antes esto daba ("cuda","float16").
        """
        simular_ct2(monkeypatch, gpus=1, soporte={"cpu": CPU_REAL})
        assert resolver_dispositivo("auto", "auto") == ("cpu", "int8")

    def test_gpu_utilizable_usa_float16(self, monkeypatch):
        simular_ct2(monkeypatch, gpus=1, soporte={"cpu": CPU_REAL, "cuda": GPU_REAL})
        assert resolver_dispositivo("auto", "auto") == ("cuda", "float16")

    def test_gpu_sin_float16_usa_la_siguiente_mejor(self, monkeypatch):
        simular_ct2(
            monkeypatch, gpus=1,
            soporte={"cpu": CPU_REAL, "cuda": {"int8_float16", "float32"}},
        )
        assert resolver_dispositivo("auto", "auto") == ("cuda", "int8_float16")

    def test_respeta_el_dispositivo_elegido_a_mano(self, monkeypatch):
        simular_ct2(monkeypatch, gpus=1, soporte={"cpu": CPU_REAL, "cuda": GPU_REAL})
        device, _ = resolver_dispositivo("cpu", "auto")
        assert device == "cpu", "si el usuario pide CPU, se le da CPU"

    def test_respeta_la_precision_elegida_a_mano(self, monkeypatch):
        simular_ct2(monkeypatch, gpus=0)
        assert resolver_dispositivo("auto", "float32") == ("cpu", "float32")

    def test_si_falla_la_deteccion_de_gpu_no_propaga(self, monkeypatch):
        simular_ct2(monkeypatch, cuenta_falla=True)
        assert not hay_gpu()
        assert resolver_dispositivo("auto", "auto") == ("cpu", "int8")

    def test_precision_desconocida_pero_soportada_se_acepta(self, monkeypatch):
        # Si una versión futura sólo ofrece algo que no habíamos previsto,
        # es preferible usarlo a quedarse sin transcriptor.
        simular_ct2(monkeypatch, gpus=0, soporte={"cpu": {"bfloat16"}})
        assert resolver_dispositivo("auto", "auto") == ("cpu", "bfloat16")


class TestCargaConRepliegue:
    def test_carga_normal_sin_repliegue(self, settings, monkeypatch):
        simular_ct2(monkeypatch, gpus=0)
        intentos = simular_whisper(monkeypatch)

        t = Transcriber(settings)
        t.cargar()

        assert intentos == [("cpu", "int8")]
        assert (t.device, t.compute_type) == ("cpu", "int8")
        assert t.motivo_repliegue is None

    def test_si_la_gpu_falla_al_cargar_se_replega_a_cpu(self, settings, monkeypatch):
        """La segunda red de seguridad: aunque ctranslate2 diga que sí, puede fallar."""
        simular_ct2(monkeypatch, gpus=1, soporte={"cpu": CPU_REAL, "cuda": GPU_REAL})
        intentos = simular_whisper(monkeypatch, fallan=[("cuda", "float16")])

        t = Transcriber(settings)
        t.cargar()

        assert intentos == [("cuda", "float16"), ("cpu", "int8")]
        assert (t.device, t.compute_type) == ("cpu", "int8")
        assert t.motivo_repliegue and "cuda/float16" in t.motivo_repliegue

    def test_el_motivo_del_repliegue_es_legible(self, settings, monkeypatch):
        simular_ct2(monkeypatch, gpus=1, soporte={"cpu": CPU_REAL, "cuda": GPU_REAL})
        simular_whisper(monkeypatch, fallan=[("cuda", "float16")])

        t = Transcriber(settings)
        t.cargar()

        # Tiene que explicar qué pasó y qué se hizo, no volcar un traceback.
        assert "se usa cpu/int8" in t.motivo_repliegue
        assert len(t.motivo_repliegue) < 250

    def test_si_todo_falla_lanza(self, settings, monkeypatch):
        """No hay que esconder un fallo total: sin transcriptor no hay asistente."""
        simular_ct2(monkeypatch, gpus=0)
        simular_whisper(monkeypatch, fallan=[("cpu", "int8")])

        with pytest.raises(ValueError):
            Transcriber(settings).cargar()

    def test_cargar_dos_veces_no_recarga(self, settings, monkeypatch):
        simular_ct2(monkeypatch, gpus=0)
        intentos = simular_whisper(monkeypatch)

        t = Transcriber(settings)
        t.cargar()
        t.cargar()

        assert len(intentos) == 1, "el modelo es caro: se carga una sola vez"

    def test_registra_si_habia_gpu(self, settings, monkeypatch):
        simular_ct2(monkeypatch, gpus=1, soporte={"cpu": CPU_REAL})
        simular_whisper(monkeypatch)

        t = Transcriber(settings)
        t.cargar()

        # El diagnóstico lo usa para sugerir instalar cuDNN.
        assert t.gpu_detectada
        assert t.device == "cpu"
