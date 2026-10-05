"""Tests for Phase 22B PBM dry-run install planning."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pyforestscan_qgis.core.backend.environment_spec import build_environment_spec
from pyforestscan_qgis.core.backend.install_plan import create_backend_install_plan, format_install_plan
from pyforestscan_qgis.core.backend.models import BackendPlatform
from pyforestscan_qgis.core.backend.paths import default_backend_root, resolve_backend_paths
from pyforestscan_qgis.core.backend.registry import default_backend_registry
from pyforestscan_qgis.core.backend.service import BackendService


class BackendInstallPlanTests(unittest.TestCase):
    """Validate PBM install planning remains dry-run and registry-driven."""

    def test_install_plan_contains_backend_paths_and_packages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.LINUX)
            plan = create_backend_install_plan(paths=paths)

        self.assertTrue(plan.dry_run_only)
        self.assertEqual(plan.backend_root, paths.backend_root)
        self.assertEqual(plan.micromamba_location, paths.micromamba_executable)
        self.assertEqual(plan.environment_path, paths.environment_path)
        self.assertEqual(
            plan.required_package_names(),
            ("python", "pdal", "python-pdal", "gdal", "rasterio", "numpy", "scipy", "pandas", "shapely", "pyproj", "fiona", "geopandas", "matplotlib", "tqdm", "requests", "pyforestscan"),
        )
        self.assertIn("conda-forge", [channel.name for channel in plan.channels])
        self.assertTrue(any("linux internal beta" in warning.lower() for warning in plan.warnings))

    def test_environment_spec_uses_dependency_registry(self) -> None:
        registry = default_backend_registry()
        spec = build_environment_spec(registry)
        names = spec.package_names()

        self.assertEqual(names[0], "python")
        self.assertIn("pyforestscan", names)
        self.assertIn("python-pdal", names)
        registry_names = registry.dependency_names()
        for name in names:
            self.assertIn(name, registry_names)

    def test_backend_path_selection_is_platform_specific(self) -> None:
        home = Path("/home/example")
        windows = default_backend_root(BackendPlatform.WINDOWS, environ={"LOCALAPPDATA": "C:/Users/Example/AppData/Local"}, home=home)
        linux = default_backend_root(BackendPlatform.LINUX, environ={}, home=home)
        macos = default_backend_root(BackendPlatform.MACOS, environ={}, home=home)

        self.assertEqual(windows.as_posix(), "C:/Users/Example/AppData/Local/PyForestScan/backend")
        self.assertEqual(linux, home / ".local" / "share" / "PyForestScan" / "backend")
        self.assertEqual(macos, home / "Library" / "Application Support" / "PyForestScan" / "backend")

    def test_dry_run_plan_does_not_modify_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            paths = resolve_backend_paths(backend_root=tmp / "backend", platform=BackendPlatform.LINUX)
            before = sorted(path.name for path in tmp.iterdir())
            service = BackendService(paths=paths)
            plan = service.preview_install_plan()
            text = service.format_install_plan(plan)
            after = sorted(path.name for path in tmp.iterdir())

        self.assertEqual(before, after)
        self.assertIn("Install Plan Preview", text)
        self.assertIn("Windows and Linux x86_64 beta builds can execute this plan", text)

    def test_format_install_plan_lists_verification_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_backend_paths(backend_root=Path(tmpdir) / "backend", platform=BackendPlatform.LINUX)
            text = format_install_plan(create_backend_install_plan(paths=paths))

        self.assertIn("Verification steps after install", text)
        self.assertIn("Rollback / repair plan", text)
        self.assertIn("Offline install placeholder", text)


if __name__ == "__main__":
    unittest.main()
