import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from pyforestscan_qgis.core.backend.models import BackendPlatform, BackendOperationResult, BackendStatus
from pyforestscan_qgis.core.backend.paths import resolve_backend_paths
from pyforestscan_qgis.core.backend.processing_engine import (
    ProcessingEngineError,
    ProcessingEngineState,
    ProcessingEngineSetupLock,
    ProcessingEngineVerifier,
    ProcessingEngineService,
    REQUIRED_PYFORESTSCAN_MODULES,
    current_plugin_build_id,
    current_runner_hash,
)


class ProcessingEngineTests(unittest.TestCase):
    def test_setup_preserves_the_actual_installer_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = resolve_backend_paths(Path(folder), BackendPlatform.LINUX)
            service = ProcessingEngineService(paths, setup_callback=Mock(return_value=
                BackendOperationResult("install", BackendStatus.FAILED, False, "Dependency solve failed: conflicting numpy requirements", False)))
            with self.assertRaisesRegex(ProcessingEngineError, "conflicting numpy requirements") as raised:
                service.ensure_processing_engine_ready()
            self.assertEqual(raised.exception.code, "ENGINE_SETUP_FAILED")

    def _paths(self, root):
        paths = resolve_backend_paths(Path(root), BackendPlatform.WINDOWS)
        paths.python_executable.parent.mkdir(parents=True)
        paths.python_executable.touch()
        return paths

    @staticmethod
    def _runner(payload, returncode=0):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, returncode, json.dumps(payload), "")
        return run

    def test_ready_requires_handlers_and_actual_backend_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = self._paths(folder)
            payload = {
                "python_executable": str(paths.python_executable),
                "protocol_compatible": True,
                "failed_required_components": [],
                "plugin_build_id": current_plugin_build_id(),
                "runner_sha256": current_runner_hash(),
                "protocol_version": "2",
            }
            report = ProcessingEngineVerifier(paths, self._runner(payload), Path(folder)).verify()
            self.assertEqual(report.state, ProcessingEngineState.READY)
            self.assertIn("pyforestscan.handlers", REQUIRED_PYFORESTSCAN_MODULES)

    def test_ready_cannot_coexist_with_missing_handlers(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = self._paths(folder)
            payload = {
                "python_executable": str(paths.python_executable),
                "protocol_compatible": True,
                "failed_required_components": ["pyforestscan.handlers"],
            }
            verifier = ProcessingEngineVerifier(paths, self._runner(payload), Path(folder))
            report = verifier.verify()
            self.assertEqual(report.state, ProcessingEngineState.REPAIR_REQUIRED)
            with self.assertRaises(ProcessingEngineError) as raised:
                verifier.require_ready()
            self.assertEqual(raised.exception.code, "ENGINE_REPAIR_REQUIRED")

    def test_runtime_identity_mismatch_is_repair_required(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = self._paths(folder)
            payload = {
                "python_executable": str(Path(folder) / "qgis-python.exe"),
                "protocol_compatible": True,
                "failed_required_components": [],
            }
            report = ProcessingEngineVerifier(paths, self._runner(payload), Path(folder)).verify()
            self.assertEqual(report.state, ProcessingEngineState.REPAIR_REQUIRED)
            self.assertIn("runtime_identity", report.failed_components)

    def test_quick_check_uses_current_manifest_without_subprocess(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = self._paths(folder)
            payload = {
                "python_executable": str(paths.python_executable),
                "protocol_compatible": True,
                "failed_required_components": [],
                "plugin_build_id": current_plugin_build_id(),
                "runner_sha256": current_runner_hash(),
                "protocol_version": "2",
            }
            verifier = ProcessingEngineVerifier(paths, self._runner(payload), Path(folder))
            verifier.verify(setup_completed=True)
            quick = ProcessingEngineVerifier(paths, lambda *a, **k: self.fail("subprocess used"), Path(folder)).quick()
            self.assertTrue(quick.ready)
            self.assertTrue(quick.from_cache)

    def test_setup_lock_rejects_second_session(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = resolve_backend_paths(Path(folder), BackendPlatform.WINDOWS)
            first = ProcessingEngineSetupLock(paths)
            second = ProcessingEngineSetupLock(paths)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_polygon_default_adapter_is_forced_to_managed_engine(self):
        source = (Path(__file__).parents[1] / "pyforestscan_qgis" / "core" / "polygon_batch.py").read_text(encoding="utf-8")
        self.assertIn('PyForestScanAdapter(execution_mode="pbm_backend")', source)
        self.assertNotIn("adapter = adapter or PyForestScanAdapter()", source)

    def test_folder_batch_requires_same_managed_engine(self):
        source = (Path(__file__).parents[1] / "pyforestscan_qgis" / "ui" / "pages.py").read_text(encoding="utf-8")
        worker = source[source.index("class _BatchExecutionWorker"):source.index("class _CatalogBuildWorker")]
        self.assertIn("validate_runtime_token_for_launch", worker)
        self.assertIn("self.request.runtime_token", worker)
        self.assertNotIn("ProcessingEngineVerifier", worker)
        self.assertIn('PyForestScanAdapter(execution_mode="pbm_backend")', worker)


if __name__ == "__main__":
    unittest.main()
