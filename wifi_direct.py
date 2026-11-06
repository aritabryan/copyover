"""The PC's own Wi-Fi network for phones when there's no router: Wi-Fi Direct, or Windows' mobile hotspot.

A small C# helper is compiled at run time (CodeDom + csc from .NET Framework, against the WinRT metadata in
System32\\WinMetadata, so nothing has to be installed) and driven from Windows PowerShell 5.1, because
PowerShell alone can't subscribe to WinRT events, and both things that matter here are events:

- `WiFiDirectAdvertisementPublisher.StatusChanged` carries the reason a start fails (`RadioNotAvailable`,
  `ResourceInUse`); polling `Status` only ever says «Aborted».
- `WiFiDirectConnectionListener.ConnectionRequested` fires when a phone joins the legacy (WPA2) network. As in
  Microsoft's WiFiDirectLegacyAP sample, the request has to be accepted with `WiFiDirectDevice.FromIdAsync`
  and the device kept referenced; if nobody accepts it, Windows drops the phone right after the handshake and
  the iPhone says «Unable to join the network».

If the publisher can't start (some drivers refuse an autonomous group owner while the radio isn't associated
to any network), the mobile hotspot is tried with the same SSID and password, sharing whatever connection
profile is there; the user's hotspot settings are put back when it stops.

The script's first output line says how it went:
    OK:wfd | OK:hotspot
    FAIL:<publisher status>:<publisher error>:<radio state>:<hotspot result>
    ERROR:<text>
Later lines (CLIENT:..., STATUS:...) only go to the log. Writing «STOP» to its stdin, or closing it, stops
everything and ends the process.
"""
import base64
import os

from i18n import t

_CSHARP = r"""
using System;
using System.Collections.Generic;
using System.Threading;
using Windows.Devices.Radios;
using Windows.Devices.WiFiDirect;
using Windows.Foundation;
using Windows.Networking.Connectivity;
using Windows.Networking.NetworkOperators;
using Windows.Security.Credentials;

public static class CopyOverWfd {
    static WiFiDirectAdvertisementPublisher pub;
    static WiFiDirectConnectionListener listener;
    static readonly Dictionary<string, WiFiDirectDevice> devices = new Dictionary<string, WiFiDirectDevice>();
    static readonly object gate = new object();
    static volatile string lastStatus = "Created";
    static volatile string lastError = "Success";
    static NetworkOperatorTetheringManager tethering;
    static string oldSsid, oldPass;
    public static volatile bool StopRequested;

    static void Out(string line) { lock (gate) { Console.Out.WriteLine(line); Console.Out.Flush(); } }

    static T Wait<T>(IAsyncOperation<T> op, int ms) {
        int waited = 0;
        while (op.Status == AsyncStatus.Started && waited < ms) { Thread.Sleep(50); waited += 50; }
        if (op.Status != AsyncStatus.Completed) throw new TimeoutException(op.Status.ToString());
        return op.GetResults();
    }

    static void Wait(IAsyncAction op, int ms) {
        int waited = 0;
        while (op.Status == AsyncStatus.Started && waited < ms) { Thread.Sleep(50); waited += 50; }
        if (op.Status != AsyncStatus.Completed) throw new TimeoutException(op.Status.ToString());
        op.GetResults();
    }

    public static string RadioState() {
        try {
            foreach (var r in Wait(Radio.GetRadiosAsync(), 5000))
                if (r.Kind == RadioKind.WiFi) return r.State.ToString();
            return "NoWiFi";
        } catch (Exception) { return "Unknown"; }
    }

    public static void WatchStdin() {
        var th = new Thread(() => {
            try {
                while (true) {
                    var l = Console.In.ReadLine();
                    if (l == null || l.Trim() == "STOP") break;
                }
            } catch (Exception) { }
            StopRequested = true;
        });
        th.IsBackground = true;
        th.Start();
    }

    // Legacy clients (phones joining by SSID and password) arrive here; they're dropped unless accepted.
    static void OnConnectionRequested(WiFiDirectConnectionListener s, WiFiDirectConnectionRequestedEventArgs a) {
        try {
            var req = a.GetConnectionRequest();
            string id = req.DeviceInformation.Id;
            var op = WiFiDirectDevice.FromIdAsync(id);
            op.Completed = (o, st) => {
                try {
                    if (st != AsyncStatus.Completed) { Out("CLIENT:rejected:" + st); return; }
                    var dev = o.GetResults();
                    if (dev == null) { Out("CLIENT:rejected:null"); return; }
                    dev.ConnectionStatusChanged += (d, x) => {
                        if (d.ConnectionStatus == WiFiDirectConnectionStatus.Disconnected) {
                            lock (devices) { devices.Remove(id); }
                            try { d.Dispose(); } catch (Exception) { }
                            Out("CLIENT:disconnected");
                        }
                    };
                    lock (devices) { devices[id] = dev; }
                    Out("CLIENT:connected");
                } catch (Exception e) { Out("CLIENT:error:" + e.Message.Replace("\r", " ").Replace("\n", " ")); }
            };
            req.Dispose();
        } catch (Exception e) { Out("CLIENT:error:" + e.Message.Replace("\r", " ").Replace("\n", " ")); }
    }

    public static bool StartPublisher(string ssid, string pass) {
        if (listener == null) {
            listener = new WiFiDirectConnectionListener();
            listener.ConnectionRequested += OnConnectionRequested;
        }
        for (int attempt = 0; attempt < 3; attempt++) {
            if (pub != null) { try { pub.Stop(); } catch (Exception) { } pub = null; }
            lastStatus = "Created"; lastError = "Success";
            var p = new WiFiDirectAdvertisementPublisher();
            p.StatusChanged += (s, a) => { lastStatus = a.Status.ToString(); lastError = a.Error.ToString(); };
            p.Advertisement.IsAutonomousGroupOwnerEnabled = true;
            p.Advertisement.ListenStateDiscoverability = WiFiDirectAdvertisementListenStateDiscoverability.Normal;
            p.Advertisement.LegacySettings.IsEnabled = true;
            p.Advertisement.LegacySettings.Ssid = ssid;
            var cred = new PasswordCredential();
            cred.Password = pass;
            p.Advertisement.LegacySettings.Passphrase = cred;
            pub = p;
            try { p.Start(); } catch (Exception e) { lastStatus = "Aborted"; lastError = "Exception " + e.HResult.ToString("X8"); }
            for (int i = 0; i < 40 && lastStatus == "Created"; i++) Thread.Sleep(100);
            if (lastStatus == "Created") lastStatus = p.Status.ToString();
            if (lastStatus == "Started") return true;
            Out("STATUS:attempt" + (attempt + 1) + ":" + lastStatus + ":" + lastError);
            // Retrying doesn't help with the radio off or the adapter taken by something else.
            if (lastError == "RadioNotAvailable" || lastError == "ResourceInUse") break;
            Thread.Sleep(1500);
        }
        return false;
    }

    public static bool PublisherRunning() { return pub != null && pub.Status == WiFiDirectAdvertisementPublisherStatus.Started; }
    public static string PublisherStatus() { return lastStatus + ":" + lastError; }

    static bool IsVirtual(string name) {
        string n = (name ?? "").ToLowerInvariant();
        return n.Contains("vethernet") || n.Contains("wsl") || n.Contains("hyper-v") || n.Contains("default switch");
    }

    // Mobile hotspot with the same SSID and password, sharing the internet profile if there is one, else any
    // profile Windows lets it share (virtual switches last, so Hyper-V's NAT isn't touched unless needed).
    public static string StartHotspot(string ssid, string pass) {
        var candidates = new List<ConnectionProfile>();
        var inet = NetworkInformation.GetInternetConnectionProfile();
        if (inet != null) candidates.Add(inet);
        var all = NetworkInformation.GetConnectionProfiles();
        foreach (var pass1 in new[] { 0, 1, 2 }) {
            foreach (var p in all) {
                if (candidates.Contains(p)) continue;
                bool connected = p.GetNetworkConnectivityLevel() != NetworkConnectivityLevel.None;
                bool virt = IsVirtual(p.ProfileName);
                if ((pass1 == 0 && connected && !virt) || (pass1 == 1 && !connected && !virt) || (pass1 == 2 && virt))
                    candidates.Add(p);
            }
        }
        string result = "NoProfile";
        int tries = 0;
        foreach (var prof in candidates) {
            if (tries >= 3) break;
            try {
                if (NetworkOperatorTetheringManager.GetTetheringCapabilityFromConnectionProfile(prof) != TetheringCapability.Enabled) continue;
                tries++;
                var m = NetworkOperatorTetheringManager.CreateFromConnectionProfile(prof);
                if (m.TetheringOperationalState == TetheringOperationalState.On) return "InUse";
                var cfg = m.GetCurrentAccessPointConfiguration();
                if (oldSsid == null) { oldSsid = cfg.Ssid; oldPass = cfg.Passphrase; }
                cfg.Ssid = ssid;
                cfg.Passphrase = pass;
                Wait(m.ConfigureAccessPointAsync(cfg), 10000);
                var r = Wait(m.StartTetheringAsync(), 20000);
                if (r.Status == TetheringOperationStatus.Success) { tethering = m; return "Success"; }
                result = r.Status.ToString();
            } catch (Exception e) { result = "Exception " + e.HResult.ToString("X8"); }
        }
        RestoreHotspot(null);
        return result;
    }

    public static bool HotspotRunning() {
        try { return tethering != null && tethering.TetheringOperationalState != TetheringOperationalState.Off; }
        catch (Exception) { return false; }
    }

    static void RestoreHotspot(NetworkOperatorTetheringManager m) {
        if (oldSsid == null) return;
        try {
            if (m == null) {
                var inet = NetworkInformation.GetInternetConnectionProfile();
                foreach (var p in NetworkInformation.GetConnectionProfiles()) {
                    if (inet == null) inet = p;
                }
                if (inet == null) return;
                m = NetworkOperatorTetheringManager.CreateFromConnectionProfile(inet);
            }
            var cfg = m.GetCurrentAccessPointConfiguration();
            cfg.Ssid = oldSsid;
            cfg.Passphrase = oldPass;
            Wait(m.ConfigureAccessPointAsync(cfg), 10000);
            oldSsid = null;
        } catch (Exception) { }
    }

    public static void StopAll() {
        if (pub != null) { try { pub.Stop(); } catch (Exception) { } }
        lock (devices) {
            foreach (var d in devices.Values) { try { d.Dispose(); } catch (Exception) { } }
            devices.Clear();
        }
        if (tethering != null) {
            try { Wait(tethering.StopTetheringAsync(), 15000); } catch (Exception) { }
            RestoreHotspot(tethering);
            tethering = null;
        }
    }
}
"""

_POWERSHELL = r"""
$ErrorActionPreference = 'Stop'
try {
    $fw = [System.Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory()
    $wm = Join-Path $env:SystemRoot 'System32\WinMetadata'
    $cp = New-Object System.CodeDom.Compiler.CompilerParameters
    $cp.GenerateInMemory = $true
    foreach ($r in @('System.dll', 'System.Core.dll', (Join-Path $fw 'System.Runtime.dll'),
                     (Join-Path $fw 'System.Runtime.InteropServices.WindowsRuntime.dll'),
                     (Join-Path $wm 'Windows.Foundation.winmd'), (Join-Path $wm 'Windows.Devices.winmd'),
                     (Join-Path $wm 'Windows.Networking.winmd'), (Join-Path $wm 'Windows.Security.winmd'))) {
        [void]$cp.ReferencedAssemblies.Add($r)
    }
    $src = $env:COPYOVER_WFD_CS
    $res = (New-Object Microsoft.CSharp.CSharpCodeProvider).CompileAssemblyFromSource($cp, $src)
    if ($res.Errors.HasErrors) {
        $first = @($res.Errors | Where-Object { -not $_.IsWarning })[0]
        [Console]::Out.WriteLine('ERROR:compile ' + $first.ErrorNumber + ' ' + $first.ErrorText)
        [Console]::Out.Flush()
        exit 1
    }
} catch {
    [Console]::Out.WriteLine('ERROR:' + ($_.Exception.Message -replace '\s+', ' '))
    [Console]::Out.Flush()
    exit 1
}
$ssid = $env:COPYOVER_WFD_SSID
$pass = $env:COPYOVER_WFD_CLAVE
[CopyOverWfd]::WatchStdin()
$modo = ''
if ($env:COPYOVER_WFD_FORZAR_HOTSPOT -ne '1' -and [CopyOverWfd]::StartPublisher($ssid, $pass)) {
    $modo = 'wfd'
} else {
    $wfd = [CopyOverWfd]::PublisherStatus()
    [CopyOverWfd]::StopAll()
    $radio = [CopyOverWfd]::RadioState()
    $hs = 'Skipped'
    if ($radio -ne 'Off' -and $radio -ne 'NoWiFi') { $hs = [CopyOverWfd]::StartHotspot($ssid, $pass) }
    if ($hs -eq 'Success') {
        $modo = 'hotspot'
    } else {
        [Console]::Out.WriteLine('FAIL:' + $wfd + ':' + $radio + ':' + $hs)
        [Console]::Out.Flush()
        exit 2
    }
}
[Console]::Out.WriteLine('OK:' + $modo)
if ($modo -eq 'hotspot') { [Console]::Out.WriteLine('STATUS:wfd:' + $wfd) }
[Console]::Out.Flush()
while (-not [CopyOverWfd]::StopRequested) {
    Start-Sleep -Milliseconds 500
    if ($modo -eq 'wfd' -and -not [CopyOverWfd]::PublisherRunning()) {
        [Console]::Out.WriteLine('STATUS:ended:' + [CopyOverWfd]::PublisherStatus()); break
    }
    if ($modo -eq 'hotspot' -and -not [CopyOverWfd]::HotspotRunning()) {
        [Console]::Out.WriteLine('STATUS:ended:hotspot'); break
    }
}
[CopyOverWfd]::StopAll()
[Console]::Out.Flush()
"""


def command() -> list[str]:
    """powershell.exe arguments; -EncodedCommand so no quote in the script is mangled on the command line."""
    encrypted = base64.b64encode(_POWERSHELL.encode("utf-16-le")).decode("ascii")
    return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encrypted]


def environment(ssid: str, key: str, base: dict | None = None) -> dict:
    """Environment for the script. The C# source, SSID and password go here rather than on the command line:
    the line would be too long, and a quote in the SSID can't break out of anything."""
    env = dict(base if base is not None else os.environ)
    env.update({"COPYOVER_WFD_CS": _CSHARP, "COPYOVER_WFD_SSID": ssid, "COPYOVER_WFD_CLAVE": key})
    return env


def is_result(line: str) -> bool:
    """Whether a line is the script's verdict (the ones before it are only for the log)."""
    return (line or "").strip().startswith(("OK:", "FAIL:", "ERROR:"))


def parse(line: str) -> tuple[str | None, str]:
    """(mode, '') when the network is up ("wfd" or "hotspot"), else (None, message for the panel)."""
    line = (line or "").strip()
    if line.startswith("OK:"):
        mode = line[3:].strip()
        return (mode if mode in ("wfd", "hotspot") else "wfd"), ""
    return None, error_message(line)


def error_message(line: str) -> str:
    """Turn the script's FAIL/ERROR line into something the user can act on."""
    line = (line or "").strip()
    if not line:
        return t("Couldn’t start Wi-Fi Direct ({detail})", detail=t("no response"))
    if line.startswith("FAIL:"):
        parts = line[5:].split(":")
        parts += [""] * (4 - len(parts))
        _state, error, radio, hotspot = parts[:4]
        if radio == "NoWiFi":
            return t("This PC doesn’t have a Wi-Fi adapter that can create the CopyOver network.")
        if radio in ("Off", "Disabled") or error == "RadioNotAvailable" or hotspot == "WiFiDeviceOff":
            return t("Turn on the PC’s Wi-Fi; it doesn’t need to be connected to a network.")
        if error == "ResourceInUse" or hotspot in ("InUse", "OperationInProgress"):
            return t("The PC’s Wi-Fi is busy with something else (mobile hotspot, projecting to another screen or "
                     "another Wi-Fi Direct network). Turn it off and try again.")
        return t("Windows didn’t let CopyOver create its network with this Wi-Fi adapter. Turn the PC’s Wi-Fi off "
                 "and on and try again; if it still fails, connect the PC and the phone to the same Wi-Fi network.")
    detail = line[6:].strip() if line.startswith("ERROR:") else line
    return t("Couldn’t start Wi-Fi Direct ({detail})", detail=detail[:200])
