"""Bounded EPT orchestration with per-tile feedback and a common raster grid."""

import math
import os

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputMultipleLayers,
    QgsProcessingOutputString,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber,
)

from .common import (
    add_raster_to_load_on_completion,
    check_canceled,
    ensure_output_folder,
    help_url,
    protect_input_paths,
    ensure_backend,
    require_metric_crs,
)
from .point_cloud import (
    add_point_cloud_parameters,
    prepare_point_cloud,
    read_point_cloud,
    with_point_cloud_bounds,
)


class ProcessEptTilesAlgorithm(QgsProcessingAlgorithm):
    METRIC_OPTIONS = ["CHM", "PAI", "FHD", "canopy cover"]

    def name(self):
        return "processepttiles"

    def displayName(self):
        return "Process EPT Tiles"

    def group(self):
        return "Large datasets"

    def groupId(self):
        return "largedatasets"

    def helpUrl(self):
        return help_url(self.name())

    def shortHelpString(self):
        return (
            "<p>Process a loaded EPT layer, local ept.json, or remote EPT URL one tile at a time. "
            "Choose CHM, PAI, FHD, or PAD-derived canopy cover. Results are GeoTIFFs on a common "
            "grid in the source's projected metre CRS. The source must already contain a "
            "<b>HeightAboveGround</b> dimension. Create it with QGIS or PyForestScan's "
            "Add Height Above Ground before calculating forest metrics.</p>"
            "<p>Tile dimensions must be whole multiples of the XY cell size. The output extent "
            "is snapped outward to the grid. Points are clipped to the study area; edge cells "
            "can extend beyond it and cells without retained points are NoData. "
            "When outlier removal is enabled, buffer points supply filtering context and are excluded from the "
            "core tile metrics. CHM uses the maximum HeightAboveGround per globally aligned cell, "
            "without gap interpolation. Empty tiles are skipped.</p>"
            "<p>New files only are returned. Existing tile files cause an error unless Overwrite "
            "is enabled. Completed tiles remain on disk if canceled; no incomplete tile is published. "
            "Cancellation and progress are checked between tiles, not during a PDAL operation. "
            "Use QGIS Build Virtual Raster to view many tiles together.</p>"
        )

    def _number(self, name, label, default, minimum=0.0, when=None):
        parameter = QgsProcessingParameterNumber(
            name,
            label,
            Qgis.ProcessingNumberParameterType.Double,
            defaultValue=default,
            minValue=minimum,
        )
        if when is not None:
            from .parameter_widgets import show_parameter_when

            show_parameter_when(parameter, *when)
        self.addParameter(parameter)

    def initAlgorithm(self, config=None):
        add_point_cloud_parameters(
            self, crop=False, input_name="EPT_INPUT", crs_name="SRS"
        )
        self.addParameter(
            QgsProcessingParameterExtent("BOUNDS", "Study area extent", optional=True)
        )
        self._number("TILE_WIDTH", "Tile width (m)", 100, 0.000001)
        self._number("TILE_HEIGHT", "Tile height (m)", 100, 0.000001)
        self.addParameter(
            QgsProcessingParameterEnum(
                "METRIC", "Metric", self.METRIC_OPTIONS, defaultValue=0
            )
        )
        self._number("VOXEL_XY", "XY grid cell size (m)", 1, 0.000001)
        self._number(
            "VOXEL_Z", "Height bin size (m)", 1, 0.000001, when=("METRIC", [1, 2, 3])
        )
        self._number(
            "MIN_HEIGHT", "Minimum height for PAI (m)", 1, when=("METRIC", [1])
        )
        self._number(
            "FHD_MIN_HEIGHT", "Minimum height for FHD (m)", 0, when=("METRIC", [2])
        )
        self._number(
            "COVER_MIN_HEIGHT",
            "Minimum height for canopy cover (m)",
            2,
            when=("METRIC", [3]),
        )
        self._number(
            "COVER_K",
            "Canopy cover extinction coefficient",
            0.5,
            when=("METRIC", [3]),
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                "REMOVE_OUTLIERS",
                "Remove statistical outliers before calculating metrics",
                defaultValue=False,
            )
        )
        self._number(
            "BUFFER_FRACTION",
            "Buffer fraction for outlier filtering",
            0.1,
            when=("REMOVE_OUTLIERS", [True]),
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination("OUTPUT_FOLDER", "Output folder")
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                "LOAD_OUTPUTS", "Load output tiles into the project", defaultValue=False
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                "OVERWRITE", "Overwrite existing tile files", defaultValue=False
            )
        )
        self.addOutput(QgsProcessingOutputString("OUTPUT_FILES", "Created tile files"))
        self.addOutput(
            QgsProcessingOutputMultipleLayers("OUTPUT_LAYERS", "Created raster tiles")
        )

    def prepareAlgorithm(self, parameters, context, feedback):
        self._backend = ensure_backend("tiles", parameters, context, self, feedback)
        self._prepared = prepare_point_cloud(
            self,
            parameters,
            context,
            feedback,
            input_name="EPT_INPUT",
            crs_name="SRS",
            backend=self._backend,
        )
        if self._prepared.reader["type"] != "readers.ept":
            raise QgsProcessingException(
                "This tool requires an EPT layer or ept.json path / URL."
            )
        require_metric_crs(QgsCoordinateReferenceSystem(self._prepared.crs_wkt))
        return True

    def processAlgorithm(self, parameters, context, feedback):
        if self._backend == "engine":
            from dataclasses import asdict
            from ..runtime.client import call_engine

            folder = self.parameterAsString(parameters, "OUTPUT_FOLDER", context)
            options = {
                key: self.parameterAsDouble(parameters, key, context)
                for key in (
                    "VOXEL_XY",
                    "VOXEL_Z",
                    "TILE_WIDTH",
                    "TILE_HEIGHT",
                    "BUFFER_FRACTION",
                    "MIN_HEIGHT",
                    "FHD_MIN_HEIGHT",
                    "COVER_MIN_HEIGHT",
                    "COVER_K",
                )
            }
            options["METRIC"] = self.parameterAsEnum(parameters, "METRIC", context)
            for key in ("REMOVE_OUTLIERS", "OVERWRITE"):
                options[key] = self.parameterAsBool(parameters, key, context)
            result = call_engine(
                "tiles",
                {
                    "prepared": asdict(self._prepared),
                    "options": options,
                    "folder": folder,
                },
                feedback,
            )
            created = result["files"]
            if self.parameterAsBool(parameters, "LOAD_OUTPUTS", context):
                for path in created:
                    add_raster_to_load_on_completion(
                        context, path, os.path.basename(path), "OUTPUT_LAYERS"
                    )
            return {
                "OUTPUT_FOLDER": folder,
                "OUTPUT_FILES": "\n".join(created),
                "OUTPUT_LAYERS": created,
            }
        import numpy as np
        from dataclasses import replace
        from .metric_engine import (
            compute_metrics,
            MetricResult,
            write_metric_raster,
            MAX_RASTER_CELLS,
        )

        def number(key):
            return self.parameterAsDouble(parameters, key, context)

        metric = self.METRIC_OPTIONS[
            self.parameterAsEnum(parameters, "METRIC", context)
        ]
        xy = number("VOXEL_XY")
        dz = 1.0 if metric == "CHM" else number("VOXEL_Z")
        width, height = number("TILE_WIDTH"), number("TILE_HEIGHT")
        buffer = (
            number("BUFFER_FRACTION")
            if self.parameterAsBool(parameters, "REMOVE_OUTLIERS", context)
            else 0.0
        )
        if not all(
            math.isfinite(value) and value > 0 for value in (xy, dz, width, height)
        ) or not math.isfinite(buffer):
            raise QgsProcessingException(
                "Tile and voxel sizes must be finite and positive."
            )
        if buffer < 0 or buffer > 1:
            raise QgsProcessingException("Buffer fraction must be between 0 and 1.")
        if any(
            not math.isclose(size / xy, round(size / xy), rel_tol=0, abs_tol=1e-8)
            for size in (width, height)
        ):
            raise QgsProcessingException(
                "Tile width and height must be whole multiples of XY cell size."
            )
        if round(width / xy) * round(height / xy) > MAX_RASTER_CELLS:
            raise QgsProcessingException(
                "The tile raster exceeds the memory limit. Use smaller tiles or larger cells."
            )
        prepared = self._prepared
        if prepared.bounds:
            (xmin, xmax), (ymin, ymax) = prepared.bounds
        else:
            extent = prepared.info["bounds"]
            xmin, xmax, ymin, ymax = (
                extent[key] for key in ("minx", "maxx", "miny", "maxy")
            )
        if (
            not all(math.isfinite(v) for v in (xmin, xmax, ymin, ymax))
            or xmax <= xmin
            or ymax <= ymin
        ):
            raise QgsProcessingException(
                "EPT study bounds must be finite and have nonzero area."
            )

        def edge_index(value, rounding):
            quotient = value / xy
            nearest = round(quotient)
            return (
                nearest
                if math.isclose(quotient, nearest, rel_tol=0, abs_tol=1e-8)
                else rounding(quotient)
            )

        x0, y0 = edge_index(xmin, math.floor), edge_index(ymin, math.floor)
        x1, y1 = edge_index(xmax, math.ceil), edge_index(ymax, math.ceil)
        tile_columns, tile_rows = round(width / xy), round(height / xy)
        nx = (x1 - x0 + tile_columns - 1) // tile_columns
        ny = (y1 - y0 + tile_rows - 1) // tile_rows
        if nx * ny > 100_000:
            raise QgsProcessingException(
                "The request exceeds 100,000 tiles. Limit the study extent or increase tile size."
            )
        suffix = {"canopy cover": "cover"}.get(metric, metric.lower())
        folder = ensure_output_folder(
            self.parameterAsString(parameters, "OUTPUT_FOLDER", context)
        )
        paths = {
            (i, j): os.path.join(folder, f"tile_{i}_{j}_{suffix}.tif")
            for i in range(nx)
            for j in range(ny)
        }
        protect_input_paths(paths.values(), [prepared.path, prepared.dtm_path])
        if not self.parameterAsBool(parameters, "OVERWRITE", context):
            existing = next(
                (path for path in paths.values() if os.path.exists(path)), None
            )
            if existing:
                raise QgsProcessingException(
                    f"Tile already exists: {existing}. Choose another folder or enable Overwrite."
                )
        created = []
        for index, ((i, j), path) in enumerate(paths.items()):
            check_canceled(feedback)
            left_index, bottom_index = x0 + i * tile_columns, y0 + j * tile_rows
            right_index, top_index = (
                min(left_index + tile_columns, x1),
                min(bottom_index + tile_rows, y1),
            )
            left, bottom = left_index * xy, bottom_index * xy
            right, top = right_index * xy, top_index * xy
            read_bounds = (
                [
                    math.nextafter(left - buffer * width, -math.inf),
                    math.nextafter(right + buffer * width, math.inf),
                ],
                [
                    math.nextafter(bottom - buffer * height, -math.inf),
                    math.nextafter(top + buffer * height, math.inf),
                ],
            )
            tile = with_point_cloud_bounds(prepared, read_bounds)
            if self.parameterAsBool(parameters, "REMOVE_OUTLIERS", context):
                stages = list(tile.filters)
                position = next(
                    (
                        k
                        for k, stage in enumerate(stages)
                        if stage["type"].startswith("filters.hag")
                    ),
                    len(stages),
                )
                stages[position:position] = [
                    {
                        "type": "filters.outlier",
                        "method": "statistical",
                        "mean_k": 8,
                        "multiplier": 3,
                    },
                    {"type": "filters.expression", "expression": "Classification != 7"},
                ]
                tile = replace(tile, filters=tuple(stages))
            feedback.pushInfo(f"Tile {index + 1}/{len(paths)} ({i}, {j})")
            points = read_point_cloud(tile, feedback, allow_empty=True, context=context)
            if points.size:
                # Use integer cell membership, not rounded floating tile edges.
                # In particular, X=.3 belongs to cell3 for a .1m grid even if
                # the computed tile boundary is .30000000000000004.
                xcell = np.floor(np.nextafter(points["X"] / xy, np.inf))
                ycell = np.floor(np.nextafter(points["Y"] / xy, np.inf))
                xcell = np.minimum(xcell, x1 - 1)
                ycell = np.minimum(ycell, y1 - 1)
                # LAS scale/offset decoding can move an exact AOI edge by one
                # float64 ULP. Retain that edge without a map-unit tolerance.
                keep = (
                    (points["X"] >= math.nextafter(xmin, -math.inf))
                    & (points["X"] <= math.nextafter(xmax, math.inf))
                    & (points["Y"] >= math.nextafter(ymin, -math.inf))
                    & (points["Y"] <= math.nextafter(ymax, math.inf))
                    & (xcell >= left_index)
                    & (xcell < right_index)
                    & (ycell >= bottom_index)
                    & (ycell < top_index)
                )
                points = points[keep]
                xcell, ycell = xcell[keep], ycell[keep]
            if points.size:
                try:
                    # The core's final histogram bin is closed. Centre positions
                    # preserve global cell membership without changing heights.
                    points = points.copy()
                    points["X"] = (xcell + 0.5) * xy
                    points["Y"] = (ycell + 0.5) * xy
                    # Core column maximum has the same globally anchored grid as PAD/PAI/FHD.
                    requested = "voxel statistic" if metric == "CHM" else metric
                    result = compute_metrics(
                        points,
                        [requested],
                        xy_resolution=xy,
                        z_resolution=dz,
                        min_height=number("MIN_HEIGHT") if metric == "PAI" else 1,
                        fhd_min_height=number("FHD_MIN_HEIGHT")
                        if metric == "FHD"
                        else 0,
                        cover_min_height=(
                            number("COVER_MIN_HEIGHT")
                            if metric == "canopy cover"
                            else 2
                        ),
                        cover_k=number("COVER_K") if metric == "canopy cover" else 0.5,
                        voxel_dimension="HeightAboveGround",
                        voxel_stat="max",
                        feedback=feedback,
                    )[requested]
                    grid = np.full(
                        (right_index - left_index, top_index - bottom_index), np.nan
                    )
                    col = round((result.extent[0] - left) / xy)
                    row = round((top - result.extent[3]) / xy)
                    if (
                        col < 0
                        or row < 0
                        or col + result.data.shape[0] > grid.shape[0]
                        or row + result.data.shape[1] > grid.shape[1]
                    ):
                        raise ValueError("Metric grid exceeds the tile footprint.")
                    grid[
                        col : col + result.data.shape[0],
                        row : row + result.data.shape[1],
                    ] = result.data
                    check_canceled(feedback)
                    write_metric_raster(
                        MetricResult(grid, (left, right, bottom, top)),
                        path,
                        prepared.crs_wkt,
                    )
                except (ValueError, KeyError, RuntimeError, MemoryError) as exc:
                    raise QgsProcessingException(
                        f"Tile ({i}, {j}) failed: {exc}"
                    ) from exc
                created.append(path)
                if self.parameterAsBool(parameters, "LOAD_OUTPUTS", context):
                    add_raster_to_load_on_completion(
                        context, path, f"{metric} tile {i}, {j}", "OUTPUT_LAYERS"
                    )
            feedback.setProgress(100 * (index + 1) / len(paths))
        return {
            "OUTPUT_FOLDER": folder,
            "OUTPUT_FILES": "\n".join(created),
            "OUTPUT_LAYERS": created,
        }

    def createInstance(self):
        return ProcessEptTilesAlgorithm()
