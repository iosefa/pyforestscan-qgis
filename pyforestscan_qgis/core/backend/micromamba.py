"""Micromamba bootstrap policy for the managed backend installer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .checksums import ChecksumPolicy
from .models import BackendPlatform
from .paths import BackendPaths
from .manifest import load_backend_manifest, platform_subdir

MICROMAMBA_BASE_URL = "https://micro.mamba.pm/api/micromamba"

@dataclass(frozen=True)
class MicromambaBootstrapPolicy:
    """Resolved Micromamba bootstrap source and local paths."""

    platform: BackendPlatform
    source_url: str
    archive_name: str
    download_path: Path
    executable_path: Path
    checksum_policy: ChecksumPolicy
    retry_count: int = 2
    notes: tuple[str, ...] = ()


def micromamba_source_url(platform: BackendPlatform, version: str = "latest", machine: str | None = None) -> str:
    """Return the official Micromamba API URL for a supported platform."""
    subdir = platform_subdir(platform, machine)
    if not subdir:
        return ""
    return f"{MICROMAMBA_BASE_URL}/{subdir}/{version}"


def micromamba_archive_name(platform: BackendPlatform, machine: str | None = None) -> str:
    """Return the local archive name used in the PBM download cache."""
    return f"micromamba-{platform_subdir(platform, machine) or 'unknown'}.tar.bz2"


def micromamba_bootstrap_policy(paths: BackendPaths, checksum: str | None = None) -> MicromambaBootstrapPolicy:
    """Build the bootstrap policy for the current backend paths."""
    artifact = load_backend_manifest().micromamba_artifact()
    sources = sorted(artifact.sources, key=lambda source: source.priority)
    subdir = platform_subdir(paths.platform, paths.architecture or None)
    source_url = sources[0].url_for(subdir, artifact.version) if sources and subdir else ""
    expected = checksum or artifact.sha256_for(paths.platform, paths.architecture or None)
    archive_name = micromamba_archive_name(paths.platform, paths.architecture or None)
    notes = [
        "Micromamba is downloaded only into the user-local PBM downloads directory.",
        "Micromamba version, download source, and required SHA-256 come from backend_manifest.json.",
        "Offline installation remains a future placeholder for pre-fetched artifacts and lock files.",
    ]
    if not source_url:
        notes.append("Unsupported platform; no Micromamba source URL is selected.")
    return MicromambaBootstrapPolicy(
        platform=paths.platform,
        source_url=source_url,
        archive_name=archive_name,
        download_path=paths.downloads_dir / archive_name,
        executable_path=paths.micromamba_executable,
        checksum_policy=ChecksumPolicy(expected=expected, required=artifact.checksum_required or bool(checksum), source="backend_manifest.json"),
        notes=tuple(notes),
    )
