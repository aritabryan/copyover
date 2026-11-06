import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import i18n
import bridge


class ImageTests(unittest.TestCase):
    def setUp(self):
        i18n.pin("es")

    def test_android_photos_are_copied_as_images(self):
        for format_name, suffix in [("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp")]:
            with self.subTest(format=format_name), tempfile.TemporaryDirectory() as directory:
                stream = io.BytesIO()
                Image.new("RGB", (4, 4), "blue").save(stream, format=format_name)
                with patch.object(bridge, "get_downloads_folder", lambda kind="iphone", d=directory: Path(d)), \
                        patch.object(bridge.so, "set_image") as image_clipboard, \
                        patch.object(bridge.so, "set_files") as files_clipboard:
                    handler = object.__new__(bridge.Handler)
                    handler.notices = False
                    result = handler.receive([("captura." + suffix, "image/" + suffix, stream.getvalue())])
                    self.assertEqual(result, "Foto copiada en la PC")
                    image_clipboard.assert_called_once()
                    files_clipboard.assert_not_called()
                    if suffix == "webp":
                        self.assertTrue(image_clipboard.call_args.args[0].read_bytes().startswith(b"\x89PNG"))

    def test_every_hevc_photo_brand_is_recognized(self):
        for brand in (b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs", b"mif1", b"msf1"):
            with self.subTest(brand=brand):
                self.assertEqual(bridge.image_type(b"\x00\x00\x00\x18ftyp" + brand + b"\x00" * 8), "heic")

    def test_converted_photo_keeps_its_color_profile(self):
        # iPhone photos are in Display P3: without the profile, the JPG looks washed out on the PC.
        import pillow_heif
        from PIL import ImageCms
        pillow_heif.register_heif_opener()
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        stream = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(stream, format="HEIF", icc_profile=profile)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "foto.jpg"
            bridge.save_as_jpg(stream.getvalue(), path)
            with Image.open(path) as converted:
                self.assertEqual(converted.info.get("icc_profile"), profile)

    def test_converted_photo_keeps_date_and_location(self):
        import pillow_heif
        pillow_heif.register_heif_opener()
        exif = Image.Exif()
        exif[0x0132] = "2026:11:04 20:24:00"
        exif.get_ifd(0x8769)[0x9003] = "2026:11:04 20:24:00"
        exif.get_ifd(0x8825)[1] = "N"
        exif.get_ifd(0x8825)[2] = (14.0, 5.0, 0.0)
        for format_name in ("HEIF", "JPEG"):
            with self.subTest(format=format_name), tempfile.TemporaryDirectory() as directory:
                stream = io.BytesIO()
                Image.new("RGB", (8, 4), "red").save(stream, format=format_name, exif=exif)
                path = Path(directory) / "foto.jpg"
                bridge.save_as_jpg(stream.getvalue(), path)
                with Image.open(path) as converted:
                    saved = converted.getexif()
                    self.assertEqual(saved[0x0132], "2026:11:04 20:24:00")
                    self.assertEqual(saved.get_ifd(0x8769)[0x9003], "2026:11:04 20:24:00")
                    self.assertEqual(saved.get_ifd(0x8825)[1], "N")
                    self.assertEqual(float(saved.get_ifd(0x8825)[2][0]), 14.0)

    def test_rotation_is_applied_once_and_not_left_in_the_exif(self):
        exif = Image.Exif()
        exif[0x0112] = 6
        stream = io.BytesIO()
        Image.new("RGB", (8, 4), "red").save(stream, format="JPEG", exif=exif)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "foto.jpg"
            bridge.save_as_jpg(stream.getvalue(), path)
            with Image.open(path) as converted:
                self.assertEqual(converted.size, (4, 8))
                self.assertNotIn(0x0112, converted.getexif())

    def test_photo_whose_exif_doesnt_fit_in_a_jpg_is_still_converted(self):
        exif = Image.Exif()
        exif[0x010E] = "x" * 70000
        stream = io.BytesIO()
        Image.new("RGB", (8, 4), "red").save(stream, format="PNG", exif=exif)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "foto.jpg"
            bridge.save_as_jpg(stream.getvalue(), path)
            self.assertTrue(path.read_bytes().startswith(b"\xff\xd8"))

    def test_photo_that_cant_be_decoded_is_saved_as_it_arrived(self):
        damaged_heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 40
        damaged_jpg = b"\xff\xd8\xff" + b"\x00" * 40
        for name, data, saved_as in [("IMG_1.HEIC", damaged_heic, "IMG_1.HEIC"), ("", damaged_heic, ".heic"),
                                     ("roto.jpg", damaged_jpg, "roto.jpg")]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                with patch.object(bridge, "get_downloads_folder", lambda kind="iphone", d=directory: Path(d)), \
                        patch.object(bridge.so, "set_image") as image_clipboard, \
                        patch.object(bridge.so, "set_files") as files_clipboard:
                    handler = object.__new__(bridge.Handler)
                    handler.notices = False
                    handler.receive([(name, "image/heic", data)])
                image_clipboard.assert_not_called()
                saved = files_clipboard.call_args.args[0]
                self.assertEqual(len(saved), 1)
                self.assertTrue(saved[0].name.endswith(saved_as), saved[0].name)
                self.assertEqual(saved[0].read_bytes(), data)
                self.assertEqual(list(Path(directory).iterdir()), saved)

    def test_received_heic_by_panel_remains_in_jpg(self):
        import pillow_heif
        pillow_heif.register_heif_opener()
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "IMG_0001.HEIC"
            Image.new("RGB", (8, 8), "red").save(original, format="HEIF")
            converted = bridge.heic_a_jpg(original)
            self.assertEqual(converted.name, "IMG_0001.jpg")
            self.assertFalse(original.exists())
            self.assertTrue(converted.read_bytes().startswith(b"\xff\xd8"))

    def test_damaged_heic_remains_as_arrived(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "roto.heic"
            original.write_bytes(b"no es una foto")
            self.assertEqual(bridge.heic_a_jpg(original), original)
            self.assertTrue(original.exists())
