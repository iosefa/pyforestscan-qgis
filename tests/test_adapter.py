"""Unit tests for the PyForestScan adapter boundary."""

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from pyforestscan_qgis.core.adapter import PyForestScanAdapter
from pyforestscan_qgis.core.config import AdapterConfig, InspectionOptions
from pyforestscan_qgis.core.exceptions import DatasetError
from pyforestscan_qgis.core.types import (
    DatasetFormat,
    LogLevel,
    ProductRequest,
    ProductType,
    ProgressState,
)


class TestPyForestScanAdapterValidation(unittest.TestCase):
    """Dataset validation tests that do not require QGIS."""

    def test_missing_dataset_returns_invalid_result(self) -> None:
        adapter = PyForestScanAdapter(execution_mode="qgis_python")

        result = adapter.validate_dataset("missing.las")

        self.assertFalse(result.is_valid)
        self.assertIsNone(result.source)
        self.assertIn("Dataset does not exist", result.messages[0])

    def test_unsupported_dataset_returns_invalid_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "points.txt"
            path.write_text("not lidar", encoding="utf-8")
            adapter = PyForestScanAdapter(execution_mode="qgis_python")

            result = adapter.validate_dataset(path)

        self.assertFalse(result.is_valid)
        self.assertIsNone(result.source)
        self.assertIn("Unsupported dataset format", result.messages[0])

    def test_open_dataset_accepts_local_laz(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tile.copc.laz"
            path.write_bytes(b"")
            adapter = PyForestScanAdapter(execution_mode="qgis_python")

            source = adapter.open_dataset(path)

        self.assertEqual(source.format, DatasetFormat.COPC)
        self.assertFalse(source.is_remote)

    def test_remote_non_ept_is_invalid(self) -> None:
        adapter = PyForestScanAdapter(execution_mode="qgis_python")

        result = adapter.validate_dataset("https://example.test/data.laz")

        self.assertFalse(result.is_valid)
        self.assertIn("Only EPT JSON remote datasets", " ".join(result.messages))


class TestPyForestScanAdapterInspection(unittest.TestCase):
    """Dataset inspection tests using local metadata or fake PDAL."""

    def test_local_ept_json_inspection_returns_typed_metadata(self) -> None:
        metadata = {
            "bounds": [0, 0, 2, 20, 20, 30],
            "points": 400,
            "srs": {"authority": "EPSG", "horizontal": "32610"},
            "schema": [
                {"name": "X"},
                {"name": "Y"},
                {"name": "Z"},
                {"name": "Classification"},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            ept = Path(directory) / "ept.json"
            ept.write_text(json.dumps(metadata), encoding="utf-8")
            adapter = PyForestScanAdapter(execution_mode="qgis_python")

            inspection = adapter.inspect_dataset(ept)

        self.assertEqual(inspection.source.format, DatasetFormat.EPT)
        self.assertEqual(inspection.point_count, 400)
        self.assertEqual(inspection.crs, "EPSG:32610")
        self.assertEqual(inspection.bounds.min_x, 0.0)
        self.assertEqual(inspection.bounds.max_y, 20.0)
        self.assertEqual(inspection.dimensions, ("X", "Y", "Z", "Classification"))
        self.assertEqual(inspection.classification_summary, ())
        self.assertAlmostEqual(inspection.estimated_density, 1.0)
        self.assertIn(ProductType.CHM, inspection.supported_products)
        self.assertEqual(adapter.get_progress().state, ProgressState.COMPLETE)


    def test_ept_inspection_can_omit_dimensions(self) -> None:
        metadata = {
            "bounds": [0, 0, 0, 10, 10, 10],
            "points": 100,
            "schema": [{"name": "X"}, {"name": "Y"}, {"name": "Z"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            ept = Path(directory) / "ept.json"
            ept.write_text(json.dumps(metadata), encoding="utf-8")
            adapter = PyForestScanAdapter(execution_mode="qgis_python")

            inspection = adapter.inspect_dataset(
                ept,
                InspectionOptions(include_dimensions=False),
            )

        self.assertEqual(inspection.dimensions, ())

    def test_pdal_inspection_uses_fake_pipeline(self) -> None:
        try:
            import numpy
        except ImportError:
            self.skipTest("numpy is required for fake PDAL array inspection")

        with tempfile.TemporaryDirectory() as directory:
            las = Path(directory) / "plot.las"
            las.write_bytes(b"")
            array = numpy.array(
                [
                    (0.0, 0.0, 1.0, 2),
                    (2.0, 0.0, 4.0, 2),
                    (2.0, 2.0, 6.0, 5),
                    (0.0, 2.0, 8.0, 5),
                ],
                dtype=[("X", "f8"), ("Y", "f8"), ("Z", "f8"), ("Classification", "u1")],
            )

            class FakePipeline:
                def __init__(self, pipeline_json: str) -> None:
                    self.pipeline_json = pipeline_json
                    self.arrays = []
                    self.metadata = json.dumps(
                        {
                            "metadata": {
                                "readers.las": {
                                    "srs": {"wkt": "EPSG:32610"},
                                    "dataformat_id": 7,
                                }
                            }
                        }
                    )

                def execute(self) -> int:
                    self.arrays = [array]
                    return len(array)

            fake_pdal = types.SimpleNamespace(Pipeline=FakePipeline)
            adapter = PyForestScanAdapter(execution_mode="qgis_python")

            with patch.dict(sys.modules, {"pdal": fake_pdal}):
                inspection = adapter.inspect_dataset(las)

        self.assertEqual(inspection.point_count, 4)
        self.assertEqual(inspection.bounds.min_x, 0.0)
        self.assertEqual(inspection.bounds.max_z, 8.0)
        self.assertEqual([(item.classification, item.count) for item in inspection.classification_summary], [(2, 2), (5, 2)])
        self.assertEqual(inspection.point_format, "7")
        self.assertEqual(inspection.crs, "EPSG:32610")
        self.assertAlmostEqual(inspection.estimated_density, 1.0)

    def test_inspect_without_open_dataset_raises_dataset_error(self) -> None:
        adapter = PyForestScanAdapter(execution_mode="qgis_python")

        with self.assertRaises(DatasetError):
            adapter.inspect_dataset()


class TestPyForestScanAdapterControlSurface(unittest.TestCase):
    """Control and placeholder behavior for future processing methods."""

    def test_compute_products_is_explicitly_not_implemented(self) -> None:
        adapter = PyForestScanAdapter(execution_mode="qgis_python")
        request = ProductRequest(products=(ProductType.CHM,))

        with self.assertRaises(NotImplementedError):
            adapter.compute_products(request)

    def test_logging_and_cancellation_are_structured(self) -> None:
        records = []
        adapter = PyForestScanAdapter(
            config=AdapterConfig(),
            log_sink=records.append,
        )

        adapter.cancel()

        self.assertEqual(adapter.get_progress().state, ProgressState.CANCELED)
        self.assertEqual(records[-1].level, LogLevel.WARNING)
        self.assertEqual(records[-1].message, "Adapter cancellation requested")


if __name__ == "__main__":
    unittest.main()
