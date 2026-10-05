"""Regression tests for staged PBM verification and promotion."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from pyforestscan_qgis.core.backend import installer as installer_module
from pyforestscan_qgis.core.backend.installer import BACKEND_INSTALL_ENABLE_ENV, BackendInstaller, staged_backend_paths, staging_paths
from pyforestscan_qgis.core.backend.models import BackendPlatform, BackendRegistry, BackendStatus
from pyforestscan_qgis.core.backend.paths import resolve_backend_paths
from pyforestscan_qgis.core.backend.repair import plan_backend_repair
from pyforestscan_qgis.core.backend.verification import verify_backend


class BackendStagedPromotionTests(unittest.TestCase):
    """Validate staged verification before final promotion."""

    def test_staged_backend_paths_use_exact_staging_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.WINDOWS)
            staged = staging_paths(paths)
            staged_paths = staged_backend_paths(paths)

        self.assertEqual(staged_paths.backend_root, staged.root)
        self.assertEqual(staged_paths.micromamba_executable, staged.micromamba_executable)
        self.assertEqual(staged_paths.environment_path, staged.environment_path)
        self.assertEqual(staged_paths.python_executable, staged.python_executable)
        self.assertEqual(staged_paths.config_file, staged.root / "backend.json")

    def test_staged_verification_does_not_require_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.LINUX)
            staged = staging_paths(paths)
            staged.micromamba_executable.parent.mkdir(parents=True, exist_ok=True)
            staged.micromamba_executable.write_text("micromamba", encoding="utf-8")
            staged.python_executable.parent.mkdir(parents=True, exist_ok=True)
            staged.python_executable.write_text("python", encoding="utf-8")
            installer = BackendInstaller(paths=paths, environ={BACKEND_INSTALL_ENABLE_ENV: "1"}, registry=BackendRegistry(()))
            result = installer.verify_environment()

        self.assertEqual(result.status, BackendStatus.READY)
        self.assertFalse(staged.root.joinpath("backend.json").exists())

    def test_final_verification_requires_config_after_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.LINUX)
            paths.micromamba_executable.parent.mkdir(parents=True, exist_ok=True)
            paths.micromamba_executable.write_text("micromamba", encoding="utf-8")
            paths.python_executable.parent.mkdir(parents=True, exist_ok=True)
            paths.python_executable.write_text("python", encoding="utf-8")

            missing_config = verify_backend(paths, BackendRegistry(()), require_config=True)
            staged_without_config = verify_backend(staged_backend_paths(paths), BackendRegistry(()), require_config=False)

        self.assertEqual(missing_config.status, BackendStatus.REPAIR_REQUIRED)
        self.assertEqual(staged_without_config.status, BackendStatus.NOT_INSTALLED)

    def test_promotion_failure_restores_existing_backend(self) -> None:
        original_move = installer_module.shutil.move
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.WINDOWS)
            paths.micromamba_executable.parent.mkdir(parents=True, exist_ok=True)
            (paths.micromamba_executable.parent / "old.txt").write_text("old micromamba", encoding="utf-8")
            paths.environment_path.mkdir(parents=True, exist_ok=True)
            (paths.environment_path / "old.txt").write_text("old env", encoding="utf-8")
            paths.config_file.parent.mkdir(parents=True, exist_ok=True)
            paths.config_file.write_text("old config", encoding="utf-8")

            staged = staging_paths(paths)
            staged.micromamba_executable.parent.mkdir(parents=True, exist_ok=True)
            staged.micromamba_executable.write_text("new micromamba", encoding="utf-8")
            staged.environment_path.mkdir(parents=True, exist_ok=True)
            (staged.environment_path / "new.txt").write_text("new env", encoding="utf-8")

            def failing_move(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
                if Path(src) == staged.environment_path and Path(dst) == paths.environment_path:
                    raise OSError("simulated env promotion failure")
                return original_move(src, dst, *args, **kwargs)

            installer_module.shutil.move = failing_move
            try:
                result = BackendInstaller(paths=paths, environ={BACKEND_INSTALL_ENABLE_ENV: "1"}).promote_staging()
            finally:
                installer_module.shutil.move = original_move

            self.assertFalse(result.success)
            # The transaction invokes rollback after the promotion failure.
            BackendInstaller(paths).rollback_failed_install()
            self.assertEqual((paths.micromamba_executable.parent / "old.txt").read_text(encoding="utf-8"), "old micromamba")
            self.assertEqual((paths.environment_path / "old.txt").read_text(encoding="utf-8"), "old env")
            self.assertEqual(paths.config_file.read_text(encoding="utf-8"), "old config")

    def test_successful_cleanup_keeps_promoted_backend_not_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.LINUX)
            paths.micromamba_executable.parent.mkdir(parents=True, exist_ok=True)
            (paths.micromamba_executable.parent / "old.txt").write_text("old", encoding="utf-8")
            staged = staging_paths(paths)
            staged.micromamba_executable.parent.mkdir(parents=True, exist_ok=True)
            staged.micromamba_executable.write_text("new micromamba", encoding="utf-8")
            staged.environment_path.mkdir(parents=True, exist_ok=True)
            (staged.environment_path / "new.txt").write_text("new env", encoding="utf-8")

            calls = []
            def runner(command, **kwargs):
                calls.append(command)
                if "--clone" in command:
                    shutil.copytree(staged.environment_path, paths.environment_path)
                return subprocess.CompletedProcess(command, 0, "ok", "")
            installer = BackendInstaller(paths=paths, environ={BACKEND_INSTALL_ENABLE_ENV: "1"}, runner=runner)
            promote = installer.promote_staging()
            cleanup = installer.cleanup_successful_install()

            self.assertTrue(promote.success)
            self.assertTrue(cleanup.success)
            self.assertTrue(paths.micromamba_executable.exists())
            self.assertTrue((paths.environment_path / "new.txt").exists())
            self.assertFalse(paths.staging_dir.exists())
            self.assertIn("--clone", calls[0])
            self.assertEqual(calls[0][calls[0].index("-p") + 1], str(paths.environment_path))
            self.assertEqual(calls[1][0], str(paths.python_executable))
            self.assertIn("pyforestscan==0.4.1", calls[1])
            self.assertEqual(calls[2][-3:], ["-m", "pip", "check"])

    def test_failed_linux_clone_restores_existing_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(Path(tmpdir) / "backend", BackendPlatform.LINUX)
            paths.environment_path.mkdir(parents=True)
            (paths.environment_path / "previous").write_text("working")
            staged = staging_paths(paths)
            staged.micromamba_executable.parent.mkdir(parents=True)
            staged.micromamba_executable.touch()
            staged.environment_path.mkdir()
            def runner(command, **kwargs):
                paths.environment_path.mkdir()
                (paths.environment_path / "partial").touch()
                return subprocess.CompletedProcess(command, 1, "", "clone failed")
            installer = BackendInstaller(paths, environ={BACKEND_INSTALL_ENABLE_ENV: "1"}, runner=runner)
            result = installer.promote_staging()
            self.assertFalse(result.success)
            installer.rollback_failed_install()
            self.assertEqual((paths.environment_path / "previous").read_text(), "working")
            self.assertFalse((paths.environment_path / "partial").exists())

    def test_repair_plan_reports_staging_remnants(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.LINUX)
            paths.staging_dir.mkdir(parents=True)
            plan = plan_backend_repair(paths)

        self.assertTrue(any(issue.code == "staging_remnants" for issue in plan.issues))
        self.assertTrue(any(action.code == "cleanup_staging" for action in plan.actions))


if __name__ == "__main__":
    unittest.main()
