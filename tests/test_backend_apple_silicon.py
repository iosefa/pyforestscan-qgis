"""Apple Silicon installer routing; no Mac binaries run on the test host."""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from pyforestscan_qgis.core.backend.bootstrap import build_micromamba_bootstrap_plan
from pyforestscan_qgis.core.backend.installer import BackendInstaller, staged_backend_paths, staging_paths
from pyforestscan_qgis.core.backend.manifest import BackendManifestError, load_backend_manifest
from pyforestscan_qgis.core.backend.micromamba import micromamba_bootstrap_policy
from pyforestscan_qgis.core.backend.models import BackendPlatform
from pyforestscan_qgis.core.backend.paths import resolve_backend_paths
from pyforestscan_qgis.core.backend.service import BackendService
from pyforestscan_qgis.core.platform_compat import _is_rosetta_process, native_architecture


class AppleSiliconInstallerTests(unittest.TestCase):
    def test_native_and_translated_qgis_select_same_arm_engine(self) -> None:
        for process_machine, translated in (("arm64", False), ("x86_64", True)):
            with self.subTest(process_machine=process_machine), \
                 patch("pyforestscan_qgis.core.platform_compat.platform.machine", return_value=process_machine), \
                 patch("pyforestscan_qgis.core.platform_compat._is_rosetta_process", return_value=translated):
                paths = resolve_backend_paths(platform=BackendPlatform.MACOS)
            policy = micromamba_bootstrap_policy(paths)
            self.assertEqual(paths.architecture, "arm64")
            self.assertEqual(staged_backend_paths(paths).architecture, "arm64")
            self.assertIn("/osx-arm64/", policy.source_url)
            self.assertEqual(policy.archive_name, "micromamba-osx-arm64.tar.bz2")
            self.assertEqual(policy.checksum_policy.expected, "500f5074feb8d02c4296ef9921c3650ed2874171805a9fbb8fbb53896433646b")
            self.assertTrue(BackendInstaller(paths, environ={}).enabled())
            self.assertTrue(BackendService(paths=paths).install_availability().enabled)
            self.assertIn("experimental", BackendInstaller(paths, environ={}).install_availability().reason)

    def test_rosetta_probe_reads_current_process_flag(self) -> None:
        for flag, code, expected in ((1, 0, True), (0, 0, False), (1, -1, False)):
            def sysctl(name, output, size, new_value, new_length):
                self.assertEqual(name, b"sysctl.proc_translated")
                ctypes.cast(output, ctypes.POINTER(ctypes.c_int))[0] = flag
                return code
            library = Mock()
            library.sysctlbyname = Mock(side_effect=sysctl)
            with self.subTest(flag=flag, code=code), patch("pyforestscan_qgis.core.platform_compat.ctypes.CDLL", return_value=library):
                self.assertEqual(_is_rosetta_process(), expected)

    def test_missing_rosetta_probe_does_not_guess_arm(self) -> None:
        with patch("pyforestscan_qgis.core.platform_compat.platform.machine", return_value="x86_64"), \
             patch("pyforestscan_qgis.core.platform_compat.ctypes.CDLL", side_effect=OSError("unavailable")):
            self.assertEqual(native_architecture("Darwin"), "x86_64")
            self.assertFalse(BackendInstaller(resolve_backend_paths(platform=BackendPlatform.MACOS), environ={}).enabled())

    def test_arm_hash_never_falls_back_to_intel_hash(self) -> None:
        artifact = load_backend_manifest().micromamba_artifact()
        legacy = replace(artifact, hashes={"macos": artifact.hashes["macos"]})
        self.assertIsNone(legacy.sha256_for(BackendPlatform.MACOS, "arm64"))
        self.assertEqual(legacy.sha256_for(BackendPlatform.MACOS, "x86_64"), artifact.hashes["macos"])

    def test_preview_uses_arm_artifact_and_survives_bad_manifest(self) -> None:
        paths = resolve_backend_paths(platform=BackendPlatform.MACOS, machine="arm64")
        preview = build_micromamba_bootstrap_plan(paths)
        self.assertIn("osx-arm64", preview.artifact_name)
        self.assertIn("/osx-arm64/", preview.source_note)
        with patch("pyforestscan_qgis.core.backend.bootstrap.micromamba_bootstrap_policy", side_effect=BackendManifestError("bad manifest")):
            preview = build_micromamba_bootstrap_plan(paths)
        self.assertIn("bad manifest", preview.warnings)

    def test_mac_create_and_promotion_keep_native_target_and_space_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = resolve_backend_paths(
                home=Path(directory), platform=BackendPlatform.MACOS, machine="arm64",
            )
            staged = staging_paths(paths)
            staged.micromamba_executable.parent.mkdir(parents=True)
            staged.micromamba_executable.touch()
            staged.environment_path.mkdir()
            (staged.environment_path / "data").write_text("engine")
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                if "--clone" in command:
                    shutil.copytree(staged.environment_path, paths.environment_path)
                return subprocess.CompletedProcess(command, 0, "ok", "")

            installer = BackendInstaller(paths, runner=runner, environ={
                "HOME": directory, "PATH": "/Applications/QGIS.app/Contents/MacOS/bin:/usr/bin",
                "PYTHONPATH": "/Applications/QGIS.app/Contents/Resources/python",
                "DYLD_LIBRARY_PATH": "/Applications/QGIS.app/Contents/lib",
                "CONDA_SUBDIR": "osx-64",
            })
            self.assertTrue(installer.create_environment().success)
            self.assertTrue(installer.promote_staging().success)
            self.assertEqual((paths.environment_path / "data").read_text(), "engine")
            for command, kwargs in calls[:2]:
                self.assertEqual(command[command.index("--platform") + 1], "osx-arm64")
                self.assertIn("--no-rc", command)
                prefix = command[command.index("-p") + 1]
                self.assertIn("Application Support", prefix)
                self.assertIn(prefix, (str(staged.environment_path), str(paths.environment_path)))
                self.assertNotIn("CONDA_SUBDIR", kwargs["env"])
                self.assertNotIn("DYLD_LIBRARY_PATH", kwargs["env"])
                self.assertNotIn("PYTHONPATH", kwargs["env"])
                self.assertNotIn("QGIS.app", kwargs["env"]["PATH"])
            self.assertEqual(calls[2][0][0], str(paths.python_executable))
            self.assertEqual(calls[3][0][-3:], ["-m", "pip", "check"])


if __name__ == "__main__":
    unittest.main()
