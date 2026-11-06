"""Checks what usually keeps the phone from reaching the PC and fixes it when possible.

Each problem is a dict with «key», «title», «detail» and «fix» (the button text, or empty if it can
only be explained). If a check fails on its own (slow PowerShell, no permissions), nothing is reported: better
to stay quiet than to scare with a false problem.
"""

import base64
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import bundle
from i18n import t
from version import APP_NAME

CLOUD_ONLY_ATTRIBUTE = 0x400000  # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS (OneDrive «online-only»)
ACTION_ALLOW, ACTION_BLOCK = 2, 4  # NetSecurity.Action values once converted to JSON
# NetworkCategory of the active network (Public=0, Private=1, DomainAuthenticated=2) -> Profile bit of the
# rule
PROFILE_BIT = {0: 4, 1: 2, 2: 1}


def _without_window() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def _ps_quotes(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def _windows_rules(exe, run):
    """{"reglas": [...], "redes": [...]} with the enabled inbound rules for the executable and, for each
    active network, its category, name and IPs, or None if it couldn't be found out."""
    script = (
        f"$r = Get-NetFirewallApplicationFilter -Program {_ps_quotes(exe)} -ErrorAction SilentlyContinue | "
        "Get-NetFirewallRule | Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' } | "
        "Select-Object Action, Profile; "
        "$n = Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object { [pscustomobject]@{ "
        "NetworkCategory = $_.NetworkCategory; Name = $_.Name; Ips = @(Get-NetIPAddress -InterfaceIndex "
        "$_.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { $_.IPAddress }) } }; "
        "ConvertTo-Json @{reglas=@($r); redes=@($n)} -Compress -Depth 3"
    )
    try:
        r = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                   capture_output=True, text=True, timeout=15, **_without_window())
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        data = json.loads((r.stdout or "").strip() or "{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _covers(rule, bit) -> bool:
    profile = rule.get("Profile", 0)
    return not profile or bool(profile & bit)  # 0 = all profiles


def _ips(network) -> list:
    ips = network.get("Ips") or []
    return [ips] if isinstance(ips, str) else [i for i in ips if isinstance(i, str)]


def _windows_firewall_problem(exe, run, ip=None):
    data = _windows_rules(exe, run)
    if data is None:
        return None
    rules = [r for r in (data.get("rules") or []) if isinstance(r, dict)]
    networks = [n for n in (data.get("networks") or []) if isinstance(n, dict)]
    # Only the network the phone comes in through counts (the one with the announced IP): a VPN like Tailscale
    # is usually a different network, of a different type, and the phone doesn't connect through it.
    networks = [n for n in networks if ip and ip in _ips(n)] or networks
    categories = [n.get("NetworkCategory") for n in networks]
    bits = [PROFILE_BIT[c] for c in categories if c in PROFILE_BIT] or [2]  # no network: assume Private
    blocked = any(r.get("Action") == ACTION_BLOCK and _covers(r, b) for r in rules for b in bits)
    allowed = all(any(r.get("Action") == ACTION_ALLOW and _covers(r, b) for r in rules) for b in bits)
    if blocked:
        detail = t("Windows has a rule that blocks CopyOver (it’s created automatically if you cancel the firewall "
                    "prompt). That’s why your phone can’t reach the PC.")
    elif not allowed:
        names = [n["Name"] for n in networks if isinstance(n.get("Name"), str) and n["Name"]]
        if len(names) == 1:
            detail = t("Windows Firewall won’t let your phone reach this PC on the “{network}” network. Tap “Allow in "
                        "firewall” and accept the Windows prompt.", network=names[0])
        else:
            detail = t("Windows Firewall won’t let your phone reach this PC on this network. Tap “Allow in "
                        "firewall” and accept the Windows prompt.")
    else:
        return None
    return {"key": "firewall", "title": t("The firewall is blocking connections"), "detail": detail,
            "fix": t("Allow in firewall")}


def _linux_firewall_problem(port, run):
    if not shutil.which("firewall-cmd"):
        return None
    try:
        state = run(["firewall-cmd", "--state"], capture_output=True, text=True, timeout=5)
        if state.returncode != 0:
            return None  # firewalld installed but stopped
        port_open = run(["firewall-cmd", f"--query-port={port}/tcp"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if port_open.returncode == 0:
        return None
    return {"key": "firewall", "title": t("firewalld is blocking port {port}", port=port),
            "detail": t("Fedora blocks incoming connections by default; your phone can’t reach CopyOver."),
            "fix": t("Open the port")}


def _attributes(path: Path) -> int:
    try:
        return getattr(os.stat(path), "st_file_attributes", 0)
    except OSError:
        return 0


def check(cfg, *, run=subprocess.run, platform=sys.platform, folder=None, ip=None, announced=True,
            exe=None, attributes=_attributes, repeated_name=False) -> list:
    """List of problems found; empty if everything is fine."""
    problems = []
    exe = exe or sys.executable
    folder = Path(folder or bundle.data_folder())
    port = cfg.get("port", 8765)

    if platform == "win32":
        firewall = _windows_firewall_problem(exe, run, ip)
        if attributes(folder) & CLOUD_ONLY_ATTRIBUTE:
            problems.append({
                "key": "onedrive", "title": t("The app folder is online-only"),
                "detail": t("OneDrive downloads {folder} every time it opens, which sometimes slows it down. In "
                             "File Explorer, right-click the folder and choose “Always keep on this device”.",
                             folder=folder),
                "fix": ""})
    else:
        firewall = _linux_firewall_problem(port, run)
    if firewall:
        problems.insert(0, firewall)

    if not ip or ip.startswith(("127.", "169.254.")):
        problems.append({
            "key": "no_network", "title": t("The PC isn’t connected to any network"),
            "detail": (t("Connect the PC to Wi-Fi (the same one as your phone) or turn on Wi-Fi Direct above.")
                        if sys.platform == "win32" else t("Connect the PC to Wi-Fi, the same one as the phone.")),
            "fix": ""})
    if not announced:
        name = cfg.get("name", "puente-pc")
        problems.append({
            "key": "mdns", "title": t("{name}.local isn’t being announced on the network", name=name),
            "detail": t("Shortcuts will use the backup IP, which changes if the router reassigns it. Restart {app} "
                         "from the tray; if it continues, make sure the network is set to “Private”.", app=APP_NAME),
            "fix": ""})
    if repeated_name:
        # zeroconf only renames the service instance: the <nombre>.local host stays shared and the phone may
        # reach the other PC («Clave incorrecta»).
        name = cfg.get("name", "puente-pc")
        problems.append({
            "key": "repeated_name",
            "title": t("Another PC on this network is also called “{name}”", name=name),
            "detail": t("Rename one of them in Settings so the shortcuts reach the right one."),
            "fix": ""})
    return problems


def fix(key, *, run=subprocess.run, platform=sys.platform, exe=None, port=8765) -> bool:
    """Applies the fix for a problem. Asks for administrator rights (UAC or pkexec)."""
    if key != "firewall":
        return False
    exe = exe or sys.executable
    try:
        if platform == "win32":
            r = run(_windows_elevated_command(exe), capture_output=True, text=True, timeout=120, **_without_window())
        else:
            order = (f"firewall-cmd --permanent --add-port={port}/tcp && "
                     f"firewall-cmd --add-port={port}/tcp")
            r = run(["pkexec", "sh", "-c", order], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        logging.exception("Couldn't apply fix %s", key)
        return False
    if r.returncode != 0:
        logging.warning("Fix %s ended with code %s: %s", key, r.returncode, (r.stderr or "").strip())
    return r.returncode == 0


def _windows_elevated_command(exe) -> list:
    """PowerShell as administrator: removes the program's blocks and adds the «CopyOver» rule."""
    internal = (
        f"$p = {_ps_quotes(exe)}; "
        "Get-NetFirewallApplicationFilter -Program $p -ErrorAction SilentlyContinue | Get-NetFirewallRule | "
        "Where-Object { $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' } | Remove-NetFirewallRule; "
        f"Get-NetFirewallRule -DisplayName {_ps_quotes(APP_NAME)} -ErrorAction SilentlyContinue | "
        "Remove-NetFirewallRule; "
        f"New-NetFirewallRule -DisplayName {_ps_quotes(APP_NAME)} -Direction Inbound -Program $p "
        "-Action Allow -Profile Private,Public | Out-Null"
    )
    encoded = base64.b64encode(internal.encode("utf-16-le")).decode()
    external = ("$p = Start-Process powershell -Verb RunAs -WindowStyle Hidden -Wait -PassThru "
               f"-ArgumentList '-NoProfile','-EncodedCommand','{encoded}'; exit $p.ExitCode")
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", external]


def remove_windows_rule(run=subprocess.run) -> bool:
    """On uninstall: deletes the «CopyOver» rule if it exists (asks for UAC only then)."""
    try:
        exists = run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                         f"if (Get-NetFirewallRule -DisplayName {_ps_quotes(APP_NAME)} -ErrorAction "
                         "SilentlyContinue) { exit 0 } else { exit 1 }"],
                        capture_output=True, text=True, timeout=15, **_without_window())
        if exists.returncode != 0:
            return True
        internal = f"Get-NetFirewallRule -DisplayName {_ps_quotes(APP_NAME)} | Remove-NetFirewallRule"
        encoded = base64.b64encode(internal.encode("utf-16-le")).decode()
        r = run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "$p = Start-Process powershell -Verb RunAs -WindowStyle Hidden -Wait -PassThru "
                    f"-ArgumentList '-NoProfile','-EncodedCommand','{encoded}'; exit $p.ExitCode"],
                   capture_output=True, text=True, timeout=120, **_without_window())
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
