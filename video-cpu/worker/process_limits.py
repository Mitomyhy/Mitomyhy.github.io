"""Process containment and memory accounting; no ML dependencies."""
from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes


class ProcessLimitError(RuntimeError):
    pass


if os.name == "nt":
    SIZE_T = ctypes.c_size_t
    ULONG_PTR = ctypes.c_size_t

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", SIZE_T), ("MaximumWorkingSetSize", SIZE_T),
                    ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ULONG_PTR),
                    ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", SIZE_T), ("JobMemoryLimit", SIZE_T),
                    ("PeakProcessMemoryUsed", SIZE_T), ("PeakJobMemoryUsed", SIZE_T)]

    class MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, SIZE_T) for name in ("PeakWorkingSetSize", "WorkingSetSize",
                                     "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                                     "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                                     "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]

    class THREAD_ENTRY(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                    ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD)]


def resume_initial_thread(process):
    """Resume a process created suspended, after it is assigned to the Job.

    Popen closes CreateProcess's thread handle. Documented Toolhelp/OpenThread
    APIs recover the sole initial thread while it has never executed. Creating
    the venv launcher suspended closes the otherwise dangerous child-spawn race.
    """
    if os.name != "nt":
        raise ProcessLimitError("Suspended containment requires Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREAD_ENTRY)]
    kernel.Thread32First.restype = wintypes.BOOL
    kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREAD_ENTRY)]
    kernel.Thread32Next.restype = wintypes.BOOL
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD.
    if snapshot == ctypes.c_void_p(-1).value or not snapshot:
        raise ProcessLimitError("Could not enumerate the suspended process thread")
    try:
        entry = THREAD_ENTRY()
        entry.dwSize = ctypes.sizeof(entry)
        found = []
        present = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while present:
            if entry.th32OwnerProcessID == process.pid:
                found.append(int(entry.th32ThreadID))
            entry.dwSize = ctypes.sizeof(entry)
            present = kernel.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    if len(found) != 1:
        raise ProcessLimitError("Suspended process does not have exactly one initial thread")
    thread = kernel.OpenThread(0x0002, False, found[0])  # THREAD_SUSPEND_RESUME.
    if not thread:
        raise ProcessLimitError("Could not open the suspended initial thread")
    try:
        previous_count = kernel.ResumeThread(thread)
        if previous_count != 1:
            raise ProcessLimitError("Initial thread had an unexpected suspend count")
    finally:
        kernel.CloseHandle(thread)


def start_contained_process(command, memory_bytes, *, limit_factory=None, **kwargs):
    """Return (Popen, ProcessLimits) after race-free Windows Job assignment.

    The caller must close the returned limits object in finally (or use it as a
    context manager), then wait for the process. All future descendants inherit
    this non-breakaway Job, including Python spawned by Windows venv launchers.
    """
    if os.name != "nt":
        raise ProcessLimitError("The reviewed containment helper requires Windows")
    factory = limit_factory or ProcessLimits
    flags = kwargs.pop("creationflags", 0) | subprocess.CREATE_NO_WINDOW | 0x00000004
    process = subprocess.Popen(command, creationflags=flags, **kwargs)  # CREATE_SUSPENDED.
    limits = None
    try:
        limits = factory(process, memory_bytes)
        resume_initial_thread(process)
        return process, limits
    except BaseException:
        if limits is not None:
            limits.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
        raise


class ProcessLimits:
    """Windows Job memory limit includes the inference child and its encoder.

    Always spawn with start_contained_process: attaching a running venv launcher
    cannot retroactively contain a Python child it already spawned. Closing the
    Job kills every member, including the actual Python and its encoder children.
    """

    def __init__(self, process, memory_bytes: int):
        self.process = process
        self.memory_bytes = memory_bytes
        self.handle = None
        if os.name != "nt":
            # The Windows package is the only currently reviewed deployment.
            raise ProcessLimitError("This worker requires Windows Job memory containment")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel = kernel
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                     ctypes.c_void_p, wintypes.DWORD,
                                                     ctypes.c_void_p]
        kernel.QueryInformationJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ProcessLimitError("Could not create Windows memory containment")
        limits = EXTENDED_LIMIT()
        # JOB_OBJECT_LIMIT_JOB_MEMORY | KILL_ON_JOB_CLOSE.
        limits.BasicLimitInformation.LimitFlags = 0x200 | 0x2000
        limits.JobMemoryLimit = memory_bytes
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ProcessLimitError("Could not configure Windows memory containment")
        if not kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            self.close()
            raise ProcessLimitError("Could not attach inference process to memory containment")
        configured = EXTENDED_LIMIT()
        if (not kernel.QueryInformationJobObject(self.handle, 9, ctypes.byref(configured),
                                                 ctypes.sizeof(configured), None)
                or configured.JobMemoryLimit != memory_bytes
                or configured.BasicLimitInformation.LimitFlags & (0x200 | 0x2000) != (0x200 | 0x2000)):
            self.close()
            raise ProcessLimitError("Windows Job memory and kill-on-close settings did not persist")

    def memory(self) -> dict:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.GetExitCodeProcess.restype = wintypes.BOOL
        rss, private, measured = 0, 0, []
        for pid in self.member_pids():
            handle = self.kernel.OpenProcess(0x1000 | 0x0010, False, pid)
            if not handle:
                if ctypes.get_last_error() == 87:  # Member exited after snapshot.
                    continue
                raise ProcessLimitError("Could not open a Job member for memory accounting")
            try:
                counters = MEMORY_COUNTERS()
                counters.cb = ctypes.sizeof(counters)
                if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), ctypes.sizeof(counters)):
                    exit_code = wintypes.DWORD()
                    if self.kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)) and exit_code.value != 259:
                        continue
                    raise ProcessLimitError("Could not read an active Job member's memory")
                rss += int(counters.WorkingSetSize)
                private += int(counters.PrivateUsage)
                measured.append(pid)
            finally:
                self.kernel.CloseHandle(handle)
        limits = EXTENDED_LIMIT()
        if not self.kernel.QueryInformationJobObject(self.handle, 9, ctypes.byref(limits),
                                                     ctypes.sizeof(limits), None):
            raise ProcessLimitError("Could not read inference job memory")
        return {"rss_bytes": rss, "private_bytes": private,
                "peak_job_private_bytes": int(limits.PeakJobMemoryUsed),
                "job_process_count": len(measured)}

    def member_pids(self):
        # JobObjectBasicProcessIdList is variable length; allocate generously,
        # fail closed instead of silently dropping children if that bound is hit.
        class PROCESS_LIST(ctypes.Structure):
            _fields_ = [("assigned", wintypes.DWORD), ("count", wintypes.DWORD),
                        ("ids", ctypes.c_size_t * 256)]
        members = PROCESS_LIST()
        if not self.kernel.QueryInformationJobObject(self.handle, 3, ctypes.byref(members),
                                                     ctypes.sizeof(members), None):
            raise ProcessLimitError("Could not enumerate all inference Job processes")
        if members.count > 256 or members.assigned > 256:
            raise ProcessLimitError("Inference Job has too many processes")
        return [int(members.ids[index]) for index in range(members.count)]

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
