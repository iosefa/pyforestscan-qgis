"""File-based scientific jobs for the optional isolated processing runtime.

This module imports no QGIS classes. Prepared inputs contain saved source paths,
CRS WKT, already transformed crop geometries, and explicit PDAL reader stages.
"""

import csv
import json
import math
import os
import shutil
import sys
import tempfile
from urllib.request import urlopen

from ..algorithms.metric_engine import (
    MAX_RASTER_CELLS,
    MetricResult,
    compute_metrics,
    write_metric_raster,
)


READERS = {"readers.las", "readers.copc", "readers.ept"}
FILTERS = {
    "filters.crop",
    "filters.hag_delaunay",
    "filters.hag_dem",
    "filters.outlier",
    "filters.expression",
}


def _reader(reader):
    if (
        not isinstance(reader, dict)
        or reader.get("type") not in READERS
        or not reader.get("filename")
    ):
        raise ValueError("Use a LAS, LAZ, COPC or EPT point cloud reader.")
    return dict(reader)


def metadata(payload, feedback=None):
    import pdal

    result = pdal.Pipeline(json.dumps([_reader(payload["reader"])])).quickinfo
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict) or not result:
        raise ValueError("No metadata was returned for the point cloud.")
    return next(iter(result.values()))


def _crs(wkt, metres=False):
    from pyproj import CRS

    crs = CRS.from_user_input(wkt)
    if not crs.is_projected:
        raise ValueError(
            "Use a projected point cloud CRS; assigning a CRS does not transform coordinates."
        )
    if metres and any(
        not math.isclose(axis.unit_conversion_factor, 1.0) for axis in crs.axis_info
    ):
        raise ValueError(
            "Forest metrics require horizontal and vertical coordinates in metres."
        )
    return crs


def _prepared(value, metres=False):
    if not isinstance(value, dict):
        raise ValueError("Prepared point cloud input must be an object.")
    prepared = dict(value)
    prepared["reader"] = _reader(prepared["reader"])
    if prepared.get("path") != prepared["reader"]["filename"]:
        raise ValueError("The prepared input path differs from the PDAL reader source.")
    prepared["filters"] = [dict(stage) for stage in prepared.get("filters", [])]
    if any(stage.get("type") not in FILTERS for stage in prepared["filters"]):
        raise ValueError("The prepared input contains an unsupported processing stage.")
    if prepared.get("hag_method", "none") not in ("none", "delaunay", "dtm"):
        raise ValueError("Unknown height above ground method.")
    _crs(prepared["crs_wkt"], metres=metres)
    return prepared


def _protect(outputs, inputs):
    for output in outputs:
        for source in filter(None, inputs):
            same = os.path.realpath(output) == os.path.realpath(source)
            if not same and os.path.isfile(output) and os.path.isfile(source):
                same = os.path.samefile(output, source)
            if same:
                raise ValueError(
                    "An output cannot replace a source point cloud or terrain raster."
                )


def _stages(prepared):
    return [dict(prepared["reader"]), *(dict(stage) for stage in prepared["filters"])]


def _terrain_valid(points, path, cloud_crs):
    """Validate real point samples using bounded GDAL block reads."""
    import numpy as np
    from osgeo import gdal
    from pyproj import CRS

    dataset = gdal.Open(os.fspath(path), gdal.GA_ReadOnly)
    if dataset is None or dataset.RasterCount < 1:
        raise ValueError("Choose a readable local terrain raster with a band 1.")
    try:
        _crs(dataset.GetProjectionRef(), metres=True)
        if not dataset.GetProjectionRef() or not CRS.from_wkt(
            dataset.GetProjectionRef()
        ).to_2d().equals(
            CRS.from_user_input(cloud_crs).to_2d(), ignore_axis_order=True
        ):
            raise ValueError(
                "The terrain raster's stored CRS must match the point cloud CRS."
            )
        inverse = gdal.InvGeoTransform(dataset.GetGeoTransform())
        if inverse is None:
            raise ValueError("The terrain raster georeferencing cannot be inverted.")
        band = dataset.GetRasterBand(1)
        unit = (band.GetUnitType() or "").strip().lower()
        if unit and unit not in ("m", "metre", "meter", "metres", "meters"):
            raise ValueError("Terrain raster band 1 elevations must be in metres.")
        mask = band.GetMaskBand()
        nodata = band.GetNoDataValue()
        # Fixed small windows prevent striped rasters from requesting huge buffers.
        size = 256
        for start in range(0, points.size, 65536):
            chunk = points[start : start + 65536]
            columns = np.floor(
                inverse[0] + inverse[1] * chunk["X"] + inverse[2] * chunk["Y"]
            ).astype(np.int64)
            rows = np.floor(
                inverse[3] + inverse[4] * chunk["X"] + inverse[5] * chunk["Y"]
            ).astype(np.int64)
            if (
                (rows < 0)
                | (columns < 0)
                | (rows >= dataset.RasterYSize)
                | (columns >= dataset.RasterXSize)
            ).any():
                raise ValueError(
                    "The terrain raster must cover every processed point with valid band 1 elevations."
                )
            keys = np.column_stack((columns // size, rows // size))
            for block in np.unique(keys, axis=0):
                selected = np.all(keys == block, axis=1)
                left, top = int(block[0]) * size, int(block[1]) * size
                width, height = (
                    min(size, dataset.RasterXSize - left),
                    min(size, dataset.RasterYSize - top),
                )
                values = band.ReadAsArray(left, top, width, height)[
                    rows[selected] - top, columns[selected] - left
                ]
                valid = (
                    mask.ReadAsArray(left, top, width, height)[
                        rows[selected] - top, columns[selected] - left
                    ]
                    != 0
                )
                valid &= np.isfinite(values)
                if nodata is not None:
                    valid &= values != nodata
                if not valid.all():
                    raise ValueError(
                        "The terrain raster must cover every processed point with valid band 1 elevations."
                    )
    finally:
        dataset = None


def validate_points(points, prepared):
    import numpy as np

    if points.size == 0:
        raise ValueError("No points were returned for the selected source and crop.")
    fields = set(points.dtype.names or ())
    if not {"X", "Y", "Z"}.issubset(fields) or not all(
        np.isfinite(points[name]).all() for name in ("X", "Y", "Z")
    ):
        raise ValueError("The point cloud contains missing or nonfinite coordinates.")
    method = prepared.get("hag_method", "none")
    if method != "none":
        if "HeightAboveGround" not in fields:
            raise ValueError("HeightAboveGround was not produced by PDAL.")
        if (
            not np.isfinite(points["HeightAboveGround"]).all()
            or (points["HeightAboveGround"] <= -1e29).any()
        ):
            raise ValueError(
                "HeightAboveGround contains invalid heights; check terrain coverage and NoData."
            )
    if method == "delaunay":
        if "Classification" not in fields:
            raise ValueError("Delaunay height requires ground points classified as 2.")
        ground = points[points["Classification"] == 2]
        if ground.size < 3:
            raise ValueError(
                "Delaunay height requires at least three ground points classified as 2."
            )
        xy = np.column_stack((ground["X"], ground["Y"]))
        if np.linalg.matrix_rank(xy - xy[0]) < 2:
            raise ValueError(
                "Delaunay height requires ground points that are not all on one line."
            )
    if prepared.get("dtm_path"):
        _terrain_valid(points, prepared["dtm_path"], prepared["crs_wkt"])


def _points(arrays, prepared, allow_empty=False):
    import numpy as np

    if not arrays:
        if allow_empty:
            return np.empty(0, dtype=[])
        raise ValueError("No points were returned for the selected source and crop.")
    points = arrays[0] if len(arrays) == 1 else np.concatenate(arrays)
    if points.size or not allow_empty:
        validate_points(points, prepared)
    return points


def read_points(prepared, feedback, allow_empty=False):
    import pdal

    feedback.pushInfo(
        "Reading full-resolution point cloud data in the isolated runtime."
    )
    pipeline = pdal.Pipeline(json.dumps(_stages(prepared)))
    pipeline.execute()
    return _points(pipeline.arrays, prepared, allow_empty)


def _temporary(output, prefix, suffix):
    directory = os.path.dirname(os.path.abspath(output))
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=prefix, suffix=suffix, dir=directory
    )
    os.close(descriptor)
    return temporary


def _write_rumple(path, value):
    temporary = _temporary(path, ".pyforestscan-rumple-", ".csv")
    try:
        with open(temporary, "w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["metric", "value"])
            writer.writerow(["rumple", value])
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def hag(payload, feedback):
    import pdal

    prepared = _prepared(payload["prepared"], metres=True)
    if prepared.get("hag_method", "none") == "none":
        raise ValueError("Choose a height above ground method.")
    output = os.fspath(payload["output"])
    lower = output.lower()
    if not lower.endswith((".las", ".laz")) or lower.endswith(".copc.laz"):
        raise ValueError("Choose a LAS or LAZ output; COPC output is not supported.")
    _protect([output], [prepared["path"], prepared.get("dtm_path")])
    writer = {
        "type": "writers.las",
        "minor_version": 4,
        "forward": "all",
        "extra_dims": "all",
        "compression": lower.endswith(".laz"),
        "a_srs": prepared["crs_wkt"],
    }
    if prepared["reader"]["type"] == "readers.ept":
        writer.update({f"scale_{axis}": "auto" for axis in "xyz"})
        writer.update({f"offset_{axis}": "auto" for axis in "xyz"})
    # PDAL interprets # anywhere in its writer path as a point-view placeholder.
    # Write privately first, then copy to a same-filesystem staging file before
    # atomic publication so ordinary user folder names cannot alter that path.
    with tempfile.TemporaryDirectory(prefix="pyforestscan-hag-") as directory:
        staged = os.path.join(directory, "height" + os.path.splitext(output)[1])
        writer["filename"] = staged
        feedback.pushInfo(
            "Calculating and persisting HeightAboveGround in the isolated runtime."
        )
        pipeline = pdal.Pipeline(json.dumps(_stages(prepared) + [writer]))
        pipeline.execute()
        points = _points(pipeline.arrays, prepared)
        temporary = _temporary(
            output, ".pyforestscan-hag-", os.path.splitext(output)[1]
        )
        try:
            shutil.copyfile(staged, temporary)
            os.replace(temporary, output)
            return {"output": output, "count": int(points.size)}
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def metrics(payload, feedback):
    prepared = _prepared(payload["prepared"], metres=True)
    paths = dict(payload["paths"])
    selected = payload["metrics"]
    if not selected or any(name not in paths for name in selected):
        raise ValueError("Every selected metric requires an output path.")
    _protect(paths.values(), [prepared["path"], prepared.get("dtm_path")])
    if len({os.path.realpath(path) for path in paths.values()}) != len(paths):
        raise ValueError("Each metric requires a different output path.")
    if not payload.get("overwrite", True) and any(
        os.path.exists(path) for path in paths.values()
    ):
        raise ValueError(
            "Metric output files already exist; choose new destinations or enable overwrite."
        )
    if any(
        name != "rumple" and not paths[name].lower().endswith((".tif", ".tiff"))
        for name in selected
    ):
        raise ValueError(
            "Forest metric raster outputs must use a .tif or .tiff extension."
        )
    options = dict(payload.get("options", {}))
    options.pop("feedback", None)
    options.setdefault("z_resolution", payload.get("z_resolution", 1))
    results = compute_metrics(
        read_points(prepared, feedback), selected, feedback=feedback, **options
    )
    result = {"files": {}}
    for index, name in enumerate(selected):
        path = paths[name]
        if name == "rumple":
            _write_rumple(path, results[name])
            result["rumple"] = results[name]
        else:
            write_metric_raster(
                results[name],
                path,
                prepared["crs_wkt"],
                z_resolution=options["z_resolution"],
            )
        result["files"][name] = path
        feedback.setProgress(100 * (index + 1) / len(selected))
    return result


def rumple(payload, feedback):
    import numpy as np
    from osgeo import gdal
    from pyproj import CRS
    from ..vendor.pyforestscan.calculate import calculate_rumple

    source, output = payload["source"], payload["output"]
    _protect([output], [source])
    dataset = gdal.Open(source, gdal.GA_ReadOnly)
    if dataset is None:
        raise ValueError("The canopy height raster could not be read.")
    try:
        stored_crs = _crs(dataset.GetProjectionRef(), metres=True)
        if payload.get("crs_wkt") and not stored_crs.equals(
            CRS.from_user_input(payload["crs_wkt"]), ignore_axis_order=True
        ):
            raise ValueError("The canopy raster's stored CRS must match its layer CRS.")
        transform = dataset.GetGeoTransform()
        if (
            transform[2] != 0
            or transform[4] != 0
            or transform[1] <= 0
            or transform[5] >= 0
        ):
            raise ValueError(
                "Use a north-up canopy height raster; warp rotated rasters first."
            )
        if dataset.RasterXSize * dataset.RasterYSize > 20_000_000:
            raise ValueError(
                "The canopy raster exceeds the memory limit; clip it first."
            )
        index = int(payload.get("band", 1))
        if index < 1 or index > dataset.RasterCount:
            raise ValueError("Choose an existing raster band.")
        band = dataset.GetRasterBand(index)
        values = band.ReadAsArray().astype(float)
        values[band.GetMaskBand().ReadAsArray() == 0] = np.nan
        nodata = band.GetNoDataValue()
        if nodata is not None:
            values[values == nodata] = np.nan
        value = float(
            calculate_rumple(
                values.T,
                (transform[1], -transform[5]),
                min_height=payload.get("min_height"),
            )
        )
    finally:
        dataset = None
    if not math.isfinite(value):
        raise ValueError("No valid 2 × 2 canopy surface patches remain.")
    _write_rumple(output, value)
    return {"rumple": value, "output": output}


def _ept_bounds(path):
    if path.lower().startswith(("http://", "https://")):
        with urlopen(path, timeout=30) as stream:
            data = json.load(stream)
    else:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
    bounds = data.get("bounds", data.get("boundsConforming"))
    if isinstance(bounds, dict):
        return bounds["minx"], bounds["maxx"], bounds["miny"], bounds["maxy"]
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 6:
        raise ValueError("The EPT metadata contains no valid bounds.")
    return bounds[0], bounds[3], bounds[1], bounds[4]


def tiles(payload, feedback):
    import numpy as np

    prepared = _prepared(payload["prepared"], metres=True)
    if prepared["reader"]["type"] != "readers.ept":
        raise ValueError("Tiled processing requires an EPT point cloud.")
    options = dict(payload.get("options", {}))
    defaults = {
        "VOXEL_XY": 1,
        "VOXEL_Z": 1,
        "TILE_WIDTH": 100,
        "TILE_HEIGHT": 100,
        "BUFFER_FRACTION": 0.1,
        "MIN_HEIGHT": 1,
        "FHD_MIN_HEIGHT": 0,
        "COVER_MIN_HEIGHT": 2,
        "COVER_K": 0.5,
    }

    def number(key):
        return float(options.get(key, defaults[key]))

    xy, dz = number("VOXEL_XY"), number("VOXEL_Z")
    width, height, buffer = (
        number("TILE_WIDTH"),
        number("TILE_HEIGHT"),
        number("BUFFER_FRACTION"),
    )
    if (
        not all(math.isfinite(v) and v > 0 for v in (xy, dz, width, height))
        or not math.isfinite(buffer)
        or not 0 <= buffer <= 1
    ):
        raise ValueError(
            "Tile/grid sizes must be positive and finite; buffer fraction must be between 0 and 1."
        )
    if any(
        not math.isclose(v / xy, round(v / xy), rel_tol=0, abs_tol=1e-8)
        for v in (width, height)
    ):
        raise ValueError("Tile dimensions must be whole multiples of XY resolution.")
    columns, rows = round(width / xy), round(height / xy)
    if columns < 1 or rows < 1 or columns * rows > MAX_RASTER_CELLS:
        raise ValueError(
            "Tile raster dimensions are invalid or exceed the memory limit."
        )
    if prepared.get("bounds"):
        (xmin, xmax), (ymin, ymax) = prepared["bounds"]
    elif prepared.get("info", {}).get("bounds"):
        extent = prepared["info"]["bounds"]
        xmin, xmax, ymin, ymax = (
            extent[key] for key in ("minx", "maxx", "miny", "maxy")
        )
    else:
        xmin, xmax, ymin, ymax = _ept_bounds(prepared["path"])
    if (
        not all(math.isfinite(v) for v in (xmin, xmax, ymin, ymax))
        or xmax <= xmin
        or ymax <= ymin
    ):
        raise ValueError("EPT bounds must be finite and have nonzero area.")

    def edge(value, rounding):
        quotient = value / xy
        nearest = round(quotient)
        return (
            nearest
            if math.isclose(quotient, nearest, rel_tol=0, abs_tol=1e-8)
            else rounding(quotient)
        )

    x0, x1, y0, y1 = (
        edge(xmin, math.floor),
        edge(xmax, math.ceil),
        edge(ymin, math.floor),
        edge(ymax, math.ceil),
    )
    nx, ny = (x1 - x0 + columns - 1) // columns, (y1 - y0 + rows - 1) // rows
    if nx * ny > 100_000:
        raise ValueError("The request exceeds 100,000 tiles; limit the study area.")
    names = ["CHM", "PAI", "FHD", "canopy cover"]
    choice = options.get("METRIC", 0)
    metric = (
        names[choice]
        if isinstance(choice, int) and 0 <= choice < len(names)
        else choice
    )
    if metric not in names:
        raise ValueError("Choose CHM, PAI, FHD or canopy cover for EPT tiling.")
    suffix = "cover" if metric == "canopy cover" else metric.lower()
    folder = os.path.abspath(payload["folder"])
    paths = {
        (i, j): os.path.join(folder, f"tile_{i}_{j}_{suffix}.tif")
        for i in range(nx)
        for j in range(ny)
    }
    _protect(paths.values(), [prepared["path"], prepared.get("dtm_path")])
    if not options.get("OVERWRITE", False) and any(
        os.path.exists(path) for path in paths.values()
    ):
        raise ValueError(
            "Tile files already exist; choose a new folder or enable overwrite."
        )
    os.makedirs(folder, exist_ok=True)
    created = []
    for index, ((i, j), path) in enumerate(paths.items()):
        lx, by = x0 + i * columns, y0 + j * rows
        rx, ty = min(lx + columns, x1), min(by + rows, y1)
        left, right, bottom, top = lx * xy, rx * xy, by * xy, ty * xy
        bounds = (
            [
                math.nextafter(left - buffer * width, -math.inf),
                math.nextafter(right + buffer * width, math.inf),
            ],
            [
                math.nextafter(bottom - buffer * height, -math.inf),
                math.nextafter(top + buffer * height, math.inf),
            ],
        )
        tile = dict(prepared)
        tile["reader"] = dict(
            prepared["reader"],
            bounds=f"([{bounds[0][0]},{bounds[0][1]}],[{bounds[1][0]},{bounds[1][1]}])",
        )
        tile["filters"] = [
            dict(stage)
            for stage in prepared["filters"]
            if not (stage["type"] == "filters.crop" and "bounds" in stage)
        ]
        if options.get("REMOVE_OUTLIERS", False):
            position = next(
                (
                    n
                    for n, stage in enumerate(tile["filters"])
                    if stage["type"].startswith("filters.hag")
                ),
                len(tile["filters"]),
            )
            tile["filters"][position:position] = [
                {
                    "type": "filters.outlier",
                    "method": "statistical",
                    "mean_k": 8,
                    "multiplier": 3,
                },
                {"type": "filters.expression", "expression": "Classification != 7"},
            ]
        feedback.pushInfo(f"Tile {index + 1}/{len(paths)} ({i}, {j})")
        points = read_points(tile, feedback, allow_empty=True)
        if points.size:
            # Classify boundaries on integer grid indices. Floating tile edges
            # such as 3 * .1 otherwise send X=.3 to the preceding tile.
            keep = (
                (points["X"] >= xmin)
                & (points["X"] <= xmax)
                & (points["Y"] >= ymin)
                & (points["Y"] <= ymax)
            )
            points = points[keep]
            indices = {
                axis: np.minimum(
                    np.floor(np.nextafter(points[axis] / xy, np.inf)), high - 1
                )
                for axis, high in (("X", x1), ("Y", y1))
            }
            keep = (
                (indices["X"] >= lx)
                & (indices["X"] < rx)
                & (indices["Y"] >= by)
                & (indices["Y"] < ty)
            )
            points = points[keep].copy()
            for axis in ("X", "Y"):
                points[axis] = (indices[axis][keep] + 0.5) * xy
        if points.size:
            requested = "voxel statistic" if metric == "CHM" else metric
            result = compute_metrics(
                points,
                [requested],
                xy_resolution=xy,
                z_resolution=dz,
                min_height=number("MIN_HEIGHT"),
                fhd_min_height=number("FHD_MIN_HEIGHT"),
                cover_min_height=number("COVER_MIN_HEIGHT"),
                cover_k=number("COVER_K"),
                voxel_dimension="HeightAboveGround",
                voxel_stat="max",
                feedback=feedback,
            )[requested]
            grid = np.full((rx - lx, ty - by), np.nan)
            col, row = (
                round((result.extent[0] - left) / xy),
                round((top - result.extent[3]) / xy),
            )
            if (
                col < 0
                or row < 0
                or col + result.data.shape[0] > grid.shape[0]
                or row + result.data.shape[1] > grid.shape[1]
            ):
                raise ValueError("Metric grid exceeds the tile footprint.")
            grid[col : col + result.data.shape[0], row : row + result.data.shape[1]] = (
                result.data
            )
            write_metric_raster(
                MetricResult(grid, (left, right, bottom, top)),
                path,
                prepared["crs_wkt"],
            )
            created.append(path)
        feedback.setProgress(100 * (index + 1) / len(paths))
    return {"files": created}


def selftest(payload, feedback):
    import numpy as np
    import scipy
    import pdal
    import pyproj
    from osgeo import gdal

    feedback.pushInfo(
        "Checking the isolated runtime with a real LAS, HAG and forest metric job."
    )
    with tempfile.TemporaryDirectory(
        prefix="pyforestscan-worker-selftest-"
    ) as directory:
        source, output = (
            os.path.join(directory, "source.las"),
            os.path.join(directory, "hag.las"),
        )
        points = np.array(
            [
                (500000.25 + x, 4100000.25 + y, 100 + height, cls)
                for x in range(3)
                for y in range(3)
                for height, cls in ((0, 2), (5, 5))
            ],
            dtype=[("X", "f8"), ("Y", "f8"), ("Z", "f8"), ("Classification", "u1")],
        )
        pdal.Pipeline(
            json.dumps(
                [{"type": "writers.las", "filename": source, "a_srs": "EPSG:32610"}]
            ),
            arrays=[points],
        ).execute()
        prepared = {
            "path": source,
            "crs_wkt": pyproj.CRS("EPSG:32610").to_wkt(),
            "reader": {"type": "readers.las", "filename": source},
            "filters": [{"type": "filters.hag_delaunay"}],
            "hag_method": "delaunay",
            "dtm_path": None,
        }
        hag({"prepared": prepared, "output": output}, feedback)
        cloud = read_points(
            dict(
                prepared,
                path=output,
                reader={"type": "readers.las", "filename": output},
                filters=[],
                hag_method="none",
            ),
            feedback,
        )
        if (
            len(cloud) != len(points)
            or not np.allclose(cloud["HeightAboveGround"], np.tile([0, 5], 9))
            or not np.array_equal(cloud["Z"], points["Z"])
        ):
            raise RuntimeError(
                "The isolated runtime HAG/coordinate preservation self-test failed."
            )
        result = compute_metrics(cloud, ["PAI"], feedback=feedback)["PAI"]
        finite = result.data[np.isfinite(result.data)]
        if not finite.size or not np.allclose(finite, np.log(2)):
            raise RuntimeError("The isolated runtime forest metric self-test failed.")
        raster = os.path.join(directory, "pai.tif")
        write_metric_raster(result, raster, prepared["crs_wkt"])
        dataset = gdal.Open(raster)
        if dataset is None or dataset.RasterCount != 1:
            raise RuntimeError("The isolated runtime raster writer self-test failed.")
        dataset = None
    return {
        "ready": True,
        "python": sys.version.split()[0],
        "pyforestscan": "0.4.1",
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "pdal": pdal.__version__,
        "gdal": gdal.VersionInfo("RELEASE_NAME"),
        "pyproj": pyproj.__version__,
    }


def run_job(operation, payload, feedback):
    operations = {
        "selftest": selftest,
        "metadata": metadata,
        "hag": hag,
        "metrics": metrics,
        "rumple": rumple,
        "tiles": tiles,
    }
    if operation not in operations:
        raise ValueError(f"Unknown worker operation: {operation}.")
    return operations[operation](payload, feedback)
