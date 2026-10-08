package bubbleui

import (
	"strings"
	"testing"
)

// boxText stands in for real fonts: fixed-width glyphs drawn as solid boxes.
type boxText struct{}

func (boxText) Width(f Font, s string) float64 { return float64(len([]rune(s))) * f.Px * 0.56 }
func (boxText) Draw(c *Canvas, f Font, s string, x0, y0, x1, y1 float64, align int, col RGB) {
	w := float64(len([]rune(s))) * f.Px * 0.56
	cy := (y0 + y1) / 2
	x := x0
	if align == Center {
		x = (x0+x1)/2 - w/2
	}
	c.RoundRect(x, cy-f.Px*0.35, x+w, cy+f.Px*0.35, 0, Solid(col), 1)
}

func ids(l *Layout) string {
	var out []string
	for _, h := range l.Hits {
		out = append(out, h.ID)
	}
	return strings.Join(out, ",")
}

func TestEveryThemeIsComplete(t *testing.T) {
	for _, k := range ThemeKeys {
		th, ok := Themes[k]
		if !ok || th.Key != k {
			t.Fatalf("theme %q missing or mislabelled", k)
		}
		if len(th.Panel.Stops) == 0 || len(th.Accent.Stops) == 0 || len(th.Tile.Stops) == 0 || len(th.Sub.Stops) == 0 {
			t.Fatalf("theme %q has an empty fill", k)
		}
		if th.Ink == th.Panel.First() {
			t.Fatalf("theme %q: ink is invisible on the panel", k)
		}
	}
	if ThemeFor("nope").Key != "dark" {
		t.Fatal("unknown theme should fall back to dark")
	}
}

func TestCapsuleStates(t *testing.T) {
	for _, k := range ThemeKeys {
		th := ThemeFor(k)
		idle := RenderCapsule(View{}, th, boxText{}, 1)
		ready := RenderCapsule(View{SourceKind: "area"}, th, boxText{}, 1)
		live := RenderCapsule(View{Active: true, Elapsed: 724, Present: 18, Total: 24, Missing: 2}, th, boxText{}, 1)
		for _, l := range []*Layout{idle, ready, live} {
			if ids(l) != "capsule" {
				t.Fatalf("%s: capsule hits = %s", k, ids(l))
			}
			// The body is opaque in its middle, the corners of the canvas are not.
			cx, cy := int((l.BX0+l.BX1)/2), int((l.BY0+l.BY1)/2)
			if l.C.Alpha(cx, cy) < 0.99 {
				t.Fatalf("%s: capsule body not opaque", k)
			}
			if l.C.Alpha(0, 0) > 0.05 {
				t.Fatalf("%s: canvas corner should be transparent", k)
			}
		}
		if !(live.BX1-live.BX0 > idle.BX1-idle.BX0) {
			t.Fatalf("%s: live capsule should be wider than idle", k)
		}
		// The live dot's pulse changes the picture but not the size.
		pulsed := RenderCapsule(View{Active: true, Elapsed: 724, Present: 18, Total: 24, Missing: 2, Pulse: 0.5}, th, boxText{}, 1)
		if pulsed.C.W != live.C.W || pulsed.C.H != live.C.H {
			t.Fatalf("%s: pulse must not resize the capsule", k)
		}
		// HiDPI doubles everything.
		big := RenderCapsule(View{}, th, boxText{}, 2)
		if big.C.W < idle.C.W*2-2 {
			t.Fatalf("%s: scale not applied", k)
		}
	}
}

func TestPanelControls(t *testing.T) {
	th := ThemeFor("dark")
	none := RenderPanel(View{}, th, boxText{}, 1)
	if ids(none) != "close,tab,area,window,app,hide" { // Start is disabled: no hit
		t.Fatalf("idle hits = %s", ids(none))
	}
	ready := RenderPanel(View{SourceKind: "window", SourceLabel: "Meet - CS101", SourceDetail: "Google Chrome"}, th, boxText{}, 1)
	if ids(ready) != "close,tab,area,window,start,app,hide" {
		t.Fatalf("ready hits = %s", ids(ready))
	}
	away := []Student{{"Juan Dela Cruz", 130, true}, {"Ben Tan", 65, false}, {"A", 1, false}, {"B", 2, false},
		{"C", 3, false}, {"D", 4, false}}
	live := RenderPanel(View{Active: true, Elapsed: 724, Present: 18, Total: 24, Missing: 6, Away: away}, th, boxText{}, 1)
	want := "close,copy:0,copy:1,copy:2,copy:3,liveview,stop,app,hide"
	if ids(live) != want {
		t.Fatalf("live hits = %s", ids(live))
	}
	// Hits are inside the visible body and do not overlap.
	for i, a := range live.Hits {
		if a.X0 < live.BX0 || a.X1 > live.BX1 || a.Y0 < live.BY0 || a.Y1 > live.BY1 {
			t.Fatalf("hit %s outside the panel", a.ID)
		}
		for _, b := range live.Hits[i+1:] {
			if a.X0 < b.X1 && b.X0 < a.X1 && a.Y0 < b.Y1 && b.Y0 < a.Y1 {
				t.Fatalf("hits %s and %s overlap", a.ID, b.ID)
			}
		}
	}
	h := live.Hits[1]
	if live.HitAt((h.X0+h.X1)/2, (h.Y0+h.Y1)/2) != "copy:0" {
		t.Fatal("HitAt misses the copy button")
	}
	// More rows make a taller panel; every theme lays out the same controls.
	short := RenderPanel(View{Active: true, Away: away[:1]}, th, boxText{}, 1)
	if !(live.BY1-live.BY0 > short.BY1-short.BY0) {
		t.Fatal("panel height should follow the list")
	}
	for _, k := range ThemeKeys {
		l := RenderPanel(View{Active: true, Away: away}, ThemeFor(k), boxText{}, 1.5)
		if ids(l) != want {
			t.Fatalf("%s: hits = %s", k, ids(l))
		}
	}
}

func TestGradientAndMark(t *testing.T) {
	f := Linear(90, RGB{0, 0, 0}, RGB{255, 255, 255})
	if l, r := f.at(0, 5, 0, 0, 100, 10), f.at(100, 5, 0, 0, 100, 10); l.R > 10 || r.R < 245 {
		t.Fatalf("90deg gradient goes left to right: %v %v", l, r)
	}
	if len(markPolys) != 4 {
		t.Fatalf("mark should have 4 shapes, got %d", len(markPolys))
	}
	c := NewCanvas(40, 60)
	c.Mark(0, 0, 40, 60, RGB{255, 255, 255})
	filled := 0
	for y := 0; y < c.H; y++ {
		for x := 0; x < c.W; x++ {
			if c.Alpha(x, y) > 0.5 {
				filled++
			}
		}
	}
	if share := float64(filled) / float64(c.W*c.H); share < 0.35 || share > 0.85 {
		t.Fatalf("mark coverage %.2f looks wrong", share)
	}
	for _, n := range []string{"chev-down", "chev-up", "area", "window", "tab", "eye", "copy", "pin", "app", "x", "camoff", "check", "play", "stop"} {
		if !HasIcon(n) {
			t.Fatalf("icon %s missing", n)
		}
	}
}

func TestWordsAndTimes(t *testing.T) {
	if Clock(65) != "1:05" || Clock(3725) != "1:02:05" || Clock(-3) != "0:00" {
		t.Fatal("Clock")
	}
	if m := CopyMessage("Juan Dela Cruz", 130); !strings.HasPrefix(m, "Hi Juan, please turn your camera on. Presentia shows it has been off for 2 minutes") {
		t.Fatal(m)
	}
	if m := CopyMessage("Ana", 70); !strings.Contains(m, "1 minute,") {
		t.Fatal(m)
	}
	// Face not seen (camera may be on): no claim that the camera is off.
	if m := CopyMessageFor("Kian Santos", 200, false); !strings.HasPrefix(m, "Hi Kian, we can't see your face") ||
		strings.Contains(m, "has been off") {
		t.Fatal(m)
	}
	if m := CopyMessageFor("Kian", 130, true); !strings.Contains(m, "has been off for 2 minutes") {
		t.Fatal(m)
	}
	if got := Ellipsize(boxText{}, Font{Px: 10}, "A very long class name indeed", 60); !strings.HasSuffix(got, "…") ||
		(boxText{}).Width(Font{Px: 10}, got) > 60 {
		t.Fatalf("Ellipsize = %q", got)
	}
	if warnLevel(59) != 0 || warnLevel(61) != 1 || warnLevel(500) != 4 {
		t.Fatal("warnLevel")
	}
}

func TestBGRAIsPremultiplied(t *testing.T) {
	c := NewCanvas(1, 1)
	c.Blend(0, 0, 0.5, RGB{200, 100, 0})
	b := make([]byte, 4)
	c.BGRA(b)
	if b[3] != 128 || b[2] != 100 || b[1] != 50 || b[0] != 0 {
		t.Fatalf("BGRA = %v", b)
	}
}
