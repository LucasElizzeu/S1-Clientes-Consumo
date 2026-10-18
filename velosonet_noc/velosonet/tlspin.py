"""Pinagem de certificado por chave pública (SPKI SHA-256), para BBUs com certificado autoassinado.
Em vez de desligar a verificação TLS, o Chromium passa a aceitar SOMENTE a chave pública indicada
(--ignore-certificate-errors-spki-list). Qualquer outro certificado inválido continua sendo recusado."""
from __future__ import annotations
import base64, hashlib, socket, ssl
from urllib.parse import urlparse
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

def spki_pin_from_der(der: bytes) -> str:
    cert = x509.load_der_x509_certificate(der)
    spki = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(hashlib.sha256(spki).digest()).decode()

def fetch_cert_der(host: str, port: int = 443, timeout: float = 10) -> bytes:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # só para LER o certificado apresentado; nada é enviado
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ss:
            return ss.getpeercert(True)

def describe(der: bytes) -> dict:
    c = x509.load_der_x509_certificate(der)
    return dict(subject=c.subject.rfc4514_string(), issuer=c.issuer.rfc4514_string(),
                not_before=c.not_valid_before_utc.isoformat(), not_after=c.not_valid_after_utc.isoformat(),
                sha256_cert=c.fingerprint(hashes.SHA256()).hex(":"), self_signed=(c.subject == c.issuer))

def host_port(url: str) -> tuple[str, int]:
    u = urlparse(url)
    return u.hostname, (u.port if u.scheme == "https" and u.port else 443)
