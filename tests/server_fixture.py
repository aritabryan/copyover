"""Test server that knows when it has finished handling every request.

The handler records some things (the panel's «latest», the device's event) after the response has
already gone out, so a test that checks right after reading the response could look too early, and
that late write would land in the next test. Tests call esperar() before checking.
"""
import threading

import bridge


class TestServer(bridge.ThreadingHTTPServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._in_progress = 0
        self._change = threading.Condition()

    def process_request(self, request, client_address):
        # Counted here, in the accepting thread, so it's already counted before any response goes out.
        with self._change:
            self._in_progress += 1
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._change:
                self._in_progress -= 1
                self._change.notify_all()

    def wait(self, seconds: float = 5) -> bool:
        """Waits until no request is being handled. False if it took longer than `segundos`."""
        with self._change:
            return self._change.wait_for(lambda: self._in_progress == 0, timeout=seconds)
