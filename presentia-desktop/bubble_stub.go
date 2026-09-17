//go:build !windows

package main

// Stubs for non-Windows platforms.
func OpenFloatingBubble(a *App) {}
func CloseFloatingBubble()      {}
