"""Motores de síntesis de voz."""

from .base import TTSEngine, crear_motor, decodificar_a_pcm

__all__ = ["TTSEngine", "crear_motor", "decodificar_a_pcm"]
