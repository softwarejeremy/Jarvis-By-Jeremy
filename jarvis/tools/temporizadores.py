"""Temporizadores y alarmas: "avísame en 10 minutos".

## Por qué esto es distinto al resto de `jarvis/tools/`

`volumen`, `abrir`, `estado_del_equipo`... todas devuelven texto y terminan
ahí. Un temporizador no: tiene que hablar **más tarde**, sin que nadie se lo
haya pedido en ese momento — el mismo problema que ya resolvió el guardián
de permisos para poder preguntar por voz antes de que el núcleo exista
(`jarvis/main.py:_construir`, la variable `contenedor`). Aquí se reutiliza
exactamente esa indirección: `avisar` es una función que, llamada, hace que
`JarvisCore._decir_ahora()` hable — el mismo canal que ya usa el saludo y
los avisos de timeout, no un segundo camino de voz inventado para esto.

## Lo que esto NO hace, a propósito

No sobrevive un reinicio: es una tarea de `asyncio` en memoria. Nadie espera
que un "avísame en 10 minutos" aguante apagar el equipo, y persistirlo sería
una abstracción sin necesidad real todavía. Tampoco hay "cancelar
temporizador": no se ha pedido, y añadir seguimiento de estado para eso no
compensa antes del primer uso real.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from claude_agent_sdk import tool

# Habla YA, con el núcleo ya vivo — ver el docstring del módulo.
Avisador = Callable[[str], Awaitable[None]]


def _texto_confirmacion(minutos: float) -> str:
    if minutos == 1:
        return "Vale, le aviso en un minuto."
    if minutos == int(minutos):
        return f"Vale, le aviso en {int(minutos)} minutos."
    return f"Vale, le aviso en {minutos:.1f} minutos."


def _texto_aviso(minutos: float, mensaje: str | None) -> str:
    unidad = "minuto" if minutos == 1 else "minutos"
    cantidad = str(int(minutos)) if minutos == int(minutos) else f"{minutos:.1f}"
    if mensaje:
        return f"Han pasado {cantidad} {unidad}: {mensaje}."
    return f"Han pasado los {cantidad} {unidad} que pidió."


async def _disparar(
    segundos: float, texto: str, avisar: Avisador, *, dormir: Any = asyncio.sleep
) -> None:
    """Espera y avisa. Separado de la herramienta para poder probarlo sin
    esperar minutos de verdad: los tests inyectan `dormir` con una espera
    mínima en vez del `asyncio.sleep` real."""
    await dormir(segundos)
    await avisar(texto)


def herramientas_de_temporizador(avisar: Avisador) -> list[Any]:
    """`avisar`: ver el docstring del módulo. Se construye en `main.py` con
    la misma indirección que ya usa el guardián de permisos para hablar con
    un núcleo que todavía no existe en el momento del registro.
    """

    @tool(
        "poner_temporizador",
        "Programa un aviso hablado dentro de un número de minutos. Úsalo "
        "cuando el usuario pida que le recuerden algo pasado un rato "
        "('avísame en 10 minutos', 'ponme una alarma de 5 minutos').",
        {
            "minutos": float,
            "mensaje": {
                "type": "string",
                "description": "Qué decir al sonar. Opcional.",
            },
        },
    )
    async def poner_temporizador(args: dict[str, Any]) -> dict[str, Any]:
        try:
            minutos = float(args.get("minutos") or 0)
        except (TypeError, ValueError):
            minutos = 0
        if minutos <= 0:
            return {
                "content": [{"type": "text", "text": "No he entendido cuánto tiempo."}]
            }

        mensaje = str(args.get("mensaje") or "").strip() or None
        texto = _texto_aviso(minutos, mensaje)

        # No se espera aquí: el turno de conversación no puede quedarse
        # colgado hasta que suene el temporizador.
        asyncio.create_task(_disparar(minutos * 60, texto, avisar))

        return {"content": [{"type": "text", "text": _texto_confirmacion(minutos)}]}

    return [poner_temporizador]
