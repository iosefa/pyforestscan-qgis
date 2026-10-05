"""Tests for backend manifest parsing and manifest-driven specs."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pyforestscan_qgis.core.backend.environment_spec import build_environment_spec
from pyforestscan_qgis.core.backend.manifest import BackendManifestError, backend_manifest_from_dict, load_backend_manifest
from pyforestscan_qgis.core.backend.models import BackendPlatform


class BackendManifestTests(unittest.TestCase):
    """Validate backend_manifest.json as the PBM source of truth."""

    def test_default_manifest_loads_packages_and_channels(self) -> None:
        manifest = load_backend_manifest()

        self.assertEqual(manifest.schema_version, 1)
        self.assertEqual(manifest.package_names(), ("python", "pdal", "python-pdal", "gdal", "rasterio", "numpy", "scipy", "pandas", "shapely", "pyproj", "fiona", "geopandas", "matplotlib", "tqdm", "requests", "pyforestscan"))
        versions = {package.name: package.version_spec for package in manifest.required_packages()}
        self.assertEqual(versions["gdal"], ">=3.9,<4")
        self.assertEqual(versions["rasterio"], ">=1.4,<2")
        self.assertEqual(versions["numpy"], ">=2.3.1,<3")
        self.assertEqual(versions["scipy"], ">=1.16,<2")
        self.assertEqual(versions["matplotlib"], ">=3.10.3,<4")
        self.assertEqual(versions["tqdm"], ">=4.66,<5")
        self.assertIn("conda-forge", [channel.name for channel in manifest.channels])
        self.assertIn("micromamba", manifest.artifacts)

    def test_environment_spec_comes_from_manifest(self) -> None:
        manifest = load_backend_manifest()
        spec = build_environment_spec(manifest=manifest)

        self.assertEqual(spec.environment_version, manifest.environment_version)
        self.assertEqual(spec.package_names(), manifest.package_names())
        self.assertEqual(spec.packages[0].version_spec, manifest.python_version)

    def test_manifest_pins_bootstrap_hashes_for_all_platforms(self) -> None:
        manifest = load_backend_manifest()
        artifact = manifest.micromamba_artifact()

        self.assertEqual(len(artifact.sha256_for(BackendPlatform.LINUX, machine="x86_64")), 64)
        self.assertTrue(artifact.sha256_for_platforms_present())

    def test_manifest_corruption_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "backend_manifest.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(BackendManifestError):
                load_backend_manifest(path)

    def test_manifest_missing_required_field_fails(self) -> None:
        with self.assertRaises(BackendManifestError):
            backend_manifest_from_dict({"schema_version": 1})

    def test_manifest_round_trip_package_names(self) -> None:
        manifest_path = Path(__file__).resolve().parents[1] / "backend_manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = backend_manifest_from_dict(data)

        self.assertEqual(manifest.registry().dependency_names(), manifest.package_names())


if __name__ == "__main__":
    unittest.main()
