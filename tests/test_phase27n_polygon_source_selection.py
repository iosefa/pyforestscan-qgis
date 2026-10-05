"""Phase 27N polygon source-selection and execution-plan tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pyforestscan_qgis.core.batch import BatchProductSettings
from pyforestscan_qgis.core.polygon_batch import PolygonBatchRequest, polygon_preflight_text, run_polygon_batch_preflight
from pyforestscan_qgis.core.polygon_normalization import normalized_selection_from_wkt
from pyforestscan_qgis.core.polygon_source_selection import PolygonSourceSelectionService, SpatialEnvelope
from pyforestscan_qgis.core.types import ProductType


def write_ept(path: Path, *, bounds=(0, 0, 0, 10, 10, 5), crs="EPSG:32610", points=110_008_858_527) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"bounds": list(bounds), "points": points, "srs": {"authority": crs}}), encoding="utf-8")


class Phase27NPolygonSourceSelectionTests(unittest.TestCase):
    def _request(self, root: Path, wkt: str, *, crs: str = "EPSG:32610") -> PolygonBatchRequest:
        polygon = normalized_selection_from_wkt(wkt, crs)
        settings = BatchProductSettings(products=(ProductType.CHM,), grid_resolution=1.0, max_workers=4)
        return PolygonBatchRequest(root, root / "out", polygon, (ProductType.CHM,), settings)

    def _preflight(self, root: Path, wkt: str, *, crs: str = "EPSG:32610"):
        return run_polygon_batch_preflight(self._request(root, wkt, crs=crs), backend_probe=lambda: (True, "PBM backend is ready."))

    def test_polygon_shape_does_not_change_ept_repository_identity(self) -> None:
        shapes = (
            "POLYGON ((1 1, 4 1, 4 4, 1 4, 1 1))",
            "POLYGON ((2 1, 5 2, 4 5, 1 4, 2 1))",
            "POLYGON ((1 1, 8 1, 8 3, 4 3, 4 8, 1 8, 1 1))",
            "MULTIPOLYGON (((20 20, 21 20, 21 21, 20 20)), ((1 1, 3 1, 3 3, 1 1)))",
            "POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0), (4 4, 6 4, 6 6, 4 6, 4 4))",
            "POLYGON ((-5 -5, 15 -5, 15 15, -5 15, -5 -5))",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "ept"
            write_ept(root / "ept.json")
            reports = [self._preflight(root, wkt) for wkt in shapes]

        for report in reports:
            self.assertEqual(report.repository.repository_kind, "ept")
            self.assertEqual(len(report.selected_sources), 1)
            self.assertEqual(report.selected_sources[0].source_type, "ept")
            self.assertIn("Repository: EPT dataset", polygon_preflight_text(report))

    def test_outside_polygon_keeps_ept_identity_and_reports_rejected_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "ept"
            write_ept(root / "ept.json")
            report = self._preflight(root, "POLYGON ((20 20, 30 20, 30 30, 20 30, 20 20))")
            text = polygon_preflight_text(report)

        self.assertFalse(report.ready)
        self.assertEqual(report.repository.repository_kind, "ept")
        self.assertEqual(len(report.selected_sources), 0)
        self.assertEqual(report.source_selection.rejected_sources[0].rejection_code, "OUTSIDE_POLYGON_ENVELOPE")
        self.assertIn("No LiDAR coverage was found for this area", text)
        self.assertIn("Selection method: native EPT extent overlap", text)
        self.assertNotIn("No cataloged LiDAR sources", text)

    def test_rejected_root_point_estimate_never_appears_in_guided_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "ept"
            write_ept(root / "ept.json", points=110_008_858_527)
            report = self._preflight(root, "POLYGON ((1 1, 4 1, 4 4, 1 4, 1 1))")
            text = polygon_preflight_text(report)

        self.assertIsNone(report.estimated_point_count)
        self.assertIn("Not available for this repository", text)
        self.assertNotIn("110,008,858,527", text)
        self.assertNotIn("110008858527", text)
        self.assertFalse(any("Large polygon batch: estimated point count" in warning for warning in report.warnings))

    @patch("pyforestscan_qgis.core.crs_alignment.default_transformer_factory", side_effect=ValueError("No CRS transformer available"))
    def test_unavailable_crs_transform_blocks_unsafe_extent_comparison(self, _transformer) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "ept"
            write_ept(root / "ept.json", crs="EPSG:32610")
            report = self._preflight(root, "POLYGON ((1 1, 4 1, 4 4, 1 4, 1 1))", crs="EPSG:6635")

        self.assertFalse(report.ready)
        self.assertEqual(report.repository.repository_kind, "ept")
        self.assertTrue(any("Coordinate systems" in item for item in report.blockers))
        self.assertEqual(report.source_selection.rejected_sources[0].rejection_code, "CRS_TRANSFORM_FAILED")

    def test_execution_plan_signature_changes_when_polygon_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "ept"
            write_ept(root / "ept.json")
            first = self._preflight(root, "POLYGON ((1 1, 4 1, 4 4, 1 4, 1 1))")
            second = self._preflight(root, "POLYGON ((2 2, 5 2, 5 5, 2 5, 2 2))")

        self.assertTrue(first.plan_signature)
        self.assertTrue(first.execution_plan)
        self.assertNotEqual(first.plan_signature, second.plan_signature)

    def test_spatial_envelope_refuses_different_crs_comparison(self) -> None:
        with self.assertRaises(ValueError):
            SpatialEnvelope(0, 1, 0, 1, "EPSG:32610").intersects(SpatialEnvelope(0, 1, 0, 1, "EPSG:6635"))


if __name__ == "__main__":
    unittest.main()
