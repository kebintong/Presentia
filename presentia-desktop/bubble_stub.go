//go:build !windows

package main

// Stubs for non-Windows platforms.
func OpenFloatingBubble(a *App)      {}
func CloseFloatingBubble()           {}
func setBubbleThemeNative(dark bool) {}
func setBubbleStyleNative(iri bool)  {}

func openPipNative(a *App, title string) bool                 { return false }
func closePipNative()                                         {}
func pipFrameNative(b64 string)                               {}
func pipStatusNative(badge, count, msg, idleText string)      {}
func bubbleTaskDone()                                         {}
func setHideFromCaptureNative(hide bool)                      {}
func bubbleSourcePicked(text string)                          {}
func setBubbleThemeKeyNative(key string)                      {}
func setBubbleSourceNative(kind, label, detail, title string) {}
func bubbleIsOpenNative() bool                                { return false }
func setBubbleAnimationsNative(on bool)                       {}
