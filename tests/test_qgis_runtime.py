"""Real Processing integration checks; opt in with PYFORESTSCAN_TEST_RUNTIME=1."""

import csv
import hashlib
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
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsPointCloudLayer,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingOutputLayerDefinition,
    QgsProcessingUtils,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QT_VERSION_STR

PLUGIN_ROOT = Path(
    os.environ.get("PYFORESTSCAN_PLUGIN_PATH", Path(__file__).resolve().parents[1])
)
spec = importlib.util.spec_from_file_location(
    "pyforestscan_qgis",
    PLUGIN_ROOT / "__init__.py",
    submodule_search_locations=[str(PLUGIN_ROOT)],
)
plugin_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin_module
spec.loader.exec_module(plugin_module)
from pyforestscan_qgis.algorithms import dependency_check  # noqa: E402 -- load the selected plugin package first
from pyforestscan_qgis.algorithms.common import DependencyResult  # noqa: E402

ALGORITHMS = {
    "checkdependencies",
    "addheightaboveground",
    "calculatelidarmetrics",
    "processepttiles",
    "canopyheightmodel",
    "plantareadensity",
    "plantareaindex",
    "foliageheightdiversity",
    "canopycover",
    "voxelstatistics",
    "rumpleindex",
}
FULL_RUNTIME = os.environ.get("PYFORESTSCAN_TEST_RUNTIME") == "1"
runtime = unittest.skipUnless(
    FULL_RUNTIME, "Set PYFORESTSCAN_TEST_RUNTIME=1 for full integration"
)


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
        # Deliberately different: processing must infer the source CRS, not this one.
        self.project.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        self.context.setProject(self.project)
        self.folder = tempfile.TemporaryDirectory(prefix="pyforestscan-runtime-")
        self.root = Path(self.folder.name)

    def tearDown(self):
        self.project.removeAllMapLayers()
        self.context = None
        self.project = None
        self.plugin.unload()
        self.folder.cleanup()

    def algorithm(self, name):
        algorithm = QgsApplication.processingRegistry().createAlgorithmById(
            f"pyforestscan:{name}"
        )
        self.assertIsNotNone(algorithm, name)
        return algorithm

    def run_algorithm(self, name, parameters, error=None):
        algorithm = self.algorithm(name)
        feedback = QgsProcessingFeedback()
        valid, message = algorithm.checkParameterValues(parameters, self.context)
        if error is None:
            self.assertTrue(valid, message)
        result, ok = algorithm.run(parameters, self.context, feedback)
        if error is None:
            self.assertTrue(ok, feedback.textLog())
        else:
            self.assertFalse(ok, f"Expected failure containing {error!r}")
            self.assertIn(error.lower(), feedback.textLog().lower())
        return result

    def test_provider_registration_idempotence_and_unload(self):
        self.assertEqual(
            {a.name() for a in self.plugin.provider.algorithms()}, ALGORITHMS
        )
        self.assertFalse(self.plugin.provider.icon().isNull())
        provider = self.plugin.provider
        self.plugin.initGui()
        self.plugin.initProcessing()
        self.assertIs(self.plugin.provider, provider)
        self.plugin.unload()
        self.plugin.unload()
        self.assertIsNone(
            QgsApplication.processingRegistry().providerById("pyforestscan")
        )
        self.plugin.initGui()
        self.assertEqual(len(self.plugin.provider.algorithms()), len(ALGORITHMS))

    def test_native_parameters_and_help(self):
        for name in (
            "addheightaboveground",
            "canopyheightmodel",
            "calculatelidarmetrics",
        ):
            algorithm = self.algorithm(name)
            self.assertEqual(
                algorithm.parameterDefinition("INPUT_LAYER").type(), "pointcloud"
            )
            self.assertEqual(algorithm.parameterDefinition("INPUT").type(), "string")
            self.assertIn("HeightAboveGround", algorithm.shortHelpString())
            self.assertTrue(algorithm.helpUrl().startswith("file:"))
        algorithm = self.algorithm("addheightaboveground")
        self.assertEqual(
            algorithm.parameterDefinition("OUTPUT").type(), "pointCloudDestination"
        )
        for name in (
            "canopyheightmodel",
            "plantareadensity",
            "plantareaindex",
            "foliageheightdiversity",
            "canopycover",
            "voxelstatistics",
        ):
            algorithm = self.algorithm(name)
            self.assertEqual(
                algorithm.parameterDefinition("OUTPUT").type(), "rasterDestination"
            )
            parameter = algorithm.parameterDefinition("VOXEL_XY")
            self.assertTrue(parameter.checkValueIsAcceptable(0.5, self.context))
            self.assertFalse(parameter.checkValueIsAcceptable(0, self.context))

    def test_reader_detection_preserves_local_punctuation(self):
        from pyforestscan_qgis.algorithms.point_cloud import point_cloud_reader

        for path in ("/tmp/forest#one.las", "/tmp/forest?one.laz"):
            self.assertEqual(point_cloud_reader(path), "readers.las")
        self.assertEqual(
            point_cloud_reader("https://example.com/ept.json?token=x#fragment"),
            "readers.ept",
        )
        self.assertEqual(
            point_cloud_reader("https://example.com/forest.copc.laz?token=x"),
            "readers.copc",
        )

    def test_dependency_errors_are_reported(self):
        results = [
            DependencyResult(
                "pdal",
                "PDAL Python bindings",
                False,
                error="ImportError: library not loaded",
            )
        ]
        with patch.object(dependency_check, "check_dependencies", return_value=results):
            result = self.run_algorithm("checkdependencies", {})
        self.assertTrue(result["STATUS"].startswith("FAIL\n"))
        self.assertIn("library not loaded", result["STATUS"])

    def create_point_cloud(self, name="forest.las", normalized=False):
        import numpy as np
        import pdal

        # Flat 4 m canopy over 100 m terrain, with one unsampled cell.
        fields = [
            ("X", "<f8"),
            ("Y", "<f8"),
            ("Z", "<f8"),
            ("Classification", "u1"),
            ("CustomAttribute", "<f8"),
        ]
        rows = [
            (500000.123456 + x, 4100000.123456 + y, z, classification, 42.25)
            for x in range(5)
            for y in range(5)
            if (x, y) != (2, 2)
            for z, classification in ((100.0, 2), (104.0, 5))
        ]
        if normalized:
            fields.append(("HeightAboveGround", "<f8"))
            rows = [(*row, row[2] - 100) for row in rows]
        points = np.array(rows, dtype=fields)
        path = self.root / name
        # PDAL writers interpret # as a point-view placeholder; rename the fixture
        # afterward so readers are tested with a literal # in its real filename.
        write_path = path.with_name(path.name.replace("#", "-"))
        pdal.Pipeline(
            json.dumps(
                [
                    {
                        "type": "writers.las",
                        "filename": str(write_path),
                        "a_srs": "EPSG:32610",
                        "minor_version": 4,
                        "dataformat_id": 6,
                        "extra_dims": "all",
                        "compression": name.endswith(".laz"),
                        "scale_x": 0.000001,
                        "scale_y": 0.000001,
                        "scale_z": 0.001,
                        "offset_x": 500000,
                        "offset_y": 4100000,
                    }
                ]
            ),
            arrays=[points],
        ).execute()
        if write_path != path:
            write_path.rename(path)
        return path, points

    def load_cloud(self, path):
        options = QgsPointCloudLayer.LayerOptions()
        options.skipIndexGeneration = True
        layer = QgsPointCloudLayer(str(path), Path(path).stem, "pdal", options)
        self.assertTrue(layer.isValid(), str(path))
        self.project.addMapLayer(layer)
        return layer

    def read_cloud(self, path):
        import pdal

        pipeline = pdal.Pipeline(json.dumps([str(path)]))
        pipeline.execute()
        return pipeline.arrays[0]

    def create_ept(self, points):
        ept = self.root / "ept"
        (ept / "ept-data").mkdir(parents=True)
        (ept / "ept-hierarchy").mkdir()
        points.tofile(ept / "ept-data" / "0-0-0-0.bin")
        (ept / "ept-hierarchy" / "0-0-0-0.json").write_text(
            json.dumps({"0-0-0-0": len(points)})
        )
        (ept / "ept.json").write_text(
            json.dumps(
                {
                    "bounds": [499990, 4099990, 90, 500022, 4100022, 122],
                    "boundsConforming": [
                        float(points["X"].min()),
                        float(points["Y"].min()),
                        float(points["Z"].min()),
                        float(points["X"].max()),
                        float(points["Y"].max()),
                        float(points["Z"].max()),
                    ],
                    "dataType": "binary",
                    "hierarchyType": "json",
                    "points": len(points),
                    "schema": [
                        {
                            "name": name,
                            "type": "unsigned"
                            if name == "Classification"
                            else "floating",
                            "size": points.dtype[name].itemsize,
                        }
                        for name in points.dtype.names
                    ],
                    "span": 128,
                    "srs": {"authority": "EPSG", "horizontal": "32610"},
                    "version": "1.0.0",
                }
            )
        )
        return ept / "ept.json"

    def create_dtm(
        self, name="dtm.tif", crs="EPSG:32610", value=100, nodata=-9999, outside=False
    ):
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin

        path = self.root / name
        origin = (0, 6) if outside else (500000, 4100006)
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=6,
            height=6,
            count=1,
            dtype="float32",
            crs=crs,
            nodata=nodata,
            transform=from_origin(*origin, 1, 1),
        ) as raster:
            raster.write(np.full((6, 6), value, dtype="float32"), 1)
        return path

    def raster_values(self, path):
        import rasterio

        layer = QgsRasterLayer(str(path), Path(path).stem)
        self.assertTrue(layer.isValid(), str(path))
        self.assertEqual(layer.crs().authid(), "EPSG:32610")
        with rasterio.open(path) as raster:
            self.assertEqual(raster.res, (1, 1))
            self.assertEqual(raster.crs.to_epsg(), 32610)
            self.assertTrue(math.isnan(raster.nodata))
            return raster.read()

    @runtime
    def test_hag_layers_precision_and_metrics_reuse(self):
        import numpy as np

        for suffix in ("las", "laz"):
            with self.subTest(suffix=suffix):
                source, _ = self.create_point_cloud(f"source#punctuation.{suffix}")
                before = hashlib.sha256(source.read_bytes()).digest()
                original = self.read_cloud(source)
                layer = self.load_cloud(source)
                output = self.root / f"hag.{suffix}"
                result = self.run_algorithm(
                    "addheightaboveground",
                    {
                        "INPUT_LAYER": layer.id(),
                        "OUTPUT": QgsProcessingOutputLayerDefinition(
                            str(output), self.project
                        ),
                    },
                )
                self.assertEqual(result["OUTPUT"], str(output))
                self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)
                actual = self.read_cloud(output)
                for dimension in original.dtype.names:
                    np.testing.assert_array_equal(
                        actual[dimension], original[dimension]
                    )
                np.testing.assert_array_equal(
                    actual["HeightAboveGround"], original["Z"] - 100
                )
                normalized = self.load_cloud(output)
                self.assertIn(
                    "HeightAboveGround",
                    [a.name() for a in normalized.attributes().attributes()],
                )
                self.assertEqual(
                    self.context.layersToLoadOnCompletion()[str(output)].layerTypeHint,
                    QgsProcessingUtils.LayerHint.PointCloud,
                )
                chm = self.run_algorithm(
                    "canopyheightmodel",
                    {
                        "INPUT_LAYER": normalized,
                        "OUTPUT": str(self.root / f"chm_{suffix}.tif"),
                    },
                )["OUTPUT"]
                values = self.raster_values(chm)
                np.testing.assert_allclose(values[np.isfinite(values)], 4.0)
                self.assertTrue(np.isnan(values).any())

    @runtime
    def test_hag_dtm_validation_and_atomic_failures(self):
        import numpy as np

        source, _ = self.create_point_cloud()
        output = self.root / "dtm_hag.las"
        result = self.run_algorithm(
            "addheightaboveground",
            {
                "INPUT": str(source),
                "HAG_METHOD": 1,
                "DTM": str(self.create_dtm()),
                "OUTPUT": str(output),
            },
        )
        actual = self.read_cloud(result["OUTPUT"])
        np.testing.assert_array_equal(actual["HeightAboveGround"], actual["Z"] - 100)
        original_output = output.read_bytes()
        for dtm, error in (
            (self.create_dtm("nodata.tif", value=-9999), "valid band 1"),
            (self.create_dtm("wrong_crs.tif", crs="EPSG:32611"), "same horizontal CRS"),
            (
                self.create_dtm("outside.tif", nodata=None, outside=True),
                "terrain raster",
            ),
        ):
            self.run_algorithm(
                "addheightaboveground",
                {
                    "INPUT": str(source),
                    "HAG_METHOD": 1,
                    "DTM": str(dtm),
                    "OUTPUT": str(output),
                },
                error=error,
            )
            self.assertEqual(output.read_bytes(), original_output)
        self.assertEqual(list(self.root.glob(".pyforestscan-hag-*")), [])
        dtm = self.root / "dtm.tif"
        alias = self.root / "terrain_alias.las"
        os.link(dtm, alias)
        before = dtm.read_bytes()
        self.run_algorithm(
            "addheightaboveground",
            {
                "INPUT": str(source),
                "HAG_METHOD": 1,
                "DTM": str(dtm),
                "OUTPUT": str(alias),
            },
            error="cannot replace",
        )
        self.assertEqual(dtm.read_bytes(), before)

    @runtime
    def test_input_errors_and_source_protection(self):
        source, _ = self.create_point_cloud()
        layer = self.load_cloud(source)
        output = str(self.root / "output.las")
        cases = [
            ({"OUTPUT": output}, "Choose a point cloud"),
            (
                {"INPUT": str(source), "INPUT_LAYER": layer, "OUTPUT": output},
                "not both",
            ),
            (
                {"INPUT": str(source), "INPUT_CRS": "EPSG:32611", "OUTPUT": output},
                "stored CRS",
            ),
            ({"INPUT": str(source), "OUTPUT": str(source)}, "must differ"),
        ]
        alias = self.root / "source_alias.las"
        alias.symlink_to(source)
        cases.append(({"INPUT": str(source), "OUTPUT": str(alias)}, "must differ"))
        cases.append(
            (
                {
                    "INPUT": str(source),
                    "OUTPUT": str(self.root / "output#folder" / "hag.las"),
                },
                "folder without #",
            )
        )
        for parameters, error in cases:
            self.run_algorithm("addheightaboveground", parameters, error=error)
        # A COPC index supports layer expressions without launching background indexing.
        import pdal

        copc_path = self.root / "indexed.copc.laz"
        pdal.Pipeline(
            json.dumps(
                [
                    str(source),
                    {
                        "type": "writers.copc",
                        "filename": str(copc_path),
                        "forward": "all",
                        "extra_dims": "all",
                    },
                ]
            )
        ).execute()
        indexed = QgsPointCloudLayer(str(copc_path), "Indexed", "copc")
        self.assertTrue(indexed.isValid())
        self.project.addMapLayer(indexed)
        result = self.run_algorithm(
            "addheightaboveground", {"INPUT_LAYER": indexed, "OUTPUT": output}
        )
        original, actual = self.read_cloud(copc_path), self.read_cloud(result["OUTPUT"])
        import numpy as np

        for name in original.dtype.names:
            np.testing.assert_array_equal(actual[name], original[name])
        np.testing.assert_array_equal(actual["HeightAboveGround"], actual["Z"] - 100)
        self.assertTrue(indexed.setSubsetString("Classification = 2"))
        self.run_algorithm(
            "addheightaboveground",
            {"INPUT_LAYER": indexed, "OUTPUT": output},
            error="filter",
        )

    @runtime
    def test_missing_height_geographic_crs_and_transformed_extent(self):
        import pdal

        source, _ = self.create_point_cloud()
        self.run_algorithm(
            "canopyheightmodel",
            {"INPUT": str(source), "OUTPUT": str(self.root / "missing.tif")},
            error="HeightAboveGround",
        )
        geographic = self.root / "geographic.las"
        pdal.Pipeline(
            json.dumps(
                [
                    str(source),
                    {"type": "filters.reprojection", "out_srs": "EPSG:4326"},
                    {
                        "type": "writers.las",
                        "filename": str(geographic),
                        "a_srs": "EPSG:4326",
                        "scale_x": 1e-9,
                        "scale_y": 1e-9,
                        "offset_x": "auto",
                        "offset_y": "auto",
                    },
                ]
            )
        ).execute()
        self.run_algorithm(
            "addheightaboveground",
            {"INPUT": str(geographic), "OUTPUT": str(self.root / "geo_hag.las")},
            error="projected",
        )
        normalized, _ = self.create_point_cloud("normalized.las", normalized=True)
        transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem("EPSG:32610"),
            QgsCoordinateReferenceSystem("EPSG:4326"),
            self.project,
        )
        bounds = transform.transformBoundingBox(
            QgsRectangle(500000, 4100000, 500002, 4100003)
        )
        text = f"{bounds.xMinimum()},{bounds.xMaximum()},{bounds.yMinimum()},{bounds.yMaximum()} [EPSG:4326]"
        result = self.run_algorithm(
            "calculatelidarmetrics",
            {
                "INPUT": str(normalized),
                "BOUNDS": text,
                "METRICS": [5],
                "OUTPUT_FOLDER": str(self.root / "extent"),
            },
        )
        self.assertEqual(float(self.raster_values(result["POINT_DENSITY"]).sum()), 12.0)

    @runtime
    def test_metric_values_typed_outputs_and_pad_bands(self):
        import numpy as np
        import rasterio

        source, points = self.create_point_cloud(normalized=True)
        for label, path in (("las", source), ("ept", self.create_ept(points))):
            result = self.run_algorithm(
                "calculatelidarmetrics",
                {
                    "INPUT": str(path),
                    "VOXEL_Z": 2.0,
                    "MIN_HEIGHT": 2.0,
                    "COVER_MIN_HEIGHT": 2.0,
                    "METRICS": list(range(8)),
                    "OUTPUT_FOLDER": str(self.root / label / "metrics"),
                },
            )
            self.assertEqual(len(result["OUTPUT_FILES"].splitlines()), 8)
            expected = {
                "CHM": 4.0,
                "PAI": math.log(2),
                "FHD": math.log(2),
                "CANOPY_COVER": 1 - 1 / math.sqrt(2),
                "VOXEL_STATISTIC": 2.0,
            }
            for key, value in expected.items():
                values = self.raster_values(result[key])
                np.testing.assert_allclose(
                    values[np.isfinite(values)], value, rtol=1e-6
                )
                self.assertTrue(np.isnan(values).any(), key)
                self.assertIn(result[key], self.context.layersToLoadOnCompletion())
            density = self.raster_values(result["POINT_DENSITY"])
            self.assertEqual(float(density.sum()), len(points))
            with rasterio.open(result["PAD"]) as raster:
                self.assertEqual(raster.count, 2)
                self.assertTrue(np.isnan(raster.read(1)).all())
                values = raster.read(2)
                np.testing.assert_allclose(
                    values[np.isfinite(values)], math.log(2) / 2, rtol=1e-6
                )
                self.assertEqual(raster.tags(2)["height_min"], "2.0")
                self.assertEqual(raster.tags(2)["height_max"], "4.0")
            self.assertAlmostEqual(result["RUMPLE"], 1.0)
            with open(result["RUMPLE_REPORT"], newline="") as stream:
                self.assertAlmostEqual(
                    float(next(csv.DictReader(stream))["value"]), 1.0
                )

    @runtime
    def test_focused_outputs_and_loading_preference(self):
        import numpy as np

        source, _ = self.create_point_cloud(normalized=True)
        for name, expected in (
            ("canopyheightmodel", 4),
            ("plantareaindex", math.log(2)),
            ("foliageheightdiversity", math.log(2)),
            ("canopycover", 1 - 1 / math.sqrt(2)),
            ("voxelstatistics", 2),
            ("plantareadensity", math.log(2) / 2),
        ):
            output = str(self.root / f"{name}.tif")
            result = self.run_algorithm(
                name,
                {
                    "INPUT": str(source),
                    "VOXEL_Z": 2,
                    "OUTPUT": QgsProcessingOutputLayerDefinition(output, self.project),
                },
            )
            values = self.raster_values(result["OUTPUT"])
            np.testing.assert_allclose(values[np.isfinite(values)], expected, rtol=1e-6)
            self.assertIn(output, self.context.layersToLoadOnCompletion())
        self.context.setLayersToLoadOnCompletion({})
        self.run_algorithm(
            "addheightaboveground",
            {"INPUT": str(source), "OUTPUT": str(self.root / "no_load.las")},
        )
        self.run_algorithm(
            "canopyheightmodel",
            {"INPUT": str(source), "OUTPUT": str(self.root / "no_load.tif")},
        )
        self.run_algorithm(
            "calculatelidarmetrics",
            {
                "INPUT": str(source),
                "METRICS": [0],
                "LOAD_OUTPUTS": False,
                "OUTPUT_FOLDER": str(self.root / "no_load_metrics"),
            },
        )
        self.assertEqual(self.context.layersToLoadOnCompletion(), {})

    @runtime
    def test_all_crop_features_are_transformed(self):
        import numpy as np

        source, _ = self.create_point_cloud(normalized=True)
        polygons = QgsVectorLayer("MultiPolygon?crs=EPSG:4326", "Crop", "memory")
        transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem("EPSG:32610"), polygons.crs(), self.project
        )
        for xmin, xmax in ((500000, 500002), (500003, 500005)):
            geometry = QgsGeometry.fromRect(QgsRectangle(xmin, 4100000, xmax, 4100005))
            geometry.transform(transform)
            geometry.convertToMultiType()
            feature = QgsFeature()
            feature.setGeometry(geometry)
            self.assertTrue(polygons.dataProvider().addFeatures([feature])[0])
        self.project.addMapLayer(polygons)
        result = self.run_algorithm(
            "calculatelidarmetrics",
            {
                "INPUT": str(source),
                "POLYGON": polygons,
                "METRICS": [5],
                "OUTPUT_FOLDER": str(self.root / "cropped"),
            },
        )
        density = self.raster_values(result["POINT_DENSITY"])
        self.assertEqual(float(np.sum(density)), 40.0)

    @runtime
    def test_rumple_uses_loaded_raster_resolution(self):
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin

        path = self.root / "sloped_chm.tif"
        # 2 m horizontal spacing and a 2 m rise give a surface ratio sqrt(2).
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=3,
            height=3,
            count=1,
            dtype="float32",
            crs="EPSG:32610",
            nodata=float("nan"),
            transform=from_origin(500000, 4100006, 2, 2),
        ) as raster:
            raster.write(np.tile([2, 4, 6], (3, 1)).astype("float32"), 1)
        layer = QgsRasterLayer(str(path), "Sloped CHM")
        self.project.addMapLayer(layer)
        result = self.run_algorithm(
            "rumpleindex", {"INPUT": layer, "OUTPUT": str(self.root / "rumple.csv")}
        )
        self.assertAlmostEqual(result["RUMPLE"], math.sqrt(2))

    @runtime
    def test_ept_tiles_have_known_values(self):
        import numpy as np

        _, points = self.create_point_cloud(normalized=True)
        source = self.create_ept(points)
        for index, expected in enumerate((4.0, math.log(2), math.log(2))):
            result = self.run_algorithm(
                "processepttiles",
                {
                    "EPT_INPUT": str(source),
                    "BOUNDS": "500000,500005,4100000,4100005 [EPSG:32610]",
                    "TILE_WIDTH": 5,
                    "TILE_HEIGHT": 5,
                    "VOXEL_Z": 2,
                    "METRIC": index,
                    "OUTPUT_FOLDER": str(self.root / f"tiles_{index}"),
                },
            )
            paths = result["OUTPUT_FILES"].splitlines()
            self.assertEqual(len(paths), 1)
            values = self.raster_values(paths[0])
            np.testing.assert_allclose(values[np.isfinite(values)], expected, rtol=1e-6)

    @runtime
    def test_ept_chm_keeps_core_edge_cells_separate(self):
        import numpy as np
        import rasterio

        points = np.array(
            [(500001.2, 4100000.2, 105.0, 5.0), (500002.0, 4100000.2, 109.0, 9.0)],
            dtype=[
                ("X", "<f8"),
                ("Y", "<f8"),
                ("Z", "<f8"),
                ("HeightAboveGround", "<f8"),
            ],
        )
        source = self.create_ept(points)
        result = self.run_algorithm(
            "processepttiles",
            {
                "EPT_INPUT": str(source),
                "BOUNDS": "500000,500003,4100000,4100001 [EPSG:32610]",
                "TILE_WIDTH": 3,
                "TILE_HEIGHT": 1,
                "BUFFER_FRACTION": 0,
                "METRIC": 0,
                "OUTPUT_FOLDER": str(self.root / "edge_cells"),
            },
        )
        paths = result["OUTPUT_FILES"].splitlines()
        self.assertEqual(len(paths), 1)
        with rasterio.open(paths[0]) as raster:
            self.assertEqual(raster.shape, (1, 3))
            np.testing.assert_allclose(raster.read(1), [[np.nan, 5, 9]], equal_nan=True)

    @runtime
    def test_ept_fractional_tiles_have_no_zero_width_edge(self):
        import numpy as np
        import rasterio

        points = np.array(
            [
                (500000.05 + offset, 4100000.05, 105.0, 5.0)
                for offset in (0.0, 0.1, 0.2)
            ],
            dtype=[
                ("X", "<f8"),
                ("Y", "<f8"),
                ("Z", "<f8"),
                ("HeightAboveGround", "<f8"),
            ],
        )
        source = self.create_ept(points)
        result = self.run_algorithm(
            "processepttiles",
            {
                "EPT_INPUT": str(source),
                "BOUNDS": "500000,500000.3,4100000,4100000.1 [EPSG:32610]",
                "TILE_WIDTH": 0.1,
                "TILE_HEIGHT": 0.1,
                "VOXEL_XY": 0.1,
                "BUFFER_FRACTION": 0,
                "METRIC": 0,
                "OUTPUT_FOLDER": str(self.root / "fractional_tiles"),
            },
        )
        paths = result["OUTPUT_FILES"].splitlines()
        self.assertEqual(len(paths), 3)
        for index, path in enumerate(paths):
            with rasterio.open(path) as raster:
                self.assertEqual(raster.shape, (1, 1))
                self.assertAlmostEqual(raster.res[0], 0.1, places=8)
                self.assertAlmostEqual(
                    raster.bounds.left, 500000 + index * 0.1, places=8
                )
                self.assertEqual(raster.read(1).item(), 5.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
