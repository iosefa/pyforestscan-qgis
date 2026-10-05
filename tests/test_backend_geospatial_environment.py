"""Tests for conda geospatial backend environment discovery."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pyforestscan_qgis.core.backend.models import BackendDependency, BackendPlatform, BackendRegistry, DependencyVerificationStatus
from pyforestscan_qgis.core.backend.paths import resolve_backend_paths
from pyforestscan_qgis.core.backend.process_env import build_clean_subprocess_env, conda_environment_path_entries
from pyforestscan_qgis.core.backend.verification import _dependency_path, conda_stack_summary, python_import_command, verify_backend


class BackendGeospatialEnvironmentTests(unittest.TestCase):
    """Validate Windows conda geospatial executable and DLL path handling."""

    def test_windows_conda_path_entries_include_library_bin_for_dlls(self) -> None:
        env = Path(r"C:\Users\Alala\AppData\Local\PyForestScan\backend\env")
        entries = conda_environment_path_entries(env, "windows")

        self.assertEqual(entries[0], env)
        self.assertEqual(entries[1], env / "Scripts")
        self.assertEqual(entries[2], env / "Library" / "bin")
        clean = build_clean_subprocess_env({"PATH": r"C:\Windows\System32"}, prepend_paths=entries)
        path_value = clean["PATH"]
        self.assertLess(path_value.index(str(env)), path_value.index(str(env / "Library" / "bin")))
        self.assertIn(str(env / "Library" / "bin"), path_value)

    def test_windows_dependency_path_finds_pdal_in_library_bin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.WINDOWS)
            pdal = paths.environment_path / "Library" / "bin" / "pdal.exe"
            pdal.parent.mkdir(parents=True, exist_ok=True)
            pdal.write_text("pdal", encoding="utf-8")
            dependency = BackendDependency("pdal", "PDAL", "geospatial runtime", True, executable_name="pdal", verification_command=("--version",))

            found = _dependency_path(dependency, paths)

        self.assertEqual(found, pdal)

    def test_windows_dependency_path_finds_gdalinfo_in_library_bin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.WINDOWS)
            gdalinfo = paths.environment_path / "Library" / "bin" / "gdalinfo.exe"
            gdalinfo.parent.mkdir(parents=True, exist_ok=True)
            gdalinfo.write_text("gdalinfo", encoding="utf-8")
            dependency = BackendDependency("gdal", "GDAL", "geospatial runtime", True, executable_name="gdalinfo", verification_command=("--version",))

            found = _dependency_path(dependency, paths)

        self.assertEqual(found, gdalinfo)

    def test_import_verification_uses_backend_conda_dll_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.WINDOWS)
            paths.backend_root.mkdir(parents=True, exist_ok=True)
            paths.environment_path.mkdir(parents=True, exist_ok=True)
            paths.python_executable.parent.mkdir(parents=True, exist_ok=True)
            paths.python_executable.write_text("python", encoding="utf-8")
            registry = BackendRegistry(
                dependencies=(BackendDependency("rasterio", "rasterio", "python package", True, source="conda-forge", python_import_name="rasterio"),)
            )
            captured_env = {}

            def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
                captured_env.update(kwargs["env"])
                return subprocess.CompletedProcess(command, 0, stdout="1.3.9\n", stderr="")

            with patch("pyforestscan_qgis.core.backend.verification.subprocess.run", fake_run):
                result = verify_backend(paths, registry, require_config=False)

        rasterio = next(check for check in result.checks if check.name == "rasterio")
        self.assertIs(rasterio.status, DependencyVerificationStatus.PASS)
        self.assertIn(str(paths.environment_path / "Library" / "bin"), captured_env["PATH"])
        self.assertIn(str(paths.environment_path / "Scripts"), captured_env["PATH"])

    def test_rasterio_import_command_reports_gdal_version_and_memoryfile(self) -> None:
        command = python_import_command(Path("python"), "rasterio")
        code = command[-1]

        self.assertIn("__gdal_version__", code)
        self.assertIn("MemoryFile", code)
        self.assertIn("rasterio_memoryfile=ok", code)

    def test_failed_rasterio_check_surfaces_conda_stack_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.WINDOWS)
            paths.backend_root.mkdir(parents=True, exist_ok=True)
            paths.environment_path.mkdir(parents=True, exist_ok=True)
            paths.python_executable.parent.mkdir(parents=True, exist_ok=True)
            paths.python_executable.write_text("python", encoding="utf-8")
            paths.micromamba_executable.parent.mkdir(parents=True, exist_ok=True)
            paths.micromamba_executable.write_text("micromamba", encoding="utf-8")
            registry = BackendRegistry(
                dependencies=(BackendDependency("rasterio", "rasterio", "python package", True, source="conda-forge", python_import_name="rasterio"),)
            )

            def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
                if "list" in command:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout="rasterio 1.3.10 py312_gdal39_0 conda-forge\ngdal 3.9.3 py312_0 conda-forge\nlibgdal 3.9.3 h123_0 conda-forge\nnumpy 1.26.4 py312_0 conda-forge\n",
                        stderr="",
                    )
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="ImportError: DLL load failed while importing _base: The specified procedure could not be found.")

            with patch("pyforestscan_qgis.core.backend.verification.subprocess.run", fake_run):
                result = verify_backend(paths, registry, require_config=False)

        rasterio = next(check for check in result.checks if check.name == "rasterio")
        self.assertIs(rasterio.status, DependencyVerificationStatus.FAIL)
        self.assertIn("Conda geospatial package summary", rasterio.message)
        self.assertIn("rasterio 1.3.10", rasterio.message)
        self.assertIn("libgdal 3.9.3", rasterio.message)
        self.assertFalse(result.passed())

    def test_conda_stack_summary_filters_geospatial_packages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.WINDOWS)
            paths.micromamba_executable.parent.mkdir(parents=True, exist_ok=True)
            paths.micromamba_executable.write_text("micromamba", encoding="utf-8")

            def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="# packages\nrasterio 1.3.10 py312_gdal39_0 conda-forge\nclick 8.1.7 pyhd8ed1ab_0 conda-forge\nproj 9.4.1 h123_0 conda-forge\nzstd 1.5.6 h456_0 conda-forge\n",
                    stderr="",
                )

            with patch("pyforestscan_qgis.core.backend.verification.subprocess.run", fake_run):
                check = conda_stack_summary(paths)

        self.assertIn("rasterio 1.3.10", check.stdout_preview)
        self.assertIn("proj 9.4.1", check.stdout_preview)
        self.assertIn("zstd 1.5.6", check.stdout_preview)
        self.assertNotIn("click", check.stdout_preview)


    def test_environment_specs_keep_geospatial_stack_on_conda_forge(self) -> None:
        text = "\n".join((Path(__file__).resolve().parents[1] / name).read_text(encoding="utf-8") for name in (
            "backend_specs/environment.yml",
            "backend_specs/environment.windows.yml",
            "backend_specs/environment.linux.yml",
            "backend_specs/environment.macos.yml",
        ))

        self.assertIn("  - pdal>=2.9,<3", text)
        self.assertIn("  - python-pdal", text)
        self.assertIn("  - gdal>=3.9,<4", text)
        self.assertIn("  - libgdal>=3.9,<4", text)
        self.assertIn("  - rasterio>=1.4,<2", text)
        self.assertIn("  - scipy>=1.16,<2", text)
        self.assertIn("  - geopandas>=1.1.1,<2", text)
        self.assertIn("  - matplotlib>=3.10.3,<4", text)
        self.assertIn("  - tqdm>=4.66,<5", text)
        self.assertNotIn("pip:\n", text)
        self.assertNotIn("GDAL", text.split("  - pip")[-1])
        self.assertNotIn("rasterio", text.split("  - pip")[-1])


if __name__ == "__main__":
    unittest.main()
