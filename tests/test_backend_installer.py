"""Tests for the Phase 22C controlled backend installer prototype."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pyforestscan_qgis.core.backend.checksums import ChecksumPolicy, sha256_file, verify_checksum
from pyforestscan_qgis.core.backend.downloads import download_file, download_path
from pyforestscan_qgis.core.backend.installer import (
    BACKEND_INSTALL_ENABLE_ENV,
    BackendInstaller,
    backend_install_availability,
    backend_install_enabled,
    default_environment_spec_file,
    staging_paths,
)
from pyforestscan_qgis.core.backend.micromamba import micromamba_archive_name, micromamba_bootstrap_policy, micromamba_source_url
from pyforestscan_qgis.core.backend.models import BackendPlatform, BackendStatus
from pyforestscan_qgis.core.backend.paths import resolve_backend_paths
from pyforestscan_qgis.core.backend.config import load_backend_config


class BackendInstallerTests(unittest.TestCase):
    """Validate installer guardrails and staged filesystem behavior."""

    def test_micromamba_url_selection_by_platform(self) -> None:
        self.assertEqual(micromamba_source_url(BackendPlatform.LINUX, machine="x86_64"), "https://micro.mamba.pm/api/micromamba/linux-64/latest")
        self.assertEqual(micromamba_source_url(BackendPlatform.WINDOWS, machine="x86_64"), "https://micro.mamba.pm/api/micromamba/win-64/latest")
        self.assertEqual(micromamba_source_url(BackendPlatform.MACOS, machine="x86_64"), "https://micro.mamba.pm/api/micromamba/osx-64/latest")
        self.assertEqual(micromamba_source_url(BackendPlatform.UNKNOWN), "")
        self.assertEqual(micromamba_archive_name(BackendPlatform.LINUX, machine="x86_64"), "micromamba-linux-64.tar.bz2")

    def test_checksum_policy_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / "artifact.bin"
            artifact.write_bytes(b"micromamba")
            digest = sha256_file(artifact)

            self.assertTrue(verify_checksum(artifact, ChecksumPolicy(expected=digest)).passed())
            self.assertFalse(verify_checksum(artifact, ChecksumPolicy(expected="0" * 64)).passed())
            missing_policy = verify_checksum(artifact, ChecksumPolicy(expected=None, required=True))
            self.assertEqual(missing_policy.status, "fail")
            optional_policy = verify_checksum(artifact, ChecksumPolicy(expected=None, required=False))
            self.assertTrue(optional_policy.passed())
            self.assertIn("skipped", optional_policy.message)

    def test_download_path_selection_and_mock_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = download_path(Path(tmpdir) / "downloads", "micromamba.tar.bz2")

            def fake_downloader(url: str, destination: Path) -> None:
                destination.write_bytes(f"downloaded:{url}".encode("utf-8"))

            result = download_file("https://example.invalid/micromamba", target, downloader=fake_downloader)

        self.assertTrue(result.success)
        self.assertEqual(result.attempts, 1)

    def test_internal_beta_guard_blocks_untested_platforms(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.MACOS, machine="x86_64")
            installer = BackendInstaller(paths=paths, environ={})
            result = installer.install_backend()

        self.assertFalse(backend_install_enabled({}, BackendPlatform.MACOS, machine="x86_64"))
        self.assertFalse(result.success)
        self.assertFalse(result.modified_system)
        self.assertIn("planned/experimental", result.message)
        self.assertFalse(paths.backend_root.exists())

    def test_windows_internal_beta_enables_install_without_env_var(self) -> None:
        availability = backend_install_availability(environ={}, platform=BackendPlatform.WINDOWS, machine="x86_64")

        self.assertTrue(availability.enabled)
        self.assertTrue(backend_install_enabled({}, BackendPlatform.WINDOWS, machine="x86_64"))
        self.assertFalse(availability.developer_override)
        self.assertIn("Windows internal beta", availability.reason)

    def test_linux_x86_64_install_is_available_without_override(self) -> None:
        availability = backend_install_availability({}, BackendPlatform.LINUX, machine="x86_64")
        self.assertTrue(availability.enabled)
        self.assertFalse(availability.developer_override)
        self.assertFalse(backend_install_enabled({}, BackendPlatform.LINUX, machine="aarch64"))

    def test_bootstrap_uses_manifest_artifact_and_required_checksum(self) -> None:
        paths = resolve_backend_paths(platform=BackendPlatform.LINUX, machine="x86_64")
        policy = micromamba_bootstrap_policy(paths)
        self.assertIn("micromamba-2.9.0-0.tar.bz2", policy.source_url)
        self.assertTrue(policy.checksum_policy.required)
        self.assertEqual(len(policy.checksum_policy.expected), 64)

    def test_staging_path_creation_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.LINUX)
            installer = BackendInstaller(paths=paths, environ={BACKEND_INSTALL_ENABLE_ENV: "1"})
            prepared = installer.prepare_staging()
            staged = staging_paths(paths)
            self.assertTrue(prepared.success)
            self.assertTrue(staged.root.exists())
            self.assertTrue(paths.downloads_dir.exists())
            self.assertTrue(paths.logs_dir.exists())
            rolled_back = installer.rollback_failed_install()
            self.assertTrue(rolled_back.success)
            self.assertFalse(staged.root.exists())

    def test_environment_spec_discovery(self) -> None:
        self.assertTrue(default_environment_spec_file(BackendPlatform.LINUX).name.endswith("linux.yml"))
        self.assertTrue(default_environment_spec_file(BackendPlatform.WINDOWS).name.endswith("windows.yml"))
        self.assertTrue(default_environment_spec_file(BackendPlatform.MACOS).name.endswith("macos.yml"))
        self.assertTrue(default_environment_spec_file(BackendPlatform.LINUX).exists())

    def test_config_write_after_mock_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.LINUX)
            installer = BackendInstaller(paths=paths, environ={BACKEND_INSTALL_ENABLE_ENV: "1"})
            result = installer.write_backend_config(BackendStatus.READY)
            config = load_backend_config(paths.config_file)

        self.assertTrue(result.success)
        self.assertIsNotNone(config)
        self.assertEqual(config.status, BackendStatus.READY)
        self.assertEqual(config.backend_root, paths.backend_root)

    def test_install_failure_returns_failed_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.LINUX)

            def failing_downloader(url: str, destination: Path) -> None:
                raise OSError("network disabled in test")

            installer = BackendInstaller(paths=paths, environ={BACKEND_INSTALL_ENABLE_ENV: "1"}, downloader=failing_downloader)
            result = installer.install_backend(policy=micromamba_bootstrap_policy(paths, checksum="0" * 64))

        self.assertFalse(result.success)
        self.assertEqual(result.status, BackendStatus.FAILED)
        self.assertFalse(paths.staging_dir.exists())

    def test_no_qgis_paths_are_modified(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "PyForestScan" / "backend", platform=BackendPlatform.LINUX)
            installer = BackendInstaller(paths=paths, environ={BACKEND_INSTALL_ENABLE_ENV: "1"})
            installer.prepare_staging()
            touched_paths = (paths.backend_root, paths.downloads_dir, paths.logs_dir, paths.staging_dir)

        for path in touched_paths:
            self.assertIn("PyForestScan", path.as_posix())
            self.assertNotIn("QGIS", path.as_posix())


if __name__ == "__main__":
    unittest.main()
