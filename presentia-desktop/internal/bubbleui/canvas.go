// Package bubbleui draws the floating monitor bubble (a capsule and its
// panel) into plain pixel buffers. It has no Windows or Wails code in it, so
// the whole look can be tested and previewed on any machine; the Windows side
// (bubble_win.go and friends) only puts the pixels on screen and handles the
// mouse.
package bubbleui

import "math"

// RGB is an opaque colour.
type RGB struct{ R, G, B uint8 }

// Hex makes an RGB from 0xRRGGBB.
func Hex(v uint32) RGB { return RGB{uint8(v >> 16), uint8(v >> 8), uint8(v)} }

// Mix blends a toward b by t (0..1).
func Mix(a, b RGB, t float64) RGB {
	t = clamp01(t)
	l := func(x, y uint8) uint8 { return uint8(float64(x) + (float64(y)-float64(x))*t + 0.5) }
	return RGB{l(a.R, b.R), l(a.G, b.G), l(a.B, b.B)}
}

// Stop is one colour of a gradient at position At (0..1).
type Stop struct {
	At float64
	C  RGB
}

// Fill is a solid colour or a linear gradient. Angle uses CSS degrees
// (0 = towards the top, 90 = towards the right).
type Fill struct {
	Stops []Stop
	Angle float64
}

// Solid is a one-colour fill.
func Solid(c RGB) Fill { return Fill{Stops: []Stop{{0, c}}} }

// Linear is a gradient, like CSS linear-gradient(angle, c0, c1, ...) with the
// colours spread evenly.
func Linear(angle float64, cs ...RGB) Fill {
	st := make([]Stop, len(cs))
	for i, c := range cs {
		at := 0.0
		if len(cs) > 1 {
			at = float64(i) / float64(len(cs)-1)
		}
		st[i] = Stop{at, c}
	}
	return Fill{Stops: st, Angle: angle}
}

func (f Fill) solid() bool { return len(f.Stops) <= 1 }

// First is the fill's first colour (its colour when there is no room for a
// gradient, e.g. in small text).
func (f Fill) First() RGB {
	if len(f.Stops) == 0 {
		return RGB{}
	}
	return f.Stops[0].C
}

// at evaluates the fill at (x, y) inside the box (x0,y0)-(x1,y1).
func (f Fill) at(x, y, x0, y0, x1, y1 float64) RGB {
	if f.solid() {
		return f.First()
	}
	a := f.Angle * math.Pi / 180
	dx, dy := math.Sin(a), -math.Cos(a)
	w, h := x1-x0, y1-y0
	length := math.Abs(w*dx) + math.Abs(h*dy)
	if length <= 0 {
		return f.First()
	}
	t := ((x-(x0+x1)/2)*dx+(y-(y0+y1)/2)*dy)/length + 0.5
	st := f.Stops
	if t <= st[0].At {
		return st[0].C
	}
	for i := 1; i < len(st); i++ {
		if t <= st[i].At {
			k := (t - st[i-1].At) / math.Max(1e-9, st[i].At-st[i-1].At)
			return Mix(st[i-1].C, st[i].C, k)
		}
	}
	return st[len(st)-1].C
}

// Canvas is a premultiplied RGBA float buffer.
type Canvas struct {
	W, H int
	px   []float32 // r, g, b, a — premultiplied, 0..1
}

// NewCanvas makes a transparent canvas.
func NewCanvas(w, h int) *Canvas {
	if w < 1 {
		w = 1
	}
	if h < 1 {
		h = 1
	}
	return &Canvas{W: w, H: h, px: make([]float32, w*h*4)}
}

// Blend paints colour c over pixel (x, y) with coverage a (0..1).
func (c *Canvas) Blend(x, y int, a float64, col RGB) {
	if x < 0 || y < 0 || x >= c.W || y >= c.H || a <= 0 {
		return
	}
	if a > 1 {
		a = 1
	}
	i := (y*c.W + x) * 4
	k := float32(1 - a)
	fa := float32(a)
	c.px[i] = float32(col.R)/255*fa + c.px[i]*k
	c.px[i+1] = float32(col.G)/255*fa + c.px[i+1]*k
	c.px[i+2] = float32(col.B)/255*fa + c.px[i+2]*k
	c.px[i+3] = fa + c.px[i+3]*k
}

// Alpha is the pixel's opacity (tests).
func (c *Canvas) Alpha(x, y int) float64 {
	if x < 0 || y < 0 || x >= c.W || y >= c.H {
		return 0
	}
	return float64(c.px[(y*c.W+x)*4+3])
}

// At is the pixel's colour, un-premultiplied (tests, previews).
func (c *Canvas) At(x, y int) (RGB, float64) {
	i := (y*c.W + x) * 4
	a := c.px[i+3]
	if a <= 0 {
		return RGB{}, 0
	}
	f := func(v float32) uint8 { return uint8(math.Min(255, float64(v/a)*255+0.5)) }
	return RGB{f(c.px[i]), f(c.px[i+1]), f(c.px[i+2])}, float64(a)
}

// BGRA returns premultiplied 8-bit BGRA rows, the layout UpdateLayeredWindow
// takes from a top-down 32-bit DIB.
func (c *Canvas) BGRA(dst []byte) {
	n := c.W * c.H
	for p := 0; p < n && p*4+3 < len(dst); p++ {
		i := p * 4
		dst[i] = uint8(math.Min(255, float64(c.px[i+2])*255+0.5))
		dst[i+1] = uint8(math.Min(255, float64(c.px[i+1])*255+0.5))
		dst[i+2] = uint8(math.Min(255, float64(c.px[i])*255+0.5))
		dst[i+3] = uint8(math.Min(255, float64(c.px[i+3])*255+0.5))
	}
}

// RGBA returns straight (non-premultiplied) 8-bit RGBA (previews, PNG).
func (c *Canvas) RGBA() []byte {
	out := make([]byte, c.W*c.H*4)
	for p := 0; p < c.W*c.H; p++ {
		col, a := c.At(p%c.W, p/c.W)
		out[p*4], out[p*4+1], out[p*4+2] = col.R, col.G, col.B
		out[p*4+3] = uint8(math.Min(255, a*255+0.5))
	}
	return out
}

// ── shapes ───────────────────────────────────────────────────────────────────

func clamp01(v float64) float64 { return math.Max(0, math.Min(1, v)) }

func smoothstep(a, b, x float64) float64 {
	t := clamp01((x - a) / (b - a))
	return t * t * (3 - 2*t)
}

// rrSDF is the signed distance from (px,py) to a rounded rectangle.
func rrSDF(px, py, x0, y0, x1, y1, r float64) float64 {
	r = math.Max(0, math.Min(r, math.Min(x1-x0, y1-y0)/2))
	cx, cy := (x0+x1)/2, (y0+y1)/2
	qx := math.Abs(px-cx) - ((x1-x0)/2 - r)
	qy := math.Abs(py-cy) - ((y1-y0)/2 - r)
	return math.Hypot(math.Max(qx, 0), math.Max(qy, 0)) + math.Min(math.Max(qx, qy), 0) - r
}

func (c *Canvas) span(lo, hi float64, limit int) (int, int) {
	a := int(math.Floor(lo))
	b := int(math.Ceil(hi))
	if a < 0 {
		a = 0
	}
	if b > limit {
		b = limit
	}
	return a, b
}

// RoundRect fills a rounded rectangle with anti-aliased edges.
func (c *Canvas) RoundRect(x0, y0, x1, y1, r float64, f Fill, opacity float64) {
	if x1 <= x0 || y1 <= y0 || opacity <= 0 {
		return
	}
	xa, xb := c.span(x0-1, x1+1, c.W)
	ya, yb := c.span(y0-1, y1+1, c.H)
	for y := ya; y < yb; y++ {
		for x := xa; x < xb; x++ {
			px, py := float64(x)+0.5, float64(y)+0.5
			cov := clamp01(0.5 - rrSDF(px, py, x0, y0, x1, y1, r))
			if cov > 0 {
				c.Blend(x, y, cov*opacity, f.at(px, py, x0, y0, x1, y1))
			}
		}
	}
}

// Box is a rounded rectangle with an optional border drawn inside its edge.
func (c *Canvas) Box(x0, y0, x1, y1, r float64, fill Fill, border *Fill, bw float64) {
	if border == nil || bw <= 0 {
		c.RoundRect(x0, y0, x1, y1, r, fill, 1)
		return
	}
	c.RoundRect(x0, y0, x1, y1, r, *border, 1)
	c.RoundRect(x0+bw, y0+bw, x1-bw, y1-bw, math.Max(0, r-bw), fill, 1)
}

// SoftShadow paints a blurred shadow under a rounded rectangle.
func (c *Canvas) SoftShadow(x0, y0, x1, y1, r, blur, dy float64, col RGB, alpha float64) {
	xa, xb := c.span(x0-blur-1, x1+blur+1, c.W)
	ya, yb := c.span(y0-blur+dy-1, y1+blur+dy+1, c.H)
	for y := ya; y < yb; y++ {
		for x := xa; x < xb; x++ {
			d := rrSDF(float64(x)+0.5, float64(y)+0.5-dy, x0, y0, x1, y1, r)
			if a := alpha * (1 - smoothstep(-blur*0.35, blur, d)); a > 0.002 {
				c.Blend(x, y, a, col)
			}
		}
	}
}

// Disc fills a circle.
func (c *Canvas) Disc(cx, cy, rad float64, col RGB, opacity float64) {
	c.RoundRect(cx-rad, cy-rad, cx+rad, cy+rad, rad, Solid(col), opacity)
}

// segSDF is the distance from p to the segment a-b.
func segSDF(px, py, ax, ay, bx, by float64) float64 {
	vx, vy := bx-ax, by-ay
	wx, wy := px-ax, py-ay
	l := vx*vx + vy*vy
	t := 0.0
	if l > 0 {
		t = clamp01((wx*vx + wy*vy) / l)
	}
	return math.Hypot(wx-vx*t, wy-vy*t)
}

// Polyline strokes connected segments with round caps and joins.
func (c *Canvas) Polyline(pts [][2]float64, width float64, col RGB) {
	if len(pts) < 2 {
		return
	}
	minX, minY, maxX, maxY := math.Inf(1), math.Inf(1), math.Inf(-1), math.Inf(-1)
	for _, p := range pts {
		minX, minY = math.Min(minX, p[0]), math.Min(minY, p[1])
		maxX, maxY = math.Max(maxX, p[0]), math.Max(maxY, p[1])
	}
	hw := width / 2
	xa, xb := c.span(minX-hw-1, maxX+hw+1, c.W)
	ya, yb := c.span(minY-hw-1, maxY+hw+1, c.H)
	for y := ya; y < yb; y++ {
		for x := xa; x < xb; x++ {
			px, py := float64(x)+0.5, float64(y)+0.5
			d := math.Inf(1)
			for i := 1; i < len(pts); i++ {
				d = math.Min(d, segSDF(px, py, pts[i-1][0], pts[i-1][1], pts[i][0], pts[i][1]))
			}
			if cov := clamp01(hw - d + 0.5); cov > 0 {
				c.Blend(x, y, cov, col)
			}
		}
	}
}
