
"""Phase 27R polygon LiDAR folder stabilization tests."""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pyforestscan_qgis.core.batch import BatchItemResult, BatchProductSettings, BatchResult
from pyforestscan_qgis.core.batch_options import BatchExecutionOptions, PolygonBatchOptions
from pyforestscan_qgis.core.lidar_source_metadata import HeaderMetadataService, LidarSourceMetadata
from pyforestscan_qgis.core.polygon_batch import PolygonBatchRequest, execute_polygon_batch, run_polygon_batch_preflight, write_polygon_batch_manifest
from pyforestscan_qgis.core.polygon_lidar_processing import PolygonLidarProcessingRequest, PolygonLidarProcessingService
from pyforestscan_qgis.core.polygon_normalization import normalized_selection_from_wkt
from pyforestscan_qgis.core.raster_mask import RasterMaskResult
from pyforestscan_qgis.core.spatial_selection import Bounds2D
from pyforestscan_qgis.core.types import HagNormalizationResult, ProductType
from pyforestscan_qgis.ui.qgis_spatial_actions import add_selected_lidar_to_qgis


def write_las_header(path: Path, *, bounds=(0.0, 10.0, 0.0, 10.0, 0.0, 1.0), points: int = 100) -> None:
    header = bytearray(375)
    header[:4] = b"LASF"
    struct.pack_into("<I", header, 107, points)
    struct.pack_into("<ddd", header, 131, 0.01, 0.01, 0.01)
    struct.pack_into("<ddd", header, 155, 0.0, 0.0, 0.0)
    xmin, xmax, ymin, ymax, zmin, zmax = bounds
    struct.pack_into("<dddddd", header, 179, xmax, xmin, ymax, ymin, zmax, zmin)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header)


class PolygonStabilizationTests(unittest.TestCase):
    def test_header_discovery_order_is_independent_of_filesystem_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = [root / name for name in ("b.las", "a.las")]
            for path in paths:
                write_las_header(path)
            with patch("pyforestscan_qgis.core.lidar_source_metadata.iter_lidar_paths", return_value=iter(paths)):
                forward = HeaderMetadataService().discover(root)
            with patch("pyforestscan_qgis.core.lidar_source_metadata.iter_lidar_paths", return_value=iter(reversed(paths))):
                reverse = HeaderMetadataService().discover(root)
        self.assertEqual(forward, reverse)
        self.assertEqual(tuple(item.path.name for item in forward), ("a.las", "b.las"))

    def test_shared_header_metadata_model_records_scaled_bounds_and_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tile = root / "nested" / "tile.las"
            write_las_header(tile, bounds=(100, 120, 200, 240, 0, 1), points=123)
            records = HeaderMetadataService().discover(root, repository_crs_override="EPSG:6635")

        self.assertEqual(len(records), 1)
        metadata = records[0]
        self.assertIsInstance(metadata, LidarSourceMetadata)
        self.assertEqual(metadata.bounds, Bounds2D(100, 200, 120, 240))
        self.assertEqual(metadata.effective_crs, "EPSG:6635")
        self.assertEqual(metadata.point_count, 123)
        self.assertTrue(metadata.metadata_signature)

    def test_irregular_and_multipolygon_envelope_selection_returns_real_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            hit_a = root / "a.las"
            hit_b = root / "b.las"
            miss = root / "miss.las"
            write_las_header(hit_a, bounds=(0, 10, 0, 10, 0, 1))
            write_las_header(hit_b, bounds=(30, 40, 30, 40, 0, 1))
            write_las_header(miss, bounds=(80, 90, 80, 90, 0, 1))
            polygon = normalized_selection_from_wkt("MULTIPOLYGON (((1 1, 8 1, 2 8, 1 1)), ((31 31, 38 31, 38 38, 31 38, 31 31)))", "EPSG:6635")
            request = PolygonLidarProcessingRequest(root, polygon, "EPSG:6635", "EPSG:6635", (ProductType.CHM,), root / "out")
            plan = PolygonLidarProcessingService().create_plan(request, backend_ready=True, backend_message="PBM ready")
            self.assertTrue(all(path.exists() for path in plan.selected_source_paths))

        self.assertEqual(plan.readiness, "ready")
        self.assertEqual(tuple(path.name for path in plan.selected_source_paths), ("a.las", "b.las"))
        self.assertEqual(plan.execution_plan.spatial_read_plan["selected_source_paths"], [str(path) for path in plan.selected_source_paths])

    def test_preflight_manifest_preserves_selected_paths_and_plan_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tile = root / "tile.las"
            write_las_header(tile, bounds=(0, 10, 0, 10, 0, 1))
            polygon = normalized_selection_from_wkt("POLYGON ((2 2, 4 2, 4 4, 2 4, 2 2))", "EPSG:6635")
            settings = BatchProductSettings(products=(ProductType.CHM,), grid_resolution=1.0)
            report = run_polygon_batch_preflight(
                PolygonBatchRequest(root, root / "out", polygon, (ProductType.CHM,), settings, repository_crs_override="EPSG:6635"),
                backend_probe=lambda: (True, "PBM ready"),
            )
            manifest = write_polygon_batch_manifest(report)
            payload = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertTrue(report.ready)
        self.assertEqual(payload["selected_source_paths"], [str(tile)])
        self.assertEqual(payload["selected_path_invariant"]["readable_path_count"], 1)
        self.assertIn(str(tile), report.execution_plan.spatial_read_plan["selected_source_paths"])

    def test_execution_uses_selected_paths_for_clipping_then_registers_masked_outputs(self) -> None:
        class FakeAdapter:
            def __init__(self) -> None:
                self.normalized_inputs = []

            def normalize_heights(self, request):
                self.normalized_inputs.append(Path(request.input_path))
                Path(request.output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(request.output_path).write_text("clipped", encoding="utf-8")
                self.last_polygon = request.crop_polygon
                return HagNormalizationResult(Path(request.output_path), 10, request.crs, True)

        class FakeExecutor:
            def run(self, request, **_kwargs):
                self.request = request
                output = request.batch_folder / "outputs" / "chm.tif"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("raster", encoding="utf-8")
                item = BatchItemResult(request.datasets[0], request.batch_folder, "completed", "done", (output,), "done")
                return BatchResult("batch", request.title, "start", "end", request.batch_folder, (item,), request.batch_folder / "batch_summary.json", request.batch_folder / "batch_summary.csv", request.batch_folder / "batch_summary.html")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tile = root / "tile.las"
            write_las_header(tile, bounds=(0, 10, 0, 10, 0, 1))
            polygon = normalized_selection_from_wkt("POLYGON ((2 2, 4 2, 4 4, 2 4, 2 2))", "EPSG:6635")
            settings = BatchProductSettings(products=(ProductType.CHM,), grid_resolution=1.0)
            report = run_polygon_batch_preflight(
                PolygonBatchRequest(root, root / "out", polygon, (ProductType.CHM,), settings, repository_crs_override="EPSG:6635", polygon_options=PolygonBatchOptions(exact_raster_mask=False)),
                backend_probe=lambda: (True, "PBM ready"),
            )
            adapter = FakeAdapter()
            executor = FakeExecutor()
            result = execute_polygon_batch(report, adapter=adapter, executor=executor)
            self.assertTrue(result.output_registry_path.exists())

        self.assertEqual(adapter.normalized_inputs, [tile])
        self.assertIn("POLYGON", adapter.last_polygon)
        self.assertNotIn(tile, executor.request.datasets)
        self.assertTrue(str(executor.request.datasets[0]).endswith("_polygon_clip.laz"))
        self.assertIsNotNone(result.output_registry_path)

    def test_execution_blocks_metadata_only_selected_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tile = root / "tile.las"
            write_las_header(tile, bounds=(0, 10, 0, 10, 0, 1))
            polygon = normalized_selection_from_wkt("POLYGON ((2 2, 4 2, 4 4, 2 4, 2 2))", "EPSG:6635")
            settings = BatchProductSettings(products=(ProductType.CHM,), grid_resolution=1.0)
            report = run_polygon_batch_preflight(
                PolygonBatchRequest(root, root / "out", polygon, (ProductType.CHM,), settings, repository_crs_override="EPSG:6635"),
                backend_probe=lambda: (True, "PBM ready"),
            )
            tile.unlink()
            with self.assertRaisesRegex(ValueError, "not readable"):
                execute_polygon_batch(report, adapter=object(), executor=object())

    def test_selected_lidar_qgis_action_requires_live_iface_and_declares_group(self) -> None:
        self.assertFalse(add_selected_lidar_to_qgis(object(), None).success)
        source = (Path(__file__).resolve().parents[1] / "pyforestscan_qgis/ui/qgis_spatial_actions.py").read_text(encoding="utf-8")
        self.assertIn("PyForestScan - Selected LiDAR", source)
        self.assertIn("Selected LiDAR Files", source)
        self.assertIn("effective_crs", source)


if __name__ == "__main__":
    unittest.main()
