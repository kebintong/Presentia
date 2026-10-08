//go:build windows

package main

import (
	"math"
	"os"
	"path/filepath"
	"syscall"
	"unsafe"
)

// ── Drawing helpers shared by the bubble, the tray and the Live View ─────────
//
// This file used to hold the bubble's speed-dial (seven round buttons around
// the logo). The bubble is now a capsule with a panel (bubble_panel_win.go,
// drawn by internal/bubbleui); what is left here are the small Win32 drawing
// helpers the other floating windows still use.

type rgb struct{ r, g, b byte }

func (c rgb) colorref() uintptr { return uintptr(c.r) | uintptr(c.g)<<8 | uintptr(c.b)<<16 }

func mix(a, b rgb, t float64) rgb {
	l := func(x, y byte) byte { return byte(float64(x) + (float64(y)-float64(x))*t + 0.5) }
	return rgb{l(a.r, b.r), l(a.g, b.g), l(a.b, b.b)}
}

const (
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

// ── Fonts ────────────────────────────────────────────────────────────────────

var (
	bFaceSegoeUI = syscall.StringToUTF16Ptr("Segoe UI")
	bFaceIcons   *uint16 // resolved once: Fluent on Windows 11, MDL2 on 10
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

func textExtent(hdc uintptr, s string) (float64, float64) {
	u, _ := syscall.UTF16FromString(s)
	var sz bSIZE
	bGetTextExtentPoint32W.Call(hdc, uintptr(unsafe.Pointer(&u[0])), uintptr(len(u)-1),
		uintptr(unsafe.Pointer(&sz)))
	return float64(sz.Cx), float64(sz.Cy)
}

// ── Small maths ──────────────────────────────────────────────────────────────

func clamp01(v float64) float64 { return math.Max(0, math.Min(1, v)) }

func smoothstep(a, b, x float64) float64 {
	t := clamp01((x - a) / (b - a))
	return t * t * (3 - 2*t)
}

// span returns the pixel range [lo, hi) covering c±r.
func span(c, r float64, limit int32) (int, int) {
	lo := int(math.Max(0, math.Floor(c-r)))
	hi := int(math.Min(float64(limit), math.Ceil(c+r)))
	return lo, hi
}
