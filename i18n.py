"""English and Spanish. CopyOver is written in English; Spanish comes from the strings_es.py dictionary.

In Python, visible text goes through `t("Text in English", name=...)`. In the web pages (panel, quick view,
linking), `translate_html` swaps each English phrase for its translation only where it appears as text: between
tags (`>phrase<`), between quotes (`'phrase'`, `"phrase"`) or as a piece of a JavaScript template. That way a
phrase is never mistaken for a function name or a CSS class.
"""
import os
import re

LANGUAGES = ("es", "en")
_current = None


def detect(cfg=None) -> str:
    """The config's `language` if it's es/en; otherwise English. Spanish is chosen in the panel's Settings."""
    chosen = (cfg or {}).get("language")
    return chosen if chosen in LANGUAGES else "en"


def pin(language: str):
    global _current
    _current = language if language in LANGUAGES else "en"
    _cache.clear()


def from_browser(accept_language: str) -> str | None:
    """The first language of a browser's Accept-Language that CopyOver has (es-HN → es), or None."""
    for part in (accept_language or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LANGUAGES:
            return code
    return None


def _saved_config() -> dict:
    """The config as it is on disk, without creating it (load_config would write it if missing)."""
    try:
        import json
        import configuration
        cfg = json.loads(configuration.CONFIG.read_text(encoding="utf-8"))
        configuration.migrate_config(cfg)
        return cfg
    except Exception:
        return {}


def current() -> str:
    """Language in use. COPYOVER_LANGUAGE=es|en overrides the config (the tests use it)."""
    if _current is None:
        cfg = _saved_config()
        forced = os.environ.get("COPYOVER_LANGUAGE") or os.environ.get("COPYOVER_IDIOMA")  # its old name
        if forced in LANGUAGES:
            cfg = {"language": forced}
        pin(detect(cfg))
    return _current


def _dictionary() -> dict:
    import strings_es
    return strings_es.ES


_page_table: dict = {}


def _page_dictionary() -> dict:
    """The dictionary with its keys' whitespace normalized, the way extract_texts finds the phrases in a page."""
    table = _dictionary()
    if _page_table.get("size") != len(table) or _page_table.get("source") is not table:
        _page_table.clear()
        _page_table.update(source=table, size=len(table), keys={" ".join(k.split()): v for k, v in table.items()})
    return _page_table["keys"]


# Actions as older versions stored them.
OLD_EVENT_ACTIONS = {
    "App en vivo": "In the web app",
    "Archivo recibido": "File received",
    "Conectado": "Connected",
    "Copiado": "Copied",
    "Descarga en App": "Downloaded in the app",
    "Envio": "Sent",
    "Envio desde App": "Sent from the app",
    "Envío": "Sent",
    "Envío desde App": "Sent from the app",
    "Foto recibida": "Photo received",
    "Texto copiado": "Text copied",
    "Traido": "Got something",
    "Traído": "Got something",
    "Vinculado por QR": "Linked by QR code",
}


def t(text: str, **values) -> str:
    if current() == "es":
        text = _dictionary().get(text, text)
    return text.format(**values) if values else text


# What each device event says, by the action stored with it.
_EVENT_TEXTS = {
    "Connected": "Connected at {time}",
    "Copied": "Copied at {time}",
    "Downloaded in the app": "Downloaded in the app at {time}",
    "File received": "File received at {time}",
    "Got something": "Got something at {time}",
    "In the web app": "In the web app at {time}",
    "Linked by QR code": "Linked by QR code at {time}",
    "Photo received": "Photo received at {time}",
    "Sent": "Sent at {time}",
    "Sent from the app": "Sent from the app at {time}",
    "Text copied": "Text copied at {time}",
}
_EVENT = re.compile(r"(.+) (?:at|a las) (\d{1,2}:\d{2})")
_LINKED_ON = re.compile(r"(?:Linked on|Vinculado el) (\d{2}/\d{2}) (?:at|a las) (\d{1,2}:\d{2})")
_PLAIN_EVENTS = {"Saved": "Saved", "Guardado": "Saved", "Linked": "Linked", "Vinculado": "Linked"}
_NO_EVENT = ("On the network", "En red")  # known device with no action to report: the card shows nothing


def translate_event(text: str) -> str:
    """A device's last action in the interface language. It's stored in English («Sent at 15:41»); older
    versions stored it in Spanish («Envio a las 15:41») and those are still read."""
    if not text or text in _NO_EVENT:
        return ""
    if text in _PLAIN_EVENTS:
        return t(_PLAIN_EVENTS[text])
    linked = _LINKED_ON.fullmatch(text)
    if linked:
        return t("Linked on {date} at {time}", date=linked[1], time=linked[2])
    event = _EVENT.fullmatch(text)
    if event:
        template = _EVENT_TEXTS.get(OLD_EVENT_ACTIONS.get(event[1], event[1]))
        if template:
            return t(template, time=event[2])
    return text


# --- Web pages -----------------------------------------------------------------

_cache: dict = {}
_QUOTES = "'\"`"


def _pattern(phrase: str) -> re.Pattern:
    body = r"\s+".join(re.escape(p) for p in phrase.split())
    return re.compile(r"([>'\"`}])(\s*)" + body + r"(\s*)(<|['\"`]|\$\{)")


def _compatible(before: str, after: str) -> bool:
    if before in _QUOTES and after in _QUOTES:
        return before == after
    if before in "'\"" and after == "${":
        return False
    if before == "}" and after in "'\"":
        return False
    return True


_patterns: dict = {}


def translate_html(html: str, language: str | None = None) -> str:
    """The page in the requested (or current) language. In English it's returned as is."""
    language = language or current()
    if language != "es":
        return html
    key = (language, html)
    if key in _cache:
        return _cache[key]
    # Only the phrases this page shows as text: the dictionary also has short Python strings («file»,
    # «Status») that may be field names in the JavaScript.
    table = _page_dictionary()
    phrases = [en for en in extract_texts(html) if en in table]
    result = html.replace('<html lang="en"', '<html lang="es"', 1)
    for en in sorted(phrases, key=len, reverse=True):
        es = table[en]
        pattern = _patterns.get(en) or _patterns.setdefault(en, _pattern(en))

        def change(m, es=es):
            if not _compatible(m.group(1), m.group(4)):
                return m.group(0)
            return m.group(1) + m.group(2) + es + m.group(3) + m.group(4)

        result = pattern.sub(change, result)
    if len(_cache) > 20:
        _cache.clear()
    _cache[key] = result
    return result


# --- Extraction (for the tests) -------------------------------------------------

_WORD = re.compile(r"[A-Za-zÁÉÍÓÚÑáéíóúñü¿¡]")


def _looks_like_text(raw: str) -> bool:
    s = raw.strip()
    if not s or not _WORD.search(s):
        return False
    if re.fullmatch(r"[A-Z]{2,}[.!?…:]*", s):
        return s in _dictionary()  # «OK», «OFFLINE»; other capitals («GET», «URL») aren't text to translate
    if s.startswith(("/", "#", ".", "http", "data:", "application/", "text/", "image/", "--")):
        return False
    # A single lowercase word is an identifier or a CSS class, unless it sits next to a ${...} with a space («
    # available», «files »): that's text.
    pasted = raw != s and re.fullmatch(r"[a-záéíóúñü]+[.!?…:,]*", s)
    if (re.fullmatch(r"[\w\-:.]+", s) and not re.search(r"[áéíóúñÁÉÍÓÚÑ]", s)
            and not re.fullmatch(r"[A-ZÁÉÍÓÚÑ¿¡][a-záéíóúñü]+[.!?…:]*", s) and not pasted):
        return False  # identifiers, CSS classes, internal values
    if re.search(r"[{}=]|=>|\(\)|\bvar\(|\bpx\b|rgba?\(", s):
        return False
    return True


def _js_chains(js: str):
    """Contents of the strings and fixed template pieces of a script, without comments."""
    i, n = 0, len(js)
    while i < n:
        c = js[i]
        if js.startswith("//", i):
            i = js.find("\n", i)
            i = n if i < 0 else i
            continue
        if js.startswith("/*", i):
            i = js.find("*/", i + 2)
            i = n if i < 0 else i + 2
            continue
        if c == "/" and re.search(r"(^|[(,=:\[!&|?{};])\s*$", js[max(0, i - 40):i]):
            j, in_class = i + 1, False  # regular expression: skipped whole
            while j < n and (js[j] != "/" or in_class) and js[j] != "\n":
                if js[j] == "\\":
                    j += 1
                elif js[j] == "[":
                    in_class = True
                elif js[j] == "]":
                    in_class = False
                j += 1
            i = j + 1
            continue
        if c in "'\"":
            j = i + 1
            while j < n and js[j] != c:
                j += 2 if js[j] == "\\" else 1
            yield js[i + 1:j]
            i = j + 1
            continue
        if c == "`":
            j, startup = i + 1, i + 1
            while j < n and js[j] != "`":
                if js[j] == "\\":
                    j += 2
                    continue
                if js.startswith("${", j):
                    yield js[startup:j]
                    depth, j = 1, j + 2
                    opens = j
                    while j < n and depth:
                        depth += {"{": 1, "}": -1}.get(js[j], 0)
                        j += 1
                    yield from _js_chains(js[opens:j - 1])  # strings inside ${...}
                    startup = j
                    continue
                j += 1
            yield js[startup:j]
            i = j + 1
            continue
        i += 1


def _markup_texts(html: str):
    for m in re.finditer(r">([^<>]+)<", html):
        yield m.group(1)
    for m in re.finditer(r"\b(?:title|placeholder|aria-label|alt)=\"([^\"]*)\"", html):
        yield m.group(1)


def extract_texts(html: str) -> list:
    """Visible phrases of a page (normalized), to check they all have a translation."""
    found = []
    without_styles = re.sub(r"<style\b.*?</style>", "", html, flags=re.S)
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", without_styles, flags=re.S)
    marked = re.sub(r"<script\b[^>]*>.*?</script>", "<script></script>", without_styles, flags=re.S)
    candidates = list(_markup_texts(marked))
    scripts += re.findall(r"\bon\w+=\"([^\"]*)\"", marked)  # onclick="..." and the like
    for js in scripts:
        for chain in _js_chains(js):
            if "<" in chain and ">" in chain:
                candidates += list(_markup_texts(">" + chain + "<"))
            else:
                candidates.append(chain)
    for c in candidates:
        clean = " ".join(c.split())
        if _looks_like_text(c) and clean not in found:
            found.append(clean)
    return found
