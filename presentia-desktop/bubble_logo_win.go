//go:build windows

package main

import (
	"bytes"
	_ "embed"
	"image"
	"image/draw"
	"image/png"
	"math"
	"unsafe"
)

// ── Floating logo ────────────────────────────────────────────────────────────
//
// The bubble is the Presentia mark itself, floating over the desktop with a
// soft glow. It follows the app's look: the cyan mark in light mode, the
// white mark in dark mode, and the spectrum mark in the iridescent design.
// That needs per-pixel alpha, which GDI painting cannot give, so the window is
// fed a premultiplied 32-bit bitmap through UpdateLayeredWindow instead of
// WM_PAINT.
//
// The PNGs are rendered at 4x by assets/gen_bubble_logo.py and scaled down to
// the monitor's DPI here, so the logo stays crisp at any display scaling.

var (
	//go:embed assets/bubble_light.png
	logoLightPNG []byte
	//go:embed assets/bubble_light_active.png
	logoLightActivePNG []byte
	//go:embed assets/bubble_dark.png
	logoDarkPNG []byte
	//go:embed assets/bubble_dark_active.png
	logoDarkActivePNG []byte
	//go:embed assets/bubble_iridescent.png
	logoIriPNG []byte
	//go:embed assets/bubble_iridescent_active.png
	logoIriActivePNG []byte
)

const bLogoScale = 4 // the embedded PNGs are this many times their 96-DPI size

type bBITMAPINFOHEADER struct {
	Size          uint32
	Width         int32
	Height        int32
	Planes        uint16
	BitCount      uint16
	Compression   uint32
	SizeImage     uint32
	XPelsPerMeter int32
	YPelsPerMeter int32
	ClrUsed       uint32
	ClrImportant  uint32
}

// uiScale is the system DPI as a multiple of 96, clamped to what the embedded
// art can serve without upscaling.
func uiScale() float64 {
	dpi := uintptr(96)
	if bGetDpiForSystem.Find() == nil { // Windows 10 1607+
		if d, _, _ := bGetDpiForSystem.Call(); d != 0 {
			dpi = d
		}
	}
	s := float64(dpi) / 96
	return math.Max(1, math.Min(s, bLogoScale))
}

// logoSource picks the PNG for the current style and theme. Even indexes are
// the resting logo, odd ones the brighter halo shown while the dial is out.
func logoSource(active bool) (idx int, src []byte) {
	all := [...][]byte{
		logoLightPNG, logoLightActivePNG,
		logoDarkPNG, logoDarkActivePNG,
		logoIriPNG, logoIriActivePNG,
	}
	switch {
	case gIridescent:
		idx = 4
	case gDarkTheme:
		idx = 2
	}
	if active {
		idx++
	}
	return idx, all[idx]
}

// bubbleSize is the window size for the current style, in physical pixels.
func bubbleSize() (int32, int32) {
	_, src := logoSource(false)
	cfg, err := png.DecodeConfig(bytes.NewReader(src))
	if err != nil {
		return 56, 56
	}
	s := uiScale() / bLogoScale
	return int32(math.Round(float64(cfg.Width) * s)), int32(math.Round(float64(cfg.Height) * s))
}

// newDIB creates a top-down 32-bit DIB section and returns its pixel memory.
func newDIB(w, h int32) (uintptr, []byte) {
	bi := bBITMAPINFOHEADER{
		Size: uint32(unsafe.Sizeof(bBITMAPINFOHEADER{})), Width: w, Height: -h, // negative = top-down
		Planes: 1, BitCount: 32,
	}
	var bits unsafe.Pointer
	hbm, _, _ := bCreateDIBSection.Call(0, uintptr(unsafe.Pointer(&bi)), 0,
		uintptr(unsafe.Pointer(&bits)), 0, 0)
	if hbm == 0 || bits == nil {
		return 0, nil
	}
	return hbm, unsafe.Slice((*byte)(bits), int(w*h*4))
}

// pushLayered hands a premultiplied DIB to a layered window. A nil pos keeps
// the window where it is.
func pushLayered(hwnd, hbm uintptr, w, h int32, pos *bPOINT) {
	mem, _, _ := bCreateCompatibleDC.Call(0)
	if mem == 0 {
		return
	}
	old, _, _ := bSelectObject.Call(mem, hbm)
	size := bSIZE{w, h}
	var origin bPOINT
	blend := bBLENDFUNCTION{SourceConstantAlpha: 255, AlphaFormat: bAcSrcAlpha}
	bUpdateLayeredWindow.Call(hwnd, 0, uintptr(unsafe.Pointer(pos)), uintptr(unsafe.Pointer(&size)), mem,
		uintptr(unsafe.Pointer(&origin)), 0, uintptr(unsafe.Pointer(&blend)), bUlwAlpha)
	bSelectObject.Call(mem, old)
	bDeleteDC.Call(mem)
}

// Bitmaps are built once per variant (and DPI) and live for the process.
var gLogoBmp [6]struct {
	hbm  uintptr
	w, h int32
}

// logoBitmap returns a premultiplied DIB of the current logo at the current
// DPI; active is the brighter halo shown while the dial is open.
func logoBitmap(active bool) (uintptr, int32, int32) {
	i, src := logoSource(active)
	w, h := bubbleSize()
	if b := gLogoBmp[i]; b.hbm != 0 && b.w == w && b.h == h {
		return b.hbm, w, h
	}

	img, err := png.Decode(bytes.NewReader(src))
	if err != nil {
		return 0, 0, 0
	}
	// image.RGBA is alpha-premultiplied, which is exactly what
	// UpdateLayeredWindow expects.
	rgba := image.NewRGBA(img.Bounds())
	draw.Draw(rgba, rgba.Bounds(), img, img.Bounds().Min, draw.Src)
	px := downscale(rgba, int(w), int(h))

	hbm, dst := newDIB(w, h)
	if hbm == 0 {
		return 0, 0, 0
	}
	for p := 0; p < len(px); p += 4 { // RGBA -> BGRA
		dst[p], dst[p+1], dst[p+2], dst[p+3] = px[p+2], px[p+1], px[p], px[p+3]
	}

	if old := gLogoBmp[i].hbm; old != 0 {
		bDeleteObject.Call(old)
	}
	gLogoBmp[i].hbm, gLogoBmp[i].w, gLogoBmp[i].h = hbm, w, h
	return hbm, w, h
}

// downscale area-averages premultiplied RGBA pixels to w x h. Averaging
// premultiplied values is what keeps the glow's soft edge free of dark fringes.
func downscale(src *image.RGBA, w, h int) []byte {
	sw, sh := src.Bounds().Dx(), src.Bounds().Dy()
	type tap struct {
		i int
		w float64
	}
	taps := func(n, m int) [][]tap {
		r := float64(n) / float64(m)
		out := make([][]tap, m)
		for d := 0; d < m; d++ {
			a, b := float64(d)*r, float64(d+1)*r
			for s := int(a); s < n && float64(s) < b; s++ {
				if wt := math.Min(b, float64(s+1)) - math.Max(a, float64(s)); wt > 0 {
					out[d] = append(out[d], tap{s, wt / r})
				}
			}
		}
		return out
	}
	tx, ty := taps(sw, w), taps(sh, h)

	// Horizontal pass, then vertical.
	mid := make([]float64, w*sh*4)
	for y := 0; y < sh; y++ {
		row := src.Pix[y*src.Stride:]
		for x := 0; x < w; x++ {
			for _, t := range tx[x] {
				for c := 0; c < 4; c++ {
					mid[(y*w+x)*4+c] += float64(row[t.i*4+c]) * t.w
				}
			}
		}
	}
	out := make([]byte, w*h*4)
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			for c := 0; c < 4; c++ {
				var v float64
				for _, t := range ty[y] {
					v += mid[(t.i*w+x)*4+c] * t.w
				}
				out[(y*w+x)*4+c] = byte(math.Min(255, math.Round(v)))
			}
		}
	}
	return out
}

// updateLogo pushes the logo bitmap to the bubble window. Bubble thread only.
func updateLogo(hwnd uintptr) {
	if hbm, w, h := logoBitmap(gDialOpen); hbm != 0 {
		pushLayered(hwnd, hbm, w, h, nil)
	}
}

// applyBubbleStyle resizes the bubble for the current logo, keeping it
// centred where it was, and repaints it. Bubble thread only.
func applyBubbleStyle(hwnd uintptr) {
	var rc bRECT
	bGetWindowRect.Call(hwnd, uintptr(unsafe.Pointer(&rc)))
	w, h := bubbleSize()
	pos := bPOINT{(rc.Left+rc.Right)/2 - w/2, (rc.Top+rc.Bottom)/2 - h/2}
	if hbm, bw, bh := logoBitmap(gDialOpen); hbm != 0 {
		pushLayered(hwnd, hbm, bw, bh, &pos)
	}
}
