import datetime, shutil, subprocess, base64, ssl, threading, socket, tempfile, pathlib
import pytest
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from velosonet.tlspin import spki_pin_from_der, fetch_cert_der, describe

def _selfsigned():
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bbu-test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30)).sign(key, hashes.SHA256()))
    return key, cert

def test_pin_matches_openssl_recipe():
    if not shutil.which("openssl"): return
    _, cert = _selfsigned()
    pem = cert.public_bytes(serialization.Encoding.PEM)
    cmd = "openssl x509 -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | openssl enc -base64"
    ref = subprocess.run(cmd, shell=True, input=pem, capture_output=True, check=True).stdout.decode().strip()
    assert spki_pin_from_der(cert.public_bytes(serialization.Encoding.DER)) == ref

def test_fetch_from_local_tls_server():
    key, cert = _selfsigned(); d = pathlib.Path(tempfile.mkdtemp())
    (d / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (d / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(d / "c.pem", d / "k.pem")
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1); port = srv.getsockname()[1]
    def serve():
        try:
            c, _ = srv.accept()
            with ctx.wrap_socket(c, server_side=True): pass
        except Exception: pass
    threading.Thread(target=serve, daemon=True).start()
    der = fetch_cert_der("127.0.0.1", port)
    assert der == cert.public_bytes(serialization.Encoding.DER) and describe(der)["self_signed"]
    srv.close()
