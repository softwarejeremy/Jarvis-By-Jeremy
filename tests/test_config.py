"""Prioridad de fuentes en `load_settings`.

Reportado en vivo: Jeremy cambió el modelo a `claude-sonnet-5` y J.A.R.V.I.S.
seguía usando `claude-opus-5`. La causa era pasar `config.toml` como kwargs
del constructor de `Settings` (`Settings(**toml)`) — para pydantic-settings
los kwargs del constructor son la fuente de MÁS prioridad, así que el
archivo le ganaba al entorno, al revés de lo que promete el docstring del
módulo ("el entorno siempre gana").
"""

from __future__ import annotations

from jarvis.config import load_settings


def _escribir_toml(tmp_path, contenido: str):
    ruta = tmp_path / "config.toml"
    ruta.write_text(contenido, encoding="utf-8")
    return ruta


class TestPrioridadDeFuentes:
    def test_el_entorno_le_gana_al_archivo(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENT__MODEL", "claude-sonnet-5")
        ruta = _escribir_toml(tmp_path, '[agent]\nmodel = "claude-opus-5"\n')

        assert load_settings(ruta).agent.model == "claude-sonnet-5"

    def test_el_archivo_se_usa_si_no_hay_entorno(self, tmp_path, monkeypatch):
        monkeypatch.delenv("JARVIS_AGENT__MODEL", raising=False)
        ruta = _escribir_toml(tmp_path, '[agent]\nmodel = "claude-opus-5"\n')

        assert load_settings(ruta).agent.model == "claude-opus-5"

    def test_el_valor_por_defecto_si_no_hay_ni_archivo_ni_entorno(self, tmp_path, monkeypatch):
        monkeypatch.delenv("JARVIS_AGENT__MODEL", raising=False)
        ruta = _escribir_toml(tmp_path, "")

        assert load_settings(ruta).agent.model == "claude-opus-5"
