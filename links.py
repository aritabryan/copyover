"""Web links that arrive as text: only http and https are offered to open in the browser."""
import logging
import webbrowser
from urllib.parse import urlsplit

SCHEMES = ("http", "https")
MAXIMUM_LENGTH = 2048
FORBIDDEN = set('"<>\\^`{}|')  # can't go unencoded in a URL (RFC 3986) and make it harder to pass to other programs


def web_link(text) -> str:
    """The URL if the text is just an http(s) link (with nothing around it); otherwise an empty string. Never
    accepts file:, javascript:, data: or other schemes, nor text with spaces, control characters or
    characters a URL doesn't carry unencoded."""
    if not isinstance(text, str):
        return ""
    url = text.strip()
    if not url or len(url) > MAXIMUM_LENGTH or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in FORBIDDEN
                                                      for c in url):
        return ""
    try:
        parts = urlsplit(url)
        parts.port  # an invalid port raises ValueError
    except ValueError:
        return ""
    if parts.scheme.lower() not in SCHEMES or not parts.hostname:
        return ""
    return url


def open_in_browser(text) -> bool:
    """Opens the link in the default browser, only if it's http(s)."""
    url = web_link(text)
    if not url:
        return False
    try:
        return bool(webbrowser.open(url))
    except Exception:
        logging.exception("Couldn't open the link in the browser")
        return False
