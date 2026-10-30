# Presentia for Mac (Intel)

Everything Mac-only lives in this folder. The Windows app, its installer and its workflows are not changed:
the Mac build reuses the same app code (`app/`, `presentia-desktop/`) and the same engine build file
(`build/presentia-sidecar.spec`).

**Status: first version, unsigned, built on your own Mac.**

## What works and what doesn't (yet)

| | Mac |
|---|---|
| Classes, students, registration (webcam, photos, website), reports, Excel export | ✅ |
| Phone app sync (Supabase) | ✅ |
| Meeting Monitor: **Browser tab** (Meet / Zoom / Teams in Chrome, Edge or Brave) | ✅ recommended |
| Meeting Monitor: **screen area** | ✅ needs Screen Recording permission |
| Meeting Monitor: **pick a window** (keeps watching when covered) | ❌ Windows only (Windows Graphics Capture) |
| Bubble mode, tray icon, stats chip, Live View | ❌ Windows only |
| GPU (DirectML) | ❌ runs on the CPU |
| In-app updates | ❌ not offered on Mac; build the new version instead (see below) |

Needs **macOS 13 Ventura or later** on an **Intel** Mac.

## One-time setup on the Mac

```bash
xcode-select --install
```

```bash
brew install go node python@3.12
```

```bash
go install github.com/wailsapp/wails/v2/cmd/wails@v2.16.0
```

Make sure `~/go/bin` is on your `PATH` (so `wails` is found): `wails doctor` should report no missing items.

## Build

From the Presentia folder:

```bash
bash mac/build.sh
```

It takes a few minutes and makes:

- `mac/dist/Presentia.app`
- `mac/dist/Presentia-mac-intel.dmg` — the file to share

The script also starts the built engine once and calls a few of its endpoints, so a broken build stops there
instead of on a teacher's Mac.

## Install and first open

1. Open the `.dmg` and drag **Presentia** to **Applications**.
2. Open it. The app is **not signed**, so a Mac that downloaded it will say it can't check it:
   **System Settings → Privacy & Security →** scroll down → **Open Anyway**. (Only the first time.)
3. Allow the **camera** when asked (registration and check-in). The first try after allowing it can fail: press
   Start again.
4. For **screen area** monitoring: **System Settings → Privacy & Security → Screen & System Audio Recording**,
   turn on **Presentia**, then quit and reopen it. Without this the picture is only the desktop background.

Data is kept in `~/Library/Application Support/Presentia`; face models download to `~/.insightface/models`
on first launch (about 195 MB), like on Windows.

## Updates

Releases contain only the Windows installer, so the Mac app doesn't offer updates. Build the new version with
`mac/build.sh` and drag it into Applications again; your data stays.

## Running from source (for development)

`wails dev` uses the engine from a `.venv` at the **project root** (not `mac/.venv`):

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
cd presentia-desktop && wails dev
```

## Not done yet

- **Signing and notarization** (Apple Developer ID, $99/year): removes "Open Anyway" and keeps the camera and
  screen permissions after each update (an unsigned app may ask again).
- **A GitHub Actions build** that publishes the `.dmg` with each release.
- **Apple Silicon** builds (`darwin/arm64`).
- Window capture (ScreenCaptureKit) and the bubble for Mac.
