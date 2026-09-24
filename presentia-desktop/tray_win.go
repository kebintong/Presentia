//go:build windows

package main

import (
	"bytes"
	_ "embed"
	"image"
	"image/draw"
	"image/png"
	"math"
	"sync"
	"syscall"
	"unsafe"

	wailsruntime "github.com/wailsapp/wails/v2/pkg/runtime"
)

// ── System tray while in bubble mode ─────────────────────────────────────────
//
// Opening the bubble hides the main window completely — no taskbar button, no
// Alt+Tab / Task View entry — and puts a Presentia icon in the notification
// area instead. Left-click brings the window back; right-click offers the
// Live View, closing the bubble, or quitting. The icon goes away (and the
// window comes back) when the bubble is closed.
//
// The tray icon belongs to a hidden window on the bubble thread, so its
// messages are handled by the bubble's own message loop.

//go:embed assets/presentia_mark_light.png
var trayMarkPNG []byte

const (
	trayCallback = 0x8000 + 20 // WM_APP+20: notification-area mouse events
	trayUID      = 1

	nimAdd    = 0
	nimModify = 1
	nimDelete = 2
	nifMsg    = 0x1
	nifIcon   = 0x2
	nifTip    = 0x4

	cmdOpen     = 1
	cmdLiveView = 2
	cmdClose    = 3
	cmdQuit     = 4
)

type bGUID struct {
	Data1 uint32
	Data2 uint16
	Data3 uint16
	Data4 [8]byte
}

type bNOTIFYICONDATA struct {
	CbSize           uint32
	HWnd             uintptr
	UID              uint32
	UFlags           uint32
	UCallbackMessage uint32
	HIcon            uintptr
	SzTip            [128]uint16
	DwState          uint32
	DwStateMask      uint32
	SzInfo           [256]uint16
	UVersion         uint32
	SzInfoTitle      [64]uint16
	DwInfoFlags      uint32
	GuidItem         bGUID
	HBalloonIcon     uintptr
}

type bICONINFO struct {
	FIcon    int32
	XHotspot uint32
	YHotspot uint32
	HbmMask  uintptr
	HbmColor uintptr
}

var (
	bShell32             = syscall.NewLazyDLL("shell32.dll")
	bShellNotifyIconW    = bShell32.NewProc("Shell_NotifyIconW")
	bCreatePopupMenu     = bUser32.NewProc("CreatePopupMenu")
	bAppendMenuW         = bUser32.NewProc("AppendMenuW")
	bTrackPopupMenu      = bUser32.NewProc("TrackPopupMenu")
	bDestroyMenu         = bUser32.NewProc("DestroyMenu")
	bSetForegroundWindow = bUser32.NewProc("SetForegroundWindow")
	bRegisterWindowMsgW  = bUser32.NewProc("RegisterWindowMessageW")
	bCreateIconIndirect  = bUser32.NewProc("CreateIconIndirect")
	bDestroyIcon         = bUser32.NewProc("DestroyIcon")
	bCreateBitmap        = bGdi32.NewProc("CreateBitmap")
	bClsTray             = syscall.StringToUTF16Ptr("PresentiaTrayCls")
	bTitleTray           = syscall.StringToUTF16Ptr("Presentia Tray")
	bTaskbarCreatedName  = syscall.StringToUTF16Ptr("TaskbarCreated")
	bMenuOpen            = syscall.StringToUTF16Ptr("Open Presentia")
	bMenuLiveShow        = syscall.StringToUTF16Ptr("Show Live View")
	bMenuLiveHide        = syscall.StringToUTF16Ptr("Hide Live View")
	bMenuClose           = syscall.StringToUTF16Ptr("Close Bubble")
	bMenuQuit            = syscall.StringToUTF16Ptr("Quit Presentia")
	gTrayOnce            sync.Once
	gTrayProc            uintptr
	gTrayHwnd            uintptr
	gTrayIcon            uintptr
	gTrayMu              sync.Mutex
	gTrayTip             string
	gTaskbarCreated      uint32
	gMainHidden          bool
)

// ── Main window visibility ───────────────────────────────────────────────────

// hideMain sends the main window to the tray (no taskbar / Alt+Tab entry).
func hideMain() {
	if app := gBubbleApp; app != nil && app.ctx != nil {
		wailsruntime.WindowHide(app.ctx)
		gMainHidden = true
	}
}

// showMain brings the main window back from the tray.
func showMain() {
	if app := gBubbleApp; app != nil && app.ctx != nil {
		wailsruntime.WindowShow(app.ctx)
		wailsruntime.WindowUnminimise(app.ctx)
		gMainHidden = false
	}
}

// bubbleActive reports whether the bubble is on screen.
func bubbleActive() bool {
	gBubbleMu.Lock()
	defer gBubbleMu.Unlock()
	return gBubbleHwnd != 0
}

// ── Icon ─────────────────────────────────────────────────────────────────────

// makeTrayIcon renders the Presentia mark into a small-icon-sized HICON.
func makeTrayIcon() uintptr {
	n, _, _ := bGetSystemMetrics.Call(49) // SM_CXSMICON (DPI-scaled)
	size := int(n)
	if size < 16 {
		size = 16
	}
	src, err := png.Decode(bytes.NewReader(trayMarkPNG))
	if err != nil {
		return 0
	}
	b := src.Bounds()
	rgba := image.NewRGBA(image.Rect(0, 0, b.Dx(), b.Dy()))
	draw.Draw(rgba, rgba.Bounds(), src, b.Min, draw.Src)

	// Fit the tall mark inside the square, with a pixel of breathing room.
	k := math.Min(float64(size-2)/float64(b.Dx()), float64(size)/float64(b.Dy()))
	w, h := max(1, int(math.Round(float64(b.Dx())*k))), max(1, int(math.Round(float64(b.Dy())*k)))
	small := downscale(rgba, w, h) // premultiplied RGBA

	hbm, px := newDIB(int32(size), int32(size))
	if hbm == 0 {
		return 0
	}
	ox, oy := (size-w)/2, (size-h)/2
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			s := (y*w + x) * 4
			a := small[s+3]
			if a == 0 {
				continue
			}
			un := func(v byte) byte { return byte(math.Min(255, float64(v)*255/float64(a)+0.5)) }
			d := ((oy+y)*size + ox + x) * 4
			px[d], px[d+1], px[d+2], px[d+3] = un(small[s+2]), un(small[s+1]), un(small[s]), a
		}
	}
	maskBits := make([]byte, ((size+15)/16)*2*size) // all zero: alpha decides
	mask, _, _ := bCreateBitmap.Call(uintptr(size), uintptr(size), 1, 1, uintptr(unsafe.Pointer(&maskBits[0])))
	ii := bICONINFO{FIcon: 1, HbmMask: mask, HbmColor: hbm}
	icon, _, _ := bCreateIconIndirect.Call(uintptr(unsafe.Pointer(&ii)))
	bDeleteObject.Call(hbm)
	if mask != 0 {
		bDeleteObject.Call(mask)
	}
	return icon
}

// ── Notification-area icon ───────────────────────────────────────────────────

func trayData(flags uint32) bNOTIFYICONDATA {
	nid := bNOTIFYICONDATA{HWnd: gTrayHwnd, UID: trayUID, UFlags: flags,
		UCallbackMessage: trayCallback, HIcon: gTrayIcon}
	nid.CbSize = uint32(unsafe.Sizeof(nid))
	gTrayMu.Lock()
	tip := gTrayTip
	gTrayMu.Unlock()
	if tip == "" {
		tip = "Presentia — bubble active"
	}
	u, _ := syscall.UTF16FromString(tip)
	if len(u) > len(nid.SzTip) {
		u = append(u[:len(nid.SzTip)-1], 0)
	}
	copy(nid.SzTip[:], u)
	return nid
}

// createTray adds the tray icon. Bubble thread only.
func createTray(hInst uintptr) {
	gTrayOnce.Do(func() {
		gTrayProc = syscall.NewCallback(trayWndProc)
		wc := bWNDCLASSEX{
			CbSize:        uint32(unsafe.Sizeof(bWNDCLASSEX{})),
			LpfnWndProc:   gTrayProc,
			HInstance:     hInst,
			LpszClassName: bClsTray,
		}
		bRegisterClassExW.Call(uintptr(unsafe.Pointer(&wc)))
		m, _, _ := bRegisterWindowMsgW.Call(uintptr(unsafe.Pointer(bTaskbarCreatedName)))
		gTaskbarCreated = uint32(m)
	})
	if gTrayHwnd != 0 {
		return
	}
	// A hidden, never-shown popup: it owns the icon and the context menu.
	h, _, _ := bCreateWindowExW.Call(bWsExToolWindow, uintptr(unsafe.Pointer(bClsTray)),
		uintptr(unsafe.Pointer(bTitleTray)), bWsPopup, 0, 0, 0, 0, 0, 0, hInst, 0)
	if h == 0 {
		return
	}
	gTrayHwnd = h
	if gTrayIcon == 0 {
		gTrayIcon = makeTrayIcon()
	}
	nid := trayData(nifMsg | nifIcon | nifTip)
	bShellNotifyIconW.Call(nimAdd, uintptr(unsafe.Pointer(&nid)))
}

// removeTray deletes the tray icon. Bubble thread only.
func removeTray() {
	if gTrayHwnd == 0 {
		return
	}
	nid := trayData(0)
	bShellNotifyIconW.Call(nimDelete, uintptr(unsafe.Pointer(&nid)))
	bDestroyWindow.Call(gTrayHwnd)
	gTrayHwnd = 0
}

// trayTip updates the hover text (any thread).
func trayTip(text string) {
	gTrayMu.Lock()
	changed := text != gTrayTip
	gTrayTip = text
	gTrayMu.Unlock()
	if changed && gTrayHwnd != 0 {
		nid := trayData(nifTip)
		bShellNotifyIconW.Call(nimModify, uintptr(unsafe.Pointer(&nid)))
	}
}

func showTrayMenu(hwnd uintptr) {
	menu, _, _ := bCreatePopupMenu.Call()
	if menu == 0 {
		return
	}
	defer bDestroyMenu.Call(menu)
	live := bMenuLiveShow
	if open, src := pipIsOpen(); open && src == pipSrcMonitor {
		live = bMenuLiveHide
	}
	bAppendMenuW.Call(menu, 0, cmdOpen, uintptr(unsafe.Pointer(bMenuOpen)))
	bAppendMenuW.Call(menu, 0, cmdLiveView, uintptr(unsafe.Pointer(live)))
	bAppendMenuW.Call(menu, 0x800, 0, 0) // MF_SEPARATOR
	bAppendMenuW.Call(menu, 0, cmdClose, uintptr(unsafe.Pointer(bMenuClose)))
	bAppendMenuW.Call(menu, 0, cmdQuit, uintptr(unsafe.Pointer(bMenuQuit)))

	var pt bPOINT
	bGetCursorPos.Call(uintptr(unsafe.Pointer(&pt)))
	// Required so the menu closes when the user clicks elsewhere.
	bSetForegroundWindow.Call(hwnd)
	const tpmReturnCmd, tpmRightButton, tpmBottomAlign = 0x0100, 0x0002, 0x0020
	cmd, _, _ := bTrackPopupMenu.Call(menu, tpmReturnCmd|tpmRightButton|tpmBottomAlign,
		uintptr(pt.X), uintptr(pt.Y), 0, hwnd, 0)
	bPostMessageW.Call(hwnd, 0, 0, 0) // WM_NULL, per the TrackPopupMenu docs

	switch cmd {
	case cmdOpen:
		showMain()
	case cmdLiveView:
		toggleLiveView()
	case cmdClose:
		emitBubbleCmd("bubble:quit") // lets the page update its button
		CloseFloatingBubble()
	case cmdQuit:
		if app := gBubbleApp; app != nil && app.ctx != nil {
			wailsruntime.Quit(app.ctx)
		}
	}
}

func trayWndProc(hwnd, msg, wParam, lParam uintptr) uintptr {
	switch uint32(msg) {
	case trayCallback:
		switch uint32(lParam & 0xFFFF) {
		case bWmLButtonUp, 0x0203: // click / double-click
			showMain()
		case 0x0205, 0x007B: // WM_RBUTTONUP, WM_CONTEXTMENU
			showTrayMenu(hwnd)
		}
		return 0
	case bWmDestroy:
		return 0
	}
	if gTaskbarCreated != 0 && uint32(msg) == gTaskbarCreated {
		// Explorer restarted: the icon has to be added again.
		nid := trayData(nifMsg | nifIcon | nifTip)
		bShellNotifyIconW.Call(nimAdd, uintptr(unsafe.Pointer(&nid)))
		return 0
	}
	r, _, _ := bDefWindowProcW.Call(hwnd, msg, wParam, lParam)
	return r
}
