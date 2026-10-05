"""Tests for FHD and Rumple adapter calls without QGIS."""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]

from pyforestscan_qgis.core.adapter import PyForestScanAdapter
from pyforestscan_qgis.core.exceptions import ProcessingError
from pyforestscan_qgis.core.types import FhdRequest, RumpleRequest


@unittest.skipIf(np is None, "numpy is required for adapter FHD/Rumple tests")
class AdapterFhdRumpleTests(unittest.TestCase):
    """Plain-Python tests for FHD and Rumple adapter orchestration."""

    def test_create_fhd_writes_geotiff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _fake_science_modules() as state:
            output = Path(temp_dir) / "fhd.tif"
            adapter = PyForestScanAdapter(execution_mode="qgis_python")

            result = adapter.create_fhd(
                FhdRequest(
                    input_path="plot.laz",
                    output_path=output,
                    grid_resolution=2.0,
                    voxel_height=1.5,
                    crs="EPSG:32610",
                )
            )

            self.assertEqual(output, result.output_path)
            self.assertEqual((2.0, 2.0, 1.5), state["voxel_resolution"])
            self.assertEqual(output, state["single_band_output"])
            self.assertTrue(output.exists())

    def test_create_rumple_writes_scalar_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _fake_science_modules() as state:
            output = Path(temp_dir) / "rumple_summary.csv"
            adapter = PyForestScanAdapter(execution_mode="qgis_python")

            result = adapter.create_rumple(
                RumpleRequest(
                    input_path="plot.laz",
                    output_path=output,
                    grid_resolution=1.0,
                    crs="EPSG:32610",
                    min_height=2.0,
                )
            )

            self.assertEqual(output, result.output_path)
            self.assertEqual(1.75, result.rumple_index)
            self.assertEqual((1.0, 1.0), state["rumple_cell_resolution"])
            self.assertEqual(2.0, state["rumple_min_height"])
            text = output.read_text(encoding="utf-8")
            self.assertIn("rumple_index,1.75", text)
            self.assertIn("grid_resolution,1", text)

    def test_create_rumple_rejects_geotiff_output(self) -> None:
        adapter = PyForestScanAdapter(execution_mode="qgis_python")

        with self.assertRaises(ProcessingError):
            adapter.create_rumple(
                RumpleRequest(
                    input_path="plot.laz",
                    output_path=Path("rumple.tif"),
                    grid_resolution=1.0,
                    crs="EPSG:32610",
                )
            )


class _fake_science_modules:
    """Context manager that installs fake pyforestscan handlers."""

    def __enter__(self):
        self.state = {}
        self.previous = {name: sys.modules.get(name) for name in ("pyforestscan", "pyforestscan.handlers")}
        point_array = np.array(
            [(0.0, 0.0, 0.5), (1.0, 1.0, 2.5)],
            dtype=[("X", "f8"), ("Y", "f8"), ("HeightAboveGround", "f8")],
        )

        handlers = types.ModuleType("pyforestscan.handlers")
        handlers.read_lidar = lambda input_path, crs, hag=True: [point_array]

        def create_geotiff(layer, output_file, crs, spatial_extent, nodata=-9999):
            path = Path(output_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("single band geotiff", encoding="utf-8")
            self.state["single_band_output"] = path

        handlers.create_geotiff = create_geotiff

        pyforestscan = types.ModuleType("pyforestscan")
        pyforestscan.handlers = handlers

        def assign_voxels(points, voxel_resolution):
            self.state["voxel_resolution"] = tuple(voxel_resolution)
            return np.ones((2, 3, 2), dtype="f4"), (0.0, 2.0, 0.0, 3.0)

        def calculate_fhd(voxel_returns, voxel_height=1.0, min_height=0.0, max_height=None):
            self.state["fhd_args"] = (voxel_height, min_height, max_height)
            return np.full((2, 3), 0.9, dtype="f4")

        def calculate_chm(points, voxel_resolution, interpolation="linear", interp_valid_region=False, interp_clean_edges=False):
            self.state["chm_resolution"] = tuple(voxel_resolution)
            return np.full((2, 3), 8.0, dtype="f4"), (0.0, 2.0, 0.0, 3.0)

        def calculate_rumple(chm, cell_resolution, min_height=None):
            self.state["rumple_cell_resolution"] = tuple(cell_resolution)
            self.state["rumple_min_height"] = min_height
            return 1.75

        pyforestscan.assign_voxels = assign_voxels
        pyforestscan.calculate_fhd = calculate_fhd
        pyforestscan.calculate_chm = calculate_chm
        pyforestscan.calculate_rumple = calculate_rumple

        sys.modules["pyforestscan"] = pyforestscan
        sys.modules["pyforestscan.handlers"] = handlers
        return self.state

    def __exit__(self, exc_type, exc, traceback):
        for name, module in self.previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


if __name__ == "__main__":
    unittest.main()
