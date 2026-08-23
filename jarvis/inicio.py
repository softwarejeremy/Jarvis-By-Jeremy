"""Arrancar J.A.R.V.I.S. con Windows.

Se instala un pequeño script en la carpeta de Inicio del usuario. Se eligió esa
vía, y no el registro ni el Programador de tareas, por tres razones: **no
necesita permisos de administrador**, es del usuario y no de toda la máquina, y
se puede deshacer borrando un archivo — que además el propio usuario puede ver
y borrar a mano sin herramientas.

El intermediario es un `.vbs` porque un `.bat` deja una ventana de consola
parpadeando en cada arranque. `WScript.Shell.Run` con modo de ventana 0 lo
lanza de verdad en silencio.

También se usa `pythonw.exe` en lugar de `python.exe`: el primero no abre
consola. Sin ninguna de las dos cosas, encender el ordenador significaría ver
una ventana negra abrirse sola todos los días.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

NOMBRE_ARCHIVO = "jarvis.vbs"

# Lo que se ejecuta al iniciar sesión. Se arranca con el HUD para que haya
# alguna forma de ver qué hace, pero SIN abrir el navegador: la cara de cada
# mañana es el icono de la bandeja y su globo, no una pestaña que hay que
# cerrar. La URL sigue disponible en el globo y en el propio icono.
ARGUMENTOS_POR_DEFECTO = ["-m", "jarvis", "--web", "--sin-navegador"]

# Banderas que sólo tienen sentido en la línea de órdenes de instalación, no
# en lo que se guarda para el arranque automático.
_BANDERAS_DE_INSTALACION = frozenset(
    {"--arrancar-con-windows", "--quitar-del-inicio", "--sin-bandeja"}
)


def carpeta_inicio() -> Path | None:
    """La carpeta de Inicio del usuario en Windows, o None fuera de Windows."""
    if platform.system() != "Windows":
        return None

    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _interprete_sin_consola() -> Path:
    """`pythonw.exe` si existe; si no, el intérprete actual.

    `pythonw` es el que no abre ventana de consola. En un entorno virtual está
    junto a `python.exe`, así que se busca ahí primero para respetar el venv:
    caer en el Python global significaría no encontrar las dependencias.
    """
    actual = Path(sys.executable)
    sin_consola = actual.with_name("pythonw.exe")
    return sin_consola if sin_consola.is_file() else actual


def guion_vbs(interprete: Path, proyecto: Path, argumentos: list[str]) -> str:
    """El VBScript que lanza J.A.R.V.I.S. sin ventana."""
    partes = " ".join(f'""{a}""' if " " in a else a for a in argumentos)
    return (
        "' Generado por J.A.R.V.I.S. Bórralo para desactivar el arranque\n"
        "' automático, o ejecuta: python -m jarvis --quitar-del-inicio\n"
        'Set sh = CreateObject("WScript.Shell")\n'
        f'sh.CurrentDirectory = "{proyecto}"\n'
        # El 0 final es el modo de ventana: oculta. El False significa que no
        # espera a que termine, o el inicio de sesión se quedaría colgado.
        f'sh.Run """{interprete}"" {partes}", 0, False\n'
    )


def orden_para_el_inicio(extras: list[str]) -> list[str]:
    """La orden que se guardará en el `.vbs`, a partir de cómo se arrancó hoy.

    Se conservan las banderas del usuario —quien instala con `--web --https`
    quiere eso cada mañana— pero se fuerza `--sin-navegador`: abrir una pestaña
    sola en cada inicio de sesión es intrusivo, y el icono de la bandeja ya
    avisa de que está en marcha.
    """
    filtradas = [a for a in extras if a not in _BANDERAS_DE_INSTALACION]

    if "--web" in filtradas and "--sin-navegador" not in filtradas:
        filtradas.append("--sin-navegador")

    if not filtradas:
        return ARGUMENTOS_POR_DEFECTO
    return ["-m", "jarvis", *filtradas]


def instalar(argumentos: list[str] | None = None, destino: Path | None = None) -> str:
    """Deja J.A.R.V.I.S. listo para arrancar al iniciar sesión."""
    carpeta = destino or carpeta_inicio()
    if carpeta is None:
        return (
            "El arranque automático sólo está implementado para Windows. "
            "En Linux puedes crear un servicio de usuario de systemd."
        )

    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        archivo = carpeta / NOMBRE_ARCHIVO
        proyecto = Path(__file__).resolve().parent.parent
        archivo.write_text(
            guion_vbs(_interprete_sin_consola(), proyecto, argumentos or ARGUMENTOS_POR_DEFECTO),
            encoding="utf-8",
        )
    except OSError as exc:
        return f"No he podido instalarlo: {exc}"

    return (
        f"Listo: arrancaré solo al iniciar sesión.\n"
        f"El archivo está en {archivo}\n"
        f"Para desactivarlo: python -m jarvis --quitar-del-inicio"
    )


def desinstalar(destino: Path | None = None) -> str:
    """Quita el arranque automático."""
    carpeta = destino or carpeta_inicio()
    if carpeta is None:
        return "No hay nada que quitar: esto sólo aplica a Windows."

    archivo = carpeta / NOMBRE_ARCHIVO
    if not archivo.exists():
        return "No estaba configurado para arrancar solo."

    try:
        archivo.unlink()
    except OSError as exc:
        return f"No he podido quitarlo: {exc}"

    return "Quitado. Ya no arrancaré al iniciar sesión."


def esta_instalado(destino: Path | None = None) -> bool:
    carpeta = destino or carpeta_inicio()
    return bool(carpeta and (carpeta / NOMBRE_ARCHIVO).is_file())
