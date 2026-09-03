"""Las API keys en el almacén de credenciales del sistema.

`keyring` es un extra opcional y este sandbox no tiene ningún backend de
credenciales configurado (regla de CLAUDE.md: nunca depender de esa
casualidad), así que todo se prueba con un módulo `keyring` falso inyectado
en `sys.modules`, nunca con el paquete real.
"""

from __future__ import annotations

import sys

from jarvis import claves


class _KeyringFalso:
    def __init__(self, valores: dict | None = None, *, falla: bool = False) -> None:
        self._valores = valores or {}
        self._falla = falla
        self.guardadas: dict = {}

    def get_password(self, servicio, alias):  # noqa: ANN001
        if self._falla:
            raise RuntimeError("sin backend de credenciales")
        return self._valores.get((servicio, alias))

    def set_password(self, servicio, alias, valor):  # noqa: ANN001
        if self._falla:
            raise RuntimeError("sin backend de credenciales")
        self.guardadas[(servicio, alias)] = valor


class TestLeer:
    def test_sin_keyring_instalado_devuelve_none(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "keyring", None)
        assert claves.leer("anthropic") is None

    def test_lee_la_clave_guardada(self, monkeypatch):
        falso = _KeyringFalso({("jarvis", "ANTHROPIC_API_KEY"): "sk-de-prueba"})
        monkeypatch.setitem(sys.modules, "keyring", falso)
        assert claves.leer("anthropic") == "sk-de-prueba"

    def test_sin_nada_guardado_devuelve_none(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "keyring", _KeyringFalso())
        assert claves.leer("anthropic") is None

    def test_un_backend_roto_no_revienta(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "keyring", _KeyringFalso(falla=True))
        assert claves.leer("anthropic") is None


class TestGuardar:
    def test_sin_keyring_instalado_lo_dice(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "keyring", None)
        mensaje = claves.guardar("anthropic", "sk-de-prueba")
        assert "no está instalado" in mensaje

    def test_guarda_y_lo_confirma(self, monkeypatch):
        falso = _KeyringFalso()
        monkeypatch.setitem(sys.modules, "keyring", falso)

        mensaje = claves.guardar("anthropic", "sk-de-prueba")

        assert falso.guardadas == {("jarvis", "ANTHROPIC_API_KEY"): "sk-de-prueba"}
        assert "guardada" in mensaje

    def test_un_backend_roto_lo_dice_sin_reventar(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "keyring", _KeyringFalso(falla=True))
        mensaje = claves.guardar("anthropic", "sk-de-prueba")
        assert "No he podido guardar" in mensaje


class TestClaves:
    def test_conoce_anthropic_y_elevenlabs(self):
        assert claves.CLAVES == {
            "anthropic": "ANTHROPIC_API_KEY",
            "elevenlabs": "ELEVENLABS_API_KEY",
        }
