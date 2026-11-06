import sys
from unittest.mock import patch
import unittest.mock as mock
"""Unit and integration tests for the P2P logic."""

import hashlib
import http.client
import io
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import i18n
import p2p
import bridge


class TestDiscoveryP2p(unittest.TestCase):
    def test_create_service(self):
        info = p2p.create_service("laptop-test", 8765, "uuid-1234", "192.168.1.50")
        self.assertEqual(info.type, "_puente._tcp.local.")
        self.assertTrue(info.name.startswith("Puente-"))
        self.assertTrue(info.name.endswith("._puente._tcp.local."))
        self.assertEqual(info.port, 8765)
        expected_host = hashlib.sha256(b"uuid-1234").hexdigest()[:24]
        self.assertEqual(info.server, f"puente-p2p-{expected_host}.local.")
        props = p2p.decode_properties(info.properties)
        self.assertEqual(props["v"], "1")
        self.assertEqual(props["id"], "uuid-1234")
        self.assertEqual(props["name"], "laptop-test")
        self.assertIn(props["so"], ("windows", "linux"))

    def test_observer_ignores_id_own(self):
        changes = []
        obs = p2p.PcsObserver(my_id="mi-propio-id", on_change=changes.append)
        zc = MagicMock()
        info_mock = MagicMock()
        info_mock.properties = {b"id": b"mi-propio-id", b"v": b"1", b"so": b"windows"}
        zc.get_service_info.return_value = info_mock
        obs.add_service(zc, p2p.SERVICE_TYPE, f"mi-pc.{p2p.SERVICE_TYPE}")
        self.assertEqual(len(obs.list_all()), 0)
        self.assertEqual(len(changes), 0)

    def test_observer_adds_updates_and_removes_remote(self):
        changes = []
        obs = p2p.PcsObserver(my_id="mi-propio-id", on_change=changes.append)
        zc = MagicMock()
        info_mock = MagicMock()
        info_mock.properties = {b"id": b"remoto-999", b"v": b"1", b"so": b"linux"}
        info_mock.port = 8765
        info_mock.parsed_addresses.return_value = ["192.168.1.80"]
        zc.get_service_info.return_value = info_mock
        service_name = f"PC-Fedora.{p2p.SERVICE_TYPE}"
        obs.add_service(zc, p2p.SERVICE_TYPE, service_name)
        pcs = obs.list_all()
        self.assertEqual(len(pcs), 1)
        self.assertEqual(pcs[0]["name"], "PC-Fedora")
        self.assertEqual(pcs[0]["ip"], "192.168.1.80")
        self.assertEqual(pcs[0]["so"], "linux")
        self.assertEqual(len(changes), 1)
        info_mock.parsed_addresses.return_value = ["192.168.1.81"]
        obs.update_service(zc, p2p.SERVICE_TYPE, service_name)
        self.assertEqual(obs.list_all()[0]["ip"], "192.168.1.81")
        self.assertEqual(len(changes), 2)
        obs.remove_service(zc, p2p.SERVICE_TYPE, service_name)
        self.assertEqual(len(obs.list_all()), 0)
        self.assertEqual(len(changes), 3)

    def test_observer_clean(self):
        changes = []
        obs = p2p.PcsObserver(my_id="mi-id", on_change=changes.append)
        zc = MagicMock()
        info_mock = MagicMock()
        info_mock.properties = {b"id": b"otro-id", b"v": b"1", b"so": b"windows"}
        info_mock.port = 8765
        info_mock.parsed_addresses.return_value = ["192.168.1.90"]
        zc.get_service_info.return_value = info_mock
        obs.add_service(zc, p2p.SERVICE_TYPE, f"otro.{p2p.SERVICE_TYPE}")
        self.assertEqual(len(obs.list_all()), 1)
        obs.clean()
        self.assertEqual(len(obs.list_all()), 0)


class TestTransfersP2p(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.destination = Path(self.tmp.name) / "Desde Puente"

    def tearDown(self):
        self.tmp.cleanup()

    def _example_offer(self, name="foto.jpg", data=b"contenido de prueba"):
        return {
            "sender": {"name": "PC-Emisora", "id": "peer-1"},
            "files": [
                {
                    "name": name,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            ],
        }

    def test_rejected_dangerous_names(self):
        manager = p2p.TransfersManager(self.destination, ask_user=lambda _: "accept")
        for dangerous in ("../secreto.txt", "..\\win.ini", "/etc/passwd", "sub/archivo.txt", "a\x00b.txt", "..", "."):
            offer = self._example_offer(name=dangerous)
            code, _ = manager.process_offer(offer)
            self.assertEqual(code, 400, f"Debio rechazar {dangerous!r}")

    def test_exceeded_size_limit(self):
        manager = p2p.TransfersManager(self.destination, ask_user=lambda _: "accept", max_bytes=100)
        offer = self._example_offer(data=b"x" * 101)
        code, _ = manager.process_offer(offer)
        self.assertEqual(code, 413)

    def test_rejected_offer_and_expired(self):
        rejection_manager = p2p.TransfersManager(self.destination, ask_user=lambda _: "reject")
        code, _ = rejection_manager.process_offer(self._example_offer())
        self.assertEqual(code, 403)

        def take_long(_):
            time.sleep(0.2)
            return "accept"

        timeout_manager = p2p.TransfersManager(self.destination, ask_user=take_long, offer_time=0.05)
        code_t, _ = timeout_manager.process_offer(self._example_offer())
        self.assertEqual(code_t, 408)
        self.assertFalse(self.destination.exists())

    def test_trusted_pc_accepts_always(self):
        trusted = []
        calls = []

        def ask(_):
            calls.append(1)
            return "always"

        manager = p2p.TransfersManager(
            self.destination, ask_user=ask, on_trust=trusted.append
        )
        c1, r1 = manager.process_offer(self._example_offer())
        self.assertEqual(c1, 200)
        self.assertEqual(trusted, ["peer-1"])
        self.assertEqual(len(calls), 1)

        # A second offer from the same peer doesn't ask again.
        c2, r2 = manager.process_offer(self._example_offer())
        self.assertEqual(c2, 200)
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(r1["id"], r2["id"])

    def test_upload_integrates_and_invalid_hash_cleans_partial(self):
        manager = p2p.TransfersManager(self.destination, ask_user=lambda _: "accept")
        data = b"Hola mundo P2P " * 1000
        _, resp = manager.process_offer(self._example_offer("notas.txt", data))
        id_trans = resp["id"]

        # Attempt with corrupted bytes: must not leave a .parcial.
        corrupt = b"X" + data[1:]
        err_code, _, err_path, _ = manager.receive_file(id_trans, 0, io.BytesIO(corrupt), len(corrupt))
        self.assertEqual(err_code, 400)
        self.assertIsNone(err_path)
        self.assertEqual(list(self.destination.glob("*.parcial")), [])
        self.assertEqual(list(self.destination.glob("*")), [])

        # Successful upload.
        ok_code, _, ok_path, complete = manager.receive_file(id_trans, 0, io.BytesIO(data), len(data))
        self.assertEqual(ok_code, 200)
        self.assertTrue(complete)
        self.assertIsNotNone(ok_path)
        self.assertEqual(ok_path.read_bytes(), data)
        self.assertEqual(list(self.destination.glob("*.parcial")), [])


class TestHttpServerP2p(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.destination = self.folder / "Desde Puente"
        self.origin = self.folder / "origen"
        self.origin.mkdir()

        self.manager = p2p.TransfersManager(
            destination=self.destination,
            ask_user=lambda _: "accept",
        )

        class TestHandler(bridge.Handler):
            key = "clave-secreta-prueba"
            notices = False
            p2p_manager = self.manager

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_without_correct_key_responds_403(self):
        for method, path in (
            ("GET", "/p2p/equipos"),
            ("POST", "/p2p/ofrecer"),
            ("PUT", "/p2p/subir/id123/0"),
            ("POST", "/p2p/cancelar/id123"),
        ):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            conn.request(method, path, headers={"X-Key": "incorrecta", "Content-Length": "0"})
            resp = conn.getresponse()
            resp.read()
            conn.close()
            self.assertEqual(resp.status, 403, f"{method} {path} sin clave debio dar 403")

    def test_complete_flow_several_files_and_renamed(self):
        a1 = self.origin / "documento.txt"
        a2 = self.origin / "imagen.bin"
        a1.write_bytes(b" Contenido 1 " * 500)
        a2.write_bytes(bytes(range(256)) * 400)

        # Pre-create documento.txt at the destination to check it's renamed to documento (1).txt.
        self.destination.mkdir(parents=True, exist_ok=True)
        (self.destination / "documento.txt").write_bytes(b"existente")

        progress = []
        ok, msg = p2p.send_to_pc(
            ip="127.0.0.1",
            port=self.port,
            key="clave-secreta-prueba",
            my_name="Laptop-Prueba",
            my_id="id-emisor",
            paths=[a1, a2],
            to_progress=lambda env, tot, name: progress.append((env, tot, name)),
        )
        self.assertTrue(ok, msg)
        self.assertTrue(len(progress) >= 2)
        self.assertEqual((self.destination / "documento.txt").read_bytes(), b"existente")
        self.assertEqual((self.destination / "documento (1).txt").read_bytes(), a1.read_bytes())
        self.assertEqual((self.destination / "imagen.bin").read_bytes(), a2.read_bytes())

    def test_http_pairing_and_send_with_token(self):
        # Set up the pairing manager on the test server. This server has no certificates: it pairs with
        # protocol 1 (unencrypted), like an older PC.
        seen = []
        pairer = p2p.PairingManager(confirm_code=lambda name, code_text: seen.append(code_text) or True)
        self.server.RequestHandlerClass.pairing_manager = pairer

        pairing_ok, code_text, token, ca = p2p.pair_with_pc(
            ip="127.0.0.1",
            port=self.port,
            my_name="PC-Amigo",
            my_id="id-amigo",
            confirm_code=lambda code: seen.append(code) or True,
            my_ca_pem="cualquiera",
        )
        self.assertTrue(pairing_ok)
        self.assertEqual(seen, [code_text, code_text])
        self.assertIsNone(ca)
        self.assertTrue(len(token) >= 16)

        # Send a file using the paired token (without knowing the receiver's main key).
        file = self.origin / "compartido.txt"
        file.write_bytes(b"archivo de otro equipo emparejado")
        ok_env, msg = p2p.send_to_pc(
            ip="127.0.0.1",
            port=self.port,
            key=token,
            my_name="PC-Amigo",
            my_id="id-amigo",
            paths=[file],
        )
        self.assertTrue(ok_env, msg)
        self.assertEqual((self.destination / "compartido.txt").read_bytes(), file.read_bytes())

        # The paired computer's token doesn't open the owner's routes or profile.
        for path, expected in (("/traer", 401), ("/sincronizar", 401), ("/p2p/perfil", 403),
                               ("/p2p/estado", 403), ("/p2p/equipos", 200)):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            try:
                conn.request("GET", path, headers={"X-Key": token})
                self.assertEqual(conn.getresponse().status, expected, path)
            finally:
                conn.close()

    def test_intact_iphone_contract(self):
        # /ping doesn't need a key.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/ping")
        r_ping = conn.getresponse()
        self.assertEqual(r_ping.status, 200)
        self.assertEqual(r_ping.read().decode("utf-8"), "Puente activo")
        conn.close()

        # /traer and /enviar without a key answer 401 (the original Shortcuts contract).
        for method, path in (("GET", "/traer"), ("POST", "/enviar")):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            conn.request(method, path, headers={"X-Key": "mala"})
            resp = conn.getresponse()
            resp.read()
            conn.close()
            self.assertEqual(resp.status, 401)


class TestMenuContextual(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_linux_context_menu_install_and_remove(self):
        import linux_system

        fake_script = self.base / "bridge.py"
        fake_script.write_text("# puente", encoding="utf-8")

        nautilus_dir = self.base / ".local/share/nautilus/scripts"
        kio_dir = self.base / ".local/share/kio/servicemenus"

        linux_system.install_context_menu(
            fake_script, nautilus_folder=nautilus_dir, kio_folder=kio_dir
        )
        nautilus_file = nautilus_dir / "Enviar con CopyOver"
        kio_file = kio_dir / "send-with-copyover.desktop"

        self.assertTrue(nautilus_file.exists())
        self.assertTrue(kio_file.exists())
        self.assertIn("--send", nautilus_file.read_text(encoding="utf-8"))
        self.assertIn("--send %F", kio_file.read_text(encoding="utf-8"))

        linux_system.remove_context_menu(
            nautilus_folder=nautilus_dir, kio_folder=kio_dir
        )
        self.assertFalse(nautilus_file.exists())
        self.assertFalse(kio_file.exists())

    def test_linux_context_menu_follows_the_language(self):
        import i18n
        import linux_system

        self.addCleanup(i18n.pin, i18n.current())
        fake_script = self.base / "bridge.py"
        fake_script.write_text("# puente", encoding="utf-8")
        folders = dict(nautilus_folder=self.base / "nautilus", kio_folder=self.base / "kio")

        i18n.pin("es")
        linux_system.install_context_menu(fake_script, **folders)
        i18n.pin("en")
        linux_system.install_context_menu(fake_script, **folders)
        self.assertEqual([f.name for f in folders["nautilus_folder"].iterdir()], ["Send with CopyOver"])

        linux_system.remove_context_menu(**folders)
        self.assertEqual(list(folders["nautilus_folder"].iterdir()), [])

    def test_linux_app_icon_install_and_remove(self):
        import linux_system

        apps_dir = self.base / ".local/share/applications"
        icon = self.base / ".local/share/icons/hicolor/256x256/apps/copyover.png"

        fake_script = self.base / "bridge.py"
        fake_script.write_text("# puente", encoding="utf-8")

        linux_system.install_app_icon(fake_script, apps_folder=apps_dir, icon=icon)
        desktop = apps_dir / f"{linux_system.ID_APP}.desktop"
        content = desktop.read_text(encoding="utf-8")

        self.assertTrue(icon.exists())
        self.assertIn(f"Icon={icon}", content)
        self.assertIn(f"StartupWMClass={linux_system.ID_APP}", content)
        self.assertIn(f'"{fake_script.resolve()}"', content)
        self.assertNotIn("NoDisplay", content)

        linux_system.remove_app_icon(apps_folder=apps_dir, icon=icon)
        self.assertFalse(desktop.exists())
        self.assertFalse(icon.exists())

    def test_access_in_linux_desktop_install_and_remove(self):
        import linux_system

        origin = self.base / "copyover.desktop"
        origin.write_text("[Desktop Entry]\nName=CopyOver\n", encoding="utf-8")
        folder = self.base / "Escritorio"
        folder.mkdir()

        destination = linux_system.install_desktop_access(origin, folder=folder)
        self.assertEqual(destination, folder / "copyover.desktop")
        self.assertEqual(destination.read_text(encoding="utf-8"), origin.read_text(encoding="utf-8"))

        linux_system.remove_desktop_access(folder=folder)
        self.assertFalse(destination.exists())
        # Without a desktop folder nothing is created.
        self.assertIsNone(linux_system.install_desktop_access(origin, folder=self.base / "no-existe"))

    def test_pin_in_gnome_dock_without_repeat_nor_touch_other(self):
        import linux_system

        favorites = [["org.gnome.Nautilus.desktop", "firefox.desktop"]]
        read = lambda: list(favorites[-1])
        write = favorites.append

        self.assertTrue(linux_system.pin_in_dock(read, write))
        self.assertEqual(favorites[-1], ["org.gnome.Nautilus.desktop", "firefox.desktop", "copyover.desktop"])
        linux_system.pin_in_dock(read, write)
        self.assertEqual(len(favorites), 2)  # already pinned: not written again

        linux_system.remove_from_dock(read, write)
        self.assertEqual(favorites[-1], ["org.gnome.Nautilus.desktop", "firefox.desktop"])
        # Outside GNOME (no favorites to read) nothing is written.
        self.assertFalse(linux_system.pin_in_dock(lambda: None, write))
        self.assertEqual(len(favorites), 3)

    @unittest.skipUnless(sys.platform == 'win32', 'Solo Windows')
    def test_windows_context_menu_registry_command(self):
        import windows_system

        fake_script = self.base / "bridge.py"
        cmd = windows_system.context_menu_command(fake_script)
        self.assertIn("--send", cmd)
        self.assertIn('"%1"', cmd)
        exe, arguments = windows_system.access_send_to(fake_script)
        self.assertEqual(arguments, f'"{fake_script.resolve()}" --send')
        self.assertTrue(hasattr(windows_system, "install_context_menu"))
        self.assertTrue(hasattr(windows_system, "remove_context_menu"))


class TestPairingByCode(unittest.TestCase):
    def test_code_pairing_flow_6_digits(self):
        pairer = p2p.PairingManager(confirm_code=lambda name, code_text: True)
        success, code, token = pairer.start_pairing("PC-Invitada", "id-invitado")
        self.assertTrue(success)
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())
        self.assertTrue(len(token) >= 16)
        self.assertTrue(pairer.pc_valid_key(token))
        self.assertFalse(pairer.pc_valid_key("token-falso"))


class TestNewLocalSendFunctions(unittest.TestCase):
    def setUp(self):
        i18n.pin("es")

    def test_get_default_downloads_folder(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(bridge.so, "DOCUMENTS", Path(td)),                 mock.patch.object(bridge, "load_config", return_value={}):
            folder = bridge.get_downloads_folder("iphone")
            self.assertTrue(folder.exists())
            self.assertEqual(folder.name, "From CopyOver")
            self.assertEqual(bridge.get_downloads_folder("p2p"), folder / "From another PC")

    def test_transfer_progress_metrics(self):
        notified = []
        with tempfile.TemporaryDirectory() as td:
            manager = p2p.TransfersManager(
                destination=Path(td),
                ask_user=lambda o: "accept",
                to_progress=notified.append,
            )
            offer = {
                "sender": {"name": "PC-Remota", "id": "id-remoto"},
                "files": [{"name": "datos.bin", "size": 2048, "sha256": hashlib.sha256(b"A" * 2048).hexdigest()}],
            }
            code_text, resp = manager.process_offer(offer)
            self.assertEqual(code_text, 200)
            id_t = resp["id"]
            stream = io.BytesIO(b"A" * 2048)
            res, msg, path, completed = manager.receive_file(id_t, 0, stream, 2048)
            self.assertEqual(res, 200)
            self.assertTrue(completed)
            with_data = [n for n in notified if n]
            self.assertTrue(len(with_data) > 0)
            last = with_data[-1]
            self.assertEqual(last["percent"], 100.0)
            self.assertIn("speed_mb_s", last)
            # The panel's bar says «Receiving from PC-Remota».
            self.assertEqual((last["direction"], last["peer"]), ("received", "PC-Remota"))
            self.assertIn("eta_seconds", last)


    def test_focus_existing_window_without_error(self):
        import dialogs
        res = dialogs.focus_existing_window("VentanaInexistenteXYZ123")
        self.assertIsInstance(res, bool)
        self.assertFalse(res)

    def test_500mb_validation_shortcut_vs_mobile_app_streaming(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(bridge.so, "set_files"), \
             mock.patch.object(bridge.so, "notify"), \
             mock.patch.object(bridge, "notify_received"), \
             mock.patch.object(bridge, "get_downloads_folder", return_value=Path(td)):
            file = Path(td) / "video_prueba.mp4"
            content = b"X" * 4096
            file.write_bytes(content)
            bridge.STATE["prepared_files"] = [file]
            token = "tok_test_stream"
            bridge.STATE["guest_tokens"] = {token: time.time() + 600}
            bridge.STATE["guests_since"] = {token: time.time() - 1}  # the guest arrived before the offer
            device_before = dict(bridge.STATE.get("apple_devices", {}))
            self.addCleanup(lambda: bridge.STATE.update(apple_devices=device_before))
            bridge.STATE["apple_devices"] = {"127.0.0.1": {"ip": "127.0.0.1", "guest": token}}
            bridge.STATE["phone_offer"] = {
                "id": "of1",
                "ts": time.time(),
                "type": "file",
                "destination": bridge.guest_destination("127.0.0.1"),
                "name": file.name,
                "size": len(content),
                "exceeds_shortcut": False,
                "delivered": False,
            }

            server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", f"/invitado/estado?t={token}")
                r_state = conn.getresponse()
                self.assertEqual(r_state.status, 200)
                state_data = p2p.json.loads(r_state.read().decode("utf-8"))
                self.assertFalse(state_data["offer"]["delivered"])

                conn.request("GET", f"/invitado/descargar?t={token}")
                r_desc = conn.getresponse()
                self.assertEqual(r_desc.status, 200)
                self.assertEqual(r_desc.read(), content)
                self.assertTrue(bridge.STATE["phone_offer"]["delivered"])

                # Direct streaming upload with X-Nombre-Archivo.
                conn.request(
                    "POST",
                    f"/invitado/subir?t={token}",
                    body=b"STREAM_DATA_IOS" * 100,
                    headers={
                        "Content-Length": str(len(b"STREAM_DATA_IOS" * 100)),
                        "X-Nombre-Archivo": "grabacion_ios.mov",
                    },
                )
                r_sub = conn.getresponse()
                self.assertEqual(r_sub.status, 200)
                sub_data = p2p.json.loads(r_sub.read().decode("utf-8"))
                self.assertTrue(sub_data["ok"])
                conn.close()
            finally:
                bridge.STATE["prepared_files"] = []
                server.shutdown()
                server.server_close()

    def test_launch_double_lock_unique_instance(self):
        import dialogs
        dialogs._last_launch_ts = 0.0
        self.assertFalse(dialogs._recently_launched(0.5))
        dialogs._mark_launch()
        self.assertTrue(dialogs._recently_launched(0.5))
        time.sleep(0.55)
        self.assertFalse(dialogs._recently_launched(0.5))


    def test_devices_do_not_repeat_after_a_network_change(self):
        entries = [
            {"ip": "192.168.1.30", "name": "Mi iPhone", "type": "iphone", "last_seen": "ready"},
            {"ip": "172.20.10.1", "name": "Mi iPhone", "type": "iphone", "last_seen": "18:10"},
            {"ip": "192.168.1.40", "name": "Mi iPad", "type": "ipad", "last_seen": "ready"},
            {"ip": "172.20.10.3", "name": "Mi iPad", "type": "ipad", "last_seen": "18:12"},
        ]
        dedup = bridge._dedupe_apple_devices(entries, ip_pc="172.20.10.5")
        self.assertEqual(len(dedup), 2)
        ips = {d["name"]: d["ip"] for d in dedup}
        self.assertEqual(ips["Mi iPhone"], "172.20.10.1")
        self.assertEqual(ips["Mi iPad"], "172.20.10.3")


    def test_pc_chassis_type(self):
        chassis = p2p.chassis_type()
        self.assertIn(chassis, ("laptop", "desktop"))

    def test_android_and_ios_model_detection(self):
        class DummyHandler:
            client_address = ("192.168.1.99", 54321)
            def __init__(self, headers):
                self.headers = headers
            _identify_client = bridge.Handler._identify_client

        with patch("bridge.save_config"):
            # 1. Android with the model in the User-Agent
            bridge.STATE["apple_devices"].clear()
            h_android = DummyHandler({
                "User-Agent": "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 Chrome/120.0 Mobile"
            })
            res_a = h_android._identify_client()
            self.assertIn("Samsung SM-S918B", res_a)
            device_a = bridge.STATE["apple_devices"].get("192.168.1.99")
            self.assertIsNotNone(device_a)
            self.assertEqual(device_a["type"], "android")

            # 2. iPhone with the Shortcuts X-Device header
            bridge.STATE["apple_devices"].clear()
            h_ios = DummyHandler({
                "User-Agent": "Shortcuts/1254 CFNetwork/1498.700.2 Darwin/23.6.0",
                "X-Device": "Mi iPhone"
            })
            res_i = h_ios._identify_client()
            self.assertIn("Mi iPhone", res_i)
            device_i = bridge.STATE["apple_devices"].get("192.168.1.99")
            self.assertEqual(device_i["type"], "iphone")
            self.assertEqual(device_i["name"], "Mi iPhone")


    def test_rejected_windows_reserved_names_and_sanitized(self):
        for res in ("CON", "PRN", "AUX", "NUL", "COM1", "LPT1"):
            self.assertFalse(p2p.is_valid_name(f"{res}.txt"))
            self.assertFalse(p2p.is_valid_name(res.lower()))
            secs = p2p.safe_name(f"{res}.txt")
            self.assertTrue(secs.startswith("file_"))
        length = "x" * 300 + ".png"
        self.assertLessEqual(len(p2p.safe_name(length)), 200)

    def test_disk_insufficient_space_responds_507(self):
        with tempfile.TemporaryDirectory() as td:
            manager = p2p.TransfersManager(destination=Path(td))
            offer = {
                "sender": {"name": "PC2", "id": "id2"},
                "files": [{"name": "pesado.bin", "size": 500 * 1024 * 1024, "sha256": "a" * 64}],
            }
            class FakeUsage:
                free = 100 * 1024 * 1024  # only 100 MB free
            with mock.patch("shutil.disk_usage", return_value=FakeUsage()):
                valid, code, msg = manager.validate_offer(offer)
                self.assertFalse(valid)
                self.assertEqual(code, 507)
                self.assertIn("Espacio insuficiente", msg)

    def test_bundle_directory_to_flight(self):
        import zipfile
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "carpeta_prueba"
            folder.mkdir()
            (folder / "a.txt").write_text("hola a", encoding="utf-8")
            (folder / "sub").mkdir()
            (folder / "sub" / "b.txt").write_text("hola b", encoding="utf-8")
            paths, temp_paths = p2p.bundle_if_is_directory([folder])
            self.assertEqual(len(paths), 1)
            self.assertTrue(paths[0].name.endswith(".zip"))
            with zipfile.ZipFile(paths[0], "r") as z:
                names = z.namelist()
                self.assertIn("carpeta_prueba/a.txt", names)
                self.assertIn("carpeta_prueba/sub/b.txt", names)
            for t in temp_paths:
                t.unlink(missing_ok=True)

    def test_offline_local_ip_detection_prioritizes_interfaces(self):
        # Without being able to read the interfaces (or their names) it falls back to the computer's IPs,
        # minus the virtual ones by range.
        with mock.patch("socket.socket.connect", side_effect=OSError("Sin ruta a internet")), \
             mock.patch("network.interfaces", return_value=None), \
             mock.patch("socket.getaddrinfo", return_value=[
                 (2, 1, 0, "", ("172.28.0.1", 0)),   # virtual WSL
                 (2, 1, 0, "", ("172.20.10.2", 0)),  # iOS hotspot
                 (2, 1, 0, "", ("127.0.0.1", 0)),    # Loopback
             ]):
            ip = bridge.ip_local()
            self.assertEqual(ip, "172.20.10.2")

    def test_history_copy_and_clean(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "prueba_copiar.txt"
            f.write_text("contenido copiado", encoding="utf-8")
            bridge.STATE["history"] = [
                {"id": "h1", "type": "received", "origin": "iPhone", "detail": "prueba_copiar.txt", "time": "12:00", "size": 16, "size_text": "16 B", "path": str(f)}
            ]
            server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                # Copy
                with mock.patch.object(bridge.so, "set_files") as mock_set:
                    body = p2p.json.dumps({"id": "h1"}).encode("utf-8")
                    conn.request("POST", "/app/copy-history", body=body, headers={"Content-Type": "application/json"})
                    resp = conn.getresponse()
                    self.assertEqual(resp.status, 200)
                    mock_set.assert_called_once()

                # Clear
                conn.request("POST", "/app/clear-history", body=b"{}", headers={"Content-Type": "application/json"})
                resp2 = conn.getresponse()
                self.assertEqual(resp2.status, 200)
                self.assertEqual(len(bridge.STATE["history"]), 0)
                conn.close()
            finally:
                server.shutdown()
                server.server_close()

    def test_upload_web_dragged_files(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
            body = (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file_0"; filename="arrastrado.txt"\r\n'
                "Content-Type: text/plain\r\n\r\n"
                "Contenido arrastrado al radar\r\n"
                f"--{boundary}--\r\n"
            ).encode("utf-8")
            headers = {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            }
            conn.request("POST", "/app/upload-dragged-files", body=body, headers=headers)
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            data = p2p.json.loads(resp.read().decode("utf-8"))
            self.assertTrue(len(data["files"]) == 1)
            self.assertTrue(data["files"][0]["name"].startswith("arrastrado"))
            conn.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_panel_local_paths_in_linux(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as td:
                file = Path(td) / "foto.jpg"
                file.write_bytes(b"x" * 10)
                folder = Path(td) / "Fotos"
                folder.mkdir()
                (folder / "a.txt").write_bytes(b"y" * 5)
                body = p2p.json.dumps({"paths": [str(file), str(folder), str(Path(td) / "no-existe")]})
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("POST", "/app/local-paths", body=body, headers={"Content-Type": "application/json"})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                data = p2p.json.loads(resp.read().decode("utf-8"))
                conn.close()
            self.assertEqual([(a["name"], a["size"]) for a in data["files"]], [("foto.jpg", 10), ("Fotos", 5)])
            self.assertTrue(data["files"][1]["is_directory"])
        finally:
            server.shutdown()
            server.server_close()

if __name__ == "__main__":
    unittest.main()

