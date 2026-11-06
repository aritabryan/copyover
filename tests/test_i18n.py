"""English and Spanish: language detection, t() and page translation."""
import ast
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import i18n  # noqa: E402
import web_ui  # noqa: E402
import strings_es  # noqa: E402

PAGES = {
    "panel": web_ui.HTML_APP,
    "vista rápida": web_ui.PREVIEW_PAGE,
    "configurar": web_ui.CONFIGURE_PAGE,
    "QR vencido": web_ui.EXPIRED_QR_PAGE,
    "guest": web_ui.GUEST_PAGE,
}


def page_keys():
    """The dictionary's keys with their whitespace normalized, the way extract_texts finds them in a page."""
    return {" ".join(k.split()) for k in strings_es.ES}


def t_keys():
    """(file, text) of every t("...") with a literal text in the code."""
    for file in sorted(ROOT.glob("*.py")):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name == "t" and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                yield file.name, node.args[0].value


class Detection(unittest.TestCase):
    def test_detect_respects_config(self):
        self.assertEqual(i18n.detect({"language": "en"}), "en")
        self.assertEqual(i18n.detect({"language": "es"}), "es")

    def test_without_chosen_language_is_english(self):
        self.assertEqual(i18n.detect({}), "en")
        self.assertEqual(i18n.detect(None), "en")
        self.assertEqual(i18n.detect({"language": "fr"}), "en")


class Translation(unittest.TestCase):
    def tearDown(self):
        i18n.pin("es")

    def test_language_variable_and_its_old_name(self):
        for variable in ("COPYOVER_LANGUAGE", "COPYOVER_IDIOMA"):
            others = {k: v for k, v in i18n.os.environ.items() if k not in ("COPYOVER_LANGUAGE", "COPYOVER_IDIOMA")}
            with unittest.mock.patch.dict(i18n.os.environ, {**others, variable: "en"}, clear=True), \
                    unittest.mock.patch.object(i18n, "_current", None):
                self.assertEqual(i18n.current(), "en", variable)

    def test_t_formats_arguments(self):
        i18n.pin("en")
        self.assertEqual(i18n.t("Received: {name}", name="foto.jpg"), "Received: foto.jpg")
        i18n.pin("es")
        with unittest.mock.patch.dict(strings_es.ES, {"Received: {name}": "Recibido: {name}"}):
            self.assertEqual(i18n.t("Received: {name}", name="foto.jpg"), "Recibido: foto.jpg")
        self.assertEqual(i18n.t("Text with no translation"), "Text with no translation")

    def test_unknown_language_falls_back_to_english(self):
        i18n.pin("fr")
        self.assertEqual(i18n.current(), "en")
        self.assertEqual(i18n.t("Open panel"), "Open panel")
        i18n.pin("es")
        self.assertEqual(i18n.t("Open panel"), "Abrir panel")

    def test_device_last_event_exits_in_chosen_language(self):
        i18n.pin("es")
        self.assertEqual(i18n.translate_event("Copiado a las 15:41"), "Copiado a las 15:41")
        i18n.pin("en")
        self.assertEqual(i18n.translate_event("Copiado a las 15:41"), "Copied at 15:41")
        self.assertEqual(i18n.translate_event("Envio a las 9:05"), "Sent at 9:05")
        self.assertEqual(i18n.translate_event("Conectado a las 08:00"), "Connected at 08:00")
        self.assertEqual(i18n.translate_event("Vinculado por QR a las 14:52"), "Linked by QR code at 14:52")
        self.assertEqual(i18n.translate_event("Vinculado el 28/10 a las 14:52"), "Linked on 28/10 at 14:52")
        self.assertEqual(i18n.translate_event("Guardado"), "Saved")
        # A known device with nothing to report: the card shows no event.
        self.assertEqual(i18n.translate_event("En red"), "")
        self.assertEqual(i18n.translate_event("On the network"), "")
        # What's stored now is in English and comes out in the chosen language too.
        self.assertEqual(i18n.translate_event("Sent from the app at 9:05"), "Sent from the app at 9:05")
        self.assertEqual(i18n.translate_event("Linked on 28/10 at 14:52"), "Linked on 28/10 at 14:52")
        self.assertEqual(i18n.translate_event("Linked"), "Linked")
        self.assertEqual(i18n.translate_event(""), "")

    def test_each_device_event_exits_with_accents_and_translated(self):
        cases = {
            "Envio a las 9:05": ("Envío a las 9:05", "Sent at 9:05"),
            "Traido a las 23:56": ("Descargó contenido a las 23:56", "Got something at 23:56"),
            "Texto copiado a las 10:00": ("Texto copiado a las 10:00", "Text copied at 10:00"),
            "Foto recibida a las 10:01": ("Foto recibida a las 10:01", "Photo received at 10:01"),
            "Archivo recibido a las 10:02": ("Archivo recibido a las 10:02", "File received at 10:02"),
            "Descarga en App a las 10:03": ("Descarga en la app a las 10:03", "Downloaded in the app at 10:03"),
            "App en vivo a las 10:04": ("En la app web a las 10:04", "In the web app at 10:04"),
            "Envio desde App a las 10:05": ("Envío desde la app a las 10:05", "Sent from the app at 10:05"),
        }
        for saved, (es, en) in cases.items():
            with self.subTest(event=saved):
                i18n.pin("es")
                self.assertEqual(i18n.translate_event(saved), es)
                i18n.pin("en")
                self.assertEqual(i18n.translate_event(saved), en)

    def test_history_shows_translated_origin(self):
        import panel_routes
        saved = [
            {"id": "1", "origin": "Portapapeles / iOS", "detail": "Texto: hola...", "name": "Texto: hola..."},
            {"id": "2", "origin": "Para iPhone / iPad", "detail": "3 archivos", "name": "3 archivos"},
            {"id": "3", "origin": "Para Android", "detail": "foto.jpg", "name": "foto.jpg"},
            {"id": "4", "origin": "Invitado Web", "detail": "Nota: " + "x" * 40 + "...", "name": ""},
            {"id": "5", "origin": "Mi iPhone (192.0.2.7)", "detail": "Texto: hola", "name": "Texto: hola"},
        ]
        i18n.pin("es")
        es = panel_routes.history_to_show(saved)
        self.assertEqual([h["origin"] for h in es], ["Enviado al celular", "Enviado al iPhone o iPad", "Enviado a Android",
                                                     "Invitado (página web)", "Mi iPhone (192.0.2.7)"])
        self.assertEqual(es[0]["detail"], "Texto: hola")
        self.assertEqual(es[3]["detail"], "Nota: " + "x" * 40 + "…")
        i18n.pin("en")
        en = panel_routes.history_to_show(saved)
        self.assertEqual([h["origin"] for h in en[:4]], ["Sent to phone", "Sent to iPhone or iPad", "Sent to Android", "Guest (web page)"])
        self.assertEqual(en[1]["detail"], "3 files")
        self.assertEqual(saved[0]["origin"], "Portapapeles / iOS", "lo guardado no se toca")
        without_name = [{"id": "6", "origin": "Dispositivo Móvil (192.0.2.8)", "detail": "a.txt", "name": "a.txt"}]
        self.assertEqual(panel_routes.history_to_show(without_name)[0]["origin"], "Mobile device (192.0.2.8)")

    def test_crop_only_adds_points_if_short(self):
        import panel_routes
        self.assertEqual(panel_routes.crop("hola", 40), "hola")
        self.assertEqual(panel_routes.crop("a" * 41, 40), "a" * 40 + "…")

    def test_link_pc_shortcut_name_is_not_translated(self):
        self.assertIn(">Link PC<", web_ui.HTML_APP)
        self.assertIn(">Link PC<", i18n.translate_html(web_ui.HTML_APP, "es"))
        self.assertNotIn("Link with PC", web_ui.HTML_APP)
        self.assertNotIn("README", web_ui.HTML_APP)

    def test_each_iphone_shortcut_carries_link_qr(self):
        page = web_ui.with_shortcuts_qr(web_ui.HTML_APP, lambda url: f"<svg>{url}</svg>")
        for name in ("Link PC", "Send PC", "Receive PC"):
            button = next(l for l in page.splitlines() if f">{name}</button>" in l)
            self.assertRegex(button, r'data-qr="(https://www\.icloud\.com/shortcuts/\w+)" hidden><svg>\1</svg></span>')

    def test_linking_page_translates_title(self):
        i18n.pin("en")
        self.assertIn("<h2>Link this iPad with mi-pc</h2>", web_ui.configure_page("mi-pc", "http://192.0.2.1:8765", "k", "ipad"))
        i18n.pin("es")
        page = web_ui.configure_page("mi-pc", "http://192.0.2.1:8765", "k", "ipad")
        self.assertIn("<h2>Vincular este iPad con mi-pc</h2>", page)
        self.assertIn("/device/type", page)

    def test_all_keys_t_have_translation(self):
        missing = sorted({f"{file}: {text}" for file, text in t_keys() if text not in strings_es.ES})
        self.assertEqual(missing, [])

    def test_translations_respect_markers_and_quotes(self):
        for en, es in strings_es.ES.items():
            with self.subTest(en=en):
                self.assertTrue(es.strip(), "traducción vacía")
                self.assertEqual(sorted(re.findall(r"\{\w*\}", en)), sorted(re.findall(r"\{\w*\}", es)))
                for quote in "'\"`":
                    self.assertLessEqual(es.count(quote), en.count(quote), f"comilla {quote} de más")
                self.assertEqual(en.count("<"), es.count("<"), "etiquetas distintas")

    def test_each_pages_text_has_translation(self):
        for name, html in PAGES.items():
            with self.subTest(page=name):
                missing = [x for x in i18n.extract_texts(html) if x not in page_keys() and x not in strings_es.EQUAL]
                self.assertEqual(missing, [])

    def test_panel_in_spanish_leaves_no_text_in_english(self):
        translations = {" ".join(v.split()) for v in strings_es.ES.values()}
        keys = page_keys()
        for name, html in PAGES.items():
            with self.subTest(page=name):
                spanish = i18n.translate_html(html, "es")
                remain = [x for x in i18n.extract_texts(spanish)
                          if x not in strings_es.EQUAL and x not in translations and x in keys]
                self.assertEqual(remain, [])
                self.assertIn('<html lang="es"', spanish)

    def test_english_page_is_unchanged(self):
        self.assertIs(i18n.translate_html(web_ui.HTML_APP, "en"), web_ui.HTML_APP)

    def test_settings_selector_saves_and_applies_language(self):
        import configuration
        import bridge
        import panel_routes

        class Fake:
            def respond(self, code, body, kind=None):
                return code

        with tempfile.TemporaryDirectory() as td, \
                unittest.mock.patch.object(configuration, "CONFIG", Path(td) / "config.json"):
            apply = panel_routes.PanelPaths._apply_profile_changes
            self.assertEqual(apply(Fake(), {"language": "en"}), 200)
            self.assertEqual(bridge.load_config()["language"], "en")
            self.assertEqual(i18n.current(), "en")
            apply(Fake(), {"language": "fr"})  # ignored
            self.assertEqual(bridge.load_config()["language"], "en")
            apply(Fake(), {"language": "es"})
            self.assertEqual(bridge.load_config()["language"], "es")
            self.assertEqual(i18n.current(), "es")
        self.assertIn('id="cfg-language"', web_ui.HTML_APP)
        self.assertIn('<option value="en">English</option>', web_ui.HTML_APP)

    def test_panel_in_english_says_devices(self):
        self.assertIn(">Devices<", web_ui.HTML_APP)
        self.assertIn(">Dispositivos<", i18n.translate_html(web_ui.HTML_APP, "es"))

    @unittest.skipUnless(shutil.which("node"), "sin Node.js")
    def test_translated_javascript_follows_being_valid(self):
        for name, html in PAGES.items():
            spanish = i18n.translate_html(html, "es")
            for i, js in enumerate(re.findall(r"<script\b[^>]*>(.*?)</script>", spanish, flags=re.S)):
                if not js.strip():
                    continue
                with self.subTest(page=name, script=i), tempfile.TemporaryDirectory() as td:
                    path = Path(td) / "p.js"
                    path.write_text(js, encoding="utf-8")
                    r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, r.stderr[-800:])


if __name__ == "__main__":
    unittest.main()
