# Settings (`config.json`)

[← Back to the README](../README.md)

Most settings are in the panel’s **Settings**. They’re stored in `config.json`, which is created on first run and **never committed**. There’s an example with every field in [`config.example.json`](../config.example.json).

| Field | Default | Purpose |
|---|---|---|
| `key` | random | The key in the shortcuts’ `X-Key` header and between your PCs |
| `name` | `copyover-<computer name>` (`copyover-pc` or `puente-pc` in configs from older versions) | Network name: `<name>.local` |
| `port` | `8765` | Server port |
| `id` | 32 random hex characters | Unique computer ID for PC-to-PC discovery |
| `tray_icon` | `true` | Show the tray icon |
| `notifications` | `true` | System notification each time a file or text arrives |
| `download_folder` | `""` | Custom downloads folder (empty = default) |
| `keep_days` | `0` | Move received items to the Recycle Bin after 1, 7 or 30 days (`0` = never) |
| `language` | `en` | `en` or `es`. The `COPYOVER_LANGUAGE` environment variable overrides it |
| `phone_clipboard` | `images_videos` | What Receive PC leaves only on the clipboard, without asking where to save it: `images_videos` a single photo or video, `images` only a photo, and `files` nothing (everything also asks where to save it) |

## Where things are saved

They go in *Documents* unless you pick another folder in **Settings → Change folder**; *From CopyOver* is created inside the one you pick.

| What | Where |
|---|---|
| From your phones and tablets | `Documents/From CopyOver` |
| From another PC | `Documents/From CopyOver/From another PC` |
| From guests | `Documents/From CopyOver/From guests` |

Folders from older versions, right in `Downloads` (`From iPhone`, `From another PC`, `From guests` and their Spanish names), stay where they are, and the cleanup still covers them.

The log (`copyover.log`) is capped at 10 MB and keeps two older copies.

## Dual boot and several PCs

- **Dual boot:** use the same `key` and `name` on Windows and Linux, so the shortcuts work on both systems without changes.
- **Two PCs on at the same time:** same `key`, **different `name`** (for example `pc-windows` and `pc-fedora`).

## Changing the shared key

*Settings → Change the shared key* replaces it without restarting. Phones linked by QR code keep their own keys; only shortcuts set up by hand and devices linked with an earlier version have to be linked again.
