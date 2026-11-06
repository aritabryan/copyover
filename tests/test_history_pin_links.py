"""History: pinning entries, searching in the panel and opening the http(s) links that come from the phone."""
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import links
import web_ui
import bridge


class WebLinkTests(unittest.TestCase):
    def test_accepts_only_http_link_or_https(self):
        self.assertEqual(links.web_link("https://example.com/a?b=1&c=2#d"), "https://example.com/a?b=1&c=2#d")
        self.assertEqual(links.web_link("  http://192.168.1.5:8080/x \n"), "http://192.168.1.5:8080/x")
        self.assertEqual(links.web_link("HTTPS://Example.com"), "HTTPS://Example.com")

    def test_rejects_other_schemes_and_texts_that_are_not_just_a_link(self):
        for text in ("file:///C:/Windows/System32/calc.exe", "javascript:alert(1)", "data:text/html,<b>x</b>",
                      "ftp://example.com/x", "ms-settings:privacy", "mailto:a@b.c", "\\\\servidor\\carpeta",
                      "www.example.com", "see https://example.com", "https://example.com something else",
                      "https://", "http:///solo-ruta", "https://exa\x00mple.com", "https://example.com:99999",
                      'https://example.com/"&calc', "https://example.com/<script>", "https://example.com/`x`",
                      "https://" + "a" * 3000 + ".com", "", None, 42):
            self.assertEqual(links.web_link(text), "", repr(text)[:60])

    def test_open_in_browser_only_opens_http(self):
        with patch.object(links.webbrowser, "open", return_value=True) as open_stream:
            self.assertFalse(links.open_in_browser("file:///C:/Windows/notepad.exe"))
            self.assertFalse(links.open_in_browser("javascript:alert(1)"))
            open_stream.assert_not_called()
            self.assertTrue(links.open_in_browser("https://example.com"))
            open_stream.assert_called_once_with("https://example.com")


class BaseHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.cfg = {"keep_days": 0}
        self.patches = [
            patch.object(bridge, "HISTORY", self.base / "history.json"),
            patch("bridge.load_config", side_effect=lambda: self.cfg),
            patch("bridge.get_downloads_folder", side_effect=self._folder),
            patch.object(bridge.so, "to_trash", side_effect=self._to_trash),
        ]
        self.to_trash = []
        for p in self.patches:
            p.start()
        self.before = bridge.STATE["history"]
        bridge.STATE["history"] = []
        bridge.STATE["history_persistent"] = True

    def tearDown(self):
        for p in self.patches:
            p.stop()
        bridge.STATE["history"] = self.before
        bridge.STATE["history_persistent"] = False
        self.tmp.cleanup()

    def _folder(self, kind="iphone"):
        folder = self.base / {"iphone": "From iPhone", "p2p": "From another PC", "guests": "From guests"}.get(kind, "base")
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _to_trash(self, paths):
        for path in paths:
            self.to_trash.append(Path(path).name)
            Path(path).unlink()
        return True

    def _file(self, name, days_ago):
        path = self._folder() / name
        path.write_bytes(b"x")
        old = time.time() - days_ago * 86400
        os.utime(path, (old, old))
        return path

    def saved(self):
        return json.loads(bridge.HISTORY.read_text(encoding="utf-8"))


class PinTests(BaseHistory):
    def test_pin_saves_in_file_and_survives_to_restart(self):
        bridge.register_history("received", "Mi iPhone", "Texto: importante")
        id_item = bridge.STATE["history"][0]["id"]
        self.assertTrue(bridge.pin_history(id_item, True))
        self.assertTrue(self.saved()[0]["pinned"])
        self.assertFalse(bridge.HISTORY.with_name("history.json.tmp").exists(), "escritura atómica")
        bridge.STATE["history"] = []
        bridge.load_history()
        self.assertTrue(bridge.STATE["history"][0]["pinned"])
        self.assertTrue(bridge.pin_history(id_item, False))
        self.assertNotIn("pinned", self.saved()[0])
        self.assertFalse(bridge.pin_history("no-existe", True))

    def test_history_limit_keeps_pinned_entries(self):
        bridge.register_history("received", "Mi iPhone", "Texto: el más viejo")
        old = bridge.STATE["history"][0]["id"]
        bridge.pin_history(old, True)
        for i in range(bridge.MAX_HISTORY + 15):
            bridge.register_history("received", "Mi iPhone", f"Texto: {i}")
        ids = [e["id"] for e in bridge.STATE["history"]]
        self.assertIn(old, ids)
        self.assertEqual(len(ids), bridge.MAX_HISTORY + 1, "unpinned ones keep their limit")
        # Loading from disk trims the same way.
        bridge.STATE["history"] = []
        bridge.load_history()
        self.assertIn(old, [e["id"] for e in bridge.STATE["history"]])

    def test_unpin_re_apply_limit(self):
        for i in range(bridge.MAX_HISTORY):
            bridge.register_history("received", "Mi iPhone", f"Texto: {i}")
        last = bridge.STATE["history"][-1]["id"]
        bridge.pin_history(last, True)
        bridge.register_history("received", "Mi iPhone", "Texto: nuevo")
        self.assertEqual(len(bridge.STATE["history"]), bridge.MAX_HISTORY + 1)
        bridge.pin_history(last, False)
        self.assertEqual(len(bridge.STATE["history"]), bridge.MAX_HISTORY)
        self.assertNotIn(last, [e["id"] for e in bridge.STATE["history"]])

    def test_pinned_exits_first(self):
        for name in ("a", "b", "c"):
            bridge.register_history("received", "Mi iPhone", f"Texto: {name}")
        bridge.pin_history(bridge.STATE["history"][2]["id"], True)  # «a», the oldest
        order = [e["detail"] for e in bridge.sorted_history(bridge.STATE["history"])]
        self.assertEqual(order, ["Texto: a", "Texto: c", "Texto: b"])

    def test_cleanup_keeps_pinned_files_and_entries(self):
        self.cfg["keep_days"] = 7
        pinned = self._file("contract.pdf", 30)
        with_it = self._file("annex.pdf", 30)
        loose = self._file("old.jpg", 30)
        bridge.register_history("received", "Mi iPhone", "contract.pdf, annex.pdf", local_path=str(pinned))
        bridge.STATE["history"][0]["timestamp"] = time.time() - 30 * 86400
        bridge.pin_history(bridge.STATE["history"][0]["id"], True)
        bridge.register_history("received", "Mi iPhone", "old.jpg", local_path=str(loose))
        bridge.STATE["history"][0]["timestamp"] = time.time() - 30 * 86400
        self.assertEqual(bridge.clean_old_received(), 1)
        self.assertEqual(self.to_trash, ["old.jpg"])
        self.assertTrue(pinned.exists())
        self.assertTrue(with_it.exists(), "los demás archivos de la misma entrada también se quedan")
        self.assertEqual([e["detail"] for e in bridge.STATE["history"]], ["contract.pdf, annex.pdf"])

    def test_cleanup_keeps_a_pinned_entry_without_its_file(self):
        self.cfg["keep_days"] = 1
        bridge.register_history("received", "Mi iPhone", "photo.jpg", local_path=str(self.base / "ya-no-esta.jpg"))
        bridge.STATE["history"][0]["timestamp"] = time.time() - 5 * 86400
        bridge.pin_history(bridge.STATE["history"][0]["id"], True)
        self._file("other.jpg", 5)  # so the cleanup runs
        bridge.clean_old_received()
        self.assertEqual(len(bridge.STATE["history"]), 1)

    def test_empty_list_leaves_pinned(self):
        for name in ("a", "b"):
            bridge.register_history("received", "Mi iPhone", f"Texto: {name}")
        bridge.pin_history(bridge.STATE["history"][1]["id"], True)
        self.assertEqual(bridge.empty_history(), 1)
        self.assertEqual([e["detail"] for e in self.saved()], ["Texto: a"])


class ReceivedLinksTests(BaseHistory):
    def test_muting_a_copied_image_does_not_mute_received_files(self):
        handler = object.__new__(bridge.Handler)
        handler.notices = True
        handler.client_address = ("127.0.0.1", 0)
        handler.headers = {}
        for name, kind, content, notices in (
            ("photo.png", "image/png", b"\x89PNG\r\n\x1a\ncontent", 0),
            ("file.zip", "application/zip", b"PK\x03\x04content", 1),
        ):
            with patch.object(bridge.so, "set_image"), patch.object(bridge.so, "set_files"), \
                    patch.object(bridge, "notify_received") as notice:
                handler.receive([(name, kind, content)])
            self.assertEqual(notice.call_count, notices)



    def _receive_text(self, text):
        with patch.object(bridge.so, "set_text"), patch.object(bridge.so, "notify") as notice:
            handler = object.__new__(bridge.Handler)
            handler.notices = True
            handler.client_address = ("127.0.0.1", 0)
            handler.headers = {}
            handler.receive([("", "text/plain", text.encode("utf-8"))])
        return notice

    def test_phone_link_remains_complete_in_history_and_notice_offers_open(self):
        self.cfg["clipboard_notifications"] = True
        url = "https://example.com/una/ruta/bastante/larga/para/pasar/de/40?caracteres=si"
        notice = self._receive_text(url)
        entry = bridge.STATE["history"][0]
        self.assertEqual(entry["link"], url)
        self.assertEqual(notice.call_args.args[0], "Enlace copiado")
        self.assertEqual(notice.call_args.kwargs, {"link": url})
        self.assertIn(url[:60], notice.call_args.args[1])

    def test_plain_text_or_dangerous_link_does_not_offer_open(self):
        self.cfg["clipboard_notifications"] = True
        for text in ("hello, see this: https://example.com", "javascript:alert(1)", "file:///C:/x.exe"):
            notice = self._receive_text(text)
            self.assertNotIn("link", bridge.STATE["history"][0], text)
            self.assertEqual(notice.call_args.kwargs, {}, text)
            self.assertEqual(notice.call_args.args[0], "Texto copiado")
            self.assertEqual(notice.call_args.args[1], text[:80], "the body is only what arrived")


@unittest.skipUnless(sys.platform == "win32", "los avisos toast son de Windows")
class WindowsNoticeTests(unittest.TestCase):
    def notify(self, *args, **kwargs):
        """What the helper receives: the notification's JSON (no thread, so it can be read right away)."""
        import base64
        import windows_system

        class ImmediateThread:
            def __init__(self, target, args, daemon):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        with patch.object(windows_system, "helper") as helper,                 patch.object(windows_system, "_register_notices_id", return_value=True),                 patch.object(windows_system, "_register_notice_protocol", return_value=True),                 patch.object(windows_system.threading, "Thread", ImmediateThread):
            windows_system.notify(*args, **kwargs)
        mode, encrypted = helper.call_args.args
        self.assertEqual(mode, "notify")
        return json.loads(base64.b64decode(encrypted))

    def test_notice_passes_link_and_button_only_if_is_http(self):
        import windows_system

        data = self.notify("Enlace recibido", "https://example.com", link="https://example.com", note="Ya está")
        self.assertEqual(data, {"title": "Enlace recibido", "text": "https://example.com", "note": "Ya está",
                                 "app": windows_system.NOTICES_ID, "launch": "https://example.com",
                                 "buttons": [["Abrir", "https://example.com"]]})
        data = self.notify("Texto", "file:///C:/x.exe", link="file:///C:/x.exe")
        self.assertNotIn("buttons", data, "sin enlace ni botón")
        self.assertNotIn("launch", data)

    def test_quotes_and_line_breaks_travel_integers(self):
        data = self.notify('Dijo "hola"', "una\nlínea y otra")
        self.assertEqual((data["title"], data["text"]), ('Dijo "hola"', "una\nlínea y otra"))

    def test_received_file_open_or_shows_only_with_notice_code(self):
        import windows_system

        with tempfile.TemporaryDirectory() as folder:
            photo = Path(folder) / "photo.jpg"
            photo.write_bytes(b"x")
            data = self.notify("Foto de Ana", "photo.jpg", file=photo)
            self.assertEqual([b[0] for b in data["buttons"]], ["Abrir", "Mostrar en carpeta"])
            open_stream, dir_folder = (b[1] for b in data["buttons"])
            self.assertEqual(data["launch"], open_stream)
            code, action = open_stream.removeprefix("copyover-notice:").split("/")
            self.assertEqual(action, "open")
            self.assertGreaterEqual(len(code), 20, "a code that can't be guessed")
            with patch.object(windows_system, "open_stream") as opens, patch.object(windows_system, "highlight_in_explorer") as shows:
                self.assertTrue(windows_system.serve_notice(code, "open"))
                opens.assert_called_once_with(photo.resolve())
                # What the button really asks for, not a value written here by hand.
                self.assertTrue(windows_system.serve_notice(code, dir_folder.rsplit("/", 1)[1]))
                shows.assert_called_once_with(photo.resolve())
                self.assertFalse(windows_system.serve_notice("inventado", "open"))
                self.assertFalse(windows_system.serve_notice(code, "borrar"))
                self.assertEqual(opens.call_count, 1)

    def test_what_runs_only_shows_in_folder(self):
        import windows_system

        with tempfile.TemporaryDirectory() as folder:
            program = Path(folder) / "instalar.EXE"
            program.write_bytes(b"MZ")
            for kwargs in ({"file": program}, {"file": Path(folder) / "a.txt", "open_file": False}):
                data = self.notify("Archivo", "x", **kwargs)
                self.assertEqual([b[0] for b in data["buttons"]], ["Mostrar en carpeta"], kwargs)
                self.assertTrue(data["launch"].endswith("/folder"))
            code = self.notify("Archivo", "x", file=program)["launch"].removeprefix("copyover-notice:").split("/")[0]
            with patch.object(windows_system, "open_stream") as opens, patch.object(windows_system, "highlight_in_explorer") as shows:
                windows_system.serve_notice(code, "open")
            opens.assert_not_called()
            shows.assert_called_once()


class NoticesHelperTests(unittest.TestCase):
    def test_scheme_valid_helper_before_set_button(self):
        helper = (Path(__file__).resolve().parents[1] / "helper.ps1").read_text(encoding="utf-8")
        self.assertIn("activationType', 'protocol'", helper)
        self.assertIn("$uri.Scheme -eq 'http' -or $uri.Scheme -eq 'https' -or $uri.Scheme -eq 'copyover-notice'", helper)
        # Texts go in as nodes, never pasted into the XML (a title with < or & would break it).
        self.assertIn("CreateTextNode($line)", helper)


class PanelPathsTests(BaseHistory):
    def test_deleting_a_pinned_entry_keeps_its_file_and_other_entries(self):
        file = self._file("document.txt", 0)
        bridge.register_history("received", "Mi PC", "document.txt", local_path=str(file))
        identifier = bridge.STATE["history"][0]["id"]
        bridge.pin_history(identifier, True)
        bridge.register_history("received", "Mi PC", "Texto: keep")
        state, response = self.request("POST", "/app/delete-history", {"id": identifier})
        self.assertEqual(state, 200)
        self.assertTrue(response["ok"])
        self.assertEqual([h["detail"] for h in self.saved()], ["Texto: keep"])
        bridge.STATE["history"] = []
        bridge.load_history()
        self.assertEqual([h["detail"] for h in bridge.STATE["history"]], ["Texto: keep"])
        self.assertTrue(file.exists())
        self.assertEqual(self.to_trash, [])


    def test_delete_rejects_invalid_or_missing_id(self):
        bridge.register_history("received", "Mi PC", "Texto: keep")
        for identifier in (None, "", [], {}):
            self.assertEqual(self.request("POST", "/app/delete-history", {"id": identifier})[0], 400)
        self.assertEqual(self.request("POST", "/app/delete-history", {"id": "missing"})[0], 404)
        self.assertEqual(len(bridge.STATE["history"]), 1)



    @classmethod
    def setUpClass(cls):
        cls.server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, data=None):
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = json.dumps(data).encode() if data is not None else None
        con.request(method, path, body=body, headers={"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"})
        response = con.getresponse()
        result = response.status, json.loads(response.read() or b"null")
        con.close()
        return result

    def _thumbnail(self, name, content):
        path = self._folder() / name
        path.write_bytes(content)
        bridge.register_history("received", "Mi iPhone", name, len(content), str(path))
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        con.request("GET", f"/app/history/image?id={bridge.STATE['history'][0]['id']}",
                    headers={"Host": f"127.0.0.1:{self.port}"})
        response = con.getresponse()
        response.read()
        con.close()
        return response

    def test_thumbnail_does_not_serve_a_received_svg(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        self.assertEqual(self._thumbnail("dibujo.svg", svg).status, 404)

    def test_photo_thumbnail_exits_without_can_run_nothing(self):
        response = self._thumbnail("photo.png", bytes([137]) + b"PNG" + b"0" * 40)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "image/png")
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertIn("sandbox", " ".join(response.headers.get_all("Content-Security-Policy")))

    def test_pin_from_panel_and_history_exits_with_first_pinned(self):
        for name in ("a", "b"):
            bridge.register_history("received", "Mi iPhone", f"Texto: {name}")
        id_a = bridge.STATE["history"][1]["id"]
        self.assertEqual(self.request("POST", "/app/pin-history", {"id": id_a, "pinned": True})[0], 200)
        state, items = self.request("GET", "/app/history")
        self.assertEqual([(h["detail"], h.get("pinned", False)) for h in items], [("Texto: a", True), ("Texto: b", False)])
        self.assertEqual(self.request("POST", "/app/pin-history", {"id": "none", "pinned": True})[0], 404)
        state, d = self.request("POST", "/app/clear-history", {})
        self.assertEqual(d["remaining"], 1)

    def test_open_history_link_sends_to_browser(self):
        bridge.register_history("received", "Mi iPhone", "Texto: https://example.com/x", link="https://example.com/x")
        id_item = bridge.STATE["history"][0]["id"]
        with patch.object(links.webbrowser, "open", return_value=True) as open_stream:
            state, _ = self.request("POST", "/app/open-history", {"id": id_item, "folder": False})
        self.assertEqual(state, 200)
        open_stream.assert_called_once_with("https://example.com/x")
        # Even if someone edits history.json by hand, a scheme that isn't http(s) isn't opened.
        bridge.STATE["history"][0]["link"] = "file:///C:/Windows/notepad.exe"
        with patch.object(links.webbrowser, "open") as open_stream:
            state, _ = self.request("POST", "/app/open-history", {"id": id_item, "folder": False})
        self.assertEqual(state, 400)
        open_stream.assert_not_called()

    def test_copy_complete_url_copy_link(self):
        url = "https://example.com/" + "x" * 80
        bridge.register_history("received", "Mi iPhone", f"Texto: {url[:40]}", link=url)
        with patch.object(bridge.so, "set_text") as put:
            self.request("POST", "/app/copy-history", {"id": bridge.STATE["history"][0]["id"]})
        put.assert_called_once_with(url)


class PanelTests(unittest.TestCase):
    def test_panel_has_finder_pin_and_open_links(self):
        html = web_ui.HTML_APP
        self.assertIn('id="hist-search"', html)
        self.assertIn("oninput=\"paintHistory()\"", html)
        self.assertIn("/app/pin-history", html)
        self.assertIn("Nothing matches “${esc(searched)}”", html, "la búsqueda escapa lo escrito")
        self.assertIn("h.path || isLink", web_ui.PREVIEW_PAGE, "la vista rápida también abre enlaces")
        for old_key in ("h.ruta", "h.detalle", "h.momento", "h.origen", "h.enlace", "h.existe", "h.tamano_texto"):
            self.assertNotIn(old_key, web_ui.PREVIEW_PAGE, "the history arrives with English keys")


if __name__ == "__main__":
    unittest.main()
