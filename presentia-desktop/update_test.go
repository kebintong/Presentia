package main

import (
	"strings"
	"testing"
	"time"
)

func TestTagFromLocation(t *testing.T) {
	ok := map[string]string{
		"https://github.com/kebintong/Presentia/releases/tag/v1.5.4":      "v1.5.4",
		"https://github.com/kebintong/Presentia/releases/tag/v2.0?x=1":    "v2.0",
		"https://github.com/kebintong/Presentia/releases/tag/1.6.0/extra": "1.6.0",
	}
	for loc, want := range ok {
		got, err := tagFromLocation(302, loc)
		if err != nil || got != want {
			t.Errorf("%s: got %q, %v; want %q", loc, got, err, want)
		}
	}
	bad := []struct {
		status int
		loc    string
	}{
		{200, "https://github.com/kebintong/Presentia/releases/tag/v1.5.4"},
		{302, ""},
		{302, "https://github.com/kebintong/Presentia/releases"},
		{302, "https://github.com/kebintong/Presentia/releases/tag/v1.5.4%22%3E"},
		{302, "https://github.com/kebintong/Presentia/releases/tag/nightly"},
	}
	for _, b := range bad {
		if tag, err := tagFromLocation(b.status, b.loc); err == nil {
			t.Errorf("%d %s: accepted %q", b.status, b.loc, tag)
		}
	}
}

func TestCacheFresh(t *testing.T) {
	at := func(d time.Duration) UpdateInfo {
		return UpdateInfo{CheckedAt: time.Now().Add(d).Format(time.RFC3339)}
	}
	if !cacheFresh(at(-time.Hour)) {
		t.Error("an hour-old check should still be fresh")
	}
	if cacheFresh(at(-updateCheckInterval - time.Minute)) {
		t.Error("a check older than the interval is stale")
	}
	if cacheFresh(at(48 * time.Hour)) {
		t.Error("a check dated in the future (clock changed) is stale")
	}
	if cacheFresh(UpdateInfo{}) {
		t.Error("no date is stale")
	}
}

func TestReevaluate(t *testing.T) {
	got := reevaluate(UpdateInfo{Latest: AppVersion})
	if got.Available {
		t.Error("the running version is not an update")
	}
	if !reevaluate(UpdateInfo{Latest: "99.0.0"}).Available {
		t.Error("a higher version is an update")
	}
}

func TestInfoFromReleases(t *testing.T) {
	rel := func(tag, body string, draft, pre bool) ghRelease {
		r := ghRelease{TagName: tag, Body: body, Draft: draft, Prerelease: pre}
		r.HTMLURL = "https://github.com/x/y/releases/tag/" + tag
		return r
	}
	rels := []ghRelease{
		rel("v1.4.0", "four", false, false),
		rel("v1.6.0", "draft", true, false),
		rel("v1.5.10", "ten", false, false),
		rel("v1.5.9", "nine", false, false),
		rel("v1.7.0-beta", "beta", false, true),
		rel("nightly", "nightly", false, false),
	}
	rels[2].Assets = append(rels[2].Assets, struct {
		Name string `json:"name"`
		URL  string `json:"browser_download_url"`
	}{"PresentiaSetup.exe", "https://github.com/x/y/releases/download/v1.5.10/PresentiaSetup.exe"})

	info := infoFromReleases(rels)
	if info.Latest != "1.5.10" || info.Notes != "ten" {
		t.Fatalf("newest should be 1.5.10 (not the draft or beta), got %q", info.Latest)
	}
	if info.URL != "https://github.com/x/y/releases/download/v1.5.10/PresentiaSetup.exe" {
		t.Errorf("installer link: %q", info.URL)
	}
	var got []string
	for _, r := range info.Releases {
		got = append(got, r.Version)
	}
	if strings.Join(got, ",") != "1.5.10,1.5.9,1.4.0" {
		t.Errorf("releases newest first, published only: %v", got)
	}
}

func TestReevaluateTrimsNotes(t *testing.T) {
	info := UpdateInfo{Latest: "99.0.2", Releases: []ReleaseNote{
		{Version: "99.0.2"}, {Version: "99.0.1"}, {Version: AppVersion}, {Version: "0.0.1"},
	}}
	got := reevaluate(info)
	if len(got.Releases) != 2 || got.Releases[0].Version != "99.0.2" || got.Releases[1].Version != "99.0.1" {
		t.Errorf("only versions newer than the running one: %+v", got.Releases)
	}
	if len(info.Releases) != 4 {
		t.Error("the cached list itself must stay complete")
	}
	if got.Installed == nil || got.Installed.Version != AppVersion {
		t.Errorf("the running version's notes are kept for Settings → Updates: %+v", got.Installed)
	}
	if reevaluate(UpdateInfo{Releases: []ReleaseNote{{Version: "0.0.1"}}}).Installed != nil {
		t.Error("no notes when the running version is not in the list")
	}
}

func TestPageURL(t *testing.T) {
	r := ghRelease{TagName: "v9.9.9", HTMLURL: "https://github.com/x/y/releases/tag/v9.9.9"}
	if got := infoFromReleases([]ghRelease{r}).PageURL; got != r.HTMLURL {
		t.Errorf("PageURL = %q", got)
	}
}
