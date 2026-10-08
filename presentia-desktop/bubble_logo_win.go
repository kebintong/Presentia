//go:build windows

package main

import (
	"image"
	"math"
	"unsafe"
)

// ── Layered-window bitmaps ───────────────────────────────────────────────────
//
// The floating windows need per-pixel alpha, which GDI painting cannot give,
// so each is fed a premultiplied 32-bit bitmap through UpdateLayeredWindow
// instead of WM_PAINT.

const bLogoScale = 4 // the largest display scale the floating windows are drawn for

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
