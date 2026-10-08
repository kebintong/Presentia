package bubbleui

import "math"

// Icons are drawn as strokes on a 24 x 24 grid (like the app's line icons),
// so they look the same on every Windows version without an icon font.

type stroke [][2]float64

func rect(x0, y0, x1, y1 float64) stroke {
	return stroke{{x0, y0}, {x1, y0}, {x1, y1}, {x0, y1}, {x0, y0}}
}

func ellipse(cx, cy, rx, ry float64, n int) stroke {
	s := make(stroke, n+1)
	for i := 0; i <= n; i++ {
		a := 2 * math.Pi * float64(i) / float64(n)
		s[i] = [2]float64{cx + rx*math.Cos(a), cy + ry*math.Sin(a)}
	}
	return s
}

var iconStrokes = map[string][]stroke{
	"chev-down": {{{6, 9}, {12, 15}, {18, 9}}},
	"chev-up":   {{{6, 15}, {12, 9}, {18, 15}}},
	"x":         {{{6, 6}, {18, 18}}, {{18, 6}, {6, 18}}},
	"area": {
		{{3, 8}, {3, 3}, {8, 3}}, {{16, 3}, {21, 3}, {21, 8}},
		{{21, 16}, {21, 21}, {16, 21}}, {{8, 21}, {3, 21}, {3, 16}},
	},
	"window": {rect(2, 4, 22, 17), {{2, 8}, {22, 8}}, {{8, 21}, {16, 21}}, {{12, 17}, {12, 21}}},
	// a browser tab: a page with a tab on its top edge
	"tab":  {{{3, 20}, {3, 6}, {4, 5}, {10, 5}, {12, 8}, {20, 8}, {21, 9}, {21, 20}, {3, 20}}, {{3, 11}, {21, 11}}},
	"eye":  {ellipse(12, 12, 10, 6.5, 28), ellipse(12, 12, 3, 3, 14)},
	"copy": {rect(9, 9, 20, 20), {{5, 15}, {4, 15}, {4, 4}, {15, 4}, {15, 5}}},
	"pin":  {{{12, 17}, {12, 22}}, {{5, 17}, {19, 17}, {17, 13}, {17, 4}, {7, 4}, {7, 13}, {5, 17}}},
	"app":  {{{14, 3}, {21, 3}, {21, 10}}, {{10, 14}, {21, 3}}, {{18, 14}, {18, 20}, {4, 20}, {4, 6}, {10, 6}}},
	"camoff": {
		{{2, 2}, {22, 22}}, {{15, 15}, {15, 19}, {2, 19}, {2, 6}, {5, 6}},
		{{9, 6}, {15, 6}, {15, 12}}, {{16, 11}, {22, 7}, {22, 17}, {17, 14}},
	},
	"check":  {{{5, 12}, {10, 17}, {19, 7}}},
	"person": {ellipse(12, 8, 4, 4, 16), {{4, 21}, {4, 19}, {6, 16}, {9, 15}, {15, 15}, {18, 16}, {20, 19}, {20, 21}}},
	"wifi":   {{{2, 9}, {7, 5.5}, {12, 4.5}, {17, 5.5}, {22, 9}}, {{5.5, 12.5}, {9, 10.5}, {12, 10}, {15, 10.5}, {18.5, 12.5}}, {{9, 16}, {12, 14.5}, {15, 16}}},
}

// filled icons
var iconFills = map[string][][][2]float64{
	"play": {{{7, 4.5}, {19.5, 12}, {7, 19.5}}},
	"stop": {{{6, 6}, {18, 6}, {18, 18}, {6, 18}}},
}

// Icon draws a named icon in the square (x, y, size). width is the stroke
// width in device pixels.
func (c *Canvas) Icon(name string, x, y, size, width float64, col RGB) {
	k := size / 24
	if polys, ok := iconFills[name]; ok {
		out := make([][][2]float64, len(polys))
		for i, p := range polys {
			q := make([][2]float64, len(p))
			for j, v := range p {
				q[j] = [2]float64{x + v[0]*k, y + v[1]*k}
			}
			out[i] = q
		}
		c.FillPolys(out, col)
		return
	}
	for _, s := range iconStrokes[name] {
		pts := make([][2]float64, len(s))
		for i, v := range s {
			pts[i] = [2]float64{x + v[0]*k, y + v[1]*k}
		}
		c.Polyline(pts, width, col)
	}
}

// HasIcon reports whether name is a known icon (tests).
func HasIcon(name string) bool {
	_, a := iconStrokes[name]
	_, b := iconFills[name]
	return a || b
}
