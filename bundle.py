"""Where everything lives when CopyOver runs from source or packaged as CopyOver.exe.

From source, each helper window is its own script (preview.py, panel_window.py, dialogs.py). Packaged,
there's only CopyOver.exe, so the same exe is launched with --preview, --panel or --dialog and bridge.main
dispatches.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = {"bridge": "copyover.py", "preview": "preview.py", "panel": "panel_window.py", "dialog": "dialogs.py"}
# Flag of each helper window in CopyOver.exe. The Spanish ones (--vista, --dialogo) still work.
FLAGS = {"preview": "--preview", "panel": "--panel", "dialog": "--dialog"}
LEGACY_FLAGS = {"--vista": "preview", "--dialogo": "dialog"}
MODES = {**{flag: mode for mode, flag in FLAGS.items()}, **LEGACY_FLAGS}


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def data_folder() -> Path:
    """config.json, history, log and icon: next to CopyOver.exe (portable zip) or next to the source."""
    return Path(sys.executable).resolve().parent if frozen() else ROOT


def resource(name: str) -> Path:
    """File shipped inside the package (for example helper.ps1)."""
    return Path(getattr(sys, "_MEIPASS", ROOT)) / name if frozen() else ROOT / name


def gui_executable() -> str:
    """The interpreter without a console (pythonw on Windows), or CopyOver.exe when packaged."""
    if not frozen() and sys.platform == "win32":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return sys.executable


def command(mode: str, *args, executable=None, script=None) -> list:
    """Command line to launch a part of CopyOver in its own process."""
    arguments = [str(a) for a in args]
    if frozen():
        flag = [] if mode == "bridge" else [FLAGS[mode]]
        return [sys.executable, *flag, *arguments]
    path = Path(script).resolve() if script else ROOT / SCRIPTS[mode]
    return [str(executable or gui_executable()), str(path), *arguments]
