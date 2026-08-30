"""Herramientas propias de J.A.R.V.I.S., expuestas a Claude vía MCP.

Se registran en proceso (`create_sdk_mcp_server`), sin lanzar subprocesos ni
abrir puertos: son funciones de Python que Claude puede llamar.

Llevan el prefijo ``mcp__jarvis__``, que el sistema de permisos reconoce como
seguro: escribir en la memoria del propio asistente no toca archivos del
usuario ni ejecuta nada.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from ..core.memory import CATEGORIAS, Memory
from .temporizadores import Avisador, herramientas_de_temporizador


def construir_servidor_jarvis(memoria: Memory, avisar: Avisador) -> Any:
    """El servidor MCP con TODAS las herramientas propias de J.A.R.V.I.S.

    Memoria, sistema y temporizadores van juntas en un único servidor porque
    dos con el mismo nombre se pisarían, y usar dos nombres distintos
    obligaría a mantener dos listas de permisos para nada.

    `avisar`: ver el docstring de `jarvis/tools/temporizadores.py` — hace que
    el núcleo hable cuando se cumpla un temporizador, aunque no se le haya
    pedido nada en ese turno.
    """

    lista_categorias = ", ".join(f"{k} ({v})" for k, v in CATEGORIAS.items())

    @tool(
        "recordar",
        "Guarda un dato sobre el usuario para conversaciones futuras. Úsalo "
        "cuando aprendas algo duradero: su nombre, a qué se dedica, sus "
        "preferencias o en qué proyectos anda. No lo uses para detalles "
        f"efímeros de la conversación actual. Categorías: {lista_categorias}",
        {
            "hecho": str,
            "categoria": {
                "type": "string",
                "enum": list(CATEGORIAS),
                "description": "Dónde archivarlo.",
            },
        },
    )
    async def recordar(args: dict[str, Any]) -> dict[str, Any]:
        mensaje = memoria.recordar(
            str(args.get("hecho", "")), str(args.get("categoria", "notas"))
        )
        return {"content": [{"type": "text", "text": mensaje}]}

    @tool(
        "olvidar",
        "Borra de la memoria las anotaciones que contengan un texto dado. "
        "Úsalo cuando el usuario te pida olvidar algo o te corrija un dato "
        "que habías guardado mal.",
        {"texto": str},
    )
    async def olvidar(args: dict[str, Any]) -> dict[str, Any]:
        mensaje = memoria.olvidar(str(args.get("texto", "")))
        return {"content": [{"type": "text", "text": mensaje}]}

    @tool(
        "consultar_memoria",
        "Lee todo lo que tienes anotado sobre el usuario. Ya lo recibes al "
        "empezar la conversación, así que sólo hace falta si sospechas que "
        "ha cambiado o el usuario te pregunta explícitamente qué recuerdas.",
        {},
    )
    async def consultar_memoria(args: dict[str, Any]) -> dict[str, Any]:
        del args
        contenido = memoria.cargar() or "Todavía no hay nada anotado."
        return {"content": [{"type": "text", "text": contenido}]}

    from .sistema import herramientas_de_sistema

    return create_sdk_mcp_server(
        name="jarvis",
        version="1.0.0",
        tools=[
            recordar,
            olvidar,
            consultar_memoria,
            *herramientas_de_sistema(),
            *herramientas_de_temporizador(avisar),
        ],
    )
