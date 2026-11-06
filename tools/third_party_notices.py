"""Writes THIRD-PARTY-NOTICES.txt for the Windows build: every library that travels inside CopyOver.exe, with
its version, license and the full text of the license files it ships with.

It reads the installed packages, starting from requirements.txt and following their dependencies, so the
file always matches what PyInstaller packaged. Used from build.ps1:
    python tools/third_party_notices.py dist/CopyOver/THIRD-PARTY-NOTICES.txt
"""
from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent

# Libraries under the LGPL: CopyOver.exe carries them as loose .py files (see copyover.spec), so they can be
# replaced by another version without rebuilding CopyOver.
LGPL = ("pystray", "zeroconf")

HEADER = """\
CopyOver - third-party notices
==============================

CopyOver is distributed under the MIT License (see LICENSE.txt). It includes the software listed below,
each under its own license. The full text of each license follows its entry.

Python runtime: CopyOver.exe includes the Python {python} interpreter and standard library, under the
Python Software Foundation License (text at the end of this file). The launcher is the PyInstaller
bootloader, under the GPL with an exception that allows it to be used with programs under any license.

LGPL libraries ({lgpl}): they are included unmodified, as separate source files inside the _internal
folder, so you can replace them with your own version. Their source code is available from the project
pages listed below, and CopyOver's own source code is public.

Native libraries carried by the Python packages (their licenses are part of each package's text below):
OpenSSL inside cryptography, libheif and its codecs inside pillow-heif, and libjpeg, libpng, zlib,
FreeType and others inside Pillow. pywebview includes Microsoft's WebView2Loader.dll.
"""


def _name(text: str) -> str:
    return text.lower().replace("_", "-")


def included_packages(requirements: Path) -> list[metadata.Distribution]:
    """The installed distributions reachable from requirements.txt, for this platform and without extras."""
    pending = []
    for line in requirements.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            pending.append(Requirement(line))
    seen: dict[str, metadata.Distribution] = {}
    while pending:
        req = pending.pop()
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue
        key = _name(req.name)
        if key in seen:
            continue
        try:
            dist = metadata.distribution(req.name)
        except metadata.PackageNotFoundError:
            continue
        seen[key] = dist
        pending.extend(Requirement(r) for r in (dist.requires or []))
        for extra in req.extras:
            for r in dist.requires or []:
                dep = Requirement(r)
                if dep.marker is not None and dep.marker.evaluate({"extra": extra}):
                    pending.append(Requirement(f"{dep.name}{dep.specifier}"))
    return sorted(seen.values(), key=lambda d: _name(d.metadata["Name"]))


def license_of(dist: metadata.Distribution) -> str:
    md = dist.metadata
    if md.get("License-Expression"):
        return md["License-Expression"]
    short = (md.get("License") or "").strip()
    if short and "\n" not in short and len(short) < 80:
        return short
    classifiers = [c.split(" :: ")[-1] for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
    return ", ".join(classifiers) or "see the license text below"


def page(dist: metadata.Distribution) -> str:
    md = dist.metadata
    urls = {}
    for value in md.get_all("Project-URL") or []:
        label, _, url = value.partition(",")
        urls[label.strip().lower()] = url.strip()
    for key in ("source", "source code", "repository", "code", "homepage", "home"):
        if urls.get(key):
            return urls[key]
    return md.get("Home-page") or next(iter(urls.values()), "") or f"https://pypi.org/project/{md['Name']}/"


def license_texts(dist: metadata.Distribution) -> list[tuple[str, str]]:
    texts = []
    for file in dist.files or []:
        parts = [p.lower() for p in file.parts]
        if not parts[0].endswith(".dist-info"):
            continue
        name = parts[-1]
        if "licenses" in parts or name.startswith(("license", "licence", "copying", "notice", "authors")):
            if name.endswith((".dist-info", "record", "metadata")):
                continue
            path = Path(str(dist.locate_file(file)))
            if path.is_file():
                texts.append((file.name, path.read_text(encoding="utf-8", errors="replace").strip()))
    return texts


def generate(requirements: Path = ROOT / "requirements.txt") -> str:
    python = ".".join(map(str, sys.version_info[:3]))
    parts = [HEADER.format(python=python, lgpl=", ".join(LGPL))]
    for dist in included_packages(requirements):
        md = dist.metadata
        parts.append("\n" + "=" * 78 + f"\n{md['Name']} {dist.version}\nLicense: {license_of(dist)}\n"
                      f"Source: {page(dist)}\n" + "=" * 78)
        texts = license_texts(dist)
        if not texts:
            parts.append(f"\nThe package ships no license file; its license is {license_of(dist)}.")
        for name, text in texts:
            parts.append(f"\n--- {name} ---\n\n{text}")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        parts.append("\n" + "=" * 78 + f"\nPython {python}\nLicense: PSF-2.0\nSource: https://www.python.org/\n"
                      + "=" * 78 + "\n\n" + python_license.read_text(encoding="utf-8", errors="replace").strip())
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python tools/third_party_notices.py <output file>")
    destination = Path(sys.argv[1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(generate(), encoding="utf-8", newline="\r\n")
    print(f"{destination} ({len(included_packages(ROOT / 'requirements.txt'))} packages)")
