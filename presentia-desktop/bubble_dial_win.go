//go:build windows

package main

import (
	"math"
	"os"
	"path/filepath"
	"syscall"
	"time"
	"unsafe"
)

// ── Pop-out dial ─────────────────────────────────────────────────────────────
//
// Clicking the bubble fans its controls out around it as round buttons, like a
// speed-dial. The dial is one transparent layered window laid over the bubble:
// fully transparent pixels let clicks through to whatever is underneath, so
// only the buttons (and the hovered button's label) catch the mouse.
//
// Each frame is drawn in three passes into a 32-bit DIB:
//  1. shapes (discs, label pills, shadows) are rasterised here in Go, with
//     anti-aliased edges, as opaque colour plus a separate coverage mask;
//  2. icon glyphs and label text are rendered supersampled (see
//     bubble_text_win.go) and painted into the same colour + coverage buffers;
//  3. the coverage mask is folded back in as premultiplied alpha.
//
// The buttons follow the app's design: in Light and Dark they are the app's
// own surfaces with cyan (#42D4F9) icons, the main action filled cyan like
// the app's primary buttons; in Iridescent they take the pastel spectrum.

type rgb struct{ r, g, b byte }

func (c rgb) colorref() uintptr { return uintptr(c.r) | uintptr(c.g)<<8 | uintptr(c.b)<<16 }

// dialRole picks a button's colours within the current design.
type dialRole int

const (
	roleNormal  dialRole = iota // surface disc, accent icon
	rolePrimary                 // accent-filled disc (the main action)
	roleDanger                  // surface disc, red icon
	roleMuted                   // quieter surface disc, muted icon
)

type bDialItem struct {
	label, cmd string
	glyph      rune // Segoe Fluent Icons / Segoe MDL2 Assets code point
	role       dialRole
	fillIri    rgb // iridescent design: pastel from the spectrum
}

var bDialItems = []bDialItem{
	{"Select Screen Area", "bubble:screen_area", 0xEF20, roleNormal, rgb{0x97, 0xDE, 0xF0}},
	{"Select Window", "bubble:win_picker", 0xE737, roleNormal, rgb{0xCB, 0xB9, 0xF6}},
	{"Start Live Monitor", "bubble:launch", 0xE768, rolePrimary, rgb{0xC8, 0x88, 0xF9}},
	{"Stop Monitor", "bubble:stop", 0xE71A, roleDanger, rgb{0xF7, 0xB2, 0xC4}},
	// Handled inside the bubble rather than by the frontend, so they work no
	// matter which page the app is on (or while it is hidden in the tray).
	{"Live View", "bubble:liveview", 0xE890, roleNormal, rgb{0x97, 0xDE, 0xF0}},
	{"Display on Top", "bubble:pin", 0xE718, roleNormal, rgb{0xE5, 0xD5, 0xED}},
	{"Quit Bubble", "bubble:quit", 0xE7E8, roleMuted, rgb{0xEF, 0xEE, 0xC6}},
}

// dialPalette mirrors one design's tokens from frontend/src/style.css.
type dialPalette struct {
	surface, surfaceHover, ring, ringHover    rgb // normal / danger buttons
	muted, mutedInk                           rgb // quit button
	accent, accentHover, accentRing, onAccent rgb
	accentInk, dangerInk                      rgb // icons on surfaces
	labelBg, labelBorder, labelInk            rgb
}

var (
	// Light: white cards, slate borders, cyan accent (#0A7898 for icons on white).
	palLight = dialPalette{
		surface: rgb{0xFF, 0xFF, 0xFF}, surfaceHover: rgb{0xE3, 0xF8, 0xFE},
		ring: rgb{0xDD, 0xE4, 0xED}, ringHover: rgb{0x42, 0xD4, 0xF9},
		muted: rgb{0xF1, 0xF5, 0xF9}, mutedInk: rgb{0x5A, 0x67, 0x79},
		accent: rgb{0x42, 0xD4, 0xF9}, accentHover: rgb{0x26, 0xC6, 0xEF},
		accentRing: rgb{0x0F, 0xA3, 0xCC}, onAccent: rgb{0x06, 0x25, 0x30},
		accentInk: rgb{0x0A, 0x78, 0x98}, dangerInk: rgb{0xDC, 0x26, 0x26},
		labelBg: rgb{0xFF, 0xFF, 0xFF}, labelBorder: rgb{0xE2, 0xE8, 0xF0}, labelInk: rgb{0x0F, 0x17, 0x2A},
	}
	// Dark: navy cards, cyan accent.
	palDark = dialPalette{
		surface: rgb{0x1C, 0x28, 0x46}, surfaceHover: rgb{0x22, 0x30, 0x4F},
		ring: rgb{0x2E, 0x3B, 0x56}, ringHover: rgb{0x42, 0xD4, 0xF9},
		muted: rgb{0x1A, 0x24, 0x40}, mutedInk: rgb{0x93, 0xA1, 0xB8},
		accent: rgb{0x42, 0xD4, 0xF9}, accentHover: rgb{0x6A, 0xDE, 0xFB},
		accentRing: rgb{0x14, 0xA9, 0xD3}, onAccent: rgb{0x06, 0x25, 0x30},
		accentInk: rgb{0x42, 0xD4, 0xF9}, dangerInk: rgb{0xEF, 0x44, 0x44},
		labelBg: rgb{0x16, 0x20, 0x3A}, labelBorder: rgb{0x26, 0x32, 0x4A}, labelInk: rgb{0xE8, 0xED, 0xF6},
	}
	// Iridescent: plum-black cards; buttons carry the pastel spectrum.
	iriLabelBg, iriLabelBorder, iriInk = rgb{0x1E, 0x1A, 0x28}, rgb{0x3A, 0x30, 0x50}, rgb{0x1B, 0x10, 0x30}
)

// itemText is the label and glyph, which for the pin toggle follow its state.
func itemText(it bDialItem) (string, rune) {
	if it.cmd == "bubble:pin" && gPinned {
		return "Hide from Top", 0xE77A
	}
	if it.cmd == "bubble:liveview" {
		if open, src := pipIsOpen(); open && src == pipSrcMonitor {
			return "Hide Live View", 0xE890
		}
	}
	return it.label, it.glyph
}

func mix(a, b rgb, t float64) rgb {
	l := func(x, y byte) byte { return byte(float64(x) + (float64(y)-float64(x))*t + 0.5) }
	return rgb{l(a.r, b.r), l(a.g, b.g), l(a.b, b.b)}
}

// itemColours gives a button's disc fill, 1px ring and icon colour.
func itemColours(it bDialItem, hover bool) (fill, ring, ink rgb) {
	if gIridescent {
		fill = it.fillIri
		if hover {
			fill = mix(fill, rgb{255, 255, 255}, 0.25)
		}
		return fill, mix(it.fillIri, rgb{255, 255, 255}, 0.45), iriInk
	}
	p := &palLight
	if gDarkTheme {
		p = &palDark
	}
	switch it.role {
	case rolePrimary:
		fill = p.accent
		if hover {
			fill = p.accentHover
		}
		return fill, p.accentRing, p.onAccent
	case roleMuted:
		fill, ring = p.muted, p.ring
		if hover {
			fill, ring = p.surfaceHover, p.ringHover
		}
		return fill, ring, p.mutedInk
	case roleDanger:
		fill, ring = p.surface, p.ring
		if hover {
			fill, ring = mix(p.surface, p.dangerInk, 0.12), p.dangerInk
		}
		return fill, ring, p.dangerInk
	}
	fill, ring = p.surface, p.ring
	if hover {
		fill, ring = p.surfaceHover, p.ringHover
	}
	return fill, ring, p.accentInk
}

// labelColours mirror the app's card surfaces for the current design.
func labelColours() (bg, border, ink rgb) {
	switch {
	case gIridescent:
		return iriLabelBg, iriLabelBorder, rgb{0xEC, 0xE8, 0xF4}
	case gDarkTheme:
		return palDark.labelBg, palDark.labelBorder, palDark.labelInk
	}
	return palLight.labelBg, palLight.labelBorder, palLight.labelInk
}

const (
	dialTimerID  = 1
	dialOpenDur  = 260 * time.Millisecond
	dialOpenLag  = 30 * time.Millisecond // stagger between buttons
	dialCloseDur = 150 * time.Millisecond
	dialCloseLag = 18 * time.Millisecond

	bSpiGetWorkArea = 0x0030
	bTmeLeave       = 0x00000002
	bDtCenter       = 0x00000001
	bDtVCenter      = 0x00000004
	bDtSingleLine   = 0x00000020
	bDtNoPrefix     = 0x00000800
)

type bTRACKMOUSEEVENT struct {
	CbSize      uint32
	DwFlags     uint32
	HwndTrack   uintptr
	DwHoverTime uint32
}

type dialSlot struct{ fx, fy, ux, uy float64 } // final centre and outward direction

var (
	gDialHwnd     uintptr
	gDialOpen     bool // the dial is out (or on its way out)
	gDialClosing  bool
	gDialT0       time.Time
	gDialHover    = -1
	gDialTracking bool

	// Layout, fixed when the dial opens. Coordinates are overlay-local px.
	gDialW, gDialH int32
	gDialOrigin    bPOINT
	gDialCX        float64 // bubble centre
	gDialCY        float64
	gDialS         float64 // DPI scale
	gDialRad       float64 // button radius
	gDialR         float64 // arc radius
	gDialSlots     []dialSlot
	gDialLabelRc   [4]float64 // hovered label pill, for hit-testing

	gDialBmp uintptr
	gDialPx  []byte
	gDialCov []float32
	gDialSh  []float32
)

// ── Fonts ────────────────────────────────────────────────────────────────────

var (
	bFaceSegoeUI = syscall.StringToUTF16Ptr("Segoe UI")
	bFaceIcons   *uint16 // resolved once: Fluent on Windows 11, MDL2 on 10

	gIconFonts = map[int]uintptr{}
	gLabelFont uintptr
	gLabelPx   int
)

func iconFace() *uint16 {
	if bFaceIcons == nil {
		face := "Segoe MDL2 Assets"
		if _, err := os.Stat(filepath.Join(os.Getenv("WINDIR"), "Fonts", "SegoeIcons.ttf")); err == nil {
			face = "Segoe Fluent Icons"
		}
		bFaceIcons = syscall.StringToUTF16Ptr(face)
	}
	return bFaceIcons
}

// makeFont builds an anti-aliased (not ClearType) font of the given pixel
// size — ClearType's colour fringes would show on a layered window.
func makeFont(px int, weight uintptr, face *uint16) uintptr {
	f, _, _ := bCreateFontW.Call(uintptr(uint32(int32(-px))), 0, 0, 0, weight, 0, 0, 0,
		1 /*DEFAULT_CHARSET*/, 0, 0, 4 /*ANTIALIASED_QUALITY*/, 0, uintptr(unsafe.Pointer(face)))
	return f
}

// iconFont is cached per size; the glyphs grow with the buttons as they pop.
// Fonts are made at ssK x the on-screen size for supersampled drawing.
func iconFont(px int) uintptr {
	if f, ok := gIconFonts[px]; ok {
		return f
	}
	f := makeFont(px*ssK, 400, iconFace())
	gIconFonts[px] = f
	return f
}

// labelFont is Poppins Medium (the app's display font), at ssK x size.
func labelFont(s float64) uintptr {
	px := int(math.Round(12.5 * s * ssK))
	if gLabelFont == 0 || gLabelPx != px {
		if gLabelFont != 0 {
			bDeleteObject.Call(gLabelFont)
		}
		gLabelFont, gLabelPx = makeFont(px, 500, labelFace()), px
	}
	return gLabelFont
}

func textExtent(hdc uintptr, s string) (float64, float64) {
	u, _ := syscall.UTF16FromString(s)
	var sz bSIZE
	bGetTextExtentPoint32W.Call(hdc, uintptr(unsafe.Pointer(&u[0])), uintptr(len(u)-1),
		uintptr(unsafe.Pointer(&sz)))
	return float64(sz.Cx), float64(sz.Cy)
}

// ── Open / close ─────────────────────────────────────────────────────────────

// openDial fans the controls out around the bubble. Bubble thread only.
func openDial(bubble uintptr) {
	closeDialNow()

	s := uiScale()
	n := len(bDialItems)
	var brc bRECT
	bGetWindowRect.Call(bubble, uintptr(unsafe.Pointer(&brc)))
	bcx := float64(brc.Left+brc.Right) / 2
	bcy := float64(brc.Top+brc.Bottom) / 2
	bubbleHalf := float64(max(brc.Right-brc.Left, brc.Bottom-brc.Top)) / 2

	rad := 19 * s
	minR := bubbleHalf + rad + 10*s

	// Aim the fan at the middle of the work area. Near an edge or corner a
	// wide arc will not fit, so try it turned a little, then tighter arcs on a
	// larger radius, and failing all that take the layout that spills least.
	var wa bRECT
	bSystemParametersInfo.Call(bSpiGetWorkArea, 0, uintptr(unsafe.Pointer(&wa)), 0)
	aim := math.Atan2(float64(wa.Top+wa.Bottom)/2-bcy, float64(wa.Left+wa.Right)/2-bcx)
	angles := func(mid, arc float64) []float64 {
		a := make([]float64, n)
		for i := range a {
			a[i] = mid - arc/2 + arc*float64(i)/float64(n-1)
		}
		return a
	}
	spill := func(as []float64, r float64) float64 {
		m, out := rad+6*s, 0.0
		for _, a := range as {
			x, y := bcx+r*math.Cos(a), bcy+r*math.Sin(a)
			out += math.Max(0, float64(wa.Left)-(x-m)) + math.Max(0, (x+m)-float64(wa.Right)) +
				math.Max(0, float64(wa.Top)-(y-m)) + math.Max(0, (y+m)-float64(wa.Bottom))
		}
		return out
	}
	var as []float64
	var R float64
	best := math.Inf(1)
search:
	for _, deg := range []float64{150, 120, 90} {
		arc := deg * math.Pi / 180
		r := math.Max(46*s*float64(n-1)/arc, minR)
		for _, off := range []float64{0, 15, -15, 30, -30, 45, -45} {
			cand := angles(aim+off*math.Pi/180, arc)
			if o := spill(cand, r); o < best {
				best, as, R = o, cand, r
				if o == 0 {
					break search
				}
			}
		}
	}
	// Read top to bottom: the first control goes at the upper end of the arc.
	if math.Sin(as[0]) > math.Sin(as[n-1])+1e-6 {
		for i, j := 0, n-1; i < j; i, j = i+1, j-1 {
			as[i], as[j] = as[j], as[i]
		}
	}

	// Size the overlay to hold the arc plus the widest label beyond it.
	lf := labelFont(s)
	labelMax := math.Max(textWidth(lf, "Hide from Top"), textWidth(lf, "Hide Live View"))
	for _, it := range bDialItems {
		labelMax = math.Max(labelMax, textWidth(lf, it.label))
	}
	ext := math.Ceil(R + rad*1.15 + 10*s + labelMax + 26*s + 10*s)

	gDialS, gDialRad, gDialR = s, rad, R
	gDialW, gDialH = int32(2*ext), int32(2*ext)
	gDialOrigin = bPOINT{int32(bcx - ext), int32(bcy - ext)}
	gDialCX, gDialCY = bcx-float64(gDialOrigin.X), bcy-float64(gDialOrigin.Y)
	gDialSlots = make([]dialSlot, n)
	for i, a := range as {
		ux, uy := math.Cos(a), math.Sin(a)
		gDialSlots[i] = dialSlot{gDialCX + R*ux, gDialCY + R*uy, ux, uy}
	}

	gDialBmp, gDialPx = newDIB(gDialW, gDialH)
	if gDialBmp == 0 {
		return
	}
	gDialCov = make([]float32, gDialW*gDialH)
	gDialSh = make([]float32, gDialW*gDialH)

	hInst, _, _ := bGetModuleHandleW.Call(0)
	registerClasses(hInst)
	h, _, _ := bCreateWindowExW.Call(
		bWsExTopmost|bWsExToolWindow|bWsExLayered|bWsExNoActivate,
		uintptr(unsafe.Pointer(bClsDial)),
		uintptr(unsafe.Pointer(bTitleDial)),
		bWsPopup,
		uintptr(gDialOrigin.X), uintptr(gDialOrigin.Y), uintptr(gDialW), uintptr(gDialH),
		0, 0, hInst, 0,
	)
	if h == 0 {
		bDeleteObject.Call(gDialBmp)
		gDialBmp, gDialPx = 0, nil
		return
	}
	gDialHwnd = h
	excludeFromCapture(h)
	gDialOpen, gDialClosing, gDialHover, gDialTracking = true, false, -1, false
	gDialT0 = time.Now()

	renderDial()
	bShowWindow.Call(h, 4) // SW_SHOWNOACTIVATE
	// The bubble sits in the middle of the dial and must stay clickable.
	bSetWindowPos.Call(bubble, bHwndTopmost, 0, 0, 0, 0, bSwpNoMove|bSwpNoSize|bSwpNoActivate)
	bSetTimer.Call(h, dialTimerID, 15, 0)
	updateLogo(bubble)
	updateChip() // hidden while the dial is out
}

// closeDial tucks the buttons back into the bubble, then removes the dial.
func closeDial() {
	if gDialHwnd == 0 || gDialClosing {
		return
	}
	gDialOpen, gDialClosing, gDialHover = false, true, -1
	gDialT0 = time.Now()
	bSetTimer.Call(gDialHwnd, dialTimerID, 15, 0)
	updateLogo(gBubbleHwnd)
}

// closeDialNow removes the dial without animating. Bubble thread only.
func closeDialNow() {
	wasOpen := gDialOpen
	if gDialHwnd != 0 {
		bKillTimer.Call(gDialHwnd, dialTimerID)
		bDestroyWindow.Call(gDialHwnd)
		gDialHwnd = 0
	}
	if gDialBmp != 0 {
		bDeleteObject.Call(gDialBmp)
		gDialBmp, gDialPx, gDialCov, gDialSh = 0, nil, nil, nil
	}
	gDialOpen, gDialClosing, gDialHover = false, false, -1
	if wasOpen && gBubbleHwnd != 0 {
		updateLogo(gBubbleHwnd)
	}
	updateChip()
}

// ── Animation ────────────────────────────────────────────────────────────────

func clamp01(v float64) float64 { return math.Max(0, math.Min(1, v)) }

// easeOutBack overshoots slightly before settling — the "pop".
func easeOutBack(p float64) float64 {
	const c1 = 1.70158
	const c3 = c1 + 1
	q := p - 1
	return 1 + c3*q*q*q + c1*q*q
}

// itemState is how far button i has travelled (0 = inside the bubble,
// 1 = in place), its scale and opacity, and whether it is still moving.
func itemState(i int, now time.Time) (e, scale, alpha float64, moving bool) {
	t := now.Sub(gDialT0)
	n := len(bDialItems)
	if gDialClosing {
		p := clamp01(float64(t-time.Duration(n-1-i)*dialCloseLag) / float64(dialCloseDur))
		q := 1 - p
		e = q * q * (3 - 2*q)
		return e, 0.35 + 0.65*e, clamp01(q * 3), p < 1
	}
	p := clamp01(float64(t-time.Duration(i)*dialOpenLag) / float64(dialOpenDur))
	e = easeOutBack(p)
	return e, 0.35 + 0.65*e, clamp01(p * 3), p < 1
}

// ── Rendering ────────────────────────────────────────────────────────────────

// paint lays colour c at coverage a over pixel i (painter's order).
func dialPaint(i int, a float32, c rgb) {
	old := gDialCov[i]
	n := a + old*(1-a)
	if n <= 0 {
		return
	}
	f, g := a/n, old*(1-a)/n
	p := i * 4
	gDialPx[p] = byte(f*float32(c.b) + g*float32(gDialPx[p]))
	gDialPx[p+1] = byte(f*float32(c.g) + g*float32(gDialPx[p+1]))
	gDialPx[p+2] = byte(f*float32(c.r) + g*float32(gDialPx[p+2]))
	gDialCov[i] = n
}

func dialShadow(i int, a float32) {
	if a > gDialSh[i] {
		gDialSh[i] = a
	}
}

func smoothstep(a, b, x float64) float64 {
	t := clamp01((x - a) / (b - a))
	return t * t * (3 - 2*t)
}

// span returns the overlay pixel range [lo, hi) covering c±r.
func span(c, r float64, limit int32) (int, int) {
	lo := int(math.Max(0, math.Floor(c-r)))
	hi := int(math.Min(float64(limit), math.Ceil(c+r)))
	return lo, hi
}

// disc draws an anti-aliased filled circle with a 1px ring and a soft drop
// shadow.
func disc(cx, cy, r float64, c, ring rgb, alpha float64) {
	s := gDialS
	rw := math.Max(1, s) // ring width
	x0, x1 := span(cx, r+8*s, gDialW)
	y0, y1 := span(cy+2*s, r+8*s, gDialH)
	for y := y0; y < y1; y++ {
		for x := x0; x < x1; x++ {
			i := y*int(gDialW) + x
			px, py := float64(x)+0.5, float64(y)+0.5
			ds := math.Hypot(px-cx, py-cy-2*s)
			dialShadow(i, float32(0.28*alpha*(1-smoothstep(r-3*s, r+6*s, ds))))
			d := math.Hypot(px-cx, py-cy) - r
			if cov := clamp01(0.5-d) * alpha; cov > 0 {
				dialPaint(i, float32(cov), mix(c, ring, clamp01(d+rw+0.5)))
			}
		}
	}
}

// pill draws a rounded label background with a 1px border and shadow.
func pill(x0, y0, x1, y1 float64, bg, border rgb) {
	s := gDialS
	cx, cy := (x0+x1)/2, (y0+y1)/2
	hw, hh := (x1-x0)/2, (y1-y0)/2
	sdf := func(px, py float64) float64 {
		qx := math.Abs(px-cx) - (hw - hh)
		qy := math.Abs(py-cy) - 0
		out := math.Hypot(math.Max(qx, 0), math.Max(qy, 0))
		return out + math.Min(math.Max(qx, qy), 0) - hh
	}
	xa, xb := span(cx, hw+8*s, gDialW)
	ya, yb := span(cy+2*s, hh+8*s, gDialH)
	for y := ya; y < yb; y++ {
		for x := xa; x < xb; x++ {
			i := y*int(gDialW) + x
			px, py := float64(x)+0.5, float64(y)+0.5
			dialShadow(i, float32(0.25*(1-smoothstep(-2*s, 6*s, sdf(px, py-2*s)))))
			d := sdf(px, py)
			if cov := clamp01(0.5 - d); cov > 0 {
				c := bg
				if d > -s {
					c = border
				}
				dialPaint(i, float32(cov), c)
			}
		}
	}
}

// renderDial draws the current animation frame and pushes it to the screen.
// Returns whether anything is still animating. Bubble thread only.
func renderDial() bool {
	if gDialHwnd == 0 || gDialBmp == 0 {
		return false
	}
	clear(gDialPx)
	clear(gDialCov)
	clear(gDialSh)

	now := time.Now()
	s := gDialS
	type drawn struct {
		x, y, r, alpha float64
		glyph          rune
		ink            rgb
	}
	items := make([]drawn, len(bDialItems))
	moving := false
	for i, it := range bDialItems {
		e, scale, alpha, m := itemState(i, now)
		moving = moving || m
		sl := gDialSlots[i]
		x, y := gDialCX+sl.ux*gDialR*e, gDialCY+sl.uy*gDialR*e
		hover := i == gDialHover && !m && !gDialClosing
		if hover {
			scale *= 1.1
		}
		r := gDialRad * scale
		fill, ring, ink := itemColours(it, hover)
		if alpha > 0 {
			disc(x, y, r, fill, ring, alpha)
		}
		_, glyph := itemText(it)
		items[i] = drawn{x, y, r, alpha, glyph, ink}
	}

	// Label for the hovered button, pushed outward from the arc.
	label, labelOn := "", false
	var lx0, ly0, lx1, ly1 float64
	if h := gDialHover; h >= 0 && !moving && !gDialClosing {
		label, _ = itemText(bDialItems[h])
		tw := textWidth(labelFont(s), label)
		pw, ph := tw+24*s, 26*s
		sl, it := gDialSlots[h], items[h]
		ax, ay := it.x+sl.ux*(it.r+8*s), it.y+sl.uy*(it.r+8*s)
		switch {
		case math.Abs(sl.ux) < 0.35 && sl.uy < 0: // straight up
			lx0, ly0 = ax-pw/2, ay-ph
		case math.Abs(sl.ux) < 0.35: // straight down
			lx0, ly0 = ax-pw/2, ay
		case sl.ux < 0:
			lx0, ly0 = ax-pw, ay-ph/2
		default:
			lx0, ly0 = ax, ay-ph/2
		}
		lx1, ly1 = lx0+pw, ly0+ph
		bg, border, _ := labelColours()
		pill(lx0, ly0, lx1, ly1, bg, border)
		labelOn = true
	}
	gDialLabelRc = [4]float64{lx0, ly0, lx1, ly1}
	if !labelOn {
		gDialLabelRc = [4]float64{}
	}

	// Pass 2: supersampled glyphs and label text over the shapes.
	for _, it := range items {
		if it.alpha <= 0 || it.r < 4*s {
			continue
		}
		px := int(math.Round(it.r * 0.8))
		ssText(string(it.glyph), iconFont(px), it.x-it.r, it.y-it.r, it.x+it.r, it.y+it.r, it.ink, it.alpha)
	}
	if labelOn {
		_, _, ink := labelColours()
		// Poppins sits a touch high in its line box; nudge it to optical centre.
		ssText(label, labelFont(s), lx0, ly0+0.5*s, lx1, ly1+0.5*s, ink, 1)
	}

	// Pass 3: fold coverage and shadow into premultiplied alpha.
	for i, c := range gDialCov {
		a := c + gDialSh[i]*(1-c)
		p := i * 4
		gDialPx[p] = byte(float32(gDialPx[p]) * c)
		gDialPx[p+1] = byte(float32(gDialPx[p+1]) * c)
		gDialPx[p+2] = byte(float32(gDialPx[p+2]) * c)
		gDialPx[p+3] = byte(a*255 + 0.5)
	}

	pos := gDialOrigin
	pushLayered(gDialHwnd, gDialBmp, gDialW, gDialH, &pos)
	return moving
}

// ── Dial window procedure ────────────────────────────────────────────────────

// dialHit returns the button under overlay point (x, y), or -1.
func dialHit(x, y int32) int {
	px, py := float64(x)+0.5, float64(y)+0.5
	for i, sl := range gDialSlots {
		if math.Hypot(px-sl.fx, py-sl.fy) <= gDialRad*1.15 {
			return i
		}
	}
	if r := gDialLabelRc; gDialHover >= 0 && px >= r[0] && px < r[2] && py >= r[1] && py < r[3] {
		return gDialHover
	}
	return -1
}

func dialWndProc(hwnd, msg, wParam, lParam uintptr) uintptr {
	switch uint32(msg) {
	case bWmPaint:
		var ps bPAINTSTRUCT
		bBeginPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
		bEndPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
		return 0

	case bWmTimer:
		if !renderDial() {
			bKillTimer.Call(hwnd, dialTimerID)
			if gDialClosing {
				closeDialNow()
			}
		}
		return 0

	case bWmMouseMove:
		if !gDialTracking {
			tme := bTRACKMOUSEEVENT{DwFlags: bTmeLeave, HwndTrack: hwnd}
			tme.CbSize = uint32(unsafe.Sizeof(tme))
			bTrackMouseEvent.Call(uintptr(unsafe.Pointer(&tme)))
			gDialTracking = true
		}
		if h := dialHit(loWord(lParam), hiWord(lParam)); h != gDialHover && !gDialClosing {
			gDialHover = h
			renderDial()
		}
		return 0

	case bWmMouseLeave:
		gDialTracking = false
		if gDialHover != -1 && !gDialClosing {
			gDialHover = -1
			renderDial()
		}
		return 0

	case bWmLButtonDown:
		return 0

	case bWmLButtonUp:
		i := dialHit(loWord(lParam), hiWord(lParam))
		if i < 0 || gDialClosing {
			return 0
		}
		cmd := bDialItems[i].cmd
		closeDial()
		switch cmd {
		case "bubble:pin":
			togglePin()
		case "bubble:liveview":
			toggleLiveView()
		default:
			emitBubbleCmd(cmd)
		}
		return 0

	case bWmRButtonDown:
		closeDial()
		return 0

	case bWmNcHitTest:
		return bHtClient

	case bWmDestroy:
		return 0
	}
	r, _, _ := bDefWindowProcW.Call(hwnd, msg, wParam, lParam)
	return r
}
