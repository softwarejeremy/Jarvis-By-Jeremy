"""Dos J.A.R.V.I.S. a la vez se pelean por el micrófono y por el puerto, y hoy
el segundo muere sin decir nada. Estos tests cubren el cerrojo que lo evita."""

from __future__ import annotations

import json
import socket

from jarvis import instancia


def _puerto_libre() -> int:
    """Uno que nadie esté usando, para no chocar con el equipo de quien pruebe."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestReserva:
    def test_el_primero_la_consigue(self, tmp_path):
        reserva = instancia.reservar(tmp_path, puerto=_puerto_libre())
        assert reserva is not None
        reserva.liberar()

    def test_el_segundo_se_queda_sin_ella(self, tmp_path):
        puerto = _puerto_libre()
        primero = instancia.reservar(tmp_path, puerto=puerto)
        assert primero is not None
        try:
            assert instancia.reservar(tmp_path, puerto=puerto) is None
        finally:
            primero.liberar()

    def test_al_liberarla_vuelve_a_estar_disponible(self, tmp_path):
        puerto = _puerto_libre()
        primero = instancia.reservar(tmp_path, puerto=puerto)
        assert primero is not None
        primero.liberar()

        segundo = instancia.reservar(tmp_path, puerto=puerto)
        assert segundo is not None, "el cerrojo se ha quedado echado"
        segundo.liberar()

    def test_liberar_dos_veces_no_revienta(self, tmp_path):
        # Se llama desde un `finally` que puede recorrerse más de una vez.
        reserva = instancia.reservar(tmp_path, puerto=_puerto_libre())
        assert reserva is not None
        reserva.liberar()
        reserva.liberar()

    def test_no_activa_reuseaddr(self, tmp_path):
        # En Windows, SO_REUSEADDR permite robar un puerto que ya escucha: con
        # él puesto, el cerrojo no cerraría nada en la única plataforma que nos
        # importa.
        reserva = instancia.reservar(tmp_path, puerto=_puerto_libre())
        assert reserva is not None
        try:
            sock = reserva._sock
            assert sock is not None
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0
        finally:
            reserva.liberar()


class TestHuella:
    def test_guarda_el_pid_y_la_url_del_hud(self, tmp_path):
        reserva = instancia.reservar(tmp_path, puerto=_puerto_libre())
        assert reserva is not None
        try:
            reserva.anunciar("http://localhost:8765")
            huella = instancia.huella_ajena(tmp_path)
            assert huella is not None
            assert huella.url == "http://localhost:8765"
            assert huella.pid > 0
        finally:
            reserva.liberar()

    def test_sin_url_se_puede_anunciar_igual(self, tmp_path):
        # Sin --web no hay HUD que abrir, pero seguimos siendo la única instancia.
        reserva = instancia.reservar(tmp_path, puerto=_puerto_libre())
        assert reserva is not None
        try:
            reserva.anunciar(None)
            huella = instancia.huella_ajena(tmp_path)
            assert huella is not None
            assert huella.url is None
        finally:
            reserva.liberar()

    def test_al_liberar_se_borra(self, tmp_path):
        reserva = instancia.reservar(tmp_path, puerto=_puerto_libre())
        assert reserva is not None
        reserva.anunciar("http://localhost:8765")
        reserva.liberar()
        assert instancia.huella_ajena(tmp_path) is None

    def test_sin_archivo_no_hay_huella(self, tmp_path):
        assert instancia.huella_ajena(tmp_path) is None

    def test_una_huella_corrupta_se_ignora(self, tmp_path):
        (tmp_path / instancia.NOMBRE_HUELLA).write_text("{esto no es json", encoding="utf-8")
        assert instancia.huella_ajena(tmp_path) is None

    def test_una_huella_con_forma_rara_se_ignora(self, tmp_path):
        (tmp_path / instancia.NOMBRE_HUELLA).write_text('["una", "lista"]', encoding="utf-8")
        assert instancia.huella_ajena(tmp_path) is None

    def test_una_url_vacia_cuenta_como_ausente(self, tmp_path):
        (tmp_path / instancia.NOMBRE_HUELLA).write_text(
            json.dumps({"pid": 1, "url": ""}), encoding="utf-8"
        )
        huella = instancia.huella_ajena(tmp_path)
        assert huella is not None
        assert huella.url is None

    def test_una_huella_rancia_no_impide_arrancar(self, tmp_path):
        # El proceso murió de mala manera y dejó el archivo. Como el cerrojo es
        # el socket y no el archivo, el arranque siguiente tiene que funcionar.
        (tmp_path / instancia.NOMBRE_HUELLA).write_text(
            json.dumps({"pid": 999_999, "url": "http://localhost:8765"}), encoding="utf-8"
        )
        reserva = instancia.reservar(tmp_path, puerto=_puerto_libre())
        assert reserva is not None
        reserva.liberar()

    def test_un_directorio_que_no_existe_se_crea(self, tmp_path):
        destino = tmp_path / "sin" / "crear"
        reserva = instancia.reservar(destino, puerto=_puerto_libre())
        assert reserva is not None
        try:
            reserva.anunciar("http://localhost:8765")
            assert instancia.huella_ajena(destino) is not None
        finally:
            reserva.liberar()
