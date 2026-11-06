# Where it works

[← Back to the README](../README.md)

The phone and the PCs have to be able to see each other on the same network. Nothing goes through the internet.

| Situation | Works? |
|---|---|
| Home Wi-Fi, each device on any band (2.4 or 5 GHz) | Yes |
| School or office Wi-Fi | Yes, if the network lets devices see each other |
| iPhone hotspot, with the PC connected to it | Yes |
| No router at all | Yes, with [Wi-Fi Direct](panel.md#no-router-wi-fi-direct) from the panel (Windows) |
| Café or hotel | Maybe: many isolate clients. If it fails, use the phone’s hotspot |
| iPhone on cellular data and PC on another network | No |
| VPN on the phone or the PC | Maybe: it may keep them from seeing each other. Turn it off if it doesn’t connect |

## Checking a new network

If the shortcut fails on a new network, open these two addresses in Safari on the phone (the IP is in the tray icon’s tooltip and in Settings):

- `http://<PC-IP>:8765/ping`
- `http://<name>.local:8765/ping`

| Result | Meaning |
|---|---|
| Both load | The network is fine; check the shortcut ([Troubleshooting](troubleshooting.md)) |
| The IP loads but `.local` doesn’t | The router blocks multicast. Use `fallback` instead of `server` in the shortcuts |
| Neither loads | The network isolates devices. Use the phone’s hotspot or Wi-Fi Direct |

Type the full `http://`: Safari may switch to https otherwise.
