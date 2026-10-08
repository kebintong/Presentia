//go:build windows

package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

// ── Live monitoring while in bubble mode ─────────────────────────────────────
//
// With the bubble up, the main window sits hidden in the tray, so everything
// here talks to the Python sidecar directly instead of going through the
// webview:
//
//   - a poller reads /api/monitor/live twice a second and feeds the bubble
//     (capsule and panel), the tray tooltip and the Live View pop-out;
//   - while the Live View is open, a second loop pulls the annotated preview
//     from /api/monitor/frame (only when there is a newer frame).
//
// The Live View is the same resizable pop-out window as the Register page's
// Live Face viewer (pip_win.go), in its "monitor" mode.

type liveStats struct {
	Active    bool    `json:"active"`
	Name      string  `json:"name"`
	Elapsed   float64 `json:"elapsed"`
	Present   int     `json:"present"`
	Missing   int     `json:"missing"`
	Waiting   int     `json:"waiting"`
	Total     int     `json:"total"`
	Unknown   int     `json:"unknown"`
	LastAlert string  `json:"last_alert"`
	LastLevel string  `json:"last_level"`
	FrameSeq  int64   `json:"frame_seq"`
	// Students recognised earlier but not on camera now, longest first.
	Away []struct {
		Name string  `json:"name"`
		Away float64 `json:"away"`
	} `json:"away"`
}

const bWmAppLive = 0x8000 + 3 // WM_APP+3: new live stats for the bubble thread

var (
	liveHTTP = &http.Client{Timeout: 2 * time.Second}

	gLiveMu        sync.Mutex
	gLive          liveStats
	gLiveStop      chan struct{}
	gLiveDismissed bool        // user closed the Live View during this run
	gLiveFrames    atomic.Bool // the frame loop is running
)

func liveSnapshot() liveStats {
	gLiveMu.Lock()
	defer gLiveMu.Unlock()
	return gLive
}

// startLivePoller runs until stopLivePoller; called when the bubble opens.
func startLivePoller() {
	stop := make(chan struct{})
	gLiveMu.Lock()
	if gLiveStop != nil {
		close(gLiveStop)
	}
	gLiveStop = stop
	gLive = liveStats{}
	gLiveDismissed = false
	gLiveMu.Unlock()
	go livePollLoop(stop)
}

func stopLivePoller() {
	gLiveMu.Lock()
	if gLiveStop != nil {
		close(gLiveStop)
		gLiveStop = nil
	}
	gLive = liveStats{}
	gLiveMu.Unlock()
}

func fetchLive() (liveStats, bool) {
	var st liveStats
	resp, err := liveHTTP.Get(sidecarURL + "/api/monitor/live")
	if err != nil {
		return st, false
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return st, false
	}
	if err := json.NewDecoder(resp.Body).Decode(&st); err != nil {
		return st, false
	}
	return st, true
}

func livePollLoop(stop chan struct{}) {
	tick := time.NewTicker(500 * time.Millisecond)
	defer tick.Stop()
	prevActive := false
	for {
		st, _ := fetchLive() // unreachable sidecar = inactive
		select {
		case <-stop:
			return
		default:
		}
		gLiveMu.Lock()
		gLive = st
		if st.Active && !prevActive {
			gLiveDismissed = false // a new run: offer the Live View again
		}
		dismissed := gLiveDismissed
		gLiveMu.Unlock()

		postToBubble(bWmAppLive)
		trayTip(trayTipText(st))
		liveUpdatePip(st)

		// Monitoring just started: pop the Live View open once.
		if st.Active && !prevActive && !dismissed {
			if open, _ := pipIsOpen(); !open {
				openLiveView()
			}
		}
		prevActive = st.Active

		select {
		case <-stop:
			return
		case <-tick.C:
		}
	}
}

// ── Formatting ───────────────────────────────────────────────────────────────

func fmtElapsed(sec float64) string {
	t := int(sec)
	if t >= 3600 {
		return fmt.Sprintf("%d:%02d:%02d", t/3600, t/60%60, t%60)
	}
	return fmt.Sprintf("%d:%02d", t/60, t%60)
}

// liveSegs is the coloured stats line shared by the chip and the Live View.
func liveSegs(st liveStats) []pipSeg {
	segs := []pipSeg{{fmt.Sprintf("%d/%d present", st.Present, st.Total), segPresent}}
	if st.Missing > 0 {
		segs = append(segs, pipSeg{fmt.Sprintf("%d missing", st.Missing), segMissing})
	}
	if st.Unknown > 0 {
		segs = append(segs, pipSeg{fmt.Sprintf("%d unknown", st.Unknown), segUnknown})
	}
	return segs
}

func trayTipText(st liveStats) string {
	if !st.Active {
		return "Presentia — bubble active"
	}
	t := fmt.Sprintf("Presentia — monitoring %s · %d/%d present", fmtElapsed(st.Elapsed), st.Present, st.Total)
	if st.Missing > 0 {
		t += fmt.Sprintf(" · %d missing", st.Missing)
	}
	if st.Unknown > 0 {
		t += fmt.Sprintf(" · %d unknown", st.Unknown)
	}
	return t
}

// ── Live View (monitor-mode pop-out) ─────────────────────────────────────────

func liveUpdatePip(st liveStats) {
	if open, src := pipIsOpen(); !open || src != pipSrcMonitor {
		return
	}
	if !st.Active {
		pipSetFrame(pipSrcMonitor, nil)
		pipSetStatus(pipSrcMonitor, "Idle", "", "Not monitoring",
			"Press Start Live Monitor on the bubble", nil)
		return
	}
	msg := st.LastAlert
	if msg == "" {
		msg = "Watching " + st.Name
	}
	pipSetStatus(pipSrcMonitor, "Live "+fmtElapsed(st.Elapsed), "", msg,
		"Waiting for the first frame…", liveSegs(st))
}

// openLiveView shows the monitoring preview in the resizable pop-out.
func openLiveView() {
	app := gBubbleApp
	if app == nil {
		return
	}
	pipUseSource(pipSrcMonitor, "Live Monitor", 400, 225) // 16:9 for screen captures
	if !pipOpen(app, "Live Monitor") {
		return
	}
	liveUpdatePip(liveSnapshot())
	if gLiveFrames.CompareAndSwap(false, true) {
		go liveFrameLoop()
	}
}

// toggleLiveView opens or closes the Live View (bubble dial, chip, tray).
func toggleLiveView() {
	if open, src := pipIsOpen(); open && src == pipSrcMonitor {
		closePipNative()
		return
	}
	gLiveMu.Lock()
	gLiveDismissed = false
	gLiveMu.Unlock()
	openLiveView()
}

// liveViewClosed is called from the pop-out thread when the Live View goes.
func liveViewClosed() {
	gLiveMu.Lock()
	gLiveDismissed = true
	gLiveMu.Unlock()
	pipUseSource(pipSrcPush, "Live Face", pipW, pipVideoH)
}

// liveFrameLoop pulls the newest annotated preview while the Live View is up.
func liveFrameLoop() {
	defer gLiveFrames.Store(false)
	var seq int64
	for {
		if open, src := pipIsOpen(); !open || src != pipSrcMonitor {
			return
		}
		resp, err := liveHTTP.Get(sidecarURL + "/api/monitor/frame?after=" + strconv.FormatInt(seq, 10))
		if err != nil {
			time.Sleep(500 * time.Millisecond)
			continue
		}
		if resp.StatusCode == http.StatusOK {
			raw, err := io.ReadAll(resp.Body)
			resp.Body.Close()
			if err == nil {
				if n, err := strconv.ParseInt(resp.Header.Get("X-Frame-Seq"), 10, 64); err == nil {
					seq = n
				}
				if img := decodeJPEG(raw); img != nil {
					pipSetFrame(pipSrcMonitor, img)
				}
			}
			time.Sleep(30 * time.Millisecond) // ~24 fps ceiling, like the sidecar preview
			continue
		}
		resp.Body.Close()
		time.Sleep(60 * time.Millisecond) // nothing newer yet
	}
}
