"""Central host-platform and process policy for the managed engine."""

from __future__ import annotations

import ctypes
import platform
from dataclasses import dataclass


@dataclass(frozen=True)
class HostPlatform:
    system: str
    architecture: str
    conda_subdir: str
    tested: bool
    note: str


def normalize_architecture(machine: str | None = None) -> str:
    value = (machine or platform.machine()).casefold().replace("_", "-")
    if value in {"amd64", "x86-64", "x64"}:
        return "x86_64"
    if value in {"arm64", "aarch64"}:
        return "arm64"
    return value or "unknown"


def native_architecture(system: str | None = None, machine: str | None = None) -> str:
    """Choose the separate engine's native architecture, including under Rosetta.

    An explicit machine is a target override. Otherwise query the current
    process on macOS: an Intel QGIS process can launch a native ARM engine.
    """
    architecture = normalize_architecture(machine)
    if machine is None and (system or platform.system()).casefold() == "darwin" and architecture == "x86_64":
        if _is_rosetta_process():
            return "arm64"
    return architecture


def _is_rosetta_process() -> bool:
    """Use Apple's process-local flag; a failed probe stays conservative."""
    try:
        sysctl = ctypes.CDLL(None).sysctlbyname
        sysctl.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        sysctl.restype = ctypes.c_int
        translated = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(translated))
        return sysctl(b"sysctl.proc_translated", ctypes.byref(translated), ctypes.byref(size), None, 0) == 0 and translated.value == 1
    except (AttributeError, OSError):
        return False


def conda_platform_subdir(system: str | None = None, machine: str | None = None) -> str:
    os_name = (system or platform.system()).casefold()
    architecture = native_architecture(system, machine)
    mapping = {
        ("windows", "x86_64"): "win-64",
        ("windows", "arm64"): "win-arm64",
        ("darwin", "x86_64"): "osx-64",
        ("darwin", "arm64"): "osx-arm64",
        ("linux", "x86_64"): "linux-64",
        ("linux", "arm64"): "linux-aarch64",
    }
    return mapping.get((os_name, architecture), "")


def host_platform_report(system: str | None = None, machine: str | None = None) -> HostPlatform:
    os_name = system or platform.system()
    architecture = normalize_architecture(machine)
    subdir = conda_platform_subdir(os_name, architecture)
    tested = os_name.casefold() == "windows" and architecture == "x86_64"
    note = (
        "Windows x86_64 is the currently tested managed-engine target."
        if tested else
        "This host has a recognized package platform but the complete engine is not release-qualified here."
        if subdir else
        "No managed-engine artifact mapping exists for this host."
    )
    return HostPlatform(os_name, architecture, subdir, tested, note)


__all__ = ["HostPlatform", "conda_platform_subdir", "host_platform_report", "native_architecture", "normalize_architecture"]
