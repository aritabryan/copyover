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
