"""Prioridad de fuentes en `load_settings`.

Reportado en vivo: Jeremy cambió el modelo a `claude-sonnet-5` y J.A.R.V.I.S.
seguía usando `claude-opus-5`. La causa era pasar `config.toml` como kwargs
del constructor de `Settings` (`Settings(**toml)`) — para pydantic-settings
los kwargs del constructor son la fuente de MÁS prioridad, así que el
archivo le ganaba al entorno, al revés de lo que promete el docstring del
módulo ("el entorno siempre gana").
"""

from __future__ import annotations

import pytest

from jarvis.config import Settings, load_settings


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


class TestClaveDesdeKeyring:
    """El almacén de credenciales del sistema va entre el entorno y `.env`:
    ver `_FuenteKeyring` en `jarvis/config.py`."""

    def test_se_usa_si_no_hay_entorno(self, monkeypatch):
        import jarvis.claves

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        def leer_falso(nombre):
            return "sk-del-almacen" if nombre == "anthropic" else None

        monkeypatch.setattr(jarvis.claves, "leer", leer_falso)

        assert Settings().anthropic_api_key == "sk-del-almacen"

    def test_el_entorno_le_gana(self, monkeypatch):
        import jarvis.claves

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-del-entorno")
        monkeypatch.setattr(jarvis.claves, "leer", lambda _nombre: "sk-del-almacen")

        assert Settings().anthropic_api_key == "sk-del-entorno"

    def test_sin_nada_en_el_almacen_no_rompe_nada(self, monkeypatch):
        import jarvis.claves

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(jarvis.claves, "leer", lambda _nombre: None)

        assert Settings().anthropic_api_key == ""


class TestEnvNoDependeDelDirectorio:
    """Reportado en vivo: J.A.R.V.I.S. respondía frases de ejemplo sin decir
    por qué. `env_file=".env"` se resuelve contra el directorio ACTUAL, así
    que arrancarlo desde otra carpeta lo dejaba sin `ANTHROPIC_API_KEY` —y
    por tanto en modo demostración— aunque el `.env` estuviera en su sitio.
    """

    def test_encuentra_el_env_del_proyecto_desde_otro_directorio(self, tmp_path, monkeypatch):
        from jarvis.config import PROJECT_ROOT, Settings

        env = PROJECT_ROOT / ".env"
        if env.exists():
            pytest.skip("hay un .env real en el proyecto; no se toca")

        env.write_text("ANTHROPIC_API_KEY=sk-de-prueba\n", encoding="utf-8")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)  # el caso real: arrancar desde otra carpeta
        try:
            assert Settings().anthropic_api_key == "sk-de-prueba"
        finally:
            env.unlink()
