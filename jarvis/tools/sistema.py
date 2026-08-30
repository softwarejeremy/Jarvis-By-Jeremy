"""Herramientas para manejar el equipo: volumen, abrir programas, la hora.

Son las que más se notan en el día a día — "sube el volumen", "abre Spotify",
"qué hora es"— y las que hacen que J.A.R.V.I.S. se sienta un asistente y no un
chat con micrófono.

## Cómo está organizado y por qué

Cada acción tiene dos partes separadas a propósito: **qué se pide** (común a
todos los sistemas, validado y testeable en cualquier parte) y **cómo se hace**
(dependiente de Windows, Linux o macOS). Sin esa separación, ni una sola línea
de esto podría probarse fuera de un Windows real, que es exactamente el
problema que ya nos costó caro con el audio.

## Sobre los permisos

`hora` y `volumen` se ejecutan sin preguntar: no rompen nada y su efecto es
evidente e inmediatamente reversible —oír el volumen subir es su propia
confirmación—. `abrir` y `bloquear_pantalla` pasan por el "sí" hablado, porque
lanzar programas arbitrarios es una capacidad real y bloquear la pantalla a
destiempo es, como mínimo, molesto.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from datetime import datetime
from typing import Any

import psutil
from claude_agent_sdk import tool

SISTEMA = platform.system()  # "Windows", "Linux", "Darwin"

# Cuánto sube o baja el volumen cada paso. Windows mueve ~2 % por pulsación,
# así que cinco pasos son ~10 %: un cambio que se nota sin pasarse.
PASOS_VOLUMEN = 5


# ═══════════════════════════════════════════════════════════════════════
#  Volumen
# ═══════════════════════════════════════════════════════════════════════

# Constantes de la API de Windows para las teclas multimedia.
_WM_APPCOMMAND = 0x0319
_APPCOMMAND = {
    "subir": 0x0A << 16,
    "bajar": 0x09 << 16,
    "silenciar": 0x08 << 16,
}


def ajustar_volumen(accion: str, pasos: int = PASOS_VOLUMEN) -> str:
    """Sube, baja o silencia el volumen del sistema."""
    if accion not in _APPCOMMAND:
        return f"No sé hacer «{accion}» con el volumen."

    if SISTEMA == "Windows":
        return _volumen_windows(accion, pasos)
    if SISTEMA == "Linux":
        return _volumen_linux(accion, pasos)
    if SISTEMA == "Darwin":
        return _volumen_macos(accion, pasos)
    return f"No sé controlar el volumen en {SISTEMA}."


def _volumen_windows(accion: str, pasos: int) -> str:
    # Se usa ctypes en vez de pycaw para no arrastrar una dependencia sólo por
    # esto: mandar el comando multimedia es lo mismo que hace el teclado.
    import ctypes

    user32 = ctypes.windll.user32
    ventana = user32.GetForegroundWindow()
    repeticiones = 1 if accion == "silenciar" else max(1, pasos)

    for _ in range(repeticiones):
        user32.SendMessageW(ventana, _WM_APPCOMMAND, 0, _APPCOMMAND[accion])

    return _confirmacion(accion)


def _volumen_linux(accion: str, pasos: int) -> str:
    if not shutil.which("amixer"):
        return "No encuentro `amixer` para controlar el volumen."

    argumento = {
        "subir": f"{pasos * 2}%+",
        "bajar": f"{pasos * 2}%-",
        "silenciar": "toggle",
    }[accion]
    subprocess.run(  # noqa: S603 - argumentos fijos, sin entrada del usuario
        ["amixer", "-q", "set", "Master", argumento], check=False, timeout=5
    )
    return _confirmacion(accion)


def _volumen_macos(accion: str, pasos: int) -> str:
    guion = {
        "subir": f"set volume output volume (output volume of (get volume settings) + {pasos * 2})",
        "bajar": f"set volume output volume (output volume of (get volume settings) - {pasos * 2})",
        "silenciar": "set volume output muted not (output muted of (get volume settings))",
    }[accion]
    subprocess.run(  # noqa: S603
        ["osascript", "-e", guion], check=False, timeout=5
    )
    return _confirmacion(accion)


def _confirmacion(accion: str) -> str:
    return {
        "subir": "Volumen arriba.",
        "bajar": "Volumen abajo.",
        "silenciar": "Silencio alternado.",
    }[accion]


# ═══════════════════════════════════════════════════════════════════════
#  Abrir programas y sitios
# ═══════════════════════════════════════════════════════════════════════

# Lo que NO se abre nunca, aunque el usuario diga que sí. Son intérpretes: si
# J.A.R.V.I.S. pudiera abrirlos, el sistema de permisos —que lee en voz alta
# los comandos antes de ejecutarlos— quedaría sin efecto, porque bastaría con
# "abrir" una consola para ejecutar cualquier cosa sin que nadie lo enuncie.
PROHIBIDOS = frozenset({
    "cmd", "cmd.exe", "command.com",
    "powershell", "powershell.exe", "pwsh", "pwsh.exe",
    "wscript", "wscript.exe", "cscript", "cscript.exe",
    "regedit", "regedit.exe", "bash", "sh", "zsh", "python", "python.exe",
})


def abrir(objetivo: str) -> str:
    """Abre un programa, un archivo o una dirección web."""
    objetivo = objetivo.strip()
    if not objetivo:
        return "No me ha dicho qué abrir."

    if objetivo.split("\\")[-1].split("/")[-1].lower() in PROHIBIDOS:
        return (
            f"No abro «{objetivo}»: es un intérprete de comandos, y abrirlo "
            "dejaría sin efecto las confirmaciones. Pídame el comando concreto "
            "y se lo leeré antes de ejecutarlo."
        )

    try:
        if SISTEMA == "Windows":
            import os

            os.startfile(objetivo)  # noqa: S606 - la propia API de Windows
        elif SISTEMA == "Darwin":
            subprocess.Popen(["open", objetivo])  # noqa: S603, S607
        else:
            subprocess.Popen(["xdg-open", objetivo])  # noqa: S603, S607
    except FileNotFoundError:
        return f"No he encontrado «{objetivo}»."
    except OSError as exc:
        return f"No he podido abrir «{objetivo}»: {exc}"

    return f"Abriendo {objetivo}."


# ═══════════════════════════════════════════════════════════════════════
#  Pantalla y reloj
# ═══════════════════════════════════════════════════════════════════════

def bloquear_pantalla() -> str:
    """Bloquea la sesión."""
    try:
        if SISTEMA == "Windows":
            import ctypes

            ctypes.windll.user32.LockWorkStation()
        elif SISTEMA == "Darwin":
            subprocess.run(  # noqa: S603, S607
                ["pmset", "displaysleepnow"], check=False, timeout=5
            )
        elif shutil.which("loginctl"):
            subprocess.run(["loginctl", "lock-session"], check=False, timeout=5)  # noqa: S603, S607
        else:
            return "No sé bloquear la pantalla en este sistema."
    except OSError as exc:
        return f"No he podido bloquear la pantalla: {exc}"

    return "Pantalla bloqueada."


# Días y meses en español, para no depender de la configuración regional del
# equipo: `strftime("%A")` devolvería "Friday" en un Windows en inglés.
_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def decir_hora(ahora: datetime | None = None) -> str:
    """La fecha y la hora, redactadas para leerse en voz alta."""
    momento = ahora or datetime.now()
    return (
        f"Son las {momento.hour} y {momento.minute:02d}, "
        f"{_DIAS[momento.weekday()]} {momento.day} de {_MESES[momento.month - 1]} "
        f"de {momento.year}."
    )


# ═══════════════════════════════════════════════════════════════════════
#  Estado del equipo
# ═══════════════════════════════════════════════════════════════════════

def estado_del_equipo(workspace: str = ".") -> str:
    """CPU, RAM, disco y batería, redactados para voz.

    `psutil` en vez de re-implementar por SO como el resto del archivo: aquí
    sí compensa la dependencia, porque no hay un mecanismo nativo simple y
    uniforme para esto en Windows/Linux/macOS a la vez.
    """
    cpu = psutil.cpu_percent(interval=0.3)
    ram = psutil.virtual_memory()
    partes = [
        f"CPU al {cpu:.0f} por ciento.",
        f"Memoria al {ram.percent:.0f} por ciento, "
        f"{_gib(ram.used)} de {_gib(ram.total)} gigas.",
    ]

    try:
        disco = psutil.disk_usage(workspace)
        partes.append(
            f"Disco al {disco.percent:.0f} por ciento, "
            f"{_gib(disco.free)} gigas libres."
        )
    except OSError:
        # Ruta de workspace no válida en este equipo: no es motivo para
        # dejar de informar del resto.
        pass

    bateria = psutil.sensors_battery()
    if bateria is not None:
        estado = "cargando" if bateria.power_plugged else "sin cargador"
        partes.append(f"Batería al {bateria.percent:.0f} por ciento, {estado}.")

    return " ".join(partes)


def _gib(bytes_: float) -> str:
    return f"{bytes_ / (1024 ** 3):.1f}"


# ═══════════════════════════════════════════════════════════════════════
#  Registro en MCP
# ═══════════════════════════════════════════════════════════════════════

def herramientas_de_sistema() -> list[Any]:
    """Las herramientas de sistema, para registrarlas junto a las de memoria.

    Devuelve la lista en vez de un servidor propio a propósito: dos servidores
    MCP con el mismo nombre se pisarían, y separarlos en dos nombres obligaría
    a mantener dos listas de permisos. Un único servidor `jarvis` con todo
    dentro es más simple y deja los nombres de herramienta estables.
    """

    @tool(
        "volumen",
        "Sube, baja o silencia el volumen del sistema. Úsalo cuando el usuario "
        "pida más o menos volumen, o silencio.",
        {
            "accion": {
                "type": "string",
                "enum": ["subir", "bajar", "silenciar"],
                "description": "Qué hacer con el volumen.",
            },
        },
    )
    async def volumen(args: dict[str, Any]) -> dict[str, Any]:
        texto = ajustar_volumen(str(args.get("accion", "")))
        return {"content": [{"type": "text", "text": texto}]}

    @tool(
        "abrir",
        "Abre un programa, un archivo o una página web. Acepta el nombre de una "
        "aplicación ('spotify', 'notepad'), una ruta o una URL. Pide "
        "confirmación al usuario antes de ejecutarse.",
        {"objetivo": str},
    )
    async def abrir_algo(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": abrir(str(args.get("objetivo", "")))}]}

    @tool(
        "bloquear_pantalla",
        "Bloquea la sesión del equipo, como al irse de la mesa.",
        {},
    )
    async def bloquear(args: dict[str, Any]) -> dict[str, Any]:
        del args
        return {"content": [{"type": "text", "text": bloquear_pantalla()}]}

    @tool(
        "hora",
        "Dice la fecha y la hora actuales del equipo.",
        {},
    )
    async def hora(args: dict[str, Any]) -> dict[str, Any]:
        del args
        return {"content": [{"type": "text", "text": decir_hora()}]}

    @tool(
        "estado_del_equipo",
        "Dice cómo está el equipo: uso de CPU, memoria, disco y batería si "
        "la hay. Úsalo cuando el usuario pregunte si el equipo va lento, "
        "cuánta batería queda, o cómo anda de recursos.",
        {},
    )
    async def estado(args: dict[str, Any]) -> dict[str, Any]:
        del args
        return {"content": [{"type": "text", "text": estado_del_equipo()}]}

    return [volumen, abrir_algo, bloquear, hora, estado]

