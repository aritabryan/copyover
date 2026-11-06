# CopyOver on Android

[← Back to the README](../README.md)

A native Kotlin + Rust app for Android 8.0 or newer. It keeps your current keyboard and needs no root, debugging, ADB or Shizuku unless you want automatic sending. Nothing changes on the iPhone.

- [Install and link](#install-and-link)
- [Receiving and sending](#receiving-and-sending)
- [The notification](#the-notification)
- [App updates from the PC](#app-updates-from-the-pc)
- [Preparing a release (for maintainers)](#preparing-a-release-for-maintainers)

The app’s own documentation, the build instructions and its tests are in **[android/README.md](../android/README.md)**.

## Install and link

1. On the PC, open the panel → **Link phone → Android** and scan the QR code with the phone’s camera. The APK downloads **from your PC**, with no GitHub account; the link is valid for 30 minutes. It’s also on the [releases page](https://github.com/aritabryan/copyover/releases/latest).
2. Open it and tap **Install**. The first time, Android asks you to allow apps from that source.
3. Open CopyOver: it lists the PCs on the Wi-Fi. Pick yours, compare the code with the window that opens on the PC and accept there.

It remembers the PC after that, even if its IP changes. No QR code, address or key to type in the app. The phone gets its own key when you accept the pairing on the desktop.

> [!NOTE]
> The app id is now `dev.copyover.android`. It installs as a new app: the one with the old id doesn’t update to it, so install the new APK by hand, link the PC again and remove the old app.

## Receiving and sending

- **Receiving** what you copy on the PC (text and images) is automatic and turns on when you link. Turn it off with **Receive from the PC automatically**, or with **Turn off** in its notification, which stops both automatic directions. It needs a Windows PC: with a Linux PC, tap **Get from PC** instead.
- **Sending** has two modes:
  - **Manual** (the default): Send, Attach or Share.
  - **Automatic with Shizuku:** new text and image copies go to the PC on their own. Shizuku needs installing, starting and explicit permission.
- Receiving from the PC works with either sending mode.
- The app keeps several linked PCs, shows a local clipboard history and the status of each direction.
- Manual transfers show progress and can be canceled. The app can also accept files prepared from the PC panel and save, open or share them.
- Files between Android and the PC have no fixed size cap, subject to free disk space (update both devices).

## The notification

The notification has **Send**, **Receive** and **Attach** in its compact view. **Attach** opens the Android file picker to send screenshots, photos and documents.

Taking a screenshot doesn’t always put it on the clipboard: for that, use **Attach**, or **Share → CopyOver** from the gallery.

## App updates from the PC

The PC checks the repository’s latest **stable release** when CopyOver starts and every hour, through the public GitHub API: no account, token or GitHub CLI needed. If there’s a newer desktop version, the tray tells you once and the panel shows “Version … available” with a download link.

It downloads the APK once and keeps it in `.android-updates/`. The phone checks for a new version when it connects, and you can check by hand under **Updates**. Both must be on the same network. The phone gets the APK from the linked PC, never straight from the internet. Without internet or releases, the clipboard keeps working.

**Download** fetches the APK and **Install** opens the Android installer. The first time, Android may ask you to let CopyOver install apps. SHA-256, size, package name, a higher version and the same signing certificates as the installed app are all checked.

## Preparing a release (for maintainers)

From PowerShell:

```powershell
cd <repo-folder>
# Bump versionCode and versionName in android/app/build.gradle.kts.
.\android\build.ps1 -Sdk C:\Android -Release
.\android\prepare-update.ps1 -Sdk C:\Android
```

The last command reads the APK version and writes `Puente.apk` and `puente-update.json` to `android/app/build/update/`. Upload **both files to the same stable release**. Pushing code doesn’t publish an update.

Releases are signed with CopyOver’s own key, which stays out of the repository and only its owner keeps. The build reads `%USERPROFILE%\.android\copyover-signing.properties` (or the path in `COPYOVER_SIGNING_PROPERTIES`), with `storeFile`, `storePassword`, `keyAlias` and `keyPassword`. Without it, the release APK comes out unsigned. `prepare-update.ps1` refuses any APK signed with another key, because phones would reject it. Losing the key means no more updates: people would have to uninstall and install again.
