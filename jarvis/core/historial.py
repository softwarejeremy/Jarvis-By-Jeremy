"""Registro de conversaciones, en disco.

El servidor web guardaba el historial en memoria: cerrar J.A.R.V.I.S. lo
perdía todo, y en modo voz puro (sin `--web`) no se registraba nada en
absoluto. Aquí se escucha el mismo bus de eventos —la fuente de verdad de
todo el proyecto— pero desde `main.py`, no desde el servidor: así queda
constancia haya o no HUD web mirando.

Un archivo JSONL por día en vez de un único archivo grande, y JSONL en vez
de Markdown como `memory.py`: esto es un registro que se **lee de vuelta**
programáticamente (para reponerlo al conectar, para listar días), no notas
que se editan a mano.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..events import Event, EventBus

_FORMATO_DIA = "%Y-%m-%d"


class Historial:
    """Lee y escribe el registro de conversaciones."""

    def __init__(self, directorio: Path) -> None:
        self.dir = Path(directorio)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _archivo(self, dia: str) -> Path:
        return self.dir / f"{dia}.jsonl"

    # ── escritura ───────────────────────────────────────────────────────
    def registrar(self, quien: str, texto: str) -> None:
        """Añade un turno al archivo de hoy."""
        texto = texto.strip()
        if not texto:
            return
        ahora = datetime.now()
        turno = {"hora": ahora.strftime("%H:%M:%S"), "quien": quien, "texto": texto}
        with self._archivo(ahora.strftime(_FORMATO_DIA)).open("a", encoding="utf-8") as f:
            f.write(json.dumps(turno, ensure_ascii=False) + "\n")

    def escuchar(self, bus: EventBus) -> None:
        """Se suscribe al bus: cada turno de la charla queda registrado solo.

        Mismo criterio que usaba el historial en memoria del servidor: la
        respuesta a un «¿lo autoriza?» no es parte de la charla, y
        `ASSISTANT_DONE` ya trae la respuesta entera (no hace falta acumular
        los `assistant_delta` a mano).
        """

        def _al_evento(evento: Event) -> None:
            from ..events import EventType

            if evento.type is EventType.FINAL_TRANSCRIPT:
                if evento.data.get("kind") == "confirmacion":
                    return
                self.registrar("usuario", str(evento.data.get("text", "")))
            elif evento.type is EventType.ASSISTANT_DONE:
                self.registrar("jarvis", str(evento.data.get("text", "")))

        bus.on(_al_evento)

    # ── lectura ─────────────────────────────────────────────────────────
    def dias(self) -> list[str]:
        """Los días con conversación registrada, más reciente primero."""
        return sorted((p.stem for p in self.dir.glob("*.jsonl")), reverse=True)

    def leer(self, dia: str, *, limite: int | None = None) -> list[dict[str, str]]:
        """Los turnos de un día. Un día sin archivo da una lista vacía."""
        archivo = self._archivo(dia)
        if not archivo.is_file():
            return []

        turnos: list[dict[str, str]] = []
        for linea in archivo.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea:
                continue
            try:
                turnos.append(json.loads(linea))
            except json.JSONDecodeError:
                # Una línea corrupta no debe tirar todo el día por la borda.
                continue

        if limite is not None:
            turnos = turnos[-limite:]
        return turnos
