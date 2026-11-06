"""Linking over the local network, approved on the desktop and with an encrypted key."""
import base64
import hashlib
import json
import logging
import re
import secrets
import threading
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from i18n import t
from linking import qr_content


class PairingManager:
    TTL = 120

    def __init__(self, cfg, approve, clock=time.monotonic, issue_key=None, ca_pem=None):
        self.cfg = dict(cfg)
        self.approve = approve
        self.clock = clock
        # Takes the device name and returns its own key; without it the shared key is used.
        self.issue_key = issue_key
        # Returns this PC's CA in PEM ("" without TLS). Looked up when accepting, because the certificates are
        # prepared after the manager is created.
        self.ca_pem = ca_pem
        self.lock = threading.Lock()
        self.sessions = {}
        self.recent = []
        self.dialog_active = False
        self.active_session = None

    def request(self, name, commitment, client_ip, server_ip=None):
        if not isinstance(commitment, str) or not re.fullmatch(r"[a-f0-9]{64}", commitment):
            raise ValueError(t("Invalid commitment"))
        private = X25519PrivateKey.generate()
        public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        name = "".join(c for c in str(name) if c.isalnum() or c in " ._()-")[:60] or "Android"
        now = self.clock()
        with self.lock:
            self._clean(now)
            limited = len(self.recent) >= 5 or any(ip == client_ip and now - at < 10 for at, ip in self.recent)
            retry = self._retry_offered(commitment, client_ip, server_ip or client_ip, not limited)
            if retry is not None:
                return retry
            if self.active_session or limited:
                raise ValueError(t("Another link request is pending. Try again in a few seconds."))
            session = secrets.token_urlsafe(24)
            ticket = {"session": session, "server_public": base64.b64encode(public).decode(),
                      "id": self.cfg["id"], "name": self.cfg.get("name", "puente-pc")}
            self.sessions[session] = {"status": "offered", "expires": now + self.TTL, "event": threading.Event(),
                                      "private": private, "commitment": commitment, "name": name,
                                      "server_ip": server_ip or client_ip, "client_ip": client_ip,
                                      "ticket": ticket}
            self.recent.append((now, client_ip))
            self.active_session = session
        return dict(ticket)

    def _retry_offered(self, commitment, client_ip, server_ip, can_restart):
        """Only the same peer can retry a bootstrap that has not shown a code yet.

        Called under self.lock. Pending desktop confirmations are never replaced.
        """
        record = self.sessions.get(self.active_session)
        if not record or record['status'] != 'offered':
            return None
        if record.get('client_ip') != client_ip or record['server_ip'] != server_ip:
            return None
        if secrets.compare_digest(record['commitment'], commitment):
            return dict(record['ticket'])
        if can_restart:
            self.sessions.pop(self.active_session)
            self.active_session = None
        return None

    def reveal(self, session, public_key):
        with self.lock:
            record = self.sessions.get(session)
            if not record or self.clock() >= record["expires"] or record["status"] != "offered":
                raise ValueError(t("Invalid or expired request"))
            try:
                public = base64.b64decode(public_key, validate=True)
                if not secrets.compare_digest(hashlib.sha256(public).hexdigest(), record["commitment"]):
                    raise ValueError(t("The commitment doesn't match"))
                peer = X25519PublicKey.from_public_bytes(public)
                shared = record.pop("private").exchange(peer)
                key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"puente-pair-v1").derive(shared)
            except Exception as error:
                record["status"] = "rejected"
                record.pop("private", None)
                record["event"].set()
                self.active_session = None
                raise ValueError(t("Invalid reveal")) from error
            record["status"] = "pending"
            self.dialog_active = True
            code = f"{int.from_bytes(hashlib.sha256(key + session.encode()).digest()[:4], 'big') % 1000000:06d}"
            args = (session, record["name"], code, key, record["server_ip"])
        threading.Thread(target=self._decides, args=args, daemon=True).start()
        return {"status": "pending"}

    def poll(self, session):
        with self.lock:
            record = self.sessions.get(session)
            if not record or self.clock() >= record["expires"]:
                return {"status": "expired"}
            return {k: v for k, v in record.items() if k in ("status", "nonce", "ciphertext")}

    def wait_for_decision(self, session, timeout):
        with self.lock:
            event = self.sessions[session]["event"]
        return event.wait(timeout)

    def _clean(self, now):
        self.sessions = {k: v for k, v in self.sessions.items() if v["expires"] > now}
        if self.active_session not in self.sessions and not self.dialog_active:
            self.active_session = None
        self.recent = [(at, ip) for at, ip in self.recent if now - at < 60]

    def _decides(self, session, name, code, key, server_ip):
        try:
            accepted = self.approve(name, code)
            nonce = secrets.token_bytes(12)
            # The device key is only issued if the desktop accepted: no orphaned registrations.
            issued_key = self.issue_key(name) if accepted and self.issue_key else None
            ca = (self.ca_pem() or None) if accepted and self.ca_pem else None
            encrypted = AESGCM(key).encrypt(nonce, qr_content(self.cfg, server_ip, issued_key, ca).encode(), session.encode()) if accepted else None
            with self.lock:
                record = self.sessions.get(session)
                if record and self.clock() < record["expires"]:
                    record["status"] = "approved" if accepted else "rejected"
                    if accepted:
                        record.update(nonce=base64.b64encode(nonce).decode(), ciphertext=base64.b64encode(encrypted).decode())
        except Exception:
            logging.exception("Couldn't show the link request")
            with self.lock:
                if session in self.sessions:
                    self.sessions[session]["status"] = "rejected"
        finally:
            with self.lock:
                if self.active_session == session:
                    self.dialog_active = False
                    self.active_session = None
                record = self.sessions.get(session)
                if record:
                    record["event"].set()


def confirm_in_desktop(name, code):
    """Local dialog; no HTTP request can accept the link."""
    import sys
    import subprocess

    if sys.platform == "win32":
        # Same window as when linking two PCs; it closes by itself after 105 s.
        import dialogs
        return dialogs.confirm_pairing(name, code, timeout=105, incoming=True)
    text = t("{name} wants to link with this PC.\n\nCode: {code}\n\nAccept only if you see the same code on the phone.",
             name=name, code=code)
    import shutil

    if shutil.which("zenity"):
        command = ["zenity", "--question", "--title=CopyOver", f"--text={text}", "--timeout=105", "--default-cancel", "--no-markup",
                   f"--ok-label={t('The codes match')}", f"--cancel-label={t('Decline')}"]
    elif shutil.which("kdialog"):
        command = ["kdialog", "--title", "CopyOver", "--yesno", text,
                   "--yes-label", t("The codes match"), "--no-label", t("Decline")]
    else:
        logging.error("To accept Android connections, install zenity or kdialog")
        return False
    try:
        return subprocess.run(command, timeout=110, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
