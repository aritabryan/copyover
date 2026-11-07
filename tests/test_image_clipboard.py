"""Verify Windows image formats without touching the user's clipboard."""
import base64
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from PIL import Image, BmpImagePlugin


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell")


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows Forms requires Windows")
class ImageClipboardFormats(unittest.TestCase):
    def formats(self, size=(4, 3), fail=False):
        source = (ROOT / "helper.ps1").read_text(encoding="utf-8")
        start = source.index("function Set-ImageClipboard")
        function = source[start:source.index("switch ($Mode)", start)]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "image.png"
            Image.new("RGBA", size, (255, 0, 0, 128)).save(path)
            script = Path(folder) / "check.ps1"
            writer = "throw 'Simulated writer failure'" if fail else """
$png = $data.GetData('PNG', $false).ToArray()
$dib = $data.GetData([System.Windows.Forms.DataFormats]::Dib, $false).ToArray()
$bitmap = $data.GetData([System.Windows.Forms.DataFormats]::Bitmap, $false)
$exported = Export-ClipboardImage $data $exportPath
@{ formats = @($data.GetFormats($false)); width = $bitmap.Width; height = $bitmap.Height;
   png = [Convert]::ToBase64String($png); dib = [Convert]::ToBase64String($dib);
   exported = $exported; restored = [Convert]::ToBase64String([IO.File]::ReadAllBytes($exportPath)) } | ConvertTo-Json -Compress
"""
            script.write_text("Add-Type -AssemblyName System.Windows.Forms, System.Drawing\n" + function +
                              "\n$exportPath = $args[1]\nSet-ImageClipboard $args[0] { param($data)\n" + textwrap.dedent(writer) + "\n}\n",
                              encoding="utf-8-sig")
            result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-STA", "-File", str(script), str(path), str(Path(folder) / "restored.png")],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if fail:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Simulated writer failure", result.stderr)
                path.unlink()
                return
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            data["source_png"] = path.read_bytes()
            path.unlink()
            return data

    def test_publishes_png_bitmap_and_dib_with_original_pixels(self):
        data = self.formats()
        self.assertTrue({"PNG", "Bitmap", "DeviceIndependentBitmap"}.issubset(data["formats"]))
        self.assertEqual((data["width"], data["height"]), (4, 3))
        self.assertEqual(base64.b64decode(data["png"]), data["source_png"])
        self.assertTrue(data["exported"])
        self.assertEqual(base64.b64decode(data["restored"]), data["source_png"])
        with Image.open(io.BytesIO(base64.b64decode(data["png"]))) as png:
            self.assertEqual(png.size, (4, 3))
            self.assertEqual(png.convert("RGBA").getpixel((0, 0)), (255, 0, 0, 128))
        with BmpImagePlugin.DibImageFile(io.BytesIO(base64.b64decode(data["dib"]))) as dib:
            self.assertEqual(dib.size, (4, 3))
            self.assertEqual(dib.convert("RGB").getpixel((0, 0)), (255, 0, 0))

    def test_large_images_are_not_resized_to_fit_windows_history(self):
        data = self.formats(size=(1500, 1200))
        self.assertEqual((data["width"], data["height"]), (1500, 1200))
        self.assertGreater(len(base64.b64decode(data["dib"])), 4 * 1024 * 1024)

    def test_writer_failure_releases_the_image_file(self):
        self.formats(fail=True)

    def test_jpeg_uses_same_pixels_as_the_echo_fingerprint(self):
        import windows_system
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "image.jpg"
            Image.new("RGB", (5, 4), (31, 96, 173)).save(source, "JPEG")
            with Image.open(source) as image:
                expected = image.convert("RGBA").tobytes()
            temporary = []
            def helper(mode, path):
                self.assertEqual(mode, "set-image")
                temporary.append(Path(path))
                with Image.open(path) as image:
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(image.convert("RGBA").tobytes(), expected)
                return True
            with patch.object(windows_system, "helper", side_effect=helper):
                self.assertTrue(windows_system.set_image(source))
            self.assertFalse(temporary[0].exists())

    @unittest.skipUnless(os.environ.get("COPYOVER_TEST_CLIPBOARD") == "1" and
                         os.environ.get("GITHUB_ACTIONS") == "true", "Only touch the isolated GitHub runner clipboard")
    def test_formats_survive_the_writer_process_and_round_trip(self):
        import ctypes
        import windows_system
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.png"
            restored = Path(folder) / "restored.png"
            Image.new("RGBA", (8, 6), (255, 0, 0, 128)).save(source)
            self.assertTrue(windows_system.set_image(source))
            self.assertTrue(windows_system.user32.IsClipboardFormatAvailable(windows_system.CF_DIB))
            self.assertTrue(windows_system.user32.IsClipboardFormatAvailable(windows_system.CF_BITMAP))
            windows_system.open_clipboard()
            try:
                handle = windows_system.user32.GetClipboardData(windows_system.CF_DIB)
                self.assertTrue(handle)
                pointer = windows_system.kernel32.GlobalLock(handle)
                self.assertTrue(pointer)
                try:
                    header = ctypes.string_at(pointer, 40)
                finally:
                    windows_system.kernel32.GlobalUnlock(handle)
            finally:
                windows_system.user32.CloseClipboard()
            self.assertIn(int.from_bytes(header[:4], 'little'), (40, 52, 56, 108, 124))
            self.assertEqual(int.from_bytes(header[4:8], 'little', signed=True), 8)
            self.assertEqual(abs(int.from_bytes(header[8:12], 'little', signed=True)), 6)
            self.assertTrue(windows_system.read_image(restored))
            self.assertEqual(restored.read_bytes(), source.read_bytes())
