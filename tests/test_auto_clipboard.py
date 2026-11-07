import importlib.util
import unittest


class AutomaticClipboardTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('sync'), 'Falta la recepción automática')
        import sync
        return sync

    def test_cursor_changes_on_copy_and_server_restart(self):
        module = self.module()
        sequence = [10]
        watcher = module.ClipboardChanges(lambda: sequence[0])
        original = watcher.current()
        self.assertEqual(original, watcher.current())
        sequence[0] = 11
        self.assertNotEqual(original, watcher.current())
        self.assertNotEqual(watcher.current(), module.ClipboardChanges(lambda: 11).current())

    def test_unchanged_clipboard_waits_without_reading_content(self):
        module = self.module()
        times = iter([0.0, 0.0, 0.2, 0.4, 0.6])
        sleeps = []
        watcher = module.ClipboardChanges(lambda: 5, clock=lambda: next(times), sleep=sleeps.append)
        token = watcher.current()
        self.assertEqual(watcher.wait(token, timeout=0.5), (token, False))
        self.assertTrue(sleeps)

    def test_changed_or_initial_cursor_returns_immediately(self):
        module = self.module()
        watcher = module.ClipboardChanges(lambda: 5, sleep=lambda _: self.fail('No debe esperar'))
        self.assertEqual(watcher.wait(''), (watcher.current(), True))
        self.assertEqual(watcher.wait('a' * 32 + ':4'), (watcher.current(), True))

    def test_received_image_origin_and_failed_write(self):
        from unittest.mock import Mock
        module = self.module()
        sequence = [1]
        watcher = module.ClipboardChanges(lambda: sequence[0])
        clipboard = Mock()
        clipboard.read_files.return_value = []
        clipboard.read_text.return_value = None
        import io
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (2, 2), 'red').save(buffer, 'PNG')
        image = buffer.getvalue()
        def read_image(path):
            path.write_bytes(image)
            return True
        clipboard.read_image.side_effect = read_image
        def write():
            sequence[0] += 1
        watcher.write('sender', write, expected=module.content_fingerprint(image, 'image/png'))
        self.assertEqual(watcher.frame('sender', clipboard)[1], None)
        self.assertEqual(watcher.frame('other', clipboard)[1:], (image, 'image/png'))
        with self.assertRaises(OSError):
            watcher.write('sender', Mock(side_effect=OSError('write failed')))
        self.assertEqual(watcher.frame('sender', clipboard)[1], image)
        watcher.write('sender', lambda: False)
        self.assertEqual(watcher.frame('sender', clipboard)[1], image)

    def test_local_copy_before_origin_sequence_sampling_is_not_suppressed(self):
        from unittest.mock import Mock
        module = self.module()
        sequence = [1]
        watcher = module.ClipboardChanges(lambda: sequence[0])
        clipboard = Mock()
        clipboard.read_files.return_value = []
        clipboard.read_text.return_value = 'local copy'
        def write():
            sequence[0] += 2  # Remote write followed immediately by an external local copy.
        expected = module.content_fingerprint(b'remote copy', 'text/plain')
        watcher.write('sender', write, expected=expected)
        self.assertEqual(watcher.frame('sender', clipboard)[1], b'local copy')

    def test_frame_retries_when_local_copy_changes_during_read(self):
        from unittest.mock import Mock
        module = self.module()
        sequence = [1]
        watcher = module.ClipboardChanges(lambda: sequence[0])
        clipboard = Mock()
        clipboard.read_files.return_value = []
        def read():
            sequence[0] += 1
            return 'changing'
        clipboard.read_text.side_effect = read
        with self.assertRaises(OSError):
            watcher.frame('sender', clipboard)
        self.assertEqual(clipboard.read_text.call_count, 3)

    def test_inbound_write_and_frame_do_not_interleave(self):
        from unittest.mock import Mock
        import threading
        module = self.module()
        sequence = [1]
        watcher = module.ClipboardChanges(lambda: sequence[0])
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def write():
            sequence[0] += 1
            entered.set()
            self.assertTrue(release.wait(2))
        clipboard = Mock()
        result = []
        def read():
            result.append(watcher.frame('sender', clipboard))
            finished.set()
        writer = threading.Thread(target=lambda: watcher.write('sender', write))
        reader = threading.Thread(target=read)
        writer.start()
        self.assertTrue(entered.wait(2))
        reader.start()
        try:
            self.assertFalse(finished.wait(0.05))
        finally:
            release.set()
            writer.join(2)
            reader.join(2)
        self.assertTrue(finished.is_set())
        self.assertEqual(result[0][0], watcher.current())

    def test_windows_sequence_call_does_not_read_or_modify_clipboard(self):
        import sys
        if sys.platform != 'win32':
            self.skipTest('API Windows')
        import windows_system
        self.assertTrue(hasattr(windows_system, 'clipboard_version'), 'Falta GetClipboardSequenceNumber')
        self.assertIsInstance(windows_system.clipboard_version(), int)

    def test_automatic_text_is_bounded_for_android_binder(self):
        from unittest.mock import Mock
        module = self.module()
        clipboard = Mock()
        clipboard.read_files.return_value = []
        clipboard.read_text.return_value = 'a' * (256 * 1024 + 1)
        self.assertIsNone(module.automatic_content(clipboard)[0])
        clipboard.read_image.assert_not_called()


if __name__ == '__main__':
    unittest.main()
