"""El cerebro: envoltura sobre el Claude Agent SDK.

Aquí es donde J.A.R.V.I.S. deja de ser una interfaz de voz y se convierte en
una extensión real de Claude. El SDK aporta el bucle de agente, las
herramientas (leer, escribir, ejecutar, buscar en la web) y el manejo de
contexto; nosotros aportamos personalidad, permisos y voz.

La clase expone un único método, :meth:`Agent.ask`, que devuelve un flujo de
trozos tipados. Quien lo consume no necesita saber nada del SDK.
"""

from __future__ import annotations

import os
import platform
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolUseBlock,
)

from ..config import Settings
from .personality import build_system_prompt


# ── trozos que produce el agente ────────────────────────────────────────
@dataclass(slots=True)
class Delta:
    """Un fragmento de texto recién escrito por Claude."""

    text: str


@dataclass(slots=True)
class ToolCall:
    """Claude va a usar una herramienta."""

    name: str
    input: dict[str, Any]


@dataclass(slots=True)
class Done:
    """Fin del turno, con lo que costó."""

    cost_usd: float | None = None
    session_id: str | None = None
    error: str | None = None


Chunk = Delta | ToolCall | Done


class AgentProtocol(Protocol):
    """Lo que el núcleo necesita de un cerebro. Permite sustituirlo en tests."""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def ask(self, prompt: str) -> AsyncIterator[Chunk]: ...


class Agent:
    """Conversación persistente con Claude a través del Agent SDK."""

    def __init__(
        self,
        settings: Settings,
        *,
        can_use_tool: Any = None,
        mcp_servers: dict[str, Any] | None = None,
        memoria: str = "",
    ) -> None:
        self._settings = settings
        self._can_use_tool = can_use_tool
        self._mcp_servers = mcp_servers or {}
        self._memoria = memoria
        self._client: ClaudeSDKClient | None = None
        self.session_id: str | None = None
        self.coste_sesion_usd: float = 0.0

    # ── ciclo de vida ───────────────────────────────────────────────────
    def _build_options(self) -> ClaudeAgentOptions:
        s = self._settings

        system_prompt = build_system_prompt(
            user_name=s.agent.user_name,
            workspace=str(s.workspace),
            so=f"{platform.system()} {platform.release()}",
            memoria=self._memoria,
        )

        # La API key se le pasa al subproceso del CLI por entorno. Si está
        # vacía, el SDK caerá en las credenciales que encuentre en la máquina.
        env = dict(os.environ)
        if s.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = s.anthropic_api_key

        return ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=s.agent.model,
            effort=s.agent.effort,
            max_budget_usd=s.agent.max_budget_usd,
            cwd=str(s.workspace),
            add_dirs=[str(p) for p in s.agent.extra_dirs],
            env=env,
            can_use_tool=self._can_use_tool,
            mcp_servers=self._mcp_servers,
            # Necesario para recibir el texto token a token y poder empezar a
            # hablar antes de que Claude termine de escribir.
            include_partial_messages=True,
            # No heredamos la configuración de Claude Code de la máquina:
            # J.A.R.V.I.S. debe comportarse igual en cualquier equipo.
            setting_sources=[],
        )

    async def start(self) -> None:
        self._client = ClaudeSDKClient(options=self._build_options())
        await self._client.connect()

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def __aenter__(self) -> Agent:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    # ── conversación ────────────────────────────────────────────────────
    async def ask(self, prompt: str) -> AsyncIterator[Chunk]:
        """Envía un turno y va soltando lo que Claude produce.

        Se prefieren los deltas del streaming (llegan token a token) sobre el
        mensaje completo, que llegaría demasiado tarde para el TTS. Si por lo
        que sea no hubo deltas, se recurre al texto del mensaje final para no
        quedarnos mudos.
        """
        if self._client is None:
            raise RuntimeError("El agente no está iniciado; llama a start() primero.")

        await self._client.query(prompt)

        hubo_deltas = False

        async for message in self._client.receive_response():
            # 1. Texto en streaming.
            if isinstance(message, StreamEvent):
                texto = _extraer_delta(message.event)
                if texto:
                    hubo_deltas = True
                    yield Delta(texto)
                continue

            # 2. Mensaje completo: nos interesan las herramientas, y el texto
            #    sólo como red de seguridad.
            if isinstance(message, AssistantMessage):
                if message.error:
                    yield Done(error=_mensaje_de_error(message.error))
                    return
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        yield ToolCall(block.name, block.input or {})
                    elif isinstance(block, TextBlock) and not hubo_deltas:
                        yield Delta(block.text)
                continue

            # 3. Fin del turno.
            if isinstance(message, ResultMessage):
                self.session_id = message.session_id
                if message.total_cost_usd:
                    self.coste_sesion_usd = message.total_cost_usd
                error = None
                if message.is_error:
                    error = (message.errors or ["error desconocido"])[0]
                yield Done(
                    cost_usd=message.total_cost_usd,
                    session_id=message.session_id,
                    error=error,
                )

    async def interrupt(self) -> None:
        """Corta lo que Claude esté haciendo ahora mismo."""
        if self._client is not None:
            await self._client.interrupt()


def _extraer_delta(event: dict[str, Any]) -> str:
    """Saca el texto de un evento de streaming de la API, si lo lleva."""
    if event.get("type") != "content_block_delta":
        return ""
    delta = event.get("delta") or {}
    if delta.get("type") != "text_delta":
        return ""
    return delta.get("text") or ""


def _mensaje_de_error(codigo: str) -> str:
    """Traduce los códigos de error del SDK a algo que se pueda decir en voz alta."""
    return {
        "authentication_failed": (
            "La clave de la API no es válida. Revise el archivo punto env."
        ),
        "billing_error": (
            "No hay saldo en la cuenta de Anthropic. Hay que recargarla en la consola."
        ),
        "rate_limit": "Se ha alcanzado el límite de peticiones. Deme un momento.",
        "invalid_request": "La petición no era válida.",
        "server_error": "El servidor de Anthropic ha fallado. Intente de nuevo.",
    }.get(codigo, "Ha ocurrido un error inesperado al contactar con Claude.")


# ── cerebro de mentira, para probar sin gastar ──────────────────────────
class DemoAgent:
    """Responde con texto simulado. Sirve para ver la interfaz y probar toda
    la cadena de voz sin API key y sin gastar un céntimo."""

    RESPUESTAS = [
        "Sistemas operativos al cien por cien. Estoy en modo demostración, "
        "así que no tengo acceso real a Claude todavía.",
        "Entendido. En modo demostración me limito a responder con frases de "
        "ejemplo, pero la voz y la escucha funcionan igual que en producción.",
        "Anotado. Cuando configure la clave de la API responderé de verdad.",
    ]

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self._i = 0
        self.session_id = "demo"
        self.coste_sesion_usd = 0.0

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def interrupt(self) -> None: ...

    async def ask(self, prompt: str) -> AsyncIterator[Chunk]:
        import asyncio

        respuesta = self.RESPUESTAS[self._i % len(self.RESPUESTAS)]
        self._i += 1

        # Se emite palabra a palabra para imitar el ritmo del streaming real.
        for palabra in respuesta.split(" "):
            await asyncio.sleep(0.03)
            yield Delta(palabra + " ")
        yield Done(cost_usd=0.0, session_id="demo")
