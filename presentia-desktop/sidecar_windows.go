//go:build windows

package main

import (
	"path/filepath"
	"strings"
	"syscall"
	"unsafe"
)

// killPortOwner ends a leftover Presentia sidecar still listening on `port`
// from an earlier run (one started before the sidecar learned to exit with
// the app). Only a process that is recognisably ours is touched:
// presentia-sidecar.exe, or python in dev mode.
func killPortOwner(port uint16) {
	iphlp := syscall.NewLazyDLL("iphlpapi.dll")
	getTable := iphlp.NewProc("GetExtendedTcpTable")
	if getTable.Find() != nil {
		return
	}
	const afInet, tcpTableOwnerPidListener = 2, 3
	var size uint32
	getTable.Call(0, uintptr(unsafe.Pointer(&size)), 0, afInet, tcpTableOwnerPidListener, 0)
	if size == 0 {
		return
	}
	buf := make([]byte, size+1024)
	size = uint32(len(buf))
	if r, _, _ := getTable.Call(uintptr(unsafe.Pointer(&buf[0])), uintptr(unsafe.Pointer(&size)), 0,
		afInet, tcpTableOwnerPidListener, 0); r != 0 {
		return
	}
	// MIB_TCPTABLE_OWNER_PID: DWORD count, then rows of 6 DWORDs:
	// state, localAddr, localPort (network order), remoteAddr, remotePort, pid.
	n := *(*uint32)(unsafe.Pointer(&buf[0]))
	for i := uint32(0); i < n; i++ {
		off := 4 + int(i)*24
		if off+24 > len(buf) {
			break
		}
		row := (*[6]uint32)(unsafe.Pointer(&buf[off]))
		p := uint16(row[2]&0xFF)<<8 | uint16(row[2]>>8&0xFF)
		if p != port {
			continue
		}
		terminateIfSidecar(row[5])
	}
}

func terminateIfSidecar(pid uint32) {
	k32 := syscall.NewLazyDLL("kernel32.dll")
	openProcess := k32.NewProc("OpenProcess")
	queryName := k32.NewProc("QueryFullProcessImageNameW")
	terminate := k32.NewProc("TerminateProcess")
	closeHandle := k32.NewProc("CloseHandle")

	const queryLimited, processTerminate = 0x1000, 0x0001
	h, _, _ := openProcess.Call(queryLimited|processTerminate, 0, uintptr(pid))
	if h == 0 {
		return
	}
	defer closeHandle.Call(h)
	name := make([]uint16, 1024)
	n := uint32(len(name))
	if r, _, _ := queryName.Call(h, 0, uintptr(unsafe.Pointer(&name[0])), uintptr(unsafe.Pointer(&n))); r == 0 {
		return
	}
	exe := strings.ToLower(filepath.Base(syscall.UTF16ToString(name[:n])))
	if exe == "presentia-sidecar.exe" || strings.HasPrefix(exe, "python") {
		terminate.Call(h, 1)
	}
}
