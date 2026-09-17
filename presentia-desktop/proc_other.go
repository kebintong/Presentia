//go:build !windows

package main

import "os/exec"

// hideConsoleWindow is a no-op on platforms without console windows.
func hideConsoleWindow(cmd *exec.Cmd) {}
