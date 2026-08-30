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

import sys
import types

import pytest

from jarvis.audio.stt import (
    Transcriber,
    _registrar_dll_cuda_en_windows,
    hay_gpu,
    resolver_dispositivo,
)

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


def simular_whisper(monkeypatch, fallan=(), fallan_transcribir=(), segmentos=()):
    """Sustituye a WhisperModel. Devuelve la lista de intentos realizados.

    `fallan` reproduce un fallo al construir el modelo (lo que ya vivía aquí).
    `fallan_transcribir` reproduce el caso real de Jeremy: la construcción
    tiene éxito y sólo la primera transcripción revienta (cuBLAS a medio
    instalar, por ejemplo) — un fallo que `cargar()` no puede ver.
    """
    import faster_whisper

    intentos: list[tuple[str, str]] = []

    class _Segmento:
        def __init__(self, texto: str) -> None:
            self.text = texto

    class FakeWhisper:
        def __init__(self, size, device=None, compute_type=None, **_kw):
            del size
            self._device = device
            self._compute = compute_type
            intentos.append((device, compute_type))
            if (device, compute_type) in fallan:
                raise ValueError(
                    f"Requested {compute_type} compute type, but the target device "
                    "or backend do not support efficient computation."
                )

        def transcribe(self, *_a, **_kw):
            if (self._device, self._compute) in fallan_transcribir:
                raise RuntimeError(
                    "Library cublas64_12.dll is not found or cannot be loaded"
                )
            return [_Segmento(s) for s in segmentos], None

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


class TestTranscribirConRepliegue:
    """El fallo real de Jeremy: un cuBLAS a medio instalar deja construir el
    modelo en GPU sin protestar, y sólo revienta al transcribir de verdad
    ("Library cublas64_12.dll is not found..."). El repliegue de `cargar()`
    no lo detecta porque, en ese momento, la carga sí tuvo éxito."""

    async def test_si_la_gpu_falla_transcribiendo_se_replega_a_cpu(self, settings, monkeypatch):
        simular_ct2(monkeypatch, gpus=1, soporte={"cpu": CPU_REAL, "cuda": GPU_REAL})
        intentos = simular_whisper(
            monkeypatch, fallan_transcribir=[("cuda", "float16")], segmentos=["hola"]
        )

        t = Transcriber(settings)
        texto = await t.transcribir(_audio())

        assert texto == "hola"
        assert (t.device, t.compute_type) == ("cpu", "int8")
        assert intentos == [("cuda", "float16"), ("cpu", "int8")]
        assert t.motivo_repliegue and "cublas" in t.motivo_repliegue.lower()

    async def test_una_segunda_frase_ya_no_repliega_dos_veces(self, settings, monkeypatch):
        simular_ct2(monkeypatch, gpus=1, soporte={"cpu": CPU_REAL, "cuda": GPU_REAL})
        intentos = simular_whisper(
            monkeypatch, fallan_transcribir=[("cuda", "float16")], segmentos=["hola"]
        )

        t = Transcriber(settings)
        await t.transcribir(_audio())
        await t.transcribir(_audio())

        # Ya replegado a CPU, la segunda frase no debe volver a construir el
        # modelo de GPU: ya se sabe que no funciona.
        assert intentos == [("cuda", "float16"), ("cpu", "int8")]

    async def test_si_tambien_falla_en_cpu_lanza(self, settings, monkeypatch):
        # Sin GPU de por medio: si hasta el repliegue falla transcribiendo,
        # no hay adónde más ir. Esconder el error dejaría a J.A.R.V.I.S. mudo
        # sin que nadie supiera por qué.
        simular_ct2(monkeypatch, gpus=0)
        simular_whisper(monkeypatch, fallan_transcribir=[("cpu", "int8")])

        t = Transcriber(settings)
        with pytest.raises(RuntimeError):
            await t.transcribir(_audio())


def _audio():
    import numpy as np

    return np.zeros(1600, dtype=np.float32)


def _instalar_paquete_namespace_falso(
    monkeypatch: pytest.MonkeyPatch, nombre: str, tmp_path, *, con_bin: bool = True
):
    """Simula `nvidia.cublas`/`nvidia.cudnn`: paquetes de espacio de nombres,
    sin `__file__`, sólo `__path__` apuntando a una carpeta real en disco —
    con `bin/` dentro, como en Windows de verdad, o sin ella si `con_bin`
    es falso (paquete a medio instalar)."""
    raiz = tmp_path / nombre.replace(".", "_")
    (raiz / "bin" if con_bin else raiz).mkdir(parents=True)
    modulo = types.ModuleType(nombre)
    modulo.__path__ = [str(raiz)]
    monkeypatch.setitem(sys.modules, nombre, modulo)
    return raiz


class TestRegistrarDllCudaEnWindows:
    """El fallo real de Jeremy, capa 2: incluso con `nvidia-cublas-cu12` y
    `nvidia-cudnn-cu12` instalados vía pip, `ctranslate2` en Windows sólo
    añade su propia carpeta al buscador de DLL, no las de esos paquetes.

    Capa 3, descubierta en vivo con `Get-ChildItem` en su Windows real: el
    wheel de Windows deja las DLL en `bin/`, no en `lib/` como el de Linux —
    buscar `nvidia.cublas.lib` no encontraba nada NUNCA en Windows, con o
    sin los paquetes puestos.

    Capa 4, descubierta a mano con `ctypes.WinDLL` una vez arreglada la
    capa 3: cuBLAS y cuDNN cargaban perfectamente solos y la transcripción
    seguía fallando, porque cuBLAS depende en tiempo de ejecución de
    `cudart64_12.dll` — un tercer paquete (`nvidia-cuda-runtime-cu12`) que
    nadie había pedido instalar.
    """

    def test_fuera_de_windows_no_hace_nada(self, monkeypatch, tmp_path):
        # Con los paquetes "instalados" de mentira: si el filtro de
        # plataforma no cortara aquí, sí que habría algo que registrar.
        monkeypatch.setattr(sys, "platform", "linux")
        llamadas = []
        monkeypatch.setattr(
            "os.add_dll_directory", lambda ruta: llamadas.append(ruta), raising=False
        )
        _instalar_paquete_namespace_falso(monkeypatch, "nvidia.cublas", tmp_path)
        _instalar_paquete_namespace_falso(monkeypatch, "nvidia.cudnn", tmp_path)
        _instalar_paquete_namespace_falso(monkeypatch, "nvidia.cuda_runtime", tmp_path)

        _registrar_dll_cuda_en_windows()

        assert llamadas == []

    def test_en_windows_anade_las_tres_carpetas_bin(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "win32")
        llamadas = []
        monkeypatch.setattr(
            "os.add_dll_directory", lambda ruta: llamadas.append(ruta), raising=False
        )
        raiz_cublas = _instalar_paquete_namespace_falso(monkeypatch, "nvidia.cublas", tmp_path)
        raiz_cudnn = _instalar_paquete_namespace_falso(monkeypatch, "nvidia.cudnn", tmp_path)
        raiz_cudart = _instalar_paquete_namespace_falso(
            monkeypatch, "nvidia.cuda_runtime", tmp_path
        )

        _registrar_dll_cuda_en_windows()

        assert sorted(llamadas) == sorted(
            [str(raiz_cublas / "bin"), str(raiz_cudnn / "bin"), str(raiz_cudart / "bin")]
        )

    def test_en_windows_sin_los_paquetes_no_revienta(self, monkeypatch):
        # Fuerza la ausencia con sys.modules en vez de confiar en que este
        # sandbox no los tenga instalados de verdad.
        monkeypatch.setattr(sys, "platform", "win32")
        llamadas = []
        monkeypatch.setattr(
            "os.add_dll_directory", lambda ruta: llamadas.append(ruta), raising=False
        )
        monkeypatch.setitem(sys.modules, "nvidia.cublas", None)
        monkeypatch.setitem(sys.modules, "nvidia.cudnn", None)
        monkeypatch.setitem(sys.modules, "nvidia.cuda_runtime", None)

        _registrar_dll_cuda_en_windows()  # no debe lanzar

        assert llamadas == []

    def test_paquete_instalado_sin_carpeta_bin_no_registra_nada(self, monkeypatch, tmp_path):
        # El paquete a medio instalar (o una versión que cambie la
        # estructura otra vez): que no haya `bin/` no debe reventar, sólo
        # no registrar nada para ese paquete.
        monkeypatch.setattr(sys, "platform", "win32")
        llamadas = []
        monkeypatch.setattr(
            "os.add_dll_directory", lambda ruta: llamadas.append(ruta), raising=False
        )
        _instalar_paquete_namespace_falso(
            monkeypatch, "nvidia.cublas", tmp_path, con_bin=False
        )

        _registrar_dll_cuda_en_windows()  # no debe lanzar

        assert llamadas == []

    def test_sin_add_dll_directory_no_revienta(self, monkeypatch, tmp_path):
        # Python en Linux nunca ha tenido `os.add_dll_directory`; se fuerza su
        # ausencia explícitamente para no depender de esa casualidad.
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delattr("os.add_dll_directory", raising=False)
        _instalar_paquete_namespace_falso(monkeypatch, "nvidia.cublas", tmp_path)

        _registrar_dll_cuda_en_windows()  # no debe lanzar
