"""Transcripción con faster-whisper.

Corre en local: tu voz no sale de la máquina. El modelo se carga una sola vez
al arrancar (tarda unos segundos la primera vez, que además incluye la
descarga) y a partir de ahí cada frase se transcribe en unas décimas.

La transcripción es de por sí bloqueante y usa CPU a tope, así que va siempre
en un hilo aparte: si corriera en el loop de asyncio congelaría la captura de
audio justo mientras el usuario sigue hablando.

## Sobre la elección de dispositivo

Detectar que hay una GPU NVIDIA **no** significa que se pueda usar: faster-whisper
necesita cuDNN 9 y cuBLAS instalados aparte, y no toda GPU hace `float16` de
forma eficiente. Preguntar por la presencia de la tarjeta y dar por hecha la
capacidad es exactamente lo que rompía la carga en Windows.

Por eso aquí se verifica dos veces: primero se le pregunta a ctranslate2 qué
precisiones soporta de verdad ese dispositivo, y después, si aun así la carga
falla, se replega a CPU. Un asistente que transcribe despacio sirve; uno que no
transcribe, no.

## El caso de "cublas64_12.dll is not found"

Con una GPU NVIDIA real, cuDNN 9 y cuBLAS instalados vía pip
(`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) en vez del CUDA Toolkit completo,
la carga del modelo puede tener éxito y aun así reventar en la primera
transcripción real: `ctranslate2` en Windows sólo añade **su propia** carpeta
al buscador de DLLs (`os.add_dll_directory`), no las de esos paquetes de
pip, así que Windows no encuentra `cublas64_12.dll` aunque esté instalado.
`_registrar_dll_cuda_en_windows()` se lo dice a mano, antes de intentar cargar
nada — sin eso, la única alternativa real era instalar el CUDA Toolkit
completo de NVIDIA (varios GB) sólo para tener las DLLs en el PATH del
sistema.

Ojo con la carpeta: el wheel de Linux mete las `.so` en `nvidia/cublas/lib/`,
pero el de **Windows** las deja en `nvidia/cublas/bin/` (verificado con
`Get-ChildItem` en una instalación real: sólo hay `bin/` e `include/`, nunca
`lib/`). Buscar `nvidia.cublas.lib` como si fuera Linux falla siempre en
Windows —silenciosamente, dentro del `contextlib.suppress`— y por eso la
carpeta nunca llegaba a registrarse aunque los paquetes sí estuvieran puestos.

## La tercera pieza: `cudart64_12.dll`

Con `bin/` ya registrado, `cublas64_12.dll` y `cudnn64_9.dll` cargan solos
(comprobado a mano con `ctypes.WinDLL`) y aun así la transcripción real
seguía fallando con el mismo mensaje. `cublas` depende en tiempo de
ejecución del runtime de CUDA (`cudart64_12.dll`), que **no** viene con
`nvidia-cublas-cu12` ni con `nvidia-cudnn-cu12` — es un tercer paquete,
`nvidia-cuda-runtime-cu12`, que nunca se había pedido instalar. Sin él,
`ctranslate2` reporta el fallo apuntando a `cublas64_12.dll` aunque ese
archivo cargue perfectamente por su cuenta: el mensaje nombra el símbolo
que no pudo resolver, no necesariamente el archivo que falta de verdad.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

    from ..config import Settings


def _registrar_dll_cuda_en_windows() -> None:
    """Añade al buscador de DLLs las carpetas `bin/` de cuBLAS/cuDNN/CUDA
    instaladas vía pip, si las hay. No hace nada fuera de Windows ni si no
    están instaladas.

    `nvidia.cublas`/`nvidia.cudnn`/`nvidia.cuda_runtime` son paquetes de
    espacio de nombres: no tienen `__file__`, sólo `__path__` (la carpeta
    real en disco). Ahí dentro va `bin/` en Windows, nunca `lib/` (eso es
    Linux) — ver el docstring del módulo. `cuda_runtime` da `cudart64_12.dll`,
    del que `cublas` depende para inicializarse; sin él, cuBLAS carga bien
    aislado pero la transcripción real revienta igual.
    """
    if sys.platform != "win32":
        return
    anadir_directorio_dll = getattr(os, "add_dll_directory", None)
    if anadir_directorio_dll is None:
        return
    for paquete in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime"):
        with contextlib.suppress(Exception):
            modulo = importlib.import_module(paquete)
            carpeta_bin = Path(next(iter(modulo.__path__))) / "bin"
            if carpeta_bin.is_dir():
                anadir_directorio_dll(str(carpeta_bin))

# Precisiones aceptables por dispositivo, de mejor a peor. En GPU manda la
# velocidad; en CPU, int8 es el punto dulce entre rapidez y calidad.
_PREFERENCIAS: dict[str, tuple[str, ...]] = {
    "cuda": ("float16", "int8_float16", "float32"),
    "cpu": ("int8", "int8_float32", "float32"),
}

_REPLIEGUE = ("cpu", "int8")


def _precisiones_soportadas(device: str) -> set[str]:
    """Qué precisiones admite realmente este dispositivo.

    Devuelve un conjunto vacío si el dispositivo no es utilizable — es lo que
    pasa con una GPU presente pero sin drivers o sin cuDNN, donde consultar
    lanza excepción en vez de devolver una lista vacía.
    """
    try:
        import ctranslate2

        return set(ctranslate2.get_supported_compute_types(device))
    except Exception:  # noqa: BLE001 - dispositivo inservible; ya nos vale saberlo
        return set()


def _mejor_precision(device: str) -> str | None:
    """La mejor precisión soportada, o None si el dispositivo no sirve."""
    soportadas = _precisiones_soportadas(device)
    if not soportadas:
        return None
    for precision in _PREFERENCIAS.get(device, ()):
        if precision in soportadas:
            return precision
    # Soporta algo que no habíamos previsto: mejor eso que nada.
    return sorted(soportadas)[0]


def hay_gpu() -> bool:
    """¿Hay al menos una GPU NVIDIA visible? No implica que sea usable."""
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 - sin CUDA, sin drama
        return False


def resolver_dispositivo(device_cfg: str, compute_cfg: str) -> tuple[str, str]:
    """Decide dispositivo y precisión a partir de la configuración.

    Una elección explícita del usuario se respeta tal cual: si pide algo que no
    funciona, que lo diga la carga, no nosotros por adelantado.
    """
    if device_cfg == "auto":
        device = "cuda" if (hay_gpu() and _mejor_precision("cuda")) else "cpu"
    else:
        device = device_cfg

    if compute_cfg != "auto":
        return device, compute_cfg

    return device, _mejor_precision(device) or _REPLIEGUE[1]


class Transcriber:
    """Whisper local, envuelto para uso asíncrono."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings.stt
        self._modelo = None
        self.device = ""
        self.compute_type = ""
        # Se rellena si hubo que replegarse; el diagnóstico lo muestra para
        # explicar por qué va en CPU en lugar de soltar el error de la librería.
        self.motivo_repliegue: str | None = None
        self.gpu_detectada = False

    def cargar(self) -> None:
        """Carga el modelo, replegándose a CPU si hace falta.

        Conviene llamarlo al arrancar y no en la primera frase, para que el
        primer "Hey Jarvis" no tarde diez segundos.
        """
        if self._modelo is not None:
            return

        _registrar_dll_cuda_en_windows()
        from faster_whisper import WhisperModel

        self.gpu_detectada = hay_gpu()
        objetivo = resolver_dispositivo(self._s.device, self._s.compute_type)

        intentos = [objetivo]
        if objetivo != _REPLIEGUE:
            intentos.append(_REPLIEGUE)

        for device, compute in intentos:
            try:
                self._modelo = WhisperModel(
                    self._s.model_size, device=device, compute_type=compute
                )
            except Exception as exc:  # noqa: BLE001 - probamos el siguiente intento
                if (device, compute) == intentos[-1]:
                    raise
                self.motivo_repliegue = (
                    f"{device}/{compute} no funcionó ({_resumir(exc)}); "
                    f"se usa {_REPLIEGUE[0]}/{_REPLIEGUE[1]}."
                )
                continue

            self.device, self.compute_type = device, compute
            return

    async def transcribir(self, audio: np.ndarray) -> str:
        """Transcribe audio float32 mono a 16 kHz. Devuelve texto limpio."""
        if audio.size == 0:
            return ""
        return await asyncio.to_thread(self._transcribir_sync, audio)

    def _transcribir_sync(self, audio: np.ndarray) -> str:
        self.cargar()
        assert self._modelo is not None

        try:
            return self._transcribir_con_modelo_cargado(audio)
        except Exception as exc:  # noqa: BLE001 - probamos el repliegue a CPU
            if (self.device, self.compute_type) == _REPLIEGUE:
                raise  # ya estábamos en el repliegue; no hay adónde más ir

            # Un cuBLAS o cuDNN a medio instalar puede dejar construir el
            # modelo sin protestar y sólo reventar en la primera transcripción
            # real ("Library cublas64_12.dll is not found..."): el repliegue
            # de `cargar()` no lo detecta porque ahí la carga sí tuvo éxito.
            self.motivo_repliegue = (
                f"{self.device}/{self.compute_type} falló transcribiendo "
                f"({_resumir(exc)}); se usa {_REPLIEGUE[0]}/{_REPLIEGUE[1]}."
            )
            from faster_whisper import WhisperModel

            self.device, self.compute_type = _REPLIEGUE
            self._modelo = WhisperModel(
                self._s.model_size, device=_REPLIEGUE[0], compute_type=_REPLIEGUE[1]
            )
            return self._transcribir_con_modelo_cargado(audio)

    def _transcribir_con_modelo_cargado(self, audio: np.ndarray) -> str:
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


def _resumir(exc: Exception, limite: int = 120) -> str:
    texto = " ".join(str(exc).split())
    return texto if len(texto) <= limite else texto[:limite] + "…"


class FakeTranscriber:
    """Devuelve textos predefinidos. Para tests y modo demostración."""

    def __init__(self, respuestas: list[str] | None = None) -> None:
        self._respuestas = respuestas or ["hola jarvis"]
        self._i = 0
        self.device = "fake"
        self.compute_type = "fake"
        self.motivo_repliegue = None
        self.gpu_detectada = False

    def cargar(self) -> None: ...

    async def transcribir(self, audio: np.ndarray) -> str:
        del audio
        texto = self._respuestas[min(self._i, len(self._respuestas) - 1)]
        self._i += 1
        return texto
