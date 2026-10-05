"""Dry-run backend installation planning for PBM."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .bootstrap import MicromambaBootstrapPlan, build_micromamba_bootstrap_plan
from .channels import BackendChannel, default_backend_channels, format_channels
from .environment_spec import BackendEnvironmentSpec, build_environment_spec
from .manifest import BackendManifest, BackendManifestError, load_backend_manifest
from .models import BackendPlatform, BackendRegistry
from .paths import BackendPaths, resolve_backend_paths
from .registry import default_backend_registry


@dataclass(frozen=True)
class BackendInstallStep:
    """One planned installation, verification, or rollback step."""

    title: str
    detail: str
    modifies_files: bool = False


@dataclass(frozen=True)
class BackendInstallPlan:
    """Complete dry-run installation plan for the managed backend."""

    backend_root: Path
    micromamba_location: Path
    environment_path: Path
    platform: BackendPlatform
    channels: tuple[BackendChannel, ...]
    environment_spec: BackendEnvironmentSpec
    bootstrap_plan: MicromambaBootstrapPlan
    estimated_steps: tuple[BackendInstallStep, ...]
    verification_steps: tuple[BackendInstallStep, ...]
    rollback_steps: tuple[BackendInstallStep, ...]
    offline_install_notes: tuple[str, ...]
    warnings: tuple[str, ...]
    dry_run_only: bool = True
    backend_version: str = "unknown"
    environment_version: str = "unknown"
    manifest_schema_version: int = 0

    def required_package_names(self) -> tuple[str, ...]:
        """Return required backend package names in planned install order."""
        return self.environment_spec.package_names()


def create_backend_install_plan(paths: BackendPaths | None = None, registry: BackendRegistry | None = None, manifest: BackendManifest | None = None) -> BackendInstallPlan:
    """Create a dry-run PBM install plan without touching the filesystem."""
    paths_value = paths or resolve_backend_paths()
    warnings: list[str] = []
    manifest_value: BackendManifest | None = manifest
    try:
        manifest_value = manifest_value or load_backend_manifest()
    except BackendManifestError as exc:
        warnings.append(str(exc))
    registry_value = registry or (manifest_value.registry() if manifest_value is not None else default_backend_registry())
    channels = _channels_from_manifest(manifest_value) if manifest_value is not None else default_backend_channels()
    environment_spec = build_environment_spec(registry_value, channels=tuple(channel.name for channel in channels), manifest=manifest_value)
    bootstrap_plan = build_micromamba_bootstrap_plan(paths_value)
    warnings.extend(bootstrap_plan.warnings)
    if paths_value.platform is BackendPlatform.UNKNOWN:
        warnings.append("Backend platform is unknown; installation cannot be enabled until path and artifact policies are defined.")
    if manifest_value is not None and not manifest_value.micromamba_artifact().sha256_for(paths_value.platform, paths_value.architecture or None):
        warnings.append("Manifest does not pin a SHA-256 hash for this platform; installation cannot verify the required bootstrap artifact.")
    from .installer import backend_install_availability
    warnings.append(backend_install_availability(platform=paths_value.platform, machine=paths_value.architecture or None).reason)

    return BackendInstallPlan(
        backend_root=paths_value.backend_root,
        micromamba_location=paths_value.micromamba_executable,
        environment_path=paths_value.environment_path,
        platform=paths_value.platform,
        channels=channels,
        environment_spec=environment_spec,
        bootstrap_plan=bootstrap_plan,
        estimated_steps=(
            BackendInstallStep("Prepare transaction", f"Create staging, downloads, and logs under {paths_value.backend_root}.", modifies_files=True),
            BackendInstallStep("DOWNLOAD", f"Download {bootstrap_plan.artifact_name} to {bootstrap_plan.download_cache_path} with resume/retry support.", modifies_files=True),
            BackendInstallStep("VERIFY", "Verify artifact checksum and manifest policy before extraction."),
            BackendInstallStep("EXTRACT", f"Extract Micromamba into staging before activation at {paths_value.micromamba_executable}.", modifies_files=True),
            BackendInstallStep("CREATE ENVIRONMENT", f"Create managed environment at {paths_value.environment_path} from backend_manifest.json.", modifies_files=True),
            BackendInstallStep("INSTALL PACKAGES", ", ".join(environment_spec.package_names()), modifies_files=True),
            BackendInstallStep("VERIFY PACKAGES", "Verify Python, PyForestScan, PDAL, GDAL, rasterio, and numpy inside the managed backend."),
            BackendInstallStep("WRITE CONFIG", f"Write backend.json only after verification succeeds at {paths_value.config_file}.", modifies_files=True),
            BackendInstallStep("PROMOTE BACKEND", "Promote staged files only after verification succeeds.", modifies_files=True),
            BackendInstallStep("READY", "Mark backend ready for future managed execution."),
        ),
        verification_steps=(
            BackendInstallStep("Verify backend Python", f"Run {paths_value.python_executable} --version."),
            BackendInstallStep("Verify PyForestScan import", "Import pyforestscan inside the managed backend Python."),
            BackendInstallStep("Verify PDAL", "Run pdal --version and import python-pdal."),
            BackendInstallStep("Verify raster stack", "Import osgeo.gdal, rasterio, and numpy from the managed backend Python."),
            BackendInstallStep("Record verification report", f"Write verification results to {paths_value.verify_log}.", modifies_files=True),
        ),
        rollback_steps=(
            BackendInstallStep("Transactional staging", "Build downloads, extracted executable, and environment in staging before activation."),
            BackendInstallStep("Automatic rollback", "If any stage fails or is cancelled, remove staging and preserve logs."),
            BackendInstallStep("Repair plan", "Detect missing executables, broken environments, corrupt config, corrupt manifest, and missing Python."),
            BackendInstallStep("Known-good preservation", "A future upgrade path must preserve a working backend until replacement verification passes."),
        ),
        offline_install_notes=(
            "Offline install remains a planned mode for pre-fetched artifacts and package caches.",
            "Offline artifacts must still match backend_manifest.json hashes and package versions.",
            "No installer path modifies QGIS Python, QGIS install directories, or user environment variables.",
        ),
        warnings=tuple(warnings),
        backend_version=manifest_value.backend_version if manifest_value is not None else "unknown",
        environment_version=manifest_value.environment_version if manifest_value is not None else "unknown",
        manifest_schema_version=manifest_value.schema_version if manifest_value is not None else 0,
    )


def format_install_plan(plan: BackendInstallPlan) -> str:
    """Format the dry-run install plan for Mission Control."""
    lines = [
        "PyForestScan Backend Install Plan Preview",
        "Windows and Linux x86_64 beta builds can execute this plan. Apple Silicon setup is enabled experimentally. PBM installs into a separate user-local environment.",
        "",
        f"Manifest schema: {plan.manifest_schema_version}",
        f"Backend version: {plan.backend_version}",
        f"Environment version: {plan.environment_version}",
        f"Platform: {plan.platform.value}",
        f"Backend root: {plan.backend_root}",
        f"Micromamba location: {plan.micromamba_location}",
        f"Environment path: {plan.environment_path}",
        "",
        "Package channels:",
        *[f"- {line}" for line in format_channels(plan.channels)],
        "",
        "Required packages:",
        *[f"- {package.name} {package.version_spec} ({package.source})" for package in plan.environment_spec.packages],
        "",
        "Transaction stages:",
        *[f"- {step.title}: {step.detail}" for step in plan.estimated_steps],
        "",
        "Verification steps after install:",
        *[f"- {step.title}: {step.detail}" for step in plan.verification_steps],
        "",
        "Rollback / repair plan:",
        *[f"- {step.title}: {step.detail}" for step in plan.rollback_steps],
        "",
        "Offline install placeholder:",
        *[f"- {note}" for note in plan.offline_install_notes],
    ]
    if plan.warnings:
        lines.extend(("", "Warnings:"))
        lines.extend(f"- {warning}" for warning in plan.warnings)
    return "\n".join(lines)


def _channels_from_manifest(manifest: BackendManifest | None) -> tuple[BackendChannel, ...]:
    if manifest is None:
        return default_backend_channels()
    channels = []
    for channel in sorted(manifest.channels, key=lambda item: item.priority):
        manager = "pip" if channel.name.lower() in {"pypi", "pypi-placeholder"} else "conda"
        url = "https://pypi.org/simple" if manager == "pip" else f"https://conda.anaconda.org/{channel.name}"
        channels.append(BackendChannel(channel.name, url, channel.priority, manager, channel.notes))
    return tuple(channels)
