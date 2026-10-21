"""Validate the actual install ZIP; optionally start it with QGIS's plugin loader."""

import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
import zipfile


PLUGIN_ID = "pyforestscan_qgis"
REQUIRED_FILES = (
    "__init__.py",
    "metadata.txt",
    "LICENSE",
    "pyforestscan_plugin.py",
    "provider.py",
    "setup_dialog.py",
    "runtime/manager.py",
    "runtime/worker.py",
    "vendor/pyforestscan/LICENSE",
    "vendor/laspy/LICENSE.txt",
    "docs/index.html",
)


def check_archive(path):
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        names = [item.filename for item in members]
        roots = {name.split("/")[0] for name in names}
        if roots != {PLUGIN_ID}:
            raise ValueError(
                f"The ZIP must contain one top-level {PLUGIN_ID}/ folder; "
                f"found {sorted(roots)}. GitHub's automatic Source code ZIP "
                "is not a QGIS installer. Use the asset built by scripts/package_plugin.sh."
            )
        if len(names) != len(set(names)):
            raise ValueError("The ZIP contains duplicate paths.")
        for item in members:
            parts = PurePosixPath(item.filename).parts
            if (
                "\\" in item.filename
                or ".." in parts
                or stat.S_ISLNK(item.external_attr >> 16)
            ):
                raise ValueError(f"Unsafe archive path: {item.filename}")
            if "__pycache__" in parts or item.filename.endswith((".pyc", ".pyo")):
                raise ValueError(f"Bytecode must not be shipped: {item.filename}")
        for relative in REQUIRED_FILES:
            name = f"{PLUGIN_ID}/{relative}"
            if name not in names or not archive.read(name):
                raise ValueError(f"Missing or empty required file: {name}")
        damaged = archive.testzip()
        if damaged:
            raise ValueError(f"ZIP checksum failed: {damaged}")
        metadata = configparser.ConfigParser(interpolation=None)
        metadata.read_string(archive.read(f"{PLUGIN_ID}/metadata.txt").decode("utf-8"))
        version = metadata.get("general", "version")
        if not version.strip():
            raise ValueError("Plugin metadata has no version.")
        for item in members:
            if item.filename.endswith(".py"):
                compile(archive.read(item), item.filename, "exec")
    return {
        "archive": str(path),
        "package": PLUGIN_ID,
        "version": version,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def check_qgis(path):
    # Use a fresh profile and the real folder name from the ZIP. Loading source
    # via spec_from_file_location with a supplied alias conceals broken ZIP roots.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qgis import utils
    from qgis.core import Qgis, QgsApplication

    if QgsApplication.instance() is not None or PLUGIN_ID in sys.modules:
        raise RuntimeError("Run this check in a fresh QGIS Python process.")
    with tempfile.TemporaryDirectory(prefix="pyforestscan-zip-check-") as directory:
        profile = Path(directory) / "profile"
        plugins = profile / "python" / "plugins"
        plugins.mkdir(parents=True)
        with zipfile.ZipFile(path) as archive:
            archive.extractall(plugins)
        app = QgsApplication([], False, str(profile))
        app.initQgis()
        utils.plugin_paths.insert(0, str(plugins))
        sys.path.insert(0, str(plugins))
        try:
            utils.updateAvailablePlugins()
            if PLUGIN_ID not in utils.available_plugins:
                raise RuntimeError("QGIS did not discover the extracted plugin.")
            if not utils.loadPlugin(PLUGIN_ID) or not utils.startPlugin(PLUGIN_ID):
                raise RuntimeError("QGIS could not load and start the extracted plugin.")
            provider = app.processingRegistry().providerById("pyforestscan")
            if provider is None:
                raise RuntimeError("The PyForestScan Processing provider is missing.")
            algorithms = sorted(algorithm.id() for algorithm in provider.algorithms())
            for name in (
                "addheightaboveground",
                "canopyheightmodel",
                "canopycover",
                "checkdependencies",
            ):
                if f"pyforestscan:{name}" not in algorithms:
                    raise RuntimeError(f"Missing Processing algorithm: {name}")
            if not utils.unloadPlugin(PLUGIN_ID):
                raise RuntimeError("QGIS could not unload the plugin.")
            if app.processingRegistry().providerById("pyforestscan") is not None:
                raise RuntimeError("Unloading left the Processing provider registered.")
            return {
                "qgis": Qgis.QGIS_VERSION,
                "algorithms": algorithms,
                "load_unload": "passed",
            }
        finally:
            if PLUGIN_ID in utils.active_plugins:
                utils.unloadPlugin(PLUGIN_ID)
            utils.plugin_paths.remove(str(plugins))
            sys.path.remove(str(plugins))
            app.exitQgis()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument(
        "--qgis",
        action="store_true",
        help="Also load/unload using QGIS's Python interpreter.",
    )
    args = parser.parse_args()
    try:
        result = check_archive(args.archive)
        if args.qgis:
            result.update(check_qgis(args.archive))
    except (
        OSError,
        ValueError,
        configparser.Error,
        zipfile.BadZipFile,
        SyntaxError,
        RuntimeError,
        ImportError,
    ) as exc:
        parser.exit(1, f"Plugin ZIP check failed: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
