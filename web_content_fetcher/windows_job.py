"""First-party Windows Job Object supervisor reused from the WCF private runtime.

Provenance: first-party current shared Python working snapshot. Source file
sha256 0f96dee3cb065e98e1cdfc1769a29ab7c92845e961d70bab5135d6ad975f5a8b,
class lines 214-315. The original supplied class excerpt has sha256
4692dc76f8f2227b5a8e28b30f429e1fbe252663353c90bd9472eeae255d868e.
Owner-authorized adaptation: only WindowsJob.close changed to bind the Win32
CloseHandle signature, check the BOOL result, raise OSError on FALSE and retain
self.handle until closure is confirmed; all other class methods remain
AST-identical and outside-class source is unchanged apart from this provenance
docstring. Reused within the owner-authorized Apache-2.0 WCF edition; no
third-party grant is asserted, no ACL/lease/cleanup/root-path code is copied,
and no OS call runs at import time.
"""
import ctypes
from ctypes import wintypes
import os
import subprocess
from typing import Any


class WindowsJob:
    def __init__(self) -> None:
        self.handle: int | None = None
        self.assigned = False
        if os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_job = kernel32.CreateJobObjectW
        create_job.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        create_job.restype = wintypes.HANDLE
        handle = create_job(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimit),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        set_information = kernel32.SetInformationJobObject
        set_information.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        set_information.restype = wintypes.BOOL
        ok = set_information(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not ok:
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "SetInformationJobObject failed")
        self.handle = handle

    def assign(self, process: subprocess.Popen[Any]) -> None:
        if os.name != "nt":
            self.assigned = True
            return
        if self.handle is None:
            raise OSError("job handle unavailable")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        assign = kernel32.AssignProcessToJobObject
        assign.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        assign.restype = wintypes.BOOL
        if not assign(self.handle, int(process._handle)):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        self.assigned = True

    def terminate(self, code: int = 124) -> None:
        if os.name == "nt" and self.handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            terminate = kernel32.TerminateJobObject
            terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
            terminate.restype = wintypes.BOOL
            if not terminate(self.handle, code):
                raise OSError(ctypes.get_last_error(), "TerminateJobObject failed")

    def close(self) -> None:
        """Close the Win32 Job Object handle fail-closed.

        Binds the Win32 signature, checks the returned BOOL, raises OSError on
        FALSE with the last error, and retains self.handle unless closure is
        confirmed. No handle or non-Windows is a no-op.
        """
        if os.name != "nt" or self.handle is None:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        if not close_handle(self.handle):
            raise OSError(ctypes.get_last_error(), "CloseHandle failed")
        self.handle = None

    def total_processes(self) -> int:
        """Kernel lifetime accounting, including short-lived descendants."""
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in
                        ('user', 'kernel', 'period_user', 'period_kernel')] + [
                (name, wintypes.DWORD) for name in ('faults', 'total', 'active', 'terminated')]
        query = ctypes.WinDLL('kernel32', use_last_error=True).QueryInformationJobObject
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        query.restype = wintypes.BOOL
        value = Accounting()
        if not query(self.handle, 1, ctypes.byref(value), ctypes.sizeof(value), None):
            raise OSError(ctypes.get_last_error(), 'QueryInformationJobObject failed')
        return value.total
