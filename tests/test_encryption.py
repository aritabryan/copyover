"""Encryption between PCs: own certificates, TLS and HTTP on the same port, protected pairing and sending with
the other PC's CA pinned."""
import datetime
import hashlib
import http.client
import ipaddress
import json
import logging
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

import certificates
import pc_pairing
import linked_pcs
import p2p
import bridge

ROOT = Path(__file__).resolve().parents[1]


def _cert(folder, file=certificates.SERVER_FILE):
    return x509.load_pem_x509_certificate((Path(folder) / file).read_bytes())


class CertificatesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "certificados"
        self.ips = ["192.168.10.20", "8.8.8.8"]
        self.certs = certificates.Certificates(self.folder, "Oficina Ñ", ips=lambda: list(self.ips))

    def tearDown(self):
        self.tmp.cleanup()

    def test_creates_restricted_ca_and_valid_server_certificate(self):
        self.assertTrue(self.certs.ensure())
        ca = _cert(self.folder, certificates.CA_FILE)
        basic = ca.extensions.get_extension_for_class(x509.BasicConstraints)
        self.assertTrue(basic.critical)
        self.assertTrue(basic.value.ca)
        self.assertEqual(basic.value.path_length, 0)
        self.assertTrue(ca.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign)
        limits = ca.extensions.get_extension_for_class(x509.NameConstraints)
        self.assertTrue(limits.critical)
        self.assertIn(x509.DNSName("local"), limits.value.permitted_subtrees)
        self.assertGreater(ca.not_valid_after_utc - ca.not_valid_before_utc, datetime.timedelta(days=3000))

        sheet = _cert(self.folder)
        san = sheet.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertEqual(san.get_values_for_type(x509.DNSName), ["oficina-n.local"])
        # The public IP isn't included: the CA can't sign it.
        self.assertEqual({str(i) for i in san.get_values_for_type(x509.IPAddress)}, {"192.168.10.20", "127.0.0.1"})
        self.assertIn(ExtendedKeyUsageOID.SERVER_AUTH, sheet.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value)
        sheet.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
        aki = sheet.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value
        ski_ca = ca.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
        self.assertEqual(aki.key_identifier, ski_ca.digest)
        self.assertLessEqual(sheet.not_valid_after_utc - sheet.not_valid_before_utc, datetime.timedelta(days=825))
        self.assertEqual(self.certs.ca_fingerprint(), certificates.pem_fingerprint(self.certs.ca_pem()))
        self.assertEqual(len(self.certs.ca_fingerprint()), 64)
        self.assertIsInstance(self.certs.server_context(), ssl.SSLContext)

    def test_ensure_keeps_certificates_when_nothing_changed(self):
        self.certs.ensure()
        before = (self.folder / certificates.SERVER_FILE).read_bytes()
        other = certificates.Certificates(self.folder, "Oficina Ñ", ips=lambda: list(self.ips))
        self.assertFalse(other.ensure())
        self.assertEqual((self.folder / certificates.SERVER_FILE).read_bytes(), before)
        self.assertEqual(other.ca_fingerprint(), self.certs.ca_fingerprint())

    def test_on_change_name_or_ip_redoes_only_server_and_context_reload(self):
        self.certs.ensure()
        fingerprint, context = self.certs.ca_fingerprint(), self.certs.server_context()
        self.ips = ["10.1.2.3"]
        thread = self.certs.update("Sala")
        thread.join(10)
        sheet = _cert(self.folder)
        san = sheet.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertEqual(san.get_values_for_type(x509.DNSName), ["sala.local"])
        self.assertEqual({str(i) for i in san.get_values_for_type(x509.IPAddress)}, {"10.1.2.3", "127.0.0.1"})
        self.assertEqual(self.certs.ca_fingerprint(), fingerprint, "la CA no cambia: los vínculos siguen valiendo")
        self.assertIsNot(self.certs.server_context(), context)

    def test_renews_before_expire(self):
        short = certificates.Certificates(self.folder, "pc", ips=lambda: [], server_days=10)
        short.ensure()
        self.assertTrue(certificates.Certificates(self.folder, "pc", ips=lambda: []).ensure())
        sheet = _cert(self.folder)
        self.assertGreater(sheet.not_valid_after_utc - certificates._now(), datetime.timedelta(days=300))

    def test_damaged_ca_replaces_without_break(self):
        self.certs.ensure()
        fingerprint = self.certs.ca_fingerprint()
        (self.folder / certificates.CA_FILE).write_text("basura")
        with self.assertLogs(level=logging.ERROR):
            self.assertTrue(self.certs.ensure())
        self.assertNotEqual(self.certs.ca_fingerprint(), fingerprint)

    @unittest.skipIf(sys.platform == "win32", "on Windows permissions are restricted with icacls")
    def test_private_keys_only_reads_user(self):
        self.certs.ensure()
        for file in (certificates.CA_KEY_FILE, certificates.SERVER_KEY_FILE):
            self.assertEqual(os.stat(self.folder / file).st_mode & 0o077, 0)

    def test_keys_remain_outside_git(self):
        rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("certificados/", rules)
        self.assertIn("*.key", rules)

    def test_validate_ca_rejects_a_certificate_that_is_not_a_ca(self):
        self.certs.ensure()
        self.assertEqual(certificates.validate_ca(self.certs.ca_pem()), self.certs.ca_pem())
        sheet = (self.folder / certificates.SERVER_FILE).read_text().split("-----END CERTIFICATE-----")[0]
        for bad in (sheet + "-----END CERTIFICATE-----\n", "", None, "x" * 9000):
            with self.assertRaises(Exception):
                certificates.validate_ca(bad)

    def test_ca_is_not_valid_for_internet_names(self):
        """NameConstraints: even if someone used the CA's key to sign another name, it wouldn't be valid."""
        self.certs.ensure()
        key_ca = serialization.load_pem_private_key((self.folder / certificates.CA_KEY_FILE).read_bytes(), None)
        ca_cert = _cert(self.folder, certificates.CA_FILE)
        key = ec.generate_private_key(ec.SECP256R1())
        now = certificates._now()
        sheet = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "banco.example")]))
                .issuer_name(ca_cert.subject).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("banco.example"),
                                                            x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()), False)
                .sign(key_ca, hashes.SHA256()))
        folder = Path(self.tmp.name)
        (folder / "falsa.pem").write_bytes(sheet.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(serialization.Encoding.PEM))
        (folder / "falsa.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                                serialization.NoEncryption()))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(folder / "falsa.pem", folder / "falsa.key")
        server = _SimpleTlsServer(context)
        try:
            with self.assertRaises(ssl.SSLCertVerificationError):
                conn = http.client.HTTPSConnection("127.0.0.1", server.port, timeout=5,
                                                   context=certificates.client_context(self.certs.ca_pem()))
                conn.connect()
        finally:
            server.close()


class _SimpleTlsServer:
    def __init__(self, context):
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.context = context
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        try:
            client, _ = self.sock.accept()
            with self.context.wrap_socket(client, server_side=True) as tls:
                tls.recv(1)
        except OSError:
            pass

    def close(self):
        self.sock.close()


class _PC:
    """An in-memory PC: its server (HTTP and TLS on the same port), its certificates and its links."""

    def __init__(self, name, folder, confirm=None, with_tls=True):
        self.name = name
        self.id = f"id-{name}"
        self.folder = Path(folder) / name
        self.received = self.folder / "received_bytes"
        self.saved = {}
        self.questions = []
        self.certs = certificates.Certificates(self.folder / "certificados", name, ips=lambda: []) if with_tls else None
        if self.certs:
            self.certs.ensure()

        def save(peer_id, token, ca=None):
            self.saved[peer_id] = (token, ca)

        def confirm_b(other_name, code, replaces=None):
            self.shown_codes.append(code)
            self.windows.append({"name": other_name, "code": code, "replaces": replaces})
            return confirm(other_name, code) if confirm else True

        self.shown_codes = []
        self.windows = []
        self.saved_names = {}
        self.manager = p2p.PairingManager(confirm_code=confirm_b, on_save=save,
                                               link_name=self.saved_names.get)
        self.transfers = p2p.TransfersManager(destination=self.received,
                                                       ask_user=lambda data: self.questions.append(data) or "accept")
        pc = self

        class Handler(bridge.Handler):
            key = f"own-key-{name}"
            identifier = pc.id
            notices = False
            p2p_manager = pc.transfers
            pairing_manager = pc.manager
            requests = []

            def log_message(self, format, *args):
                pass

            def handle_one_request(self):
                Handler.requests.append(self.by_tls())
                return super().handle_one_request()

        self.handler = Handler
        self.server = bridge.BridgeServer(("127.0.0.1", 0), Handler)
        self.server.certificates = self.certs
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def ca(self):
        return self.certs.ca_pem() if self.certs else ""

    def pair_with(self, other, confirm=lambda code: True, **extra):
        return p2p.pair_with_pc("127.0.0.1", other.port, self.name, self.id, confirm_code=confirm,
                                        my_ca_pem=self.ca(), **extra)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pcs = []
        silence = patch.object(bridge, "register_history")
        silence.start()
        self.addCleanup(silence.stop)

    def pc(self, name, **options):
        new_value = _PC(name, self.tmp.name, **options)
        self.pcs.append(new_value)
        return new_value

    def tearDown(self):
        for pc in self.pcs:
            pc.close()
        self.tmp.cleanup()


class MixedServerTests(_Base):
    def _get(self, connection, path="/ping"):
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_serves_http_and_https_in_same_port(self):
        pc = self.pc("lounge")
        self.assertEqual(self._get(http.client.HTTPConnection("127.0.0.1", pc.port, timeout=5)), (200, b"Puente activo"))
        context = certificates.client_context(pc.ca())
        self.assertEqual(self._get(http.client.HTTPSConnection("127.0.0.1", pc.port, timeout=5, context=context)),
                         (200, b"Puente activo"))
        self.assertEqual(pc.handler.requests, [False, True])

    def test_without_certificates_follows_by_http(self):
        pc = self.pc("vieja", with_tls=False)
        self.assertEqual(self._get(http.client.HTTPConnection("127.0.0.1", pc.port, timeout=5)), (200, b"Puente activo"))

    def test_quiet_client_or_broken_greeting_does_not_hang_the_server(self):
        pc = self.pc("lounge")
        pc.server.FIRST_BYTE_WAIT = 0.5
        pc.server.TLS_GREETING_WAIT = 0.5
        quiet = socket.create_connection(("127.0.0.1", pc.port))
        broken = socket.create_connection(("127.0.0.1", pc.port))
        broken.sendall(b"\x16\x03\x01\x00\x05basura")
        try:
            startup = time.monotonic()
            self.assertEqual(self._get(http.client.HTTPConnection("127.0.0.1", pc.port, timeout=5))[0], 200)
            self.assertLess(time.monotonic() - startup, 2)
            quiet.settimeout(5)
            self.assertEqual(quiet.recv(1), b"", "el servidor cierra al que no manda nada")
            broken.settimeout(5)
            try:
                broken.recv(100)
            except OSError:
                pass
            self.assertEqual(self._get(http.client.HTTPConnection("127.0.0.1", pc.port, timeout=5))[0], 200)
        finally:
            quiet.close()
            broken.close()


class PairingTests(_Base):
    def test_correct_code_saves_token_and_ca_in_two_pcs(self):
        a, b = self.pc("desktop"), self.pc("laptop")
        seen = []
        ok, code, token, ca_b = a.pair_with(b, confirm=lambda c: seen.append(c) or True, expected_id=b.id)
        self.assertTrue(ok, token)
        self.assertEqual(seen, [code])
        self.assertEqual(b.shown_codes, [code], "las dos personas ven el mismo código")
        self.assertEqual(ca_b, b.ca())
        self.assertEqual(b.saved, {a.id: (token, a.ca())})
        self.assertTrue(b.manager.is_encrypted(a.id))

    def test_token_and_ca_never_travel_in_clear_text(self):
        a, b = self.pc("desktop"), self.pc("laptop")
        travelled = []
        original = pc_pairing._post

        def spy(ip, port, path, data, timeout):
            state, text = original(ip, port, path, data, timeout)
            travelled.append(json.dumps(data) + text)
            return state, text

        with patch.object(pc_pairing, "_post", spy):
            ok, code, token, _ = a.pair_with(b)
        self.assertTrue(ok)
        everything = "".join(travelled)
        self.assertEqual(len(travelled), 3)
        self.assertNotIn(token, everything)
        self.assertNotIn("CERTIFICATE", everything)
        self.assertNotIn(code, everything)

    def test_rejecting_the_code_locally_saves_nothing_on_the_other_pc(self):
        a, b = self.pc("desktop"), self.pc("laptop")
        ok, _, _, ca = a.pair_with(b, confirm=lambda c: False)
        self.assertFalse(ok)
        self.assertIsNone(ca)
        self.assertEqual(b.saved, {})

    def test_rejection_on_the_other_pc_issues_no_token(self):
        a, b = self.pc("desktop"), self.pc("laptop", confirm=lambda n, c: False)
        ok, _, error, ca = a.pair_with(b)
        self.assertFalse(ok)
        self.assertIsNone(ca)
        self.assertEqual(b.saved, {})

    def test_different_codes_in_each_screen_do_fail(self):
        """The people compare: if one screen shows a different code (man in the middle), someone rejects."""
        screen_b = []
        b = self.pc("laptop", confirm=lambda n, c: screen_b.append(c) or c == "000000")
        a = self.pc("desktop")
        ok, code, _, _ = a.pair_with(b, confirm=lambda c: c == "000000")
        self.assertFalse(ok)
        self.assertEqual(b.saved, {})

    def test_middleman_swapping_public_keys_fails(self):
        a, b = self.pc("desktop"), self.pc("laptop")
        original = pc_pairing._post
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        fake = pc_pairing._b64(pc_pairing._public(X25519PrivateKey.generate()))

        for step in ("inicio", "revelar"):
            with self.subTest(step=step):
                def touch(ip, port, path, data, timeout):
                    if step == "revelar" and path == pc_pairing.REVEAL_PATH:
                        data = dict(data, public_key=fake)
                    state, text = original(ip, port, path, data, timeout)
                    if step == "inicio" and path == pc_pairing.STARTUP_PATH:
                        text = json.dumps(dict(json.loads(text), public_key=fake))
                    return state, text

                with patch.object(pc_pairing, "_post", touch):
                    ok, _, _, _ = a.pair_with(b)
                self.assertFalse(ok)
                self.assertEqual(b.saved, {})
                time.sleep(0.05)

    def test_active_middleman_cannot_get_both_sides_the_same_code(self):
        """M acts as B to A and as A to B. Each side computes its code and they don't match."""
        a, b, m = self.pc("desktop"), self.pc("laptop"), self.pc("intrusa")
        codes = {}
        ready_b = threading.Event()

        def b_confirms(name, code):
            codes["b"] = code
            ready_b.set()
            return codes.get("a") == code or (time.sleep(0.5) or codes.get("a") == code)

        b.manager.confirm_code = b_confirms

        def m_resends(name, code):
            # M got A's request: on its side it pairs with B pretending to be A.
            codes["m-a"] = code
            ok, _, _, _ = p2p.pair_with_pc("127.0.0.1", b.port, a.name, a.id, my_ca_pem=m.ca())
            codes["m-b-ok"] = ok
            return True

        m.manager.confirm_code = m_resends

        def to_confirms(code):
            codes["a"] = code
            ready_b.wait(5)
            return codes.get("b") == code

        ok, _, _, _ = a.pair_with(m, confirm=to_confirms)
        self.assertNotEqual(codes["a"], codes["b"])
        self.assertFalse(ok)
        self.assertFalse(codes["m-b-ok"])
        self.assertEqual(b.saved, {})
        self.assertEqual(m.saved, {})

    def test_each_session_supports_only_attempt(self):
        b = self.pc("laptop")
        receiver = b.manager.receiver
        data = {"id": "x", "name": "x", "commitment": "0" * 64}
        session = receiver.startup(data, b.id, b.name, "192.0.2.5")["session"]
        with self.assertRaises(ValueError):
            receiver.reveal({"session": session, "public_key": "AAAA"}, b.ca())
        with self.assertRaises(LookupError):
            receiver.reveal({"session": session, "public_key": "AAAA"}, b.ca())

    def test_fails_when_a_different_pc_answers_than_the_one_chosen(self):
        a, b = self.pc("desktop"), self.pc("laptop")
        ok, _, error, _ = a.pair_with(b, expected_id="other-id")
        self.assertFalse(ok)
        self.assertIn("no es la que elegiste", error)

    def test_with_previous_pc_uses_protocol_1_if_allows(self):
        a, old_value = self.pc("desktop"), self.pc("vieja", with_tls=False)
        ok, _, token, ca = a.pair_with(old_value)
        self.assertTrue(ok)
        self.assertIsNone(ca)
        self.assertEqual(old_value.saved, {a.id: (token, None)})
        ok, _, error, _ = a.pair_with(old_value, allow_unencrypted=False)
        self.assertFalse(ok)
        self.assertIn("versión anterior", error)

    def test_encrypted_link_cannot_be_downgraded_to_protocol_1(self):
        a, b = self.pc("desktop"), self.pc("laptop")
        self.assertTrue(a.pair_with(b)[0])
        b.certs = None
        b.server.certificates = None  # as if B looked like an older PC
        ok, _, _, _ = a.pair_with(b)
        self.assertFalse(ok, "B already has A's CA: it doesn't accept protocol 1 for it")
        b.manager.allow_unencrypted = False
        self.assertFalse(b.manager.start_pairing("other", "other-id", "123456")[0])


class EncryptedSendTests(_Base):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.pc("desktop"), self.pc("laptop")
        ok, _, self.token, self.ca_b = self.a.pair_with(self.b)
        self.assertTrue(ok)
        self.b.handler.requests.clear()  # pairing requests go over HTTP (with their own encryption)
        self.file = Path(self.tmp.name) / "informe.txt"
        self.file.write_bytes(b"contenido de prueba " * 1000)

    def send(self, ca, key=None):
        return p2p.send_to_pc("127.0.0.1", self.b.port, key or self.token, self.a.name, self.a.id,
                                   [self.file], destination_name="Laptop", ca_pem=ca, reconnection_wait=1)

    def test_send_by_https_with_pinned_ca(self):
        ok, message = self.send(self.ca_b)
        self.assertTrue(ok, message)
        self.assertEqual((self.b.received / "informe.txt").read_bytes(), self.file.read_bytes())
        self.assertTrue(self.b.handler.requests and all(self.b.handler.requests))

    def test_encrypted_link_key_does_not_work_over_http(self):
        ok, message = self.send(None)
        self.assertFalse(ok)
        self.assertEqual(self.b.questions, [])

    def test_other_ca_rejects_without_send_nothing(self):
        impostor = certificates.Certificates(Path(self.tmp.name) / "impostora", "laptop", ips=lambda: [])
        impostor.ensure()
        ok, message = self.send(impostor.ca_pem())
        self.assertFalse(ok)
        self.assertIn("no es la que emparejaste", message)
        self.assertEqual(self.b.handler.requests, [], "no llegó ni una petición: ni la clave ni los archivos")
        self.assertFalse((self.b.received / "informe.txt").exists())

    def test_old_pc_without_fingerprint_follows_by_http(self):
        old_value = self.pc("vieja", with_tls=False)
        ok, _, token, ca = self.a.pair_with(old_value)
        self.assertTrue(ok)
        self.assertIsNone(ca)
        ok, message = p2p.send_to_pc("127.0.0.1", old_value.port, token, self.a.name, self.a.id, [self.file],
                                          ca_pem=None)
        self.assertTrue(ok, message)
        self.assertEqual(old_value.handler.requests[-1], False)


class AnnouncementV2Tests(_Base):
    """Point 1: whoever sees protocol 2 announced won't accept the other PC answering like an older one."""

    def test_mdns_announcement_carries_pairs_2_only_with_encrypted(self):
        with_pairing = p2p.decode_properties(p2p.create_service("lounge", 8765, "lounge-id", "192.168.1.5", pairing_level=2).properties)
        without_pairing = p2p.decode_properties(p2p.create_service("lounge", 8765, "lounge-id", "192.168.1.5").properties)
        self.assertEqual(with_pairing.get("pairing"), "2")
        self.assertNotIn("pairing", without_pairing)

    def test_discovery_saves_what_announces_each_pc(self):
        obs = p2p.PcsObserver("id-propio")
        for name, pairing_level in (("nueva", 2), ("vieja", 1)):
            info = p2p.create_service(name, 8765, f"id-{name}", "192.168.1.5", pairing_level=pairing_level)
            zc = Mock()
            zc.get_service_info.return_value = info
            obs.add_service(zc, info.type, info.name)
        self.assertEqual({e["id"]: e["pairing"] for e in obs.list_all()}, {"id-nueva": "2", "id-vieja": "1"})

    def test_pc_announcing_v2_that_answers_as_old_does_not_fall_back_to_protocol_1(self):
        a, old_value = self.pc("desktop"), self.pc("vieja", with_tls=False)  # like a man in the middle that strips v2
        ok, _, error, ca = a.pair_with(old_value, announces_v2=True)
        self.assertFalse(ok)
        self.assertIsNone(ca)
        self.assertIn("interfiriendo", error)
        self.assertEqual(old_value.saved, {}, "no se pidió el protocolo 1")
        self.assertEqual(old_value.windows, [])

    def test_other_non_v2_responses_do_not_fall_back_either(self):
        a, b = self.pc("desktop"), self.pc("laptop")
        for state, text in ((404, "No encontrado"), (503, "sin cifrado"), (403, "Prohibido")):
            with self.subTest(state=state), \
                    patch.object(pc_pairing, "_post", return_value=(state, text)) as post:
                ok, _, error, _ = a.pair_with(b, announces_v2=True)
            self.assertFalse(ok)
            self.assertIn("interfiriendo", error)
            self.assertEqual([c.args[2] for c in post.call_args_list], [pc_pairing.STARTUP_PATH])

    def test_without_announcement_follows_protocol_1_with_notice(self):
        a, old_value = self.pc("desktop"), self.pc("vieja", with_tls=False)
        ok, _, token, ca = a.pair_with(old_value, announces_v2=False)
        self.assertTrue(ok)
        self.assertIsNone(ca)

    def test_tray_passes_what_announces_chosen_pc(self):
        import tray_actions
        for pairing_level, expected in (("2", True), ("1", False), (None, False)):
            pc = {"id": "id-b", "name": "B", "ip": "192.0.2.4", "port": 8765}
            if pairing_level:
                pc["pairing"] = pairing_level
            with self.subTest(pairing_level=pairing_level), \
                    patch.object(tray_actions.p2p, "pair_with_pc", return_value=(False, "", "x", None)) as pair_mock:
                tray_actions._connect_selected({"id": "yo", "pcs": {}}, pc, Mock(), Mock(), "CA")
            self.assertEqual(pair_mock.call_args.kwargs["announces_v2"], expected)

    def test_tray_shows_the_reason_when_it_is_not_a_rejection(self):
        import tray_actions
        pc = {"id": "id-b", "name": "B", "ip": "192.0.2.4", "port": 8765, "pairing": "2"}
        notify = Mock()
        with patch.object(tray_actions.p2p, "pair_with_pc", return_value=(False, "", "Motivo claro", None)):
            tray_actions._connect_selected({"id": "yo", "pcs": {}}, pc, Mock(), notify, "CA")
        self.assertEqual(notify.call_args.args[1], "Motivo claro")


class SessionsTests(unittest.TestCase):
    """Point 2: nobody can keep pairing blocked without authenticating."""

    def setUp(self):
        self.now = [1000.0]
        self.confirmations = []
        self.receiver = pc_pairing.PairingReceiver(
            lambda name, code, **kw: self.confirmations.append(code) or True, Mock(), clock=lambda: self.now[0])

    def startup(self, ip, id_a="id-a"):
        data = {"id": id_a, "name": "A", "commitment": "0" * 64}
        return self.receiver.startup(data, "id-b", "B", ip)["session"]

    def test_offered_session_does_not_block_others_and_expires_alone(self):
        first = self.startup("192.0.2.66")
        self.startup("192.0.2.7")  # another PC can start right away
        self.now[0] += pc_pairing.PairingReceiver.OFFERED_TTL + 0.1
        with self.assertRaises(LookupError):
            self.receiver.reveal({"session": first, "public_key": "AAAA"}, "")
        self.startup("192.0.2.8")

    def test_limit_is_by_ip_with_general_cap(self):
        R = pc_pairing.PairingReceiver
        for _ in range(R.MAX_PER_MINUTE):
            self.startup("192.0.2.66")
        with self.assertRaises(PermissionError):
            self.startup("192.0.2.66")
        self.startup("192.0.2.7")  # the abusive IP doesn't hold up the others
        self.now[0] += 61
        self.startup("192.0.2.66")
        for i in range(R.MAX_TOTAL_PER_MINUTE - 1):
            self.now[0] += R.OFFERED_TTL + 0.1 if i % 10 == 9 else 0
            self.startup(f"10.0.{i}.1")
        with self.assertRaises(PermissionError):
            self.startup("10.9.9.9")

    def test_offered_have_cap(self):
        R = pc_pairing.PairingReceiver
        for i in range(R.MAX_OFFERED):
            self.startup(f"10.1.{i}.1")
        with self.assertRaises(PermissionError):
            self.startup("10.2.0.1")
        self.now[0] += R.OFFERED_TTL + 0.1
        self.startup("10.2.0.1")  # expired ones are replaced

    def test_with_person_deciding_wait_and_after_frees(self):
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        private = X25519PrivateKey.generate()
        public = pc_pairing._public(private)
        data = {"id": "id-a", "name": "A", "commitment": hashlib.sha256(public).hexdigest()}
        response = self.receiver.startup(data, "id-b", "B", "192.0.2.5")
        other = self.startup("192.0.2.6", id_a="id-c")
        deciding, free = threading.Event(), threading.Event()

        def person(name, code, **kw):
            deciding.set()
            free.wait(5)
            return False

        self.receiver.confirm = person
        thread = threading.Thread(target=lambda: self._reveal(private, response), daemon=True)
        thread.start()
        self.assertTrue(deciding.wait(5))
        with self.assertRaises(PermissionError):
            self.startup("192.0.2.7")
        with self.assertRaises(PermissionError):
            self.receiver.reveal({"session": other, "public_key": "AAAA"}, "")
        self.now[0] += pc_pairing.PairingReceiver.DECIDING_TTL + 1
        self.startup("192.0.2.8")  # the window expired: it no longer blocks
        free.set()
        thread.join(5)

    def _reveal(self, private, response):
        public_b = pc_pairing._from_b64(response["public_key"], 32)
        public = pc_pairing._public(private)
        summary = pc_pairing.transcription(response["session"], "id-a", "id-b", public, public_b)
        keys = pc_pairing.derive(private, public_b, summary)
        ca = certificates.Certificates(Path(tempfile.mkdtemp()), "a", ips=lambda: [])
        ca.ensure()
        try:
            self.receiver.reveal({"session": response["session"], "public_key": pc_pairing._b64(public),
                                   **pc_pairing._encrypt(keys["a_b"], {"ca": ca.ca_pem()}, summary)}, "")
        except PermissionError:
            pass


class ReplacementTests(_Base):
    """Point 3: an existing link isn't overwritten unnoticed."""

    def test_encrypted_link_cannot_be_replaced_by_another_pc_with_the_same_id(self):
        a, b = self.pc("desktop"), self.pc("laptop")
        self.assertTrue(a.pair_with(b)[0])
        saved = dict(b.saved)
        impostor = self.pc("impostora")
        ok, _, error, _ = p2p.pair_with_pc("127.0.0.1", b.port, "desktop", a.id, my_ca_pem=impostor.ca())
        self.assertFalse(ok)
        self.assertIn("vínculo cifrado", error)
        self.assertEqual(b.saved, saved)
        self.assertEqual(len(b.windows), 1, "a la persona de B no se le mostró nada")

    def test_same_pc_replaces_by_tls_with_token(self):
        a, b = self.pc("desktop"), self.pc("laptop")
        ok, _, token, ca_b = a.pair_with(b)
        self.assertTrue(ok)
        b.saved_names[a.id] = "Lounge desktop"
        b.handler.requests.clear()
        ok, _, new_item, _ = a.pair_with(b, current_ca=ca_b, current_token=token)
        self.assertTrue(ok, new_item)
        self.assertNotEqual(new_item, token)
        self.assertEqual(b.saved[a.id][0], new_item)
        self.assertTrue(b.handler.requests and all(b.handler.requests), "todo por TLS")
        self.assertEqual(b.windows[-1]["replaces"], "Lounge desktop")

    def test_foreign_token_or_plain_http_does_not_authorize_replacement(self):
        a, b, c = self.pc("desktop"), self.pc("laptop"), self.pc("other")
        _, _, token_a, ca_b = a.pair_with(b)
        _, _, token_c, _ = c.pair_with(b)
        saved = dict(b.saved)
        # C's token can't replace A's link.
        ok, _, error, _ = p2p.pair_with_pc("127.0.0.1", b.port, "x", a.id, my_ca_pem=c.ca(),
                                                   current_ca=ca_b, current_token=token_c)
        self.assertFalse(ok)
        self.assertIn("vínculo cifrado", error)
        # Nor can A's token over HTTP.
        with patch.object(pc_pairing, "_connection",
                          lambda ip, port, timeout, ca_pem=None: http.client.HTTPConnection(ip, port, timeout=timeout)):
            ok, _, error, _ = p2p.pair_with_pc("127.0.0.1", b.port, "x", a.id, my_ca_pem=c.ca(),
                                                       current_ca=ca_b, current_token=token_a)
        self.assertFalse(ok)
        self.assertEqual(b.saved, saved)

    def test_unencrypted_link_replaces_notifying_with_saved_name(self):
        a, b = self.pc("desktop"), self.pc("laptop")
        b.manager.register(a.id, "old-token")
        b.saved_names[a.id] = "My desktop"
        ok, _, token, ca = p2p.pair_with_pc("127.0.0.1", b.port, "My desktop\n(nuevo)", a.id,
                                                    my_ca_pem=a.ca())
        self.assertTrue(ok)
        self.assertEqual(b.saved[a.id], (token, a.ca()))
        window = b.windows[-1]
        self.assertEqual(window["replaces"], "My desktop", "the name saved here, not the one the other sends")
        self.assertEqual(window["name"], "My desktop (nuevo)")

    def test_protocol_1_also_notifies_replacement(self):
        b = self.pc("laptop")
        b.manager.register("id-vieja", "old-token")
        b.saved_names["id-vieja"] = "PC vieja"
        ok, _, _ = b.manager.start_pairing("Otra\u202eanu", "id-vieja", "123456")
        self.assertTrue(ok)
        self.assertEqual(b.windows[-1]["replaces"], "PC vieja")
        self.assertNotIn("\u202e", b.windows[-1]["name"])

    def test_new_link_does_not_claim_to_replace_one(self):
        a, b = self.pc("desktop"), self.pc("laptop")
        self.assertTrue(a.pair_with(b)[0])
        self.assertIsNone(b.windows[-1]["replaces"])

    def test_visible_name(self):
        nv = pc_pairing.visible_name
        self.assertEqual(nv("Sala\r\nCódigo correcto: 123 456"), "Sala Código correcto: 123 456")
        self.assertEqual(nv("a\u200b\u202eb\x00c\td"), "a b c d")
        self.assertEqual(len(nv("x" * 500)), pc_pairing.NAME_LENGTH)
        self.assertTrue(nv("x" * 500).endswith("…"))
        self.assertEqual(nv("   "), "PC")
        self.assertEqual(nv(None, "Otro"), "Otro")
        self.assertEqual(nv("Ana laptop"), "Ana laptop")

    def test_window_receives_replacement_notice(self):
        import dialogs
        with patch.object(dialogs.subprocess, "run", return_value=Mock(stdout="accept")) as run:
            self.assertTrue(dialogs.confirm_pairing("B", "123456", replaces="My desktop"))
        sent = json.loads(run.call_args.args[0][-2])
        self.assertEqual(sent["replaces"], "My desktop")


class SavedLinksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.certs = certificates.Certificates(Path(self.tmp.name), "pc", ips=lambda: [])
        self.certs.ensure()

    def tearDown(self):
        self.tmp.cleanup()

    def test_saves_ca_with_fingerprint_and_listing_indicates(self):
        cfg = {"pcs": {"a": "t1", "b": "t2"}}
        linked_pcs.save_ca(cfg, "a", self.certs.ca_pem())
        self.assertEqual(cfg["pcs_info"]["a"]["ca_fingerprint"], self.certs.ca_fingerprint())
        self.assertEqual(linked_pcs.ca_of(cfg, "a"), self.certs.ca_pem())
        self.assertIsNone(linked_pcs.ca_of(cfg, "b"))
        self.assertEqual(linked_pcs.encrypted_ids(cfg), {"a"})
        listing = {e["id"]: e["encrypted"] for e in linked_pcs.list_all(cfg, [])}
        self.assertEqual(listing, {"a": True, "b": False})
        linked_pcs.save_ca(cfg, "a", None)
        self.assertNotIn("ca", cfg["pcs_info"]["a"])

    def test_ca_that_does_not_match_the_fingerprint_is_not_used(self):
        cfg = {"pcs": {"a": "t1"}}
        linked_pcs.save_ca(cfg, "a", self.certs.ca_pem())
        cfg["pcs_info"]["a"]["ca_fingerprint"] = "0" * 64
        with self.assertRaises(ValueError):
            linked_pcs.ca_of(cfg, "a")

    def test_save_link_persists_ca_and_notifies_if_goes_unencrypted(self):
        saved = {}
        manager = p2p.PairingManager()
        with patch.object(bridge, "load_config", return_value={"pcs": {}}), \
                patch.object(bridge, "save_config", side_effect=saved.update), \
                patch.object(bridge.Handler, "pairing_manager", manager):
            bridge.save_pc_key("a", "token-a", self.certs.ca_pem())
            self.assertEqual(saved["pcs_info"]["a"]["ca"], self.certs.ca_pem())
            self.assertTrue(manager.is_encrypted("a"))
            with self.assertLogs(level=logging.WARNING) as registry:
                bridge.save_pc_key("b", "token-b")
        self.assertIn("without encryption", "\n".join(registry.output))
        self.assertFalse(manager.is_encrypted("b"))

    def test_does_not_send_the_key_to_an_unlinked_pc(self):
        file = Path(self.tmp.name) / "note.txt"
        file.write_text("hello")
        cfg = {"id": "propio", "key": "this-pc-key", "port": 8765, "name": "pc", "pcs": {"a": "t1"}}
        for destination in ({"id": "extrana", "ip": "192.0.2.9", "port": 8765, "name": "PC extraña"},
                        {"id": "", "ip": "192.0.2.10", "port": 8765, "name": "192.0.2.10"}):
            with patch.object(bridge, "_resolve_send_destination", return_value=destination),                     patch.object(bridge.p2p, "send_to_pc") as send,                     patch.object(bridge.so, "notify") as notify, patch.object(bridge.sys, "stdout", None):
                self.assertEqual(bridge.send_command(cfg, [str(file)]), 1)
            send.assert_not_called()
            self.assertIn("vincula", notify.call_args.args[1])

    def test_pages_cannot_be_framed_by_another_page(self):
        class Fake:
            respond = bridge.Handler.respond

            def __init__(self):
                self.request_headers, self.wfile = {}, __import__("io").BytesIO()

            def send_response(self, code):
                pass

            def send_header(self, k, v):
                self.request_headers[k] = v

            def end_headers(self):
                pass
        fake = Fake()
        fake.respond(200, "<html></html>", "text/html; charset=utf-8")
        self.assertEqual(fake.request_headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", fake.request_headers["Content-Security-Policy"])

    def test_send_uses_saved_ca_or_http_for_previous_links(self):
        file = Path(self.tmp.name) / "note.txt"
        file.write_text("hello")
        cfg = {"id": "propio", "key": "local", "port": 8765, "name": "pc", "pcs": {"a": "t1", "b": "t2"}}
        linked_pcs.save_ca(cfg, "a", self.certs.ca_pem())
        for peer, expected in (("a", self.certs.ca_pem()), ("b", None)):
            destination = {"id": peer, "ip": "192.0.2.9", "port": 8765, "name": f"PC {peer}"}
            with patch.object(bridge, "_resolve_send_destination", return_value=destination), \
                    patch.object(bridge.p2p, "send_to_pc", return_value=(True, "ok")) as send, \
                    patch.object(bridge.so, "notify"), patch.object(bridge.dialogs, "ProgressMonitor"), \
                    patch.object(bridge.sys, "stdout", None), \
                    self.assertLogs(level=logging.DEBUG) as registry:
                logging.getLogger().debug("inicio")
                bridge.send_command(cfg, [str(file)])
            self.assertEqual(send.call_args.kwargs["ca_pem"], expected)
            self.assertEqual(any("unencrypted" in line for line in registry.output), expected is None)

    def test_encrypted_link_key_only_by_tls(self):
        manager = p2p.PairingManager({"a": "token-a", "b": "token-b"}, encrypted={"a"})
        self.assertTrue(manager.pc_valid_key("token-a", by_tls=True))
        self.assertFalse(manager.pc_valid_key("token-a", by_tls=False))
        self.assertTrue(manager.pc_valid_key("token-b", by_tls=False))


class OtherPcReasonTests(unittest.TestCase):
    def test_only_show_copyover_reasons(self):
        self.assertEqual(pc_pairing.other_pc_reason("Descarga la versión Pro en ejemplo.com", "Rechazado"),
                         "Rechazado")
        self.assertEqual(pc_pairing.other_pc_reason(None, "Rechazado"), "Rechazado")
        pending = "Hay otra solicitud de vinculación pendiente. Intenta de nuevo en unos segundos."
        self.assertEqual(pc_pairing.other_pc_reason(pending, "Rechazado"), pending)

    def test_reason_in_english_shows_in_language_from_this_pc(self):
        reason = "The link request expired. Try again."
        es = pc_pairing.ES[reason]
        self.assertEqual(pc_pairing.other_pc_reason(reason, "Rechazado"), es)
        self.assertEqual(pc_pairing.other_pc_reason(es, "Rechazado"), es)

    def test_all_reasons_have_translation(self):
        for reason in pc_pairing.OTHER_PC_REASONS:
            self.assertIn(reason, pc_pairing.ES)


if __name__ == "__main__":
    unittest.main()
