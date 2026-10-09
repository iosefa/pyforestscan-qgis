"""QGIS-independent adapters for the released PyForestScan metric API.

Scientific calculations belong to PyForestScan. This module validates inputs,
limits dense allocations, and carries each returned grid's own spatial extent.
CHM interpolation is deliberately disabled: PyForestScan 0.4.1 does not align
its interpolation coordinates with its (X, Y) array axes.
"""

from dataclasses import dataclass
import math
import os
import tempfile


# PAD calculations hold several float64 cubes concurrently. Five million
# voxels already require hundreds of MB, excluding the source point cloud.
MAX_VOXELS = 5_000_000
MAX_RASTER_CELLS = 5_000_000
MAX_RASTER_BANDS = 65_535
METRIC_NAMES = (
    "CHM",
    "PAD",
    "PAI",
    "FHD",
    "canopy cover",
    "point density",
    "rumple",
    "voxel statistic",
)


@dataclass(frozen=True)
class MetricResult:
    """An (X, north-to-south Y[, ascending HAG]) array and its edge bounds."""

    data: object
    extent: tuple


def _number(value, label, *, positive=False):
    value = float(value)
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        comparison = "greater than zero" if positive else "nonnegative"
        raise ValueError(f"{label} must be finite and {comparison}.")
    return value


def _check_canceled(feedback):
    if feedback is not None and feedback.isCanceled():
        raise ValueError("Processing was canceled.")


def _message(feedback, message):
    _check_canceled(feedback)
    if feedback is not None:
        feedback.pushInfo(message)


def _result(data, extent):
    return MetricResult(data, tuple(float(value) for value in extent))


def _grid_shape(points, xy, dz):
    """Estimate the core's globally aligned voxel allocation before creating it."""
    x0 = math.floor(float(points["X"].min()) / xy) * xy
    y0 = math.ceil(float(points["Y"].max()) / xy) * xy
    nx = max(1, math.ceil((float(points["X"].max()) - x0) / xy))
    ny = max(1, math.ceil((y0 - float(points["Y"].min())) / xy))
    nz = max(1, math.ceil(float(points["HeightAboveGround"].max()) / dz))
    return nx, ny, nz


def _check_grid(shape, limit, label):
    if math.prod(shape) > limit:
        raise ValueError(
            f"The requested {label} grid exceeds the {limit:,}-cell memory limit "
            f"(estimated shape {shape}). Increase cell sizes or crop the input."
        )


def _voxels(calculate, points, resolution, shape):
    """Use core column counts when its histogram returns a zero-length axis."""
    import numpy as np

    voxels, extent = calculate.assign_voxels(points, resolution)
    if all(voxels.shape):
        return voxels, extent

    # Core assign_voxels produces no height bins for ground-only data, or no
    # XY bins for a grid-aligned single coordinate. Its statistic API already
    # handles those cases, including points on the uppermost bin edge.
    counts, extent = calculate.calculate_voxel_stat(
        points, resolution, "HeightAboveGround", "count"
    )
    voxels = np.zeros((*counts.shape, shape[2]), dtype=float)
    # Skip unoccupied height bins so a high isolated return does not require
    # thousands of complete point-cloud passes through the statistic API.
    occupied_bins = np.unique(
        np.minimum(
            np.floor(points["HeightAboveGround"] / resolution[2]).astype(int),
            shape[2] - 1,
        )
    )
    for index in occupied_bins:
        counts, extent = calculate.calculate_voxel_stat(
            points,
            resolution,
            "HeightAboveGround",
            "count",
            z_index_range=(int(index), int(index) + 1),
        )
        voxels[:, :, index] = counts
    return voxels, extent


def compute_metrics(
    points,
    metric_names,
    xy_resolution=1,
    z_resolution=1,
    min_height=1,
    fhd_min_height=0,
    cover_min_height=2,
    cover_k=0.5,
    feedback=None,
    voxel_dimension="HeightAboveGround",
    voxel_stat="mean",
):
    """Calculate requested metrics using PyForestScan 0.4.1.

    Return ``MetricResult`` objects, except ``rumple``, which is a float.
    Coordinates and HAG must use the same linear units (normally metres).
    Nonfinite coordinates/HAG and negative HAG are excluded consistently.
    Dense grids are limited to five million cells/voxels before allocation.
    """
    import numpy as np
    from ..vendor.pyforestscan import calculate

    names = tuple(dict.fromkeys(metric_names))
    unknown = set(names).difference(METRIC_NAMES)
    if unknown or not names:
        raise ValueError(f"Select supported metrics; unknown names: {sorted(unknown)}.")
    xy = _number(xy_resolution, "XY resolution", positive=True)
    dz = _number(z_resolution, "Height bin size", positive=True)
    min_height = _number(min_height, "Minimum PAI height")
    fhd_min_height = _number(fhd_min_height, "Minimum FHD height")
    cover_min_height = _number(cover_min_height, "Minimum canopy cover height")
    cover_k = _number(cover_k, "Canopy cover extinction coefficient")
    fields = set(getattr(getattr(points, "dtype", None), "names", None) or ())
    required = {"X", "Y", "HeightAboveGround"}
    missing = required.difference(fields)
    if missing:
        raise ValueError(
            f"Point cloud is missing dimensions: {', '.join(sorted(missing))}."
        )
    if points.ndim != 1 or points.size == 0:
        raise ValueError(
            "Point cloud must be a nonempty one-dimensional structured array."
        )
    valid = (
        np.isfinite(points["X"])
        & np.isfinite(points["Y"])
        & np.isfinite(points["HeightAboveGround"])
        & (points["HeightAboveGround"] >= 0)
    )
    excluded = int(np.count_nonzero(~valid))
    if excluded:
        _message(
            feedback,
            f"Excluded {excluded:,} points with invalid coordinates or negative/nonfinite HAG.",
        )
    points = points[valid]
    if not points.size:
        raise ValueError(
            "No points with finite coordinates and nonnegative HeightAboveGround remain."
        )
    if "voxel statistic" in names:
        if voxel_dimension not in fields:
            raise ValueError(f"Point cloud is missing dimension: {voxel_dimension}.")
        if not np.isfinite(points[voxel_dimension]).all():
            raise ValueError(f"Dimension {voxel_dimension} contains nonfinite values.")

    resolution = (xy, xy, dz)
    try:
        shape = _grid_shape(points, xy, dz)
    except OverflowError as exc:
        raise ValueError(
            "Coordinate ranges or cell sizes exceed the safe grid allocation limits."
        ) from exc
    _check_grid(shape[:2], MAX_RASTER_CELLS, "raster")
    degenerate_xy = np.ptp(points["X"]) == 0 or np.ptp(points["Y"]) == 0
    if "voxel statistic" in names or (
        degenerate_xy and {"CHM", "rumple"}.intersection(names)
    ):
        # Even a 2D column statistic constructs the complete 1D height-edge
        # array. A tall outlier or microscopic dz can otherwise allocate GB.
        _check_grid((shape[2] + 1,), MAX_VOXELS, "height-bin")
    voxel_metrics = {"PAD", "PAI", "FHD", "canopy cover", "point density"}
    needs_voxels = bool(voxel_metrics.intersection(names))
    if needs_voxels:
        # histogramdd also allocates overflow bins along each axis.
        _check_grid(tuple(size + 2 for size in shape), MAX_VOXELS, "voxel")

    results = {}
    chm = None
    if {"CHM", "rumple"}.intersection(names):
        _message(feedback, "Calculating canopy height with PyForestScan.")
        if degenerate_xy:
            chm, extent = calculate.calculate_voxel_stat(
                points, resolution, "HeightAboveGround", "max"
            )
        else:
            chm, extent = calculate.calculate_chm(
                points, resolution, interpolation=None
            )
        if "CHM" in names:
            results["CHM"] = _result(chm, extent)
        if "rumple" in names:
            _check_canceled(feedback)
            results["rumple"] = float(calculate.calculate_rumple(chm, (xy, xy)))

    if needs_voxels:
        _message(feedback, "Assigning forest structure voxels with PyForestScan.")
        voxels, extent = _voxels(calculate, points, resolution, shape)
        occupied = np.sum(voxels, axis=2) > 0
        pad = None
        if {"PAD", "PAI", "canopy cover"}.intersection(names):
            pad = calculate.calculate_pad(voxels, voxel_height=dz)
            if "PAD" in names:
                results["PAD"] = _result(pad, extent)
        if "PAI" in names:
            _message(feedback, "Calculating plant area index with PyForestScan.")
            pai = calculate.calculate_pai(pad, dz, min_height=min_height)
            pai[~occupied] = np.nan
            results["PAI"] = _result(pai, extent)
        if "FHD" in names:
            _message(
                feedback, "Calculating foliage height diversity with PyForestScan."
            )
            fhd = calculate.calculate_fhd(
                voxels, voxel_height=dz, min_height=fhd_min_height
            )
            results["FHD"] = _result(fhd, extent)
        if "canopy cover" in names:
            _message(feedback, "Calculating canopy cover with PyForestScan.")
            cover = calculate.calculate_canopy_cover(
                pad, dz, min_height=cover_min_height, k=cover_k
            )
            cover[~occupied] = np.nan
            results["canopy cover"] = _result(cover, extent)
        if "point density" in names:
            density = calculate.calculate_point_density(
                voxels, per_area=True, cell_area=xy * xy
            )
            results["point density"] = _result(density, extent)

    if "voxel statistic" in names:
        _message(
            feedback, f"Calculating {voxel_dimension} {voxel_stat} with PyForestScan."
        )
        values, extent = calculate.calculate_voxel_stat(
            points, resolution, voxel_dimension, voxel_stat
        )
        results["voxel statistic"] = _result(values, extent)
    _check_canceled(feedback)
    return {name: results[name] for name in names}


def write_metric_raster(result, path, crs, z_resolution=1):
    """Atomically write a 2D metric or a PAD cube as a float32 GeoTIFF.

    PAD band 1 is the lowest HAG interval. Values use NaN NoData; the array's
    X,Y axes are transposed to GDAL's row,column order for every band.
    """
    import numpy as np
    from osgeo import gdal, osr

    data = np.asarray(result.data)
    if data.ndim not in (2, 3) or not all(data.shape):
        raise ValueError("Raster data must be a nonempty 2D grid or 3D PAD cube.")
    count = data.shape[2] if data.ndim == 3 else 1
    if count > MAX_RASTER_BANDS:
        raise ValueError(
            f"PAD needs {count:,} bands, exceeding GeoTIFF's {MAX_RASTER_BANDS:,}-band "
            "limit. Increase the height bin size."
        )
    xmin, xmax, ymin, ymax = map(float, result.extent)
    if (
        not all(math.isfinite(v) for v in (xmin, xmax, ymin, ymax))
        or xmax <= xmin
        or ymax <= ymin
    ):
        raise ValueError(f"Invalid raster extent: {result.extent}.")
    dz = _number(z_resolution, "Height bin size", positive=True)
    reference = osr.SpatialReference()
    if reference.SetFromUserInput(str(crs)) != 0:
        raise ValueError("The output raster CRS is invalid.")
    output = os.path.abspath(os.fspath(path))
    os.makedirs(os.path.dirname(output), exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".pyforestscan-", suffix=".tif", dir=os.path.dirname(output)
    )
    os.close(fd)
    raster = None
    try:
        raster = gdal.GetDriverByName("GTiff").Create(
            temporary,
            data.shape[0],
            data.shape[1],
            count,
            gdal.GDT_Float32,
            options=["COMPRESS=DEFLATE", "PREDICTOR=3"],
        )
        if raster is None:
            raise RuntimeError("Could not create the output GeoTIFF.")
        raster.SetGeoTransform(
            (
                xmin,
                (xmax - xmin) / data.shape[0],
                0,
                ymax,
                0,
                -(ymax - ymin) / data.shape[1],
            )
        )
        raster.SetProjection(reference.ExportToWkt())
        if data.ndim == 3:
            raster.SetMetadata(
                {
                    "metric": "Plant Area Density",
                    "array_axes": "X,Y,HeightAboveGround",
                    "height_origin": "0",
                    "height_bin_size": str(dz),
                    "height_units": "m",
                    "pad_units": "m2/m3",
                    "ground_band": "masked by PyForestScan",
                }
            )
        for index in range(count):
            values = data[:, :, index] if data.ndim == 3 else data
            with np.errstate(over="ignore", invalid="ignore"):
                values = np.asarray(values.T, dtype="float32").copy()
            values[~np.isfinite(values)] = np.nan
            band = raster.GetRasterBand(index + 1)
            band.SetNoDataValue(float("nan"))
            if band.WriteArray(values) != 0:
                raise RuntimeError("Could not write the output raster band.")
            if data.ndim == 3:
                lower, upper = index * dz, (index + 1) * dz
                band.SetDescription(f"PAD: HeightAboveGround {lower:g}–{upper:g} m")
                band.SetMetadata(
                    {
                        "height_min": str(lower),
                        "height_max": str(upper),
                        "height_units": "m",
                        "units": "m2/m3",
                    }
                )
            band = None
        if raster.FlushCache() not in (None, 0):
            raise RuntimeError("Could not flush the output GeoTIFF.")
        raster = None
        os.replace(temporary, output)
    finally:
        raster = None
        if os.path.exists(temporary):
            os.unlink(temporary)
    return output
