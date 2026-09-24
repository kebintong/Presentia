//go:build !windows

package main

// superviseChild is a no-op outside Windows; process groups already handle
// this case on Unix-like systems.
func superviseChild(pid int) {}

// killPortOwner is Windows-only; on other systems the sidecar's own parent
// watchdog makes leftovers exit by themselves.
func killPortOwner(port uint16) {}
