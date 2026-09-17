//go:build !windows

package main

// superviseChild is a no-op outside Windows; process groups already handle
// this case on Unix-like systems.
func superviseChild(pid int) {}
