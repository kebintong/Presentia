package bubbleui

import (
	"math"
	"strconv"
	"strings"
)

// MarkPath is the Presentia mark (viewBox 0 0 66.26 100), the same path as
// frontend/src/components/BrandTile.tsx.
const MarkPath = "M26.97 99.95C26.25 99.83 25.60 99.43 24.96 98.74C24.72 98.48 19.15 92.34 12.58 85.09C4.67 76.36 0.59 71.82 0.47 71.62C0.37 71.45 0.23 71.14 0.16 70.92L0.02 70.53L0.02 52.12L0.02 33.72L0.17 33.28C0.44 32.45 1.06 31.72 1.83 31.35C2.62 30.96 2.21 30.99 7.82 30.97L12.84 30.95L13.34 31.17C17.91 33.13 21.83 35.53 24.06 37.73C26.26 39.91 28.56 43.60 30.63 48.29L30.86 48.81L30.86 72.98C30.86 96.88 30.86 97.16 30.76 97.53C30.35 99.16 28.67 100.23 26.97 99.95ZM37.85 67.82C36.70 67.52 35.82 66.64 35.46 65.45C35.38 65.18 35.38 64.66 35.36 57.05L35.35 48.94L35.66 48.24C37.73 43.57 39.97 39.96 42.14 37.79C43.55 36.39 45.62 34.91 48.17 33.50C49.12 32.97 51.36 31.86 52.43 31.38L53.28 31.01L58.32 31.01C63.17 31.01 63.38 31.01 63.74 31.10C64.99 31.42 65.93 32.40 66.19 33.67C66.25 34.01 66.26 34.49 66.25 37.50C66.23 41.33 66.25 41.14 65.88 41.89C65.71 42.25 65.50 42.47 53.15 54.82L40.60 67.37L40.19 67.57C39.96 67.68 39.64 67.81 39.48 67.85C39.07 67.95 38.30 67.94 37.85 67.82ZM2.53 26.39C1.40 26.12 0.47 25.21 0.11 24.05C0.03 23.77 0.02 23.31 0.02 13.23L0.02 2.71L0.16 2.32C0.34 1.79 0.60 1.37 0.98 0.98C1.37 0.60 1.79 0.34 2.32 0.16L2.71 0.02L15.26 0.01C29.37 -0 28.23 -0.03 29.05 0.37C29.82 0.75 30.40 1.41 30.70 2.25L30.83 2.61L30.85 5.69L30.86 8.77L30.49 9.60C28.35 14.36 25.98 18.09 23.77 20.13C21.56 22.18 18.19 24.24 14 26.12L13.22 26.46L8.02 26.46C3.81 26.46 2.76 26.44 2.53 26.39ZM52.15 26.07C47.58 23.98 44.23 21.87 42.14 19.78C39.97 17.60 37.77 14.07 35.75 9.53L35.35 8.63L35.36 5.72C35.38 2.47 35.37 2.58 35.74 1.83C36.11 1.06 36.84 0.44 37.68 0.17L38.11 0.02L50.81 0.02L63.50 0.02L63.87 0.14C64.35 0.29 64.62 0.43 64.98 0.72C65.52 1.15 65.90 1.70 66.12 2.37L66.24 2.71L66.25 13.06C66.26 24.72 66.29 23.81 65.89 24.65C65.52 25.41 64.85 26 64.01 26.31L63.65 26.44L58.33 26.45L53 26.46L52.15 26.07Z"

// MarkW and MarkH are the mark's viewBox size.
const (
	MarkW = 66.26
	MarkH = 100.0
)

// ParsePath turns an absolute M/L/C/Z SVG path into closed polygons
// (curves flattened into short segments).
func ParsePath(d string) [][][2]float64 {
	var polys [][][2]float64
	var cur [][2]float64
	var x, y float64
	toks := tokenize(d)
	cmd := byte(0)
	num := func(i *int) float64 {
		v, _ := strconv.ParseFloat(toks[*i], 64)
		*i++
		return v
	}
	for i := 0; i < len(toks); {
		t := toks[i]
		if len(t) == 1 && strings.ContainsAny(t, "MLCZmlcz") {
			cmd = t[0]
			i++
			if cmd == 'Z' || cmd == 'z' {
				if len(cur) > 2 {
					polys = append(polys, cur)
				}
				cur = nil
			}
			continue
		}
		switch cmd {
		case 'M':
			if len(cur) > 2 {
				polys = append(polys, cur)
			}
			x, y = num(&i), num(&i)
			cur = [][2]float64{{x, y}}
			cmd = 'L' // further pairs are line-tos
		case 'L':
			x, y = num(&i), num(&i)
			cur = append(cur, [2]float64{x, y})
		case 'C':
			x1, y1, x2, y2 := num(&i), num(&i), num(&i), num(&i)
			nx, ny := num(&i), num(&i)
			const steps = 8
			for k := 1; k <= steps; k++ {
				u := float64(k) / steps
				v := 1 - u
				px := v*v*v*x + 3*v*v*u*x1 + 3*v*u*u*x2 + u*u*u*nx
				py := v*v*v*y + 3*v*v*u*y1 + 3*v*u*u*y2 + u*u*u*ny
				cur = append(cur, [2]float64{px, py})
			}
			x, y = nx, ny
		default:
			i++ // unsupported: skip
		}
	}
	if len(cur) > 2 {
		polys = append(polys, cur)
	}
	return polys
}

func tokenize(d string) []string {
	var out []string
	var b strings.Builder
	flush := func() {
		if b.Len() > 0 {
			out = append(out, b.String())
			b.Reset()
		}
	}
	for _, r := range d {
		switch {
		case strings.ContainsRune("MLCZmlcz", r):
			flush()
			out = append(out, string(r))
		case r == ' ' || r == ',' || r == '\n' || r == '\t':
			flush()
		case r == '-' && b.Len() > 0:
			flush()
			b.WriteRune(r)
		default:
			b.WriteRune(r)
		}
	}
	flush()
	return out
}

var markPolys = ParsePath(MarkPath)

// Mark fills the Presentia mark into the box (x0,y0)-(x0+w,y0+h), keeping
// its proportions and centring it. The coverage is cached per size, since
// the bubble redraws every half second.
func (c *Canvas) Mark(x0, y0, w, h float64, col RGB) {
	k := math.Min(w/MarkW, h/MarkH)
	ox := x0 + (w-MarkW*k)/2
	oy := y0 + (h-MarkH*k)/2
	ix, iy := math.Floor(ox), math.Floor(oy)
	m := markMask(k, ox-ix, oy-iy)
	for y := 0; y < m.h; y++ {
		for x := 0; x < m.w; x++ {
			if a := m.a[y*m.w+x]; a > 0 {
				c.Blend(int(ix)+x, int(iy)+y, a, col)
			}
		}
	}
}

type mask struct {
	w, h int
	a    []float64
}

var markCache = map[[3]int]*mask{}

func markMask(k, fx, fy float64) *mask {
	key := [3]int{int(math.Round(k * 1000)), int(math.Round(fx * 8)), int(math.Round(fy * 8))}
	if m, ok := markCache[key]; ok {
		return m
	}
	w, h := int(math.Ceil(MarkW*k+fx))+1, int(math.Ceil(MarkH*k+fy))+1
	tmp := NewCanvas(w, h)
	polys := make([][][2]float64, len(markPolys))
	for i, p := range markPolys {
		q := make([][2]float64, len(p))
		for j, v := range p {
			q[j] = [2]float64{fx + v[0]*k, fy + v[1]*k}
		}
		polys[i] = q
	}
	tmp.FillPolys(polys, RGB{255, 255, 255})
	m := &mask{w: w, h: h, a: make([]float64, w*h)}
	for i := range m.a {
		m.a[i] = float64(tmp.px[i*4+3])
	}
	if len(markCache) > 64 {
		markCache = map[[3]int]*mask{}
	}
	markCache[key] = m
	return m
}

// FillPolys fills polygons (non-zero winding) with 4x4 supersampling.
func (c *Canvas) FillPolys(polys [][][2]float64, col RGB) {
	minX, minY, maxX, maxY := math.Inf(1), math.Inf(1), math.Inf(-1), math.Inf(-1)
	for _, p := range polys {
		for _, v := range p {
			minX, minY = math.Min(minX, v[0]), math.Min(minY, v[1])
			maxX, maxY = math.Max(maxX, v[0]), math.Max(maxY, v[1])
		}
	}
	xa, xb := c.span(minX, maxX, c.W)
	ya, yb := c.span(minY, maxY, c.H)
	const n = 4
	for y := ya; y < yb; y++ {
		for x := xa; x < xb; x++ {
			hit := 0
			for sy := 0; sy < n; sy++ {
				py := float64(y) + (float64(sy)+0.5)/n
				for sx := 0; sx < n; sx++ {
					px := float64(x) + (float64(sx)+0.5)/n
					if winding(polys, px, py) != 0 {
						hit++
					}
				}
			}
			if hit > 0 {
				c.Blend(x, y, float64(hit)/(n*n), col)
			}
		}
	}
}

func winding(polys [][][2]float64, px, py float64) int {
	w := 0
	for _, p := range polys {
		for i := range p {
			a, b := p[i], p[(i+1)%len(p)]
			if a[1] <= py {
				if b[1] > py && cross(a, b, px, py) > 0 {
					w++
				}
			} else if b[1] <= py && cross(a, b, px, py) < 0 {
				w--
			}
		}
	}
	return w
}

func cross(a, b [2]float64, px, py float64) float64 {
	return (b[0]-a[0])*(py-a[1]) - (px-a[0])*(b[1]-a[1])
}
