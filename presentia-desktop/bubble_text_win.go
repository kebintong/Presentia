//go:build windows

package main

import (
	_ "embed"
	"math"
	"sync"
	"syscall"
	"unsafe"
)

// ── Smooth text for the floating windows ─────────────────────────────────────
//
// GDI's own anti-aliasing is poor at label sizes on a layered window (the
// "jagged" look). So text is drawn with GDI at ssK times its size into a
// scratch bitmap, box-filtered back down to a coverage mask, and painted into
// the window's pixels like any other shape. Labels use Poppins — the app's display font —
// embedded here and registered privately for this process.

const ssK = 4 // supersampling factor

var (
	//go:embed assets/fonts/PoppinsMedium.ttf
	poppinsTTF []byte

	bAddFontMemResourceEx = bGdi32.NewProc("AddFontMemResourceEx")

	bFacePoppins = syscall.StringToUTF16Ptr("Poppins Medium")
	gFontOnce    sync.Once
	gFontOK      bool
)

// labelFace registers the embedded Poppins once and returns its face name,
// falling back to Segoe UI if Windows refuses the font.
func labelFace() *uint16 {
	gFontOnce.Do(func() {
		if bAddFontMemResourceEx.Find() != nil || len(poppinsTTF) == 0 {
			return
		}
		var n uint32
		h, _, _ := bAddFontMemResourceEx.Call(uintptr(unsafe.Pointer(&poppinsTTF[0])),
			uintptr(len(poppinsTTF)), 0, uintptr(unsafe.Pointer(&n)))
		gFontOK = h != 0 && n > 0
	})
	if gFontOK {
		return bFacePoppins
	}
	return bFaceSegoeUI
}

// ssBuf is a scratch bitmap for supersampled text. Each UI thread (bubble,
// pop-out viewer) owns its own, so they never draw into each other's.
type ssBuf struct {
	bmp  uintptr
	px   []byte
	w, h int32
}

var gBubbleSS ssBuf // bubble thread only (capsule and panel)

// ensure makes sure the scratch DIB is at least w x h.
func (b *ssBuf) ensure(w, h int32) bool {
	if b.bmp != 0 && w <= b.w && h <= b.h {
		return true
	}
	if b.bmp != 0 {
		bDeleteObject.Call(b.bmp)
	}
	nw, nh := max(w, b.w), max(h, b.h)
	b.bmp, b.px = newDIB(nw, nh)
	if b.bmp == 0 {
		b.w, b.h = 0, 0
		return false
	}
	b.w, b.h = nw, nh
	return true
}

// ssDraw lays s out in the box (x0,y0)-(x1,y1) with DrawText flags, using a
// font created at ssK x its final size, and calls paint with the downsampled
// coverage of every touched pixel inside [0,clipW) x [0,clipH).
func ssDraw(b *ssBuf, s string, bigFont uintptr, x0, y0, x1, y1 float64, flags uintptr,
	clipW, clipH int32, paint func(x, y int32, a float64)) {
	if s == "" {
		return
	}
	ix0, iy0 := int32(math.Floor(x0)), int32(math.Floor(y0))
	ix1, iy1 := int32(math.Ceil(x1)), int32(math.Ceil(y1))
	w, h := ix1-ix0, iy1-iy0
	if w <= 0 || h <= 0 {
		return
	}
	bw, bh := w*ssK, h*ssK
	if !b.ensure(bw, bh) {
		return
	}
	stride := int(b.w) * 4
	for y := 0; y < int(bh); y++ {
		clear(b.px[y*stride : y*stride+int(bw)*4])
	}

	mem, _, _ := bCreateCompatibleDC.Call(0)
	if mem == 0 {
		return
	}
	oldBmp, _, _ := bSelectObject.Call(mem, b.bmp)
	oldFont, _, _ := bSelectObject.Call(mem, bigFont)
	bSetBkMode.Call(mem, bTransparent)
	bSetTextColor.Call(mem, 0xFFFFFF)
	rc := bRECT{
		int32(math.Round((x0 - float64(ix0)) * ssK)), int32(math.Round((y0 - float64(iy0)) * ssK)),
		int32(math.Round((x1 - float64(ix0)) * ssK)), int32(math.Round((y1 - float64(iy0)) * ssK)),
	}
	drawText(mem, s, &rc, flags)
	bSelectObject.Call(mem, oldFont)
	bSelectObject.Call(mem, oldBmp)
	bDeleteDC.Call(mem)
	bGdiFlush.Call()

	// Box-filter ssK x ssK blocks of the green channel (white text on black).
	const norm = 1.0 / (255.0 * ssK * ssK)
	for oy := int32(0); oy < h; oy++ {
		gy := iy0 + oy
		if gy < 0 || gy >= clipH {
			continue
		}
		for ox := int32(0); ox < w; ox++ {
			gx := ix0 + ox
			if gx < 0 || gx >= clipW {
				continue
			}
			sum := 0
			for sy := int32(0); sy < ssK; sy++ {
				row := int(oy*ssK+sy)*stride + int(ox*ssK)*4 + 1
				for sx := 0; sx < ssK; sx++ {
					sum += int(b.px[row+sx*4])
				}
			}
			if sum != 0 {
				paint(gx, gy, float64(sum)*norm)
			}
		}
	}
}

// textWidth measures s in a big (ssK x) font and returns overlay pixels.
func textWidth(bigFont uintptr, s string) float64 {
	mem, _, _ := bCreateCompatibleDC.Call(0)
	if mem == 0 {
		return 0
	}
	old, _, _ := bSelectObject.Call(mem, bigFont)
	w, _ := textExtent(mem, s)
	bSelectObject.Call(mem, old)
	bDeleteDC.Call(mem)
	return w / ssK
}
