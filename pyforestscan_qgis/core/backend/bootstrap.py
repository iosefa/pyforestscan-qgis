"""Micromamba bootstrap planning for PBM dry runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import BackendPlatform
from .manifest import BackendManifestError
from .micromamba import micromamba_archive_name, micromamba_bootstrap_policy
from .paths import BackendPaths


@dataclass(frozen=True)
class MicromambaBootstrapPlan:
    """Dry-run description of the future micromamba bootstrap artifact."""

    platform: BackendPlatform
    target_executable: Path
    download_cache_path: Path
    artifact_name: str
    source_note: str
    verification_steps: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def build_micromamba_bootstrap_plan(paths: BackendPaths) -> MicromambaBootstrapPlan:
    """Return the planned micromamba bootstrap layout without downloading anything."""
    warnings: list[str] = []
    artifact = micromamba_archive_name(paths.platform, paths.architecture or None)
    try:
        policy = micromamba_bootstrap_policy(paths)
        source_note = f"Pinned Micromamba source: {policy.source_url}"
    except BackendManifestError as exc:
        warnings.append(str(exc))
        source_note = "Bootstrap source unavailable until the backend manifest is repaired."
    if paths.platform is BackendPlatform.UNKNOWN:
        warnings.append("Host platform is unknown; micromamba artifact selection must be resolved before installation is enabled.")
    return MicromambaBootstrapPlan(
        platform=paths.platform,
        target_executable=paths.micromamba_executable,
        download_cache_path=paths.downloads_dir / artifact,
        artifact_name=artifact,
        source_note=source_note,
        verification_steps=(
            "Verify downloaded artifact checksum before extraction.",
            "Verify micromamba executable exists at the user-local backend path.",
            "Run micromamba --version from the managed backend path.",
        ),
        warnings=tuple(warnings),
    )
