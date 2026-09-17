package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	wailsruntime "github.com/wailsapp/wails/v2/pkg/runtime"
)

// â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// Update checking
//
// Presentia does not update itself. It asks GitHub what the newest release is
// and, if that is newer than the running build, shows a banner with a link.
// The user downloads PresentiaSetup.exe and runs it; because installer.iss
// keeps a stable AppId and installs per-user, that upgrades the existing
// install in place, with no admin rights and no loss of data (attendance.db
// lives in %APPDATA%\Presentia, which the installer never touches).
//
// Nothing here can break the app: every failure path is silent and simply
// reports "no update available".
// â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

// AppVersion is the version of this build.
//
// IMPORTANT: keep this in sync with MyAppVersion in build/installer.iss and
// with the git tag you publish. If this is lower than the newest release tag,
// users see the update banner â€” that comparison is the whole mechanism.
const AppVersion = "1.2.0"

// GitHubRepo is the "owner/name" of the repository whose Releases are checked.
//
// The repository must be public for unauthenticated clients to read its
// releases. If it is ever made private, update checking simply stops finding
// releases â€” it does not break the app.
const GitHubRepo = "kebintong/Presentia"

// How long to wait before asking GitHub again.
//
// GitHub's unauthenticated API allows 60 requests per hour per IP address. A
// whole computer lab shares one public IP, so an unthrottled check on every
// launch would rate-limit the entire school. Once a day per machine keeps a
// lab of any size far below the limit.
//
// If you ever do hit the limit, point manifestURL at a plain JSON file
// instead (raw.githubusercontent.com has no API rate limit) â€” the only thing
// that has to change is parseRelease.
const updateCheckInterval = 24 * time.Hour

// UpdateInfo is what the frontend receives.
type UpdateInfo struct {
	Available bool   `json:"available"`
	Current   string `json:"current"`
	Latest    string `json:"latest"`
	Notes     string `json:"notes"`
	URL       string `json:"url"`
	CheckedAt string `json:"checkedAt"`
}

// ghRelease mirrors the parts of GitHub's release JSON that matter here.
type ghRelease struct {
	TagName    string `json:"tag_name"`
	Name       string `json:"name"`
	Body       string `json:"body"`
	HTMLURL    string `json:"html_url"`
	Draft      bool   `json:"draft"`
	Prerelease bool   `json:"prerelease"`
	Assets     []struct {
		Name string `json:"name"`
		URL  string `json:"browser_download_url"`
	} `json:"assets"`
}

// GetAppVersion returns the running version, for display in the title bar.
func (a *App) GetAppVersion() string {
	return AppVersion
}

// CheckForUpdate reports whether a newer release exists.
//
// Pass force=true to skip the once-a-day throttle (used by a manual
// "Check for updates" button). Errors are returned for logging but the
// frontend is expected to ignore them: a failed check is not a user problem.
func (a *App) CheckForUpdate(force bool) (UpdateInfo, error) {
	info := UpdateInfo{Available: false, Current: AppVersion}

	if strings.HasPrefix(GitHubRepo, "CHANGEME") {
		return info, nil // update checking not configured yet
	}

	cachePath := updateCachePath()

	// Serve the cached answer unless the throttle window has passed.
	if !force {
		if cached, ok := readUpdateCache(cachePath); ok {
			cached.Current = AppVersion
			// Re-evaluate against the current build: an upgraded app must
			// stop showing a banner for a version it now IS.
			cached.Available = cached.Latest != "" && versionLess(AppVersion, cached.Latest)
			return cached, nil
		}
	}

	rel, err := fetchLatestRelease()
	if err != nil {
		// Offline, rate-limited, or GitHub is down â€” report no update and let
		// the next check try again.
		if a.ctx != nil {
			wailsruntime.LogInfo(a.ctx, "Update check skipped: "+err.Error())
		}
		return info, err
	}

	info.Latest = strings.TrimPrefix(rel.TagName, "v")
	info.Notes = strings.TrimSpace(rel.Body)
	info.URL = rel.HTMLURL
	info.CheckedAt = time.Now().Format(time.RFC3339)

	// Prefer linking straight at the installer when the release has one.
	for _, asset := range rel.Assets {
		if strings.HasSuffix(strings.ToLower(asset.Name), ".exe") {
			info.URL = asset.URL
			break
		}
	}

	info.Available = info.Latest != "" && versionLess(AppVersion, info.Latest)
	writeUpdateCache(cachePath, info)
	return info, nil
}

// OpenDownloadPage opens a release or installer URL in the user's browser.
// Only github.com links are accepted, so a tampered cache file cannot turn
// this into a way of launching arbitrary URLs.
func (a *App) OpenDownloadPage(url string) {
	if a.ctx == nil {
		return
	}
	if !strings.HasPrefix(url, "https://github.com/") &&
		!strings.HasPrefix(url, "https://objects.githubusercontent.com/") {
		wailsruntime.LogWarning(a.ctx, "Refused to open non-GitHub update URL: "+url)
		return
	}
	wailsruntime.BrowserOpenURL(a.ctx, url)
}

// â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// In-app download and install
// â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

// isTrustedUpdateURL guards every network and exec path below. The URL comes
// from a JSON cache on disk, so it is not automatically trustworthy.
func isTrustedUpdateURL(url string) bool {
	return strings.HasPrefix(url, "https://github.com/") ||
		strings.HasPrefix(url, "https://objects.githubusercontent.com/")
}

// DownloadUpdate fetches the installer into the user's temp folder and returns
// its path. Progress is reported to the frontend as "update:progress" events
// carrying an integer percentage (-1 when the size is unknown).
func (a *App) DownloadUpdate(url string) (string, error) {
	if !isTrustedUpdateURL(url) {
		return "", fmt.Errorf("refusing to download from an untrusted URL")
	}

	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("User-Agent", "Presentia-Updater/"+AppVersion)

	// No overall timeout: this is a ~75 MB download on school wifi. The
	// transport timeouts below cover a genuinely dead connection.
	client := &http.Client{
		Transport: &http.Transport{
			TLSHandshakeTimeout:   30 * time.Second,
			ResponseHeaderTimeout: 60 * time.Second,
		},
	}
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("download failed: %s", resp.Status)
	}

	dir := filepath.Join(os.TempDir(), "Presentia-update")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	dest := filepath.Join(dir, "PresentiaSetup.exe")

	// Remove a partial file from an interrupted attempt.
	_ = os.Remove(dest)

	out, err := os.Create(dest)
	if err != nil {
		return "", err
	}

	total := resp.ContentLength
	var written int64
	lastPct := -1
	buf := make([]byte, 256*1024)

	for {
		n, readErr := resp.Body.Read(buf)
		if n > 0 {
			if _, werr := out.Write(buf[:n]); werr != nil {
				out.Close()
				os.Remove(dest)
				return "", werr
			}
			written += int64(n)
			pct := -1
			if total > 0 {
				pct = int(written * 100 / total)
			}
			if pct != lastPct && a.ctx != nil {
				lastPct = pct
				wailsruntime.EventsEmit(a.ctx, "update:progress", pct)
			}
		}
		if readErr != nil {
			if readErr == io.EOF {
				break
			}
			out.Close()
			os.Remove(dest)
			return "", readErr
		}
	}
	if err := out.Close(); err != nil {
		os.Remove(dest)
		return "", err
	}

	// A truncated installer is worse than none: it would fail halfway through
	// replacing the app.
	if total > 0 && written != total {
		os.Remove(dest)
		return "", fmt.Errorf("download incomplete: got %d of %d bytes", written, total)
	}

	if a.ctx != nil {
		wailsruntime.EventsEmit(a.ctx, "update:progress", 100)
	}
	return dest, nil
}

// InstallUpdate launches the downloaded installer and closes Presentia.
//
// The app MUST exit for the installer to replace its own files, so this does
// not return in any meaningful sense â€” the frontend should have already warned
// the user. Inno Setup relaunches Presentia when it finishes.
func (a *App) InstallUpdate(installerPath string) error {
	if a.ctx == nil {
		return fmt.Errorf("app not ready")
	}
	info, err := os.Stat(installerPath)
	if err != nil {
		return fmt.Errorf("installer not found: %w", err)
	}
	if info.Size() < 1024*1024 {
		return fmt.Errorf("installer looks truncated (%d bytes)", info.Size())
	}

	// /SILENT            - progress window, no wizard pages to click through
	// /CLOSEAPPLICATIONS - let Setup close anything holding files it must replace
	// /NORESTART         - never reboot the machine
	// The installer's [Run] entry starts Presentia again afterwards.
	cmd := exec.Command(installerPath, "/SILENT", "/CLOSEAPPLICATIONS", "/NORESTART")
	cmd.Dir = filepath.Dir(installerPath)
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("could not start the installer: %w", err)
	}
	// Let Setup get going before we vanish, then quit so our files unlock.
	// Quitting also closes the job object, which takes the sidecar down with
	// us â€” that is what frees the install folder.
	go func() {
		time.Sleep(1200 * time.Millisecond)
		wailsruntime.Quit(a.ctx)
	}()
	return nil
}

// â”€â”€ internals â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

func fetchLatestRelease() (*ghRelease, error) {
	manifestURL := fmt.Sprintf("https://api.github.com/repos/%s/releases/latest", GitHubRepo)

	req, err := http.NewRequest(http.MethodGet, manifestURL, nil)
	if err != nil {
		return nil, err
	}
	// GitHub rejects requests without a User-Agent.
	req.Header.Set("User-Agent", "Presentia-Updater/"+AppVersion)
	req.Header.Set("Accept", "application/vnd.github+json")

	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("github returned %s", resp.Status)
	}

	var rel ghRelease
	if err := json.NewDecoder(resp.Body).Decode(&rel); err != nil {
		return nil, err
	}
	if rel.Draft || rel.Prerelease {
		return nil, fmt.Errorf("latest release is a draft or prerelease")
	}
	return &rel, nil
}

func updateCachePath() string {
	base, err := os.UserConfigDir()
	if err != nil || base == "" {
		base = os.TempDir()
	}
	dir := filepath.Join(base, "Presentia")
	_ = os.MkdirAll(dir, 0o755)
	return filepath.Join(dir, "update-check.json")
}

// readUpdateCache returns the stored result when it is still fresh.
func readUpdateCache(path string) (UpdateInfo, bool) {
	var info UpdateInfo
	data, err := os.ReadFile(path)
	if err != nil {
		return info, false
	}
	if err := json.Unmarshal(data, &info); err != nil {
		return info, false
	}
	checked, err := time.Parse(time.RFC3339, info.CheckedAt)
	if err != nil || time.Since(checked) > updateCheckInterval {
		return info, false
	}
	return info, true
}

func writeUpdateCache(path string, info UpdateInfo) {
	data, err := json.Marshal(info)
	if err != nil {
		return
	}
	_ = os.WriteFile(path, data, 0o644)
}

// versionLess reports whether version a is older than version b.
//
// Handles "v1.2.3", "1.2", and "1.2.3-beta1" (the suffix is ignored, so a
// prerelease never counts as newer than the release of the same number).
func versionLess(a, b string) bool {
	pa, pb := parseVersion(a), parseVersion(b)
	for i := 0; i < 3; i++ {
		if pa[i] != pb[i] {
			return pa[i] < pb[i]
		}
	}
	return false
}

func parseVersion(v string) [3]int {
	var out [3]int
	v = strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(v), "v"))
	// Drop any "-beta", "+build" suffix.
	if i := strings.IndexAny(v, "-+ "); i >= 0 {
		v = v[:i]
	}
	for i, part := range strings.Split(v, ".") {
		if i > 2 {
			break
		}
		n, err := strconv.Atoi(strings.TrimSpace(part))
		if err != nil {
			return out
		}
		out[i] = n
	}
	return out
}
