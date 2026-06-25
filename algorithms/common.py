import importlib
import math
import os
from dataclasses import dataclass


REQUIRED_MODULES = [
    ("pyforestscan", "PyForestScan"),
    ("pdal", "PDAL Python bindings"),
    ("rasterio", "rasterio"),
    ("geopandas", "geopandas"),
    ("numpy", "NumPy"),
    ("scipy", "SciPy"),
    ("pyproj", "pyproj"),
    ("shapely", "Shapely"),
]

RUNTIME_MODULES = [
    ("pyforestscan.handlers", "PyForestScan handlers"),
    ("pyforestscan.calculate", "PyForestScan calculate"),
    ("pyforestscan.process", "PyForestScan tiled processing"),
]


@dataclass
class DependencyResult:
    module: str
    label: str
    ok: bool
    version: str = ""
    error: str = ""


def _one_line_error(exc):
    return f"{type(exc).__name__}: {exc}".replace("\n", " ")


def check_dependencies(include_runtime=True):
    results = []
    for module, label in REQUIRED_MODULES + (RUNTIME_MODULES if include_runtime else []):
        try:
            imported = importlib.import_module(module)
        except Exception as exc:
            results.append(
                DependencyResult(
                    module=module,
                    label=label,
                    ok=False,
                    error=_one_line_error(exc),
                )
            )
            continue

        version = getattr(imported, "__version__", "")
        results.append(
            DependencyResult(
                module=module,
                label=label,
                ok=True,
                version=str(version) if version else "",
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
    results = check_dependencies(include_runtime=True)
    failures = [result for result in results if not result.ok]
    if failures:
        raise qgs_exception_cls(
            "PyForestScan dependencies are not available in this Python environment.\n\n"
            + dependency_report(results)
        )


def import_metric_runtime():
    handlers = importlib.import_module("pyforestscan.handlers")
    calculate = importlib.import_module("pyforestscan.calculate")
    return handlers, calculate


def import_tiled_runtime():
    process = importlib.import_module("pyforestscan.process")
    return process


def array_names(points):
    return set(points.dtype.names or ())


def require_height_above_ground(points):
    if "HeightAboveGround" not in array_names(points):
        raise ValueError(
            "Selected metrics require a HeightAboveGround dimension. "
            "Choose Delaunay or DTM HAG, or use a point cloud that already contains HeightAboveGround."
        )


def first_point_array(arrays):
    if arrays is None:
        return None
    if isinstance(arrays, (list, tuple)):
        if not arrays:
            return None
        return arrays[0]
    return arrays


def qgis_crs_to_string(crs):
    if crs is None or not crs.isValid():
        return None
    authid = crs.authid()
    if authid:
        return authid
    return crs.toWkt()


def extent_to_ept_bounds(rectangle):
    if rectangle is None or rectangle.isEmpty():
        return None
    return ([rectangle.xMinimum(), rectangle.xMaximum()], [rectangle.yMinimum(), rectangle.yMaximum()])


def interpolation_value(label):
    if label == "none":
        return None
    return label


def safe_filename(label):
    return label.lower().replace(" ", "_").replace("/", "_")


def raster_layer_path(layer):
    if layer is None:
        return None
    return layer.source().split("|", 1)[0]


def ensure_output_folder(path):
    os.makedirs(path, exist_ok=True)
    return path


def calculate_point_density(points, xy_resolution):
    import numpy as np

    x = points["X"]
    y = points["Y"]
    if x.size == 0 or y.size == 0:
        raise ValueError("Point cloud data contains no points.")

    x_min, x_max = float(x.min()), float(x.max())
    y_min, y_max = float(y.min()), float(y.max())
    x_bins = np.arange(x_min, x_max + xy_resolution, xy_resolution)
    y_bins = np.arange(y_min, y_max + xy_resolution, xy_resolution)
    counts, _, _ = np.histogram2d(x, y, bins=(x_bins, y_bins))
    density = counts[:, ::-1] / float(xy_resolution * xy_resolution)
    return density, [x_min, x_max, y_min, y_max]


def calculate_canopy_cover(points, xy_resolution, min_height=2.0, k=1.0):
    import numpy as np

    require_height_above_ground(points)
    x = points["X"]
    y = points["Y"]
    z = points["HeightAboveGround"]
    if x.size == 0 or y.size == 0 or z.size == 0:
        raise ValueError("Point cloud data contains no points.")

    x_min, x_max = float(x.min()), float(x.max())
    y_min, y_max = float(y.min()), float(y.max())
    x_bins = np.arange(x_min, x_max + xy_resolution, xy_resolution)
    y_bins = np.arange(y_min, y_max + xy_resolution, xy_resolution)
    total, _, _ = np.histogram2d(x, y, bins=(x_bins, y_bins))
    above, _, _ = np.histogram2d(x[z >= min_height], y[z >= min_height], bins=(x_bins, y_bins))

    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.divide(above, total, out=np.full_like(total, np.nan, dtype=float), where=total > 0)

    if k and k > 0 and not math.isclose(k, 1.0):
        gap_fraction = 1.0 - ratio
        cover = 1.0 - np.power(gap_fraction, k)
    else:
        cover = ratio

    return cover[:, ::-1], [x_min, x_max, y_min, y_max]


def calculate_rumple(chm, xy_resolution):
    import numpy as np

    if chm.shape[0] < 2 or chm.shape[1] < 2:
        return float("nan")

    def triangle_area(a, b, c):
        return 0.5 * np.linalg.norm(np.cross(b - a, c - a))

    surface_area = 0.0
    ground_area = 0.0
    cell_area = float(xy_resolution * xy_resolution)

    for i in range(chm.shape[0] - 1):
        for j in range(chm.shape[1] - 1):
            z00 = chm[i, j]
            z10 = chm[i + 1, j]
            z01 = chm[i, j + 1]
            z11 = chm[i + 1, j + 1]
            if not np.all(np.isfinite([z00, z10, z01, z11])):
                continue

            p00 = np.array([0.0, 0.0, z00], dtype=float)
            p10 = np.array([xy_resolution, 0.0, z10], dtype=float)
            p01 = np.array([0.0, xy_resolution, z01], dtype=float)
            p11 = np.array([xy_resolution, xy_resolution, z11], dtype=float)
            surface_area += triangle_area(p00, p10, p11)
            surface_area += triangle_area(p00, p11, p01)
            ground_area += cell_area

    if ground_area == 0:
        return float("nan")
    return surface_area / ground_area


def add_raster_to_load_on_completion(context, path, name, output_name):
    try:
        from qgis.core import QgsProcessingContext

        context.addLayerToLoadOnCompletion(
            path,
            QgsProcessingContext.LayerDetails(name, context.project(), output_name),
        )
    except Exception:
        pass
