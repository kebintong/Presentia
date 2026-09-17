//go:build windows

package main

import (
	"syscall"
	"unsafe"
)

var (
	user32                      = syscall.NewLazyDLL("user32.dll")
	kernel32                    = syscall.NewLazyDLL("kernel32.dll")
	procEnumWindows             = user32.NewProc("EnumWindows")
	procGetWindowTextW          = user32.NewProc("GetWindowTextW")
	procGetWindowRect           = user32.NewProc("GetWindowRect")
	procIsWindowVisible         = user32.NewProc("IsWindowVisible")
	procGetWindowLongW          = user32.NewProc("GetWindowLongW")
	procGetWindowThreadProcessId = user32.NewProc("GetWindowThreadProcessId")
	procGetCurrentProcessId     = kernel32.NewProc("GetCurrentProcessId")
)

type rect struct{ Left, Top, Right, Bottom int32 }

const (
	wsVisible = 0x10000000
	wsCaption  = 0x00C00000 // real titled window
)

func enumWindows() []WindowInfo {
	var results []WindowInfo

	// Our own PID — skip all windows from this process (Presentia's WebView2 frames).
	myPID, _, _ := procGetCurrentProcessId.Call()

	cb := syscall.NewCallback(func(hwnd uintptr, _ uintptr) uintptr {
		// Must be visible
		vis, _, _ := procIsWindowVisible.Call(hwnd)
		if vis == 0 {
			return 1
		}

		// Must have a title
		buf := make([]uint16, 256)
		n, _, _ := procGetWindowTextW.Call(hwnd, uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)))
		if n < 4 { // very short titles are usually internal frames
			return 1
		}
		title := syscall.UTF16ToString(buf)

		// Skip windows from our own process (WebView2 host, child frames, etc.)
		var winPID uint32
		procGetWindowThreadProcessId.Call(hwnd, uintptr(unsafe.Pointer(&winPID)))
		if uintptr(winPID) == myPID {
			return 1
		}

		// Must be a proper captioned window (has a title bar)
		// GWL_STYLE = -16 → two's-complement as uintptr
		const gwlStyleArg = ^uintptr(15)
		style, _, _ := procGetWindowLongW.Call(hwnd, gwlStyleArg)
		if style&wsCaption == 0 {
			return 1
		}

		// Get bounds
		var r rect
		procGetWindowRect.Call(hwnd, uintptr(unsafe.Pointer(&r)))
		w := int(r.Right - r.Left)
		h := int(r.Bottom - r.Top)
		if w < 200 || h < 150 { // skip tiny / minimised windows
			return 1
		}

		results = append(results, WindowInfo{
			Title:  title,
			Left:   int(r.Left),
			Top:    int(r.Top),
			Width:  w,
			Height: h,
		})
		return 1 // continue enumeration
	})

	procEnumWindows.Call(cb, 0)
	return results
}
