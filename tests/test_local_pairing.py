import base64
import json
import hashlib
import threading
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class LocalPairingTests(unittest.TestCase):
    cfg = {"id": "a" * 32, "key": "keep-ios-key", "port": 8765, "name": "mi-pc"}

    def request(self, manager, ip="127.0.0.1"):
        private = X25519PrivateKey.generate()
        public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        ticket = manager.request("Mi teléfono", hashlib.sha256(public).hexdigest(), ip)
        manager.reveal(ticket["session"], base64.b64encode(public).decode())
        return private, ticket

    def test_key_is_delivered_encrypted_only_after_local_approval(self):
        from pairing import PairingManager
        entered, release = threading.Event(), threading.Event()

        def approve(name, code):
            self.assertEqual(name, "Mi teléfono")
            self.assertRegex(code, r"^\d{6}$")
            entered.set()
            release.wait(3)
            return True

        manager = PairingManager(self.cfg, approve)
        private, ticket = self.request(manager)
        self.assertTrue(entered.wait(2))
        self.assertEqual(manager.poll(ticket["session"])["status"], "pending")
        self.assertNotIn("keep-ios-key", json.dumps(ticket))
        release.set()
        self.assertTrue(manager.wait_for_decision(ticket["session"], 3))
        response = manager.poll(ticket["session"])
        self.assertEqual(response["status"], "approved")
        self.assertNotIn("keep-ios-key", json.dumps(response))
        public = X25519PublicKey.from_public_bytes(base64.b64decode(ticket["server_public"]))
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"puente-pair-v1").derive(private.exchange(public))
        plaintext = AESGCM(key).decrypt(base64.b64decode(response["nonce"]), base64.b64decode(response["ciphertext"]), ticket["session"].encode())
        self.assertEqual(json.loads(plaintext)["key"], "keep-ios-key")

    def test_rejection_and_expiry_do_not_release_credentials(self):
        from pairing import PairingManager
        now = [10.0]
        manager = PairingManager(self.cfg, lambda *_: False, clock=lambda: now[0])
        _, ticket = self.request(manager)
        self.assertTrue(manager.wait_for_decision(ticket["session"], 3))
        self.assertEqual(manager.poll(ticket["session"])["status"], "rejected")
        now[0] += 121
        self.assertEqual(manager.poll(ticket["session"])["status"], "expired")

    def test_one_dialog_at_a_time_and_rate_limit(self):
        from pairing import PairingManager
        release = threading.Event()
        manager = PairingManager(self.cfg, lambda *_: release.wait(3))
        _, ticket = self.request(manager)
        try:
            with self.assertRaises(ValueError):
                self.request(manager, "127.0.0.2")
        finally:
            release.set()
            manager.wait_for_decision(ticket["session"], 3)
        with self.assertRaises(ValueError):
            self.request(manager)

    def test_invalid_public_key_is_rejected(self):
        from pairing import PairingManager
        manager = PairingManager(self.cfg, lambda *_: self.fail("No debe abrir diálogo"))
        with self.assertRaises(ValueError):
            manager.request("name", "invalid-commitment", "127.0.0.1")
        ticket = manager.request("name", hashlib.sha256(bytes(32)).hexdigest(), "127.0.0.1")
        with self.assertRaises(ValueError):
            manager.reveal(ticket["session"], base64.b64encode(bytes(32)).decode())

    def test_commitment_is_fixed_before_public_key_is_revealed(self):
        from pairing import PairingManager
        approve = Mock(return_value=True)
        manager = PairingManager(self.cfg, approve)
        private = X25519PrivateKey.generate()
        public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        ticket = manager.request("name", hashlib.sha256(public).hexdigest(), "127.0.0.1")
        approve.assert_not_called()
        self.assertEqual(manager.poll(ticket["session"])["status"], "offered")
        manager.reveal(ticket["session"], base64.b64encode(public).decode())
        manager.wait_for_decision(ticket["session"], 3)
        with self.assertRaises(ValueError):
            manager.reveal(ticket["session"], base64.b64encode(public).decode())
        approve.assert_called_once()

    def test_changed_commitment_never_opens_approval(self):
        from pairing import PairingManager
        approve = Mock(return_value=True)
        manager = PairingManager(self.cfg, approve)
        ticket = manager.request("name", "a" * 64, "127.0.0.1")
        with self.assertRaises(ValueError):
            manager.reveal(ticket["session"], base64.b64encode(b"b" * 32).decode())
        approve.assert_not_called()

    @unittest.skipUnless(sys.platform == 'win32', 'Solo Windows')
    def test_windows_approval_is_bounded_and_does_not_open_a_console(self):
        from pairing import confirm_in_desktop
        import bundle
        response = Mock(stdout="accept\n", returncode=0)
        with patch("sys.platform", "win32"), patch("subprocess.run", return_value=response) as run:
            self.assertTrue(confirm_in_desktop("phone", "123456"))
        self.assertLessEqual(run.call_args.kwargs["timeout"], 115)
        # Same window as when linking two PCs, launched with the console-less interpreter.
        command = run.call_args.args[0]
        self.assertEqual(command[0], bundle.gui_executable())
        self.assertIn("pair", command)
        self.assertTrue(json.loads(command[-2])["incoming"])

    def test_unrevealed_session_expires_without_blocking_next_attempt(self):
        from pairing import PairingManager
        now = [1.0]
        manager = PairingManager(self.cfg, Mock(return_value=False), clock=lambda: now[0])
        first = manager.request("name", "a" * 64, "127.0.0.1")
        now[0] += 121
        second = manager.request("name", "b" * 64, "127.0.0.1")
        self.assertNotEqual(first["session"], second["session"])
        self.assertEqual(manager.poll(first["session"])["status"], "expired")

    @unittest.skipUnless(sys.platform == 'win32', 'Solo Windows')
    def test_windows_popup_timeout_rejects_the_request(self):
        from pairing import confirm_in_desktop
        with patch("sys.platform", "win32"), patch("subprocess.run", side_effect=subprocess.TimeoutExpired("powershell", 110)):
            self.assertFalse(confirm_in_desktop("phone", "123456"))
