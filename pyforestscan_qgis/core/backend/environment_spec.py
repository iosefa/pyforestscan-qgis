"""Manifest-driven environment specification for the managed backend."""

from __future__ import annotations

from dataclasses import dataclass

from .manifest import BackendManifest, ManifestPackage, load_backend_manifest
from .models import BackendDependency, BackendRegistry
from .registry import default_backend_registry

_REQUIRED_ENV_PACKAGES = ("python", "pdal", "python-pdal", "gdal", "rasterio", "numpy", "scipy", "pandas", "shapely", "pyproj", "fiona", "geopandas", "matplotlib", "tqdm", "requests", "pyforestscan")


@dataclass(frozen=True)
class BackendEnvironmentPackage:
    """One package planned for the managed backend environment."""

    name: str
    display_name: str
    version_spec: str
    source: str
    category: str
    registry_required: bool
    python_import_name: str | None = None
    executable_name: str | None = None

    @classmethod
    def from_dependency(cls, dependency: BackendDependency) -> "BackendEnvironmentPackage":
        """Create an environment package from the dependency registry."""
        return cls(
            name=dependency.name,
            display_name=dependency.display_name,
            version_spec=dependency.version_spec,
            source=dependency.source,
            category=dependency.category,
            registry_required=dependency.required,
            python_import_name=dependency.python_import_name,
            executable_name=dependency.executable_name,
        )

    @classmethod
    def from_manifest_package(cls, package: ManifestPackage) -> "BackendEnvironmentPackage":
        """Create an environment package from a backend manifest package."""
        return cls(
            name=package.name,
            display_name=package.display_name or package.name,
            version_spec=package.version_spec,
            source=package.source,
            category=package.category,
            registry_required=package.required,
            python_import_name=package.python_import_name,
            executable_name=package.executable_name,
        )


@dataclass(frozen=True)
class BackendEnvironmentSpec:
    """Manifest-driven backend environment specification."""

    name: str
    packages: tuple[BackendEnvironmentPackage, ...]
    channels: tuple[str, ...]
    notes: tuple[str, ...]
    environment_version: str = ""

    def package_names(self) -> tuple[str, ...]:
        """Return stable package identifiers in planned install order."""
        return tuple(package.name for package in self.packages)


def build_environment_spec(
    registry: BackendRegistry | None = None,
    channels: tuple[str, ...] = ("conda-forge", "pypi"),
    manifest: BackendManifest | None = None,
) -> BackendEnvironmentSpec:
    """Build the managed backend environment spec from the manifest or registry."""
    if manifest is not None:
        return environment_spec_from_manifest(manifest)
    if registry is None:
        try:
            return environment_spec_from_manifest(load_backend_manifest())
        except Exception:  # noqa: BLE001 - fallback keeps settings usable if packaged manifest is missing.
            registry = default_backend_registry()
    registry_value = registry or default_backend_registry()
    dependencies = {dependency.name: dependency for dependency in registry_value.dependencies}
    packages = tuple(BackendEnvironmentPackage.from_dependency(dependencies[name]) for name in _REQUIRED_ENV_PACKAGES if name in dependencies)
    return BackendEnvironmentSpec(
        name="pyforestscan-backend",
        packages=packages,
        channels=channels,
        notes=(
            "Backend environment is user-local and separate from QGIS Python.",
            "Phase 22D prefers backend_manifest.json; registry fallback is retained only for defensive UI operation.",
        ),
    )


def environment_spec_from_manifest(manifest: BackendManifest) -> BackendEnvironmentSpec:
    """Return an environment spec directly from backend_manifest.json."""
    return BackendEnvironmentSpec(
        name="pyforestscan-backend",
        packages=tuple(BackendEnvironmentPackage.from_manifest_package(package) for package in manifest.required_packages()),
        channels=tuple(channel.name for channel in sorted(manifest.channels, key=lambda item: item.priority)),
        notes=(
            f"Manifest backend version: {manifest.backend_version}.",
            f"Manifest environment version: {manifest.environment_version}.",
            "PBM must not infer package versions from scattered files.",
            "The backend environment is user-local and separate from QGIS Python.",
        ),
        environment_version=manifest.environment_version,
    )
