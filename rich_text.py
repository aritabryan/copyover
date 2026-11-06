"""Rich text the iPhone sends as a file: copying in Notes, Mail or Safari leaves RTF on the clipboard, and the
Send PC shortcut sends it as «Clipboard 2 Nov 2026 at 9.41 PM.rtf». That is copied text, not a document, so the
PC takes the plain text out and puts it on the clipboard."""
import re
import unicodedata
from pathlib import Path

# How iOS names the clipboard when a shortcut sends it as a file, by system language.
CLIPBOARD_NAMES = ("clipboard", "portapapeles")

_ENDS_ON_THE_HOUR = re.compile(r"(?<![A-Za-z0-9.])\d{1,2}[.:]\d{2}(?:\s*[ap]\.?\s?m\.?)?$")

# Groups that hold settings or attachments, not text.
_HIDDEN_DESTINATIONS = {
    "fonttbl", "colortbl", "expandedcolortbl", "stylesheet", "info", "pict", "header", "footer",
    "listtable", "listoverridetable", "rsidtbl", "generator", "xmlnstbl", "themedata", "datastore",
    "NeXTGraphic",
}
_WORDS = {"par": "\n", "line": "\n", "tab": "\t", "emdash": "\u2014", "endash": "\u2013",
             "lquote": "\u2018", "rquote": "\u2019", "ldblquote": "\u201c", "rdblquote": "\u201d",
             "bullet": "\u2022"}
_SYMBOLS = {"\\": "\\", "{": "{", "}": "}", "~": "\u00a0", "_": "-", "\n": "\n", "\r": "\n"}
_TOKEN = re.compile(rb"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([^\\{}\r\n]+)|[\r\n]+", re.S)


def is_rtf(data: bytes) -> bool:
    return data.lstrip()[:5] == b"{\\rtf"


def is_from_clipboard(name: str | None, data: bytes) -> bool:
    """RTF named the way iOS names the clipboard (and not a .rtf file someone chose to send)."""
    if not name or not is_rtf(data):
        return False
    base = unicodedata.normalize("NFKC", Path(name).stem).strip().lower()
    if any(base == word or base.startswith(word + " ") for word in CLIPBOARD_NAMES):
        return True
    # In any language iOS ends the name with the time it was copied («… 9.40 PM», «… 21:40», «… à 21:40»).
    return bool(_ENDS_ON_THE_HOUR.search(base))


def is_just_a_clipboard_name(text: str) -> bool:
    """«Clipboard 3 Nov 2026 at 2.29 PM» and nothing else: what Send PC sends when the clipboard held a photo or a
    file and Shortcuts passed its name instead of the content."""
    base = unicodedata.normalize("NFKC", text).strip().lower()
    if not base or "\n" in base or len(base) > 80:
        return False
    if not any(base == word or base.startswith(word + " ") for word in CLIPBOARD_NAMES):
        return False
    return bool(_ENDS_ON_THE_HOUR.search(base))


def rtf_to_text(data: bytes) -> str:
    """The plain text of an RTF document, without fonts, colors or styles."""
    page = re.search(rb"\\ansicpg(\d+)", data)
    encoding = f"cp{int(page.group(1))}" if page else "cp1252"
    try:
        "".encode(encoding)
    except LookupError:
        encoding = "cp1252"

    output: list[str] = []
    stack: list[tuple[bool, int]] = []
    hidden, skip_uc, to_skip = False, 1, 0
    group_first = False
    for m in _TOKEN.finditer(data):
        word, number, hexa, symbol, key, text = m.groups()
        if key == b"{":
            stack.append((hidden, skip_uc))
            group_first = True
            continue
        if key == b"}":
            hidden, skip_uc = stack.pop() if stack else (False, 1)
            group_first = False
            continue
        if symbol == b"*":
            hidden = True
            continue
        if word is not None:
            name = word.decode("ascii")
            if group_first and name in _HIDDEN_DESTINATIONS:
                hidden = True
            group_first = False
            if name == "uc" and number is not None:
                skip_uc = int(number)
            elif name == "u" and number is not None and not hidden:
                output.append(chr(int(number) % 65536))
                to_skip = skip_uc
            elif name in _WORDS and not hidden:
                output.append(_WORDS[name])
            continue
        group_first = False
        if to_skip:
            # The fallback characters after \uN are for readers that don't know Unicode.
            if hexa is not None or symbol is not None:
                to_skip -= 1
                continue
            if text:
                removed = min(to_skip, len(text))
                text, to_skip = text[removed:], to_skip - removed
        if hidden:
            continue
        if hexa is not None:
            output.append(bytes([int(hexa, 16)]).decode(encoding, errors="replace"))
        elif symbol is not None:
            # A backslash before a line break is a line break (that's how Cocoa writes paragraphs).
            output.append(_SYMBOLS.get(symbol.decode("latin-1"), ""))
        elif text:
            output.append(text.decode(encoding, errors="replace"))
    # Emoji come as two \uN halves (surrogates): join them into one character.
    joined = "".join(output).encode("utf-16", "surrogatepass").decode("utf-16", errors="replace")
    lines = joined.replace("\r\n", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip("\n")
