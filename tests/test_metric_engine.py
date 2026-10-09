"""Analytic checks for forest metrics and GIS outputs; no QGIS runtime needed."""

import importlib
import types
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PLUGIN_ROOT = Path(
    os.environ.get("PYFORESTSCAN_PLUGIN_PATH", Path(__file__).resolve().parents[1])
)
_package = types.ModuleType("_pyforestscan_metric_tests")
_package.__path__ = [str(PLUGIN_ROOT)]
sys.modules[_package.__name__] = _package
try:
    import numpy as np
    import rasterio
    from osgeo import gdal

    calculate = importlib.import_module(
        _package.__name__ + ".vendor.pyforestscan.calculate"
    )
    _engine = importlib.import_module(_package.__name__ + ".algorithms.metric_engine")
except (ImportError, OSError) as exc:
    if os.environ.get("PYFORESTSCAN_TEST_RUNTIME") == "1":
        raise
    raise unittest.SkipTest(
        f"Metric tests require NumPy, SciPy, GDAL and rasterio: {exc}"
    ) from exc

MetricResult = _engine.MetricResult
compute_metrics = _engine.compute_metrics
write_metric_raster = _engine.write_metric_raster


def points(rows):
    return np.array(rows, dtype=[("X", "f8"), ("Y", "f8"), ("HeightAboveGround", "f8")])


class MetricEngineTests(unittest.TestCase):
    def forest(self):
        return points(
            [
                (x, y, height)
                for x, y in ((0.1, 0.1), (2.1, 2.1))
                for height in (0.0, 0.75, 1.25)
            ]
        )

    def test_nonunit_height_bins_preserve_pai_cover_and_entropy(self):
        cloud = self.forest()
        result = compute_metrics(
            cloud,
            ["PAD", "PAI", "FHD", "canopy cover", "point density"],
            z_resolution=0.5,
            min_height=0.5,
            cover_min_height=0.5,
        )
        # Three equally populated height bins; lower two transmit two and one
        # returns. Integrating PAD cancels dz and gives ln(3), not 2*ln(3).
        self.assertAlmostEqual(result["PAI"].data[0, 2], math.log(3))
        self.assertAlmostEqual(result["canopy cover"].data[0, 2], 1 - 1 / math.sqrt(3))
        self.assertAlmostEqual(result["FHD"].data[0, 2], math.log(3))
        self.assertEqual(result["point density"].data[0, 2], 3)
        self.assertEqual(result["point density"].data.sum(), len(cloud))
        self.assertTrue(np.isnan(result["PAI"].data[1, 1]))
        self.assertTrue(np.isnan(result["PAD"].data[:, :, 0]).all())
        voxels, extent = calculate.assign_voxels(cloud, (1, 1, 0.5))
        expected_pad = calculate.calculate_pad(voxels, 0.5)
        np.testing.assert_allclose(result["PAD"].data, expected_pad, equal_nan=True)
        np.testing.assert_allclose(
            result["canopy cover"].data,
            calculate.calculate_canopy_cover(expected_pad, 0.5, min_height=0.5),
            equal_nan=True,
        )
        self.assertEqual(result["PAD"].extent, tuple(extent))

    def test_above_canopy_threshold_keeps_empty_cells_nodata(self):
        result = compute_metrics(
            self.forest(), ["PAI", "canopy cover"], min_height=100, cover_min_height=100
        )
        for metric in result.values():
            self.assertEqual(metric.data[0, 2], 0)
            self.assertTrue(np.isnan(metric.data[1, 1]))

    def test_rumple_known_inclined_surface(self):
        cloud = points(
            [(x, y, 2 + x) for x in range(3) for y in range(3)] + [(2.1, 2.1, 4)]
        )
        result = compute_metrics(cloud, ["CHM", "rumple"])
        np.testing.assert_array_equal(
            result["CHM"].data, [[2, 2, 2], [3, 3, 3], [4, 4, 4]]
        )
        self.assertAlmostEqual(result["rumple"], math.sqrt(2))
        self.assertEqual(result["CHM"].extent, (0, 3, 0, 3))

    def test_chm_leaves_unsampled_cells_uninterpolated(self):
        cloud = points(
            [
                (x, y, 1 + 2 * x + 3 * y)
                for x in range(6)
                for y in range(4)
                if (x, y) != (2, 1)
            ]
            + [(5.9, 3.9, 20)]
        )
        chm = compute_metrics(cloud, ["CHM"])["CHM"]
        self.assertTrue(np.isnan(chm.data[2, 2]))
        self.assertEqual(chm.extent, (0, 6, 0, 4))

    def test_ground_only_and_single_coordinate_inputs(self):
        cloud = points([(1, 1, 0), (1, 1, 0)])
        result = compute_metrics(
            cloud,
            ["CHM", "PAD", "PAI", "FHD", "canopy cover", "point density", "rumple"],
        )
        self.assertEqual(result["CHM"].data.shape, (1, 1))
        self.assertEqual(result["CHM"].data.item(), 0)
        self.assertEqual(result["PAD"].data.shape, (1, 1, 1))
        self.assertEqual(result["PAI"].data.item(), 0)
        self.assertEqual(result["FHD"].data.item(), 0)
        self.assertEqual(result["canopy cover"].data.item(), 0)
        self.assertEqual(result["point density"].data.item(), 2)
        self.assertTrue(math.isnan(result["rumple"]))
        cloud = points([(1, 1, 0), (1, 1, 0.75), (1, 1, 1.25)])
        result = compute_metrics(
            cloud, ["PAI", "point density"], z_resolution=0.5, min_height=0.5
        )
        self.assertAlmostEqual(result["PAI"].data.item(), math.log(3))
        self.assertEqual(result["point density"].data.item(), 3)

    def test_invalid_and_negative_points_are_excluded_consistently(self):
        cloud = np.concatenate(
            [self.forest(), points([(100, 100, -1), (np.nan, 0, 3), (1, 1, np.inf)])]
        )
        result = compute_metrics(cloud, ["CHM", "point density"])
        self.assertEqual(result["point density"].data.sum(), len(self.forest()))
        self.assertEqual(result["CHM"].extent, (0.1, 2.1, 0.1, 2.1))
        for cloud in (points([]), points([(1, 1, -1)]), points([(1, 1, np.nan)])):
            with self.assertRaises(ValueError):
                compute_metrics(cloud, ["CHM"])

    def test_voxel_statistic_and_density_units(self):
        result = compute_metrics(
            self.forest(), ["voxel statistic", "point density"], xy_resolution=2
        )
        self.assertAlmostEqual(result["voxel statistic"].data[0, 1], 2 / 3)
        self.assertEqual(result["point density"].data[0, 1], 3 / 4)
        self.assertEqual(result["point density"].extent, (0, 4, 0, 4))

    def test_large_grids_rejected_before_core_allocation(self):
        with patch.object(
            calculate, "assign_voxels", side_effect=AssertionError("must not allocate")
        ):
            with self.assertRaisesRegex(ValueError, "memory limit"):
                compute_metrics(points([(0.1, 0.1, 0), (1000000, 1000000, 1)]), ["PAD"])
            with self.assertRaisesRegex(ValueError, "memory limit"):
                compute_metrics(points([(0.1, 0.1, 100000000)]), ["PAD"])

    def test_column_statistics_and_degenerate_chm_guard_height_edges(self):
        cloud = points([(1, 1, 1e9)])
        with patch.object(
            calculate,
            "calculate_voxel_stat",
            side_effect=AssertionError("must not allocate"),
        ):
            for metric in ("voxel statistic", "CHM", "rumple"):
                with (
                    self.subTest(metric=metric),
                    self.assertRaisesRegex(ValueError, "memory limit"),
                ):
                    compute_metrics(cloud, [metric])

    def test_pad_band_limit_rejected_before_writing(self):
        result = MetricResult(np.zeros((1, 1, 65536), dtype="u1"), (0, 1, 0, 1))
        with tempfile.TemporaryDirectory(prefix="pyforestscan-metrics-") as folder:
            path = Path(folder) / "too-many-bands.tif"
            with patch.object(
                gdal, "GetDriverByName", side_effect=AssertionError("must not write")
            ):
                with self.assertRaisesRegex(ValueError, "65,535-band"):
                    write_metric_raster(result, path, "EPSG:32610")
            self.assertFalse(path.exists())

    def test_invalid_parameters(self):
        for kwargs in (
            {"z_resolution": 0},
            {"xy_resolution": float("nan")},
            {"cover_k": -1},
        ):
            with self.assertRaises(ValueError):
                compute_metrics(self.forest(), ["CHM"], **kwargs)
        with self.assertRaises(ValueError):
            compute_metrics(self.forest(), ["unknown"])

    def test_geotiff_orientation_extent_and_pad_metadata(self):
        grid = np.array([[11, 12], [21, np.nan], [31, 32]], dtype=float)
        result = MetricResult(grid, (100, 106, 200, 204))
        with tempfile.TemporaryDirectory(prefix="pyforestscan-metrics-") as folder:
            path = write_metric_raster(result, Path(folder) / "grid.tif", "EPSG:32610")
            with rasterio.open(path) as raster:
                self.assertEqual(raster.res, (2, 2))
                self.assertEqual(tuple(raster.bounds), (100, 200, 106, 204))
                self.assertEqual(raster.crs.to_epsg(), 32610)
                self.assertEqual(raster.dtypes, ("float32",))
                self.assertTrue(math.isnan(raster.nodata))
                np.testing.assert_allclose(raster.read(1), grid.T, equal_nan=True)
            pad = MetricResult(np.stack((grid, grid + 1), axis=2), result.extent)
            path = write_metric_raster(
                pad, Path(folder) / "pad.tif", "EPSG:32610", z_resolution=0.5
            )
            with rasterio.open(path) as raster:
                self.assertEqual(raster.count, 2)
                np.testing.assert_allclose(raster.read(2), (grid + 1).T, equal_nan=True)
                self.assertEqual(raster.tags(2)["height_min"], "0.5")
                self.assertEqual(raster.tags(2)["height_max"], "1.0")
                self.assertIn("0.5–1 m", raster.descriptions[1])
                self.assertEqual(raster.tags()["pad_units"], "m2/m3")

    def test_failed_write_preserves_existing_output(self):
        result = MetricResult(np.zeros((2, 2)), (0, 2, 0, 2))
        with tempfile.TemporaryDirectory(prefix="pyforestscan-metrics-") as folder:
            path = Path(folder) / "existing.tif"
            path.write_bytes(b"previous output")
            with patch.object(
                gdal, "GetDriverByName", side_effect=RuntimeError("write failed")
            ):
                with self.assertRaises(RuntimeError):
                    write_metric_raster(result, path, "EPSG:32610")
            self.assertEqual(path.read_bytes(), b"previous output")
            self.assertEqual(
                [item.name for item in Path(folder).iterdir()], [path.name]
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
