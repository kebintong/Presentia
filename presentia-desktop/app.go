package main

import (
	"context"
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

	// Locate the project root (one level up from presentia-desktop/)
	exe, err := os.Executable()
	if err != nil {
		wailsruntime.LogError(ctx, "Could not locate executable: "+err.Error())
		return
	}

	// In dev mode wails runs from the project root; in built mode the exe
	// is in presentia-desktop/build/bin — we need to go up to the project root.
	exeDir := filepath.Dir(exe)
	projectRoot := filepath.Join(exeDir, "..", "..")
	projectRoot, _ = filepath.Abs(projectRoot)

	// Try the dev-mode path first: CWD might already be the project root.
	if _, err := os.Stat(filepath.Join(projectRoot, "app", "sidecar.py")); err != nil {
		// Fallback: go up from the presentia-desktop dir
		projectRoot = filepath.Join(exeDir, "..")
		projectRoot, _ = filepath.Abs(projectRoot)
	}

	wailsruntime.LogInfo(ctx, fmt.Sprintf("Project root: %s", projectRoot))

	// Find the python interpreter
	pythonBin := "python"
	if runtime.GOOS != "windows" {
		pythonBin = "python3"
	}

	// Start the FastAPI sidecar
	cmd := exec.Command(
		pythonBin,
		"-m", "uvicorn",
		"app.sidecar:app",
		"--host", "127.0.0.1",
		"--port", sidecarPort,
		"--log-level", "warning",
	)
	cmd.Dir = projectRoot
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		wailsruntime.LogError(ctx, "Failed to start Python sidecar: "+err.Error())
		return
	}
	a.sidecar = cmd
	wailsruntime.LogInfo(ctx, "Python sidecar started (pid "+fmt.Sprint(cmd.Process.Pid)+")")

	// Poll until the sidecar health endpoint responds (up to 120 s for model load)
	deadline := time.Now().Add(120 * time.Second)
	for time.Now().Before(deadline) {
		resp, err := http.Get(sidecarURL + "/api/engine/status")
		if err == nil {
			resp.Body.Close()
			wailsruntime.LogInfo(ctx, "Sidecar is ready")
			return
		}
		time.Sleep(500 * time.Millisecond)
	}
	wailsruntime.LogWarning(ctx, "Sidecar did not become ready within 120 s")
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

