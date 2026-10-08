"""Run with QGIS's Python; opt into real PDAL/PyForestScan jobs via the environment."""

import csv
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProject,
    QgsRasterLayer,
)
from qgis.PyQt.QtCore import QT_VERSION_STR


PLUGIN_ROOT = Path(os.environ.get("PYFORESTSCAN_PLUGIN_PATH", Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location(
    "pyforestscan_qgis", PLUGIN_ROOT / "__init__.py",
    submodule_search_locations=[str(PLUGIN_ROOT)],
)
plugin_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin_module
spec.loader.exec_module(plugin_module)

from pyforestscan_qgis.algorithms import dependency_check
from pyforestscan_qgis.algorithms.common import DependencyResult


class QgisRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = tempfile.TemporaryDirectory(prefix="pyforestscan-qgis-profile-")
        cls.app = QgsApplication([], False, cls.profile.name)
        cls.app.initQgis()
        print(f"Testing QGIS {Qgis.QGIS_VERSION}, Qt {QT_VERSION_STR}", flush=True)

    @classmethod
    def tearDownClass(cls):
        cls.app.exitQgis()
        cls.app = None
        cls.profile.cleanup()

    def setUp(self):
        self.plugin = plugin_module.classFactory(None)
        self.plugin.initGui()
        self.context = QgsProcessingContext()
        self.project = QgsProject()
        self.project.setCrs(QgsCoordinateReferenceSystem("EPSG:32610"))
        self.context.setProject(self.project)
        self.feedback = QgsProcessingFeedback()

    def tearDown(self):
        self.context = None
        self.project = None
        self.plugin.unload()

    def algorithm(self, name):
        algorithm = QgsApplication.processingRegistry().createAlgorithmById(f"pyforestscan:{name}")
        self.assertIsNotNone(algorithm)
        return algorithm

    def run_algorithm(self, name, parameters):
        algorithm = self.algorithm(name)
        valid, message = algorithm.checkParameterValues(parameters, self.context)
        self.assertTrue(valid, message)
        result, ok = algorithm.run(parameters, self.context, self.feedback)
        self.assertTrue(ok, self.feedback.textLog())
        return result

    def test_provider_registration_and_unload(self):
        self.assertEqual(
            {a.name() for a in self.plugin.provider.algorithms()},
            {"checkdependencies", "calculatelidarmetrics", "processepttiles"},
        )
        self.assertFalse(self.plugin.provider.icon().isNull())
        self.plugin.unload()
        self.assertIsNone(QgsApplication.processingRegistry().providerById("pyforestscan"))
        self.plugin.initGui()
        self.assertEqual(len(self.plugin.provider.algorithms()), 3)

    def test_input_sources_and_decimal_parameters(self):
        algorithm = self.algorithm("calculatelidarmetrics")
        source = algorithm.parameterDefinition("INPUT")
        for value in ("/tmp/forest.laz", "/tmp/forest.copc.laz", "/tmp/ept.json", "https://example.com/ept.json"):
            self.assertTrue(source.checkValueIsAcceptable(value, self.context))
        self.assertFalse(source.checkValueIsAcceptable("", self.context))
        for name in ("calculatelidarmetrics", "processepttiles"):
            algorithm = self.algorithm(name)
            parameter = algorithm.parameterDefinition("VOXEL_XY")
            self.assertTrue(parameter.checkValueIsAcceptable(0.5, self.context))
            self.assertFalse(parameter.checkValueIsAcceptable(0, self.context))
            self.assertEqual(algorithm.parameterAsDouble({"VOXEL_XY": 0.5}, "VOXEL_XY", self.context), 0.5)

    def test_dependency_errors_are_reported(self):
        results = [DependencyResult("pdal", "PDAL Python bindings", False, error="ImportError: library not loaded")]
        with patch.object(dependency_check, "check_dependencies", return_value=results):
            result = self.run_algorithm("checkdependencies", {})
        self.assertTrue(result["STATUS"].startswith("FAIL\n"))
        self.assertIn("library not loaded", result["STATUS"])

    def create_point_cloud(self, folder):
        import numpy as np
        import pdal

        points = np.array(
            [(500000.5 + x, 4100000.5 + y, z, classification)
             for x in range(11) for y in range(11)
             for z, classification in ((100.0, 2), (105.0 + x % 3 + y % 2, 5))],
            dtype=[("X", "<f8"), ("Y", "<f8"), ("Z", "<f8"), ("Classification", "u1")],
        )
        las_path = folder / "forest.las"
        pipeline = pdal.Pipeline(json.dumps([{
            "type": "writers.las", "filename": str(las_path), "a_srs": "EPSG:32610",
        }]), arrays=[points])
        self.assertEqual(pipeline.execute(), len(points))

        ept = folder / "ept"
        (ept / "ept-data").mkdir(parents=True)
        (ept / "ept-hierarchy").mkdir()
        points.tofile(ept / "ept-data" / "0-0-0-0.bin")
        (ept / "ept-hierarchy" / "0-0-0-0.json").write_text(json.dumps({"0-0-0-0": len(points)}))
        (ept / "ept.json").write_text(json.dumps({
            "bounds": [499990, 4099990, 90, 500022, 4100022, 122],
            "boundsConforming": [500000.5, 4100000.5, 100, 500010.5, 4100010.5, 108],
            "dataType": "binary", "hierarchyType": "json", "points": len(points),
            "schema": [
                {"name": name, "type": "floating", "size": 8} for name in ("X", "Y", "Z")
            ] + [{"name": "Classification", "type": "unsigned", "size": 1}],
            "span": 128, "srs": {"authority": "EPSG", "horizontal": "32610"}, "version": "1.0.0",
        }))
        return las_path, ept / "ept.json"

    def assert_raster(self, path):
        import numpy as np
        import rasterio

        self.assertTrue(Path(path).is_file())
        layer = QgsRasterLayer(str(path), Path(path).stem)
        self.assertTrue(layer.isValid(), str(path))
        self.assertEqual(layer.crs().authid(), "EPSG:32610")
        with rasterio.open(path) as raster:
            self.assertGreater(raster.width, 0)
            self.assertGreater(raster.height, 0)
            self.assertTrue(np.isfinite(raster.read(1)).any())

    @unittest.skipUnless(os.environ.get("PYFORESTSCAN_TEST_RUNTIME") == "1", "Set PYFORESTSCAN_TEST_RUNTIME=1 for PDAL/PyForestScan integration")
    def test_real_lidar_and_ept_processing(self):
        import numpy as np

        report = self.run_algorithm("checkdependencies", {})["STATUS"]
        self.assertTrue(report.startswith("OK\n"), report)
        with tempfile.TemporaryDirectory(prefix="pyforestscan-lidar-") as directory:
            folder = Path(directory)
            las_path, ept_path = self.create_point_cloud(folder)
            for source in (las_path, ept_path):
                with self.subTest(source=source.name):
                    result = self.run_algorithm("calculatelidarmetrics", {
                        "INPUT": str(source), "HAG_METHOD": 1, "METRICS": list(range(7)),
                        "OUTPUT_FOLDER": str(folder / source.stem / "metrics"),
                    })
                    outputs = result["OUTPUT_FILES"].splitlines()
                    self.assertEqual(len(outputs), 7)
                    for path in outputs:
                        if path.endswith(".tif"):
                            self.assert_raster(path)
                            self.assertIn(path, self.context.layersToLoadOnCompletion())
                        elif path.endswith(".npz"):
                            with np.load(path) as data:
                                self.assertEqual(data["pad"].ndim, 3)
                                self.assertGreater(data["pad"].size, 0)
                    with open(result["RUMPLE_REPORT"], newline="") as stream:
                        rumple = float(next(csv.DictReader(stream))["value"])
                    self.assertTrue(math.isfinite(rumple))
                    self.assertGreaterEqual(rumple, 1.0)

            for index, metric in enumerate(("chm", "pai", "fhd")):
                with self.subTest(tiled_metric=metric):
                    result = self.run_algorithm("processepttiles", {
                        "EPT_INPUT": str(ept_path), "SRS": "EPSG:32610", "HAG_METHOD": 1,
                        "BOUNDS": "500000,500011,4100000,4100011 [EPSG:32610]",
                        "TILE_WIDTH": 11, "TILE_HEIGHT": 11, "METRIC": index,
                        "OUTPUT_FOLDER": str(folder / "tiles" / metric),
                    })
                    outputs = result["OUTPUT_FILES"].splitlines()
                    self.assertEqual(len(outputs), 1)
                    self.assert_raster(outputs[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
