"""Encrypted pairing between two PCs (protocol 2).

The 6-digit code both people see never travels over the network: each PC computes it from the key
exchange, like Bluetooth's «numeric comparison». If someone gets in the middle, each PC ends up with a
different code and the people see it before accepting.

1. inicio   A → B  {nombre, id, commitment = SHA-256(A's X25519 public key)}
            B → A  {sesion, id, nombre, B's X25519 public key}
2. revelar  A → B  {sesion, A's public key, AES-GCM(A's CA)}
            B checks the commitment, derives the keys, decrypts A's CA (proof that A holds the
            key), shows the code and, if the person accepts:
            B → A  AES-GCM({token, B's CA})
3. confirmar A → B {sesion, confirmation HMAC}: only after the person at A also accepted.
            Only then does B store A's token and CA; A stores B's when it gets the answer.

Why there's no brute force: nothing secret comes from the code (it isn't used as a password), so whoever
captures the exchange has nothing to try offline. A man in the middle has to commit to a key with A
(through the commitment) before knowing A's, and with B before knowing B's: both codes come out at
random and match once in a million, with both people looking at the screen.
"""
import base64
import hashlib
import hmac
import http.client
import json
import logging
import re
import secrets
import ssl
import threading
import time
import unicodedata
from typing import Callable, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import certificates
from i18n import t
from strings_es import ES
from wire_keys import from_wire, headers_to_wire, route_to_old, to_wire

VERSION = 2
STARTUP_PATH = "/p2p/pair/v2/start"
REVEAL_PATH = "/p2p/pair/v2/reveal"
CONFIRM_PATH = "/p2p/pair/v2/confirm"
OLD_PATH = "/p2p/pair"
_CONTEXT = b"copyover-pc-pair-v2"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class PairingError(Exception):
    pass


class OldPc(PairingError):
    """The other PC doesn't know protocol 2 (older CopyOver) or has no TLS."""


class ExistingLink(PermissionError):
    """This PC already has an encrypted link with the one asking to pair and the request doesn't prove it."""


NAME_LENGTH = 40


def visible_name(text: object, backup: str = "PC") -> str:
    """Another PC's name as shown in a confirmation window: one line, no control or formatting characters
    (line breaks, text direction changes, zero-width spaces) and short, so it can't fake other text in the
    window."""
    if not isinstance(text, str):
        return backup
    clean = "".join(c if unicodedata.category(c)[0] not in "CZ" else " " for c in text)
    clean = " ".join(clean.split())
    if len(clean) > NAME_LENGTH:
        clean = clean[:NAME_LENGTH - 1].rstrip() + "…"
    return clean or backup


# What the other PC may answer when it rejects. Any other text from it goes to the log and not to the screen,
# so it can't pass off its own message as a CopyOver one.
OTHER_PC_REASONS = (
    "Rejected",
    "Pairing rejected",
    "Another link request is pending. Try again in a few seconds.",
    "The other PC already has an encrypted link with this one. To link them again, first remove the link on the other PC.",
    "The link request expired. Try again.",
    "The identity doesn’t match the pairing",
)


def other_pc_reason(text: object, backup: str) -> str:
    """The rejection reason the other PC sent, only if it's one of CopyOver's (in either language)."""
    text = text.strip() if isinstance(text, str) else ""
    for reason in OTHER_PC_REASONS:
        if text in (reason, ES.get(reason)):
            return t(reason)
    if text:
        logging.info("The other PC rejected the link: %r", text[:200])
    return backup


def _public(private: X25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _from_b64(text: object, length: Optional[int] = None) -> bytes:
    if not isinstance(text, str):
        raise ValueError("invalid base64")
    data = base64.b64decode(text, validate=True)
    if length is not None and len(data) != length:
        raise ValueError("invalid length")
    return data


def transcription(session: str, id_a: str, id_b: str, public_a: bytes, public_b: bytes) -> bytes:
    """Digest of everything that identifies this exchange; used in the derivation and as AAD."""
    h = hashlib.sha256(_CONTEXT)
    for part in (session.encode(), id_a.encode(), id_b.encode(), public_a, public_b):
        h.update(len(part).to_bytes(4, "big"))
        h.update(part)
    return h.digest()


def derive(private: X25519PrivateKey, other_public: bytes, summary: bytes) -> dict:
    shared = private.exchange(X25519PublicKey.from_public_bytes(other_public))
    material = HKDF(algorithm=hashes.SHA256(), length=96, salt=summary, info=_CONTEXT + b" claves").derive(shared)
    keys = {"a_b": material[:32], "b_a": material[32:64], "confirm": material[64:]}
    number = int.from_bytes(hmac.new(keys["confirm"], b"codigo" + summary, hashlib.sha256).digest()[:4], "big")
    keys["code"] = f"{number % 1_000_000:06d}"
    return keys


def _confirmation_test(keys: dict, summary: bytes) -> str:
    return hmac.new(keys["confirm"], b"A acepta" + summary, hashlib.sha256).hexdigest()


def _encrypt(key: bytes, data: dict, summary: bytes) -> dict:
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(key).encrypt(nonce, json.dumps(data).encode("utf-8"), summary)
    return {"nonce": _b64(nonce), "encrypted": _b64(encrypted)}


def _decrypt(key: bytes, data: dict, summary: bytes) -> dict:
    nonce = _from_b64(data.get("nonce"), 12)
    content = json.loads(AESGCM(key).decrypt(nonce, _from_b64(data.get("encrypted")), summary))
    if not isinstance(content, dict):
        raise ValueError("invalid content")
    return content


# --- Side that receives the request (B) -------------------------------------


class PairingReceiver:
    """Protocol 2 sessions on the PC that receives the request.

    An 'offered' session (nobody revealed their key yet) costs nothing and expires by itself within seconds,
    so it doesn't block anyone. One that is 'deciding' (a person is looking at the code) does hold the others
    until the person answers or the window expires. Requests are limited per source IP, with a higher overall
    cap so nobody can flood the server."""

    OFFERED_TTL = 15.0
    DECIDING_TTL = 75.0   # the confirmation window lasts 60 s
    ACCEPTED_TTL = 75.0     # the person at the other PC also has 60 s to confirm
    MAX_PER_MINUTE = 5      # per source IP
    MAX_TOTAL_PER_MINUTE = 30
    MAX_OFFERED = 16

    def __init__(self, confirm: Callable[..., bool], save: Callable[[str, str, str], None],
                 clock: Callable[[], float] = time.monotonic,
                 link: Optional[Callable[[str], Optional[tuple[bool, str]]]] = None):
        """`vinculo(id)`: if this PC is already linked with that one, (encrypted, name stored here); otherwise
        None."""
        self.confirm = confirm
        self.save = save
        self.clock = clock
        self.link = link or (lambda _id: None)
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}
        self._recent: list[tuple[float, str]] = []

    def _clean(self, now: float):
        self._sessions = {k: v for k, v in self._sessions.items() if v["expires"] > now}
        self._recent = [(at, ip) for at, ip in self._recent if now - at < 60]

    def _replaces(self, id_a: str, authenticated: bool) -> Optional[str]:
        """Checks whether this pairing would overwrite an existing link. An encrypted link can only be
        replaced by the same PC (over TLS and with its current token); an unencrypted one can be replaced,
        telling the person. Returns the name stored here for the link being replaced, or None if there's
        none."""
        existing = self.link(id_a)
        if not existing:
            return None
        encrypted, local_name = existing
        if encrypted and not authenticated:
            raise ExistingLink(t("The other PC already has an encrypted link with this one. To link them again, "
                                     "first remove the link on the other PC."))
        return visible_name(local_name, f"PC {id_a[:8]}")

    def startup(self, data: object, my_id: str, my_name: str, client_ip: str, authenticated_as: str = "") -> dict:
        """`authenticated_as`: the id of the linked PC whose token arrived over TLS in this request, if any."""
        if not isinstance(data, dict):
            raise ValueError("Invalid request")
        commitment = data.get("commitment")
        id_a = data.get("id")
        name = data.get("name")
        if not isinstance(commitment, str) or not _HEX64.match(commitment):
            raise ValueError("Invalid commitment")
        if not isinstance(id_a, str) or not 0 < len(id_a) <= 128 or not isinstance(name, str):
            raise ValueError("Invalid identity")
        name = visible_name(name)
        authenticated = bool(authenticated_as) and authenticated_as == id_a
        self._replaces(id_a, authenticated)
        now = self.clock()
        private = X25519PrivateKey.generate()
        with self._lock:
            self._clean(now)
            deciding = any(s["status"] == "deciding" for s in self._sessions.values())
            offered = sum(s["status"] == "offered" for s in self._sessions.values())
            from_this_ip = sum(ip == client_ip for _, ip in self._recent)
            if (deciding or offered >= self.MAX_OFFERED or from_this_ip >= self.MAX_PER_MINUTE
                    or len(self._recent) >= self.MAX_TOTAL_PER_MINUTE):
                raise PermissionError(t("Another link request is pending. Try again in a few seconds."))
            session = secrets.token_urlsafe(24)
            self._sessions[session] = {"status": "offered", "expires": now + self.OFFERED_TTL, "private": private,
                                      "commitment": commitment, "id_a": id_a, "name_a": name, "id_b": my_id,
                                      "authenticated": authenticated}
            self._recent.append((now, client_ip))
        return {"v": VERSION, "session": session, "id": my_id, "name": my_name, "public_key": _b64(_public(private))}

    def _take(self, session: object, state: str) -> dict:
        registry = self._sessions.get(session) if isinstance(session, str) else None
        if not registry or registry["expires"] <= self.clock() or registry["status"] != state:
            raise LookupError("Invalid or expired request")
        return registry

    def reveal(self, data: object, my_ca_pem: str) -> dict:
        """Blocks while the person at this PC decides. Returns the token and our own CA, encrypted."""
        if not isinstance(data, dict):
            raise ValueError("Invalid request")
        with self._lock:
            registry = self._take(data.get("session"), "offered")
            if any(s["status"] == "deciding" for s in self._sessions.values()):
                registry["status"] = "rechazada"
                raise PermissionError(t("Another link request is pending. Try again in a few seconds."))
            # One attempt per session, and one person deciding at a time.
            registry.update(status="deciding", expires=self.clock() + self.DECIDING_TTL)
            private = registry.pop("private")
        try:
            replaces = self._replaces(registry["id_a"], registry["authenticated"])
        except ExistingLink:
            with self._lock:
                registry["status"] = "rechazada"
            raise
        try:
            public_a = _from_b64(data.get("public_key"), 32)
            if not hmac.compare_digest(hashlib.sha256(public_a).hexdigest(), registry["commitment"]):
                raise ValueError(t("The commitment doesn't match"))
            summary = transcription(data["session"], registry["id_a"], registry["id_b"], public_a, _public(private))
            keys = derive(private, public_a, summary)
            ca_a = certificates.validate_ca(_decrypt(keys["a_b"], data, summary).get("ca"))
        except Exception as error:
            with self._lock:
                registry["status"] = "rechazada"
            raise ValueError("Invalid reveal") from error
        try:
            if replaces:
                accepted = bool(self.confirm(registry["name_a"], keys["code"], replaces=replaces))
            else:
                accepted = bool(self.confirm(registry["name_a"], keys["code"]))
        except Exception:
            accepted = False
        with self._lock:
            if not accepted or registry["expires"] <= self.clock():
                registry["status"] = "rechazada"
                raise PermissionError(t("Pairing rejected"))
            token = secrets.token_urlsafe(24)
            registry.update(status="aceptada", expires=self.clock() + self.ACCEPTED_TTL, token=token, ca_a=ca_a,
                            keys=keys, summary=summary)
        return {"v": VERSION, **_encrypt(keys["b_a"], {"token": token, "ca": my_ca_pem}, summary)}

    def confirm_session(self, data: object) -> dict:
        if not isinstance(data, dict):
            raise ValueError("Invalid request")
        with self._lock:
            registry = self._take(data.get("session"), "aceptada")
            expected = _confirmation_test(registry["keys"], registry["summary"])
            test = data.get("proof")
            if not isinstance(test, str) or not hmac.compare_digest(test, expected):
                registry["status"] = "rechazada"
                raise PermissionError(t("Pairing rejected"))
            registry["status"] = "guardada"
            id_a, token, ca_a = registry["id_a"], registry["token"], registry["ca_a"]
        self.save(id_a, token, ca_a)
        return {"v": VERSION, "ok": True}


# --- Side that asks to pair (A) ---------------------------------------------


def _connection(ip: str, port: int, timeout: float, ca_pem: Optional[str] = None) -> http.client.HTTPConnection:
    # On a new link there's no CA from the other PC to trust yet: this exchange is protected with its own
    # encryption. When relinking a PC already linked with encryption it goes over TLS with its CA.
    if ca_pem:
        return http.client.HTTPSConnection(ip, port, timeout=timeout, context=certificates.client_context(ca_pem))
    return http.client.HTTPConnection(ip, port, timeout=timeout)


def _post(ip: str, port: int, path: str, data: dict, timeout: float,
          ca_pem: Optional[str] = None, key: str = "") -> tuple[int, str]:
    body = json.dumps(to_wire(data)).encode("utf-8")
    conn = _connection(ip, port, timeout, ca_pem)
    headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
    if ca_pem and key:
        headers["X-Key"] = key
    try:
        conn.request("POST", route_to_old(path), body=body, headers=headers_to_wire(headers))
        resp = conn.getresponse()
        return resp.status, resp.read(65536).decode("utf-8", errors="replace")
    finally:
        conn.close()


def _json_v2(text: str) -> Optional[dict]:
    try:
        data = from_wire(json.loads(text))
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("v") == VERSION else None


class OutgoingPairing:
    """A pairing this PC asked for: preparar() gives the code, wait_response() waits for the other person
    and confirmar() closes it once the person at this PC also accepted."""

    def __init__(self, ip: str, port: int, my_name: str, my_id: str, my_ca_pem: str = "",
                 expected_id: str = "", allow_unencrypted: bool = True, response_timeout: float = 125.0,
                 announces_v2: bool = False, current_ca: Optional[str] = None, current_token: str = ""):
        """`announces_v2`: the other PC announces on the network that it pairs with protocol 2; if it then
        answers like an older PC, someone got in the middle and we don't fall back to protocol 1.
        `current_ca` and `current_token`: the encrypted link we already have with that PC, to replace it
        (goes over TLS and presents the token)."""
        self.ip, self.port = ip, int(port)
        self.my_name, self.my_id, self.my_ca_pem = my_name, my_id, my_ca_pem
        self.expected_id = expected_id
        self._current_ca = current_ca if current_ca and current_token else None
        self._current_token = current_token if self._current_ca else ""
        self.announces_v2 = announces_v2
        self.allow_unencrypted = allow_unencrypted and not announces_v2 and not self._current_ca
        self.response_timeout = response_timeout
        self.encrypted = False
        self.code = ""
        self.token = ""
        self.remote_ca: Optional[str] = None
        self.remote_id = ""
        self._session = ""
        self._keys: dict = {}
        self._summary = b""
        self._private: Optional[X25519PrivateKey] = None

    def _post(self, path: str, data: dict, timeout: float) -> tuple[int, str]:
        if self._current_ca:
            return _post(self.ip, self.port, path, data, timeout, ca_pem=self._current_ca, key=self._current_token)
        return _post(self.ip, self.port, path, data, timeout)

    def prepare(self) -> str:
        if self.my_ca_pem:
            try:
                return self._prepare_v2()
            except OldPc:
                if self.announces_v2:
                    raise PairingError(t("The other PC announces encrypted linking, but answered like an "
                                                "older version. Someone on the network may be interfering: they were "
                                                "not linked. Try on another network."))
                if not self.allow_unencrypted:
                    raise PairingError(t("The other PC has an older version of CopyOver: update it to link them with encryption."))
        elif not self.allow_unencrypted:
            raise PairingError(t("This PC could not prepare its certificate; check the log."))
        # Protocol 1: this PC picks the code and it travels as is. Only for PCs with an older CopyOver.
        self.encrypted = False
        self.code = f"{secrets.randbelow(1_000_000):06d}"
        return self.code

    def _prepare_v2(self) -> str:
        self._private = X25519PrivateKey.generate()
        public = _public(self._private)
        try:
            state, text = self._post(STARTUP_PATH, {
                "v": VERSION, "name": self.my_name, "id": self.my_id,
                "commitment": hashlib.sha256(public).hexdigest()}, timeout=10)
        except ssl.SSLError as error:
            raise PairingError(t("The other PC is not the one that was linked: its certificate doesn’t match.")) from error
        except OSError as error:
            raise PairingError(t("Connection error: {error}", error=error)) from error
        data = _json_v2(text)
        if data is None:
            if state in (403, 404, 503):
                raise OldPc(other_pc_reason(text, t("Rejected")))
            raise PairingError(other_pc_reason(text, t("Rejected")))
        if state != 200:
            raise PairingError(other_pc_reason(data.get("error"), t("Rejected")))
        try:
            self._session = str(data["session"])
            self.remote_id = str(data["id"])
            public_b = _from_b64(data["public_key"], 32)
        except (KeyError, ValueError) as error:
            raise PairingError(t("Invalid answer from the other PC")) from error
        if self.expected_id and self.remote_id != self.expected_id:
            raise PairingError(t("The PC that answered is not the one you chose. Search for it again."))
        self._summary = transcription(self._session, self.my_id, self.remote_id, public, public_b)
        self._keys = derive(self._private, public_b, self._summary)
        self.encrypted = True
        self.code = self._keys["code"]
        return self.code

    def wait_response(self) -> str:
        """Waits for the other person to accept. Returns the token (not yet stored on the other PC)."""
        if not self.encrypted:
            return self._wait_v1()
        message = {"v": VERSION, "session": self._session, "public_key": _b64(_public(self._private)),
                   **_encrypt(self._keys["a_b"], {"ca": self.my_ca_pem}, self._summary)}
        try:
            state, text = self._post(REVEAL_PATH, message, timeout=self.response_timeout)
        except OSError as error:
            raise PairingError(t("Connection error: {error}", error=error)) from error
        data = _json_v2(text)
        if state != 200 or data is None:
            raise PairingError(other_pc_reason((data or {}).get("error"), t("Pairing rejected")))
        try:
            content = _decrypt(self._keys["b_a"], data, self._summary)
            token = content["token"]
            if not isinstance(token, str) or not 16 <= len(token) <= 128:
                raise ValueError("invalid token")
            self.remote_ca = certificates.validate_ca(content.get("ca"))
        except Exception as error:
            raise PairingError(t("The other PC’s answer could not be verified.")) from error
        self.token = token
        return token

    def _wait_v1(self) -> str:
        data = {"name": self.my_name, "id": self.my_id, "code": self.code}
        try:
            state, text = _post(self.ip, self.port, OLD_PATH, data, timeout=65)
        except OSError as error:
            raise PairingError(t("Connection error: {error}", error=error)) from error
        if state != 200:
            raise PairingError(other_pc_reason(text, t("Rejected")))
        try:
            token = str(json.loads(text).get("token", ""))
        except (ValueError, AttributeError) as error:
            raise PairingError(t("Invalid answer from the other PC")) from error
        if not token:
            raise PairingError(t("Rejected"))
        self.token = token
        return token

    def confirm(self) -> None:
        """The person at this PC accepted: only now does the other PC store the link."""
        if not self.encrypted:
            return
        test = _confirmation_test(self._keys, self._summary)
        try:
            state, text = self._post(CONFIRM_PATH, {"v": VERSION, "session": self._session, "proof": test},
                                       timeout=10)
        except OSError as error:
            raise PairingError(t("Connection error: {error}", error=error)) from error
        if state != 200 or _json_v2(text) is None:
            raise PairingError(other_pc_reason(text, t("Pairing rejected")))
