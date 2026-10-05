"""Thread-safe ownership and resource sampling for managed child processes."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import json
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OwnedWorkerSnapshot:
    worker_id: str
    pid: int
    stage: str
    peak_rss: int


class OwnedWorkerRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._workers: dict[str, tuple[object, str, int]] = {}

    def register(self, worker_id: str, process, stage: str = "Reading LiDAR") -> None:
        with self._lock:
            self._workers[worker_id] = (process, stage, 0)

    def update(self, worker_id: str, *, stage: str | None = None, peak_rss: int | None = None) -> None:
        with self._lock:
            current = self._workers.get(worker_id)
            if current is None:
                return
            process, old_stage, old_peak = current
            self._workers[worker_id] = (process, stage or old_stage, max(old_peak, int(peak_rss or 0)))

    def unregister(self, worker_id: str) -> OwnedWorkerSnapshot | None:
        with self._lock:
            current = self._workers.pop(worker_id, None)
        if current is None:
            return None
        process, stage, peak = current
        return OwnedWorkerSnapshot(worker_id, int(process.pid), stage, peak)

    def snapshots(self) -> tuple[OwnedWorkerSnapshot, ...]:
        with self._lock:
            return tuple(OwnedWorkerSnapshot(key, int(value[0].pid), value[1], value[2]) for key, value in self._workers.items())

    def terminate_all(self) -> None:
        with self._lock:
            workers = tuple(self._workers.values())
        for process, _stage, _peak in workers:
            if process.poll() is None:
                terminate_process_tree(process)


def terminate_process_tree(process, os_name: str | None = None) -> None:
    """Terminate one owned worker and its process group without touching QGIS."""
    platform_name = os_name or os.name
    if process.poll() is not None:
        return
    if platform_name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        except OSError:
            process.terminate()
            return
    try:
        pid = int(process.pid)
        group = os.getpgid(pid)
        # Only a child launched in its own group owns that group. Ordinary
        # children inherit QGIS's (or the coordinator's) group, so signalling
        # that group would also terminate the application and sibling jobs.
        if group == pid and group != os.getpgrp():
            os.killpg(group, signal.SIGTERM)
            return
    except (AttributeError, OSError):
        pass
    try:
        process.terminate()
    except ProcessLookupError:
        pass  # The worker can exit between poll() and termination.


class GlobalWorkerLease:
    def __init__(self,path:Path):self.path=path
    def release(self):
        try:self.path.unlink()
        except OSError:pass
    def __enter__(self):return self
    def __exit__(self,*_args):self.release()


class GlobalResourceGovernor:
    """Coordinate a hard machine-wide heavy-worker ceiling across jobs."""
    def __init__(self,root:Path|None=None,maximum:int=5):
        base=Path(root) if root is not None else Path(os.environ.get("LOCALAPPDATA",tempfile.gettempdir()))/"PyForestScan"/"resource_governor"
        self.root=base;self.maximum=max(1,min(int(maximum),5));self.root.mkdir(parents=True,exist_ok=True)
    def acquire(self,worker_id:str,timeout:float=600.0)->GlobalWorkerLease:
        started=time.monotonic()
        while time.monotonic()-started<timeout:
            self._remove_stale()
            for slot in range(self.maximum):
                path=self.root/f"slot-{slot}.json"
                try:
                    descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
                except FileExistsError:continue
                payload=json.dumps({"pid":os.getpid(),"worker_id":worker_id,"lease_id":uuid.uuid4().hex,"created_at":time.time()}).encode()
                with os.fdopen(descriptor,"wb") as stream:stream.write(payload)
                return GlobalWorkerLease(path)
            time.sleep(.25)
        raise RuntimeError("GLOBAL_RESOURCE_CAPACITY_WAIT_TIMEOUT: no heavy-worker slot became available.")
    def _remove_stale(self):
        for path in self.root.glob("slot-*.json"):
            try:
                data=json.loads(path.read_text(encoding="utf-8"));pid=int(data.get("pid",0))
                os.kill(pid,0)
            except ProcessLookupError:
                try:path.unlink()
                except OSError:pass
            except (OSError,ValueError,TypeError):
                if time.time()-path.stat().st_mtime>86400:
                    try:path.unlink()
                    except OSError:pass


def process_rss_bytes(pid: int) -> int:
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ]
            handle = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, int(pid))
            if not handle:
                return 0
            counters = Counters(); counters.cb = ctypes.sizeof(counters)
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
            ctypes.windll.kernel32.CloseHandle(handle)
            return int(counters.WorkingSetSize) if ok else 0
        except (AttributeError, OSError, ValueError):
            return 0
    try:
        for line in (Path("/proc") / str(pid) / "status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return 0
    return 0
