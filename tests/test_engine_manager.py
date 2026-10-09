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

    def test_windows_extended_paths_cover_reported_failure_and_unc(self):
        reported = (
            r"C:\Users\example_account\AppData\Local\pyforestscan\engines"
            r"\0.4.1-1-win-64\instances\814607b8d98e4b0481c661b4c9f1c02b"
            r"\mamba\pkgs\file\C_\Users\example_account\AppData\Local"
            r"\pyforestscan\engines\0.4.1-1-win-64\downloads"
            r"\graphite2-1.3.15-h5112557_1\info\licenses"
        )
        self.assertEqual(len(reported), 260)
        cases = (
            (reported, "\\\\?\\" + reported),
            (
                r"C:\Users\person\engine\temporary\..\cache",
                r"\\?\C:\Users\person\engine\cache",
            ),
            (r"\\server\share\engine", r"\\?\UNC\server\share\engine"),
            (r"\\?\C:\engine\cache", r"\\?\C:\engine\cache"),
            (r"\\?\UNC\server\share\engine", r"\\?\UNC\server\share\engine"),
        )
        for original, expected in cases:
            with self.subTest(path=original):
                self.assertEqual(manager._windows_extended_path(original), expected)

    @unittest.skipUnless(os.name == "nt", "Requires the Windows filesystem")
    def test_windows_removal_handles_descendants_longer_than_max_path(self):
        base = self.owned()
        generation = self.generation(base)
        deep = generation.joinpath(*(["nested-" + "x" * 32] * 8))
        filename = str(deep / "payload.txt")
        self.assertGreater(len(filename), 260)
        os.makedirs(manager._windows_extended_path(deep))
        with open(manager._windows_extended_path(filename), "w") as stream:
            stream.write("owned temporary package content")
        try:
            manager._remove_generation(generation, base)
            self.assertFalse(generation.exists())
        finally:
            if generation.exists():
                shutil.rmtree(manager._windows_extended_path(generation))

    def test_partial_generation_removal_restores_ownership_for_retry(self):
        base = self.owned()
        generation = self.generation(base)
        retained = generation / "locked-package.dll"
        retained.write_bytes(b"still in use")

        def partial_removal(path):
            (Path(path) / manager.MARKER).unlink()
            raise PermissionError("package file is temporarily locked")

        with patch.object(manager, "_remove_tree", side_effect=partial_removal):
            with self.assertRaisesRegex(PermissionError, "temporarily locked"):
                manager._remove_generation(generation, base)
        self.assertTrue(manager._owned(generation, "generation"))
        self.assertEqual(retained.read_bytes(), b"still in use")
        manager._remove_generation(generation, base)
        self.assertFalse(generation.exists())

    def test_package_cache_seed_copies_if_hard_links_are_unavailable(self):
        source = Path(self.directory.name) / "example-1.0-0.conda"
        source.write_bytes(b"verified archive")
        package = {
            "filename": source.name,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "size": source.stat().st_size,
        }
        cache = Path(self.directory.name) / "private-cache"
        with patch.object(manager.os, "link", side_effect=OSError("no hard links")):
            manager._seed_package_cache([(source, package)], cache)
        target = cache / source.name
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertNotEqual(target.stat().st_ino, source.stat().st_ino)
        target.unlink()
        self.assertEqual(source.read_bytes(), b"verified archive")

    def _installation(self, selftest, *, process_result=(0, ""), cleanup_failed=False):
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
        initial_generations = set((base / "instances").iterdir())

        def download(url, destination, digest, size, cancel=None, progress=None):
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"test artifact")
            return destination

        def process(command, **kwargs):
            self.assertEqual(command[1], "install")
            self.assertIn("--no-env", command)
            self.assertIn("--offline", command)
            config = Path(command[command.index("--rc-file") + 1])
            self.assertEqual(config.suffix, ".yaml")
            prefix = Path(command[command.index("--prefix") + 1])
            self.assertTrue((prefix / "conda-meta" / "history").is_file())
            self.assertEqual(
                json.loads(config.read_text())["pkgs_dirs"],
                [str(prefix.parent / "mamba" / "pkgs")],
            )
            explicit = Path(command[command.index("--file") + 1]).read_text()
            self.assertEqual(
                explicit.splitlines(),
                ["@EXPLICIT"]
                + [
                    package["url"] + "#sha256:" + package["sha256"]
                    for package in lock["packages"]
                ],
            )
            self.assertNotIn("file://", explicit)
            for package in lock["packages"]:
                archive = prefix.parent / "mamba" / "pkgs" / package["filename"]
                self.assertEqual(archive.read_bytes(), b"test artifact")
            python = manager._python(prefix)
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("new interpreter")
            return process_result

        with (
            patch.object(manager, "_download_verified", side_effect=download),
            patch.object(manager, "run_process", side_effect=process),
            patch.object(manager, "call_with_python", side_effect=selftest),
        ):
            try:
                result = manager.install_engine()
            except client.EngineError:
                self.assertEqual((base / "active.json").read_bytes(), before)
                remaining = set((base / "instances").iterdir())
                if cleanup_failed:
                    self.assertTrue(initial_generations < remaining)
                else:
                    self.assertEqual(remaining, initial_generations)
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

    def test_cleanup_failure_preserves_original_installer_error(self):
        with patch.object(
            manager, "_remove_generation", side_effect=OSError("cleanup is locked")
        ):
            with self.assertRaises(client.EngineError) as caught:
                self._installation(
                    lambda *a, **k: self.fail("Failed installer must not self-test"),
                    process_result=(1, "specific package extraction failure"),
                    cleanup_failed=True,
                )
        self.assertIn("specific package extraction failure", str(caught.exception))
        self.assertIn("cleanup is locked", str(caught.exception))

    def test_cleanup_failure_keeps_cancellation_type(self):
        def canceled(*args, **kwargs):
            raise client.EngineCanceled("installation was canceled")

        with patch.object(
            manager, "_remove_generation", side_effect=OSError("cleanup is locked")
        ):
            with self.assertRaises(client.EngineCanceled) as caught:
                self._installation(canceled, cleanup_failed=True)
        self.assertIn("installation was canceled", str(caught.exception))
        self.assertIn("cleanup is locked", str(caught.exception))

    def test_retry_can_install_beside_unsentinelled_legacy_remnant(self):
        base = self.owned()
        legacy = base / "instances" / ("b" * 32)
        legacy.mkdir()
        retained = legacy / "package-fragment.txt"
        retained.write_text("legacy data must remain untouched")
        _, _, result = self._installation(lambda *a, **k: {"ready": True})
        self.assertTrue(result["installed"])
        self.assertFalse((legacy / manager.MARKER).exists())
        self.assertEqual(retained.read_text(), "legacy data must remain untouched")

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
