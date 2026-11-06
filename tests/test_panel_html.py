"""Smoke test of the panel and the quick view: the JS only calls routes that exist and only touches ids that
are in the HTML."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import web_ui

SERVER_FONT = "".join((Path(__file__).resolve().parents[1] / name).read_text(encoding="utf-8")
                          for name in ("bridge.py", "panel_routes.py"))


def called_paths(html: str) -> set[str]:
    return set(re.findall(r"""fetch\(\s*['"`](/[a-z\-]+(?:/[a-z\-]+)*)""", html))


def used_ids(html: str) -> set[str]:
    return set(re.findall(r"""getElementById\(\s*['"]([\w\-]+)['"]\s*\)""", html))


def defined_ids(html: str) -> set[str]:
    return set(re.findall(r"""\sid=["']([\w\-]+)["']""", html))


class PanelHtmlTests(unittest.TestCase):
    def test_each_path_the_panel_calls_exists_in_server(self):
        for name, html in (("panel", web_ui.HTML_APP), ("vista rápida", web_ui.PREVIEW_PAGE)):
            paths = called_paths(html)
            self.assertTrue(paths, f"{name}: no se encontraron llamadas fetch")
            missing = [r for r in paths if f'"{r}"' not in SERVER_FONT and f"'{r}'" not in SERVER_FONT]
            self.assertEqual(missing, [], f"{name}: rutas que el servidor no atiende")

    def test_each_id_the_js_touches_is_in_html(self):
        for name, html in (("panel", web_ui.HTML_APP), ("vista rápida", web_ui.PREVIEW_PAGE)):
            missing = sorted(used_ids(html) - defined_ids(html))
            self.assertEqual(missing, [], f"{name}: getElementById sin elemento")

    def test_without_emojis_nor_old_green_in_panel(self):
        for html in (web_ui.HTML_APP, web_ui.PREVIEW_PAGE, web_ui.CONFIGURE_PAGE):
            self.assertNotRegex(html, r"[\U0001F300-\U0001FAFF⭐⚡]", "quedó un emoji")
            self.assertNotIn("#10b981", html)
            self.assertNotIn("rgba(16, 185, 129", html)

    def test_quick_view_can_pause_from_python(self):
        view = web_ui.PREVIEW_PAGE
        self.assertIn("window.copyoverVisible = function", view)
        self.assertNotIn("panel=1", view)
        self.assertIn("'/app/state?panel=1", web_ui.HTML_APP)

    def test_transfer_bar_says_with_who_and_towards_where(self):
        import i18n
        self.assertIn("`Sending to ${tr.peer}`", web_ui.HTML_APP)
        self.assertIn("`Receiving from ${tr.peer}`", web_ui.HTML_APP)
        es = i18n.translate_html(web_ui.HTML_APP, "es")
        self.assertIn("`Enviando a ${tr.peer}`", es)

    def test_settings_has_button_for_re_default_folder(self):
        html = web_ui.HTML_APP
        self.assertIn('onclick="resetDownloadsFolder()"', html)
        self.assertIn("p.download_folder_is_default ? 'none'", html)
        import i18n
        self.assertIn(">Reset</button>", html)
        self.assertNotIn(">Reset</button>", i18n.translate_html(html, "es"))

    def test_equal_panel_version_in_html_and_server(self):
        local = re.search(r"VERSION_LOCAL = (\d+)", web_ui.HTML_APP)[1]
        server = re.search(r'"app_version": (\d+)', SERVER_FONT)[1]
        self.assertEqual(local, server)

    def test_each_phone_tab_finds_its_panel_and_button(self):
        html = web_ui.HTML_APP
        tabs = re.search(r"for \(const t of \[([^\]]+)\]\)", html[html.index("function changePhoneTab"):])
        names = re.findall(r"'(\w+)'", tabs[1])
        self.assertEqual(sorted(names), sorted(re.findall(r"""onclick="changePhoneTab\('(\w+)'\)\"""", html)))
        for name in names:
            self.assertIn(f'id="panel-phone-{name}"', html)
            self.assertIn(f'id="tab-btn-{name}"', html)

    def test_guest_keeps_the_name_saved_under_the_old_key(self):
        guest = (Path(__file__).resolve().parents[1] / "web" / "guest.js").read_text(encoding="utf-8")
        self.assertIn("localStorage.getItem('copyover_my_name') || localStorage.getItem('airbridge_mi_nombre')", guest)
        self.assertIn("saveLocal('copyover_my_name'", guest)

    def test_device_card(self):
        html = web_ui.HTML_APP
        self.assertIn("currentState.network_name", html)  # the phone sees the PC by its network name
        self.assertNotIn("|| 'vinculado'", html)
        import i18n
        self.assertNotIn("Desktop PC", i18n.translate_html(html, "es"))  # nothing left in English
        # «Otra red» never beats a device that answers
        self.assertIn("const network = d.online ? 'same'", html)
        self.assertIn('class="modal-footer"', html)  # Save changes always in view

    def test_releasing_a_drag_outside_does_not_close_the_window(self):
        # Selecting text and dropping it on the backdrop does not close: the click has to start on the backdrop.
        backgrounds = re.findall(r'<div class="modal-backdrop"[^>]*>', web_ui.HTML_APP)
        self.assertTrue(backgrounds)
        for background in backgrounds:
            if 'onclick' in background:
                self.assertIn('onmousedown="this.atBottom=event.target===this"', background)
                self.assertIn('this.atBottom)', background)


if __name__ == "__main__":
    unittest.main()
