# PyInstaller recipe for CopyOver.exe (folder mode, no console). Used from build.ps1.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

ROOT = Path(SPECPATH)
sys.path.insert(0, str(ROOT))
from version import APP_NAME, REPOSITORY, VERSION  # noqa: E402

# File properties of CopyOver.exe (Details tab): the same version as version.py.
numbers = tuple(int(n) for n in VERSION.split(".")) + (0,) * (4 - len(VERSION.split(".")))
version_exe = VSVersionInfo(
    ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", APP_NAME),
            StringStruct("FileDescription", APP_NAME),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", APP_NAME),
            StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
            StringStruct("ProductName", APP_NAME),
            StringStruct("ProductVersion", VERSION),
            StringStruct("Comments", f"https://github.com/{REPOSITORY}"),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

# Our own modules are imported on demand (quick view, panel, diagnostics...): all of them are listed.
hidden = sorted(p.stem for p in ROOT.glob("*.py"))
data = [(str(ROOT / "helper.ps1"), "."), (str(ROOT / "web"), "web")]
binaries_list = []
for package in ("webview", "pystray", "zeroconf", "pillow_heif"):
    d, b, h = collect_all(package)
    data += d
    binaries_list += b
    hidden += h
hidden += collect_submodules("PIL")

a = Analysis(
    [str(ROOT / "copyover.py")],
    pathex=[str(ROOT)],
    binaries=binaries_list,
    datas=data,
    hiddenimports=hidden,
    excludes=["tests"],
    # pystray and zeroconf are LGPL: they go as loose .py files in _internal, not inside the archive, so
    # anyone can replace them (see tools/third_party_notices.py and THIRD-PARTY-NOTICES.txt).
    module_collection_mode={"pystray": "py", "zeroconf": "py"},
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CopyOver",
    console=False,
    icon=str(ROOT / "puente.ico"),
    version=version_exe,
)
coll = COLLECT(exe, a.binaries, a.datas, name="CopyOver")
