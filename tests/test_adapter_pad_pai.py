"""Tests for PAD and PAI adapter calls without QGIS."""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

try:
    import numpy as np
except ImportError:  # pragma: no cover - dependency is optional outside QGIS.
    np = None  # type: ignore[assignment]

from pyforestscan_qgis.core.adapter import PyForestScanAdapter
from pyforestscan_qgis.core.exceptions import ProcessingError
from pyforestscan_qgis.core.types import PadRequest, PaiRequest


@unittest.skipIf(np is None, "numpy is required for adapter PAD/PAI tests")
class AdapterPadPaiTests(unittest.TestCase):
    """Plain-Python tests for adapter PAD and PAI orchestration."""

    def test_create_pad_writes_multiband_geotiff(self) -> None:
        """PAD calls assign_voxels/calculate_pad and writes one raster band per height bin."""
        with tempfile.TemporaryDirectory() as temp_dir, _fake_science_modules() as state:
            output = Path(temp_dir) / "pad.tif"
            adapter = PyForestScanAdapter(execution_mode="qgis_python")

            result = adapter.create_pad(
                PadRequest(
                    input_path="plot.laz",
                    output_path=output,
                    grid_resolution=2.0,
                    voxel_height=1.5,
                    crs="EPSG:32610",
                )
            )

            self.assertEqual(output, result.output_path)
            self.assertEqual(2, result.band_count)
            self.assertEqual((2.0, 2.0, 1.5), state["voxel_resolution"])
            self.assertEqual([1, 2], state["written_bands"])
            self.assertTrue(output.exists())

    def test_create_pai_writes_single_band_geotiff(self) -> None:
        """PAI computes PAD internally and delegates 2D GeoTIFF writing to PyForestScan."""
        with tempfile.TemporaryDirectory() as temp_dir, _fake_science_modules() as state:
            output = Path(temp_dir) / "pai.tif"
            adapter = PyForestScanAdapter(execution_mode="qgis_python")

            result = adapter.create_pai(
                PaiRequest(
                    input_path="plot.laz",
                    output_path=output,
                    grid_resolution=1.0,
                    voxel_height=2.0,
                    crs="EPSG:32610",
                    min_height=1.0,
                    max_height=8.0,
                )
            )

            self.assertEqual(output, result.output_path)
            self.assertEqual((1.0, 1.0, 2.0), state["voxel_resolution"])
            self.assertEqual((1.0, 8.0), state["pai_height_range"])
            self.assertEqual(output, state["single_band_output"])
            self.assertTrue(output.exists())

    def test_create_pai_requires_positive_voxel_height(self) -> None:
        """PAI rejects invalid height bins before importing PyForestScan."""
        adapter = PyForestScanAdapter(execution_mode="qgis_python")

        with self.assertRaises(ProcessingError):
            adapter.create_pai(
                PaiRequest(
                    input_path="plot.laz",
                    output_path=Path("pai.tif"),
                    grid_resolution=1.0,
                    voxel_height=0.0,
                    crs="EPSG:32610",
                )
            )


class _fake_science_modules:
    """Context manager that installs fake pyforestscan and rasterio modules."""

    def __enter__(self):
        self.state = {"written_bands": []}
        self.previous = {
            name: sys.modules.get(name)
            for name in ("pyforestscan", "pyforestscan.handlers", "rasterio", "rasterio.transform")
        }
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

        def calculate_pad(voxel_returns, voxel_height=1.0, beer_lambert_constant=1.0, drop_ground=True):
            self.state["pad_kwargs"] = (voxel_height, beer_lambert_constant, drop_ground)
            return np.full((2, 3, 2), 0.75, dtype="f4")

        def calculate_pai(pad, voxel_height, min_height=1.0, max_height=None):
            self.state["pai_height_range"] = (min_height, max_height)
            return np.full((2, 3), 1.25, dtype="f4")

        pyforestscan.assign_voxels = assign_voxels
        pyforestscan.calculate_pad = calculate_pad
        pyforestscan.calculate_pai = calculate_pai

        rasterio = types.ModuleType("rasterio")
        rasterio.open = self._rasterio_open
        transform = types.ModuleType("rasterio.transform")
        transform.from_bounds = lambda *args: ("transform", args)

        sys.modules["pyforestscan"] = pyforestscan
        sys.modules["pyforestscan.handlers"] = handlers
        sys.modules["rasterio"] = rasterio
        sys.modules["rasterio.transform"] = transform
        return self.state

    def __exit__(self, exc_type, exc, traceback):
        for name, module in self.previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def _rasterio_open(self, output_path, mode, **kwargs):
        return _FakeRasterWriter(Path(output_path), self.state)


class _FakeRasterWriter:
    """Tiny rasterio writer stand-in for multi-band PAD output."""

    def __init__(self, output_path: Path, state: dict[str, object]) -> None:
        self.output_path = output_path
        self.state = state

    def __enter__(self):
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.output_path.write_text("multi band geotiff", encoding="utf-8")

    def write(self, array, band_index: int) -> None:
        self.state["written_bands"].append(band_index)


if __name__ == "__main__":
    unittest.main()
