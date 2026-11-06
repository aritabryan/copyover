# Troubleshooting

[← Back to the README](../README.md)

| Symptom | Fix |
|---|---|
| The shortcut hangs and fails | The PC is off or logged out, or they’re not on the same Wi-Fi |
| Can’t find `copyover-pc.local` | Both must be on the **same Wi-Fi**; with the iPhone on cellular only, it won’t work. See [Where it works](network.md) |
| It worked and stopped working at home | Windows switched the network to **Private**. Allow CopyOver on private networks too: *Allow an app through Windows Firewall*, or the panel’s **Allow in firewall** button |
| “Wrong key” | Check the `X-Key` header (it’s case-sensitive), or link again with the panel’s QR code |
| “Nothing arrived” | In **Send PC**, the `file` field must hold the **If Result** variable. Reinstall the shortcut, or compare it with the [recipe](iphone-shortcuts.md#build-them-by-hand) |
| Safari: “couldn’t establish a secure connection” | Safari switched to https; type the full `http://`. The shortcuts don’t have this problem |
| Prompt about another site (teams.microsoft.com…) | The shortcut’s URL has a variable instead of the address. Tap **Don’t Allow** and fix it |
| Doesn’t start after a reboot | Make sure the CopyOver folder isn’t “online-only” (OneDrive) |
| A large file from the iPhone closes the shortcut | Over 2 GB, use the browser page instead. See [Large files](iphone-shortcuts.md#large-files) |
| The phone shows up as offline or doesn’t appear | It appears once its key reaches the PC: run Link PC, Send PC or Receive PC once |
| “Windows isn’t letting CopyOver use port 8765” | The port is reserved, usually by Hyper-V, WSL or Docker. Restart the PC. If it stays reserved (`netsh interface ipv4 show excludedportrange protocol=tcp`), change `port` in [`config.json`](configuration.md) and set up your phones again |
| On GNOME there’s no tray icon | Install the *AppIndicator* extension; CopyOver works without it |
| Linux: other devices can’t reach the PC | Open the port and mDNS in the firewall; see the [Linux guide](linux.md#tray-icon-and-firewall) |

Still stuck? Open an [issue](https://github.com/aritabryan/copyover/issues) with your system, the CopyOver version and what the panel’s diagnostics say. Don’t paste your key, IP addresses or device names.
