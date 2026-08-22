"""Certificado autofirmado para servir el HUD por HTTPS.

Hace falta por una razón concreta y no negociable: el navegador **sólo expone
el micrófono en un contexto seguro** —HTTPS o `localhost`—. Sobre
`http://192.168.1.x`, que es como entrarías desde el móvil,
`navigator.mediaDevices` sencillamente no existe. Sin TLS no hay forma de
hablarle desde el celular.

Un certificado autofirmado no lo firma ninguna autoridad, así que el navegador
avisará la primera vez y habrá que aceptarlo a mano. A cambio no depende de
ningún servicio externo ni de tener un dominio: para un asistente que vive en
tu red local, es el equilibrio correcto.

El certificado se guarda en la carpeta de datos y se reutiliza: si se
regenerase en cada arranque, habría que aceptar el aviso una y otra vez.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
from pathlib import Path

# Un año: lo bastante para no molestar, lo bastante poco para que un
# certificado olvidado en un equipo viejo acabe caducando.
VALIDEZ_DIAS = 365
# Se regenera antes de caducar, para que nunca falle justo al arrancar.
MARGEN_DIAS = 14


def asegurar_certificado(directorio: Path, ips: list[str] | None = None) -> tuple[Path, Path]:
    """Devuelve (certificado, clave), generándolos si hace falta.

    Se incluyen en el certificado tanto `localhost` como la IP local, para que
    valga desde el propio equipo y desde el móvil sin avisos distintos.
    """
    directorio = Path(directorio)
    directorio.mkdir(parents=True, exist_ok=True)
    cert = directorio / "jarvis-cert.pem"
    clave = directorio / "jarvis-key.pem"

    if _sigue_valido(cert, ips or []):
        return cert, clave

    _generar(cert, clave, ips or [])
    return cert, clave


def _sigue_valido(cert: Path, ips: list[str]) -> bool:
    """¿Existe, no está a punto de caducar y cubre las IPs de hoy?"""
    if not cert.is_file():
        return False

    try:
        from cryptography import x509

        certificado = x509.load_pem_x509_certificate(cert.read_bytes())
    except Exception:  # noqa: BLE001 - ilegible o corrupto: se regenera
        return False

    if certificado.not_valid_after_utc - _ahora() < dt.timedelta(days=MARGEN_DIAS):
        return False

    # La IP de la red puede cambiar (otro router, otra wifi). Si la actual no
    # está en el certificado, el móvil daría un error distinto y más confuso
    # que el aviso normal, así que conviene rehacerlo.
    try:
        from cryptography import x509 as _x509

        alternativos = certificado.extensions.get_extension_for_class(
            _x509.SubjectAlternativeName
        ).value
        cubiertas = {str(ip) for ip in alternativos.get_values_for_type(_x509.IPAddress)}
    except Exception:  # noqa: BLE001
        return False

    return all(ip in cubiertas for ip in ips)


def _ahora() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _generar(cert: Path, clave: Path, ips: list[str]) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    # Curva elíptica en vez de RSA: se genera al instante y es igual de sólida.
    llave = ec.generate_private_key(ec.SECP256R1())

    nombre = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "J.A.R.V.I.S."),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "J.A.R.V.I.S. local"),
    ])

    alternativos: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    for ip in ips:
        try:
            alternativos.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            continue

    ahora = _ahora()
    certificado = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)          # autofirmado: emisor y sujeto coinciden
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - dt.timedelta(minutes=5))  # margen por relojes desajustados
        .not_valid_after(ahora + dt.timedelta(days=VALIDEZ_DIAS))
        .add_extension(x509.SubjectAlternativeName(alternativos), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(llave, hashes.SHA256())
    )

    cert.write_bytes(certificado.public_bytes(serialization.Encoding.PEM))
    clave.write_bytes(
        llave.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    # La clave privada sólo la debe poder leer su dueño.
    with __import__("contextlib").suppress(OSError, NotImplementedError):
        clave.chmod(0o600)
