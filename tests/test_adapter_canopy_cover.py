"""Tests for adapter-owned canopy cover processing."""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from pyforestscan_qgis.core.adapter import PyForestScanAdapter
from pyforestscan_qgis.core.exceptions import ProcessingError
from pyforestscan_qgis.core.types import CanopyCoverRequest, ProgressState


class AdapterCanopyCoverTests(unittest.TestCase):
    """Plain-Python canopy cover tests using fake PyForestScan modules."""

    def test_create_canopy_cover_uses_pyforestscan_api_and_writes_geotiff(self) -> None:
        """The adapter computes canopy cover through PyForestScan public APIs."""
        calls: dict[str, object] = {}
        point_array = np.array(
            [(0.0, 0.0, 2.0), (1.0, 0.0, 5.0)],
            dtype=[("X", "f8"), ("Y", "f8"), ("HeightAboveGround", "f8")],
        )
        fake_pyforestscan = types.ModuleType("pyforestscan")
        fake_handlers = types.ModuleType("pyforestscan.handlers")

        def assign_voxels(arr, voxel_resolution):
            calls["assign"] = (arr, voxel_resolution)
            return np.ones((1, 1, 4), dtype="f8"), [0.0, 1.0, 0.0, 1.0]

        def calculate_pad(voxel_returns, voxel_height=1.0, beer_lambert_constant=1.0, drop_ground=True):
            calls["pad"] = (voxel_returns, voxel_height)
            return np.ones((1, 1, 4), dtype="f8")

        def calculate_canopy_cover(pad, voxel_height, min_height=2.0, max_height=None, k=0.5):
            calls["cover"] = (pad, voxel_height, min_height, max_height, k)
            return np.array([[0.75]], dtype="f8")

        def read_lidar(input_file, srs, hag=False):
            calls["read"] = (input_file, srs, hag)
            return [point_array]

        def create_geotiff(layer, output_file, crs, spatial_extent):
            calls["write"] = (layer, output_file, crs, spatial_extent)
            Path(output_file).write_text("fake canopy cover", encoding="utf-8")

        fake_pyforestscan.assign_voxels = assign_voxels  # type: ignore[attr-defined]
        fake_pyforestscan.calculate_pad = calculate_pad  # type: ignore[attr-defined]
        fake_pyforestscan.calculate_canopy_cover = calculate_canopy_cover  # type: ignore[attr-defined]
        fake_handlers.read_lidar = read_lidar  # type: ignore[attr-defined]
        fake_handlers.create_geotiff = create_geotiff  # type: ignore[attr-defined]

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            sys.modules,
            {"pyforestscan": fake_pyforestscan, "pyforestscan.handlers": fake_handlers},
        ):
            output_path = Path(temp_dir) / "canopy_cover.tif"
            result = PyForestScanAdapter(execution_mode="qgis_python").create_canopy_cover(
                CanopyCoverRequest(
                    input_path="plot.laz",
                    output_path=output_path,
                    grid_resolution=2.0,
                    canopy_height_threshold=3.5,
                    crs="EPSG:32610",
                )
            )

            self.assertEqual(output_path, result.output_path)
            self.assertTrue(output_path.exists())
            self.assertEqual(calls["read"], ("plot.laz", "EPSG:32610", True))
            self.assertEqual(calls["assign"][1], (2.0, 2.0, 1.0))  # type: ignore[index]
            self.assertEqual(calls["cover"][2], 3.5)  # type: ignore[index]
            self.assertEqual(calls["write"][1], str(output_path))  # type: ignore[index]

    def test_create_canopy_cover_requires_hag_dimension(self) -> None:
        """Missing HeightAboveGround is reported as a processing error."""
        fake_pyforestscan = types.ModuleType("pyforestscan")
        fake_handlers = types.ModuleType("pyforestscan.handlers")
        fake_pyforestscan.assign_voxels = lambda *args, **kwargs: None  # type: ignore[attr-defined]
        fake_pyforestscan.calculate_pad = lambda *args, **kwargs: None  # type: ignore[attr-defined]
        fake_pyforestscan.calculate_canopy_cover = lambda *args, **kwargs: None  # type: ignore[attr-defined]
        fake_handlers.read_lidar = lambda *args, **kwargs: [np.array([(0.0, 0.0)], dtype=[("X", "f8"), ("Y", "f8")])]  # type: ignore[attr-defined]
        fake_handlers.create_geotiff = lambda *args, **kwargs: None  # type: ignore[attr-defined]

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            sys.modules,
            {"pyforestscan": fake_pyforestscan, "pyforestscan.handlers": fake_handlers},
        ):
            adapter = PyForestScanAdapter(execution_mode="qgis_python")
            with self.assertRaises(ProcessingError):
                adapter.create_canopy_cover(CanopyCoverRequest("plot.laz", Path(temp_dir) / "cover.tif", 1.0, 2.0, "EPSG:32610"))
            self.assertEqual(ProgressState.FAILED, adapter.get_progress().state)

    def test_create_canopy_cover_rejects_negative_threshold(self) -> None:
        """Negative canopy height thresholds are rejected before processing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ProcessingError):
                PyForestScanAdapter(execution_mode="qgis_python").create_canopy_cover(
                    CanopyCoverRequest("plot.laz", Path(temp_dir) / "cover.tif", 1.0, -1.0, "EPSG:32610")
                )


if __name__ == "__main__":
    unittest.main()
