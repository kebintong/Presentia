package main

import (
	"context"
	"encoding/base64"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"time"

	wailsruntime "github.com/wailsapp/wails/v2/pkg/runtime"
)

const sidecarPort = "7788"
const sidecarURL = "http://127.0.0.1:" + sidecarPort

// App is the main application struct bound to the Wails runtime.
type App struct {
	ctx     context.Context
	sidecar *exec.Cmd
}

// NewApp creates the App struct.
func NewApp() *App {
	return &App{}
}

// startup is called when the app starts. It launches the Python sidecar
// and waits until the engine is ready (AI models loaded).
func (a *App) startup(ctx context.Context) {
	a.ctx = ctx

	// Locate the project root — the folder that contains app/sidecar.py.
	// We probe several candidate paths because the exe location differs between
	// wails dev (temp dir), wails build (presentia-desktop/build/bin/), and
	// the installed product (next to Presentia.exe).
	exe, err := os.Executable()
	if err != nil {
		wailsruntime.LogError(ctx, "Could not locate executable: "+err.Error())
		return
	}
	exeDir := filepath.Dir(exe)

	findProjectRoot := func() string {
		sidecarPy := filepath.Join("app", "sidecar.py")
		candidates := []string{
			// Production: exe is in presentia-desktop/build/bin/ → go up 3 levels
			filepath.Join(exeDir, "..", "..", ".."),
			// Production: exe is in presentia-desktop/build/bin/ → go up 2 levels
			filepath.Join(exeDir, "..", ".."),
			// Some wails builds put exe one level above bin/
			filepath.Join(exeDir, ".."),
			// Dev: CWD is presentia-desktop/ → go up 1 level
			func() string {
				cwd, _ := os.Getwd()
				return filepath.Join(cwd, "..")
			}(),
			// Dev: CWD is the project root itself
			func() string {
				cwd, _ := os.Getwd()
				return cwd
			}(),
		}
		for _, c := range candidates {
			abs, _ := filepath.Abs(c)
			if _, err := os.Stat(filepath.Join(abs, sidecarPy)); err == nil {
				return abs
			}
		}
		return ""
	}

	projectRoot := findProjectRoot()
	if projectRoot == "" {
		// Last resort: one level above the exe; the sidecar will likely fail
		// to start but at least the app window opens.
		projectRoot, _ = filepath.Abs(filepath.Join(exeDir, ".."))
		wailsruntime.LogWarning(ctx, "Could not locate app/sidecar.py — guessing project root: "+projectRoot)
	} else {
		wailsruntime.LogInfo(ctx, fmt.Sprintf("Project root: %s", projectRoot))
	}

	// PRODUCTION: look for the PyInstaller-frozen sidecar shipped alongside
	// the app binary (presentia-desktop/build/bin/sidecar/ once packaged by
	// Inno Setup — see build/installer.iss).
	sidecarExeName := "presentia-sidecar"
	if runtime.GOOS == "windows" {
		sidecarExeName += ".exe"
	}
	frozenSidecar := filepath.Join(exeDir, "sidecar", sidecarExeName)

	// Never talk to a sidecar left over from an earlier run: it may be old
	// code, and it keeps the webcam and port busy.
	clearStaleSidecar(ctx)

	var cmd *exec.Cmd
	if _, err := os.Stat(frozenSidecar); err == nil {
		wailsruntime.LogInfo(ctx, "Using bundled sidecar: "+frozenSidecar)
		cmd = exec.Command(frozenSidecar, "--host", "127.0.0.1", "--port", sidecarPort)
		cmd.Dir = filepath.Dir(frozenSidecar)
	} else {
		// DEV FALLBACK: no frozen sidecar found — use the venv Python if
		// present (avoids dependency on Python being on the system PATH).
		wailsruntime.LogInfo(ctx, "No bundled sidecar — dev mode fallback (uvicorn), root: "+projectRoot)

		// Prefer the project's own venv so we get the right dependencies.
		pythonBin := filepath.Join(projectRoot, ".venv", "Scripts", "python.exe")
		if runtime.GOOS != "windows" {
			pythonBin = filepath.Join(projectRoot, ".venv", "bin", "python3")
		}
		if _, err := os.Stat(pythonBin); err != nil {
			// venv not found — fall back to whatever python is on PATH
			if runtime.GOOS == "windows" {
				pythonBin = "python"
			} else {
				pythonBin = "python3"
			}
		}

		cmd = exec.Command(
			pythonBin,
			"-m", "uvicorn",
			"app.sidecar:app",
			"--host", "127.0.0.1",
			"--port", sidecarPort,
			"--log-level", "warning",
		)
		cmd.Dir = projectRoot
	}

	// Per-user, always-writable data dir (SQLite DB, etc.) — read by
	// app/data/db.py's _resolve_db_path(). Falls back to APPDATA on
	// Windows, ~/Library/Application Support on macOS, XDG on Linux.
	env := os.Environ()
	if appData, _ := os.UserConfigDir(); appData != "" {
		env = append(env, "PRESENTIA_DATA_DIR="+filepath.Join(appData, "Presentia"))
	}
	// The sidecar watches this process and exits as soon as it is gone —
	// a second guarantee next to the Job Object (superviseChild), and the
	// only one on Linux.
	env = append(env, fmt.Sprintf("PRESENTIA_PARENT_PID=%d", os.Getpid()))
	cmd.Env = env

	// Redirect sidecar output to log files and hide its console window on Windows.
	tmpDir := os.TempDir()
	stdoutLog, err := os.Create(filepath.Join(tmpDir, "presentia-sidecar-stdout.log"))
	if err == nil {
		cmd.Stdout = stdoutLog
	}
	stderrLog, err := os.Create(filepath.Join(tmpDir, "presentia-sidecar-stderr.log"))
	if err == nil {
		cmd.Stderr = stderrLog
	}

	// Hide the sidecar's console window (Windows only; a no-op elsewhere).
	// The flags live in a build-tagged file because syscall.SysProcAttr has
	// no HideWindow/CreationFlags fields on Linux, which broke the Linux build.
	hideConsoleWindow(cmd)

	if err := cmd.Start(); err != nil {
		wailsruntime.LogError(ctx, "Failed to start Python sidecar: "+err.Error())
		return
	}
	a.sidecar = cmd

	// Tie the sidecar's lifetime to ours at the OS level. Without this, a
	// crash or an End Task leaves presentia-sidecar.exe running, holding the
	// install folder open so upgrades and uninstalls fail with "file in use".
	superviseChild(cmd.Process.Pid)
	wailsruntime.LogInfo(ctx, "Python sidecar started (pid "+fmt.Sprint(cmd.Process.Pid)+")")

	// Poll until the sidecar health endpoint responds (up to 120 s for model load)
	deadline := time.Now().Add(120 * time.Second)
	for time.Now().Before(deadline) {
		resp, err := http.Get(sidecarURL + "/api/engine/status")
		if err == nil {
			resp.Body.Close()
			wailsruntime.LogInfo(ctx, "Sidecar is ready")
			break
		}
		time.Sleep(500 * time.Millisecond)
	}

	// Register listeners for the native bubble window events.
	// bubble_win.go fires these via EventsEmit; we forward them to the frontend.
	for _, cmd := range []string{
		"bubble:screen_area", "bubble:win_picker",
		"bubble:launch", "bubble:stop", "bubble:quit",
		"bubble:failed",
	} {
		cmdCopy := cmd // capture
		wailsruntime.EventsOn(ctx, cmdCopy, func(data ...interface{}) {
			// Forward to the frontend JS listener
			wailsruntime.EventsEmit(ctx, "native:"+cmdCopy)
		})
	}

	wailsruntime.LogWarning(ctx, "Startup complete")
}

// clearStaleSidecar makes sure nothing from a previous run is serving our
// port: newer sidecars are asked to exit; anything still answering after
// that (an older build) is ended if it is recognisably ours.
func clearStaleSidecar(ctx context.Context) {
	c := &http.Client{Timeout: 700 * time.Millisecond}
	alive := func() bool {
		resp, err := c.Get(sidecarURL + "/api/engine/status")
		if err != nil {
			return false
		}
		resp.Body.Close()
		return true
	}
	if !alive() {
		return
	}
	wailsruntime.LogWarning(ctx, "A sidecar from an earlier run is still running — stopping it")
	if resp, err := c.Post(sidecarURL+"/api/shutdown", "application/json", nil); err == nil {
		resp.Body.Close()
	}
	for i := 0; i < 10; i++ {
		time.Sleep(200 * time.Millisecond)
		if !alive() {
			return
		}
	}
	killPortOwner(7788)
	time.Sleep(400 * time.Millisecond)
}

// beforeClose is called before the window is closed. Returning true blocks close.
func (a *App) beforeClose(ctx context.Context) (prevent bool) {
	return false
}

// shutdown is called when the app is about to exit. It kills the Python sidecar.
func (a *App) shutdown(ctx context.Context) {
	if a.sidecar != nil && a.sidecar.Process != nil {
		wailsruntime.LogInfo(ctx, "Shutting down Python sidecar…")
		_ = a.sidecar.Process.Kill()
	}
}

// ---------------------------------------------------------------------------
// Utility bindings exposed to the frontend via Wails JS runtime
// ---------------------------------------------------------------------------

// OpenImageFilesDialog opens a native multi-file picker filtered to images.
func (a *App) OpenImageFilesDialog() []string {
	files, err := wailsruntime.OpenMultipleFilesDialog(a.ctx, wailsruntime.OpenDialogOptions{
		Title: "Select student photos",
		Filters: []wailsruntime.FileFilter{
			{DisplayName: "Images (*.png;*.jpg;*.jpeg;*.bmp;*.webp)", Pattern: "*.png;*.jpg;*.jpeg;*.bmp;*.webp"},
		},
	})
	if err != nil || files == nil {
		return []string{}
	}
	return files
}

// SaveCSVDialog opens a native save-file dialog for CSV export.
func (a *App) SaveCSVDialog(defaultName string) string {
	path, err := wailsruntime.SaveFileDialog(a.ctx, wailsruntime.SaveDialogOptions{
		Title:           "Export attendance CSV",
		DefaultFilename: defaultName,
		Filters: []wailsruntime.FileFilter{
			{DisplayName: "CSV files (*.csv)", Pattern: "*.csv"},
		},
	})
	if err != nil {
		return ""
	}
	return path
}

// SaveCSVToFile opens the native save dialog and writes the CSV text to the
// chosen path. Returns the path written, or "" when the user cancels.
// The frontend fetches the CSV from the sidecar and hands the text here, so
// the export lands where the instructor wants it instead of silently
// dropping into the browser download folder.
func (a *App) SaveCSVToFile(defaultName string, content string) (string, error) {
	path := a.SaveCSVDialog(defaultName)
	if path == "" {
		return "", nil
	}
	if filepath.Ext(path) == "" {
		path += ".csv"
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		return "", err
	}
	return path, nil
}

// SaveExcelToFile opens the native save dialog and writes an .xlsx workbook.
// The frontend downloads the workbook from the sidecar and passes it base64
// encoded (Wails bindings carry strings, not bytes). Returns the path
// written, or "" when the user cancels.
func (a *App) SaveExcelToFile(defaultName string, contentB64 string) (string, error) {
	data, err := base64.StdEncoding.DecodeString(contentB64)
	if err != nil {
		return "", fmt.Errorf("invalid workbook data: %w", err)
	}
	path, err := wailsruntime.SaveFileDialog(a.ctx, wailsruntime.SaveDialogOptions{
		Title:           "Export to Excel",
		DefaultFilename: defaultName,
		Filters: []wailsruntime.FileFilter{
			{DisplayName: "Excel workbook (*.xlsx)", Pattern: "*.xlsx"},
		},
	})
	if err != nil || path == "" {
		return "", nil
	}
	if filepath.Ext(path) == "" {
		path += ".xlsx"
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		return "", err
	}
	return path, nil
}

// SaveTextToFile opens the native save dialog and writes plain text (a
// diagnostic report or the activity log). Returns the path written, or ""
// when the user cancels.
func (a *App) SaveTextToFile(defaultName string, content string) (string, error) {
	path, err := wailsruntime.SaveFileDialog(a.ctx, wailsruntime.SaveDialogOptions{
		Title:           "Save diagnostic report",
		DefaultFilename: defaultName,
		Filters: []wailsruntime.FileFilter{
			{DisplayName: "Text file (*.txt)", Pattern: "*.txt"},
		},
	})
	if err != nil || path == "" {
		return "", nil
	}
	if filepath.Ext(path) == "" {
		path += ".txt"
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		return "", err
	}
	return path, nil
}

// SidecarURL returns the base URL of the Python sidecar for the frontend.
func (a *App) SidecarURL() string {
	return sidecarURL
}

// WindowMinimise minimises the window.
func (a *App) WindowMinimise() {
	if a.ctx != nil {
		wailsruntime.WindowMinimise(a.ctx)
	}
}

// WindowToggleMaximise toggles between maximised and restored window state.
func (a *App) WindowToggleMaximise() {
	if a.ctx != nil {
		wailsruntime.WindowToggleMaximise(a.ctx)
	}
}

// WindowClose closes the application.
func (a *App) WindowClose() {
	if a.ctx != nil {
		wailsruntime.Quit(a.ctx)
	}
}

// ---------------------------------------------------------------------------
// Monitor Bubble bindings
// ---------------------------------------------------------------------------

// WindowInfo describes a visible top-level application window.
type WindowInfo struct {
	Title  string `json:"title"`
	Left   int    `json:"left"`
	Top    int    `json:"top"`
	Width  int    `json:"width"`
	Height int    `json:"height"`
	// Hwnd lets the sidecar follow the window when it moves or resizes.
	Hwnd uint64 `json:"hwnd"`
}

// GetOpenWindows returns all visible, titled top-level windows so the
// frontend "Select a Windows Tab" picker can list them.
// On non-Windows platforms it returns an empty slice.
func (a *App) GetOpenWindows() []WindowInfo {
	return enumWindows()
}

// SetAlwaysOnTop pins the Wails window above all other windows (or unpins it).
// When enable=true and compact=true, the window is also resized to a small
// floating panel (420 × 300) so it can float over Google Meet / Zoom.
// When enable=false the window is restored to a comfortable default size.
func (a *App) SetAlwaysOnTop(enable bool, compact bool) {
	if a.ctx == nil {
		return
	}
	wailsruntime.WindowSetAlwaysOnTop(a.ctx, enable)
	if enable && compact {
		wailsruntime.WindowSetSize(a.ctx, 420, 320)
	} else if !enable {
		wailsruntime.WindowSetSize(a.ctx, 1100, 700)
	}
}

// PrepareScreenPick minimizes the Presentia window so the Python sidecar can
// capture a clean screenshot of the desktop without Presentia appearing in it.
// The frontend should wait ~350 ms after this call before fetching the screenshot.
func (a *App) PrepareScreenPick() {
	if a.ctx == nil {
		return
	}
	wailsruntime.WindowMinimise(a.ctx)
}

// EnterPickerMode restores and maximizes the Presentia window so the in-app
// picker overlay fills as much of the screen as possible.
// Call this AFTER the screenshot has been fetched.
func (a *App) EnterPickerMode() {
	if a.ctx == nil {
		return
	}
	wailsruntime.WindowShow(a.ctx) // may be hidden in the tray (bubble mode)
	wailsruntime.WindowUnminimise(a.ctx)
	time.Sleep(80 * time.Millisecond)
	wailsruntime.WindowMaximise(a.ctx)
}

// ExitPickerMode restores the window from maximized back to a comfortable size
// once the user has finished selecting a region.
func (a *App) ExitPickerMode() {
	if a.ctx == nil {
		return
	}
	wailsruntime.WindowUnmaximise(a.ctx)
	bubbleTaskDone()
}

// BubbleTaskDone sends the window back to the tray after a bubble action
// that needed it on screen (Select Window). Does nothing otherwise.
func (a *App) BubbleTaskDone() {
	bubbleTaskDone()
}

// ShowMainWindow brings the window out of the tray, e.g. to show a message
// the instructor must see while the bubble is up.
func (a *App) ShowMainWindow() {
	if a.ctx == nil {
		return
	}
	wailsruntime.WindowShow(a.ctx)
	wailsruntime.WindowUnminimise(a.ctx)
}

// OpenBubble spawns the native Win32 floating bubble window that lives above
// all other apps on the screen.  Safe to call multiple times — only one bubble
// is ever created.
func (a *App) OpenBubble() {
	OpenFloatingBubble(a)
}

// SetBubbleTheme keeps the native bubble in step with the app's dark/light
// setting. The bubble is a native window and cannot read the stylesheet, so
// the frontend pushes the current theme down whenever it changes.
func (a *App) SetBubbleTheme(dark bool) {
	setBubbleThemeNative(dark)
}

// SetBubbleStyle turns the experimental iridescent logo and dial on or off,
// repainting the bubble in place if it is already on screen.
func (a *App) SetBubbleStyle(iridescent bool) {
	setBubbleStyleNative(iridescent)
}

// SetBubbleThemeKey makes the bubble follow Settings → Appearance: one of
// light, brutal, editorial, bento, iri, dark.
func (a *App) SetBubbleThemeKey(key string) {
	setBubbleThemeKeyNative(key)
}

// SetBubbleSource tells the bubble what the Monitor page will watch (kind is
// "", "area" or "window") and which class it is, so the bubble can show
// "Ready" and offer Start without the app on screen.
func (a *App) SetBubbleSource(kind, label, detail, classTitle string) {
	setBubbleSourceNative(kind, label, detail, classTitle)
}

// BubbleIsOpen reports whether the floating bubble is on screen (the Monitor
// page asks when it opens, since the bubble outlives the page).
func (a *App) BubbleIsOpen() bool {
	return bubbleIsOpenNative()
}

// SourcePicked is called by the Monitor page right after a screen area or
// window is chosen; when the choice came from the bubble it is confirmed
// with a Windows notification (the app window goes back to the tray).
func (a *App) SourcePicked(text string) {
	bubbleSourcePicked(text)
}

// SetHideFromCapture keeps every Presentia window out of screen recordings,
// screenshots and screen sharing (true) or lets them show (false, default).
func (a *App) SetHideFromCapture(hide bool) {
	setHideFromCaptureNative(hide)
}

// CloseBubble destroys the native bubble window.
func (a *App) CloseBubble() {
	CloseFloatingBubble()
}

// PipOpen pops the Live Face viewer out into its own always-on-top window
// that can be dragged outside the app. Returns false where that is not
// supported, and the frontend keeps its in-app panel instead. The window
// emits "pip:closed" when the user sends it back to its card.
func (a *App) PipOpen(title string) bool {
	return openPipNative(a, title)
}

// PipFrame hands the pop-out viewer the latest base64 JPEG frame ("" = idle).
func (a *App) PipFrame(jpegB64 string) {
	pipFrameNative(jpegB64)
}

// PipStatus updates the pop-out viewer's badge, step count and prompt.
func (a *App) PipStatus(badge, count, msg, idleText string) {
	pipStatusNative(badge, count, msg, idleText)
}

// PipClose closes the pop-out viewer if it is open.
func (a *App) PipClose() {
	closePipNative()
}
