"""Troceo de texto en frases, para el TTS incremental.

Es la pieza que más se nota en la latencia percibida. Si esperáramos a que
Claude terminase de escribir toda la respuesta para empezar a hablar, habría
un silencio de varios segundos. En vez de eso vamos alimentando el streaming
token a token, y en cuanto hay una frase completa, se manda a la voz mientras
Claude sigue redactando el resto.
"""

from __future__ import annotations

import re

# Fin de frase: puntuación de cierre seguida de espacio o final de texto.
# Incluye los signos de cierre del español (»", ), …) que van *después* del punto.
_FRASE_FIN = re.compile(r'[.!?…](?=[\s"\'»)\]]*(?:\s|$))|[\n\r]{2,}')

# Abreviaturas frecuentes en español: el punto NO termina la frase.
_ABREVIATURAS = {
    "sr", "sra", "srta", "dr", "dra", "lic", "ing", "prof", "av", "aprox",
    "etc", "ej", "p", "pág", "núm", "no", "ud", "uds", "vs", "a.m", "p.m",
}

# Longitud mínima de una frase antes de mandarla al TTS. Frases muy cortas
# ("Sí.") suenan entrecortadas si van solas; se acumulan con la siguiente.
_MIN_CHARS = 24
# Si un fragmento crece más que esto sin puntuación, se corta igual: más vale
# una pausa rara que un silencio eterno.
_MAX_CHARS = 240


def _es_abreviatura(texto: str, pos: int) -> bool:
    """¿El punto en `pos` pertenece a una abreviatura o a un número decimal?"""
    if texto[pos] != ".":
        return False

    # Decimales y versiones: "3.14", "v1.2"
    if pos + 1 < len(texto) and texto[pos + 1].isdigit():
        return True

    palabra = re.search(r"([\w.]+)$", texto[:pos])
    return bool(palabra and palabra.group(1).lower().rstrip(".") in _ABREVIATURAS)


class SentenceChunker:
    """Acumula texto en streaming y va soltando frases completas.

    Uso::

        chunker = SentenceChunker()
        for delta in stream:
            for frase in chunker.feed(delta):
                await tts.hablar(frase)
        for frase in chunker.flush():
            await tts.hablar(frase)
    """

    def __init__(self, min_chars: int = _MIN_CHARS, max_chars: int = _MAX_CHARS) -> None:
        self._buffer = ""
        self._min_chars = min_chars
        self._max_chars = max_chars

    def feed(self, delta: str) -> list[str]:
        """Añade texto y devuelve las frases que ya se pueden pronunciar."""
        self._buffer += delta
        frases: list[str] = []

        while True:
            corte = self._buscar_corte()
            if corte is None:
                break
            frase, self._buffer = self._buffer[:corte].strip(), self._buffer[corte:].lstrip()
            if frase:
                frases.append(frase)

        return frases

    def flush(self) -> list[str]:
        """Suelta lo que quede en el buffer. Llamar al final del stream."""
        resto = self._buffer.strip()
        self._buffer = ""
        return [resto] if resto else []

    # ── interno ─────────────────────────────────────────────────────────
    def _buscar_corte(self) -> int | None:
        for match in _FRASE_FIN.finditer(self._buffer):
            fin = match.end()
            if fin < self._min_chars:
                continue
            if _es_abreviatura(self._buffer, match.start()):
                continue
            return fin

        # Sin puntuación utilizable: si ya es muy largo, cortamos en el último
        # espacio para no partir una palabra por la mitad.
        if len(self._buffer) >= self._max_chars:
            espacio = self._buffer.rfind(" ", 0, self._max_chars)
            return espacio + 1 if espacio > self._min_chars else self._max_chars

        return None


# ── limpieza para voz ───────────────────────────────────────────────────

_BLOQUE_CODIGO = re.compile(r"```.*?```", re.DOTALL)
_CODIGO_INLINE = re.compile(r"`([^`]+)`")
_ENFASIS = re.compile(r"(\*{1,3}|_{1,3})(.+?)\1")
_ENLACE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_ENCABEZADO = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_VINETA = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_EMOJI = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff]+"
)


def limpiar_para_voz(texto: str) -> str:
    """Quita markdown, código y emojis: nada de eso suena bien leído en voz alta.

    Los bloques de código se sustituyen por una mención hablada; el código
    completo sigue viéndose en el HUD, que es donde tiene sentido.
    """
    texto = _BLOQUE_CODIGO.sub(" (el código está en pantalla) ", texto)
    texto = _ENLACE.sub(r"\1", texto)
    texto = _CODIGO_INLINE.sub(r"\1", texto)
    texto = _ENFASIS.sub(r"\2", texto)
    texto = _ENCABEZADO.sub("", texto)
    texto = _VINETA.sub("", texto)
    texto = _EMOJI.sub("", texto)
    return re.sub(r"\s+", " ", texto).strip()
