"""CopyOver web pages: the desktop panel, the live web app for phones (up to 10 GB) and Wi-Fi Direct."""
import html
import json
import re
from urllib.parse import quote

import bundle
import i18n

# The pages live in web/ as normal HTML, CSS and JS files. «<!-- include: x -->» pastes another file
# of that folder in place, so each page is still served as a single document.
WEB_FOLDER = bundle.resource("web")


def _page(name: str) -> str:
    text = (WEB_FOLDER / name).read_text(encoding="utf-8")
    return re.sub(r"<!-- include: ([\w.-]+) -->", lambda m: _page(m.group(1)), text)


# CopyOver logo: two clipboards, the back one outlined and the front one solid. The same shapes are in
# tray.draw_logo and in android/.../drawable/ic_bridge.xml.
SVG_RADAR_LOGO = _page("logo.svg")

HTML_APP = _page("panel.html")

EXPIRED_QR_PAGE = _page("expired-qr.html")

CONFIGURE_PAGE = _page("configure.html")


def with_shortcuts_qr(page: str, qr_svg) -> str:
    """Puts in each iPhone shortcut button the QR of its link; the button shows it in a modal."""
    for url in re.findall(r'<span class="shortcut-qr" data-qr="([^"]+)" hidden></span>', page):
        page = page.replace(f'data-qr="{url}" hidden></span>', f'data-qr="{url}" hidden>{qr_svg(url)}</span>', 1)
    return page


def shortcuts_config(pc_name: str, base: str, key: str) -> str:
    """What the «Link PC» shortcut saves in Shortcuts/copyover/[Device Name].txt. «key» is what the shortcuts
    read; «clave» stays for the ones installed before the English names."""
    port = base.rsplit(":", 1)[-1]
    return json.dumps({"key": key, "clave": key, "server": f"http://{pc_name}.local:{port}", "fallback": base},
                      ensure_ascii=False, separators=(",", ":"))


def configure_page(pc_name: str, base: str, key: str, kind: str, language: str | None = None) -> str:
    """Fills in the page the phone sees after scanning the panel's QR, in the phone's language when known."""
    language = language or i18n.current()
    translate = (lambda text: i18n._dictionary().get(text, text)) if language == "es" else (lambda text: text)
    type_name = {"iphone": "iPhone", "ipad": "iPad", "android": "Android"}.get(kind, translate("device"))
    # Opens the «Link PC» shortcut, passing it the configuration as input text.
    link = "shortcuts://run-shortcut?name=Link%20PC&input=text&text=" + quote(shortcuts_config(pc_name, base, key), safe="")
    # The title has placeholders, which translate_html doesn't treat as text: it's translated separately.
    title = "Link this {{TYPE}} with {{PC}}"
    return (i18n.translate_html(CONFIGURE_PAGE, language)
            .replace(title, translate(title))
            .replace("{{TYPE}}", type_name)
            .replace("{{PC}}", html.escape(pc_name))
            .replace("{{KEY}}", html.escape(key))
            .replace("{{SHORTCUT_LINK}}", html.escape(link))
            .replace("{{BASE}}", html.escape(base)))

PREVIEW_PAGE = _page("preview.html")

GUEST_PAGE = _page("guest.html")
