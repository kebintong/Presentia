package bubbleui

import (
	"fmt"
	"math"
	"strings"
)

// ── text ─────────────────────────────────────────────────────────────────────

// Font is a text size in device pixels.
type Font struct {
	Px   float64
	Bold bool
}

// Align for Text.Draw.
const (
	Left = iota
	Center
	Right
)

// Text measures and draws strings. Windows uses GDI with the app's Poppins;
// tests and previews use a font file.
type Text interface {
	Width(f Font, s string) float64
	// Draw writes s inside the box, vertically centred.
	Draw(c *Canvas, f Font, s string, x0, y0, x1, y1 float64, align int, col RGB)
}

// Ellipsize shortens s with "…" until it fits in w.
func Ellipsize(t Text, f Font, s string, w float64) string {
	if t.Width(f, s) <= w {
		return s
	}
	r := []rune(s)
	for len(r) > 1 {
		r = r[:len(r)-1]
		cand := strings.TrimRight(string(r), " ") + "…"
		if t.Width(f, cand) <= w {
			return cand
		}
	}
	return "…"
}

// ── what the bubble shows ────────────────────────────────────────────────────

// Student is someone not on camera, and for how long (seconds).
type Student struct {
	Name string
	Away float64
}

// View is everything the bubble shows. The Windows side fills it from the
// sidecar's /api/monitor/live and from what the Monitor page tells it.
type View struct {
	Active  bool
	Elapsed float64
	Title   string // class name (or "Presentia")

	Present, Missing, Waiting, Total, Unknown int
	Away                                      []Student

	SourceKind   string // "", "area" or "window"
	SourceLabel  string
	SourceDetail string

	Pinned    bool
	PanelOpen bool
	Hover     string // id of the control under the pointer
	Copied    string // id of the copy button just used
}

// Hit is a clickable area, in canvas pixels.
type Hit struct {
	ID             string
	X0, Y0, X1, Y1 float64
}

// Layout is a drawn capsule or panel.
type Layout struct {
	C    *Canvas
	Hits []Hit
	// The visible body inside the canvas (the rest is room for the shadow).
	BX0, BY0, BX1, BY1 float64
}

// HitAt returns the id of the control at (x, y), or "".
func (l *Layout) HitAt(x, y float64) string {
	for i := len(l.Hits) - 1; i >= 0; i-- {
		h := l.Hits[i]
		if x >= h.X0 && x < h.X1 && y >= h.Y0 && y < h.Y1 {
			return h.ID
		}
	}
	return ""
}

// Clock formats seconds as m:ss or h:mm:ss.
func Clock(sec float64) string {
	t := int(math.Max(0, sec))
	if t >= 3600 {
		return fmt.Sprintf("%d:%02d:%02d", t/3600, t/60%60, t%60)
	}
	return fmt.Sprintf("%d:%02d", t/60, t%60)
}

// CopyMessage is the reminder the instructor pastes into the meeting chat
// (same wording as app/core/attendance/messages.py).
func CopyMessage(name string, away float64) string {
	first := "there"
	if f := strings.Fields(name); len(f) > 0 {
		first = f[0]
	}
	m := int(away / 60)
	if m < 1 {
		return fmt.Sprintf("Hi %s, please turn your camera on. Attendance needs your face on screen.", first)
	}
	s := "s"
	if m == 1 {
		s = ""
	}
	return fmt.Sprintf("Hi %s, please turn your camera on. Presentia shows it has been off for %d minute%s, "+
		"and attendance needs your face on screen.", first, m, s)
}

// warnLevel is the camera-off warning a stretch of `away` seconds has reached
// (one per minute; W4 means Absent).
func warnLevel(away float64) int { return int(math.Min(4, math.Max(0, away/60))) }

// ── drawing helpers ──────────────────────────────────────────────────────────

type painter struct {
	c  *Canvas
	t  Text
	th Theme
	s  float64
	v  View
}

func (p *painter) r(v float64) float64 {
	if p.th.Square {
		return 0
	}
	return v * p.s
}

// shadow under a body box.
func (p *painter) shadow(x0, y0, x1, y1, rad float64) {
	th := p.th
	if th.HardShadow {
		o := 4 * p.s
		p.c.RoundRect(x0+o, y0+o, x1+o, y1+o, rad, Solid(th.ShadowCol), 1)
		return
	}
	p.c.SoftShadow(x0, y0, x1, y1, rad, 16*p.s, 6*p.s, th.ShadowCol, th.ShadowAlpha)
}

func (p *painter) body(x0, y0, x1, y1, rad float64) {
	p.shadow(x0, y0, x1, y1, rad)
	p.c.Box(x0, y0, x1, y1, rad, p.th.Panel, p.th.Border, math.Max(1, p.th.BorderW*p.s))
}

func (p *painter) tile(x, y, size float64) {
	th := p.th
	rad := p.r(size * 0.27)
	if th.TileShadow != nil {
		o := 2 * p.s
		p.c.RoundRect(x+o, y+o, x+size+o, y+size+o, rad, Solid(*th.TileShadow), 1)
	}
	p.c.RoundRect(x, y, x+size, y+size, rad, th.Tile, 1)
	p.c.Mark(x+size*0.27, y+size*0.2, size*0.46, size*0.6, th.Mark)
}

func hovered(v View, id string) bool { return v.Hover == id }

// button draws a secondary (kind "sub"), primary ("pri") or danger ("dan")
// button with an icon and a label, and records its hit.
func (p *painter) button(id, kind, icon, label string, x0, y0, x1, y1 float64, disabled bool, hits *[]Hit) {
	th := p.th
	rad := p.r(10)
	hov := hovered(p.v, id) && !disabled
	var fill Fill
	var ink RGB
	var border *Fill
	bw := math.Max(1, th.SubBW*p.s)
	switch kind {
	case "pri":
		fill, ink = th.Accent, th.OnAccent
		if hov {
			fill = brighten(fill, 0.12)
		}
		if th.Key == "brutal" {
			border = fp(Solid(th.Ink))
			if !disabled {
				o := 3 * p.s
				if hov {
					o = 4 * p.s
				}
				p.c.RoundRect(x0+o, y0+o, x1+o, y1+o, rad, Solid(th.Ink), 1)
			}
		}
	case "dan":
		fill, ink, border = th.Sub, th.Danger, th.SubBorder
		if hov {
			fill = Solid(Mix(th.Sub.First(), th.Danger, 0.12))
		}
	default:
		fill, ink, border = th.Sub, th.Ink, th.SubBorder
		if hov {
			fill = Solid(Mix(th.Sub.First(), th.Ink, 0.07))
		}
	}
	op := 1.0
	if disabled {
		op = 0.45
	}
	if border != nil {
		p.c.RoundRect(x0, y0, x1, y1, rad, *border, op)
		p.c.RoundRect(x0+bw, y0+bw, x1-bw, y1-bw, math.Max(0, rad-bw), fill, op)
	} else {
		p.c.RoundRect(x0, y0, x1, y1, rad, fill, op)
	}
	f := Font{12 * p.s, true}
	if kind == "pri" {
		f.Px = 13 * p.s
	}
	isz := 14 * p.s
	tw := p.t.Width(f, label)
	gap := 7 * p.s
	total := isz + gap + tw
	if icon == "" {
		total = tw
	}
	cx := (x0 + x1) / 2
	x := cx - total/2
	iconCol := ink
	if kind == "sub" {
		iconCol = th.Icon
	}
	if disabled {
		ink, iconCol = Mix(ink, th.Sub.First(), 0.35), Mix(iconCol, th.Sub.First(), 0.35)
	}
	if icon != "" {
		p.c.Icon(icon, x, (y0+y1)/2-isz/2, isz, 1.9*p.s, iconCol)
		x += isz + gap
	}
	p.t.Draw(p.c, f, label, x, y0, x+tw+2*p.s, y1, Left, ink)
	if !disabled {
		*hits = append(*hits, Hit{id, x0, y0, x1, y1})
	}
}

func brighten(f Fill, k float64) Fill {
	out := Fill{Angle: f.Angle, Stops: make([]Stop, len(f.Stops))}
	for i, s := range f.Stops {
		out.Stops[i] = Stop{s.At, Mix(s.C, RGB{255, 255, 255}, k)}
	}
	return out
}

func (p *painter) label(s string, x, y, w float64) {
	p.t.Draw(p.c, Font{9.5 * p.s, true}, strings.ToUpper(s), x, y, x+w, y+14*p.s, Left, p.th.Muted)
}

// chip is a small rounded tag; returns its width.
func (p *painter) chip(s string, xRight, cy float64, bg, fg RGB, outline bool) float64 {
	f := Font{10 * p.s, true}
	w := p.t.Width(f, s) + 14*p.s
	h := 18 * p.s
	x0 := xRight - w
	rad := p.r(9)
	if outline {
		p.c.RoundRect(x0, cy-h/2, xRight, cy+h/2, rad, Solid(p.th.Ink), 1)
		b := math.Max(1, 1.5*p.s)
		p.c.RoundRect(x0+b, cy-h/2+b, xRight-b, cy+h/2-b, math.Max(0, rad-b), Solid(bg), 1)
	} else {
		p.c.RoundRect(x0, cy-h/2, xRight, cy+h/2, rad, Solid(bg), 1)
	}
	p.t.Draw(p.c, f, s, x0, cy-h/2, xRight, cy+h/2, Center, fg)
	return w
}

func (p *painter) hline(x0, x1, y float64, col RGB, w float64) {
	p.c.RoundRect(x0, y, x1, y+w, 0, Solid(col), 1)
}

// ── capsule ──────────────────────────────────────────────────────────────────

type capItem struct {
	kind string // text, icon, dot, sep
	s    string
	col  RGB
}

// RenderCapsule draws the collapsed bubble: logo, then what is going on.
func RenderCapsule(v View, th Theme, t Text, s float64) *Layout {
	f := Font{12.5 * s, true}
	var items []capItem
	switch {
	case v.Active:
		items = append(items, capItem{"dot", "", th.OK}, capItem{"text", Clock(v.Elapsed), th.Ink},
			capItem{"sep", "", th.Muted},
			capItem{"text", fmt.Sprintf("%d/%d", v.Present, v.Total), th.OK})
		if v.Missing > 0 {
			items = append(items, capItem{"sep", "", th.Muted}, capItem{"icon", "camoff", th.Warn},
				capItem{"text", fmt.Sprint(v.Missing), th.Warn})
		}
	case v.SourceKind != "":
		items = append(items, capItem{"text", "Ready", th.Ink}, capItem{"sep", "", th.Muted},
			capItem{"text", "Press Start", th.Muted})
	default:
		items = append(items, capItem{"text", "Not watching", th.Muted})
	}
	chev := "chev-down"
	if v.PanelOpen {
		chev = "chev-up"
	}
	items = append(items, capItem{"icon", chev, th.Muted})

	gap := 8 * s
	h := 42 * s
	tileSz := 32 * s
	inset := 5 * s
	widths := make([]float64, len(items))
	w := inset + tileSz + 10*s
	for i, it := range items {
		switch it.kind {
		case "text":
			widths[i] = t.Width(f, it.s)
		case "icon":
			widths[i] = 14 * s
		case "dot":
			widths[i] = 8 * s
		case "sep":
			widths[i] = 1 * s
		}
		w += widths[i]
		if i > 0 {
			gap2 := gap
			if items[i-1].kind == "icon" && it.kind == "text" || items[i-1].kind == "dot" {
				gap2 = 5 * s
			}
			w += gap2
		}
	}
	w += 12 * s

	m := 18 * s // room for the shadow
	c := NewCanvas(int(math.Ceil(w+2*m)), int(math.Ceil(h+2*m)))
	p := &painter{c: c, t: t, th: th, s: s, v: v}
	x0, y0 := m, m
	x1, y1 := x0+w, y0+h
	rad := h / 2
	if th.Square {
		rad = 0
	}
	p.body(x0, y0, x1, y1, rad)
	if hovered(v, "capsule") {
		c.RoundRect(x0, y0, x1, y1, rad, Solid(th.Ink), 0.04)
	}
	p.tile(x0+inset, y0+(h-tileSz)/2, tileSz)
	x := x0 + inset + tileSz + 10*s
	cy := (y0 + y1) / 2
	for i, it := range items {
		if i > 0 {
			gap2 := gap
			if items[i-1].kind == "icon" && it.kind == "text" || items[i-1].kind == "dot" {
				gap2 = 5 * s
			}
			x += gap2
		}
		switch it.kind {
		case "text":
			t.Draw(c, f, it.s, x, y0, x+widths[i]+2*s, y1, Left, it.col)
		case "icon":
			c.Icon(it.s, x, cy-7*s, 14*s, 2*s, it.col)
		case "dot":
			c.Disc(x+4*s, cy, 6.5*s, it.col, 0.25)
			c.Disc(x+4*s, cy, 4*s, it.col, 1)
		case "sep":
			c.RoundRect(x, cy-7*s, x+1*s, cy+7*s, 0, Solid(Mix(it.col, th.Panel.First(), 0.55)), 1)
		}
		x += widths[i]
	}
	return &Layout{C: c, Hits: []Hit{{"capsule", x0, y0, x1, y1}}, BX0: x0, BY0: y0, BX1: x1, BY1: y1}
}

// ── panel ────────────────────────────────────────────────────────────────────

// PanelWidth is the panel's width in logical px.
const PanelWidth = 276

// MaxRows is how many "not on camera" names the panel lists.
const MaxRows = 4

// RenderPanel draws the opened bubble.
func RenderPanel(v View, th Theme, t Text, s float64) *Layout {
	w := PanelWidth * s
	pad := 12 * s
	headH := 56 * s
	footH := 42 * s

	// Body height first, so the canvas can be sized.
	var bodyH float64
	if v.Active {
		rows := len(v.Away)
		if rows > MaxRows {
			rows = MaxRows
		}
		bodyH = 50*s + 10*s + 16*s + 6*s
		if rows == 0 {
			bodyH += 24 * s
		} else {
			bodyH += float64(rows) * 32 * s
			if len(v.Away) > MaxRows {
				bodyH += 18 * s
			}
		}
		bodyH += 8*s + 36*s
	} else {
		bodyH = 16*s + 6*s + 50*s + 10*s + 34*s + 10*s + 40*s
	}
	h := headH + pad + bodyH + pad + footH

	m := 20 * s
	c := NewCanvas(int(math.Ceil(w+2*m)), int(math.Ceil(h+2*m)))
	p := &painter{c: c, t: t, th: th, s: s, v: v}
	var hits []Hit
	x0, y0 := m, m
	x1, y1 := x0+w, y0+h
	rad := p.r(th.Radius)
	bw := math.Max(1, th.BorderW*s)
	p.body(x0, y0, x1, y1, rad)

	// Header.
	if th.HeaderBand != nil {
		c.RoundRect(x0+bw, y0+bw, x1-bw, y0+headH, math.Max(0, rad-bw), *th.HeaderBand, 1)
		c.RoundRect(x0+bw, y0+headH-rad-1, x1-bw, y0+headH, 0, *th.HeaderBand, 1)
	}
	divW := math.Max(1, s)
	if th.Key == "brutal" {
		divW = 2 * s
	}
	p.hline(x0+bw, x1-bw, y0+headH-divW, th.Divider, divW)
	tileSz := 30 * s
	p.tile(x0+pad, y0+(headH-tileSz)/2, tileSz)
	tx := x0 + pad + tileSz + 10*s
	title := v.Title
	if title == "" {
		title = "Presentia"
	}
	closeSz := 26 * s
	tf := Font{13.5 * s, true}
	title = Ellipsize(t, tf, title, x1-pad-closeSz-8*s-tx)
	t.Draw(c, tf, title, tx, y0+10*s, x1-pad-closeSz, y0+30*s, Left, th.Ink)
	sf := Font{11 * s, true}
	sy0, sy1 := y0+29*s, y0+46*s
	switch {
	case v.Active:
		c.Disc(tx+4*s, (sy0+sy1)/2, 3.5*s, th.OK, 1)
		t.Draw(c, sf, "Live · "+Clock(v.Elapsed), tx+11*s, sy0, x1-pad, sy1, Left, th.OK)
	case v.SourceKind != "":
		t.Draw(c, sf, "Ready to start", tx, sy0, x1-pad, sy1, Left, th.Muted)
	default:
		t.Draw(c, sf, "Not monitoring", tx, sy0, x1-pad, sy1, Left, th.Muted)
	}
	cx0 := x1 - pad - closeSz
	cy0 := y0 + (headH-closeSz)/2
	if hovered(v, "close") {
		c.RoundRect(cx0, cy0, cx0+closeSz, cy0+closeSz, p.r(8), Solid(th.Ink), 0.08)
	}
	c.Icon("chev-up", cx0+5*s, cy0+5*s, closeSz-10*s, 2*s, th.Muted)
	hits = append(hits, Hit{"close", cx0, cy0, cx0 + closeSz, cy0 + closeSz})

	// Body.
	y := y0 + headH + pad
	bx0, bx1 := x0+pad, x1-pad
	if v.Active {
		y = p.liveBody(bx0, bx1, y, &hits)
	} else {
		y = p.idleBody(bx0, bx1, y, &hits)
	}

	// Footer.
	fy := y1 - footH
	p.hline(x0+bw, x1-bw, fy, th.Divider, divW)
	pin := "On top"
	if v.Pinned {
		pin = "Unpin app"
	}
	items := []struct{ id, icon, label string }{
		{"pin", "pin", pin}, {"app", "app", "Open app"}, {"hide", "x", "Hide bubble"},
	}
	ff := Font{11 * s, true}
	cw := (bx1 - bx0) / float64(len(items))
	for i, it := range items {
		ix0 := bx0 + float64(i)*cw
		ix1 := ix0 + cw
		iy0, iy1 := fy+6*s, y1-6*s
		if hovered(v, it.id) {
			c.RoundRect(ix0+2*s, iy0, ix1-2*s, iy1, p.r(8), Solid(th.Ink), 0.07)
		}
		lw := t.Width(ff, it.label)
		isz := 13 * s
		tot := isz + 5*s + lw
		lx := (ix0+ix1)/2 - tot/2
		col := th.Muted
		if it.id == "pin" && v.Pinned {
			col = th.Icon
		}
		c.Icon(it.icon, lx, (iy0+iy1)/2-isz/2, isz, 1.8*s, col)
		t.Draw(c, ff, it.label, lx+isz+5*s, iy0, ix1, iy1, Left, col)
		hits = append(hits, Hit{it.id, ix0, iy0, ix1, iy1})
	}
	_ = y
	return &Layout{C: c, Hits: hits, BX0: x0, BY0: y0, BX1: x1, BY1: y1}
}

func (p *painter) idleBody(bx0, bx1, y float64, hits *[]Hit) float64 {
	th, s, v, c, t := p.th, p.s, p.v, p.c, p.t
	p.label("What to watch", bx0, y, bx1-bx0)
	y += 16*s + 6*s

	// The picked area or window.
	rh := 50 * s
	c.Box(bx0, y, bx1, y+rh, p.r(12), th.Sub, th.SubBorder, math.Max(1, th.SubBW*s))
	ib := 30 * s
	ix := bx0 + 10*s
	iy := y + (rh-ib)/2
	icon, l1, l2 := "area", "Nothing picked yet", "Pick the meeting's video tiles or its window"
	if v.SourceKind != "" {
		icon, l1, l2 = v.SourceKind, v.SourceLabel, v.SourceDetail
		c.RoundRect(ix, iy, ix+ib, iy+ib, p.r(8), th.Accent, 0.18)
	} else {
		c.RoundRect(ix, iy, ix+ib, iy+ib, p.r(8), Solid(th.Muted), 0.12)
	}
	icol := th.Muted
	if v.SourceKind != "" {
		icol = th.Icon
	}
	c.Icon(icon, ix+7*s, iy+7*s, ib-14*s, 1.9*s, icol)
	tx := ix + ib + 10*s
	f1, f2 := Font{12.5 * s, true}, Font{10.5 * s, false}
	t.Draw(c, f1, Ellipsize(t, f1, l1, bx1-10*s-tx), tx, y+7*s, bx1-10*s, y+26*s, Left, th.Ink)
	t.Draw(c, f2, Ellipsize(t, f2, l2, bx1-10*s-tx), tx, y+25*s, bx1-10*s, y+43*s, Left, th.Muted)
	y += rh + 10*s

	gap := 8 * s
	mid := (bx0 + bx1) / 2
	areaL, winL := "Screen area", "Window"
	if v.SourceKind == "area" {
		areaL = "Change area"
	}
	if v.SourceKind == "window" {
		winL = "Change window"
	}
	p.button("area", "sub", "area", areaL, bx0, y, mid-gap/2, y+34*s, false, hits)
	p.button("window", "sub", "window", winL, mid+gap/2, y, bx1, y+34*s, false, hits)
	y += 34*s + 10*s
	p.button("start", "pri", "play", "Start monitoring", bx0, y, bx1, y+40*s, v.SourceKind == "", hits)
	return y + 40*s
}

func (p *painter) liveBody(bx0, bx1, y float64, hits *[]Hit) float64 {
	th, s, v, c, t := p.th, p.s, p.v, p.c, p.t
	stats := []struct {
		n     int
		label string
		col   RGB
	}{
		{v.Present, "HERE", th.OK}, {v.Missing, "CAM OFF", th.Warn},
		{v.Waiting, "NOT YET", th.Muted}, {v.Unknown, "UNKNOWN", th.Icon},
	}
	gap := 6 * s
	cw := (bx1 - bx0 - 3*gap) / 4
	sh := 50 * s
	for i, st := range stats {
		x0 := bx0 + float64(i)*(cw+gap)
		fill := th.Sub
		if th.Stat[i] != nil {
			fill = *th.Stat[i]
		}
		var border *Fill
		if th.Key != "bento" {
			border = th.SubBorder
		}
		c.Box(x0, y, x0+cw, y+sh, p.r(11), fill, border, math.Max(1, th.SubBW*s))
		t.Draw(c, Font{17 * s, true}, fmt.Sprint(st.n), x0, y+5*s, x0+cw, y+29*s, Center, st.col)
		t.Draw(c, Font{8.5 * s, true}, st.label, x0, y+29*s, x0+cw, y+44*s, Center, th.Muted)
	}
	y += sh + 10*s

	if len(v.Away) == 0 {
		p.label("Cameras", bx0, y, bx1-bx0)
		y += 16*s + 6*s
		c.Icon("check", bx0, y+4*s, 15*s, 2*s, th.OK)
		t.Draw(c, Font{12 * s, true}, "Everyone seen is on camera", bx0+21*s, y, bx1, y+24*s, Left, th.Muted)
		y += 24 * s
	} else {
		p.label("Not on camera", bx0, y, bx1-bx0)
		y += 16*s + 6*s
		for i, st := range v.Away {
			if i >= MaxRows {
				break
			}
			rh := 28 * s
			cy := y + rh/2
			id := fmt.Sprintf("copy:%d", i)
			bsz := 26 * s
			bx := bx1 - bsz
			// copy button
			hov := hovered(v, id)
			bfill := th.Sub
			if hov {
				bfill = Solid(Mix(th.Sub.First(), th.Ink, 0.08))
			}
			c.Box(bx, cy-bsz/2, bx1, cy+bsz/2, p.r(8), bfill, th.SubBorder, math.Max(1, th.SubBW*s))
			if v.Copied == id {
				c.Icon("check", bx+6*s, cy-7*s, 14*s, 2.2*s, th.OK)
			} else {
				c.Icon("copy", bx+6*s, cy-7*s, 14*s, 1.8*s, th.Icon)
			}
			*hits = append(*hits, Hit{id, bx, cy - bsz/2, bx1, cy + bsz/2})
			// chip: warning level and time away
			lvl := warnLevel(st.Away)
			label := Clock(st.Away)
			bg, fg := th.Sub.First(), th.Muted
			if lvl >= 1 {
				label = fmt.Sprintf("W%d · %s", min(lvl, 3), Clock(st.Away))
				bg, fg = th.W1Bg, th.W1Fg
				if lvl >= 2 {
					bg, fg = th.W2Bg, th.W2Fg
				}
			}
			cwid := p.chip(label, bx-8*s, cy, bg, fg, th.OutlinedChips)
			nf := Font{12.5 * s, true}
			nameW := bx - 8*s - cwid - 8*s - bx0
			t.Draw(c, nf, Ellipsize(t, nf, st.Name, nameW), bx0, y, bx0+nameW, y+rh, Left, th.Ink)
			y += rh + 4*s
		}
		if len(v.Away) > MaxRows {
			t.Draw(c, Font{11 * s, true}, fmt.Sprintf("+%d more — open the app to see everyone", len(v.Away)-MaxRows),
				bx0, y, bx1, y+16*s, Left, th.Muted)
			y += 18 * s
		}
	}
	y += 8 * s
	mid := bx1 - 96*s
	p.button("liveview", "sub", "eye", "Live View", bx0, y, mid-8*s, y+36*s, false, hits)
	p.button("stop", "dan", "stop", "Stop", mid, y, bx1, y+36*s, false, hits)
	return y + 36*s
}
