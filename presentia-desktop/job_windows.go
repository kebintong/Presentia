//go:build windows

package main

import (
	"syscall"
	"unsafe"
)

// ─────────────────────────────────────────────────────────────────────────────
// Sidecar lifetime
//
// cmd.Process.Kill() only runs if our shutdown handler runs, and only kills the
// direct child. If Presentia is force-closed, crashes, or is ended from Task
// Manager, presentia-sidecar.exe survives — and because it lives inside the
// install folder it holds those files open, so the next install or uninstall
// fails with "file in use".
//
// A Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE fixes this at the OS
// level: when the last handle to the job closes — which Windows does for us
// however this process dies — every process in the job is terminated too.
// ─────────────────────────────────────────────────────────────────────────────

var (
	jKernel32                 = syscall.NewLazyDLL("kernel32.dll")
	jCreateJobObjectW         = jKernel32.NewProc("CreateJobObjectW")
	jSetInformationJobObject  = jKernel32.NewProc("SetInformationJobObject")
	jAssignProcessToJobObject = jKernel32.NewProc("AssignProcessToJobObject")
	jOpenProcess              = jKernel32.NewProc("OpenProcess")
	jCloseHandle              = jKernel32.NewProc("CloseHandle")
)

const (
	jJobObjectExtendedLimitInformation = 9
	jJobObjectLimitKillOnJobClose      = 0x00002000

	// PROCESS_SET_QUOTA | PROCESS_TERMINATE — the minimum needed to put a
	// process into a job.
	jProcessSetQuota  = 0x0100
	jProcessTerminate = 0x0001
)

type jIoCounters struct {
	ReadOperationCount  uint64
	WriteOperationCount uint64
	OtherOperationCount uint64
	ReadTransferCount   uint64
	WriteTransferCount  uint64
	OtherTransferCount  uint64
}

type jBasicLimitInformation struct {
	PerProcessUserTimeLimit int64
	PerJobUserTimeLimit     int64
	LimitFlags              uint32
	MinimumWorkingSetSize   uintptr
	MaximumWorkingSetSize   uintptr
	ActiveProcessLimit      uint32
	Affinity                uintptr
	PriorityClass           uint32
	SchedulingClass         uint32
}

type jExtendedLimitInformation struct {
	BasicLimitInformation jBasicLimitInformation
	IoInfo                jIoCounters
	ProcessMemoryLimit    uintptr
	JobMemoryLimit        uintptr
	PeakProcessMemoryUsed uintptr
	PeakJobMemoryUsed     uintptr
}

// gJobHandle is deliberately kept for the life of the process. Closing it would
// immediately kill the sidecar, since that is exactly what the job is for.
var gJobHandle uintptr

// superviseChild ties the given process to this one, so it cannot outlive us.
// Failures are silent: worst case we are back to the old behaviour.
func superviseChild(pid int) {
	job, _, _ := jCreateJobObjectW.Call(0, 0)
	if job == 0 {
		return
	}

	var info jExtendedLimitInformation
	info.BasicLimitInformation.LimitFlags = jJobObjectLimitKillOnJobClose
	ok, _, _ := jSetInformationJobObject.Call(
		job,
		jJobObjectExtendedLimitInformation,
		uintptr(unsafe.Pointer(&info)),
		unsafe.Sizeof(info),
	)
	if ok == 0 {
		jCloseHandle.Call(job)
		return
	}

	h, _, _ := jOpenProcess.Call(jProcessSetQuota|jProcessTerminate, 0, uintptr(pid))
	if h == 0 {
		jCloseHandle.Call(job)
		return
	}
	defer jCloseHandle.Call(h)

	if assigned, _, _ := jAssignProcessToJobObject.Call(job, h); assigned == 0 {
		jCloseHandle.Call(job)
		return
	}

	gJobHandle = job
}
