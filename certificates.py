"""This PC's own certificates, used to encrypt with TLS what travels between computers.

Each PC creates its own long-lived authority (CA) and signs the server certificate with it. What's shared when
pairing is the CA (its SHA-256 fingerprint): the server certificate can be redone when the name or the IPs
change without the paired computers having to pair again.

The CA can only sign .local names and private IPs (NameConstraints), so even if someone installed it as
trusted on an iPhone or Android, it couldn't be used to impersonate an internet site.
"""
import datetime
import functools
import hashlib
import ipaddress
import logging
import os
import ssl
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Iterable, Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

import bundle

FOLDER = bundle.data_folder() / "certificates"
OLD_FOLDER = "certificados"  # name before 1.0.7
CA_FILE = "ca.pem"
CA_KEY_FILE = "ca.key"
SERVER_FILE = "server.pem"
SERVER_KEY_FILE = "server.key"
# Server files written before 1.0.7, renamed on load.
OLD_FILES = {"servidor.pem": SERVER_FILE, "servidor.key": SERVER_KEY_FILE}

CA_DAYS = 3650
SERVER_DAYS = 397  # below the 825 that iOS and macOS accept for a private CA
RENEW_BEFORE = datetime.timedelta(days=30)
MAX_PEM = 8192

# The only things the CA may sign: .local names and private, link-local and loopback IPs.
ALLOWED_NETWORKS = tuple(ipaddress.ip_network(r) for r in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16", "100.64.0.0/10",
    "fc00::/7", "fe80::/10", "::1/128",
))


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _name(text: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, text[:64])])


def dns_name(name: str) -> str:
    """The network name as a valid DNS label (no accents or spaces), ending in .local."""
    from configuration import _sanitize_for_dns
    return f"{_sanitize_for_dns(name)[:63].rstrip('-') or 'copyover-pc'}.local"


def allowed_ips(ips: Iterable[str]) -> list[str]:
    """The IPs the CA may sign, without repeats, plus 127.0.0.1."""
    result = []
    for text in [*ips, "127.0.0.1"]:
        try:
            ip = ipaddress.ip_address(str(text).split("%")[0])
        except ValueError:
            continue
        if any(ip in network for network in ALLOWED_NETWORKS if network.version == ip.version) and str(ip) not in result:
            result.append(str(ip))
    return result


def der_fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def pem_fingerprint(pem: str | bytes) -> str:
    """SHA-256 fingerprint (hex) of the PEM certificate: what's stored from another PC's CA when pairing."""
    data = pem.encode("ascii") if isinstance(pem, str) else pem
    return der_fingerprint(x509.load_pem_x509_certificate(data).public_bytes(serialization.Encoding.DER))


def validate_ca(pem: object) -> str:
    """Checks that what arrived is a CopyOver CA (EC P-256, CA, signs certificates) and returns its PEM."""
    if not isinstance(pem, str) or not pem or len(pem) > MAX_PEM:
        raise ValueError("Invalid CA")
    cert = x509.load_pem_x509_certificate(pem.encode("ascii"))
    restrictions = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    use = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    key = cert.public_key()
    if not restrictions.ca or not use.key_cert_sign or not isinstance(key, ec.EllipticCurvePublicKey):
        raise ValueError("Invalid CA")
    if not isinstance(key.curve, ec.SECP256R1) or cert.not_valid_after_utc <= _now():
        raise ValueError("Invalid CA")
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


@functools.lru_cache(maxsize=32)
def client_context(ca_pem: str) -> ssl.SSLContext:
    """TLS context that trusts only the CA of a paired PC.

    The server name isn't checked (check_hostname=False) because the other PC is reached through the IP it
    announces over mDNS, and that IP changes. It isn't needed: the CA belongs to that PC alone and its private
    key never leaves it, so only that PC can present a certificate it signed."""
    context = ssl.create_default_context(cadata=ca_pem)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _restrict_permissions(path: Path) -> bool:
    """Only the current user can read the private key (on Windows, by removing inherited permissions).
    False, with a warning in the log, if it couldn't: the key then keeps the folder's permissions."""
    if sys.platform != "win32":
        try:
            os.chmod(path, 0o600)
            return True
        except OSError as error:
            logging.warning("Couldn't restrict the permissions of %s: %s", path.name, error)
            return False
    user = os.environ.get("USERNAME")
    if not user:
        logging.warning("Couldn't restrict the permissions of %s: USERNAME is missing", path.name)
        return False
    domain = os.environ.get("USERDOMAIN")
    count = f"{domain}\\{user}" if domain else user
    try:
        result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{count}:F"],
                                   capture_output=True, timeout=10, check=False,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as error:
        logging.warning("Couldn't restrict the permissions of %s: %s", path.name, error)
        return False
    if result.returncode != 0:
        output = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip()
        logging.warning("Couldn't restrict the permissions of %s (icacls %s): %s", path.name,
                        result.returncode, output)
        return False
    return True


def _write(path: Path, data: bytes, private: bool = False) -> None:
    """Writes to a .tmp and swaps it in one go; the private key is created with 0600 permissions."""
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                         0o600 if private else 0o644)
    with os.fdopen(descriptor, "wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    if private:
        _restrict_permissions(temporary)
    os.replace(temporary, path)


def _key_pem(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption())


def create_ca(name: str):
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key()
    now = _now()
    allowed = [x509.DNSName("local")] + [x509.IPAddress(network) for network in ALLOWED_NETWORKS]
    cert = (x509.CertificateBuilder()
            .subject_name(_name(f"CopyOver CA {name}"))
            .issuer_name(_name(f"CopyOver CA {name}"))
            .public_key(public)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                         crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.NameConstraints(permitted_subtrees=allowed, excluded_subtrees=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(public), critical=False)
            .sign(key, hashes.SHA256()))
    return key, cert


def create_server(key_ca, ca_cert, dns: str, ips: list[str], days: int = SERVER_DAYS):
    key = ec.generate_private_key(ec.SECP256R1())
    now = _now()
    names = [x509.DNSName(dns)] + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    cert = (x509.CertificateBuilder()
            .subject_name(_name(dns))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(min(now + datetime.timedelta(days=days), ca_cert.not_valid_after_utc))
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()), critical=False)
            .sign(key_ca, hashes.SHA256()))
    return key, cert


def _san(cert) -> tuple[set, set]:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return set(), set()
    return set(san.get_values_for_type(x509.DNSName)), {str(i) for i in san.get_values_for_type(x509.IPAddress)}


class Certificates:
    """This PC's CA and server certificate, stored in `carpeta`, and the current TLS context."""

    def __init__(self, folder: Path | str = None, name: str = "copyover-pc",
                 ips: Optional[Callable[[], Iterable[str]]] = None, server_days: int = SERVER_DAYS):
        from configuration import move_if_missing
        if folder is None:
            move_if_missing(FOLDER.with_name(OLD_FOLDER), FOLDER)
        self.folder = Path(folder) if folder is not None else FOLDER
        for old, new_item in OLD_FILES.items():
            move_if_missing(self.folder / old, self.folder / new_item)
        self.name = name
        self._ips = ips or (lambda: [])
        self.server_days = server_days
        self._lock = threading.Lock()
        self._context: Optional[ssl.SSLContext] = None
        self._ca_pem = ""
        self._fingerprint = ""

    def _path(self, file: str) -> Path:
        return self.folder / file

    # --- CA -----------------------------------------------------------------

    def _load_or_create_ca(self):
        path_cert, path_key = self._path(CA_FILE), self._path(CA_KEY_FILE)
        if path_cert.exists() and path_key.exists():
            try:
                cert = x509.load_pem_x509_certificate(path_cert.read_bytes())
                key = serialization.load_pem_private_key(path_key.read_bytes(), password=None)
                if key.public_key().public_numbers() != cert.public_key().public_numbers():
                    raise ValueError("the key is not the certificate's")
                if cert.not_valid_after_utc - _now() > RENEW_BEFORE:
                    return key, cert
                logging.warning("This PC's CA is about to expire: creating a new one (the PCs will have to pair again)")
            except (ValueError, TypeError, OSError) as error:
                # A new CA means pairing the other PCs again, but without it there's no TLS.
                logging.error("Couldn't read this PC's CA (%s): creating a new one", error)
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        key, cert = create_ca(self.name)
        _write(path_key, _key_pem(key), private=True)
        _write(path_cert, cert.public_bytes(serialization.Encoding.PEM))
        self._path(SERVER_FILE).unlink(missing_ok=True)  # signed by the previous CA
        logging.info("Created this PC's own CA to encrypt connections between PCs")
        return key, cert

    # --- Server -------------------------------------------------------------

    def _valid_server(self, ca_cert, dns: str, ips: list[str]) -> bool:
        path, path_key = self._path(SERVER_FILE), self._path(SERVER_KEY_FILE)
        if not path.exists() or not path_key.exists():
            return False
        try:
            cert = x509.load_pem_x509_certificate(path.read_bytes())
            key = serialization.load_pem_private_key(path_key.read_bytes(), password=None)
            ca_cert.public_key().verify(cert.signature, cert.tbs_certificate_bytes,
                                        ec.ECDSA(cert.signature_hash_algorithm))
        except Exception:
            return False
        if key.public_key().public_numbers() != cert.public_key().public_numbers():
            return False
        if cert.not_valid_after_utc - _now() < RENEW_BEFORE:
            return False
        return _san(cert) == ({dns}, set(ips))

    def _ensure(self) -> bool:
        """Creates whatever is missing and redoes the server certificate if something changed. True if it did."""
        key_ca, ca_cert = self._load_or_create_ca()
        dns = dns_name(self.name)
        ips = allowed_ips(self._ips())
        redone = False
        if not self._valid_server(ca_cert, dns, ips):
            key, cert = create_server(key_ca, ca_cert, dns, ips, self.server_days)
            _write(self._path(SERVER_KEY_FILE), _key_pem(key), private=True)
            # The full chain: the other PC trusts the CA and validates the leaf against it.
            _write(self._path(SERVER_FILE), cert.public_bytes(serialization.Encoding.PEM)
                      + ca_cert.public_bytes(serialization.Encoding.PEM))
            logging.info("Server certificate renewed for %s and %s", dns, ", ".join(ips))
            redone = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self._path(SERVER_FILE), self._path(SERVER_KEY_FILE))
        pem = ca_cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
        self._ca_pem, self._fingerprint = pem, pem_fingerprint(pem)
        self._context = context  # swapping the reference is atomic: new connections use this one
        return redone

    def ensure(self) -> bool:
        with self._lock:
            return self._ensure()

    def update(self, name: Optional[str] = None, in_background: bool = True):
        """When the name or the network changes: redoes the server certificate if needed, without blocking
        anyone."""
        if name:
            self.name = name

        def task():
            try:
                self.ensure()
            except Exception:
                logging.exception("Couldn't renew the server certificate")

        if not in_background:
            return task()
        thread = threading.Thread(target=task, name="certificados", daemon=True)
        thread.start()
        return thread

    def server_context(self) -> Optional[ssl.SSLContext]:
        return self._context

    def ca_pem(self) -> str:
        return self._ca_pem

    def ca_fingerprint(self) -> str:
        return self._fingerprint
