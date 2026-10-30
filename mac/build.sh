#!/bin/bash
# Build Presentia for macOS (Intel) on a Mac: mac/dist/Presentia.app and
# mac/dist/Presentia-mac-intel.dmg. Unsigned (see mac/README.md).
#
#   bash mac/build.sh
#
# Needs: Xcode Command Line Tools, Go 1.25, Node.js 22, Python 3.12,
# Wails CLI v2.16.0. Nothing outside mac/ is changed, except the usual
# untracked build output (dist/, build/ and presentia-desktop/build/bin).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/mac/dist"
VENV="$ROOT/mac/.venv"
APP="$OUT/Presentia.app"
cd "$ROOT"

step() { printf '\n== %s\n' "$1"; }

# Check the tools first, not after the slow engine build. `go install` puts
# wails in Go's own bin folder, which is often not on PATH.
export PATH="$PATH:$(go env GOPATH 2>/dev/null)/bin"
for tool in python3.12 go npm wails; do
  command -v "$tool" >/dev/null || { echo "Missing: $tool (see mac/README.md, One-time setup)"; exit 1; }
done

step "Python dependencies (mac/.venv)"
[ -d "$VENV" ] || python3.12 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip -q
# On an Intel Mac pip picks the newest versions that still ship Intel
# builds (onnxruntime 1.23.x, mediapipe 0.10.21); requirements.txt allows them.
"$VENV/bin/pip" install --progress-bar on -r requirements.txt "pyinstaller>=6.10"

step "Engine (PyInstaller, same build file as Windows)"
"$VENV/bin/pyinstaller" build/presentia-sidecar.spec --noconfirm --clean

step "Desktop app (Wails, darwin/amd64)"
# Wails writes its macOS templates into presentia-desktop/build/darwin on the
# first build; remove them again so the shared folder stays as it was.
had_darwin=0; [ -d presentia-desktop/build/darwin ] && had_darwin=1
(cd presentia-desktop && wails build -clean -platform darwin/amd64)
[ "$had_darwin" = 1 ] || rm -rf presentia-desktop/build/darwin

step "Put the engine inside the app"
rm -rf "$OUT" && mkdir -p "$OUT"
cp -R presentia-desktop/build/bin/Presentia.app "$APP"
# app.go looks for <folder of the app's executable>/sidecar/presentia-sidecar.
cp -R dist/presentia-sidecar "$APP/Contents/MacOS/sidecar"

# python.org's Python for Mac has no trusted HTTPS certificates of its own, so the
# face-model download failed (CERTIFICATE_VERIFY_FAILED). Start the engine through
# a launcher that points it at the certificate list bundled with it (certifi).
SIDECAR="$APP/Contents/MacOS/sidecar"
[ -f "$SIDECAR/_internal/certifi/cacert.pem" ] || { echo "certifi's cacert.pem is missing from the engine"; exit 1; }
mv "$SIDECAR/presentia-sidecar" "$SIDECAR/presentia-sidecar-bin"
cat > "$SIDECAR/presentia-sidecar" <<'LAUNCHER'
#!/bin/sh
here="$(cd "$(dirname "$0")" && pwd)"
export SSL_CERT_FILE="$here/_internal/certifi/cacert.pem"
exec "$here/presentia-sidecar-bin" "$@"
LAUNCHER
chmod +x "$SIDECAR/presentia-sidecar"

# macOS closes an app that opens the camera without saying why it needs it.
PLIST="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Delete :NSCameraUsageDescription" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSCameraUsageDescription string Presentia uses the camera to register students and check them in by face." "$PLIST"
# The engine's newest Intel builds (onnxruntime) need macOS 13 Ventura.
/usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion 13.0" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 13.0" "$PLIST"

step "Start the engine once"
port=7799
work="$(mktemp -d)"
PRESENTIA_DATA_DIR="$work" PRESENTIA_SKIP_ENGINE=1 \
  "$APP/Contents/MacOS/sidecar/presentia-sidecar" --host 127.0.0.1 --port "$port" >"$work/out.log" 2>&1 &
pid=$!
trap 'kill $pid 2>/dev/null || true' EXIT
for _ in $(seq 1 120); do
  curl -sf "http://127.0.0.1:$port/api/engine/status" >/dev/null && break
  kill -0 "$pid" 2>/dev/null || { cat "$work/out.log"; echo "The engine stopped while starting."; exit 1; }
  sleep 1
done
call() { curl -sf --max-time 120 -X "$1" -H 'Content-Type: application/json' ${3:+-d "$3"} "http://127.0.0.1:$port$2" >/dev/null \
  || { cat "$work/out.log"; echo "Failed: $1 $2"; exit 1; }; }
call GET /api/engine/status
class_id=$(curl -sf -X POST -H 'Content-Type: application/json' -d '{"name":"Smoke test"}' \
  "http://127.0.0.1:$port/api/classes" | "$VENV/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["id"])')
call GET "/api/classes/$class_id/export.xlsx"   # Excel export (loaded on first use)
call GET /api/diagnostics                       # website check, HTTPS certificates
call GET /api/perf                              # ONNX Runtime devices
# The engine's Python with only the bundled certificate list, like the launcher sets it up.
SSL_CERT_FILE="$SIDECAR/_internal/certifi/cacert.pem" "$VENV/bin/python" -c \
  'import urllib.request; urllib.request.urlopen("https://github.com", timeout=30)' \
  || { echo "HTTPS with the bundled certificates failed"; exit 1; }
kill "$pid"; trap - EXIT
echo "Engine OK."

step "Disk image"
stage="$(mktemp -d)"
cp -R "$APP" "$stage/"
ln -s /Applications "$stage/Applications"
hdiutil create -volname Presentia -srcfolder "$stage" -ov -format UDZO "$OUT/Presentia-mac-intel.dmg" >/dev/null
rm -rf "$stage"

printf '\nDone:\n  %s\n  %s\n' "$APP" "$OUT/Presentia-mac-intel.dmg"
