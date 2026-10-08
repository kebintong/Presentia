package bubbleui

// Theme is one of the app's six looks (Settings → Appearance), as colours and
// shapes for the bubble. Values mirror frontend/src/style.css and themes.css.
type Theme struct {
	Key string

	Panel   Fill
	Border  *Fill
	BorderW float64 // logical px
	Radius  float64 // panel corner radius; the capsule is a pill unless Square
	Square  bool    // Editorial Grid: no rounding anywhere

	// Shadow: a soft blur, or a hard offset block (New Brutalism).
	HardShadow  bool
	ShadowCol   RGB
	ShadowAlpha float64

	Ink, Muted RGB
	Divider    RGB

	Sub       Fill // stat tiles, rows, secondary buttons
	SubBorder *Fill
	SubBW     float64

	Accent   Fill // primary button
	OnAccent RGB

	Tile       Fill // logo tile
	Mark       RGB
	TileShadow *RGB // New Brutalism: a small yellow offset under the tile

	HeaderBand *Fill // New Brutalism: yellow header

	OK, Warn, Danger, Icon RGB
	Stat                   [4]*Fill // per stat tile background (nil = Sub)
	W1Bg, W1Fg, W2Bg, W2Fg RGB
	OutlinedChips          bool
}

func fp(f Fill) *Fill { return &f }
func cp(c RGB) *RGB   { return &c }

var iriSpectrum = []RGB{Hex(0x97DEF0), Hex(0xEFEEC6), Hex(0xC888F9), Hex(0xCBB9F6), Hex(0xE5D5ED)}

// Themes by key (the frontend's theme keys: light, brutal, editorial, bento, iri, dark).
var Themes = map[string]Theme{
	"light": {
		Key: "light", Panel: Solid(Hex(0xFFFFFF)), Border: fp(Solid(Hex(0xE2E8F0))), BorderW: 1, Radius: 16,
		ShadowCol: Hex(0x0F172A), ShadowAlpha: 0.20,
		Ink: Hex(0x0F172A), Muted: Hex(0x5A6779), Divider: Hex(0xE2E8F0),
		Sub: Solid(Hex(0xF1F5F9)), SubBorder: fp(Solid(Hex(0xE2E8F0))), SubBW: 1,
		Accent: Solid(Hex(0x42D4F9)), OnAccent: Hex(0x062530),
		Tile: Solid(Hex(0x0C0C0C)), Mark: Hex(0xFFFFFF),
		OK: Hex(0x059669), Warn: Hex(0xD97706), Danger: Hex(0xDC2626), Icon: Hex(0x0E7490),
		W1Bg: Hex(0xFEF3C7), W1Fg: Hex(0xB45309), W2Bg: Hex(0xFFEDD5), W2Fg: Hex(0xC2410C),
	},
	"brutal": {
		Key: "brutal", Panel: Solid(Hex(0xFFFFFF)), Border: fp(Solid(Hex(0x0F172A))), BorderW: 2, Radius: 14,
		HardShadow: true, ShadowCol: Hex(0x0F172A), ShadowAlpha: 1,
		Ink: Hex(0x0F172A), Muted: Hex(0x475569), Divider: Hex(0x0F172A),
		Sub: Solid(Hex(0xFFFFFF)), SubBorder: fp(Solid(Hex(0x0F172A))), SubBW: 2,
		Accent: Solid(Hex(0x0E7490)), OnAccent: Hex(0xFFFFFF),
		Tile: Solid(Hex(0x0C0C0C)), Mark: Hex(0xFFFFFF), TileShadow: cp(Hex(0xFCD34D)),
		HeaderBand: fp(Solid(Hex(0xFDE68A))),
		OK:         Hex(0x047857), Warn: Hex(0xB45309), Danger: Hex(0xB91C1C), Icon: Hex(0x0F172A),
		Stat: [4]*Fill{fp(Solid(Hex(0xD1FAE5))), fp(Solid(Hex(0xFED7AA))), nil, nil},
		W1Bg: Hex(0xFDE68A), W1Fg: Hex(0x78350F), W2Bg: Hex(0xFED7AA), W2Fg: Hex(0x9A3412),
		OutlinedChips: true,
	},
	"editorial": {
		Key: "editorial", Panel: Solid(Hex(0xFFFFFF)), Border: fp(Solid(Hex(0x0A0A0A))), BorderW: 1.5, Radius: 0, Square: true,
		ShadowCol: Hex(0x000000), ShadowAlpha: 0.10,
		Ink: Hex(0x0A0A0A), Muted: Hex(0x525252), Divider: Hex(0x0A0A0A),
		Sub: Solid(Hex(0xFFFFFF)), SubBorder: fp(Solid(Hex(0x0A0A0A))), SubBW: 1,
		Accent: Solid(Hex(0x0A0A0A)), OnAccent: Hex(0xFFFFFF),
		Tile: Solid(Hex(0x0A0A0A)), Mark: Hex(0xFFFFFF),
		OK: Hex(0x047857), Warn: Hex(0xB45309), Danger: Hex(0xDC2626), Icon: Hex(0x0A0A0A),
		W1Bg: Hex(0xFFFFFF), W1Fg: Hex(0x0A0A0A), W2Bg: Hex(0x0A0A0A), W2Fg: Hex(0xFFFFFF),
		OutlinedChips: true,
	},
	"bento": {
		Key: "bento", Panel: Solid(Hex(0xFFFFFF)), Border: fp(Solid(Hex(0xD9DDF4))), BorderW: 1, Radius: 22,
		ShadowCol: Hex(0x4F46E5), ShadowAlpha: 0.22,
		Ink: Hex(0x1E1B4B), Muted: Hex(0x4B4F7A), Divider: Hex(0xE4E7F8),
		Sub: Solid(Hex(0xF4F5FD)), SubBorder: fp(Solid(Hex(0xE4E7F8))), SubBW: 1,
		Accent: Linear(180, Hex(0x6366F1), Hex(0x4F46E5)), OnAccent: Hex(0xFFFFFF),
		Tile: Linear(145, Hex(0x6366F1), Hex(0x22D3EE)), Mark: Hex(0xFFFFFF),
		OK: Hex(0x047857), Warn: Hex(0xC2410C), Danger: Hex(0xDC2626), Icon: Hex(0x4F46E5),
		Stat: [4]*Fill{fp(Solid(Hex(0xD3F5E6))), fp(Solid(Hex(0xFFE4D6))), fp(Solid(Hex(0xD6F0FF))), fp(Solid(Hex(0xEDE4FF)))},
		W1Bg: Hex(0xFEF3C7), W1Fg: Hex(0x92400E), W2Bg: Hex(0xFFE4D6), W2Fg: Hex(0xC2410C),
	},
	"iri": {
		Key: "iri", Panel: Solid(Hex(0x18151F)), Border: fp(Linear(110, iriSpectrum...)), BorderW: 1, Radius: 18,
		ShadowCol: Hex(0x000000), ShadowAlpha: 0.55,
		Ink: Hex(0xFFFFFF), Muted: Hex(0xA39CB5), Divider: Hex(0x2A2536),
		Sub: Solid(Hex(0x1F1B29)), SubBorder: fp(Solid(Hex(0x2A2536))), SubBW: 1,
		Accent: Linear(110, iriSpectrum...), OnAccent: Hex(0x1B1030),
		Tile: Linear(110, iriSpectrum...), Mark: Hex(0x1B1030),
		OK: Hex(0x6EE7B7), Warn: Hex(0xFCD34D), Danger: Hex(0xFCA5A5), Icon: Hex(0xC888F9),
		W1Bg: Hex(0x2E2A14), W1Fg: Hex(0xFDE68A), W2Bg: Hex(0x3B2A14), W2Fg: Hex(0xFDBA74),
	},
	"dark": {
		Key: "dark", Panel: Solid(Hex(0x16203A)), Border: fp(Solid(Hex(0x2E3B56))), BorderW: 1, Radius: 16,
		ShadowCol: Hex(0x000000), ShadowAlpha: 0.55,
		Ink: Hex(0xE8EDF6), Muted: Hex(0x93A1B8), Divider: Hex(0x26324A),
		Sub: Solid(Hex(0x0F1729)), SubBorder: fp(Solid(Hex(0x26324A))), SubBW: 1,
		Accent: Solid(Hex(0x42D4F9)), OnAccent: Hex(0x062530),
		Tile: Solid(Hex(0x42D4F9)), Mark: Hex(0x062530),
		OK: Hex(0x34D399), Warn: Hex(0xFBBF24), Danger: Hex(0xF87171), Icon: Hex(0x42D4F9),
		W1Bg: Hex(0x3A2F10), W1Fg: Hex(0xFCD34D), W2Bg: Hex(0x4A2A0A), W2Fg: Hex(0xFB923C),
	},
}

// ThemeKeys lists the six looks in the order of the Settings picker.
var ThemeKeys = []string{"light", "brutal", "editorial", "bento", "iri", "dark"}

// ThemeFor returns the theme for a key, Normal Dark if unknown.
func ThemeFor(key string) Theme {
	if t, ok := Themes[key]; ok {
		return t
	}
	return Themes["dark"]
}

// Dark tells whether the theme is a dark one (for windows that only know
// light and dark, like the Live View pop-out).
func (t Theme) Dark() bool { return t.Key == "dark" || t.Key == "iri" }
