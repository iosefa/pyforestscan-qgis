"""Phase 30E automatic CRS and source-local regressions."""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from pyforestscan_qgis.core.adapter import PyForestScanAdapter
from pyforestscan_qgis.core.adapter import _write_source_local_geotiff
from pyforestscan_qgis.core.exceptions import ProcessingError
from pyforestscan_qgis.core.spatial_reference_resolver import (
    SpatialReferenceAssignmentStore,
    SpatialReferenceConfidence,
    SpatialReferenceResolver,
    SpatialReferenceStatus,
    normalize_crs,
    profile_repository,
)
from pyforestscan_qgis.core.types import ChmRequest, RumpleRequest


class SpatialReferenceResolverTests(unittest.TestCase):
    def test_epsg_wkt_and_compound_normalize_to_horizontal_authority(self):
        self.assertEqual("EPSG:32605", normalize_crs("EPSG:32605"))
        self.assertEqual("EPSG:32605", normalize_crs('PROJCRS["WGS 84 / UTM zone 5N",ID["EPSG",32605]]'))

    def test_embedded_and_ept_metadata_are_authoritative(self):
        embedded = SpatialReferenceResolver().resolve("plot.las", embedded_crs="EPSG:6635")
        ept = SpatialReferenceResolver().resolve("ept.json", ept_payload={"srs": {"authority": "EPSG", "horizontal": "6635"}})
        self.assertEqual(SpatialReferenceStatus.RESOLVED_AUTHORITATIVE, embedded.status)
        self.assertEqual("EPSG:6635", ept.resolved_crs)
        self.assertEqual(SpatialReferenceConfidence.AUTHORITATIVE, ept.confidence)

    def test_exact_sidecar_and_qgis_datasource_assignment_resolve(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "plot.las"
            source.touch()
            source.with_suffix(".prj").write_text("EPSG:6635", encoding="utf-8")
            sidecar = SpatialReferenceResolver().resolve(source)
            self.assertEqual("EPSG:6635", sidecar.resolved_crs)
            source.with_suffix(".prj").unlink()
            qgis = SpatialReferenceResolver().resolve(source, qgis_context={str(source): "EPSG:32605"})
            self.assertEqual("EPSG:32605", qgis.resolved_crs)
            self.assertEqual("qgis_layer_assignment", qgis.source)

    def test_supported_json_sidecar_and_local_ept_file_resolve(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "plot.las"
            source.touch()
            source.with_suffix(".metadata.json").write_text('{"epsg": 6635}', encoding="utf-8")
            self.assertEqual("EPSG:6635", SpatialReferenceResolver().resolve(source).resolved_crs)
            ept = root / "ept.json"
            ept.write_text('{"srs": {"authority": "EPSG", "horizontal": "32605"}}', encoding="utf-8")
            self.assertEqual("EPSG:32605", SpatialReferenceResolver().resolve(ept).resolved_crs)

    def test_repository_consensus_inherits_unknown_members(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sources = [SimpleNamespace(embedded_crs="EPSG:6635") for _ in range(90)] + [SimpleNamespace(embedded_crs=None) for _ in range(10)]
            profile = profile_repository(root, sources)
            result = SpatialReferenceResolver().resolve(root / "unknown.las", repository_context=profile)
            self.assertTrue(profile.can_inherit)
            self.assertEqual(90, profile.agreement_count)
            self.assertEqual(SpatialReferenceStatus.RESOLVED_REPOSITORY_INHERITANCE, result.status)

    def test_mixed_repository_and_conflicting_sidecar_do_not_silently_choose(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = profile_repository(root, [SimpleNamespace(embedded_crs="EPSG:6635") for _ in range(50)] + [SimpleNamespace(embedded_crs="EPSG:32605") for _ in range(50)])
            self.assertEqual("conflict", profile.status)
            self.assertFalse(profile.can_inherit)
            source = root / "plot.las"
            source.touch()
            source.with_suffix(".prj").write_text("EPSG:32605", encoding="utf-8")
            result = SpatialReferenceResolver().resolve(source, embedded_crs="EPSG:6635")
            self.assertEqual(SpatialReferenceStatus.CONFLICT, result.status)

    def test_unknown_standalone_is_source_local_but_polygon_requires_assignment(self):
        standalone = SpatialReferenceResolver().resolve("plot.las", source_local_allowed=True)
        polygon = SpatialReferenceResolver().resolve("plot.las", source_local_allowed=True, spatial_alignment_required=True, polygon_crs="EPSG:6635")
        suggestion = SpatialReferenceResolver().resolve("plot.las", spatial_alignment_required=True, polygon_crs="EPSG:6635", project_crs="EPSG:6635")
        self.assertEqual(SpatialReferenceStatus.SOURCE_LOCAL_ONLY, standalone.status)
        self.assertTrue(standalone.safe_for_source_local_processing)
        self.assertEqual(SpatialReferenceStatus.AMBIGUOUS, polygon.status)
        self.assertTrue(polygon.user_action_required)
        self.assertEqual(SpatialReferenceConfidence.MEDIUM, suggestion.confidence)

    def test_persisted_repository_assignment_is_remembered_and_invalidated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "repo"
            root.mkdir()
            source = root / "plot.las"
            source.touch()
            store = SpatialReferenceAssignmentStore(Path(folder) / "assignments.json")
            store.assign_repository(root, "EPSG:6635")
            self.assertEqual("EPSG:6635", SpatialReferenceResolver(store).resolve(source).resolved_crs)
            (root / "new.las").touch()
            self.assertEqual(SpatialReferenceStatus.INVALID, SpatialReferenceResolver(store).resolve(source).status)
            store.clear_repository(root)


class SourceLocalAdapterTests(unittest.TestCase):
    def setUp(self):
        # These mocked scientific calls model a managed worker, even when a
        # previous test has imported QGIS for CRS handling in this interpreter.
        self.enterContext(patch.dict("os.environ", {"PYFORESTSCAN_MANAGED_ENGINE": "1"}))
        self.points = np.array([(268228.0, 2152129.0, 4.0), (268229.0, 2152130.0, 8.0)], dtype=[("X", "f8"), ("Y", "f8"), ("HeightAboveGround", "f8")])

    def test_unknown_crs_chm_reaches_science_without_fake_epsg(self):
        fake = types.ModuleType("pyforestscan")
        fake.calculate_chm = lambda *args, **kwargs: (np.array([[8.0]], dtype="f4"), (268228.0, 268229.0, 2152129.0, 2152130.0))
        handlers = types.ModuleType("pyforestscan.handlers")
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {"pyforestscan": fake, "pyforestscan.handlers": handlers}), patch("pyforestscan_qgis.core.adapter._read_source_local_lidar", return_value=(self.points,)), patch("pyforestscan_qgis.core.adapter._write_source_local_geotiff") as writer:
            output = Path(folder) / "chm.tif"
            writer.side_effect = lambda _a, path, _e, **_k: path.write_bytes(b"source-local")
            result = PyForestScanAdapter(execution_mode="qgis_python").create_chm(ChmRequest("ohia_01_5m_norm.las", output, 1.0, "", hag_method="existing_normalized_height"))
            self.assertEqual("", result.crs)
            self.assertTrue(output.exists())
            writer.assert_called_once()

    def test_unknown_crs_rumple_runs_and_polygon_use_is_blocked(self):
        fake = types.ModuleType("pyforestscan")
        fake.calculate_chm = lambda *args, **kwargs: (np.array([[1.0, 2.0], [2.0, 3.0]], dtype="f4"), (0.0, 2.0, 0.0, 2.0))
        fake.calculate_rumple = lambda *args, **kwargs: 1.0
        handlers = types.ModuleType("pyforestscan.handlers")
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {"pyforestscan": fake, "pyforestscan.handlers": handlers}), patch("pyforestscan_qgis.core.adapter._read_source_local_lidar", return_value=(self.points,)):
            output = Path(folder) / "rumple.csv"
            result = PyForestScanAdapter(execution_mode="qgis_python").create_rumple(RumpleRequest("ohia_01_5m_norm.las", output, 1.0, ""))
            self.assertEqual("", result.crs)
            with self.assertRaisesRegex(ProcessingError, "cannot align"):
                PyForestScanAdapter(execution_mode="qgis_python").create_chm(ChmRequest("plot.las", Path(folder) / "blocked.tif", 1.0, "", hag_method="existing_normalized_height", crop_polygon="POLYGON ((0 0, 1 0, 1 1, 0 0))"))

    def test_source_local_geotiff_has_no_crs_and_explicit_provenance(self):
        try:
            import rasterio
        except ImportError:
            self.skipTest("rasterio is unavailable")
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "source_local.tif"
            _write_source_local_geotiff(np.array([[1.0, 2.0]], dtype="f4"), output, (0.0, 1.0, 0.0, 2.0), product="CHM")
            with rasterio.open(output) as dataset:
                self.assertIsNone(dataset.crs)
                self.assertEqual("SOURCE_LOCAL", dataset.tags()["PYFORESTSCAN_SPATIAL_REFERENCE"])
                self.assertEqual("false", dataset.tags()["SOURCE_CRS_RESOLVED"])


if __name__ == "__main__":
    unittest.main()
