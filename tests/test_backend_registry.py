"""Tests for backend dependency registry."""

from __future__ import annotations

import unittest

from pyforestscan_qgis.core.backend.models import BackendRegistry
from pyforestscan_qgis.core.backend.registry import default_backend_registry


class BackendRegistryTests(unittest.TestCase):
    """Validate registry-driven dependency metadata."""

    def test_required_dependencies_exist(self) -> None:
        registry = default_backend_registry()
        required_names = {dependency.name for dependency in registry.required_dependencies()}
        self.assertEqual(
            {
                "micromamba",
                "python",
                "pdal",
                "python-pdal",
                "gdal",
                "rasterio",
                "numpy",
                "scipy",
                "pandas",
                "shapely",
                "pyproj",
                "fiona",
                "geopandas",
                "matplotlib",
                "tqdm",
                "requests",
                "pyforestscan",
            },
            required_names,
        )

    def test_future_modules_are_registered_optional(self) -> None:
        registry = default_backend_registry()
        optional_names = {dependency.name for dependency in registry.dependencies if not dependency.required}
        self.assertIn("whiteboxtools", optional_names)
        self.assertIn("open3d", optional_names)
        self.assertIn("pytorch", optional_names)
        self.assertIn("onnx-runtime", optional_names)
        self.assertIn("segment-anything", optional_names)
        self.assertIn("cloudcompare-cli", optional_names)
        self.assertIn("entwine", optional_names)
        self.assertIn("potree-converter", optional_names)

    def test_registry_round_trip(self) -> None:
        registry = default_backend_registry()
        restored = BackendRegistry.from_dict(registry.to_dict())
        self.assertEqual(restored.dependency_names(), registry.dependency_names())
        self.assertEqual(len(restored.required_dependencies()), len(registry.required_dependencies()))


if __name__ == "__main__":
    unittest.main()
