//go:build windows

package main

import (
	"bytes"
	"encoding/base64"
	"image"
	"image/draw"
	"image/jpeg"
	"math"
	"runtime"
	"sync"
	"syscall"
	"unsafe"

	wailsruntime "github.com/wailsapp/wails/v2/pkg/runtime"
)

// ── Pop-out Live Face viewer ─────────────────────────────────────────────────
//
// The Live Face Viewport's picture-in-picture used to be a panel inside the
// webview, so it could never leave the app window. This is a real top-level
// Win32 window instead: always on top, draggable anywhere on any monitor, and
// it stays up when Presentia is minimised or behind other apps.
//
// The frontend owns the camera socket, so it forwards each JPEG frame and the
// status text here (PipFrame / PipStatus). Frames are decoded on the Wails
// goroutine; the window's own thread only scales and paints the latest one.
// Clicking the return button (or PipClose) sends it back into its card.
//
// It resizes freely from any edge or corner (down to pipMinW x pipMinH); the
// video area takes whatever height is left between the header and status row.

const (
	pipWmFrame = 0x8000 + 10 // WM_APP+10: new frame or status, repaint

	pipW       = 320 // logical px
	pipHeaderH = 40
	pipPad     = 8
	pipVideoH  = 228 // initial height: 4:3 inside the 304px body
	pipStatusH = 28
	pipBtn     = 26
	pipGrip    = 6 // resize border thickness, logical px

	pipMinW = 220
	pipMinH = 190

	bWsThickFrame = 0x00040000
)

var (
	bClsPip   = syscall.StringToUTF16Ptr("PresentiaPipCls")
	bTitlePip = syscall.StringToUTF16Ptr("Presentia Live Face")

	bInvalidateRect = bUser32.NewProc("InvalidateRect")
	bBitBlt         = bGdi32.NewProc("BitBlt")
	bDwmapi         = syscall.NewLazyDLL("dwmapi.dll")
	bDwmSetWinAttr  = bDwmapi.NewProc("DwmSetWindowAttribute")

	gPipMu        sync.Mutex
	gPipHwnd      uintptr
	gPipApp       *App
	gPipFrame     *image.RGBA // latest decoded frame (nil = idle)
	gPipTitle     = "Live Face"
	gPipBadge     = "Idle"
	gPipCount     = "0 / 5"
	gPipMsg       = ""
	gPipIdle      = "Start webcam capture or import photos"
	gPipClassOnce sync.Once
	gPipProc      uintptr

	// What feeds the window. pipSrcPush: the frontend (Live Face on the
	// Register page). pipSrcMonitor: the Go live poller (Meet monitoring,
	// opened from the bubble or tray); frontend pushes are ignored then.
	gPipSource int
	gPipSegs   []pipSeg // monitor mode: coloured stats in the status row
	gPipInitW  float64  = pipW
	gPipInitVH float64  = pipVideoH

	// Pop-out thread only.
	gPipSS       ssBuf
	gPipBmp      uintptr
	gPipPx       []byte
	gPipW, gPipH int32
	gPipS        float64
	gPipHover    bool
	gPipTracking bool
	gPipFonts    = map[[2]int]uintptr{}
)

const (
	pipSrcPush = iota
	pipSrcMonitor
)

// pipSeg is one coloured stat in the status row ("2/3 present").
type pipSeg struct {
	text string
	role int // segPresent, segMissing, segUnknown, segMuted
}

const (
	segMuted = iota
	segPresent
	segMissing
	segUnknown
)

// segColour resolves a stat colour for the current design.
func segColour(role int) rgb {
	switch {
	case gIridescent:
		return [...]rgb{{0xA3, 0x9C, 0xB5}, {0xA8, 0xEE, 0xCF}, {0xF7, 0xB2, 0xC4}, {0xEF, 0xEE, 0xC6}}[role]
	case gDarkTheme:
		return [...]rgb{{0x93, 0xA1, 0xB8}, {0x10, 0xB9, 0x81}, {0xEF, 0x44, 0x44}, {0xF5, 0x9E, 0x0B}}[role]
	}
	return [...]rgb{{0x5A, 0x67, 0x79}, {0x05, 0x96, 0x69}, {0xDC, 0x26, 0x26}, {0xB4, 0x53, 0x09}}[role]
}

// pipPalette mirrors the app's card tokens for each design.
type pipPalette struct {
	bg, border, ink, muted, mutedLo, idle, pillBg, pillBorder, accent, statusBg, hover rgb
}

func pipColours() pipPalette {
	switch {
	case gIridescent:
		return pipPalette{rgb{0x1E, 0x1A, 0x28}, rgb{0x2A, 0x25, 0x36}, rgb{0xFF, 0xFF, 0xFF}, rgb{0xA3, 0x9C, 0xB5},
			rgb{0x66, 0x5F, 0x7A}, rgb{0x14, 0x11, 0x1B}, rgb{0x1D, 0x19, 0x26}, rgb{0x33, 0x2D, 0x42},
			rgb{0xD6, 0xA8, 0xFB}, rgb{0x1D, 0x19, 0x26}, rgb{0x26, 0x21, 0x2F}}
	case gDarkTheme:
		return pipPalette{rgb{0x1C, 0x28, 0x46}, rgb{0x26, 0x32, 0x4A}, rgb{0xFF, 0xFF, 0xFF}, rgb{0x93, 0xA1, 0xB8},
			rgb{0x5A, 0x6A, 0x84}, rgb{0x12, 0x1B, 0x31}, rgb{0x1A, 0x24, 0x40}, rgb{0x2E, 0x3B, 0x56},
			rgb{0x42, 0xD4, 0xF9}, rgb{0x1A, 0x24, 0x40}, rgb{0x22, 0x30, 0x4F}}
	}
	return pipPalette{rgb{0xFF, 0xFF, 0xFF}, rgb{0xE2, 0xE8, 0xF0}, rgb{0x0F, 0x17, 0x2A}, rgb{0x5A, 0x67, 0x79},
		rgb{0x94, 0xA3, 0xB8}, rgb{0xF1, 0xF5, 0xF9}, rgb{0xF1, 0xF5, 0xF9}, rgb{0xDD, 0xE4, 0xED},
		rgb{0x0A, 0x78, 0x98}, rgb{0xF5, 0xF7, 0xFA}, rgb{0xEB, 0xEF, 0xF5}}
}

// ── Public entry points (Wails thread) ───────────────────────────────────────

// openPipNative shows the pop-out viewer. Returns false if it could not be
// created, so the frontend can fall back to its in-app panel.
func openPipNative(a *App, title string) bool {
	pipUseSource(pipSrcPush, title, pipW, pipVideoH)
	return pipOpen(a, title)
}

// pipOpen creates the window (or retitles it if it is already up).
func pipOpen(a *App, title string) bool {
	gPipMu.Lock()
	if title != "" {
		gPipTitle = title
	}
	if gPipHwnd != 0 {
		gPipMu.Unlock()
		pipPost()
		return true
	}
	gPipMu.Unlock()
	gPipApp = a

	ready := make(chan bool, 1)
	go func() {
		runtime.LockOSThread()
		defer runtime.UnlockOSThread()
		defer func() {
			if r := recover(); r != nil {
				gPipMu.Lock()
				gPipHwnd = 0
				gPipMu.Unlock()
				select {
				case ready <- false:
				default:
				}
				pipEmit("pip:closed")
			}
		}()

		hInst, _, _ := bGetModuleHandleW.Call(0)
		gPipClassOnce.Do(func() {
			gPipProc = syscall.NewCallback(pipWndProc)
			arrow, _, _ := bLoadCursorW.Call(0, 32512)
			wc := bWNDCLASSEX{
				CbSize:        uint32(unsafe.Sizeof(bWNDCLASSEX{})),
				Style:         0x00020000, // CS_DROPSHADOW
				LpfnWndProc:   gPipProc,
				HInstance:     hInst,
				LpszClassName: bClsPip,
				HCursor:       arrow,
			}
			bRegisterClassExW.Call(uintptr(unsafe.Pointer(&wc)))
		})

		s := uiScale()
		gPipS = s
		gPipW = int32(math.Round(gPipInitW * s))
		gPipH = int32(math.Round((pipHeaderH + pipPad + gPipInitVH + pipPad + pipStatusH + pipPad) * s))

		// Bottom-right of the work area, clear of the taskbar.
		var wa bRECT
		bSystemParametersInfo.Call(bSpiGetWorkArea, 0, uintptr(unsafe.Pointer(&wa)), 0)
		x := wa.Right - gPipW - int32(24*s)
		y := wa.Bottom - gPipH - int32(24*s)

		hwnd, _, _ := bCreateWindowExW.Call(
			bWsExTopmost|bWsExToolWindow|bWsExNoActivate,
			uintptr(unsafe.Pointer(bClsPip)),
			uintptr(unsafe.Pointer(bTitlePip)),
			bWsPopup|bWsThickFrame, // thick frame = resizable; WM_NCCALCSIZE hides it
			uintptr(x), uintptr(y), uintptr(gPipW), uintptr(gPipH),
			0, 0, hInst, 0,
		)
		if hwnd == 0 {
			ready <- false
			return
		}
		// Windows 11: native rounded corners (ignored on Windows 10).
		if bDwmSetWinAttr.Find() == nil {
			pref := uint32(2) // DWMWCP_ROUND
			bDwmSetWinAttr.Call(hwnd, 33, uintptr(unsafe.Pointer(&pref)), 4)
		}
		pipResizeBuffer(gPipW, gPipH)

		gPipMu.Lock()
		gPipHwnd = hwnd
		excludeFromCapture(hwnd)
		gPipMu.Unlock()
		bShowWindow.Call(hwnd, 4) // SW_SHOWNOACTIVATE
		ready <- true

		var msg bMSG
		for {
			r, _, _ := bGetMessageW.Call(uintptr(unsafe.Pointer(&msg)), 0, 0, 0)
			if r == 0 || r == ^uintptr(0) {
				break
			}
			bTranslateMessage.Call(uintptr(unsafe.Pointer(&msg)))
			bDispatchMessageW.Call(uintptr(unsafe.Pointer(&msg)))
		}

		gPipMu.Lock()
		gPipHwnd = 0
		gPipMu.Unlock()
		if gPipBmp != 0 {
			bDeleteObject.Call(gPipBmp)
			gPipBmp, gPipPx = 0, nil
		}
	}()
	return <-ready
}

// closePipNative removes the viewer. Safe to call when it is not open.
func closePipNative() {
	gPipMu.Lock()
	h := gPipHwnd
	gPipMu.Unlock()
	if h != 0 {
		bPostMessageW.Call(h, bWmClose, 0, 0)
	}
}

// decodeJPEG turns JPEG bytes into an RGBA image, or nil.
func decodeJPEG(raw []byte) *image.RGBA {
	src, err := jpeg.Decode(bytes.NewReader(raw))
	if err != nil {
		return nil
	}
	b := src.Bounds()
	img := image.NewRGBA(image.Rect(0, 0, b.Dx(), b.Dy()))
	draw.Draw(img, img.Bounds(), src, b.Min, draw.Src)
	return img
}

// pipSetFrame shows img (nil = idle) if the window is fed by `source`.
func pipSetFrame(source int, img *image.RGBA) {
	gPipMu.Lock()
	if gPipSource != source {
		gPipMu.Unlock()
		return
	}
	open := gPipHwnd != 0
	gPipFrame = img
	gPipMu.Unlock()
	if open {
		pipPost()
	}
}

// pipFrameNative takes one base64 JPEG frame from the frontend; "" = idle.
func pipFrameNative(b64 string) {
	var img *image.RGBA
	if b64 != "" {
		raw, err := base64.StdEncoding.DecodeString(b64)
		if err != nil {
			return
		}
		if img = decodeJPEG(raw); img == nil {
			return
		}
	}
	pipSetFrame(pipSrcPush, img)
}

// pipSetStatus updates the badge and status row if the window is fed by `source`.
func pipSetStatus(source int, badge, count, msg, idleText string, segs []pipSeg) {
	gPipMu.Lock()
	if gPipSource != source {
		gPipMu.Unlock()
		return
	}
	gPipBadge, gPipCount, gPipMsg, gPipSegs = badge, count, msg, segs
	if idleText != "" {
		gPipIdle = idleText
	}
	gPipMu.Unlock()
	pipPost()
}

// pipStatusNative updates the header badge and the status row (frontend).
func pipStatusNative(badge, count, msg, idleText string) {
	pipSetStatus(pipSrcPush, badge, count, msg, idleText, nil)
}

// pipIsOpen reports whether the window is up and which source feeds it.
func pipIsOpen() (bool, int) {
	gPipMu.Lock()
	defer gPipMu.Unlock()
	return gPipHwnd != 0, gPipSource
}

// pipUseSource switches what feeds the window (and its initial size, used
// the next time it is created) and clears the previous content.
func pipUseSource(source int, title string, initW, initVH float64) {
	gPipMu.Lock()
	if gPipSource != source {
		gPipFrame, gPipSegs, gPipCount, gPipMsg = nil, nil, "", ""
	}
	gPipSource = source
	gPipTitle = title
	gPipInitW, gPipInitVH = initW, initVH
	gPipMu.Unlock()
}

// pipPost asks the viewer to repaint (theme, style, frame or status change).
func pipPost() {
	gPipMu.Lock()
	h := gPipHwnd
	gPipMu.Unlock()
	if h != 0 {
		bPostMessageW.Call(h, pipWmFrame, 0, 0)
	}
}

func pipEmit(name string) {
	if gPipApp != nil && gPipApp.ctx != nil {
		wailsruntime.EventsEmit(gPipApp.ctx, name)
	}
}

// ── Layout ───────────────────────────────────────────────────────────────────

// pipResizeBuffer (re)creates the back buffer for a new window size.
func pipResizeBuffer(w, h int32) {
	if w <= 0 || h <= 0 {
		return
	}
	if gPipBmp != 0 && w == gPipW && h == gPipH && gPipPx != nil {
		return
	}
	if gPipBmp != 0 {
		bDeleteObject.Call(gPipBmp)
		gPipBmp, gPipPx = 0, nil
	}
	gPipW, gPipH = w, h
	gPipBmp, gPipPx = newDIB(w, h)
}

// pipEdgeHit maps a window-local point near the border to a sizing hit code.
func pipEdgeHit(x, y int32) uintptr {
	g := int32(math.Max(4, math.Round(pipGrip*gPipS)))
	c := g * 2 // corners are easier to grab
	left, right := x < g, x >= gPipW-g
	top, bottom := y < g, y >= gPipH-g
	switch {
	case (top && x < c) || (left && y < c):
		return 13 // HTTOPLEFT
	case (top && x >= gPipW-c) || (right && y < c):
		return 14 // HTTOPRIGHT
	case (bottom && x < c) || (left && y >= gPipH-c):
		return 16 // HTBOTTOMLEFT
	case (bottom && x >= gPipW-c) || (right && y >= gPipH-c):
		return 17 // HTBOTTOMRIGHT
	case left:
		return 10 // HTLEFT
	case right:
		return 11 // HTRIGHT
	case top:
		return 12 // HTTOP
	case bottom:
		return 15 // HTBOTTOM
	}
	return 0
}

func pipBtnRect() (x0, y0, x1, y1 float64) {
	s := gPipS
	x1 = float64(gPipW) - 8*s
	x0 = x1 - pipBtn*s
	y0 = (pipHeaderH - pipBtn) / 2 * s
	return x0, y0, x1, y0 + pipBtn*s
}

func pipOnBtn(x, y int32) bool {
	x0, y0, x1, y1 := pipBtnRect()
	fx, fy := float64(x)+0.5, float64(y)+0.5
	return fx >= x0 && fx < x1 && fy >= y0 && fy < y1
}

// ── Painting (pop-out thread) ────────────────────────────────────────────────

func pipBlend(i int, a float64, c rgb) {
	if a <= 0 {
		return
	}
	if a > 1 {
		a = 1
	}
	p := i * 4
	gPipPx[p] = byte(float64(gPipPx[p])*(1-a) + float64(c.b)*a + 0.5)
	gPipPx[p+1] = byte(float64(gPipPx[p+1])*(1-a) + float64(c.g)*a + 0.5)
	gPipPx[p+2] = byte(float64(gPipPx[p+2])*(1-a) + float64(c.r)*a + 0.5)
	gPipPx[p+3] = 255
}

// rrSDF is the signed distance from (px,py) to a rounded rectangle.
func rrSDF(px, py, x0, y0, x1, y1, r float64) float64 {
	cx, cy := (x0+x1)/2, (y0+y1)/2
	qx := math.Abs(px-cx) - ((x1-x0)/2 - r)
	qy := math.Abs(py-cy) - ((y1-y0)/2 - r)
	return math.Hypot(math.Max(qx, 0), math.Max(qy, 0)) + math.Min(math.Max(qx, qy), 0) - r
}

// pipRoundRect fills a rounded rectangle, with an optional 1px border.
func pipRoundRect(x0, y0, x1, y1, r float64, fill rgb, border *rgb) {
	bw := math.Max(1, gPipS)
	xa, xb := span((x0+x1)/2, (x1-x0)/2+1, gPipW)
	ya, yb := span((y0+y1)/2, (y1-y0)/2+1, gPipH)
	for y := ya; y < yb; y++ {
		for x := xa; x < xb; x++ {
			d := rrSDF(float64(x)+0.5, float64(y)+0.5, x0, y0, x1, y1, r)
			cov := clamp01(0.5 - d)
			if cov <= 0 {
				continue
			}
			c := fill
			if border != nil {
				c = mix(fill, *border, clamp01(d+bw+0.5))
			}
			pipBlend(y*int(gPipW)+x, cov, c)
		}
	}
}

func pipFont(px float64, face *uint16, icon bool) uintptr {
	n := int(math.Round(px * ssK))
	k := [2]int{n, 0}
	if icon {
		k[1] = 1
	}
	if f, ok := gPipFonts[k]; ok {
		return f
	}
	weight := uintptr(500)
	if icon {
		weight = 400
	}
	f := makeFont(n, weight, face)
	gPipFonts[k] = f
	return f
}

func pipText(s string, font uintptr, x0, y0, x1, y1 float64, flags uintptr, c rgb) {
	ssDraw(&gPipSS, s, font, x0, y0, x1, y1, flags, gPipW, gPipH, func(x, y int32, a float64) {
		pipBlend(int(y)*int(gPipW)+int(x), a, c)
	})
}

const (
	dtLeft     = 0x0000
	dtEllipsis = 0x8000 // DT_END_ELLIPSIS
)

// pipDrawFrame scales img into the video box (contain), clipped to its
// rounded corners, with bilinear sampling.
func pipDrawFrame(img *image.RGBA, x0, y0, x1, y1, r float64) {
	iw, ih := float64(img.Rect.Dx()), float64(img.Rect.Dy())
	if iw < 2 || ih < 2 {
		return
	}
	bw, bh := x1-x0, y1-y0
	k := math.Min(bw/iw, bh/ih)
	dw, dh := iw*k, ih*k
	dx0, dy0 := x0+(bw-dw)/2, y0+(bh-dh)/2
	xa, xb := int(math.Floor(dx0)), int(math.Ceil(dx0+dw))
	ya, yb := int(math.Floor(dy0)), int(math.Ceil(dy0+dh))
	stride := img.Stride
	pix := img.Pix
	for y := max(ya, 0); y < min(yb, int(gPipH)); y++ {
		sy := (float64(y)+0.5-dy0)/k - 0.5
		sy = math.Max(0, math.Min(sy, ih-1.001))
		iy := int(sy)
		fy := sy - float64(iy)
		for x := max(xa, 0); x < min(xb, int(gPipW)); x++ {
			cov := clamp01(0.5 - rrSDF(float64(x)+0.5, float64(y)+0.5, x0, y0, x1, y1, r))
			if cov <= 0 {
				continue
			}
			sx := (float64(x)+0.5-dx0)/k - 0.5
			sx = math.Max(0, math.Min(sx, iw-1.001))
			ix := int(sx)
			fx := sx - float64(ix)
			p00 := iy*stride + ix*4
			p10 := p00 + 4
			p01 := p00 + stride
			p11 := p01 + 4
			ch := func(o int) byte {
				top := float64(pix[p00+o])*(1-fx) + float64(pix[p10+o])*fx
				bot := float64(pix[p01+o])*(1-fx) + float64(pix[p11+o])*fx
				return byte(top*(1-fy) + bot*fy + 0.5)
			}
			pipBlend(y*int(gPipW)+x, cov, rgb{ch(0), ch(1), ch(2)})
		}
	}
}

func renderPip() {
	if gPipPx == nil {
		return
	}
	gPipMu.Lock()
	frame, title, badge, count, msg, idle := gPipFrame, gPipTitle, gPipBadge, gPipCount, gPipMsg, gPipIdle
	segs, source := gPipSegs, gPipSource
	gPipMu.Unlock()

	s := gPipS
	pal := pipColours()
	W := float64(gPipW)
	face := labelFace()

	// Card background and outer border.
	for i := 0; i < len(gPipPx); i += 4 {
		gPipPx[i], gPipPx[i+1], gPipPx[i+2], gPipPx[i+3] = pal.border.b, pal.border.g, pal.border.r, 255
	}
	bw := math.Max(1, math.Round(s))
	for y := int(bw); y < int(gPipH)-int(bw); y++ {
		for x := int(bw); x < int(gPipW)-int(bw); x++ {
			p := (y*int(gPipW) + x) * 4
			gPipPx[p], gPipPx[p+1], gPipPx[p+2] = pal.bg.b, pal.bg.g, pal.bg.r
		}
	}

	// Iridescent: spectrum hairline along the top, like the in-app panels.
	if gIridescent {
		stops := []rgb{{0x97, 0xDE, 0xF0}, {0xEF, 0xEE, 0xC6}, {0xC8, 0x88, 0xF9}, {0xCB, 0xB9, 0xF6}, {0xE5, 0xD5, 0xED}}
		xa, xb := int(14*s), int(W-14*s)
		for x := xa; x < xb; x++ {
			t := float64(x-xa) / float64(xb-xa) * float64(len(stops)-1)
			i := min(int(t), len(stops)-2)
			c := mix(stops[i], stops[i+1], t-float64(i))
			for y := 0; y < int(math.Max(2, 2*s)); y++ {
				pipBlend(y*int(gPipW)+x, 0.85, c)
			}
		}
	}

	// ── Header: grip, title, badge, return button ──
	hh := pipHeaderH * s
	for y := int(hh - bw); y < int(hh); y++ {
		for x := int(bw); x < int(gPipW)-int(bw); x++ {
			pipBlend(y*int(gPipW)+x, 1, pal.border)
		}
	}
	gx, gy := 12*s, hh/2-4*s
	for row := 0; row < 3; row++ {
		for col := 0; col < 2; col++ {
			cx, cy := gx+float64(col)*4*s, gy+float64(row)*4*s
			pipRoundRect(cx-1.2*s, cy-1.2*s, cx+1.2*s, cy+1.2*s, 1.2*s, pal.mutedLo, nil)
		}
	}
	titleFont := pipFont(12.5*s, face, false)
	tx := 24 * s
	tw := textWidth(titleFont, title)
	pipText(title, titleFont, tx, 0, tx+tw+2*s, hh+s, dtLeft|bDtVCenter|bDtSingleLine|bDtNoPrefix, pal.ink)
	if badge != "" {
		bf := pipFont(10.5*s, face, false)
		bwid := textWidth(bf, badge) + 14*s
		bx0, by0 := tx+tw+8*s, hh/2-9*s
		pipRoundRect(bx0, by0, bx0+bwid, by0+18*s, 9*s, pal.pillBg, &pal.pillBorder)
		pipText(badge, bf, bx0, by0+0.5*s, bx0+bwid, by0+18*s+0.5*s, bDtCenter|bDtVCenter|bDtSingleLine|bDtNoPrefix, pal.muted)
	}
	bx0, by0, bx1, by1 := pipBtnRect()
	btnInk := pal.muted
	if gPipHover {
		pipRoundRect(bx0, by0, bx1, by1, 6*s, pal.hover, nil)
		btnInk = pal.ink
	}
	btnGlyph := rune(0xE73F) // BackToWindow: return to the card
	if source == pipSrcMonitor {
		btnGlyph = 0xE711 // Cancel: close the live view
	}
	pipText(string(btnGlyph), pipFont(13*s, iconFace(), true), bx0, by0, bx1, by1,
		bDtCenter|bDtVCenter|bDtSingleLine|bDtNoPrefix, btnInk)

	// ── Video ──
	vx0, vy0 := pipPad*s, hh+pipPad*s
	// The video takes all the height the header and status row leave.
	vx1 := W - pipPad*s
	vy1 := math.Max(vy0+40*s, float64(gPipH)-pipPad*s-pipStatusH*s-pipPad*s)
	pipRoundRect(vx0, vy0, vx1, vy1, 10*s, pal.idle, &pal.border)
	if frame != nil {
		pipDrawFrame(frame, vx0, vy0, vx1, vy1, 10*s)
	} else {
		cy := (vy0 + vy1) / 2
		pipText(string(rune(0xE714)), pipFont(30*s, iconFace(), true), vx0, cy-44*s, vx1, cy, // Video
			bDtCenter|bDtVCenter|bDtSingleLine|bDtNoPrefix, pal.mutedLo)
		pipText(idle, pipFont(12*s, face, false), vx0+12*s, cy+2*s, vx1-12*s, cy+24*s,
			bDtCenter|bDtVCenter|bDtSingleLine|bDtNoPrefix|dtEllipsis, pal.muted)
	}

	// ── Status row: "n / 5" + prompt ──
	sy0 := vy1 + pipPad*s
	sy1 := sy0 + pipStatusH*s
	pipRoundRect(vx0, sy0, vx1, sy1, 8*s, pal.statusBg, nil)
	sf := pipFont(11.5*s, face, false)
	tx0 := vx0 + 9*s
	if count != "" {
		cw := textWidth(sf, count)
		pipText(count, sf, tx0, sy0, tx0+cw+2*s, sy1+s, dtLeft|bDtVCenter|bDtSingleLine|bDtNoPrefix, pal.accent)
		tx0 += cw + 8*s
	}
	// Monitor mode: "2/3 present · 1 missing · 1 unknown", then the last alert.
	for i, sg := range segs {
		sw := textWidth(sf, sg.text)
		if tx0+sw > vx1-12*s {
			break
		}
		if i > 0 {
			pipRoundRect(tx0-4.5*s-s, (sy0+sy1)/2-s, tx0-4.5*s+s, (sy0+sy1)/2+s, s, pal.mutedLo, nil)
		}
		pipText(sg.text, sf, tx0, sy0, tx0+sw+2*s, sy1+s, dtLeft|bDtVCenter|bDtSingleLine|bDtNoPrefix, segColour(sg.role))
		tx0 += sw + 9*s
	}
	if len(segs) > 0 {
		tx0 += 3 * s
	}
	pipText(msg, sf, tx0, sy0, vx1-9*s-6*s, sy1+s,
		dtLeft|bDtVCenter|bDtSingleLine|bDtNoPrefix|dtEllipsis, pal.muted)

	// Resize grip in the bottom-right corner: three dots on a diagonal.
	for i := 0; i < 3; i++ {
		for j := 0; j <= i; j++ {
			cx := W - 5*s - float64(j)*3.5*s
			cy := float64(gPipH) - 5*s - float64(i-j)*3.5*s
			pipRoundRect(cx-s, cy-s, cx+s, cy+s, s, pal.mutedLo, nil)
		}
	}
}

// ── Window procedure ─────────────────────────────────────────────────────────

func pipWndProc(hwnd, msg, wParam, lParam uintptr) uintptr {
	switch uint32(msg) {
	case bWmPaint:
		var ps bPAINTSTRUCT
		hdc, _, _ := bBeginPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
		renderPip()
		if gPipBmp != 0 && hdc != 0 {
			mem, _, _ := bCreateCompatibleDC.Call(hdc)
			old, _, _ := bSelectObject.Call(mem, gPipBmp)
			bBitBlt.Call(hdc, 0, 0, uintptr(gPipW), uintptr(gPipH), mem, 0, 0, 0x00CC0020) // SRCCOPY
			bSelectObject.Call(mem, old)
			bDeleteDC.Call(mem)
		}
		bEndPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
		return 0

	case 0x0014: // WM_ERASEBKGND — everything is painted in WM_PAINT
		return 1

	case pipWmFrame:
		bInvalidateRect.Call(hwnd, 0, 0)
		return 0

	case 0x0083: // WM_NCCALCSIZE — the whole window is client area (no frame)
		if wParam != 0 {
			return 0
		}

	case 0x0024: // WM_GETMINMAXINFO — keep the header and status row usable
		mmi := (*[10]int32)(unsafe.Pointer(lParam)) // POINT x5; ptMinTrackSize is [6],[7]
		mmi[6] = int32(math.Round(pipMinW * uiScale()))
		mmi[7] = int32(math.Round(pipMinH * uiScale()))
		return 0

	case 0x0005: // WM_SIZE
		pipResizeBuffer(loWord(lParam), hiWord(lParam))
		bInvalidateRect.Call(hwnd, 0, 0)
		return 0

	case bWmNcHitTest:
		// Edges and corners resize, the header drags the window, and the
		// return button stays clickable.
		var rc bRECT
		bGetWindowRect.Call(hwnd, uintptr(unsafe.Pointer(&rc)))
		x, y := loWord(lParam)-rc.Left, hiWord(lParam)-rc.Top
		if h := pipEdgeHit(x, y); h != 0 {
			return h
		}
		if float64(y) < pipHeaderH*gPipS && !pipOnBtn(x, y) {
			return 2 // HTCAPTION
		}
		return bHtClient

	case 0x00A3: // WM_NCLBUTTONDBLCLK — no maximise on a double-click
		return 0

	case bWmMouseMove:
		if !gPipTracking {
			tme := bTRACKMOUSEEVENT{DwFlags: bTmeLeave, HwndTrack: hwnd}
			tme.CbSize = uint32(unsafe.Sizeof(tme))
			bTrackMouseEvent.Call(uintptr(unsafe.Pointer(&tme)))
			gPipTracking = true
		}
		if on := pipOnBtn(loWord(lParam), hiWord(lParam)); on != gPipHover {
			gPipHover = on
			bInvalidateRect.Call(hwnd, 0, 0)
		}
		return 0

	case bWmMouseLeave:
		gPipTracking = false
		if gPipHover {
			gPipHover = false
			bInvalidateRect.Call(hwnd, 0, 0)
		}
		return 0

	case bWmLButtonUp:
		if pipOnBtn(loWord(lParam), hiWord(lParam)) {
			bDestroyWindow.Call(hwnd)
		}
		return 0

	case bWmClose:
		bDestroyWindow.Call(hwnd)
		return 0

	case bWmDestroy:
		gPipHover, gPipTracking = false, false
		gPipMu.Lock()
		wasMonitor := gPipSource == pipSrcMonitor
		gPipMu.Unlock()
		if wasMonitor {
			liveViewClosed()
		}
		pipEmit("pip:closed")
		bPostQuitMessage.Call(0)
		return 0
	}
	r, _, _ := bDefWindowProcW.Call(hwnd, msg, wParam, lParam)
	return r
}
