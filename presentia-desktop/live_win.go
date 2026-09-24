//go:build windows

package main

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	"unsafe"
)

// ── Live monitoring while in bubble mode ─────────────────────────────────────
//
// With the bubble up, the main window sits hidden in the tray, so everything
// here talks to the Python sidecar directly instead of going through the
// webview:
//
//   - a poller reads /api/monitor/live twice a second and feeds the stats
//     chip under the bubble, the tray tooltip and the Live View pop-out;
//   - while the Live View is open, a second loop pulls the annotated preview
//     from /api/monitor/frame (only when there is a newer frame).
//
// The Live View is the same resizable pop-out window as the Register page's
// Live Face viewer (pip_win.go), in its "monitor" mode.

type liveStats struct {
	Active    bool    `json:"active"`
	Name      string  `json:"name"`
	Elapsed   float64 `json:"elapsed"`
	Present   int     `json:"present"`
	Missing   int     `json:"missing"`
	Waiting   int     `json:"waiting"`
	Total     int     `json:"total"`
	Unknown   int     `json:"unknown"`
	LastAlert string  `json:"last_alert"`
	LastLevel string  `json:"last_level"`
	FrameSeq  int64   `json:"frame_seq"`
}

const bWmAppLive = 0x8000 + 3 // WM_APP+3: new live stats for the bubble thread

var (
	liveHTTP = &http.Client{Timeout: 2 * time.Second}

	gLiveMu        sync.Mutex
	gLive          liveStats
	gLiveStop      chan struct{}
	gLiveDismissed bool        // user closed the Live View during this run
	gLiveFrames    atomic.Bool // the frame loop is running
)

func liveSnapshot() liveStats {
	gLiveMu.Lock()
	defer gLiveMu.Unlock()
	return gLive
}

// startLivePoller runs until stopLivePoller; called when the bubble opens.
func startLivePoller() {
	stop := make(chan struct{})
	gLiveMu.Lock()
	if gLiveStop != nil {
		close(gLiveStop)
	}
	gLiveStop = stop
	gLive = liveStats{}
	gLiveDismissed = false
	gLiveMu.Unlock()
	go livePollLoop(stop)
}

func stopLivePoller() {
	gLiveMu.Lock()
	if gLiveStop != nil {
		close(gLiveStop)
		gLiveStop = nil
	}
	gLive = liveStats{}
	gLiveMu.Unlock()
}

func fetchLive() (liveStats, bool) {
	var st liveStats
	resp, err := liveHTTP.Get(sidecarURL + "/api/monitor/live")
	if err != nil {
		return st, false
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return st, false
	}
	if err := json.NewDecoder(resp.Body).Decode(&st); err != nil {
		return st, false
	}
	return st, true
}

func livePollLoop(stop chan struct{}) {
	tick := time.NewTicker(500 * time.Millisecond)
	defer tick.Stop()
	prevActive := false
	for {
		st, _ := fetchLive() // unreachable sidecar = inactive
		select {
		case <-stop:
			return
		default:
		}
		gLiveMu.Lock()
		gLive = st
		if st.Active && !prevActive {
			gLiveDismissed = false // a new run: offer the Live View again
		}
		dismissed := gLiveDismissed
		gLiveMu.Unlock()

		postToBubble(bWmAppLive)
		trayTip(trayTipText(st))
		liveUpdatePip(st)

		// Monitoring just started: pop the Live View open once.
		if st.Active && !prevActive && !dismissed {
			if open, _ := pipIsOpen(); !open {
				openLiveView()
			}
		}
		prevActive = st.Active

		select {
		case <-stop:
			return
		case <-tick.C:
		}
	}
}

// ── Formatting ───────────────────────────────────────────────────────────────

func fmtElapsed(sec float64) string {
	t := int(sec)
	if t >= 3600 {
		return fmt.Sprintf("%d:%02d:%02d", t/3600, t/60%60, t%60)
	}
	return fmt.Sprintf("%d:%02d", t/60, t%60)
}

// liveSegs is the coloured stats line shared by the chip and the Live View.
func liveSegs(st liveStats) []pipSeg {
	segs := []pipSeg{{fmt.Sprintf("%d/%d present", st.Present, st.Total), segPresent}}
	if st.Missing > 0 {
		segs = append(segs, pipSeg{fmt.Sprintf("%d missing", st.Missing), segMissing})
	}
	if st.Unknown > 0 {
		segs = append(segs, pipSeg{fmt.Sprintf("%d unknown", st.Unknown), segUnknown})
	}
	return segs
}

func trayTipText(st liveStats) string {
	if !st.Active {
		return "Presentia — bubble active"
	}
	t := fmt.Sprintf("Presentia — monitoring %s · %d/%d present", fmtElapsed(st.Elapsed), st.Present, st.Total)
	if st.Missing > 0 {
		t += fmt.Sprintf(" · %d missing", st.Missing)
	}
	if st.Unknown > 0 {
		t += fmt.Sprintf(" · %d unknown", st.Unknown)
	}
	return t
}

// ── Live View (monitor-mode pop-out) ─────────────────────────────────────────

func liveUpdatePip(st liveStats) {
	if open, src := pipIsOpen(); !open || src != pipSrcMonitor {
		return
	}
	if !st.Active {
		pipSetFrame(pipSrcMonitor, nil)
		pipSetStatus(pipSrcMonitor, "Idle", "", "Not monitoring",
			"Press Start Live Monitor on the bubble", nil)
		return
	}
	msg := st.LastAlert
	if msg == "" {
		msg = "Watching " + st.Name
	}
	pipSetStatus(pipSrcMonitor, "Live "+fmtElapsed(st.Elapsed), "", msg,
		"Waiting for the first frame…", liveSegs(st))
}

// openLiveView shows the monitoring preview in the resizable pop-out.
func openLiveView() {
	app := gBubbleApp
	if app == nil {
		return
	}
	pipUseSource(pipSrcMonitor, "Live Monitor", 400, 225) // 16:9 for screen captures
	if !pipOpen(app, "Live Monitor") {
		return
	}
	liveUpdatePip(liveSnapshot())
	if gLiveFrames.CompareAndSwap(false, true) {
		go liveFrameLoop()
	}
}

// toggleLiveView opens or closes the Live View (bubble dial, chip, tray).
func toggleLiveView() {
	if open, src := pipIsOpen(); open && src == pipSrcMonitor {
		closePipNative()
		return
	}
	gLiveMu.Lock()
	gLiveDismissed = false
	gLiveMu.Unlock()
	openLiveView()
}

// liveViewClosed is called from the pop-out thread when the Live View goes.
func liveViewClosed() {
	gLiveMu.Lock()
	gLiveDismissed = true
	gLiveMu.Unlock()
	pipUseSource(pipSrcPush, "Live Face", pipW, pipVideoH)
}

// liveFrameLoop pulls the newest annotated preview while the Live View is up.
func liveFrameLoop() {
	defer gLiveFrames.Store(false)
	var seq int64
	for {
		if open, src := pipIsOpen(); !open || src != pipSrcMonitor {
			return
		}
		resp, err := liveHTTP.Get(sidecarURL + "/api/monitor/frame?after=" + strconv.FormatInt(seq, 10))
		if err != nil {
			time.Sleep(500 * time.Millisecond)
			continue
		}
		if resp.StatusCode == http.StatusOK {
			raw, err := io.ReadAll(resp.Body)
			resp.Body.Close()
			if err == nil {
				if n, err := strconv.ParseInt(resp.Header.Get("X-Frame-Seq"), 10, 64); err == nil {
					seq = n
				}
				if img := decodeJPEG(raw); img != nil {
					pipSetFrame(pipSrcMonitor, img)
				}
			}
			time.Sleep(30 * time.Millisecond) // ~24 fps ceiling, like the sidecar preview
			continue
		}
		resp.Body.Close()
		time.Sleep(60 * time.Millisecond) // nothing newer yet
	}
}

// ── Stats chip under the bubble (bubble thread) ─────────────────────────────

var (
	bClsChip   = syscall.StringToUTF16Ptr("PresentiaBubbleChipCls")
	bTitleChip = syscall.StringToUTF16Ptr("")

	gChipOnce sync.Once
	gChipProc uintptr
	gChipHwnd uintptr
	gChipBmp  uintptr
	gChipPx   []byte
	gChipCov  []float32
	gChipSh   []float32
	gChipW    int32
	gChipH    int32
	gChipSS   ssBuf
	gChipFont uintptr
	gChipFPx  int
)

const chipMargin = 6 // shadow room around the pill, logical px

func chipPaint(i int, a float32, c rgb) {
	old := gChipCov[i]
	n := a + old*(1-a)
	if n <= 0 {
		return
	}
	f, g := a/n, old*(1-a)/n
	p := i * 4
	gChipPx[p] = byte(f*float32(c.b) + g*float32(gChipPx[p]))
	gChipPx[p+1] = byte(f*float32(c.g) + g*float32(gChipPx[p+1]))
	gChipPx[p+2] = byte(f*float32(c.r) + g*float32(gChipPx[p+2]))
	gChipCov[i] = n
}

// chipRoundRect paints an anti-aliased rounded rect (optional 1px border and
// soft shadow) into the chip buffers.
func chipRoundRect(x0, y0, x1, y1, r float64, fill rgb, border *rgb, shadow bool, s float64) {
	pad := 1.0
	if shadow {
		pad = 8 * s
	}
	xa, xb := span((x0+x1)/2, (x1-x0)/2+pad, gChipW)
	ya, yb := span((y0+y1)/2, (y1-y0)/2+pad, gChipH)
	for y := ya; y < yb; y++ {
		for x := xa; x < xb; x++ {
			i := y*int(gChipW) + x
			px, py := float64(x)+0.5, float64(y)+0.5
			if shadow {
				ds := rrSDF(px, py-1.5*s, x0, y0, x1, y1, r)
				if a := float32(0.22 * (1 - smoothstep(-2*s, 6*s, ds))); a > gChipSh[i] {
					gChipSh[i] = a
				}
			}
			d := rrSDF(px, py, x0, y0, x1, y1, r)
			if cov := clamp01(0.5 - d); cov > 0 {
				c := fill
				if border != nil {
					c = mix(fill, *border, clamp01(d+math.Max(1, s)+0.5))
				}
				chipPaint(i, float32(cov), c)
			}
		}
	}
}

func chipFont(s float64) uintptr {
	px := int(math.Round(12 * s * ssK))
	if gChipFont == 0 || gChipFPx != px {
		if gChipFont != 0 {
			bDeleteObject.Call(gChipFont)
		}
		gChipFont, gChipFPx = makeFont(px, 500, labelFace()), px
	}
	return gChipFont
}

func chipText(t string, font uintptr, x0, y0, x1, y1 float64, c rgb) {
	ssDraw(&gChipSS, t, font, x0, y0, x1, y1, dtLeft|bDtVCenter|bDtSingleLine|bDtNoPrefix,
		gChipW, gChipH, func(x, y int32, a float64) {
			chipPaint(int(y)*int(gChipW)+int(x), float32(a), c)
		})
}

// The chip starts in the top-left corner of the screen and can be dragged
// anywhere; where it was left is remembered across launches. A click (no
// drag) opens the Live View.
var (
	gChipPos     bPOINT
	gChipPlaced  bool
	gChipLoaded  bool
	gChipDrag    bool
	gChipMoved   bool
	gChipGrab    bPOINT
	gChipGrabWin bPOINT
)

func chipPosFile() string {
	dir, err := os.UserConfigDir()
	if err != nil {
		return ""
	}
	return filepath.Join(dir, "Presentia", "stats-chip.json")
}

func loadChipPos() {
	if gChipLoaded {
		return
	}
	gChipLoaded = true
	if f := chipPosFile(); f != "" {
		if b, err := os.ReadFile(f); err == nil {
			var p struct{ X, Y int32 }
			if json.Unmarshal(b, &p) == nil {
				gChipPos, gChipPlaced = bPOINT{p.X, p.Y}, true
			}
		}
	}
}

func saveChipPos() {
	if f := chipPosFile(); f != "" {
		_ = os.MkdirAll(filepath.Dir(f), 0o755)
		b, _ := json.Marshal(struct{ X, Y int32 }{gChipPos.X, gChipPos.Y})
		_ = os.WriteFile(f, b, 0o644)
	}
}

func chipWndProc(hwnd, msg, wParam, lParam uintptr) uintptr {
	switch uint32(msg) {
	case bWmPaint:
		var ps bPAINTSTRUCT
		bBeginPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
		bEndPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
		return 0
	case bWmNcHitTest:
		return bHtClient
	case bWmLButtonDown:
		var rc bRECT
		bGetCursorPos.Call(uintptr(unsafe.Pointer(&gChipGrab)))
		bGetWindowRect.Call(hwnd, uintptr(unsafe.Pointer(&rc)))
		gChipGrabWin = bPOINT{rc.Left, rc.Top}
		gChipDrag, gChipMoved = true, false
		bSetCapture.Call(hwnd)
		return 0
	case bWmMouseMove:
		if !gChipDrag {
			return 0
		}
		var pt bPOINT
		bGetCursorPos.Call(uintptr(unsafe.Pointer(&pt)))
		dx, dy := pt.X-gChipGrab.X, pt.Y-gChipGrab.Y
		if !gChipMoved && (abs32(dx) > bDragSlop || abs32(dy) > bDragSlop) {
			gChipMoved = true
		}
		if gChipMoved {
			gChipPos, gChipPlaced = bPOINT{gChipGrabWin.X + dx, gChipGrabWin.Y + dy}, true
			p := chipPos()
			bSetWindowPos.Call(hwnd, 0, uintptr(uint32(p.X)), uintptr(uint32(p.Y)), 0, 0,
				bSwpNoSize|bSwpNoZOrder|bSwpNoActivate)
		}
		return 0
	case bWmLButtonUp:
		if !gChipDrag {
			return 0
		}
		gChipDrag = false
		bReleaseCapture.Call()
		if gChipMoved {
			saveChipPos()
		} else {
			toggleLiveView() // a click on the stats opens the Live View
		}
		return 0
	case bWmRButtonDown:
		return 0
	}
	r, _, _ := bDefWindowProcW.Call(hwnd, msg, wParam, lParam)
	return r
}

// updateChip shows, refreshes or hides the stats chip. Bubble thread only.
func updateChip() {
	st := liveSnapshot()
	if !st.Active || gBubbleHwnd == 0 {
		if gChipHwnd != 0 {
			bShowWindow.Call(gChipHwnd, 0) // SW_HIDE
		}
		return
	}
	s := uiScale()
	font := chipFont(s)

	// Layout: [● 12:31] | 2/3 present · 1 missing · 1 unknown
	elapsed := fmtElapsed(st.Elapsed)
	segs := liveSegs(st)
	padX, gap, dotD := 11*s, 9*s, 7*s
	pillH := 26 * s
	w := padX + dotD + 5*s + textWidth(font, elapsed)
	for _, sg := range segs {
		w += gap + textWidth(font, sg.text)
	}
	w += padX
	m := chipMargin * s
	cw, ch := int32(math.Ceil(w+2*m)), int32(math.Ceil(pillH+2*m))

	if gChipBmp == 0 || cw != gChipW || ch != gChipH {
		if gChipBmp != 0 {
			bDeleteObject.Call(gChipBmp)
		}
		gChipBmp, gChipPx = newDIB(cw, ch)
		if gChipBmp == 0 {
			return
		}
		gChipW, gChipH = cw, ch
		gChipCov = make([]float32, cw*ch)
		gChipSh = make([]float32, cw*ch)
	}
	clear(gChipPx)
	clear(gChipCov)
	clear(gChipSh)

	bg, border, ink := labelColours()
	accent := rgb{0x42, 0xD4, 0xF9}
	if gIridescent {
		accent = rgb{0xC8, 0x88, 0xF9}
	} else if !gDarkTheme {
		accent = rgb{0x0A, 0x78, 0x98}
	}
	x0, y0 := m, m
	x1, y1 := x0+w, y0+pillH
	chipRoundRect(x0, y0, x1, y1, pillH/2, bg, &border, true, s)

	cy := (y0 + y1) / 2
	x := x0 + padX
	chipRoundRect(x, cy-dotD/2, x+dotD, cy+dotD/2, dotD/2, accent, nil, false, s)
	x += dotD + 5*s
	ew := textWidth(font, elapsed)
	chipText(elapsed, font, x, y0, x+ew+2*s, y1+0.5*s, ink)
	x += ew
	for i, sg := range segs {
		if i == 0 {
			// thin divider between the clock and the counts
			chipRoundRect(x+gap/2-0.5*s, cy-6*s, x+gap/2+0.5*s, cy+6*s, 0.5*s, border, nil, false, s)
		} else {
			chipRoundRect(x+gap/2-s, cy-s, x+gap/2+s, cy+s, s, segColour(segMuted), nil, false, s)
		}
		x += gap
		tw := textWidth(font, sg.text)
		chipText(sg.text, font, x, y0, x+tw+2*s, y1+0.5*s, segColour(sg.role))
		x += tw
	}

	// Fold coverage + shadow into premultiplied alpha.
	for i, c := range gChipCov {
		a := c + gChipSh[i]*(1-c)
		p := i * 4
		gChipPx[p] = byte(float32(gChipPx[p]) * c)
		gChipPx[p+1] = byte(float32(gChipPx[p+1]) * c)
		gChipPx[p+2] = byte(float32(gChipPx[p+2]) * c)
		gChipPx[p+3] = byte(a*255 + 0.5)
	}

	if gChipHwnd == 0 {
		hInst, _, _ := bGetModuleHandleW.Call(0)
		gChipOnce.Do(func() {
			gChipProc = syscall.NewCallback(chipWndProc)
			hand, _, _ := bLoadCursorW.Call(0, 32649) // IDC_HAND
			wc := bWNDCLASSEX{
				CbSize:        uint32(unsafe.Sizeof(bWNDCLASSEX{})),
				LpfnWndProc:   gChipProc,
				HInstance:     hInst,
				LpszClassName: bClsChip,
				HCursor:       hand,
			}
			bRegisterClassExW.Call(uintptr(unsafe.Pointer(&wc)))
		})
		h, _, _ := bCreateWindowExW.Call(
			bWsExTopmost|bWsExToolWindow|bWsExLayered|bWsExNoActivate,
			uintptr(unsafe.Pointer(bClsChip)), uintptr(unsafe.Pointer(bTitleChip)),
			bWsPopup, 0, 0, uintptr(cw), uintptr(ch), 0, 0, hInst, 0,
		)
		if h == 0 {
			return
		}
		gChipHwnd = h
		excludeFromCapture(h)
	}
	pos := chipPos()
	pushLayered(gChipHwnd, gChipBmp, gChipW, gChipH, &pos)
	bShowWindow.Call(gChipHwnd, 4) // SW_SHOWNOACTIVATE
}

// chipPos is where the chip goes: the top-left corner of the work area by
// default, or wherever the user dragged it — kept on the desktop so a
// changed monitor layout can never strand it off-screen.
func chipPos() bPOINT {
	loadChipPos()
	if !gChipPlaced {
		var wa bRECT
		bSystemParametersInfo.Call(bSpiGetWorkArea, 0, uintptr(unsafe.Pointer(&wa)), 0)
		m := int32(math.Round((16 - chipMargin) * uiScale()))
		return bPOINT{wa.Left + m, wa.Top + m}
	}
	vx, _, _ := bGetSystemMetrics.Call(76) // SM_XVIRTUALSCREEN
	vy, _, _ := bGetSystemMetrics.Call(77)
	vw, _, _ := bGetSystemMetrics.Call(78) // SM_CXVIRTUALSCREEN
	vh, _, _ := bGetSystemMetrics.Call(79)
	left, top := int32(vx), int32(vy)
	right, bottom := left+int32(vw), top+int32(vh)
	x := max(left, min(gChipPos.X, right-gChipW))
	y := max(top, min(gChipPos.Y, bottom-gChipH))
	return bPOINT{x, y}
}

// destroyChip removes the chip. Bubble thread only.
func destroyChip() {
	if gChipHwnd != 0 {
		bDestroyWindow.Call(gChipHwnd)
		gChipHwnd = 0
	}
	if gChipBmp != 0 {
		bDeleteObject.Call(gChipBmp)
		gChipBmp, gChipPx, gChipCov, gChipSh = 0, nil, nil, nil
	}
}
