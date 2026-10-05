#!/usr/bin/env python3
"""Cleanly replace a QGIS-profile PyForestScan plugin from a packaged ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any


PLUGIN_NAME = "pyforestscan_qgis"
IGNORED_PARTS = {"__pycache__"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


def default_profiles_root() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "QGIS/QGIS3/profiles"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/QGIS/QGIS3/profiles"
    return Path.home() / ".local/share/QGIS/QGIS3/profiles"


def profile_plugin_root(profile: str, profiles_root: Path | None = None) -> Path:
    return (profiles_root or default_profiles_root()) / profile / "python/plugins" / PLUGIN_NAME


def install_plugin(zip_path: Path, destination: Path) -> dict[str, Any]:
    """Replace only the exact plugin directory and verify packaged build hashes."""
    zip_path = zip_path.resolve()
    destination = destination.resolve()
    if destination.name != PLUGIN_NAME:
        raise ValueError(f"Refusing to replace unexpected destination: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pyforestscan-qgis-install-") as folder:
        staging = Path(folder)
        with zipfile.ZipFile(zip_path) as archive:
            _safe_extract(archive, staging)
        staged_plugin = staging / PLUGIN_NAME
        if not (staged_plugin / "build_info.json").is_file():
            raise ValueError("Package does not contain pyforestscan_qgis/build_info.json.")
        verification = verify_installed_build(staged_plugin)
        if verification["status"] != "PLUGIN_VALID":
            raise ValueError(f"Package failed build-info verification: {verification['mismatches']}")
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(staged_plugin, destination)
    installed = verify_installed_build(destination)
    if installed["status"] != "PLUGIN_VALID":
        raise RuntimeError(f"Installed plugin failed verification: {installed['mismatches']}")
    return installed


def verify_installed_build(plugin_root: Path) -> dict[str, Any]:
    try:
        info = json.loads((plugin_root / "build_info.json").read_text(encoding="utf-8"))
        expected = dict(info["critical_module_hashes"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"status": "PLUGIN_CORRUPT", "plugin_root": str(plugin_root), "mismatches": [f"build_info.json: {exc}"]}
    actual = {
        name: _sha256(plugin_root / name)
        for name in expected
        if (plugin_root / name).is_file()
    }
    mismatches = sorted(name for name, digest in expected.items() if actual.get(name) != digest)
    return {
        "status": "PLUGIN_MIXED_INSTALL" if mismatches else "PLUGIN_VALID",
        "plugin_root": str(plugin_root.resolve()),
        "version": info.get("version", "unknown"),
        "git_commit": info.get("git_commit", "unknown"),
        "build_id": info.get("build_id", "unknown"),
        "package_identity": info.get("package_identity", "unknown"),
        "critical_module_hashes": actual,
        "mismatches": mismatches,
    }


def compare_zip_to_install(zip_path: Path, plugin_root: Path) -> dict[str, Any]:
    """Compare production files recursively, ignoring bytecode/runtime artifacts."""
    with zipfile.ZipFile(zip_path) as archive:
        packaged = {
            name[len(f"{PLUGIN_NAME}/"):]: hashlib.sha256(archive.read(name)).hexdigest()
            for name in archive.namelist()
            if name.startswith(f"{PLUGIN_NAME}/") and not name.endswith("/") and _included(Path(name))
        }
    installed = {
        path.relative_to(plugin_root).as_posix(): _sha256(path)
        for path in plugin_root.rglob("*")
        if path.is_file() and _included(path.relative_to(plugin_root))
    } if plugin_root.is_dir() else {}
    return {
        "zip": str(zip_path.resolve()),
        "plugin_root": str(plugin_root.resolve()),
        "missing_files": sorted(set(packaged) - set(installed)),
        "extra_files": sorted(set(installed) - set(packaged)),
        "differing_files": sorted(name for name in set(packaged) & set(installed) if packaged[name] != installed[name]),
        "packaged_build": _zip_build_info(zip_path),
        "installed_build": verify_installed_build(plugin_root) if plugin_root.is_dir() else {"status": "NOT_INSTALLED"},
    }


def write_deployment_comparison(zip_path: Path, default_root: Path, tested_root: Path | None, output: Path) -> dict[str, Any]:
    payload = {
        "package": str(zip_path.resolve()),
        "default_profile": compare_zip_to_install(zip_path, default_root),
        "tested_profile": None if tested_root is None else compare_zip_to_install(zip_path, tested_root),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _zip_build_info(zip_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(zip_path) as archive:
        return json.loads(archive.read(f"{PLUGIN_NAME}/build_info.json"))


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if root not in target.parents and target != root:
            raise ValueError(f"Unsafe ZIP member: {member.filename}")
    archive.extractall(destination)


def _included(path: Path) -> bool:
    return not (set(path.parts) & IGNORED_PARTS) and path.suffix not in IGNORED_SUFFIXES


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--profiles-root", type=Path)
    parser.add_argument("--tested-plugin-root", type=Path)
    parser.add_argument("--comparison-output", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    destination = profile_plugin_root(args.profile, args.profiles_root)
    if args.verify_only:
        result = compare_zip_to_install(args.zip, destination)
    else:
        print("Close QGIS before replacing plugin files. Restart QGIS after installation.")
        result = install_plugin(args.zip, destination)
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.comparison_output:
        write_deployment_comparison(args.zip, destination, args.tested_plugin_root, args.comparison_output)
        print(f"Deployment comparison: {args.comparison_output.resolve()}")
    return 0 if result.get("status", result.get("installed_build", {}).get("status")) in {"PLUGIN_VALID", None} else 1


if __name__ == "__main__":
    raise SystemExit(main())
