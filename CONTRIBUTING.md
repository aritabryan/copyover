# Contributing to CopyOver

Thanks for helping. Issues and pull requests are welcome in English or Spanish.

## Setup

You need Python 3.10 or newer.

```bash
git clone https://github.com/aritabryan/copyover.git
cd copyover
python -m pip install -r requirements.txt
python copyover.py          # starts with a console window
```

The app listens on port 8765. The panel is at `http://127.0.0.1:8765/app`.
Your `config.json`, `history.json` and `copyover.log` stay local: they are in `.gitignore`, never commit them.

## Tests

```bash
COPYOVER_LANGUAGE=es python -m unittest discover -s tests -p "test_*.py"
```

On PowerShell:

```powershell
$env:COPYOVER_LANGUAGE = "es"; python -m unittest discover -s tests -p "test_*.py"
```

Some tests compare translated texts, so `COPYOVER_LANGUAGE=es` pins the language the same way CI does.
GitHub Actions runs the same suite on Windows and Ubuntu with Python 3.10 and 3.12.
A pull request should leave every test passing and add a test for the behavior it changes.

The Android app has its own build and tests: see [android/README.md](android/README.md).

## English and Spanish

Names in the code, comments, docstrings and log messages are in English, and English is the source text for every message. English is also the default language.

- **Python:** wrap each visible text in `t()` from `i18n.py`, for example `t("Received: {name}", name=file)`.
- **Web pages** (`web/`, filled in by `web_ui.py`): write the text in English. `i18n.translate_html()` translates the page to Spanish as it is served.
- **Spanish:** add the English text as a key in `ES` in `strings_es.py`, with its translation as the value. Keep the same `{placeholders}`, and use typographic quotes (« » ‘ ’) when the key has no straight ones.
- Texts that read the same in both languages, such as product names, go in `EQUAL` in `strings_es.py`.

Two tests list whatever is missing: `test_all_keys_t_have_translation` for Python and `test_each_pages_text_has_translation` for the pages.
To see the app in Spanish, start it with `COPYOVER_LANGUAGE=es`, or pick the language in the panel’s Settings.

Some names are part of the protocol and never change, in any language: the `/ping` reply (`Puente activo`), the `_puente._tcp` mDNS service and the JSON field names.

## Testing on Windows and Fedora

Changes to the clipboard, notifications, the tray or the right-click menu need a manual check on each system they touch.

- **Windows 10 or 11:** run `python copyover.py`, copy text, an image and a file in File Explorer, and bring each one to the phone with Receive PC. Send something from the phone with Send PC and paste it.
- **Fedora (GNOME and KDE):** do the same in a virtual environment created with `--system-site-packages`, as in the README. Try both Wayland and X11 when the change touches the clipboard. On GNOME, the tray icon needs the AppIndicator extension.
- **Between PCs:** send a file larger than 1 GB and cut the network halfway to check that the transfer resumes.

Say in the pull request what you tried and on which system.

## Commits and pull requests

- One change per commit, with a short message that says what it does. Spanish or English are both fine.
- Keep pull requests focused. Explain the why in the description, and link the issue if there is one.
- Never include keys, IP addresses, device names or personal files in commits, screenshots or logs.

## Security issues

Don’t open a public issue for a vulnerability. Follow [SECURITY.md](SECURITY.md).
