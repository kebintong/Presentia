//go:build windows

package main

import (
	"runtime"
	"sync"
	"syscall"
	"unsafe"

	wailsruntime "github.com/wailsapp/wails/v2/pkg/runtime"
)

// ── Win32 API setup ──────────────────────────────────────────────────────────
//
// NOTE: LazyProc.Call() PANICS if the procedure is not exported by the DLL it
// was loaded from, and a panic on the bubble's goroutine takes the whole app
// down. Calls are split by DLL deliberately: window/message functions live in
// user32.dll, device-context and drawing-object functions in gdi32.dll.
// SetTextColor and SetBkMode are GDI calls — loading them from user32 crashed
// the app on the bubble's first paint. Procedures that only exist on newer
// Windows are checked with Find() before use.

var (
	bUser32   = syscall.NewLazyDLL("user32.dll")
	bGdi32    = syscall.NewLazyDLL("gdi32.dll")
	bKernel32 = syscall.NewLazyDLL("kernel32.dll")

	// user32.dll — windows, messages, text layout
	bRegisterClassExW     = bUser32.NewProc("RegisterClassExW")
	bCreateWindowExW      = bUser32.NewProc("CreateWindowExW")
	bDestroyWindow        = bUser32.NewProc("DestroyWindow")
	bShowWindow           = bUser32.NewProc("ShowWindow")
	bDefWindowProcW       = bUser32.NewProc("DefWindowProcW")
	bGetMessageW          = bUser32.NewProc("GetMessageW")
	bTranslateMessage     = bUser32.NewProc("TranslateMessage")
	bDispatchMessageW     = bUser32.NewProc("DispatchMessageW")
	bPostQuitMessage      = bUser32.NewProc("PostQuitMessage")
	bPostMessageW         = bUser32.NewProc("PostMessageW")
	bBeginPaint           = bUser32.NewProc("BeginPaint")
	bEndPaint             = bUser32.NewProc("EndPaint")
	bDrawTextW            = bUser32.NewProc("DrawTextW")
	bGetSystemMetrics     = bUser32.NewProc("GetSystemMetrics")
	bSystemParametersInfo = bUser32.NewProc("SystemParametersInfoW")
	bGetWindowRect        = bUser32.NewProc("GetWindowRect")
	bLoadCursorW          = bUser32.NewProc("LoadCursorW")
	bSetCapture           = bUser32.NewProc("SetCapture")
	bReleaseCapture       = bUser32.NewProc("ReleaseCapture")
	bGetCursorPos         = bUser32.NewProc("GetCursorPos")
	bSetWindowPos         = bUser32.NewProc("SetWindowPos")
	bSetTimer             = bUser32.NewProc("SetTimer")
	bKillTimer            = bUser32.NewProc("KillTimer")
	bTrackMouseEvent      = bUser32.NewProc("TrackMouseEvent")
	bUpdateLayeredWindow  = bUser32.NewProc("UpdateLayeredWindow")
	bGetDpiForSystem      = bUser32.NewProc("GetDpiForSystem")

	bGetCurrentThreadId = bKernel32.NewProc("GetCurrentThreadId")
	bGetModuleHandleW   = bKernel32.NewProc("GetModuleHandleW")

	// gdi32.dll — drawing objects and device-context state
	bDeleteObject          = bGdi32.NewProc("DeleteObject")
	bSelectObject          = bGdi32.NewProc("SelectObject")
	bCreateFontW           = bGdi32.NewProc("CreateFontW")
	bSetTextColor          = bGdi32.NewProc("SetTextColor")
	bSetBkMode             = bGdi32.NewProc("SetBkMode")
	bGetTextExtentPoint32W = bGdi32.NewProc("GetTextExtentPoint32W")
	bCreateDIBSection      = bGdi32.NewProc("CreateDIBSection")
	bCreateCompatibleDC    = bGdi32.NewProc("CreateCompatibleDC")
	bDeleteDC              = bGdi32.NewProc("DeleteDC")
	bGdiFlush              = bGdi32.NewProc("GdiFlush")
)

const (
	bWsExTopmost    = 0x00000008
	bWsExToolWindow = 0x00000080
	bWsExLayered    = 0x00080000
	bWsExNoActivate = 0x08000000
	bWsPopup        = 0x80000000
	bWsVisible      = 0x10000000

	bWmDestroy     = 0x0002
	bWmClose       = 0x0010
	bWmPaint       = 0x000F
	bWmTimer       = 0x0113
	bWmMouseMove   = 0x0200
	bWmLButtonDown = 0x0201
	bWmLButtonUp   = 0x0202
	bWmRButtonDown = 0x0204
	bWmMouseLeave  = 0x02A3
	bWmNcHitTest   = 0x0084

	// Posted from the Wails thread; the bubble thread does the actual work.
	bWmAppStyle = 0x8000 + 1 // WM_APP+1: logo style changed
	bWmAppTheme = 0x8000 + 2 // WM_APP+2: dark/light changed

	bSwpNoSize     = 0x0001
	bSwpNoMove     = 0x0002
	bSwpNoZOrder   = 0x0004
	bSwpNoActivate = 0x0010
	bHwndTopmost   = ^uintptr(0) // HWND_TOPMOST (-1)

	bHtClient    = 1
	bTransparent = 1
	bUlwAlpha    = 0x00000002
	bAcSrcAlpha  = 0x01

	// Pointer travel (px) past which a press counts as a drag, not a click.
	bDragSlop = 4
)

type bWNDCLASSEX struct {
	CbSize        uint32
	Style         uint32
	LpfnWndProc   uintptr
	CbClsExtra    int32
	CbWndExtra    int32
	HInstance     uintptr
	HIcon         uintptr
	HCursor       uintptr
	HbrBackground uintptr
	LpszMenuName  *uint16
	LpszClassName *uint16
	HIconSm       uintptr
}
type bPOINT struct{ X, Y int32 }
type bRECT struct{ Left, Top, Right, Bottom int32 }
type bSIZE struct{ Cx, Cy int32 }
type bMSG struct {
	Hwnd    uintptr
	Message uint32
	WParam  uintptr
	LParam  uintptr
	Time    uint32
	Pt      bPOINT
}
type bPAINTSTRUCT struct {
	Hdc         uintptr
	FErase      int32
	RcPaint     bRECT
	FRestore    int32
	FIncUpdate  int32
	RgbReserved [32]byte
}
type bBLENDFUNCTION struct{ BlendOp, BlendFlags, SourceConstantAlpha, AlphaFormat byte }

// Strings handed to Win32 live at package scope so the garbage collector can
// never reclaim them mid-call. A *uint16 passed as a uintptr argument is
// invisible to the GC, so a short-lived local can be collected while the API
// is still reading it — a rare crash that is very hard to reproduce.
var (
	bClsBubble   = syscall.StringToUTF16Ptr("PresentiaBubbleCls")
	bClsDial     = syscall.StringToUTF16Ptr("PresentiaBubbleDialCls")
	bTitleBubble = syscall.StringToUTF16Ptr("Presentia Monitor Bubble")
	bTitleDial   = syscall.StringToUTF16Ptr("")
)

// ── Style & theme ────────────────────────────────────────────────────────────
//
// Both are set from the frontend (Wails thread) and read on the bubble thread.
// The logo follows both: cyan in light mode, white in dark mode, spectrum when
// gIridescent is on (the app only allows that in dark mode). gIridescent also
// switches the dial to pastel buttons.

var (
	gDarkTheme  = true
	gIridescent bool
)

// setBubbleThemeNative swaps the logo and dial labels for dark/light.
func setBubbleThemeNative(dark bool) {
	gDarkTheme = dark
	postToBubble(bWmAppTheme)
	pipPost()
}

// setBubbleStyleNative turns the iridescent logo and dial on or off.
func setBubbleStyleNative(iri bool) {
	gIridescent = iri
	postToBubble(bWmAppStyle)
	pipPost()
}

func postToBubble(msg uintptr) {
	gBubbleMu.Lock()
	h := gBubbleHwnd
	gBubbleMu.Unlock()
	if h != 0 {
		bPostMessageW.Call(h, msg, 0, 0)
	}
}

// ── Global state ─────────────────────────────────────────────────────────────

var (
	gBubbleHwnd uintptr
	gBubbleApp  *App
	gBubbleMu   sync.Mutex
	gThreadID   uint32 // Win32 thread ID of the bubble message loop
	gClassOnce  sync.Once

	// Drag state. The bubble has to be both draggable AND clickable, so a
	// press is only a click if the pointer barely moved before release.
	gDragging  bool
	gDragMoved bool
	gGrabX     int32 // cursor position when the press started
	gGrabY     int32
	gWinX      int32 // window position when the press started
	gWinY      int32

	// Whether the main window is currently pinned above other windows.
	gPinned bool
)

// Package-level callbacks — must NOT be GC'd, so they are stored here rather
// than in a local.
//
// They are assigned lazily instead of by a var initializer: bubbleWndProc
// reaches registerClasses (via openDial), and registerClasses needs these same
// variables, which Go rejects at compile time as an initialization cycle.
var (
	gProcOnce      sync.Once
	gBubbleWndProc uintptr
	gDialWndProc   uintptr
)

func ensureWndProcs() {
	gProcOnce.Do(func() {
		gBubbleWndProc = syscall.NewCallback(bubbleWndProc)
		gDialWndProc = syscall.NewCallback(dialWndProc)
	})
}

// ── Small helpers ─────────────────────────────────────────────────────────────

var bSetWindowDisplayAffinity = bUser32.NewProc("SetWindowDisplayAffinity")

// excludeFromCapture keeps one of Presentia's floating windows (bubble,
// dial, stats chip, pop-out) out of screen captures — including our own
// Meet monitoring, which otherwise films the Live View inside itself, and
// the instructor's screen share. Windows 10 2004+; a no-op before that.
func excludeFromCapture(hwnd uintptr) {
	if hwnd != 0 && bSetWindowDisplayAffinity.Find() == nil {
		bSetWindowDisplayAffinity.Call(hwnd, 0x11) // WDA_EXCLUDEFROMCAPTURE
	}
}

// drawText renders one string, keeping every pointer alive across the call.
func drawText(hdc uintptr, s string, rc *bRECT, flags uintptr) {
	p, err := syscall.UTF16PtrFromString(s)
	if err != nil {
		return
	}
	bDrawTextW.Call(hdc, uintptr(unsafe.Pointer(p)), ^uintptr(0),
		uintptr(unsafe.Pointer(rc)), flags)
	runtime.KeepAlive(p)
	runtime.KeepAlive(rc)
}

// loWord / hiWord extract the signed mouse coordinates from an LPARAM.
func loWord(lp uintptr) int32 { return int32(int16(lp & 0xFFFF)) }
func hiWord(lp uintptr) int32 { return int32(int16((lp >> 16) & 0xFFFF)) }

func abs32(v int32) int32 {
	if v < 0 {
		return -v
	}
	return v
}

// togglePin shows or hides Presentia as a compact panel pinned above every
// other window, so an instructor can keep the roster visible next to Meet.
func togglePin() {
	app := gBubbleApp
	if app == nil || app.ctx == nil {
		return
	}
	gPinned = !gPinned
	if gPinned {
		showMain()
		wailsruntime.WindowSetAlwaysOnTop(app.ctx, true)
		wailsruntime.WindowSetSize(app.ctx, 460, 360)
	} else {
		wailsruntime.WindowSetAlwaysOnTop(app.ctx, false)
		wailsruntime.WindowSetSize(app.ctx, 1100, 700)
		hideMain() // back to the tray
	}
}

// ── Bubble window procedure ───────────────────────────────────────────────────

func bubbleWndProc(hwnd, msg, wParam, lParam uintptr) uintptr {
	switch uint32(msg) {
	case bWmPaint:
		// The picture comes from UpdateLayeredWindow; just validate.
		var ps bPAINTSTRUCT
		bBeginPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
		bEndPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
		return 0

	case bWmAppStyle, bWmAppTheme:
		applyBubbleStyle(hwnd)
		renderDial()
		updateChip()
		return 0

	case bWmAppLive:
		updateChip() // new monitoring stats from the live poller
		return 0

	case bWmLButtonDown:
		// Start a possible drag. Whether this turns out to be a click or a
		// drag is decided on mouse-up, by how far the pointer travelled.
		var pt bPOINT
		bGetCursorPos.Call(uintptr(unsafe.Pointer(&pt)))
		var rc bRECT
		bGetWindowRect.Call(hwnd, uintptr(unsafe.Pointer(&rc)))
		gGrabX, gGrabY = pt.X, pt.Y
		gWinX, gWinY = rc.Left, rc.Top
		gDragging, gDragMoved = true, false
		bSetCapture.Call(hwnd)
		return 0

	case bWmMouseMove:
		if !gDragging {
			return 0
		}
		var pt bPOINT
		bGetCursorPos.Call(uintptr(unsafe.Pointer(&pt)))
		dx, dy := pt.X-gGrabX, pt.Y-gGrabY
		if !gDragMoved && (abs32(dx) > bDragSlop || abs32(dy) > bDragSlop) {
			gDragMoved = true
			// A dial fanned out around the old position would be left stranded.
			closeDialNow()
		}
		if gDragMoved {
			bSetWindowPos.Call(hwnd, 0,
				uintptr(uint32(gWinX+dx)), uintptr(uint32(gWinY+dy)), 0, 0,
				bSwpNoSize|bSwpNoZOrder|bSwpNoActivate)
		}
		return 0

	case bWmLButtonUp:
		if !gDragging {
			return 0
		}
		gDragging = false
		bReleaseCapture.Call()
		if gDragMoved {
			return 0 // that was a drag, not a click
		}
		// A real click: pop the controls out, or tuck them back in.
		if gDialOpen {
			closeDial()
		} else {
			openDial(hwnd)
		}
		return 0

	case bWmRButtonDown:
		// Right-click = quit immediately
		emitBubbleCmd("bubble:quit")
		return 0

	case bWmNcHitTest:
		// MUST be HTCLIENT. Returning HTCAPTION makes Windows treat the whole
		// bubble as a title bar, which suppresses every client mouse message —
		// WM_LBUTTONDOWN never arrives and the dial can never open. Dragging is
		// handled above instead.
		return bHtClient

	case bWmClose:
		// Posted by CloseFloatingBubble from the Wails thread. Destroying the
		// window has to happen HERE, on the thread that created it — Windows
		// rejects a cross-thread DestroyWindow, which is why closing the
		// bubble from the UI used to do nothing at all.
		closeDialNow()
		bDestroyWindow.Call(hwnd)
		return 0

	case bWmDestroy:
		bPostQuitMessage.Call(0)
		return 0
	}
	r, _, _ := bDefWindowProcW.Call(hwnd, msg, wParam, lParam)
	return r
}

// ── Window classes ────────────────────────────────────────────────────────────

// registerClasses registers both window classes exactly once per process.
func registerClasses(hInst uintptr) {
	gClassOnce.Do(func() {
		ensureWndProcs()
		arrow, _, _ := bLoadCursorW.Call(0, 32512) // IDC_ARROW
		hand, _, _ := bLoadCursorW.Call(0, 32649)  // IDC_HAND

		// Both windows are fully painted by UpdateLayeredWindow, so neither
		// needs a background brush.
		wcBubble := bWNDCLASSEX{
			CbSize:        uint32(unsafe.Sizeof(bWNDCLASSEX{})),
			LpfnWndProc:   gBubbleWndProc,
			HInstance:     hInst,
			LpszClassName: bClsBubble,
			HCursor:       arrow,
		}
		bRegisterClassExW.Call(uintptr(unsafe.Pointer(&wcBubble)))

		wcDial := bWNDCLASSEX{
			CbSize:        uint32(unsafe.Sizeof(bWNDCLASSEX{})),
			LpfnWndProc:   gDialWndProc,
			HInstance:     hInst,
			LpszClassName: bClsDial,
			HCursor:       hand,
		}
		bRegisterClassExW.Call(uintptr(unsafe.Pointer(&wcDial)))
	})
}

// ── Event emission ────────────────────────────────────────────────────────────

// gBubbleTask: the app window was brought out of the tray for a bubble
// action and should go back when that action is finished.
var gBubbleTask bool

func emitBubbleCmd(cmd string) {
	// The pickers are inside the app, so the app has to come out of the tray
	// for them; bubbleTaskDone sends it back afterwards.
	switch cmd {
	case "bubble:win_picker":
		gBubbleTask = true
		showMain()
	case "bubble:screen_area":
		gBubbleTask = true // the page shows the window for the picker itself
	}
	if gBubbleApp != nil && gBubbleApp.ctx != nil {
		wailsruntime.EventsEmit(gBubbleApp.ctx, cmd)
	}
}

// ── Bubble lifecycle ──────────────────────────────────────────────────────────

// OpenFloatingBubble creates a native Win32 always-on-top bubble window that
// floats freely above all other windows.
// It also minimizes the Presentia main window so the bubble is the only
// Presentia UI element visible.
func OpenFloatingBubble(a *App) {
	gBubbleMu.Lock()
	if gBubbleHwnd != 0 {
		gBubbleMu.Unlock()
		return // already running
	}
	gBubbleMu.Unlock()

	gBubbleApp = a

	go func() {
		// ── CRITICAL ────────────────────────────────────────────────────
		// Win32 requires that all window creation and message dispatching
		// happen on the same OS thread.  runtime.LockOSThread() prevents
		// the Go scheduler from migrating this goroutine to another thread.
		runtime.LockOSThread()
		defer runtime.UnlockOSThread()

		// A panic here would kill the whole app, and the bubble is never
		// worth losing an in-progress class session over.
		defer func() {
			if r := recover(); r != nil {
				gBubbleMu.Lock()
				gBubbleHwnd = 0
				gThreadID = 0
				gBubbleMu.Unlock()
				stopLivePoller()
				removeTray()
				if gBubbleApp != nil && gBubbleApp.ctx != nil {
					wailsruntime.LogError(gBubbleApp.ctx,
						"Monitor bubble failed and was closed")
					showMain()
					wailsruntime.EventsEmit(gBubbleApp.ctx, "bubble:failed")
				}
			}
		}()

		hInst, _, _ := bGetModuleHandleW.Call(0)
		registerClasses(hInst)

		// Start at bottom-right of primary monitor, above the taskbar
		bw, bh := bubbleSize()
		sw, _, _ := bGetSystemMetrics.Call(0)
		sh, _, _ := bGetSystemMetrics.Call(1)
		startX := int32(sw) - bw - 36
		startY := int32(sh) - bh - 84
		if startX < 0 {
			startX = 0
		}
		if startY < 0 {
			startY = 0
		}

		hwnd, _, _ := bCreateWindowExW.Call(
			bWsExTopmost|bWsExToolWindow|bWsExLayered|bWsExNoActivate,
			uintptr(unsafe.Pointer(bClsBubble)),
			uintptr(unsafe.Pointer(bTitleBubble)),
			bWsPopup|bWsVisible,
			uintptr(startX), uintptr(startY), uintptr(bw), uintptr(bh),
			0, 0, hInst, 0,
		)

		if hwnd == 0 {
			// Window creation failed — tell the UI and bail without
			// minimising, so the app never just vanishes.
			if gBubbleApp != nil && gBubbleApp.ctx != nil {
				wailsruntime.LogError(gBubbleApp.ctx, "Could not create the monitor bubble window")
				wailsruntime.EventsEmit(gBubbleApp.ctx, "bubble:failed")
			}
			return
		}

		// A layered window stays invisible until it is given its first picture.
		updateLogo(hwnd)
		bShowWindow.Call(hwnd, 5)

		gBubbleMu.Lock()
		gBubbleHwnd = hwnd
		excludeFromCapture(hwnd)
		tid, _, _ := bGetCurrentThreadId.Call()
		gThreadID = uint32(tid)
		gBubbleMu.Unlock()

		// Bubble mode: the app lives in the tray. Only hide once the bubble
		// is actually on screen — hiding first meant a failed bubble left the
		// user staring at an empty desktop, looking exactly like a crash.
		createTray(hInst)
		startLivePoller()
		hideMain()

		// ── Message loop ──────────────────────────────────────────────
		var msg bMSG
		for {
			r, _, _ := bGetMessageW.Call(uintptr(unsafe.Pointer(&msg)), 0, 0, 0)
			if r == 0 || r == ^uintptr(0) {
				break
			}
			bTranslateMessage.Call(uintptr(unsafe.Pointer(&msg)))
			bDispatchMessageW.Call(uintptr(unsafe.Pointer(&msg)))
		}

		gBubbleMu.Lock()
		gBubbleHwnd = 0
		gThreadID = 0
		gBubbleMu.Unlock()

		// Leave bubble mode: stats, chip, tray icon and Live View go, and
		// Presentia comes back out of the tray.
		stopLivePoller()
		destroyChip()
		removeTray()
		if open, src := pipIsOpen(); open && src == pipSrcMonitor {
			closePipNative()
		}
		gPinned = false
		showMain()
	}()
}

// CloseFloatingBubble asks the bubble to close itself.
//
// This runs on the Wails thread, so it must NOT touch the windows directly:
// DestroyWindow only works from the thread that created the window. PostMessage
// is thread-safe and hands the work to the bubble's own message loop.
func CloseFloatingBubble() {
	gBubbleMu.Lock()
	h := gBubbleHwnd
	gBubbleMu.Unlock()
	if h != 0 {
		bPostMessageW.Call(h, bWmClose, 0, 0)
	}
}

// bubbleTaskDone returns the window to the tray after a bubble-started
// picker, unless the instructor has pinned it on screen meanwhile.
func bubbleTaskDone() {
	if gBubbleTask && bubbleActive() && !gPinned {
		hideMain()
	}
	gBubbleTask = false
}
