"""Backend manifest parsing and registry conversion."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..platform_compat import conda_platform_subdir
from .models import BackendDependency, BackendPlatform, BackendRegistry


class BackendManifestError(ValueError):
    """Raised when a backend manifest is missing or invalid."""


@dataclass(frozen=True)
class ManifestChannel:
    """One package channel declared by the backend manifest."""

    name: str
    priority: int = 0
    notes: str = ""


@dataclass(frozen=True)
class ManifestSource:
    """One download source declared by the manifest."""

    name: str
    url_template: str
    priority: int = 0

    def url_for(self, platform_subdir: str, version: str) -> str:
        """Render this source for a platform subdir and artifact version."""
        return self.url_template.format(platform_subdir=platform_subdir, version=version)


@dataclass(frozen=True)
class ManifestArtifact:
    """One downloadable artifact declared by the backend manifest."""

    name: str
    version: str
    checksum_required: bool
    hashes: dict[str, str]
    sources: tuple[ManifestSource, ...]
    future_mirrors: tuple[str, ...] = ()

    def sha256_for(self, platform: BackendPlatform, machine: str | None = None) -> str | None:
        """Select an architecture hash; legacy OS-only hashes mean x86_64."""
        subdir = platform_subdir(platform, machine)
        value = self.hashes.get(subdir, "")
        if not value and subdir in {"win-64", "linux-64", "osx-64"}:
            value = self.hashes.get(platform.value, "")
        return value or None

    def sha256_for_platforms_present(self) -> bool:
        """Return whether every declared platform hash is pinned."""
        return bool(self.hashes) and all(bool(value) for value in self.hashes.values())


@dataclass(frozen=True)
class ManifestPackage:
    """One backend package from the manifest package list."""

    name: str
    version_spec: str
    source: str
    required: bool
    category: str
    display_name: str | None = None
    executable_name: str | None = None
    python_import_name: str | None = None

    def to_dependency(self) -> BackendDependency:
        """Convert this manifest package into a dependency-registry entry."""
        return BackendDependency(
            name=self.name,
            display_name=self.display_name or self.name,
            category=self.category,
            required=self.required,
            version_spec=self.version_spec,
            source=self.source,
            executable_name=self.executable_name,
            python_import_name=self.python_import_name,
        )


@dataclass(frozen=True)
class BackendManifest:
    """Single source of truth for the managed backend release."""

    schema_version: int
    backend_version: str
    environment_version: str
    micromamba_version: str
    python_version: str
    created_at: str
    minimum_plugin_version: str
    maximum_plugin_version: str | None
    supported_plugin_versions: tuple[str, ...]
    future_migration_version: str
    channels: tuple[ManifestChannel, ...]
    artifacts: dict[str, ManifestArtifact]
    packages: tuple[ManifestPackage, ...]
    module_placeholders: tuple[str, ...]

    def required_packages(self) -> tuple[ManifestPackage, ...]:
        """Return required packages in manifest install order."""
        return tuple(package for package in self.packages if package.required)

    def package_names(self) -> tuple[str, ...]:
        """Return package identifiers in manifest install order."""
        return tuple(package.name for package in self.required_packages())

    def registry(self) -> BackendRegistry:
        """Return a dependency registry derived from the manifest."""
        return BackendRegistry(dependencies=tuple(package.to_dependency() for package in self.packages), registry_version=str(self.schema_version))

    def micromamba_artifact(self) -> ManifestArtifact:
        """Return the manifest micromamba artifact or raise a manifest error."""
        try:
            return self.artifacts["micromamba"]
        except KeyError as exc:
            raise BackendManifestError("Manifest is missing the micromamba artifact.") from exc


def platform_subdir(platform: BackendPlatform, machine: str | None = None) -> str:
    """Return the manifest platform subdir used by download source templates."""
    system = "Darwin" if platform is BackendPlatform.MACOS else platform.value
    return conda_platform_subdir(system, machine)


def default_manifest_path() -> Path:
    """Return the backend manifest path in source or packaged plugin layouts."""
    source_root = Path(__file__).resolve().parents[3]
    package_root = Path(__file__).resolve().parents[2]
    for candidate in (source_root / "backend_manifest.json", package_root / "backend_manifest.json"):
        if candidate.exists():
            return candidate
    return source_root / "backend_manifest.json"


def load_backend_manifest(path: Path | None = None) -> BackendManifest:
    """Load and validate a backend manifest."""
    manifest_path = path or default_manifest_path()
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackendManifestError(f"Backend manifest could not be loaded: {manifest_path}") from exc
    return backend_manifest_from_dict(data)


def backend_manifest_from_dict(data: dict[str, Any]) -> BackendManifest:
    """Build a typed manifest from raw JSON data."""
    required_keys = ("schema_version", "backend_version", "environment_version", "micromamba_version", "python_version", "packages", "channels", "artifacts")
    missing = [key for key in required_keys if key not in data]
    if missing:
        raise BackendManifestError(f"Backend manifest is missing required field(s): {', '.join(missing)}")
    packages = tuple(_package_from_dict(item) for item in data.get("packages", ()))
    if not packages:
        raise BackendManifestError("Backend manifest must define at least one package.")
    artifacts = {name: _artifact_from_dict(name, item) for name, item in data.get("artifacts", {}).items()}
    return BackendManifest(
        schema_version=int(data["schema_version"]),
        backend_version=str(data["backend_version"]),
        environment_version=str(data["environment_version"]),
        micromamba_version=str(data["micromamba_version"]),
        python_version=str(data["python_version"]),
        created_at=str(data.get("created_at", "")),
        minimum_plugin_version=str(data.get("minimum_plugin_version", "0")),
        maximum_plugin_version=data.get("maximum_plugin_version"),
        supported_plugin_versions=tuple(str(item) for item in data.get("supported_plugin_versions", ())),
        future_migration_version=str(data.get("future_migration_version", "")),
        channels=tuple(ManifestChannel(str(item["name"]), int(item.get("priority", 0)), str(item.get("notes", ""))) for item in data.get("channels", ())),
        artifacts=artifacts,
        packages=packages,
        module_placeholders=tuple(str(item) for item in data.get("module_placeholders", ())),
    )


def _artifact_from_dict(name: str, data: dict[str, Any]) -> ManifestArtifact:
    hashes = {str(platform): str(values.get("sha256", "")) for platform, values in data.get("hashes", {}).items()}
    sources = tuple(ManifestSource(str(item["name"]), str(item["url_template"]), int(item.get("priority", 0))) for item in data.get("sources", ()))
    return ManifestArtifact(
        name=name,
        version=str(data.get("version", "")),
        checksum_required=bool(data.get("checksum_required", True)),
        hashes=hashes,
        sources=tuple(sorted(sources, key=lambda source: source.priority)),
        future_mirrors=tuple(str(item) for item in data.get("future_mirrors", ())),
    )


def _package_from_dict(data: dict[str, Any]) -> ManifestPackage:
    return ManifestPackage(
        name=str(data["name"]),
        version_spec=str(data.get("version_spec", "")),
        source=str(data.get("source", "")),
        required=bool(data.get("required", True)),
        category=str(data.get("category", "runtime")),
        display_name=data.get("display_name"),
        executable_name=data.get("executable_name"),
        python_import_name=data.get("python_import_name"),
    )
