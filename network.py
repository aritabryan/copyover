"""This PC's networks: which IP to announce, with which mask, and whether another IP is on the same network.

Interfaces are read from the system (Get-NetIPAddress on Windows, «ip addr» on Linux) and cached: they're read
again at most once a minute, or sooner if the outbound IP is no longer in the list (the PC changed networks).
That way no panel request waits on PowerShell.
"""
import ipaddress
import json
import logging
import re
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

VALIDITY = 60.0
SUPPOSED_PREFIX = 24  # when the real mask couldn't be read

# Adapters that are never the network the phone comes in through: WSL and Hyper-V (vEthernet), virtual
# machines, containers and VPNs like Tailscale or ZeroTier.
_VIRTUAL_NAMES = re.compile(
    r"vethernet|\bwsl\b|hyper-v|default switch|virtualbox|vmware|vmnet|tailscale|zerotier|docker|podman"
    r"|wireguard|loopback|npcap|^br-|^virbr|^veth|^tun\d|^tap\d|^wg\d|^zt|^lo$",
    re.IGNORECASE,
)
_VIRTUAL_RANGES = (ipaddress.ip_network("100.64.0.0/10"),)  # Tailscale (CGNAT)


@dataclass(frozen=True)
class Interface:
    name: str
    ip: str
    prefix: int
    network_name: str = ""  # the network name (on Windows, the profile's: almost always the Wi-Fi's)
    description: str = ""  # the alias can be «Ethernet 2» even for a VirtualBox adapter

    @property
    def network(self):
        return ipaddress.ip_network(f"{self.ip}/{self.prefix}", strict=False)

    @property
    def virtual(self) -> bool:
        return is_virtual(self.ip, f"{self.name} {self.description}")


def is_virtual(ip: str, name: str = "") -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if address.is_loopback or address.is_link_local or address.is_unspecified:
        return True
    if any(address in range_header for range_header in _VIRTUAL_RANGES):
        return True
    return bool(name and _VIRTUAL_NAMES.search(name))


def _without_window() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def _read_windows() -> list:
    script = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        "$perfiles = @{}; Get-NetConnectionProfile -ErrorAction SilentlyContinue | "
        "ForEach-Object { $perfiles[[int]$_.InterfaceIndex] = [string]$_.Name }; "
        "$adaptadores = @{}; Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | "
        "ForEach-Object { $adaptadores[[int]$_.ifIndex] = [string]$_.InterfaceDescription }; "
        "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "Where-Object { $_.AddressState -eq 'Preferred' } | "
        "ForEach-Object { [pscustomobject]@{ InterfaceAlias = $_.InterfaceAlias; IPAddress = $_.IPAddress; "
        "PrefixLength = $_.PrefixLength; Red = $perfiles[[int]$_.InterfaceIndex]; "
        "InterfaceDescription = $adaptadores[[int]$_.InterfaceIndex] } } | ConvertTo-Json -Compress"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                       **_without_window())
    if r.returncode != 0:
        raise OSError(r.stderr.strip() or f"powershell ended with {r.returncode}")
    data = json.loads((r.stdout or "").strip() or "[]")
    return json_interfaces(data)


def json_interfaces(data) -> list:
    """The interfaces from Get-NetIPAddress's JSON output (with «Red», the network profile name)."""
    if isinstance(data, dict):
        data = [data]
    return [Interface(str(d.get("InterfaceAlias") or ""), str(d["IPAddress"]), int(d["PrefixLength"]),
                     str(d.get("Red") or ""), str(d.get("InterfaceDescription") or ""))
            for d in data if isinstance(d, dict)]


def _read_linux() -> list:
    r = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        raise OSError(r.stderr.strip())
    return read_ip_output(r.stdout)


def read_ip_output(text: str) -> list:
    """The interfaces from the output of «ip -o -4 addr show»."""
    interfaces = []
    for line in text.splitlines():
        m = re.match(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
        if m:
            interfaces.append(Interface(m.group(1), m.group(2), int(m.group(3))))
    return interfaces


_CACHE: dict = {"timestamp": float("-inf"), "interfaces": None, "failure_notified": False}
_LOCK = threading.Lock()


def interfaces(refresh: bool = False):
    """Active IPv4 interfaces, or None if they couldn't be read (then the old way is used)."""
    with _LOCK:
        if not refresh and time.monotonic() - _CACHE["timestamp"] < VALIDITY:
            return _CACHE["interfaces"]
    try:
        items = _read_windows() if sys.platform == "win32" else _read_linux()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        if not _CACHE["failure_notified"]:
            logging.warning("Couldn't read the network interfaces: %s", error)
            _CACHE["failure_notified"] = True
        items = None
    with _LOCK:
        _CACHE.update(timestamp=time.monotonic(), interfaces=items)
    return items


def forget():
    """Makes the next lookup read the interfaces again."""
    with _LOCK:
        _CACHE.update(timestamp=float("-inf"), interfaces=None)


def output_ip():
    """The IP the PC uses to reach the internet (without sending anything), or None if there's no route."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        return ip if ip and not ip.startswith("127.") else None
    except OSError:
        return None
    finally:
        s.close()


def _score(ip: str) -> int:
    if ip.startswith("172.20.10."):
        return 110  # iOS hotspot
    if ip.startswith("192.168.137."):
        return 105  # Windows hotspot / Wi-Fi Direct
    if ip.startswith("192.168."):
        return 100  # Wi-Fi / LAN
    if ip.startswith("10."):
        return 90
    if ip.startswith("172."):
        return 70
    return 10


def _candidates_without_interfaces() -> list:
    """Fallback when the interfaces couldn't be read: the IPs of the computer name, minus the virtual ones by
    range (172.16-31.x.1 is usually the WSL or Hyper-V gateway)."""
    candidates = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if is_virtual(ip):
                continue
            if ipaddress.ip_address(ip) in ipaddress.ip_network("172.16.0.0/12") and ip.endswith(".1"):
                continue
            candidates.append(ip)
    except (OSError, ValueError):
        pass
    return candidates


def ip_local() -> str:
    """The IP that's announced and goes in the QR. Never a virtual adapter's: if only those are left,
    127.0.0.1."""
    output = output_ip()
    items = interfaces()
    if items is not None and output and all(i.ip != output for i in items):
        items = interfaces(refresh=True)  # the PC changed networks since the last read
    if items is not None:
        own = [i.ip for i in items if not i.virtual]
        if output in own:
            return output
    else:
        if output and not is_virtual(output):
            return output
        own = _candidates_without_interfaces()
    if not own:
        return "127.0.0.1"
    return max(own, key=_score)


def _supposed_network(ip: str):
    return ipaddress.ip_network(f"{ip}/{SUPPOSED_PREFIX}", strict=False)


def prefix_of(ip: str) -> int:
    for i in interfaces() or []:
        if i.ip == ip:
            return i.prefix
    return SUPPOSED_PREFIX


def own_networks(extra=()) -> list:
    """The networks the PC is on, with their real mask. `extra` are our own IPs that may not be in the list
    yet (the announced one, the Wi-Fi Direct one): with no known mask, /24 is assumed."""
    networks = []
    for i in interfaces() or []:
        try:
            address = ipaddress.ip_address(i.ip)
        except ValueError:
            continue
        if address.is_loopback or address.is_link_local:
            continue
        if i.network not in networks:
            networks.append(i.network)
    for ip in extra:
        try:
            address = ipaddress.ip_address(ip or "")
        except ValueError:
            continue
        if address.is_loopback or address.is_unspecified or any(address in r for r in networks):
            continue
        networks.append(_supposed_network(ip))
    return networks


def same_network(ip: str, networks) -> bool:
    try:
        address = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    return any(address in r for r in networks)


def same_subnet(ip_a: str, ip_b: str) -> bool:
    """Whether ip_a is in ip_b's network (with ip_b's real mask if it's this PC's)."""
    try:
        return ipaddress.ip_address(ip_a or "") in ipaddress.ip_network(f"{ip_b}/{prefix_of(ip_b)}", strict=False)
    except ValueError:
        return False


def signature(networks) -> str:
    """Identifies the PC's set of networks, to know later whether it changed."""
    return ",".join(sorted(str(r) for r in networks))


def own_ips(items=None) -> list:
    """This PC's IPs a phone can come in through (no virtual adapters)."""
    items = interfaces() if items is None else items
    views = []
    for i in items or []:
        if not i.virtual and i.ip not in views:
            views.append(i.ip)
    return views


def all_ips(items=None) -> set:
    """All of this PC's IPs, virtual ones included: used to recognize our own answers."""
    items = interfaces() if items is None else items
    return {i.ip for i in items or []} | {"127.0.0.1"}


def announcement_signature(items) -> str:
    """Identifies the PC's real connections (adapter, IP, mask and network name). It changes when moving from
    one Wi-Fi to another even if both hand out the same IP, and doesn't change if only a virtual adapter
    shows up."""
    return ",".join(sorted(f"{i.name}={i.ip}/{i.prefix}@{i.network_name}" for i in items or [] if not i.virtual))
