"""Dependency diagnostics and Processing output helpers (no metric implementations)."""

import importlib
import math
import os
from importlib.metadata import PackageNotFoundError, version
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_PYFORESTSCAN = "0.4.1"
REQUIRED_MODULES = [
    ("pyforestscan", "PyForestScan"),
    ("pdal", "PDAL Python bindings"),
    ("rasterio", "rasterio"),
    ("geopandas", "geopandas"),
    ("numpy", "NumPy"),
    ("scipy", "SciPy"),
    ("pyproj", "pyproj"),
    ("shapely", "Shapely"),
    ("pandas", "pandas"),
    ("requests", "requests"),
    ("matplotlib", "matplotlib"),
    ("tqdm", "tqdm"),
]
RUNTIME_MODULES = [
    ("pyforestscan.handlers", "PyForestScan handlers"),
    ("pyforestscan.calculate", "PyForestScan metrics"),
    ("pyforestscan.filters", "PyForestScan filters"),
    ("pyforestscan.process", "PyForestScan tiled processing"),
]


@dataclass
class DependencyResult:
    module: str
    label: str
    ok: bool
    version: str = ""
    error: str = ""


def check_dependencies(include_runtime=True):
    results = []
    for module, label in REQUIRED_MODULES + (
        RUNTIME_MODULES if include_runtime else []
    ):
        try:
            imported = importlib.import_module(module)
            module_version = str(getattr(imported, "__version__", ""))
            if module == "pyforestscan":
                try:
                    module_version = version("pyforestscan")
                except PackageNotFoundError:
                    module_version = "unknown"
                if module_version != SUPPORTED_PYFORESTSCAN:
                    raise ImportError(
                        f"Found PyForestScan {module_version}; this release requires "
                        f"{SUPPORTED_PYFORESTSCAN}. Install the supplied environment.yml."
                    )
            results.append(DependencyResult(module, label, True, module_version))
        except Exception as exc:
            results.append(
                DependencyResult(
                    module,
                    label,
                    False,
                    error=f"{type(exc).__name__}: {exc}".replace("\n", " "),
                )
            )
    return results


def dependency_report(results):
    lines = []
    for result in results:
        if result.ok:
            suffix = f" ({result.version})" if result.version else ""
            lines.append(f"OK: {result.label} [{result.module}]{suffix}")
        else:
            lines.append(f"FAIL: {result.label} [{result.module}] - {result.error}")
    return "\n".join(lines)


def raise_for_missing_dependencies(qgs_exception_cls):
    results = check_dependencies()
    if any(not result.ok for result in results):
        raise qgs_exception_cls(
            "PyForestScan runtime dependencies are missing or incompatible in QGIS's Python. "
            "Run Check PyForestScan Dependencies and follow the bundled installation guide.\n\n"
            + dependency_report(results)
        )


def help_url(anchor):
    return (
        (Path(__file__).resolve().parents[1] / "docs" / "index.html").as_uri()
        + "#"
        + anchor
    )


def require_metric_crs(crs):
    from qgis.core import Qgis, QgsProcessingException
    from pyproj import CRS

    if (
        not crs.isValid()
        or crs.isGeographic()
        or crs.mapUnits() != Qgis.DistanceUnit.Meters
    ):
        raise QgsProcessingException(
            "Forest metrics require a projected CRS in metres and Z/HeightAboveGround in metres. "
            "Reproject the point cloud with QGIS first; changing the project CRS does not reproject points."
        )
    for axis in CRS.from_wkt(crs.toWkt()).axis_info:
        if axis.direction in ("up", "down") and not math.isclose(
            axis.unit_conversion_factor, 1.0
        ):
            raise QgsProcessingException(
                "The point cloud's declared vertical CRS is not in metres. Convert vertical units before calculating forest metrics."
            )


def protect_input_paths(outputs, inputs):
    from qgis.core import QgsProcessingException

    for output in outputs:
        for source in filter(None, inputs):
            same_path = os.path.realpath(output) == os.path.realpath(source)
            same_file = (
                os.path.isfile(output)
                and os.path.isfile(source)
                and os.path.samefile(output, source)
            )
            if same_path or same_file:
                raise QgsProcessingException(
                    "An output cannot replace a source point cloud or terrain raster. Choose a different destination."
                )


def check_canceled(feedback):
    if feedback.isCanceled():
        from qgis.core import QgsProcessingException

        raise QgsProcessingException("Processing canceled.")


def ensure_output_folder(path):
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


def add_raster_to_load_on_completion(context, path, name, output_name):
    from qgis.core import QgsProcessingContext, QgsProcessingUtils

    if context.project() is not None:
        details = QgsProcessingContext.LayerDetails(
            name,
            context.project(),
            output_name,
            QgsProcessingUtils.LayerHint.Raster,
        )
        context.addLayerToLoadOnCompletion(path, details)
