//go:build windows

package main

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	"unsafe"

	"presentia-desktop/internal/bubbleui"
)

// ── Bubble B: capsule + panel ────────────────────────────────────────────────
//
// The bubble is a small capsule that shows what the monitor is doing (live
// time, present/total, cameras off). Clicking it opens a panel with the
// controls: what to watch, Start/Stop, the students not on camera with a Copy
// button for the reminder message, Live View, and pin/open/hide.
//
// Both are layered windows fed with pixels from internal/bubbleui, which draws
// them in the theme picked in Settings → Appearance. Everything here runs on
// the bubble thread (the one that created the windows), except the setters at
// the top, which only store values and post a message to that thread.

var (
	gBubbleUIMu sync.Mutex
	gThemeKey   = "dark"
	gSrcKind    string // "", "area", "window"
	gSrcLabel   string
	gSrcDetail  string
	gClassTitle string
)

// setBubbleThemeKeyNative follows Settings → Appearance (light, brutal,
// editorial, bento, iri, dark).
func setBubbleThemeKeyNative(key string) {
	gBubbleUIMu.Lock()
	gThemeKey = key
	gBubbleUIMu.Unlock()
	th := bubbleui.ThemeFor(key)
	gDarkTheme = th.Dark()
	gIridescent = key == "iri"
	postToBubble(bWmAppTheme)
	pipPost()
}

// setBubbleSourceNative is what the Monitor page has picked to watch, and the
// class it belongs to.
func setBubbleSourceNative(kind, label, detail, title string) {
	gBubbleUIMu.Lock()
	gSrcKind, gSrcLabel, gSrcDetail, gClassTitle = kind, label, detail, title
	gBubbleUIMu.Unlock()
	postToBubble(bWmAppLive)
}

func bubbleIsOpenNative() bool { return bubbleActive() }

func bubbleTheme() bubbleui.Theme {
	gBubbleUIMu.Lock()
	defer gBubbleUIMu.Unlock()
	return bubbleui.ThemeFor(gThemeKey)
}

// ── state of the two windows (bubble thread only) ────────────────────────────

var (
	gCapLayout  *bubbleui.Layout
	gCapBody    bPOINT // screen position of the capsule's visible body
	gCapPlaced  bool
	gCapBmp     uintptr
	gCapPx      []byte
	gCapW       int32
	gCapH       int32
	gCapHover   bool
	gCapTracked bool

	gPanelHwnd    uintptr
	gPanelLayout  *bubbleui.Layout
	gPanelBmp     uintptr
	gPanelPx      []byte
	gPanelW       int32
	gPanelH       int32
	gPanelHover   string
	gPanelPress   string
	gPanelTracked bool
	gCopied       string

	gPanelOnce sync.Once
	gPanelProc uintptr
	bClsPanel  = syscall.StringToUTF16Ptr("PresentiaBubblePanelCls")
	bTitlePan  = syscall.StringToUTF16Ptr("")

	bMonitorFromPoint = bUser32.NewProc("MonitorFromPoint")
	bGetMonitorInfoW  = bUser32.NewProc("GetMonitorInfoW")
	bSetCursor        = bUser32.NewProc("SetCursor")
	bOpenClipboard    = bUser32.NewProc("OpenClipboard")
	bEmptyClipboard   = bUser32.NewProc("EmptyClipboard")
	bSetClipboardData = bUser32.NewProc("SetClipboardData")
	bCloseClipboard   = bUser32.NewProc("CloseClipboard")
	bGlobalAlloc      = bKernel32.NewProc("GlobalAlloc")
	bGlobalLock       = bKernel32.NewProc("GlobalLock")
	bGlobalUnlock     = bKernel32.NewProc("GlobalUnlock")
	bGlobalFree       = bKernel32.NewProc("GlobalFree")
)

const (
	bWmMouseLeaveMsg = 0x02A3
	bWmSetCursor     = 0x0020
	bWmRButtonUp     = 0x0205
	copiedTimerID    = 7
)

// ── text through GDI with the app's Poppins ──────────────────────────────────

type gdiText struct{}

var gTextFonts = map[[2]int]uintptr{}

func (gdiText) font(f bubbleui.Font) uintptr {
	px := int(math.Round(f.Px * ssK))
	w := 500
	if f.Bold {
		w = 700
	}
	k := [2]int{px, w}
	if h, ok := gTextFonts[k]; ok {
		return h
	}
	h := makeFont(px, uintptr(w), labelFace())
	gTextFonts[k] = h
	return h
}

func (t gdiText) Width(f bubbleui.Font, s string) float64 { return textWidth(t.font(f), s) }

func (t gdiText) Draw(c *bubbleui.Canvas, f bubbleui.Font, s string, x0, y0, x1, y1 float64, align int, col bubbleui.RGB) {
	flags := uintptr(bDtVCenter | bDtSingleLine | bDtNoPrefix)
	switch align {
	case bubbleui.Center:
		flags |= bDtCenter
	case bubbleui.Right:
		flags |= 0x0002 // DT_RIGHT
	}
	ssDraw(&gBubbleSS, s, t.font(f), x0, y0, x1, y1, flags, int32(c.W), int32(c.H),
		func(x, y int32, a float64) { c.Blend(int(x), int(y), a, col) })
}

// ── what to show ─────────────────────────────────────────────────────────────

func bubbleView() bubbleui.View {
	st := liveSnapshot()
	gBubbleUIMu.Lock()
	kind, label, detail, title := gSrcKind, gSrcLabel, gSrcDetail, gClassTitle
	gBubbleUIMu.Unlock()
	if title == "" {
		title = st.Name
	}
	v := bubbleui.View{
		Active: st.Active, Elapsed: st.Elapsed, Title: title,
		Present: st.Present, Missing: st.Missing, Waiting: st.Waiting, Total: st.Total, Unknown: st.Unknown,
		SourceKind: kind, SourceLabel: label, SourceDetail: detail,
		PanelOpen: gPanelHwnd != 0 && gPanelAnim.dir >= 0, Hover: gPanelHover, Copied: gCopied,
	}
	for _, a := range st.Away {
		v.Away = append(v.Away, bubbleui.Student{Name: a.Name, Away: a.Away})
	}
	return v
}

// ── screen geometry ──────────────────────────────────────────────────────────

type bMONITORINFO struct {
	CbSize    uint32
	RcMonitor bRECT
	RcWork    bRECT
	DwFlags   uint32
}

// workAreaAt is the work area (screen minus taskbar) of the monitor at p.
func workAreaAt(p bPOINT) bRECT {
	var wa bRECT
	if bMonitorFromPoint.Find() == nil {
		const defaultToNearest = 2
		h, _, _ := bMonitorFromPoint.Call(uintptr(uint32(p.X))|uintptr(uint32(p.Y))<<32, defaultToNearest)
		if h != 0 {
			mi := bMONITORINFO{CbSize: uint32(unsafe.Sizeof(bMONITORINFO{}))}
			if r, _, _ := bGetMonitorInfoW.Call(h, uintptr(unsafe.Pointer(&mi))); r != 0 {
				return mi.RcWork
			}
		}
	}
	bSystemParametersInfo.Call(bSpiGetWorkArea, 0, uintptr(unsafe.Pointer(&wa)), 0)
	return wa
}

func capPosFile() string {
	dir, err := os.UserConfigDir()
	if err != nil {
		return ""
	}
	return filepath.Join(dir, "Presentia", "bubble-position.json")
}

func loadCapPos() {
	if f := capPosFile(); f != "" {
		if b, err := os.ReadFile(f); err == nil {
			var p struct{ X, Y int32 }
			if json.Unmarshal(b, &p) == nil {
				gCapBody, gCapPlaced = bPOINT{p.X, p.Y}, true
			}
		}
	}
}

func saveCapPos() {
	if f := capPosFile(); f != "" {
		_ = os.MkdirAll(filepath.Dir(f), 0o755)
		b, _ := json.Marshal(struct{ X, Y int32 }{gCapBody.X, gCapBody.Y})
		_ = os.WriteFile(f, b, 0o644)
	}
}

// capWindowPos turns the body position into the window position (the window
// also holds the shadow), keeping the whole body on its monitor.
func capWindowPos(l *bubbleui.Layout) bPOINT {
	bw, bh := int32(l.BX1-l.BX0), int32(l.BY1-l.BY0)
	if !gCapPlaced {
		wa := workAreaAt(bPOINT{0, 0})
		m := int32(24 * uiScale())
		gCapBody = bPOINT{wa.Right - bw - m, wa.Bottom - bh - m}
		gCapPlaced = true
	}
	wa := workAreaAt(bPOINT{gCapBody.X + bw/2, gCapBody.Y + bh/2})
	b := gCapBody
	b.X = max(wa.Left, min(b.X, wa.Right-bw))
	b.Y = max(wa.Top, min(b.Y, wa.Bottom-bh))
	return bPOINT{b.X - int32(l.BX0), b.Y - int32(l.BY0)}
}

// snapCapsule pulls the capsule against a screen edge it was dropped near.
func snapCapsule() {
	if gCapLayout == nil {
		return
	}
	l := gCapLayout
	s := uiScale()
	bw, bh := int32(l.BX1-l.BX0), int32(l.BY1-l.BY0)
	wa := workAreaAt(bPOINT{gCapBody.X + bw/2, gCapBody.Y + bh/2})
	near, gap := int32(32*s), int32(10*s)
	if gCapBody.X-wa.Left < near {
		gCapBody.X = wa.Left + gap
	} else if wa.Right-(gCapBody.X+bw) < near {
		gCapBody.X = wa.Right - bw - gap
	}
	if gCapBody.Y-wa.Top < near {
		gCapBody.Y = wa.Top + gap
	} else if wa.Bottom-(gCapBody.Y+bh) < near {
		gCapBody.Y = wa.Bottom - bh - gap
	}
}

// ── drawing ──────────────────────────────────────────────────────────────────

// fillDIB copies a canvas into a (re)used DIB.
func fillDIB(c *bubbleui.Canvas, bmp *uintptr, px *[]byte, w, h *int32) bool {
	cw, ch := int32(c.W), int32(c.H)
	if *bmp == 0 || cw != *w || ch != *h {
		if *bmp != 0 {
			bDeleteObject.Call(*bmp)
		}
		*bmp, *px = newDIB(cw, ch)
		if *bmp == 0 {
			*w, *h = 0, 0
			return false
		}
		*w, *h = cw, ch
	}
	c.BGRA(*px)
	return true
}

// renderCapsule redraws the capsule. Bubble thread only.
func renderCapsule() {
	h := gBubbleHwnd
	if h == 0 {
		return
	}
	v := bubbleView()
	if gCapHover {
		v.Hover = "capsule"
	}
	v.Pulse = gPulse
	l := bubbleui.RenderCapsule(v, bubbleTheme(), gdiText{}, uiScale())
	if !fillDIB(l.C, &gCapBmp, &gCapPx, &gCapW, &gCapH) {
		return
	}
	gCapLayout = l
	pushCapsuleFrame()
}

// pushCapsuleFrame shows the capsule's current picture at the current step
// of its fade-in (no redraw).
func pushCapsuleFrame() {
	if gBubbleHwnd == 0 || gCapLayout == nil || gCapBmp == 0 {
		return
	}
	pos := capWindowPos(gCapLayout)
	e := easeOutCubic(gCapAnim.p)
	pos.Y += int32(math.Round((1 - e) * 6 * uiScale()))
	pushLayeredAlpha(gBubbleHwnd, gCapBmp, gCapW, gCapH, &pos, byte(math.Round(255*e)))
}

// panelWindowPos puts the panel under the capsule (or above it when there is
// no room below), lined up with its left edge.
func panelWindowPos(l *bubbleui.Layout) bPOINT {
	c := gCapLayout
	s := uiScale()
	gap := int32(8 * s)
	pw, ph := int32(l.BX1-l.BX0), int32(l.BY1-l.BY0)
	cw, ch := int32(c.BX1-c.BX0), int32(c.BY1-c.BY0)
	wa := workAreaAt(bPOINT{gCapBody.X + cw/2, gCapBody.Y + ch/2})
	x := gCapBody.X
	y := gCapBody.Y + ch + gap
	if y+ph > wa.Bottom {
		y = gCapBody.Y - gap - ph
	}
	x = max(wa.Left, min(x, wa.Right-pw))
	y = max(wa.Top, min(y, wa.Bottom-ph))
	return bPOINT{x - int32(l.BX0), y - int32(l.BY0)}
}

// renderPanel redraws the panel if it is open. Bubble thread only.
func renderPanel() {
	h := gPanelHwnd
	if h == 0 || gCapLayout == nil {
		return
	}
	l := bubbleui.RenderPanel(bubbleView(), bubbleTheme(), gdiText{}, uiScale())
	if !fillDIB(l.C, &gPanelBmp, &gPanelPx, &gPanelW, &gPanelH) {
		return
	}
	gPanelLayout = l
	pushPanelFrame()
}

// pushPanelFrame shows the panel's current picture at the current step of
// its open/close animation: it fades in while sliding out of the capsule.
func pushPanelFrame() {
	if gPanelHwnd == 0 || gPanelLayout == nil || gPanelBmp == 0 {
		return
	}
	pos := panelWindowPos(gPanelLayout)
	e := easeOutCubic(gPanelAnim.p)
	// It starts a little closer to the capsule: above its spot when it
	// opens downwards, below it when it opens upwards.
	slide := (1 - e) * 10 * uiScale()
	if pos.Y+int32(gPanelLayout.BY0) >= gCapBody.Y {
		slide = -slide
	}
	pos.Y += int32(math.Round(slide))
	pushLayeredAlpha(gPanelHwnd, gPanelBmp, gPanelW, gPanelH, &pos, byte(math.Round(255*e)))
}

// renderBubble redraws whatever is open.
func renderBubble() {
	updatePulse()
	renderCapsule()
	renderPanel()
}

// ── animation ────────────────────────────────────────────────────────────────
//
// The capsule fades in when the bubble opens, the panel fades and slides in
// and out, and the live dot breathes while monitoring. All of it follows the
// app's Animations setting (Settings → Appearance). Frames only move and fade
// the already-drawn bitmaps, except the dot, which redraws the capsule a few
// times a second.

type anim struct {
	p     float64 // 0 = hidden, 1 = shown
	from  float64
	dir   int // +1 showing, -1 hiding, 0 still
	start time.Time
}

const (
	animTimerID  = 9
	pulseTimerID = 10
	panelOpenMs  = 180
	panelCloseMs = 120
	capFadeMs    = 220
	pulsePeriod  = 1.6 // seconds per breath
)

var (
	gAnimations atomic.Bool
	gCapAnim    = anim{p: 1}
	gPanelAnim  = anim{p: 1}
	gPulse      float64
	gPulseOn    bool
	gPulseStart time.Time
	gAnimTimer  bool
)

func init() { gAnimations.Store(true) }

// setBubbleAnimationsNative follows the app's Animations switch.
func setBubbleAnimationsNative(on bool) {
	gAnimations.Store(on)
	postToBubble(bWmAppLive)
}

func easeOutCubic(t float64) float64 {
	t = clamp01(t)
	u := 1 - t
	return 1 - u*u*u
}

func (a *anim) run(dir int) {
	if !gAnimations.Load() {
		a.dir = 0
		if dir > 0 {
			a.p = 1
		} else {
			a.p = 0
		}
		return
	}
	a.from, a.dir, a.start = a.p, dir, time.Now()
	ensureAnimTimer()
}

// step advances the animation; true while it is still moving.
func (a *anim) step(ms float64) bool {
	if a.dir == 0 {
		return false
	}
	t := float64(time.Since(a.start).Milliseconds()) / ms
	a.p = clamp01(a.from + float64(a.dir)*t)
	if (a.dir > 0 && a.p >= 1) || (a.dir < 0 && a.p <= 0) {
		a.dir = 0
		return false
	}
	return true
}

func ensureAnimTimer() {
	if !gAnimTimer && gBubbleHwnd != 0 {
		bSetTimer.Call(gBubbleHwnd, animTimerID, 15, 0)
		gAnimTimer = true
	}
}

// animTick runs one frame (capsule thread, WM_TIMER).
func animTick() {
	moving := false
	if gCapAnim.dir != 0 {
		moving = gCapAnim.step(capFadeMs) || moving
		pushCapsuleFrame()
	}
	if gPanelAnim.dir != 0 {
		ms := float64(panelOpenMs)
		if gPanelAnim.dir < 0 {
			ms = panelCloseMs
		}
		still := gPanelAnim.step(ms)
		moving = still || moving
		if !still && gPanelAnim.p <= 0 {
			destroyPanel() // finished closing
		} else {
			pushPanelFrame()
		}
	}
	if !moving && gAnimTimer {
		bKillTimer.Call(gBubbleHwnd, animTimerID)
		gAnimTimer = false
	}
}

// updatePulse starts or stops the live dot's breathing.
func updatePulse() {
	want := gAnimations.Load() && liveSnapshot().Active && gBubbleHwnd != 0
	if want == gPulseOn {
		return
	}
	gPulseOn = want
	if want {
		gPulseStart = time.Now()
		bSetTimer.Call(gBubbleHwnd, pulseTimerID, 70, 0)
	} else {
		bKillTimer.Call(gBubbleHwnd, pulseTimerID)
		gPulse = 0
	}
}

func pulseTick() {
	t := time.Since(gPulseStart).Seconds() / pulsePeriod
	gPulse = t - math.Floor(t)
	renderCapsule()
}

// ── panel window ─────────────────────────────────────────────────────────────

func openPanel() {
	if gPanelHwnd != 0 {
		if gPanelAnim.dir < 0 { // reopened while closing: turn back
			gPanelAnim.run(+1)
			renderCapsule()
		}
		return
	}
	hInst, _, _ := bGetModuleHandleW.Call(0)
	gPanelOnce.Do(func() {
		gPanelProc = syscall.NewCallback(panelWndProc)
		arrow, _, _ := bLoadCursorW.Call(0, 32512)
		wc := bWNDCLASSEX{
			CbSize:        uint32(unsafe.Sizeof(bWNDCLASSEX{})),
			LpfnWndProc:   gPanelProc,
			HInstance:     hInst,
			LpszClassName: bClsPanel,
			HCursor:       arrow,
		}
		bRegisterClassExW.Call(uintptr(unsafe.Pointer(&wc)))
	})
	h, _, _ := bCreateWindowExW.Call(
		bWsExTopmost|bWsExToolWindow|bWsExLayered|bWsExNoActivate,
		uintptr(unsafe.Pointer(bClsPanel)), uintptr(unsafe.Pointer(bTitlePan)),
		bWsPopup, 0, 0, 1, 1, 0, 0, hInst, 0,
	)
	if h == 0 {
		return
	}
	gPanelHwnd = h
	gPanelHover, gPanelPress, gPanelTracked = "", "", false
	excludeFromCapture(h)
	gPanelAnim = anim{p: 0}
	gPanelAnim.run(+1)
	renderBubble()         // the capsule's arrow flips too
	bShowWindow.Call(h, 4) // SW_SHOWNOACTIVATE
}

// closePanel tucks the panel away (animated when animations are on).
func closePanel() {
	if gPanelHwnd == 0 || gPanelAnim.dir < 0 {
		return
	}
	gPanelHover, gPanelPress = "", ""
	gPanelAnim.run(-1)
	if gPanelAnim.dir == 0 { // animations off
		destroyPanel()
		return
	}
	renderCapsule() // the arrow flips back right away
}

// destroyPanel removes the panel at once.
func destroyPanel() {
	if gPanelHwnd == 0 {
		return
	}
	bDestroyWindow.Call(gPanelHwnd)
	gPanelHwnd = 0
	gPanelLayout = nil
	gPanelHover, gPanelPress = "", ""
	gPanelAnim = anim{p: 1}
	if gPanelBmp != 0 {
		bDeleteObject.Call(gPanelBmp)
		gPanelBmp, gPanelPx, gPanelW, gPanelH = 0, nil, 0, 0
	}
	renderCapsule()
}

func togglePanel() {
	if gPanelHwnd != 0 && gPanelAnim.dir >= 0 {
		closePanel()
	} else {
		openPanel()
	}
}

func setHandCursor(hand bool) {
	id := uintptr(32512) // IDC_ARROW
	if hand {
		id = 32649 // IDC_HAND
	}
	c, _, _ := bLoadCursorW.Call(0, id)
	bSetCursor.Call(c)
}

func panelWndProc(hwnd, msg, wParam, lParam uintptr) uintptr {
	switch uint32(msg) {
	case bWmPaint:
		var ps bPAINTSTRUCT
		bBeginPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
		bEndPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
		return 0
	case bWmNcHitTest:
		return bHtClient
	case bWmSetCursor:
		setHandCursor(gPanelHover != "")
		return 1
	case bWmMouseMove:
		if !gPanelTracked {
			tme := bTRACKMOUSEEVENT{CbSize: uint32(unsafe.Sizeof(bTRACKMOUSEEVENT{})), DwFlags: bTmeLeave, HwndTrack: hwnd}
			bTrackMouseEvent.Call(uintptr(unsafe.Pointer(&tme)))
			gPanelTracked = true
		}
		id := ""
		if gPanelLayout != nil {
			id = gPanelLayout.HitAt(float64(loWord(lParam)), float64(hiWord(lParam)))
		}
		if id != gPanelHover {
			gPanelHover = id
			setHandCursor(id != "")
			renderPanel()
		}
		return 0
	case bWmMouseLeaveMsg:
		gPanelTracked = false
		if gPanelHover != "" {
			gPanelHover = ""
			renderPanel()
		}
		return 0
	case bWmLButtonDown:
		if gPanelLayout != nil {
			gPanelPress = gPanelLayout.HitAt(float64(loWord(lParam)), float64(hiWord(lParam)))
		}
		return 0
	case bWmLButtonUp:
		id := ""
		if gPanelLayout != nil {
			id = gPanelLayout.HitAt(float64(loWord(lParam)), float64(hiWord(lParam)))
		}
		press := gPanelPress
		gPanelPress = ""
		if id != "" && id == press && gPanelAnim.dir >= 0 {
			panelAction(id)
		}
		return 0
	case bWmTimer:
		if wParam == copiedTimerID {
			bKillTimer.Call(hwnd, copiedTimerID)
			gCopied = ""
			renderPanel()
		}
		return 0
	}
	r, _, _ := bDefWindowProcW.Call(hwnd, msg, wParam, lParam)
	return r
}

// panelAction runs a panel control. Bubble thread only.
func panelAction(id string) {
	switch {
	case id == "close":
		closePanel()
	case id == "area":
		// Out of the way first: the picker takes a screenshot of the screen.
		closePanel()
		emitBubbleCmd("bubble:screen_area")
	case id == "window":
		closePanel()
		emitBubbleCmd("bubble:win_picker")
	case id == "start":
		emitBubbleCmd("bubble:launch")
	case id == "stop":
		emitBubbleCmd("bubble:stop")
	case id == "liveview":
		toggleLiveView()
	case id == "app":
		showMain()
	case id == "hide":
		closePanel()
		emitBubbleCmd("bubble:quit") // lets the page update its button
		CloseFloatingBubble()
	case strings.HasPrefix(id, "copy:"):
		var i int
		for _, ch := range id[5:] {
			i = i*10 + int(ch-'0')
		}
		st := liveSnapshot()
		if i >= 0 && i < len(st.Away) {
			a := st.Away[i]
			if setClipboard(gPanelHwnd, bubbleui.CopyMessage(a.Name, a.Away)) {
				gCopied = id
				bSetTimer.Call(gPanelHwnd, copiedTimerID, 1600, 0)
				renderPanel()
			}
		}
	}
}

// setClipboard puts text on the clipboard.
func setClipboard(hwnd uintptr, s string) bool {
	u, err := syscall.UTF16FromString(s)
	if err != nil {
		return false
	}
	if r, _, _ := bOpenClipboard.Call(hwnd); r == 0 {
		return false
	}
	defer bCloseClipboard.Call()
	bEmptyClipboard.Call()
	const gmemMoveable, cfUnicodeText = 0x0002, 13
	mem, _, _ := bGlobalAlloc.Call(gmemMoveable, uintptr(len(u)*2))
	if mem == 0 {
		return false
	}
	p, _, _ := bGlobalLock.Call(mem)
	if p == 0 {
		bGlobalFree.Call(mem)
		return false
	}
	copy(unsafe.Slice((*uint16)(unsafe.Pointer(p)), len(u)), u)
	bGlobalUnlock.Call(mem)
	if r, _, _ := bSetClipboardData.Call(cfUnicodeText, mem); r == 0 {
		bGlobalFree.Call(mem)
		return false
	}
	return true // the clipboard owns the memory now
}

// releaseBubbleBitmaps frees the capsule's bitmap when the bubble closes.
func releaseBubbleBitmaps() {
	if gCapBmp != 0 {
		bDeleteObject.Call(gCapBmp)
		gCapBmp, gCapPx, gCapW, gCapH = 0, nil, 0, 0
	}
	gCapLayout = nil
	gCapHover, gCapTracked = false, false
	gAnimTimer, gPulseOn, gPulse = false, false, 0
	gCapAnim, gPanelAnim = anim{p: 1}, anim{p: 1}
}
