"""Memoria de largo plazo.

El SDK ya mantiene el hilo de una conversación, pero eso se pierde al cerrar
el programa. Esto es lo que hace que J.A.R.V.I.S. siga sabiendo quién eres
mañana.

Deliberadamente son archivos Markdown en `~/.jarvis/memory/`, no una base de
datos: puedes abrirlos, leerlos, corregirlos a mano y borrar lo que no
quieras que recuerde. Una memoria opaca sobre un usuario concreto es
justamente lo que no queremos.

Se cargan enteros en el system prompt al arrancar. Es viable porque son
pequeños por diseño; si algún día crecen, el sitio para paginar es aquí.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

# Categorías fijas. Limitarlas evita que acabe con cuarenta archivos sueltos.
CATEGORIAS = {
    "perfil": "Quién es el usuario: nombre, oficio, contexto personal.",
    "preferencias": "Cómo le gustan las cosas: estilo, herramientas, costumbres.",
    "proyectos": "En qué está trabajando.",
    "notas": "Todo lo demás que merezca recordarse.",
}

_LIMITE_CARACTERES = 8_000  # tope por archivo, para no inflar el prompt


class Memory:
    """Lee y escribe la memoria persistente."""

    def __init__(self, directorio: Path) -> None:
        self.dir = Path(directorio)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _archivo(self, categoria: str) -> Path:
        if categoria not in CATEGORIAS:
            categoria = "notas"
        return self.dir / f"{categoria}.md"

    # ── lectura ─────────────────────────────────────────────────────────
    def cargar(self) -> str:
        """Todo lo recordado, formateado para inyectarlo en el system prompt."""
        bloques: list[str] = []
        for categoria in CATEGORIAS:
            contenido = self.leer(categoria).strip()
            if contenido:
                bloques.append(f"### {categoria.capitalize()}\n{contenido}")
        return "\n\n".join(bloques)

    def leer(self, categoria: str) -> str:
        archivo = self._archivo(categoria)
        if not archivo.is_file():
            return ""
        return archivo.read_text(encoding="utf-8")

    # ── escritura ───────────────────────────────────────────────────────
    def recordar(self, hecho: str, categoria: str = "notas") -> str:
        """Añade un hecho. Devuelve un mensaje para J.A.R.V.I.S."""
        hecho = " ".join(hecho.split()).strip()
        if not hecho:
            return "No había nada que recordar."

        archivo = self._archivo(categoria)
        existente = self.leer(categoria)

        # No duplicar lo que ya sabe.
        if hecho.lower() in existente.lower():
            return "Eso ya lo tenía anotado."

        fecha = datetime.now().strftime("%Y-%m-%d")
        linea = f"- {hecho}  _(anotado el {fecha})_\n"

        nuevo = existente + linea
        if len(nuevo) > _LIMITE_CARACTERES:
            # Se descartan las entradas más antiguas: la memoria reciente es
            # la que suele importar, y un prompt infinito no es viable.
            lineas = [ln for ln in nuevo.splitlines(keepends=True) if ln.strip()]
            while len("".join(lineas)) > _LIMITE_CARACTERES and len(lineas) > 1:
                lineas.pop(0)
            nuevo = "".join(lineas)

        archivo.write_text(nuevo, encoding="utf-8")
        return f"Anotado en {categoria}."

    def olvidar(self, texto: str) -> str:
        """Borra las entradas que contengan `texto`, en cualquier categoría."""
        aguja = texto.lower().strip()
        if not aguja:
            return "Hay que decirme qué olvidar."

        borradas = 0
        for categoria in CATEGORIAS:
            archivo = self._archivo(categoria)
            if not archivo.is_file():
                continue
            lineas = archivo.read_text(encoding="utf-8").splitlines(keepends=True)
            quedan = [ln for ln in lineas if aguja not in ln.lower()]
            if len(quedan) != len(lineas):
                borradas += len(lineas) - len(quedan)
                archivo.write_text("".join(quedan), encoding="utf-8")

        if borradas == 0:
            return "No he encontrado nada que coincida."
        return f"Olvidado ({borradas} {'entrada' if borradas == 1 else 'entradas'})."
