"""Tests for transactional backend installer orchestration."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pyforestscan_qgis.core.backend.download_manager import CancellationToken
from pyforestscan_qgis.core.backend.models import BackendOperationResult, BackendPlatform, BackendStatus
from pyforestscan_qgis.core.backend.paths import resolve_backend_paths
from pyforestscan_qgis.core.backend.progress import BackendProgressStage, STAGED_PROGRESS_ORDER, backend_progress_percentage
from pyforestscan_qgis.core.backend.transaction import BackendInstallTransaction, BackendTransactionStage


class FakeDownload:
    success = True
    message = "downloaded"
    path = Path("artifact")


class FakeChecksum:
    message = "checksum ok"

    def passed(self) -> bool:
        return True


class FakeVerification:
    summary = "verified"

    def passed(self) -> bool:
        return True


class FakeInstaller:
    def __init__(self, fail_stage: str | None = None) -> None:
        self.fail_stage = fail_stage
        self.rollback_called = False
        self.calls: list[str] = []

    def require_enabled(self, operation: str):
        self.calls.append("require_enabled")
        return None

    def prepare_staging(self):
        self.calls.append("prepare_staging")
        return self._result("prepare_staging")

    def download_micromamba(self, policy=None):
        self.calls.append("download_micromamba")
        if self.fail_stage == "download":
            return type("Download", (), {"success": False, "message": "download failed", "path": Path("artifact")})()
        return FakeDownload()

    def verify_micromamba_download(self, policy=None):
        self.calls.append("verify_micromamba_download")
        if self.fail_stage == "verify":
            return type("Checksum", (), {"message": "bad checksum", "passed": lambda self: False})()
        return FakeChecksum()

    def extract_micromamba(self, policy=None):
        self.calls.append("extract_micromamba")
        return self._result("extract_micromamba")

    def create_environment(self, spec_file=None):
        self.calls.append("create_environment")
        return self._result("create_environment")

    def install_python_packages(self):
        self.calls.append("install_python_packages")
        return self._result("install_python_packages")

    def verify_environment(self):
        self.calls.append("verify_environment")
        if self.fail_stage == "verify_environment":
            return type("Verification", (), {"summary": "missing packages", "passed": lambda self: False})()
        return FakeVerification()

    def verify_active_backend(self):
        self.calls.append("verify_active_backend")
        if self.fail_stage == "verify_active_backend":
            return type("Verification", (), {"summary": "active backend incomplete", "passed": lambda self: False})()
        return FakeVerification()

    def promote_staging(self):
        self.calls.append("promote_staging")
        return self._result("promote_staging")

    def write_backend_config(self, status=BackendStatus.READY):
        self.calls.append("write_backend_config")
        return self._result("write_backend_config", status=status)

    def rollback_failed_install(self):
        self.calls.append("rollback_failed_install")
        self.rollback_called = True
        return BackendOperationResult("rollback_failed_install", BackendStatus.REPAIR_REQUIRED, True, "rolled back", True)

    def cleanup_successful_install(self):
        self.calls.append("cleanup_successful_install")
        return BackendOperationResult("cleanup_successful_install", BackendStatus.READY, True, "cleaned", True)

    def _result(self, operation: str, status: BackendStatus = BackendStatus.INSTALLING):
        if self.fail_stage == operation:
            return BackendOperationResult(operation, BackendStatus.FAILED, False, f"{operation} failed", True)
        return BackendOperationResult(operation, status, True, f"{operation} ok", True)


class BackendTransactionTests(unittest.TestCase):
    """Validate rollback, success, and cancellation transaction behavior."""


    def test_staged_progress_percent_mapping_matches_user_facing_plan(self) -> None:
        expected = (
            (BackendProgressStage.PREPARING, 5),
            (BackendProgressStage.DOWNLOADING, 15),
            (BackendProgressStage.VERIFYING_DOWNLOAD, 25),
            (BackendProgressStage.EXTRACTING, 35),
            (BackendProgressStage.CREATING_ENVIRONMENT, 50),
            (BackendProgressStage.INSTALLING_PACKAGES, 70),
            (BackendProgressStage.VERIFYING_BACKEND, 85),
            (BackendProgressStage.FINALIZING, 95),
            (BackendProgressStage.READY, 100),
        )

        self.assertEqual(tuple(stage for stage, _percent in expected), STAGED_PROGRESS_ORDER)
        self.assertEqual(tuple(percent for _stage, percent in expected), tuple(backend_progress_percentage(stage) for stage in STAGED_PROGRESS_ORDER))

    def test_transaction_emits_ordered_estimated_progress(self) -> None:
        updates = []
        installer = FakeInstaller()
        result = BackendInstallTransaction(installer, progress_callback=updates.append).run()
        emitted = [update.stage for update in updates if update.stage in STAGED_PROGRESS_ORDER]

        self.assertTrue(result.success)
        self.assertEqual(BackendProgressStage.PREPARING, emitted[0])
        self.assertEqual(BackendProgressStage.READY, emitted[-1])
        self.assertLess(emitted.index(BackendProgressStage.DOWNLOADING), emitted.index(BackendProgressStage.VERIFYING_DOWNLOAD))
        self.assertLess(emitted.index(BackendProgressStage.CREATING_ENVIRONMENT), emitted.index(BackendProgressStage.INSTALLING_PACKAGES))
        self.assertTrue(all(update.estimated_remaining_step == "Step progress is estimated." for update in updates))

    def test_transaction_success_reaches_ready(self) -> None:
        installer = FakeInstaller()
        result = BackendInstallTransaction(installer).run()

        self.assertTrue(result.success)
        self.assertEqual(result.status, BackendStatus.READY)
        self.assertEqual(result.stage, BackendTransactionStage.READY)
        self.assertFalse(installer.rollback_called)
        self.assertEqual(result.operations[-1].operation, "cleanup_successful_install")

    def test_transaction_rolls_back_on_stage_failure(self) -> None:
        installer = FakeInstaller(fail_stage="extract_micromamba")
        result = BackendInstallTransaction(installer).run()

        self.assertFalse(result.success)
        self.assertEqual(result.stage, BackendTransactionStage.EXTRACT)
        self.assertTrue(result.rolled_back)
        self.assertTrue(installer.rollback_called)

    def test_transaction_rolls_back_on_verification_failure(self) -> None:
        installer = FakeInstaller(fail_stage="verify_environment")
        result = BackendInstallTransaction(installer).run()

        self.assertFalse(result.success)
        self.assertEqual(result.status, BackendStatus.REPAIR_REQUIRED)
        self.assertEqual(result.stage, BackendTransactionStage.VERIFY_PACKAGES)
        self.assertTrue(installer.rollback_called)

    def test_transaction_cancelled_rolls_back(self) -> None:
        installer = FakeInstaller()
        token = CancellationToken(cancelled=True)
        result = BackendInstallTransaction(installer).run(cancel_token=token)

        self.assertFalse(result.success)
        self.assertTrue(result.rolled_back)
        self.assertEqual(result.stage, BackendTransactionStage.DOWNLOAD)


    def test_transaction_order_verifies_staging_before_promoting_and_config(self) -> None:
        installer = FakeInstaller()
        result = BackendInstallTransaction(installer).run()

        self.assertTrue(result.success)
        self.assertLess(installer.calls.index("verify_environment"), installer.calls.index("promote_staging"))
        self.assertLess(installer.calls.index("promote_staging"), installer.calls.index("write_backend_config"))
        self.assertLess(installer.calls.index("write_backend_config"), installer.calls.index("verify_active_backend"))
        self.assertEqual(installer.calls[-1], "cleanup_successful_install")

    def test_transaction_rolls_back_on_failed_promotion(self) -> None:
        installer = FakeInstaller(fail_stage="promote_staging")
        result = BackendInstallTransaction(installer).run()

        self.assertFalse(result.success)
        self.assertEqual(result.stage, BackendTransactionStage.PROMOTE_BACKEND)
        self.assertTrue(installer.rollback_called)

    def test_guarded_installer_still_does_not_modify_without_flag(self) -> None:
        from pyforestscan_qgis.core.backend.installer import BackendInstaller

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.MACOS, machine="x86_64")
            result = BackendInstaller(paths, environ={}).install_backend()

        self.assertFalse(result.success)
        self.assertFalse(result.modified_system)
        self.assertIn("planned/experimental", result.message)


if __name__ == "__main__":
    unittest.main()
