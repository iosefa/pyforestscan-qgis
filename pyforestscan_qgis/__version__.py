"""Version metadata for PyForestScan QGIS releases."""

from __future__ import annotations

PLUGIN_VERSION = "0.2.0-beta.7"
BUILD_METADATA = ""
MINIMUM_QGIS_VERSION = "3.28"
SUPPORTED_QGIS_MAJOR_VERSIONS = (3,)
COMPATIBLE_PBM_MANIFEST_VERSION = "1.0.0"
COMPATIBLE_PBM_MANIFEST_SCHEMA_VERSION = 1
INTERNAL_BETA_BACKEND_INSTALL = True
INTERNAL_BETA_BACKEND_INSTALL_PLATFORMS = ("windows", "linux", "macos")


def full_version() -> str:
    """Return the plugin version with optional build metadata."""
    return f"{PLUGIN_VERSION}+{BUILD_METADATA}" if BUILD_METADATA else PLUGIN_VERSION
