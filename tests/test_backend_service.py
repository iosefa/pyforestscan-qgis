"""Tests for backend service placeholders."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pyforestscan_qgis.core.backend.models import BackendPlatform, BackendStatus
from pyforestscan_qgis.core.backend.paths import resolve_backend_paths
from pyforestscan_qgis.core.backend.service import BackendService


class BackendServiceTests(unittest.TestCase):
    """Validate service operations are safe placeholders in Phase 22A."""

    def test_detect_backend_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = BackendService(paths=resolve_backend_paths(backend_root=Path(tmpdir) / "backend"))
            state = service.detect_backend()
        self.assertEqual(state.status, BackendStatus.NOT_INSTALLED)

    def test_planned_operations_do_not_modify_system(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "backend"
            service = BackendService(paths=resolve_backend_paths(backend_root=root, platform=BackendPlatform.MACOS, machine="x86_64"))
            results = (
                service.install_backend(),
                service.repair_backend(),
                service.update_backend(),
                service.remove_backend(),
                service.run_backend_python(),
                service.run_pdal_pipeline(),
            )
        for result in results:
            self.assertFalse(result.success)
            self.assertFalse(result.modified_system)
            self.assertIn("planned", result.message.lower())

    def test_backend_root_is_user_local_contract_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "PyForestScan" / "backend"
            service = BackendService(paths=resolve_backend_paths(backend_root=root))
            self.assertEqual(service.open_backend_folder_path(), root)

    def test_get_logs_missing_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = BackendService(paths=resolve_backend_paths(backend_root=Path(tmpdir) / "backend"))
            logs = service.get_logs()
        self.assertEqual(logs["install"], ())
        self.assertEqual(logs["verify"], ())


if __name__ == "__main__":
    unittest.main()
