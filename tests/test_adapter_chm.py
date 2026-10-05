"""Tests for adapter-owned CHM processing."""

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
from pyforestscan_qgis.core.types import ChmRequest, ProgressState


class AdapterChmTests(unittest.TestCase):
    """Plain-Python CHM tests using fake PyForestScan modules."""

    def test_create_chm_uses_pyforestscan_api_and_writes_geotiff(self) -> None:
        """The adapter reads lidar, calculates CHM, and delegates GeoTIFF writing."""
        calls: dict[str, object] = {}
        point_array = np.array(
            [(0.0, 0.0, 2.0), (1.0, 0.0, 5.0)],
            dtype=[("X", "f8"), ("Y", "f8"), ("HeightAboveGround", "f8")],
        )
        fake_pyforestscan = types.ModuleType("pyforestscan")
        fake_handlers = types.ModuleType("pyforestscan.handlers")

        def calculate_chm(arr, voxel_resolution, interpolation="linear", interp_valid_region=False, interp_clean_edges=False):
            calls["calculate"] = (arr, voxel_resolution, interpolation, interp_valid_region, interp_clean_edges)
            return np.array([[5.0]], dtype="f8"), [0.0, 1.0, 0.0, 1.0]

        def read_lidar(input_file, srs, hag=False):
            calls["read"] = (input_file, srs, hag)
            return [point_array]

        def create_geotiff(layer, output_file, crs, spatial_extent):
            calls["write"] = (layer, output_file, crs, spatial_extent)
            Path(output_file).write_text("fake geotiff", encoding="utf-8")

        fake_pyforestscan.calculate_chm = calculate_chm  # type: ignore[attr-defined]
        fake_handlers.read_lidar = read_lidar  # type: ignore[attr-defined]
        fake_handlers.create_geotiff = create_geotiff  # type: ignore[attr-defined]

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            sys.modules,
            {"pyforestscan": fake_pyforestscan, "pyforestscan.handlers": fake_handlers},
        ):
            output_path = Path(temp_dir) / "chm.tif"
            result = PyForestScanAdapter(execution_mode="qgis_python").create_chm(
                ChmRequest(
                    input_path="plot.laz",
                    output_path=output_path,
                    grid_resolution=1.0,
                    crs="EPSG:32610",
                )
            )

            self.assertEqual(output_path, result.output_path)
            self.assertTrue(output_path.exists())
            self.assertEqual(calls["read"], ("plot.laz", "EPSG:32610", True))
            self.assertEqual(calls["calculate"][1], (1.0, 1.0))  # type: ignore[index]
            self.assertEqual(calls["write"][1], str(output_path))  # type: ignore[index]


    def test_create_chm_fails_when_geotiff_writer_does_not_create_file(self) -> None:
        """A missing GeoTIFF is treated as a processing failure."""
        point_array = np.array(
            [(0.0, 0.0, 2.0)],
            dtype=[("X", "f8"), ("Y", "f8"), ("HeightAboveGround", "f8")],
        )
        fake_pyforestscan = types.ModuleType("pyforestscan")
        fake_handlers = types.ModuleType("pyforestscan.handlers")
        fake_pyforestscan.calculate_chm = lambda *args, **kwargs: (np.array([[2.0]], dtype="f8"), [0.0, 1.0, 0.0, 1.0])  # type: ignore[attr-defined]
        fake_handlers.read_lidar = lambda *args, **kwargs: [point_array]  # type: ignore[attr-defined]
        fake_handlers.create_geotiff = lambda *args, **kwargs: None  # type: ignore[attr-defined]

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            sys.modules,
            {"pyforestscan": fake_pyforestscan, "pyforestscan.handlers": fake_handlers},
        ):
            adapter = PyForestScanAdapter(execution_mode="qgis_python")
            with self.assertRaises(ProcessingError):
                adapter.create_chm(ChmRequest("plot.laz", Path(temp_dir) / "chm.tif", 1.0, "EPSG:32610"))
            self.assertEqual(ProgressState.FAILED, adapter.get_progress().state)

    def test_create_chm_requires_geotiff_output_extension(self) -> None:
        """Invalid CHM output extensions are rejected before processing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ProcessingError):
                PyForestScanAdapter(execution_mode="qgis_python").create_chm(ChmRequest("plot.laz", Path(temp_dir) / "chm.asc", 1.0, "EPSG:32610"))

    def test_create_chm_requires_hag_dimension(self) -> None:
        """Missing HeightAboveGround is reported as a processing error."""
        fake_pyforestscan = types.ModuleType("pyforestscan")
        fake_handlers = types.ModuleType("pyforestscan.handlers")
        fake_pyforestscan.calculate_chm = lambda *args, **kwargs: None  # type: ignore[attr-defined]
        fake_handlers.read_lidar = lambda *args, **kwargs: [np.array([(0.0, 0.0)], dtype=[("X", "f8"), ("Y", "f8")])]  # type: ignore[attr-defined]
        fake_handlers.create_geotiff = lambda *args, **kwargs: None  # type: ignore[attr-defined]

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            sys.modules,
            {"pyforestscan": fake_pyforestscan, "pyforestscan.handlers": fake_handlers},
        ):
            adapter = PyForestScanAdapter(execution_mode="qgis_python")
            with self.assertRaises(ProcessingError):
                adapter.create_chm(ChmRequest("plot.laz", Path(temp_dir) / "chm.tif", 1.0, "EPSG:32610"))
            self.assertEqual(ProgressState.FAILED, adapter.get_progress().state)


if __name__ == "__main__":
    unittest.main()
