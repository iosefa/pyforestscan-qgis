"""Exercise the isolated worker protocol and actual scientific file operations."""

import importlib.util
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace


PLUGIN_ROOT = Path(
    os.environ.get("PYFORESTSCAN_PLUGIN_PATH", Path(__file__).resolve().parents[1])
)
WORKER = PLUGIN_ROOT / "runtime" / "worker.py"
spec = importlib.util.spec_from_file_location("pyforestscan_test_worker", WORKER)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class QuietFeedback:
    def pushInfo(self, message):
        pass

    def setProgress(self, value):
        pass

    def isCanceled(self):
        return False


class WorkerProtocolTests(unittest.TestCase):
    def execute(self, request, subprocess_run=False):
        with tempfile.TemporaryDirectory() as directory:
            source, output = (
                Path(directory) / "request.json",
                Path(directory) / "result.json",
            )
            source.write_text(json.dumps(request), encoding="utf-8")
            arguments = ["--request", str(source), "--result", str(output)]
            if subprocess_run:
                process = subprocess.run(
                    [sys.executable, "-I", str(WORKER), *arguments],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            else:
                stdout, stderr = io.StringIO(), io.StringIO()
                with (
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                    patch.object(worker.signal, "signal"),
                ):
                    status = worker.main(arguments)
                process = SimpleNamespace(
                    returncode=status,
                    stdout=stdout.getvalue(),
                    stderr=stderr.getvalue(),
                )
            return process, json.loads(output.read_text(encoding="utf-8"))

    def test_protocol_failure_returns_typed_atomic_result(self):
        process, result = self.execute(
            {"protocol": 99, "operation": "selftest", "payload": {}}
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(result["protocol"], 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "ValueError")
        self.assertIn("protocol", result["error"]["message"])

    def test_unknown_operation_does_not_import_qgis(self):
        process, result = self.execute(
            {"protocol": 1, "operation": "unknown", "payload": {}}
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("Unknown worker operation", result["error"]["message"])

    def test_nonfinite_scalars_are_json_null(self):
        self.assertEqual(
            worker._safe_json({"rumple": math.nan, "values": [math.inf, 1]}),
            {"rumple": None, "values": [None, 1]},
        )


class WorkerScientificTests(unittest.TestCase):
    def execute(self, request):
        return WorkerProtocolTests.execute(self, request, subprocess_run=True)

    @classmethod
    def setUpClass(cls):
        if os.environ.get("PYFORESTSCAN_TEST_ENGINE") != "1":
            raise unittest.SkipTest(
                "Set PYFORESTSCAN_TEST_ENGINE=1 to test the isolated PDAL runtime."
            )
        try:
            worker._configure_native_libraries()
            global np, pdal, gdal, CRS, jobs
            import numpy as np
            import pdal
            from osgeo import gdal
            from pyproj import CRS

            jobs = worker._load_jobs()
        except (ImportError, OSError) as exc:
            raise RuntimeError(
                f"The isolated PDAL test runtime is incomplete: {exc}"
            ) from exc

    def fixture(self, directory):
        source = os.path.join(directory, "source.las")
        points = np.array(
            [
                (500000.1234 + x, 4100000.5678 + y, 100 + height, cls, 1000 + index)
                for index, (x, y, height, cls) in enumerate(
                    (x, y, height, cls)
                    for x in range(3)
                    for y in range(3)
                    for height, cls in ((0, 2), (5.4321, 5))
                )
            ],
            dtype=[
                ("X", "f8"),
                ("Y", "f8"),
                ("Z", "f8"),
                ("Classification", "u1"),
                ("TreeId", "u2"),
            ],
        )
        writer = {
            "type": "writers.las",
            "filename": source,
            "a_srs": "EPSG:32610",
            "minor_version": 4,
            "extra_dims": "all",
            "scale_x": 0.0001,
            "scale_y": 0.0001,
            "scale_z": 0.0001,
            "offset_x": 500000,
            "offset_y": 4100000,
            "offset_z": 100,
        }
        pdal.Pipeline(json.dumps([writer]), arrays=[points]).execute()
        prepared = {
            "path": source,
            "crs_wkt": CRS("EPSG:32610").to_wkt(),
            "reader": {"type": "readers.las", "filename": source},
            "filters": [{"type": "filters.hag_delaunay"}],
            "hag_method": "delaunay",
            "dtm_path": None,
        }
        return prepared

    def saved_hag_fixture(self, directory):
        prepared = self.fixture(directory)
        output = os.path.join(directory, "with-height.las")
        jobs.hag({"prepared": prepared, "output": output}, QuietFeedback())
        return dict(
            prepared,
            path=output,
            reader={"type": "readers.las", "filename": output},
            filters=[],
            hag_method="none",
        )

    def test_actual_selftest_in_isolated_subprocess(self):
        process, result = self.execute(
            {"protocol": 1, "operation": "selftest", "payload": {}}
        )
        self.assertEqual(process.returncode, 0, result)
        self.assertTrue(result["ok"])
        self.assertTrue(result["result"]["ready"])
        events = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertTrue(any(event.get("type") == "log" for event in events))

    def test_hag_preserves_dimensions_precision_and_protects_source(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared = self.fixture(directory)
            source_bytes = Path(prepared["path"]).read_bytes()
            output = os.path.join(directory, "output#folder", "height#above.las")
            result = jobs.hag({"prepared": prepared, "output": output}, QuietFeedback())
            self.assertEqual(result["count"], 18)
            original = pdal.Pipeline(json.dumps([prepared["reader"]]))
            original.execute()
            actual = pdal.Pipeline(
                json.dumps([{"type": "readers.las", "filename": output}])
            )
            actual.execute()
            for name in original.arrays[0].dtype.names:
                np.testing.assert_array_equal(
                    actual.arrays[0][name], original.arrays[0][name]
                )
            np.testing.assert_allclose(
                actual.arrays[0]["HeightAboveGround"], np.tile([0, 5.4321], 9)
            )
            with self.assertRaisesRegex(ValueError, "cannot replace"):
                jobs.hag(
                    {"prepared": prepared, "output": prepared["path"]}, QuietFeedback()
                )
            self.assertEqual(Path(prepared["path"]).read_bytes(), source_bytes)
            self.assertFalse(list(Path(directory).rglob(".pyforestscan-*")))

    def test_metric_outputs_and_stored_raster_crs(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared = self.saved_hag_fixture(directory)
            paths = {
                name: os.path.join(directory, name + ".tif") for name in ("PAI", "PAD")
            }
            result = jobs.metrics(
                {
                    "prepared": prepared,
                    "metrics": ["PAI", "PAD"],
                    "paths": paths,
                    "options": {"z_resolution": 0.5},
                },
                QuietFeedback(),
            )
            self.assertEqual(result["files"], paths)
            dataset = gdal.Open(paths["PAI"])
            np.testing.assert_allclose(dataset.ReadAsArray(), math.log(2), rtol=1e-6)
            self.assertEqual(
                dataset.GetGeoTransform(), (500000.0, 1.0, 0.0, 4100003.0, 0.0, -1.0)
            )
            dataset = None
            dataset = gdal.Open(paths["PAD"])
            self.assertEqual(dataset.RasterCount, 11)
            dataset = None
            output = os.path.join(directory, "rumple.csv")
            value = jobs.rumple(
                {
                    "source": paths["PAI"],
                    "band": 1,
                    "output": output,
                    "crs_wkt": prepared["crs_wkt"],
                },
                QuietFeedback(),
            )
            self.assertEqual(value["rumple"], 1)
            with self.assertRaisesRegex(ValueError, "stored CRS"):
                jobs.rumple(
                    {
                        "source": paths["PAI"],
                        "output": output,
                        "crs_wkt": CRS("EPSG:32611").to_wkt(),
                    },
                    QuietFeedback(),
                )

    def test_metrics_require_saved_hag_and_preserve_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared = self.fixture(directory)
            prepared.update(filters=[], hag_method="none")
            output = Path(directory) / "untouched.tif"
            original = b"An existing output must survive validation failure."
            output.write_bytes(original)
            with self.assertRaisesRegex(ValueError, "missing the HeightAboveGround"):
                jobs.metrics(
                    {
                        "prepared": prepared,
                        "metrics": ["canopy cover"],
                        "paths": {"canopy cover": str(output)},
                    },
                    QuietFeedback(),
                )
            self.assertEqual(output.read_bytes(), original)
            self.assertFalse(list(Path(directory).glob(".pyforestscan-*")))

    def test_metric_and_tile_jobs_reject_inline_hag_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.fixture(directory)
            source.update(filters=[], hag_method="none")
            output = Path(directory) / "new-output" / "metric.tif"
            cases = (
                {"hag_method": "delaunay"},
                {"hag_method": "dtm"},
                {"dtm_path": "terrain.tif"},
                {"filters": [{"type": "filters.hag_delaunay"}]},
                {"filters": [{"type": "filters.hag_dem", "raster": "terrain.tif"}]},
            )
            for operation in (jobs.metrics, jobs.tiles):
                for settings in cases:
                    with self.subTest(operation=operation.__name__, settings=settings):
                        with patch.object(jobs, "read_points") as reader:
                            with self.assertRaisesRegex(
                                ValueError, "Run Add Height Above Ground separately"
                            ):
                                operation(
                                    {
                                        "prepared": dict(source, **settings),
                                        "metrics": ["PAI"],
                                        "paths": {"PAI": str(output)},
                                        "folder": str(output.parent),
                                    },
                                    QuietFeedback(),
                                )
                            reader.assert_not_called()
                        self.assertFalse(output.parent.exists())

    def test_tiles_reject_missing_hag_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.fixture(directory)
            pipeline = pdal.Pipeline(json.dumps([source["reader"]]))
            pipeline.execute()
            source.update(
                path="dummy-ept.json",
                reader={"type": "readers.ept", "filename": "dummy-ept.json"},
                filters=[],
                hag_method="none",
                bounds=[[500000, 500003], [4100000, 4100003]],
            )
            folder = Path(directory) / "new-tiles"
            with patch.object(jobs, "read_points", return_value=pipeline.arrays[0]):
                with self.assertRaisesRegex(
                    ValueError, "missing the HeightAboveGround"
                ):
                    jobs.tiles(
                        {"prepared": source, "folder": str(folder)}, QuietFeedback()
                    )
            self.assertFalse(folder.exists())

    def test_multiple_point_views_are_preserved(self):
        points = np.array(
            [(1, 2, 3), (4, 5, 6)], dtype=[("X", "f8"), ("Y", "f8"), ("Z", "f8")]
        )
        np.testing.assert_array_equal(
            jobs._points([points[:1], points[1:]], {"hag_method": "none"}), points
        )

    def test_dtm_nodata_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared = self.fixture(directory)
            info = jobs.metadata({"reader": prepared["reader"]})
            self.assertEqual(info["num_points"], 18)
            terrain = os.path.join(directory, "terrain.tif")
            raster = gdal.GetDriverByName("GTiff").Create(
                terrain, 3, 3, 1, gdal.GDT_Float32
            )
            raster.SetGeoTransform((500000, 1, 0, 4100003, 0, -1))
            raster.SetProjection(prepared["crs_wkt"])
            raster.GetRasterBand(1).SetNoDataValue(-9999)
            raster.GetRasterBand(1).WriteArray(np.full((3, 3), 100, dtype="float32"))
            raster = None
            prepared.update(
                hag_method="dtm",
                dtm_path=terrain,
                filters=[{"type": "filters.hag_dem", "raster": terrain}],
            )
            output = os.path.join(directory, "hag.las")
            jobs.hag({"prepared": prepared, "output": output}, QuietFeedback())
            previous = Path(output).read_bytes()
            raster = gdal.Open(terrain, gdal.GA_Update)
            band = raster.GetRasterBand(1)
            values = band.ReadAsArray()
            values[0, 0] = -9999
            band.WriteArray(values)
            band = raster = None
            with self.assertRaisesRegex(ValueError, "terrain raster must cover"):
                jobs.hag({"prepared": prepared, "output": output}, QuietFeedback())
            self.assertEqual(Path(output).read_bytes(), previous)
            self.assertFalse(list(Path(directory).glob(".pyforestscan-*")))

    def test_metric_crs_requires_metres(self):
        with self.assertRaisesRegex(ValueError, "metres"):
            jobs._crs("EPSG:2263", metres=True)

    def test_scaled_elevation_rasters_and_feet_preserve_existing_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared = self.fixture(directory)
            terrain = os.path.join(directory, "encoded-elevations.tif")
            hag_output = Path(directory) / "existing-hag.las"
            rumple_output = Path(directory) / "existing-rumple.csv"
            previous = b"An existing output must survive elevation validation failure."
            hag_output.write_bytes(previous)
            rumple_output.write_bytes(previous)
            prepared.update(
                hag_method="dtm",
                dtm_path=terrain,
                filters=[{"type": "filters.hag_dem", "raster": terrain}],
            )
            for scale, offset, unit, error in (
                (0.1, 0, "m", "scale or offset"),
                (1, 100, "m", "scale or offset"),
                (1, 0, "ft", "elevations to metres"),
            ):
                with self.subTest(scale=scale, offset=offset, unit=unit):
                    raster = gdal.GetDriverByName("GTiff").Create(
                        terrain, 3, 3, 1, gdal.GDT_Int16
                    )
                    raster.SetGeoTransform((500000, 1, 0, 4100003, 0, -1))
                    raster.SetProjection(prepared["crs_wkt"])
                    band = raster.GetRasterBand(1)
                    band.WriteArray(np.full((3, 3), 1000, dtype="int16"))
                    band.SetScale(scale)
                    band.SetOffset(offset)
                    band.SetUnitType(unit)
                    band = raster = None
                    with self.assertRaisesRegex(ValueError, error):
                        jobs.hag(
                            {"prepared": prepared, "output": str(hag_output)},
                            QuietFeedback(),
                        )
                    with self.assertRaisesRegex(ValueError, error):
                        jobs.rumple(
                            {"source": terrain, "output": str(rumple_output)},
                            QuietFeedback(),
                        )
                    self.assertEqual(hag_output.read_bytes(), previous)
                    self.assertEqual(rumple_output.read_bytes(), previous)
            self.assertFalse(list(Path(directory).glob(".pyforestscan-*")))

    def test_declared_terrain_vertical_units_require_metres(self):
        with tempfile.TemporaryDirectory() as directory:
            terrain = os.path.join(directory, "vertical_units.tif")
            points = np.array([(500000.5, 4100000.5)], dtype=[("X", "f8"), ("Y", "f8")])
            for vertical_crs, band_unit, valid in (
                ("EPSG:32610+6360", "", False),
                ("EPSG:32610+5703", "ft", False),
                ("EPSG:32610+5703", "metres", True),
            ):
                with self.subTest(crs=vertical_crs, unit=band_unit):
                    raster = gdal.GetDriverByName("GTiff").Create(
                        terrain, 1, 1, 1, gdal.GDT_Float32
                    )
                    raster.SetGeoTransform((500000, 1, 0, 4100001, 0, -1))
                    raster.SetProjection(CRS(vertical_crs).to_wkt())
                    raster.GetRasterBand(1).SetUnitType(band_unit)
                    raster.GetRasterBand(1).WriteArray(
                        np.array([[100.0]], dtype="float32")
                    )
                    raster = None
                    if valid:
                        jobs._terrain_valid(points, terrain, CRS("EPSG:32610").to_wkt())
                    else:
                        with self.assertRaisesRegex(ValueError, "metres"):
                            jobs._terrain_valid(
                                points, terrain, CRS("EPSG:32610").to_wkt()
                            )

    def test_tiled_chm_global_cells_boundaries_and_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared = {
                "path": "dummy-ept.json",
                "reader": {"type": "readers.ept", "filename": "dummy-ept.json"},
                "crs_wkt": CRS("EPSG:32610").to_wkt(),
                "filters": [],
                "hag_method": "none",
                "bounds": [[0.05, 0.6], [0.05, 0.3]],
                "dtm_path": None,
            }
            points = np.array(
                [(0.1, 0.1, 1, 1), (0.3, 0.1, 3, 3), (0.6, 0.3, 6, 6)],
                dtype=[
                    ("X", "f8"),
                    ("Y", "f8"),
                    ("Z", "f8"),
                    ("HeightAboveGround", "f8"),
                ],
            )
            payload = {
                "prepared": prepared,
                "folder": directory,
                "options": {
                    "METRIC": 0,
                    "VOXEL_XY": 0.1,
                    "TILE_WIDTH": 0.3,
                    "TILE_HEIGHT": 0.3,
                    # An unused legacy value must not affect a dialog where
                    # outlier removal and its dependent buffer are disabled.
                    "BUFFER_FRACTION": math.nan,
                },
            }
            with patch.object(jobs, "read_points", return_value=points):
                result = jobs.tiles(payload, QuietFeedback())
            self.assertEqual(len(result["files"]), 2)
            first, second = (gdal.Open(path) for path in result["files"])
            a, b = first.ReadAsArray(), second.ReadAsArray()
            self.assertEqual(a.shape, (3, 3))
            self.assertEqual(b.shape, (3, 3))
            self.assertEqual(a[1, 1], 1)
            self.assertEqual(b[1, 0], 3)
            self.assertEqual(b[0, 2], 6)
            self.assertEqual(np.isfinite(a).sum() + np.isfinite(b).sum(), 3)
            self.assertAlmostEqual(second.GetGeoTransform()[0], 0.3)
            first = second = None
            with self.assertRaisesRegex(ValueError, "already exist"):
                jobs.tiles(payload, QuietFeedback())
            payload["options"].update(
                OVERWRITE=True,
                VOXEL_Z=1e-30,
                MIN_HEIGHT=-1,
                FHD_MIN_HEIGHT=math.nan,
                COVER_MIN_HEIGHT="unused",
                COVER_K=math.inf,
            )
            with patch.object(jobs, "read_points", return_value=points):
                repeated = jobs.tiles(payload, QuietFeedback())
            for path, expected in zip(repeated["files"], (a, b)):
                dataset = gdal.Open(path)
                np.testing.assert_array_equal(dataset.ReadAsArray(), expected)
                dataset = None


if __name__ == "__main__":
    unittest.main()
