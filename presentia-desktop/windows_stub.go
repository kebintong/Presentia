//go:build !windows

package main

// enumWindows is a no-op on non-Windows platforms.
func enumWindows() []WindowInfo {
	return []WindowInfo{}
}
