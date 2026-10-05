"""Cross-platform backend path resolution."""

from __future__ import annotations

import os
import platform as platform_module
from dataclasses import dataclass
from pathlib import Path

from ..platform_compat import native_architecture
from .models import BackendPlatform


@dataclass(frozen=True)
class BackendPaths:
    """Resolved filesystem contract for the managed backend."""

    platform: BackendPlatform
    backend_root: Path
    micromamba_executable: Path
    environment_path: Path
    python_executable: Path
    logs_dir: Path
    config_file: Path
    registry_file: Path
    cache_dir: Path
    downloads_dir: Path
    staging_dir: Path
    scripts_dir: Path
    install_log: Path
    download_log: Path
    verify_log: Path
    repair_log: Path
    update_log: Path
    remove_log: Path
    architecture: str = ""


def backend_architecture(platform: BackendPlatform, machine: str | None = None) -> str:
    """Resolve the native engine target independently of QGIS's CPU type."""
    system = "Darwin" if platform is BackendPlatform.MACOS else platform.value
    return native_architecture(system, machine)


def detect_backend_platform(system_name: str | None = None) -> BackendPlatform:
    """Return the backend platform family for a platform.system() value."""
    name = (system_name or platform_module.system()).lower()
    if name.startswith("win"):
        return BackendPlatform.WINDOWS
    if name == "darwin":
        return BackendPlatform.MACOS
    if name == "linux":
        return BackendPlatform.LINUX
    return BackendPlatform.UNKNOWN


def default_backend_root(
    platform: BackendPlatform | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve the user-local backend root without creating directories."""
    env = environ if environ is not None else os.environ
    home_path = home or Path.home()
    platform_value = platform or detect_backend_platform()
    if platform_value is BackendPlatform.WINDOWS:
        local_app_data = env.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else home_path / "AppData" / "Local"
        return base / "PyForestScan" / "backend"
    if platform_value is BackendPlatform.MACOS:
        return home_path / "Library" / "Application Support" / "PyForestScan" / "backend"
    return home_path / ".local" / "share" / "PyForestScan" / "backend"


def resolve_backend_paths(
    backend_root: Path | None = None,
    platform: BackendPlatform | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    machine: str | None = None,
) -> BackendPaths:
    """Return all backend paths for the current or mocked platform."""
    platform_value = platform or detect_backend_platform()
    root = backend_root or default_backend_root(platform_value, environ=environ, home=home)
    exe_suffix = ".exe" if platform_value is BackendPlatform.WINDOWS else ""
    micromamba = root / "micromamba" / f"micromamba{exe_suffix}"
    environment = root / "env"
    python_executable = environment / ("python.exe" if platform_value is BackendPlatform.WINDOWS else "bin/python")
    logs_dir = root / "logs"
    cache_dir = root / "cache"
    downloads_dir = root / "downloads"
    staging_dir = root / "staging"
    scripts_dir = root / "scripts"
    return BackendPaths(
        platform=platform_value,
        architecture=backend_architecture(platform_value, machine),
        backend_root=root,
        micromamba_executable=micromamba,
        environment_path=environment,
        python_executable=python_executable,
        logs_dir=logs_dir,
        config_file=root / "backend.json",
        registry_file=root / "registry.json",
        cache_dir=cache_dir,
        downloads_dir=downloads_dir,
        staging_dir=staging_dir,
        scripts_dir=scripts_dir,
        install_log=logs_dir / "backend_install.log",
        download_log=logs_dir / "backend_download.log",
        verify_log=logs_dir / "backend_verify.log",
        repair_log=logs_dir / "backend_repair.log",
        update_log=logs_dir / "backend_update.log",
        remove_log=logs_dir / "backend_remove.log",
    )
