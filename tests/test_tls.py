"""Certificado autofirmado para HTTPS.

Existe por una razón muy concreta: los navegadores sólo exponen el micrófono
en contexto seguro. Sobre `http://192.168.1.x` —que es como entras desde el
móvil— `navigator.mediaDevices` ni siquiera está definido, así que sin TLS no
hay forma de hablarle desde el celular.
"""

from __future__ import annotations

import datetime as dt

import pytest

from jarvis.server import tls

cryptography = pytest.importorskip("cryptography")
from cryptography import x509  # noqa: E402


def leer(cert_path):  # noqa: ANN001, ANN201
    return x509.load_pem_x509_certificate(cert_path.read_bytes())


def nombres(cert):  # noqa: ANN001, ANN201
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return {str(v) for v in san.get_values_for_type(x509.IPAddress)} | set(
        san.get_values_for_type(x509.DNSName)
    )


class TestGeneracion:
    def test_crea_certificado_y_clave(self, tmp_path):
        cert, clave = tls.asegurar_certificado(tmp_path)
        assert cert.is_file() and clave.is_file()
        assert cert.read_bytes().startswith(b"-----BEGIN CERTIFICATE-----")
        assert b"PRIVATE KEY" in clave.read_bytes()

    def test_siempre_cubre_localhost(self, tmp_path):
        """Sin esto, el HUD daría error de certificado en el propio equipo."""
        cert, _ = tls.asegurar_certificado(tmp_path)
        assert {"localhost", "127.0.0.1"} <= nombres(leer(cert))

    def test_incluye_la_ip_de_la_red(self, tmp_path):
        cert, _ = tls.asegurar_certificado(tmp_path, ["192.168.1.37"])
        assert "192.168.1.37" in nombres(leer(cert))

    def test_ignora_una_ip_inválida(self, tmp_path):
        # Que `ip_local()` devuelva algo raro no puede impedir arrancar.
        cert, _ = tls.asegurar_certificado(tmp_path, ["no-es-una-ip", "10.0.0.9"])
        assert "10.0.0.9" in nombres(leer(cert))

    def test_no_es_una_autoridad_certificadora(self, tmp_path):
        # Un certificado de servidor no debe poder firmar otros.
        cert = leer(tls.asegurar_certificado(tmp_path)[0])
        limites = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        assert limites.ca is False

    def test_vale_desde_hace_un_rato(self, tmp_path):
        """Con relojes ligeramente desajustados, "válido desde ahora mismo"
        haría que el navegador lo rechazara por venir del futuro."""
        cert = leer(tls.asegurar_certificado(tmp_path)[0])
        assert cert.not_valid_before_utc < dt.datetime.now(dt.timezone.utc)

    def test_la_clave_no_lleva_contraseña(self, tmp_path):
        # uvicorn la carga sin poder preguntar nada por consola.
        from cryptography.hazmat.primitives import serialization

        _, clave = tls.asegurar_certificado(tmp_path)
        assert serialization.load_pem_private_key(clave.read_bytes(), password=None)


class TestReutilizacion:
    def test_no_lo_regenera_en_cada_arranque(self, tmp_path):
        """Si cambiara cada vez, habría que aceptar el aviso una y otra vez."""
        primero = leer(tls.asegurar_certificado(tmp_path, ["192.168.1.37"])[0])
        segundo = leer(tls.asegurar_certificado(tmp_path, ["192.168.1.37"])[0])
        assert primero.serial_number == segundo.serial_number

    def test_lo_rehace_si_cambia_la_ip(self, tmp_path):
        # Otra wifi, otro router: si la IP no está en el certificado, el móvil
        # daría un error más confuso que el aviso habitual.
        primero = leer(tls.asegurar_certificado(tmp_path, ["192.168.1.37"])[0])
        segundo = leer(tls.asegurar_certificado(tmp_path, ["10.0.0.5"])[0])
        assert primero.serial_number != segundo.serial_number

    def test_lo_rehace_si_está_corrupto(self, tmp_path):
        cert, _ = tls.asegurar_certificado(tmp_path)
        cert.write_text("esto no es un certificado")
        nuevo, _ = tls.asegurar_certificado(tmp_path)
        assert leer(nuevo)  # se regeneró y vuelve a ser legible

    def test_crea_el_directorio_si_no_existe(self, tmp_path):
        destino = tmp_path / "no" / "existe"
        cert, _ = tls.asegurar_certificado(destino)
        assert cert.is_file()
