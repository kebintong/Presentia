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
// down. Drawing calls are split by DLL deliberately: window/message functions
// live in user32.dll, device-context and drawing-object functions in gdi32.dll.
// SetTextColor and SetBkMode are GDI calls — loading them from user32 crashed
// the app on the bubble's first WM_PAINT.

var (
	bUser32   = syscall.NewLazyDLL("user32.dll")
	bGdi32    = syscall.NewLazyDLL("gdi32.dll")
	bKernel32 = syscall.NewLazyDLL("kernel32.dll")

	// user32.dll — windows, messages, painting frames
	bRegisterClassExW           = bUser32.NewProc("RegisterClassExW")
	bCreateWindowExW            = bUser32.NewProc("CreateWindowExW")
	bDestroyWindow              = bUser32.NewProc("DestroyWindow")
	bShowWindow                 = bUser32.NewProc("ShowWindow")
	bUpdateWindow               = bUser32.NewProc("UpdateWindow")
	bDefWindowProcW             = bUser32.NewProc("DefWindowProcW")
	bGetMessageW                = bUser32.NewProc("GetMessageW")
	bTranslateMessage           = bUser32.NewProc("TranslateMessage")
	bDispatchMessageW           = bUser32.NewProc("DispatchMessageW")
	bPostQuitMessage            = bUser32.NewProc("PostQuitMessage")
	bPostMessageW               = bUser32.NewProc("PostMessageW")
	bBeginPaint                 = bUser32.NewProc("BeginPaint")
	bEndPaint                   = bUser32.NewProc("EndPaint")
	bGetClientRect              = bUser32.NewProc("GetClientRect")
	bFillRect                   = bUser32.NewProc("FillRect")
	bDrawTextW                  = bUser32.NewProc("DrawTextW")
	bSetLayeredWindowAttributes = bUser32.NewProc("SetLayeredWindowAttributes")
	bInvalidateRect             = bUser32.NewProc("InvalidateRect")
	bGetSystemMetrics           = bUser32.NewProc("GetSystemMetrics")
	bGetWindowRect              = bUser32.NewProc("GetWindowRect")
	bLoadCursorW                = bUser32.NewProc("LoadCursorW")
	bSetWindowRgn               = bUser32.NewProc("SetWindowRgn")
	bSetCapture                 = bUser32.NewProc("SetCapture")
	bReleaseCapture             = bUser32.NewProc("ReleaseCapture")
	bGetCursorPos               = bUser32.NewProc("GetCursorPos")
	bSetWindowPos               = bUser32.NewProc("SetWindowPos")
	bGetCurrentThreadId         = bKernel32.NewProc("GetCurrentThreadId")
	bGetModuleHandleW           = bKernel32.NewProc("GetModuleHandleW")

	// gdi32.dll — drawing objects and device-context state
	bCreateSolidBrush   = bGdi32.NewProc("CreateSolidBrush")
	bDeleteObject       = bGdi32.NewProc("DeleteObject")
	bSelectObject       = bGdi32.NewProc("SelectObject")
	bCreateFontW        = bGdi32.NewProc("CreateFontW")
	bSetTextColor       = bGdi32.NewProc("SetTextColor")
	bSetBkMode          = bGdi32.NewProc("SetBkMode")
	bCreateEllipticRgn  = bGdi32.NewProc("CreateEllipticRgn")
	bCreateRoundRectRgn = bGdi32.NewProc("CreateRoundRectRgn")
	bFillRgn            = bGdi32.NewProc("FillRgn")
	bFrameRgn           = bGdi32.NewProc("FrameRgn")
)

const (
	bWsExTopmost    = 0x00000008
	bWsExToolWindow = 0x00000080
	bWsExLayered    = 0x00080000
	bWsExNoActivate = 0x08000000
	bWsPopup        = 0x80000000
	bWsVisible      = 0x10000000

	bLwaAlpha = 0x00000002

	bWmDestroy     = 0x0002
	bWmClose       = 0x0010
	bWmPaint       = 0x000F
	bWmLButtonDown = 0x0201
	bWmLButtonUp   = 0x0202
	bWmRButtonDown = 0x0204
	bWmMouseMove   = 0x0200
	bWmNcHitTest   = 0x0084

	bSwpNoSize     = 0x0001
	bSwpNoZOrder   = 0x0004
	bSwpNoActivate = 0x0010

	// Pointer travel (px) past which a press counts as a drag, not a click.
	bDragSlop = 4

	bHtClient     = 1
	bHtCaption    = 2
	bTransparent  = 1
	bDtCenter     = 0x00000001
	bDtVCenter    = 0x00000004
	bDtSingleLine = 0x00000020
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

func bgr(r, g, b byte) uint32 { return uint32(b) | (uint32(g) << 8) | (uint32(r) << 16) }

// The bubble is painted with GDI, so the app's CSS tokens are mirrored here.
// Keep these in step with :root and [data-theme="light"] in style.css.
type bTheme struct {
	surface    uint32 // --surface
	border     uint32 // --glass-border
	ink        uint32 // --ink-heading
	muted      uint32 // --muted
	hover      uint32 // --card-row-hover
	rowBg      uint32 // --card-row-bg
	accent     uint32 // --accent
	accentDeep uint32 // --accent-deep
	accentTint uint32 // flattened --accent-tint (GDI has no alpha here)
	onAccent   uint32 // text on an accent fill
	ok         uint32 // --present
	danger     uint32 // --missing
}

var bDark = bTheme{
	surface:    bgr(22, 32, 58),    // #16203A
	border:     bgr(38, 50, 74),    // #26324A
	ink:        bgr(255, 255, 255), // #FFFFFF
	muted:      bgr(147, 161, 184), // #93A1B8
	hover:      bgr(34, 48, 79),    // #22304F
	rowBg:      bgr(26, 36, 64),    // #1A2440
	accent:     bgr(28, 119, 195),  // #1C77C3
	accentDeep: bgr(21, 92, 160),   // #155CA0
	accentTint: bgr(29, 47, 78),    // #1C77C3 @16% over --surface
	onAccent:   bgr(255, 255, 255),
	ok:         bgr(16, 185, 129), // #10B981
	danger:     bgr(239, 68, 68),  // #EF4444
}

var bLight = bTheme{
	surface:    bgr(255, 255, 255), // #FFFFFF
	border:     bgr(226, 232, 240), // #E2E8F0
	ink:        bgr(15, 23, 42),    // #0F172A
	muted:      bgr(90, 103, 121),  // #5A6779
	hover:      bgr(235, 239, 245), // #EBEFF5
	rowBg:      bgr(245, 247, 250), // #F5F7FA
	accent:     bgr(28, 119, 195),  // #1C77C3
	accentDeep: bgr(21, 92, 160),   // #155CA0
	accentTint: bgr(232, 241, 249), // #1C77C3 @10% over white
	onAccent:   bgr(255, 255, 255),
	ok:         bgr(5, 150, 105), // #059669
	danger:     bgr(220, 38, 38), // #DC2626
}

// gDarkTheme follows the app's theme switch; the frontend pushes changes
// through SetBubbleTheme so the bubble never looks foreign next to the window.
var gDarkTheme = true

func th() bTheme {
	if gDarkTheme {
		return bDark
	}
	return bLight
}

// setBubbleThemeNative repaints the bubble and any open menu in the new theme.
func setBubbleThemeNative(dark bool) {
	gDarkTheme = dark
	if gBubbleHwnd != 0 {
		bInvalidateRect.Call(gBubbleHwnd, 0, 1)
	}
	if gMenuHwnd != 0 {
		bInvalidateRect.Call(gMenuHwnd, 0, 1)
	}
}

// Strings handed to Win32 live at package scope so the garbage collector can
// never reclaim them mid-call. A *uint16 passed as a uintptr argument is
// invisible to the GC, so a short-lived local can be collected while the API
// is still reading it — a rare crash that is very hard to reproduce.
var (
	bFaceSegoeUI    = syscall.StringToUTF16Ptr("Segoe UI")
	bClsBubble      = syscall.StringToUTF16Ptr("PresentiaBubbleCls")
	bClsMenu        = syscall.StringToUTF16Ptr("PresentiaBubbleMenuCls")
	bTitleBubble    = syscall.StringToUTF16Ptr("Presentia Monitor Bubble")
	bTitleMenu      = syscall.StringToUTF16Ptr("")
	bHdrMenuCaption = syscall.StringToUTF16Ptr("MONITOR CONTROLS")
)

// menu items
type bMenuItem struct {
	label string
	sub   string
	cmd   string
	tone  string // "accent" | "ok" | "danger" - resolved against the live theme
}

var bMenuItems = []bMenuItem{
	{"Select Screen Area", "Drag a region over the meeting", "bubble:screen_area", "accent"},
	{"Select Window", "Pick an open app window", "bubble:win_picker", "accent"},
	{"Live Monitor", "Start face tracking", "bubble:launch", "ok"},
	{"Stop Monitor", "End session and save", "bubble:stop", "danger"},
	{"", "", "", ""}, // divider
	// Handled inside the bubble rather than by the frontend, so it works no
	// matter which page the app is on.
	{"Display on Top", "Pin Presentia above other windows", "bubble:pin", "accent"},
	{"Quit Bubble", "Restore Presentia", "bubble:quit", "danger"},
}

// toneColour resolves a menu item's role against the current theme.
func toneColour(tone string) uint32 {
	t := th()
	switch tone {
	case "ok":
		return t.ok
	case "danger":
		return t.danger
	default:
		return t.accent
	}
}

const (
	bBubbleW = 64
	bBubbleH = 64
	bMenuW   = 300
	bItemH   = 54
	bHdrH    = 36
	bPad     = 8

	bMenuRadius = 12 // matches --radius-xl on the app's cards
	bRowRadius  = 8  // matches --radius-md on .card-row
	bIconSize   = 30
	bIconRadius = 8
	bTextX      = 58 // icon (14 + 30) + 14px gap
)

// ── Global state ─────────────────────────────────────────────────────────────

var (
	gBubbleHwnd  uintptr
	gMenuHwnd    uintptr
	gMenuOpen    bool
	gHoveredItem = -1
	gBubbleApp   *App
	gBubbleMu    sync.Mutex
	gThreadID    uint32 // Win32 thread ID of the bubble message loop
	gClassOnce   sync.Once

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
// reaches registerClasses (via openMenu), and registerClasses needs these same
// variables, which Go rejects at compile time as an initialization cycle.
// Assigning inside a function breaks the cycle without changing behaviour —
// the values are still created exactly once and live for the whole process.
var (
	gProcOnce      sync.Once
	gBubbleWndProc uintptr
	gMenuWndProc   uintptr
)

func ensureWndProcs() {
	gProcOnce.Do(func() {
		gBubbleWndProc = syscall.NewCallback(bubbleWndProc)
		gMenuWndProc = syscall.NewCallback(menuWndProc)
	})
}

// ── Small helpers ─────────────────────────────────────────────────────────────

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

// makeFont builds a Segoe UI font of the given pixel height and weight.
func makeFont(height, weight uintptr) uintptr {
	f, _, _ := bCreateFontW.Call(height, 0, 0, 0, weight, 0, 0, 0, 0, 0, 0, 0, 0,
		uintptr(unsafe.Pointer(bFaceSegoeUI)))
	return f
}

// Fonts are built once and reused. Creating them per WM_PAINT leaked a GDI
// handle every time (DeleteObject silently fails on a font still selected
// into a DC), and the menu repaints on every mouse move — enough to exhaust
// the process handle quota during a long class.
var (
	gFontOnce   sync.Once
	gFontBubble uintptr
	gFontHdr    uintptr
	gFontTitle  uintptr
	gFontSub    uintptr
)

func ensureFonts() {
	gFontOnce.Do(func() {
		gFontBubble = makeFont(24, 700)
		gFontHdr = makeFont(12, 700)
		gFontTitle = makeFont(15, 600)
		gFontSub = makeFont(12, 400)
	})
}

// fillRect paints a solid rectangle in one colour.
func fillRect(hdc uintptr, rc *bRECT, colour uint32) {
	br, _, _ := bCreateSolidBrush.Call(uintptr(colour))
	if br == 0 {
		return
	}
	bFillRect.Call(hdc, uintptr(unsafe.Pointer(rc)), br)
	runtime.KeepAlive(rc)
	bDeleteObject.Call(br)
}

// fillRound paints a filled rounded rectangle, the shape the app uses for
// cards, rows and icon badges.
func fillRound(hdc uintptr, rc bRECT, radius int32, colour uint32) {
	rgn, _, _ := bCreateRoundRectRgn.Call(
		uintptr(rc.Left), uintptr(rc.Top), uintptr(rc.Right), uintptr(rc.Bottom),
		uintptr(radius), uintptr(radius))
	if rgn == 0 {
		return
	}
	defer bDeleteObject.Call(rgn)
	br, _, _ := bCreateSolidBrush.Call(uintptr(colour))
	if br == 0 {
		return
	}
	defer bDeleteObject.Call(br)
	bFillRgn.Call(hdc, rgn, br)
}

// frameRound draws a 1px rounded outline - the app's "1px border" look.
func frameRound(hdc uintptr, rc bRECT, radius int32, colour uint32) {
	rgn, _, _ := bCreateRoundRectRgn.Call(
		uintptr(rc.Left), uintptr(rc.Top), uintptr(rc.Right), uintptr(rc.Bottom),
		uintptr(radius), uintptr(radius))
	if rgn == 0 {
		return
	}
	defer bDeleteObject.Call(rgn)
	br, _, _ := bCreateSolidBrush.Call(uintptr(colour))
	if br == 0 {
		return
	}
	defer bDeleteObject.Call(br)
	bFrameRgn.Call(hdc, rgn, br, 1, 1)
}

// fillEllipse paints a filled circle, used for the bubble body and icon dots.
func fillEllipse(hdc uintptr, rc bRECT, colour uint32) {
	rgn, _, _ := bCreateEllipticRgn.Call(
		uintptr(rc.Left), uintptr(rc.Top), uintptr(rc.Right), uintptr(rc.Bottom))
	if rgn == 0 {
		return
	}
	defer bDeleteObject.Call(rgn)
	br, _, _ := bCreateSolidBrush.Call(uintptr(colour))
	if br == 0 {
		return
	}
	defer bDeleteObject.Call(br)
	bFillRgn.Call(hdc, rgn, br)
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
		wailsruntime.WindowUnminimise(app.ctx)
		wailsruntime.WindowSetAlwaysOnTop(app.ctx, true)
		wailsruntime.WindowSetSize(app.ctx, 460, 360)
	} else {
		wailsruntime.WindowSetAlwaysOnTop(app.ctx, false)
		wailsruntime.WindowSetSize(app.ctx, 1100, 700)
		wailsruntime.WindowMinimise(app.ctx)
	}
}

// ── Bubble window procedure ───────────────────────────────────────────────────

func bubbleWndProc(hwnd, msg, wParam, lParam uintptr) uintptr {
	switch uint32(msg) {
	case bWmPaint:
		var ps bPAINTSTRUCT
		hdc, _, _ := bBeginPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
		drawBubble(hdc)
		bEndPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
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
			// A menu anchored to the old position would be left stranded.
			if gMenuOpen {
				closeMenu()
				gMenuOpen = false
				bInvalidateRect.Call(hwnd, 0, 1)
			}
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
		// A real click: toggle the control menu.
		if gMenuOpen {
			closeMenu()
			gMenuOpen = false
		} else {
			gMenuOpen = true
			openMenu(hwnd)
		}
		bInvalidateRect.Call(hwnd, 0, 1)
		return 0

	case bWmRButtonDown:
		// Right-click = quit immediately
		emitBubbleCmd("bubble:quit")
		return 0

	case bWmNcHitTest:
		// MUST be HTCLIENT. Returning HTCAPTION makes Windows treat the whole
		// bubble as a title bar, which suppresses every client mouse message —
		// WM_LBUTTONDOWN never arrives and the menu can never open. Dragging is
		// handled above instead.
		return bHtClient

	case bWmClose:
		// Posted by CloseFloatingBubble from the Wails thread. Destroying the
		// window has to happen HERE, on the thread that created it — Windows
		// rejects a cross-thread DestroyWindow, which is why closing the
		// bubble from the UI used to do nothing at all.
		closeMenu()
		gMenuOpen = false
		bDestroyWindow.Call(hwnd)
		return 0

	case bWmDestroy:
		gMenuOpen = false
		bPostQuitMessage.Call(0)
		return 0
	}
	r, _, _ := bDefWindowProcW.Call(hwnd, msg, wParam, lParam)
	return r
}

func drawBubble(hdc uintptr) {
	ensureFonts()
	t := th()

	full := bRECT{Right: bBubbleW, Bottom: bBubbleH}
	// The window region already clips to a circle; painting the surface colour
	// first keeps the edge clean against whatever is behind it.
	fillRect(hdc, &full, t.surface)
	fillEllipse(hdc, full, t.accent)

	// A slightly deeper ring, echoing the app's 1px button border.
	ring := bRECT{Left: 1, Top: 1, Right: bBubbleW - 1, Bottom: bBubbleH - 1}
	rgn, _, _ := bCreateEllipticRgn.Call(
		uintptr(ring.Left), uintptr(ring.Top), uintptr(ring.Right), uintptr(ring.Bottom))
	if rgn != 0 {
		if br, _, _ := bCreateSolidBrush.Call(uintptr(t.accentDeep)); br != 0 {
			bFrameRgn.Call(hdc, rgn, br, 2, 2)
			bDeleteObject.Call(br)
		}
		bDeleteObject.Call(rgn)
	}

	if gFontBubble != 0 {
		bSelectObject.Call(hdc, gFontBubble)
	}
	bSetBkMode.Call(hdc, bTransparent)
	bSetTextColor.Call(hdc, uintptr(t.onAccent))

	// "P" matches the brand emblem in the title bar; "x" while the menu is up.
	label := "P"
	if gMenuOpen {
		label = "\u00D7"
	}
	drawText(hdc, label, &full, uintptr(bDtCenter|bDtVCenter|bDtSingleLine))
}

// ── Menu window procedure ─────────────────────────────────────────────────────

func menuWndProc(hwnd, msg, wParam, lParam uintptr) uintptr {
	switch uint32(msg) {
	case bWmPaint:
		var ps bPAINTSTRUCT
		hdc, _, _ := bBeginPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
		drawMenu(hdc, hwnd)
		bEndPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
		return 0

	case bWmMouseMove:
		old := gHoveredItem
		gHoveredItem = menuHitTest(loWord(lParam), hiWord(lParam))
		if gHoveredItem != old {
			bInvalidateRect.Call(hwnd, 0, 1)
		}
		return 0

	case bWmLButtonDown:
		idx := menuHitTest(loWord(lParam), hiWord(lParam))
		if idx >= 0 && idx < len(bMenuItems) && bMenuItems[idx].cmd != "" {
			cmd := bMenuItems[idx].cmd
			closeMenu()
			gMenuOpen = false
			bInvalidateRect.Call(gBubbleHwnd, 0, 1)
			if cmd == "bubble:pin" {
				togglePin()
			} else {
				emitBubbleCmd(cmd)
			}
		}
		return 0

	case bWmDestroy:
		return 0
	}
	r, _, _ := bDefWindowProcW.Call(hwnd, msg, wParam, lParam)
	return r
}

func menuHitTest(x, y int32) int {
	if x < 0 || x > int32(bMenuW) {
		return -1
	}
	yOff := int32(bHdrH)
	for i, item := range bMenuItems {
		if item.label == "" {
			yOff += 12
			continue
		}
		if y >= yOff && y < yOff+bItemH {
			return i
		}
		yOff += bItemH
	}
	return -1
}

func menuHeight() int32 {
	h := int32(bHdrH)
	for _, item := range bMenuItems {
		if item.label == "" {
			h += 12
		} else {
			h += bItemH
		}
	}
	return h + bPad
}

func drawMenu(hdc, hwnd uintptr) {
	ensureFonts()
	t := th()

	var rc bRECT
	bGetClientRect.Call(hwnd, uintptr(unsafe.Pointer(&rc)))

	// Card surface with a 1px border, same as .launcher-card.
	fillRect(hdc, &rc, t.surface)
	fillRound(hdc, rc, bMenuRadius, t.surface)
	frameRound(hdc, rc, bMenuRadius, t.border)

	bSetBkMode.Call(hdc, bTransparent)

	// Section label, matching .field-label
	bSelectObject.Call(hdc, gFontHdr)
	bSetTextColor.Call(hdc, uintptr(t.muted))
	hdrRc := bRECT{Left: 16, Top: 10, Right: int32(bMenuW - 16), Bottom: int32(bHdrH)}
	bDrawTextW.Call(hdc, uintptr(unsafe.Pointer(bHdrMenuCaption)), ^uintptr(0),
		uintptr(unsafe.Pointer(&hdrRc)), uintptr(bDtVCenter|bDtSingleLine))
	runtime.KeepAlive(&hdrRc)

	yOff := int32(bHdrH)
	for i, item := range bMenuItems {
		if item.label == "" {
			divRc := bRECT{Left: 16, Top: yOff + 5, Right: int32(bMenuW - 16), Bottom: yOff + 6}
			fillRect(hdc, &divRc, t.border)
			yOff += 12
			continue
		}

		// Hovered row: filled rounded rectangle, like .card-row:hover
		if i == gHoveredItem {
			hvRc := bRECT{Left: 8, Top: yOff + 2, Right: int32(bMenuW - 8), Bottom: yOff + bItemH - 2}
			fillRound(hdc, hvRc, bRowRadius, t.hover)
		}

		// Icon badge: tinted rounded square with a 1px border, like
		// .card-icon-badge, with a solid dot of the item's tone inside.
		iy := yOff + (bItemH-bIconSize)/2
		iconRc := bRECT{Left: 14, Top: iy, Right: 14 + bIconSize, Bottom: iy + bIconSize}
		fillRound(hdc, iconRc, bIconRadius, t.accentTint)
		frameRound(hdc, iconRc, bIconRadius, t.border)
		dot := bRECT{
			Left: iconRc.Left + 10, Top: iconRc.Top + 10,
			Right: iconRc.Right - 10, Bottom: iconRc.Bottom - 10,
		}
		fillEllipse(hdc, dot, toneColour(item.tone))

		// The pin entry is a toggle, so its wording reflects current state.
		label, sub := item.label, item.sub
		if item.cmd == "bubble:pin" && gPinned {
			label, sub = "Hide from Top", "Unpin and minimise Presentia"
		}

		bSelectObject.Call(hdc, gFontTitle)
		bSetTextColor.Call(hdc, uintptr(t.ink))
		tRc := bRECT{Left: bTextX, Top: yOff + 8, Right: int32(bMenuW - 14), Bottom: yOff + 28}
		drawText(hdc, label, &tRc, uintptr(bDtSingleLine))

		bSelectObject.Call(hdc, gFontSub)
		bSetTextColor.Call(hdc, uintptr(t.muted))
		sRc := bRECT{Left: bTextX, Top: yOff + 28, Right: int32(bMenuW - 14), Bottom: yOff + bItemH - 4}
		drawText(hdc, sub, &sRc, uintptr(bDtSingleLine))

		yOff += bItemH
	}
}

// ── Window classes ────────────────────────────────────────────────────────────

// registerClasses registers both window classes exactly once per process.
// Re-registering on every open leaked a brush each time and failed silently.
func registerClasses(hInst uintptr) {
	gClassOnce.Do(func() {
		ensureWndProcs()
		cursor, _, _ := bLoadCursorW.Call(0, 32512) // IDC_ARROW

		bubbleBr, _, _ := bCreateSolidBrush.Call(uintptr(bDark.surface))
		wcBubble := bWNDCLASSEX{
			CbSize:        uint32(unsafe.Sizeof(bWNDCLASSEX{})),
			LpfnWndProc:   gBubbleWndProc,
			HInstance:     hInst,
			LpszClassName: bClsBubble,
			HCursor:       cursor,
			HbrBackground: bubbleBr,
		}
		bRegisterClassExW.Call(uintptr(unsafe.Pointer(&wcBubble)))

		menuBr, _, _ := bCreateSolidBrush.Call(uintptr(bDark.surface))
		wcMenu := bWNDCLASSEX{
			CbSize:        uint32(unsafe.Sizeof(bWNDCLASSEX{})),
			LpfnWndProc:   gMenuWndProc,
			HInstance:     hInst,
			LpszClassName: bClsMenu,
			HCursor:       cursor,
			HbrBackground: menuBr,
		}
		bRegisterClassExW.Call(uintptr(unsafe.Pointer(&wcMenu)))
	})
}

// ── Menu window open / close ──────────────────────────────────────────────────

func openMenu(bubbleHwnd uintptr) {
	if gMenuHwnd != 0 {
		return
	}
	hInst, _, _ := bGetModuleHandleW.Call(0)
	registerClasses(hInst)

	// Position above the bubble
	var brc bRECT
	bGetWindowRect.Call(bubbleHwnd, uintptr(unsafe.Pointer(&brc)))
	mh := menuHeight()
	mx := brc.Left - int32(bMenuW) + int32(bBubbleW)
	my := brc.Top - mh - 6

	// Keep on screen
	sw, _, _ := bGetSystemMetrics.Call(0)
	sh, _, _ := bGetSystemMetrics.Call(1)
	if mx < 0 {
		mx = 0
	}
	if mx+int32(bMenuW) > int32(sw) {
		mx = int32(sw) - int32(bMenuW)
	}
	if my < 0 {
		my = brc.Bottom + 6
	}
	if my+mh > int32(sh) {
		my = int32(sh) - mh
	}

	h, _, _ := bCreateWindowExW.Call(
		bWsExTopmost|bWsExToolWindow|bWsExNoActivate,
		uintptr(unsafe.Pointer(bClsMenu)),
		uintptr(unsafe.Pointer(bTitleMenu)),
		bWsPopup|bWsVisible,
		uintptr(mx), uintptr(my), uintptr(bMenuW), uintptr(mh),
		0, 0, hInst, 0,
	)
	if h == 0 {
		gMenuOpen = false
		return
	}
	gMenuHwnd = h
	// Clip the window to a rounded rectangle so the corners are genuinely
	// round rather than painted over a square frame.
	if rgn, _, _ := bCreateRoundRectRgn.Call(0, 0,
		uintptr(bMenuW+1), uintptr(mh+1),
		uintptr(bMenuRadius*2), uintptr(bMenuRadius*2)); rgn != 0 {
		bSetWindowRgn.Call(h, rgn, 1)
	}
	bShowWindow.Call(h, 5)
	bUpdateWindow.Call(h)
}

// closeMenu destroys the popup. Only call it from the bubble's own thread.
func closeMenu() {
	if gMenuHwnd != 0 {
		bDestroyWindow.Call(gMenuHwnd)
		gMenuHwnd = 0
	}
	gHoveredItem = -1
}

// ── Event emission ────────────────────────────────────────────────────────────

func emitBubbleCmd(cmd string) {
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
				if gBubbleApp != nil && gBubbleApp.ctx != nil {
					wailsruntime.LogError(gBubbleApp.ctx,
						"Monitor bubble failed and was closed")
					wailsruntime.WindowUnminimise(gBubbleApp.ctx)
					wailsruntime.EventsEmit(gBubbleApp.ctx, "bubble:failed")
				}
			}
		}()

		hInst, _, _ := bGetModuleHandleW.Call(0)
		registerClasses(hInst)

		// Start at bottom-right of primary monitor, above the taskbar
		sw, _, _ := bGetSystemMetrics.Call(0)
		sh, _, _ := bGetSystemMetrics.Call(1)
		startX := int32(sw) - bBubbleW - 30
		startY := int32(sh) - bBubbleH - 80
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
			uintptr(startX), uintptr(startY), bBubbleW, bBubbleH,
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

		// Round the window into an actual bubble.  Windows takes ownership
		// of the region, so it must not be deleted here.
		if rgn, _, _ := bCreateEllipticRgn.Call(0, 0, bBubbleW+1, bBubbleH+1); rgn != 0 {
			bSetWindowRgn.Call(hwnd, rgn, 1)
		}

		// 90% opacity
		bSetLayeredWindowAttributes.Call(hwnd, 0, 230, bLwaAlpha)
		bShowWindow.Call(hwnd, 5)
		bUpdateWindow.Call(hwnd)

		gBubbleMu.Lock()
		gBubbleHwnd = hwnd
		tid, _, _ := bGetCurrentThreadId.Call()
		gThreadID = uint32(tid)
		gBubbleMu.Unlock()

		// Only minimise once the bubble is actually on screen. Minimising
		// first meant a failed bubble left the user staring at an empty
		// desktop, looking exactly like a crash.
		if gBubbleApp != nil && gBubbleApp.ctx != nil {
			wailsruntime.WindowMinimise(gBubbleApp.ctx)
		}

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

		// Restore Presentia when bubble exits
		if gBubbleApp != nil && gBubbleApp.ctx != nil {
			wailsruntime.WindowUnminimise(gBubbleApp.ctx)
		}
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
