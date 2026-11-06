"""The quick view: where it's placed, how it's launched and what the server gives it."""
import http.client
import json
import re
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bridge
import panel_routes
import preview


class MenuDevicesTests(unittest.TestCase):
    def test_includes_offline_linked_and_excludes_foreign_pcs_and_credentials(self):
        cfg = {'devices': {'mobile': {'name': 'Mi Android', 'type': 'android', 'key_hash': 'privado'}},
               'pcs': {'pc': 'clave-privada'}, 'pcs_info': {'pc': {'alias': 'Mi laptop'}}}
        listing = panel_routes.devices_for_tray(cfg, [{'id': 'ajena', 'name': 'Otra PC'}])
        self.assertEqual(listing, [{'name': 'Mi Android', 'type': 'android'}, {'name': 'Mi laptop', 'type': 'pc'}])
        self.assertNotIn('privad', json.dumps(listing))

    def test_initial_view_shows_history_and_sends_without_submenu(self):
        import web_ui
        html = web_ui.PREVIEW_PAGE
        history = re.search(r'<main\b[^>]*id="list"[^>]*>', html).group()
        self.assertNotIn('hidden', history)
        footer = re.search(r'<footer\b[^>]*>(.*?)</footer>', html, re.S).group()
        self.assertNotIn('hidden', footer)
        for action in ('send_phone', 'send_pc', 'open_panel', 'open_folder'):
            self.assertIn(f"action('{action}')", footer)


class PositionTests(unittest.TestCase):
    def test_pastes_to_work_area_right_lower_corner(self):
        self.assertEqual(preview.position((0, 0, 1920, 1032), width=380, height=560, margin=12), (1528, 460))

    def test_stays_on_a_small_screen(self):
        self.assertEqual(preview.position((0, 0, 300, 400), width=380, height=560, margin=12), (0, 0))

    def test_about_icon_remains_centered_and_with_lower_border_on_top_click(self):
        area = (0, 0, 2560, 1392)
        self.assertEqual(preview.position_over((2400, 1380), area, width=320, height=420, margin=10), (2230, 950))
        # Against the right edge it doesn't go off screen, and with the taskbar on top it opens downward.
        self.assertEqual(preview.position_over((2550, 1380), area, width=320, height=420, margin=10), (2230, 950))
        self.assertEqual(preview.position_over((100, 20), area, width=320, height=420, margin=10), (10, 30))

    def test_show_sends_click_point(self):
        process = Mock()
        process.poll.return_value = None
        process.pid = 1234
        launcher = preview.Launcher(8765, executable="pythonw", popen=Mock(return_value=process))
        launcher._show((1500, 1380))
        self.assertEqual(process.stdin.write.call_args.args, (b"mostrar 1500 1380\n",))

    def test_warm_up_launches_without_show(self):
        process = Mock()
        process.poll.return_value = None
        popen = Mock(return_value=process)
        launcher = preview.Launcher(8765, executable="pythonw", popen=popen)
        launcher.warm_up()
        launcher.warm_up()
        self.assertEqual(popen.call_count, 1)
        process.stdin.write.assert_not_called()


class LauncherTests(unittest.TestCase):
    def test_starts_process_once_and_asks_show(self):
        process = Mock()
        process.poll.return_value = None
        popen = Mock(return_value=process)
        launcher = preview.Launcher(8765, executable="pythonw", popen=popen)
        launcher.show()
        launcher.show()
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(popen.call_args.args[0][:1] + popen.call_args.args[0][2:3], ["pythonw", "8765"])
        self.assertTrue(process.stdin.write.call_args_list[0].args[0].startswith(b"mostrar"))
        self.assertEqual(process.stdin.write.call_count, 2)

    def test_if_process_died_re_launch(self):
        dead, alive = Mock(), Mock()
        dead.poll.return_value = 1
        alive.poll.return_value = None
        popen = Mock(side_effect=[dead, alive])
        launcher = preview.Launcher(8765, executable="pythonw", popen=popen)
        launcher.show()
        launcher.show()
        self.assertEqual(popen.call_count, 2)

    def test_close_sends_exit(self):
        process = Mock()
        process.poll.return_value = None
        launcher = preview.Launcher(8765, executable="pythonw", popen=Mock(return_value=process))
        launcher.show()
        launcher.close()
        self.assertEqual(process.stdin.write.call_args.args, (b"salir\n",))
        self.assertIsNone(launcher.process)


class PreviewServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, body=None, host=None):
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        con.request(method, path, body=body, headers={"Host": host or f"127.0.0.1:{self.port}", "Content-Type": "application/json"})
        response = con.getresponse()
        data = response.read()
        con.close()
        return response.status, data

    def test_page_only_serves_to_local_panel(self):
        state, html = self.request("GET", "/app/preview")
        self.assertEqual(state, 200)
        self.assertIn("CopyOver", html.decode("utf-8"))
        self.assertEqual(self.request("GET", "/app/preview", host="otro.local:8765")[0], 403)

    def test_actions_are_tray(self):
        done = threading.Event()
        bridge.Handler.actions = {"open_panel": done.set}
        try:
            state, body = self.request("POST", "/app/action", json.dumps({"action": "open_panel"}))
            self.assertEqual((state, json.loads(body)["ok"]), (200, True))
            self.assertTrue(done.wait(2))
            self.assertEqual(self.request("POST", "/app/action", json.dumps({"action": "formatear_disco"}))[0], 404)
        finally:
            bridge.Handler.actions = {}

    @unittest.skipUnless(sys.platform == 'win32', 'Inicio de Windows')
    def test_autostart_validates_boolean_and_confirms_real_state(self):
        change = Mock()
        with patch.object(bridge, '_automatic_startup', return_value=(Mock(), change)), \
                patch.object(bridge.so, 'installed', return_value=True):
            state, body = self.request('POST', '/app/autostart', json.dumps({'enable': True}))
            self.assertEqual((state, json.loads(body)['ok']), (200, True))
            change.assert_called_once_with(True)
            change.reset_mock()
            self.assertEqual(self.request('POST', '/app/autostart', json.dumps({'enable': 'false'}))[0], 400)
            change.assert_not_called()

    @unittest.skipUnless(sys.platform == 'win32', 'Inicio de Windows')
    def test_autostart_does_not_report_success_if_windows_did_not_apply_it(self):
        with patch.object(bridge, '_automatic_startup', return_value=(Mock(), Mock())), \
                patch.object(bridge.so, 'installed', return_value=False):
            state, body = self.request('POST', '/app/autostart', json.dumps({'enable': True}))
            self.assertEqual(state, 200)
            self.assertFalse(json.loads(body)['ok'])


class OpenPanelTests(unittest.TestCase):
    def test_quick_view_is_not_taken_for_the_panel(self):
        import dialogs

        self.assertFalse(dialogs.is_window_of(preview.WINDOW_TITLE))
        self.assertTrue(dialogs.is_window_of("CopyOver"))
        # The browser CopyOver launched counts by its process, even if the title isn't exact.
        self.assertTrue(dialogs.is_window_of("CopyOver - Brave", pid=4321, pids={4321}))
        self.assertFalse(dialogs.is_window_of(preview.WINDOW_TITLE, pid=4321, pids={4321}))

    def test_foreign_windows_with_copyover_in_the_title_are_not_the_panel(self):
        import dialogs

        foreign = ["CopyOver - File Explorer", "CopyOver - Explorador de archivos",
                  "usuario/proyecto: CopyOver - Brave", "bridge.py - CopyOver - Visual Studio Code",
                  "puente.log: Bloc de notas", "Linked computers — CopyOver", "Equipos vinculados — CopyOver",
                  "CopyOver.log - Notepad"]
        for title in foreign:
            with self.subTest(title=title):
                self.assertFalse(dialogs.is_window_of(title, pid=999, pids={4321}))

    def test_selector_finds_by_translated_title(self):
        import dialogs
        import i18n

        try:
            i18n.pin("en")
            title = dialogs.files_selector_title()
            self.assertEqual(title, "Select files — CopyOver")
            self.assertTrue(dialogs.is_window_of(title, title))
            self.assertFalse(dialogs.is_window_of("Seleccionar archivos — CopyOver", title))
        finally:
            i18n.pin("es")


if __name__ == "__main__":
    unittest.main()
