package main

import (
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
