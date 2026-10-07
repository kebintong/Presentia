//go:build windows

package main

import (
	"sync"
	"sync/atomic"
	"syscall"
	"unsafe"
)

// ── Showing Presentia in screen recordings ──────────────────────────────────
//
// Windows can keep a window out of every screen capture (recorders, the
// Snipping Tool, screen sharing in Meet/Zoom/Teams) with
// SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE). Presentia used to do that
// for all its floating windows, so they never showed in recordings. It is now
// a setting, off by default; when it is on it covers the main window too.
//
// The Meet monitor no longer relies on this to avoid filming itself: the
// sidecar paints Presentia's windows out of screen-area grabs (self_mask.py).

const (
	wdaNone               = 0x00
	wdaExcludeFromCapture = 0x11 // Windows 10 2004+
)

var (
	bSetWindowDisplayAffinity = bUser32.NewProc("SetWindowDisplayAffinity")
	gHideFromCapture          atomic.Bool
	gAffinityEnumOnce         sync.Once
	gAffinityEnumProc         uintptr
)

// applyCaptureAffinity sets one window to match the current setting.
func applyCaptureAffinity(hwnd uintptr) {
	if hwnd == 0 || bSetWindowDisplayAffinity.Find() != nil {
		return
	}
	mode := uintptr(wdaNone)
	if gHideFromCapture.Load() {
		mode = wdaExcludeFromCapture
	}
	bSetWindowDisplayAffinity.Call(hwnd, mode)
}

// setHideFromCaptureNative changes the setting and applies it to every
// top-level window Presentia has open right now (main window, bubble, dial,
// Live View, stats chip, pop-out). Windows opened later pick it up as they
// are created.
func setHideFromCaptureNative(hide bool) {
	gHideFromCapture.Store(hide)
	gAffinityEnumOnce.Do(func() {
		gAffinityEnumProc = syscall.NewCallback(func(hwnd uintptr, pid uintptr) uintptr {
			var owner uint32
			procGetWindowThreadProcessId.Call(hwnd, uintptr(unsafe.Pointer(&owner)))
			if uintptr(owner) == pid {
				applyCaptureAffinity(hwnd)
			}
			return 1
		})
	})
	me, _, _ := procGetCurrentProcessId.Call()
	procEnumWindows.Call(gAffinityEnumProc, me)
}
