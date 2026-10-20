# Windows installer and updates

CapsWriter Lite uses the same Windows distribution architecture as Cut Player:

- Electron Builder NSIS produces a normal Setup.exe with shortcuts and uninstaller.
- electron-updater downloads update metadata (latest.yml) and NSIS blockmaps from GitHub Releases.
- DifferentialDownloader uses the old and new .exe.blockmap to download changed blocks.
- Differential failures fall back to the complete installer; the fallback path retries with HTTP Range
  and checks the full downloaded file against SHA-512.
- The Electron process remains headless; it launches the original Python tray (with the
  full-size original models) as a child. Users only see the Python tray.
- The tray has a "检查软件更新" menu command that launches a second Electron instance with
  --check-for-updates; the primary instance handles the request using Electron's
  single-instance lock and presents the update/install prompts.
- NSIS updates stop the child before installation and then restart the app.
- User settings and model weights stay in %LOCALAPPDATA%\CapsWriterLite, never in
  the installation tree. Uninstall does not delete this directory.

The previous v0.2.0 was ZIP-only without a blockmap, so v0.3.0 is the first installed
baseline; subsequent installer versions can actually download delta blocks.

Development notes: the Electron main process is intentionally windowless to avoid a
second, duplicate GUI. Source code runs can still invoke desktop/app.py directly,
but automatic NSIS updates are only available from installed Setup.exe versions.
