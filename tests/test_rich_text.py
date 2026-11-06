"""Text copied in Notes, Mail or Safari arrives from the iPhone as «Clipboard … .rtf» and must land on the
PC clipboard as plain text, not as a file in Downloads."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bridge  # noqa: E402
import rich_text  # noqa: E402
from test_receiving import multipart  # noqa: E402

# What iOS 26 sends when you copy a bold title in Notes (the name has a narrow no-break space before PM).
FROM_NOTES = (b"{\\rtf1\\ansi\\ansicpg1252\\cocoartf2907\n"
            b"\\cocoatextscaling1\\cocoaplatform1{\\fonttbl\\f0\\fnil\\fcharset0 .SFUI-Bold;"
            b"\\f1\\froman\\fcharset0 TimesNewRomanPSMT;}\n"
            b"{\\colortbl;\\red255\\green255\\blue255;\\red0\\green0\\blue0;}\n"
            b"{\\*\\expandedcolortbl;;\\cssrgb\\c0\\c0\\c0;}\n"
            b"\\deftab720\n\\pard\\pardeftab720\\sa60\\partightenfactor0\n\n"
            b"\\f0\\b\\fs56 \\cf0 \\expnd0\\expndtw0\\kerning0\n"
            b"\\outl0\\strokewidth0 \\strokec2 Esto es CopyOver\n"
            b"\\f1\\b0 \\strokec2 \\\n}")
IOS_NAME = "Clipboard 2 Nov 2026 at 9.40\u202fPM.rtf"


class RtfToTextTests(unittest.TestCase):
    def test_copied_in_notes_remains_without_fonts_nor_colors(self):
        self.assertEqual(rich_text.rtf_to_text(FROM_NOTES), "Esto es CopyOver")

    def test_emoji_links_quotes_accents_and_paragraphs(self):
        rtf = (b"{\\rtf1\\ansi\\ansicpg1252{\\fonttbl\\f0 Helvetica;}\\f0 Canci\\'f3n de "
               b"\\uc0\\u8220 ma\\'f1ana\\uc0\\u8221 \\\n"
               b"{\\field{\\*\\fldinst{HYPERLINK \"https://ejemplo.com\"}}{\\fldrslt enlace}} "
               b"\\uc0\\u55357 \\u56832 \\\n\\\nfin}")
        self.assertEqual(rich_text.rtf_to_text(rtf),
                         "Canci\u00f3n de \u201cma\u00f1ana\u201d\nenlace \U0001F600\n\nfin")

    def test_fallback_character_after_u_is_not_repeated(self):
        self.assertEqual(rich_text.rtf_to_text(b"{\\rtf1\\ansi \\u225?rbol \\'e1rbol}"), "\u00e1rbol \u00e1rbol")

    def test_pair_and_other_programs_tab(self):
        self.assertEqual(rich_text.rtf_to_text(b"{\\rtf1 uno\\tab dos\\par tres\\line cuatro}"),
                         "uno\tdos\ntres\ncuatro")


class IsFromClipboardTests(unittest.TestCase):
    def test_recognizes_the_name_ios_assigns(self):
        self.assertTrue(rich_text.is_from_clipboard(IOS_NAME, FROM_NOTES))
        self.assertTrue(rich_text.is_from_clipboard("Portapapeles 2 nov 2026, 21:40.rtf", FROM_NOTES))
        self.assertTrue(rich_text.is_from_clipboard("Clipboard.rtf", FROM_NOTES))

    def test_in_other_languages_recognizes_by_time_to_final(self):
        for name in ("Presse-papiers 2 nov. 2026 à 21:40.rtf", "Zwischenablage 02.11.2026, 21:40.rtf",
                       "Appunti 2 nov 2026, 21:40.rtf", "剪贴板 2026年11月2日 下午9:40.rtf",
                       "Area de transferência 2 nov 2026 9.40 p.m..rtf"):
            self.assertTrue(rich_text.is_from_clipboard(name, FROM_NOTES), name)

    def test_sent_rtf_to_purpose_follows_being_file(self):
        self.assertFalse(rich_text.is_from_clipboard("Contract.rtf", FROM_NOTES))
        self.assertFalse(rich_text.is_from_clipboard("Clipboarding notes.rtf", FROM_NOTES))
        self.assertFalse(rich_text.is_from_clipboard("Informe 2026.10.rtf", FROM_NOTES))
        self.assertFalse(rich_text.is_from_clipboard("Notas v1.20.rtf", FROM_NOTES))
        self.assertFalse(rich_text.is_from_clipboard(None, FROM_NOTES))

    def test_leaves_non_rtf_content_alone(self):
        self.assertFalse(rich_text.is_from_clipboard("Clipboard 2 Nov 2026.txt", b"hola"))


class JustTheNameTests(unittest.TestCase):
    def test_recognizes_sent_clipboard_name_as_text(self):
        for text in ("Clipboard 3 Nov 2026 at 2.29 AM", "Portapapeles 3 nov 2026, 2:29", "Clipboard 03.11.2026, 02:29"):
            self.assertTrue(rich_text.is_just_a_clipboard_name(text), text)

    def test_normal_text_mentioning_clipboard_is_pasted(self):
        for text in ("Clipboard history is on", "Copia esto al portapapeles a las 2:29 y listo", "nota de las 2:29",
                      "Clipboard 3 Nov 2026 at 2.29 AM\nsegunda línea"):
            self.assertFalse(rich_text.is_just_a_clipboard_name(text), text)


class SendRtfTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, name, data):
        import http.client
        import threading
        body = multipart(b"b", (name, "text/rtf", data))
        server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        server.RequestHandlerClass.key = "test-key"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with patch.object(bridge, "get_downloads_folder", return_value=self.folder), \
                    patch.object(bridge.so, "set_text") as set_text, \
                    patch.object(bridge.so, "set_files"), patch.object(bridge, "notify_received"), \
                    patch.object(bridge, "notify_text"), patch.object(bridge, "register_history"):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
                conn.request("POST", "/enviar", body, {"X-Key": "test-key",
                                                        "Content-Type": "multipart/form-data; boundary=b"})
                self.assertEqual(conn.getresponse().status, 200)
                conn.close()
        finally:
            server.shutdown()
            server.server_close()
        return set_text

    def test_copied_from_notes_pastes_as_text_and_leaves_no_rtf(self):
        set_text = self.send(IOS_NAME, FROM_NOTES)
        set_text.assert_called_once_with("Esto es CopyOver")
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_rtf_with_own_name_saves(self):
        set_text = self.send("Contract.rtf", FROM_NOTES)
        set_text.assert_not_called()
        self.assertEqual((self.folder / "Contract.rtf").read_bytes(), FROM_NOTES)


if __name__ == "__main__":
    unittest.main()
