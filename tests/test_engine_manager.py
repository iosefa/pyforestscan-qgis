"""Installer integrity, rollback, deletion boundaries, and process isolation."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

PLUGIN_ROOT = Path(
    os.environ.get("PYFORESTSCAN_PLUGIN_PATH", Path(__file__).resolve().parents[1])
)
spec = importlib.util.spec_from_file_location(
    "pyforestscan_test_runtime",
    PLUGIN_ROOT / "runtime" / "__init__.py",
    submodule_search_locations=[str(PLUGIN_ROOT / "runtime")],
)
runtime = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runtime
spec.loader.exec_module(runtime)
from pyforestscan_test_runtime import client, manager  # noqa: E402 -- import the selected synthetic runtime package


class EngineManagerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pyforestscan-manager-")
        self.root = Path(self.directory.name) / "engines"
        self.override = patch.dict(
            os.environ, {"PYFORESTSCAN_ENGINE_ROOT": str(self.root)}
        )
        self.override.start()

    def tearDown(self):
        self.override.stop()
        self.directory.cleanup()

    def owned(self):
        manager._ensure_owned(self.root, "root")
        base = manager.version_root()
        manager._ensure_owned(base, "version")
        (base / "instances").mkdir(exist_ok=True)
        return base

    def generation(self, base, name="a" * 32):
        folder = base / "instances" / name
        manager._ensure_owned(folder, "generation")
        python = manager._python(folder / "env")
        python.parent.mkdir(parents=True)
        python.write_text("test interpreter", encoding="utf-8")
        return folder

    def test_manifests_have_hashes_complete_platforms_and_no_qgis(self):
        for tag in ("osx-arm64", "osx-64", "linux-64", "win-64"):
            with (
                self.subTest(platform=tag),
                patch.object(manager, "platform_tag", return_value=tag),
            ):
                lock, digest = manager._lock_data()
                self.assertEqual(len(digest), 64)
                names = {entry["name"] for entry in lock["packages"]}
                self.assertIn("python", names)
                self.assertIn("gdal", names)
                self.assertNotIn("qgis", names)
                self.assertGreater(manager._bootstrap_data()["size"], 0)

    def test_download_verified_and_cached(self):
        data = b"verified package"
        digest = hashlib.sha256(data).hexdigest()
        target = Path(self.directory.name) / "package.conda"

        def download(url, destination, size, cancel, progress):
            Path(destination).write_bytes(data)

        with patch.object(manager, "_download", side_effect=download) as fetch:
            manager._download_verified(
                "https://example.org/package", target, digest, len(data)
            )
            manager._download_verified(
                "https://example.org/package", target, digest, len(data)
            )
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(target.read_bytes(), data)

    def test_tampered_download_preserves_previous_file_and_cleans_staging(self):
        target = Path(self.directory.name) / "package.conda"
        target.write_bytes(b"previous")

        def download(url, destination, size, cancel, progress):
            Path(destination).write_bytes(b"tampered")

        with patch.object(manager, "_download", side_effect=download):
            with self.assertRaisesRegex(client.EngineError, "checksum"):
                manager._download_verified(
                    "https://example.org/package", target, "a" * 64, 8
                )
        self.assertEqual(target.read_bytes(), b"previous")
        self.assertEqual(list(target.parent.glob(".download-*")), [])

    def test_plain_http_and_download_symlinks_are_rejected(self):
        with self.assertRaises(client.EngineError):
            manager._HTTPSRedirect().redirect_request(
                None, None, 302, "", {}, "http://example.org/file"
            )
        target = Path(self.directory.name) / "linked.conda"
        other = Path(self.directory.name) / "outside"
        other.write_bytes(b"untouched")
        try:
            target.symlink_to(other)
        except OSError:
            self.skipTest("Symbolic links unavailable")
        with self.assertRaises(client.EngineError):
            manager._download_verified("https://example.org/file", target, "a" * 64, 9)
        self.assertEqual(other.read_bytes(), b"untouched")

    def test_unowned_root_is_never_adopted_or_deleted(self):
        self.root.mkdir()
        marker = self.root / "important.txt"
        marker.write_text("preserve")
        with self.assertRaises(client.EngineError):
            manager._ensure_owned(self.root, "root")
        self.assertEqual(marker.read_text(), "preserve")

    def test_unowned_generation_blocks_removal_before_activation_changes(self):
        base = self.owned()
        self.generation(base)
        unknown = base / "instances" / "user-data"
        unknown.mkdir()
        (unknown / "important.txt").write_text("preserve")
        active = base / "active.json"
        active.write_text('{"generation":"test"}')
        with self.assertRaises(client.EngineError):
            manager.remove_engine()
        self.assertTrue(active.exists())
        self.assertEqual((unknown / "important.txt").read_text(), "preserve")

    def test_generation_traversal_and_link_activation_are_rejected(self):
        base = self.owned()
        (base / "active.json").write_text(json.dumps({"generation": "../../outside"}))
        self.assertIsNone(manager.engine_python())
        with self.assertRaises(client.EngineError):
            manager._remove_generation(base.parent, base)

    def test_remove_deletes_only_owned_generations_and_keeps_downloads(self):
        base = self.owned()
        generation = self.generation(base)
        (base / "active.json").write_text("{}")
        (base / "downloads").mkdir()
        cached = base / "downloads" / "verified.conda"
        cached.write_bytes(b"retained")
        result = manager.remove_engine()
        self.assertFalse(result["installed"])
        self.assertFalse(generation.exists())
        self.assertFalse((base / "active.json").exists())
        self.assertEqual(cached.read_bytes(), b"retained")

    def _installation(self, selftest):
        base = self.owned()
        previous = self.generation(base)
        lock, digest = manager._lock_data()
        manager._atomic_json(
            base / "active.json",
            {
                "protocol": 1,
                "generation": previous.name,
                "lock_sha256": digest,
                "selftest": {"ready": True},
            },
        )
        before = (base / "active.json").read_bytes()

        def download(url, destination, digest, size, cancel=None, progress=None):
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"test artifact")
            return destination

        def process(command, **kwargs):
            self.assertEqual(command[1], "install")
            self.assertIn("--no-env", command)
            config = Path(command[command.index("--rc-file") + 1])
            self.assertEqual(config.suffix, ".yaml")
            prefix = Path(command[command.index("--prefix") + 1])
            self.assertTrue((prefix / "conda-meta" / "history").is_file())
            self.assertEqual(
                json.loads(config.read_text())["pkgs_dirs"],
                [str(prefix.parent / "mamba" / "pkgs")],
            )
            python = manager._python(prefix)
            python.parent.mkdir(parents=True)
            python.write_text("new interpreter")
            return 0, ""

        with (
            patch.object(manager, "_download_verified", side_effect=download),
            patch.object(manager, "run_process", side_effect=process),
            patch.object(manager, "call_with_python", side_effect=selftest),
        ):
            try:
                result = manager.install_engine()
            except client.EngineError:
                self.assertEqual((base / "active.json").read_bytes(), before)
                self.assertEqual(list((base / "instances").iterdir()), [previous])
                raise
        return base, previous, result

    def test_failed_repair_keeps_previous_working_engine(self):
        with self.assertRaisesRegex(client.EngineError, "failed validation"):
            self._installation(
                lambda *a, **k: (_ for _ in ()).throw(
                    client.EngineError("failed validation")
                )
            )

    def test_successful_repair_activates_only_after_ready_selftest(self):
        base, previous, result = self._installation(lambda *a, **k: {"ready": True})
        self.assertTrue(result["installed"])
        self.assertTrue(previous.exists())
        self.assertNotEqual(
            json.loads((base / "active.json").read_text())["generation"], previous.name
        )

    def test_selftest_not_ready_cannot_activate(self):
        with self.assertRaisesRegex(client.EngineError, "did not pass"):
            self._installation(lambda *a, **k: {"ready": False})

    def test_environment_removes_host_qgis_and_python_overrides(self):
        pollution = {
            "PYTHONPATH": "/qgis/python",
            "PYTHONHOME": "/qgis",
            "GDAL_DATA": "/qgis/gdal",
            "PROJ_LIB": "/qgis/proj",
            "PDAL_DRIVER_PATH": "/qgis/pdal",
            "QT_PLUGIN_PATH": "/qgis/qt",
            "DYLD_LIBRARY_PATH": "/qgis/lib",
            "LD_LIBRARY_PATH": "/qgis/lib",
            "CONDA_PREFIX": "/qgis",
            "PATH": "/qgis/bin",
            "HTTPS_PROXY": "https://proxy.example.org",
        }
        with patch.dict(os.environ, pollution):
            env = client.sanitized_environment()
        for key in pollution:
            if key not in ("PATH", "HTTPS_PROXY"):
                self.assertNotIn(key, env)
        self.assertNotIn("qgis", env["PATH"])
        self.assertEqual(env["HTTPS_PROXY"], pollution["HTTPS_PROXY"])

    def usable_process_python(self):
        candidates = [sys.executable, shutil.which("python3"), "/usr/bin/python3"]
        for candidate in dict.fromkeys(filter(None, candidates)):
            try:
                result = subprocess.run(
                    [candidate, "-I", "-c", "import encodings; print('ready')"],
                    env=client.sanitized_environment(),
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode == 0 and b"ready" in result.stdout:
                    return candidate
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.skipTest("No standalone Python is available for isolated process checks")

    def test_both_process_pipes_are_drained(self):
        seen = []
        code, tail = client.run_process(
            [
                self.usable_process_python(),
                "-I",
                "-c",
                "import sys; print('stdout'); print('stderr', file=sys.stderr)",
            ],
            event=seen.append,
            timeout=5,
        )
        self.assertEqual(code, 0)
        self.assertIn("stdout", seen)
        self.assertIn("stderr", seen)

    def test_process_cancellation_terminates_worker(self):
        checks = []

        def cancel():
            checks.append(True)
            return len(checks) > 1

        with self.assertRaises(client.EngineCanceled):
            client.run_process(
                [
                    self.usable_process_python(),
                    "-I",
                    "-c",
                    "import time; time.sleep(30)",
                ],
                cancel=cancel,
                timeout=5,
            )

    def test_protocol_request_and_error_response(self):
        def process(command, **kwargs):
            self.assertIn("-I", command)
            request = json.loads(
                Path(command[command.index("--request") + 1]).read_text()
            )
            self.assertEqual(
                request, {"protocol": 1, "operation": "selftest", "payload": {}}
            )
            Path(command[command.index("--result") + 1]).write_text(
                json.dumps(
                    {
                        "protocol": 1,
                        "ok": False,
                        "error": {"message": "specific processing failure"},
                    }
                )
            )
            return 1, ""

        with patch.object(client, "run_process", side_effect=process):
            with self.assertRaisesRegex(
                client.EngineError, "specific processing failure"
            ):
                client.call_with_python(sys.executable, "selftest", {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
