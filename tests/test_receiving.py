"""An upload is split as it arrives: large parts go to disk and never whole into RAM."""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bridge
import receiving


def multipart(border, *parts, preamble=b""):
    body = preamble
    for name, kind, data in parts:
        device = b'form-data; name="archivo"' + (b'; filename="' + name.encode() + b'"' if name else b"")
        body += (b"--" + border + b"\r\nContent-Disposition: " + device + b"\r\nContent-Type: "
                   + kind.encode() + b"\r\n\r\n" + data + b"\r\n")
    return body + b"--" + border + b"--\r\n"


class ReadPartsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, body, kind, length=None):
        return receiving.read_parts(io.BytesIO(body).read, len(body) if length is None else length, kind, self.folder)

    def temp_paths(self):
        return [p.name for p in self.folder.iterdir() if p.name.startswith(".copyover-recibiendo-")]

    def test_gives_the_same_result_as_the_previous_parser_with_any_piece_size(self):
        border = b"----limite123"
        # Data with pieces that look like the boundary, which must not cut the part.
        binary = os.urandom(3000) + b"\r\n--" + border[:-1] + b"X" + b"\r\n----" + os.urandom(500)
        body = multipart(border, (None, "text/plain", "hola señor\r\nlínea 2".encode()),
                           ("photo.jpg", "image/jpeg", b"\xff\xd8\xff" + binary),
                           ("vacío.txt", "text/plain", b""), preamble=b"basura antes\r\n")
        kind = "multipart/form-data; boundary=" + border.decode()
        expected = bridge.request_parts(kind, body)
        for piece in (1, 7, 64, 4096, 1 << 20):
            with self.subTest(piece=piece), patch.object(receiving, "PIECE", piece):
                self.assertEqual(self.read(body, kind), expected)

    def test_large_goes_to_disk_and_only_moves(self):
        data = os.urandom(5000)
        body = multipart(b"b", (None, "text/plain", b"texto"), ("video.mov", "video/quicktime", data))
        with patch.object(receiving, "PART_IN_RAM_LIMIT", 1000), patch.object(receiving, "PIECE", 333):
            parts = self.read(body, 'multipart/form-data; boundary="b"')
        self.assertEqual(parts[0], (None, "text/plain", b"texto"))
        name, kind, part = parts[1]
        self.assertEqual((name, kind, len(part)), ("video.mov", "video/quicktime", 5000))
        self.assertIsInstance(part, receiving.PartOnDisk)
        self.assertEqual(part.head, data[:receiving.HEAD_SIZE])
        destination = self.folder / "video.mov"
        part.move_a(destination)
        self.assertEqual(destination.read_bytes(), data)
        receiving.discard(parts)
        self.assertEqual(self.temp_paths(), [])

    def test_interrupted_send_leaves_no_temporary_file(self):
        body = multipart(b"b", ("video.mov", "video/quicktime", os.urandom(5000)))
        with patch.object(receiving, "PART_IN_RAM_LIMIT", 1000), patch.object(receiving, "PIECE", 500), \
                self.assertRaises(receiving.CutSend):
            self.read(body[:3000], "multipart/form-data; boundary=b", length=len(body))
        self.assertEqual(self.temp_paths(), [])

    def test_raw_file_and_form(self):
        data = os.urandom(5000)
        with patch.object(receiving, "PART_IN_RAM_LIMIT", 1000):
            [(name, kind, part)] = self.read(data, "application/pdf")
        self.assertEqual((name, kind), (None, "application/pdf"))
        self.assertEqual(part.read(), data)
        part.discard()
        self.assertEqual(self.read("texto=hola%20se%C3%B1or".encode(), "application/x-www-form-urlencoded"),
                         [(None, "text/plain", "hola señor".encode())])

    def test_multipart_without_border_is_malformed(self):
        with self.assertRaises(receiving.MalformedSend):
            self.read(b"nada", "multipart/form-data")
        with self.assertRaises(receiving.MalformedSend):
            self.read(b"no hay borde aqui", "multipart/form-data; boundary=xyz")

    def test_deletes_temporary_files_left_by_an_interrupted_send(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            old = folder / f"{receiving.TEMPORARY_PREFIX}aaa.part"
            alive = folder / f"{receiving.TEMPORARY_PREFIX}bbb.part"
            foreign = folder / "video.part"
            for path in (old, alive, foreign):
                path.write_bytes(b"x")
            two_hours_ago = alive.stat().st_mtime - 7200
            os.utime(old, (two_hours_ago, two_hours_ago))
            os.utime(foreign, (two_hours_ago, two_hours_ago))
            self.assertEqual(receiving.delete_old_temporary(folder), 1)
            self.assertFalse(old.exists())
            self.assertTrue(alive.exists(), "uno que sigue llegando no se toca")
            self.assertTrue(foreign.exists(), "what isn't CopyOver's is left alone")
        self.assertEqual(receiving.delete_old_temporary(Path(td) / "no-existe"), 0)


class SendLargeTests(unittest.TestCase):
    """/enviar saves a large file without collecting it in memory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_file_spilled_to_disk_arrives_whole_with_its_name(self):
        import http.client
        import threading
        data = b"PK\x03\x04" + os.urandom(300_000)
        body = multipart(b"b", ("respaldo.zip", "application/zip", data))
        server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        server.RequestHandlerClass.key = "test-key"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with patch.object(receiving, "PART_IN_RAM_LIMIT", 10_000), \
                    patch.object(bridge, "get_downloads_folder", return_value=self.folder), \
                    patch.object(bridge.so, "set_files"), patch.object(bridge, "notify_received"), \
                    patch.object(bridge, "register_history"):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
                conn.request("POST", "/enviar", body, {"X-Key": "test-key",
                                                        "Content-Type": "multipart/form-data; boundary=b"})
                self.assertEqual(conn.getresponse().status, 200)
                conn.close()
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual((self.folder / "respaldo.zip").read_bytes(), data)
        self.assertEqual([p.name for p in self.folder.iterdir()], ["respaldo.zip"], "sin temporales")

    def test_empty_file_says_it_arrived_empty_without_blaming_the_shortcut(self):
        import http.client
        import threading
        import i18n
        body = multipart(b"b", ("notes.txt", "application/octet-stream", b""))
        server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        server.RequestHandlerClass.key = "test-key"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with patch.object(bridge, "get_downloads_folder", return_value=self.folder), \
                    patch.object(bridge, "register_history"), patch.object(i18n, "current", return_value="es"):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
                conn.request("POST", "/enviar", body, {"X-Key": "test-key",
                                                        "Content-Type": "multipart/form-data; boundary=b"})
                response = conn.getresponse().read().decode("utf-8")
                conn.close()
        finally:
            server.shutdown()
            server.server_close()
        self.assertIn("notes.txt está vacío", response)
        self.assertNotIn("Shortcut Input", response)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_disk_full_midway_is_reported_and_leaves_nothing(self):
        import errno
        import http.client
        import threading
        import i18n
        body = multipart(b"b", ("video.mov", "application/octet-stream", os.urandom(50_000)))
        server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        server.RequestHandlerClass.key = "test-key"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        write = receiving._Destination.write

        def fill(destination, data):
            write(destination, data)
            if destination.file is not None:  # it already started writing to disk and ran out of space
                raise OSError(errno.ENOSPC, "No space left on device")

        try:
            with patch.object(receiving, "PART_IN_RAM_LIMIT", 10_000), \
                    patch.object(receiving._Destination, "write", fill), \
                    patch.object(bridge, "get_downloads_folder", return_value=self.folder), \
                    patch.object(bridge, "register_history"), patch.object(i18n, "current", return_value="es"):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
                conn.request("POST", "/enviar", body, {"X-Key": "test-key",
                                                        "Content-Type": "multipart/form-data; boundary=b"})
                r = conn.getresponse()
                state, response = r.status, r.read().decode("utf-8")
                conn.close()
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(state, 507)
        self.assertIn("Espacio en disco insuficiente", response)
        self.assertEqual(list(self.folder.iterdir()), [], "sin temporales")


if __name__ == "__main__":
    unittest.main()
