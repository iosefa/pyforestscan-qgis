#!/usr/bin/env python3
"""Test an extracted plugin ZIP in real QGIS with temporary user settings.

Run with QGIS's Python interpreter, for example on Linux:
QT_QPA_PLATFORM=offscreen /usr/bin/python3 scripts/smoke_test_plugin.py
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def smoke_test(zip_path: Path) -> dict[str, object]:
    """Exercise the shipped plugin without changing a user's QGIS profile."""
    from qgis.core import Qgis, QgsApplication
    from qgis.PyQt.QtCore import QCoreApplication, QEvent, QSettings, QT_VERSION_STR

    original_cwd = Path.cwd()
    original_path = list(sys.path)
    with tempfile.TemporaryDirectory(prefix="pyforestscan-qgis-smoke-") as directory:
        root = Path(directory)
        plugin_parent = root / "plugins"
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(plugin_parent)
        sys.path.insert(0, str(plugin_parent))
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(root / "settings"))
        app = QgsApplication([], False, str(root / "profile"))
        try:
            os.chdir(root)
            with patch.object(Path, "home", return_value=root):
                harness = runpy.run_path(str(ROOT / "scripts/qgis_ui_startup_smoke.py"))
                result = harness["run"]()
                from pyforestscan_qgis import classFactory
                from pyforestscan_qgis.core.build_identity import session_identity
                from pyforestscan_qgis.plugin import __file__ as plugin_file

                assert Path(plugin_file).is_relative_to(plugin_parent), plugin_file
                plugin = classFactory(harness["SmokeIface"]())
                try:
                    plugin.initGui()
                    assert plugin.provider is not None, "Processing provider registration failed"
                    result["algorithms"] = [
                        {
                            "id": algorithm.id(),
                            "parameters": len(algorithm.create().parameterDefinitions()),
                        }
                        for algorithm in plugin.provider.algorithms()
                    ]
                    assert len(result["algorithms"]) == 14, result["algorithms"]
                    assert plugin.mission_control_action is not None
                    plugin.mission_control_action.trigger()
                    app.processEvents()
                    result["engine_status"] = plugin.mission_control.application_availability.engine_status
                    result["installation_status"] = session_identity().status
                    result["build_id"] = session_identity().build_id
                finally:
                    plugin.unload()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                result["provider_unloaded"] = QgsApplication.processingRegistry().providerById("pyforestscan") is None
                result["qgis_version"] = Qgis.QGIS_VERSION
                result["qt_version"] = QT_VERSION_STR
                result["passed"] = bool(result["passed"] and result["provider_unloaded"] and result["installation_status"] == "PLUGIN_VALID")
                return result
        finally:
            os.chdir(original_cwd)
            sys.path[:] = original_path
            app.exitQgis()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_path", nargs="?", type=Path, default=ROOT / "dist/pyforestscan_qgis.zip")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/qgis-smoke.json")
    args = parser.parse_args()
    try:
        report = smoke_test(args.zip_path.resolve())
    except Exception as exc:
        report = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
        import traceback
        traceback.print_exc()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
