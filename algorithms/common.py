"""Runtime selection and Processing output helpers (no metric implementations)."""

import importlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..vendor import PYFORESTSCAN_VERSION

SUPPORTED_PYFORESTSCAN = PYFORESTSCAN_VERSION


@dataclass
class DependencyResult:
    module: str
    label: str
    ok: bool
    version: str = ""
    error: str = ""


def _version_tuple(value):
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", value)
    return tuple(map(int, match.groups())) if match else ()


def _native_results(kind="metrics", hag="none", outliers=False):
    # Keep compiled dependencies supplied by QGIS. Never alter its sys.path or
    # install/upgrade packages inside the application interpreter.
    results = []
    modules = [
        ("numpy", "NumPy", (2, 3, 1)),
        ("osgeo.gdal", "GDAL Python bindings", ()),
    ]
    if kind != "hag":
        modules.append(("scipy", "SciPy", (1, 16, 0)))
    for module, label, minimum in modules:
        try:
            imported = importlib.import_module(module)
            value = str(getattr(imported, "__version__", ""))
            if minimum and _version_tuple(value) < minimum:
                raise ImportError(
                    f"Found {value or 'unknown'}; need {'.'.join(map(str, minimum))} or later."
                )
            results.append(DependencyResult(module, label, True, value))
        except Exception as exc:
            results.append(DependencyResult(module, label, False, error=str(exc)))
    if kind != "rumple":
        from qgis.core import Qgis, QgsApplication

        if Qgis.QGIS_VERSION_INT < 40200:
            results.append(
                DependencyResult(
                    "native-point-cloud-version",
                    "Native point-cloud export",
                    False,
                    error="Native processing requires QGIS 4.2 or later to preserve point attributes reliably. Use the isolated engine on older versions.",
                )
            )
        registry = QgsApplication.processingRegistry()
        if registry.providerById("pdal") is None:
            try:
                from qgis.analysis import QgsPdalAlgorithms

                registry.addProvider(QgsPdalAlgorithms())
            except (ImportError, RuntimeError):
                pass
        ids = ["pdal:convertformat", "pdal:filter", "pdal:clip"]
        if hag == "delaunay":
            ids.append("pdal:heightabovegroundtriangulation")
        for algorithm_id in ids:
            present = registry.algorithmById(algorithm_id) is not None
            results.append(
                DependencyResult(
                    algorithm_id,
                    algorithm_id,
                    present,
                    error="Not provided by this QGIS installation."
                    if not present
                    else "",
                )
            )
        if outliers:
            results.append(
                DependencyResult(
                    "tiled-outliers",
                    "Tiled statistical outlier cleaning",
                    False,
                    error="This advanced option uses the isolated processing engine.",
                )
            )
    results.append(
        DependencyResult(
            "bundled.pyforestscan", "Bundled PyForestScan", True, PYFORESTSCAN_VERSION
        )
    )
    return results


def check_dependencies(include_runtime=True):
    """Report the native metric workflow; other features are checked when selected."""
    return _native_results(hag="delaunay" if include_runtime else "none")


def dependency_report(results):
    return "\n".join(
        f"OK: {item.label} [{item.module}]"
        + (f" ({item.version})" if item.version else "")
        if item.ok
        else f"FAIL: {item.label} [{item.module}] - {item.error}"
        for item in results
    )


def ensure_backend(kind, parameters, context, algorithm, feedback):
    """Choose a capable native installation, then an explicitly installed engine."""
    from qgis.core import QgsProcessingException, QgsSettings
    from ..runtime.manager import status

    hag = (
        ["delaunay", "dtm"][
            algorithm.parameterAsEnum(parameters, "HAG_METHOD", context)
        ]
        if kind == "hag"
        else "none"
    )
    outliers = kind == "tiles" and algorithm.parameterAsBool(
        parameters, "REMOVE_OUTLIERS", context
    )
    forced = os.environ.get(
        "PYFORESTSCAN_BACKEND",
        QgsSettings().value("pyforestscan/backend", "auto", type=str),
    )
    if forced not in ("auto", "native", "engine"):
        raise QgsProcessingException(
            "PYFORESTSCAN_BACKEND must be auto, native, or engine."
        )
    results = _native_results(kind, hag, outliers) if forced != "engine" else []
    if forced != "engine" and all(item.ok for item in results):
        feedback.pushInfo(
            "Using QGIS native point-cloud tools and bundled PyForestScan calculations."
        )
        return "native"
    engine = status()
    if forced != "native" and engine.get("installed"):
        feedback.pushInfo("Using the isolated PyForestScan processing engine.")
        return "engine"
    reason = dependency_report([item for item in results if not item.ok])
    raise QgsProcessingException(
        "This workflow needs the isolated PyForestScan engine on this installation. "
        "Open Plugins → PyForestScan → Set up PyForestScan to install or repair it. "
        "Your QGIS Python environment will not be changed.\n"
        + reason
        + ("\n" + str(engine["error"]) if engine.get("error") else "")
    )


def help_url(anchor):
    return (
        (Path(__file__).resolve().parents[1] / "docs" / "index.html").as_uri()
        + "#"
        + anchor
    )


def require_metric_crs(crs):
    from qgis.core import Qgis, QgsProcessingException

    horizontal = crs.horizontalCrs() if hasattr(crs, "horizontalCrs") else crs
    if not horizontal.isValid():
        horizontal = crs
    if (
        not crs.isValid()
        or horizontal.isGeographic()
        or horizontal.mapUnits() != Qgis.DistanceUnit.Meters
    ):
        raise QgsProcessingException(
            "Forest metrics require a projected CRS in metres and Z/HeightAboveGround in metres. "
            "Reproject the point cloud with QGIS first; changing the project CRS does not reproject points."
        )
    vertical = crs.verticalCrs() if hasattr(crs, "verticalCrs") else None
    if (
        vertical is not None
        and vertical.isValid()
        and vertical.mapUnits() != Qgis.DistanceUnit.Meters
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
