"""Resolve QGIS inputs once, then process their full-resolution source with PDAL."""

from dataclasses import dataclass, replace
import json
import os
from urllib.parse import unquote, urlsplit

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProcessing,
    QgsProcessingException,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterPointCloudLayer,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsProviderRegistry,
)


@dataclass(frozen=True)
class PreparedPointCloud:
    path: str
    crs_wkt: str
    crs_authid: str
    bounds: object
    polygon: object
    reader: dict
    filters: tuple
    hag_method: str
    dtm_path: object = None

    @property
    def stages(self):
        return [dict(self.reader), *(dict(stage) for stage in self.filters)]


def add_point_cloud_parameters(
    algorithm, hag=True, crop=True, input_name="INPUT", crs_name="INPUT_CRS"
):
    algorithm.addParameter(
        QgsProcessingParameterPointCloudLayer(
            "INPUT_LAYER", "Input point cloud layer", optional=True
        )
    )
    algorithm.addParameter(
        QgsProcessingParameterString(
            input_name, "Or point cloud file / EPT URL", optional=True
        )
    )
    algorithm.addParameter(
        QgsProcessingParameterCrs(
            crs_name, "Assign CRS only when the source has no CRS", optional=True
        )
    )
    if crop:
        algorithm.addParameter(
            QgsProcessingParameterExtent(
                "BOUNDS", "Limit processing to extent", optional=True
            )
        )
        algorithm.addParameter(
            QgsProcessingParameterFeatureSource(
                "POLYGON",
                "Crop polygon layer",
                [QgsProcessing.SourceType.TypeVectorPolygon],
                optional=True,
            )
        )
    if hag:
        algorithm.addParameter(
            QgsProcessingParameterEnum(
                "HAG_METHOD",
                "Height above ground",
                ["none", "delaunay", "dtm"],
                defaultValue=0,
            )
        )
        algorithm.addParameter(
            QgsProcessingParameterRasterLayer(
                "DTM", "Terrain raster for DTM height above ground", optional=True
            )
        )


def _provided(parameters, name):
    value = parameters.get(name)
    return value is not None and value != ""


def point_cloud_reader(path):
    """Inspect the URL path without losing query parameters required by servers."""
    suffix = (
        urlsplit(path).path
        if path.lower().startswith(("http://", "https://", "file://"))
        else path
    ).lower()
    if suffix.endswith((".copc.laz", ".copc")):
        return "readers.copc"
    if suffix.endswith((".las", ".laz")):
        return "readers.las"
    if suffix.endswith("ept.json"):
        return "readers.ept"
    raise QgsProcessingException(
        "Use a LAS, LAZ, COPC, or EPT ept.json source. Virtual point clouds and "
        "other providers must first be exported with the QGIS point cloud tools."
    )


def _source_path(path):
    parsed = urlsplit(path) if "://" in path else None
    if parsed and parsed.scheme.lower() == "file":
        if parsed.netloc not in ("", "localhost"):
            raise QgsProcessingException(
                "Use a local path for file:// point cloud sources."
            )
        path = unquote(parsed.path)
    elif parsed and parsed.scheme.lower() in ("http", "https"):
        return path
    elif parsed:
        raise QgsProcessingException("Point cloud URLs must use HTTP or HTTPS.")
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise QgsProcessingException(f"Point cloud file does not exist: {path}")
    return path


def _metadata(reader):
    import pdal

    try:
        info = pdal.Pipeline(json.dumps([reader])).quickinfo
        if isinstance(info, str):
            info = json.loads(info)
        return next(iter(info.values()))
    except (RuntimeError, ValueError, StopIteration, TypeError) as exc:
        raise QgsProcessingException(
            f"Could not read the point cloud metadata: {exc}"
        ) from exc


def _metadata_crs(info):
    srs = info.get("srs") or {}
    if isinstance(srs, str):
        return QgsCoordinateReferenceSystem(srs)
    return QgsCoordinateReferenceSystem(srs.get("compoundwkt") or srs.get("wkt") or "")


def _same_horizontal_crs(first, second):
    # A LAS may include a vertical CRS while a raster/layer advertises its horizontal CRS.
    import pyproj

    a = pyproj.CRS.from_wkt(first.toWkt()).to_2d()
    b = pyproj.CRS.from_wkt(second.toWkt()).to_2d()
    return a.equals(b, ignore_axis_order=True)


def _crop_polygon(algorithm, parameters, context, crs):
    if not _provided(parameters, "POLYGON"):
        return None
    source = algorithm.parameterAsSource(parameters, "POLYGON", context)
    if source is None:
        raise QgsProcessingException("The crop polygon layer could not be read.")
    if not source.sourceCrs().isValid():
        raise QgsProcessingException("The crop polygon layer must have a valid CRS.")
    transform = QgsCoordinateTransform(
        source.sourceCrs(), crs, context.transformContext()
    )
    geometries = []
    for feature in source.getFeatures():
        geometry = QgsGeometry(feature.geometry())
        if geometry.isEmpty():
            continue
        if not geometry.isGeosValid():
            raise QgsProcessingException(
                "The crop polygon contains invalid geometry; fix it before processing."
            )
        geometry.transform(transform)
        geometries.append(geometry)
    if not geometries:
        raise QgsProcessingException(
            "The crop polygon layer contains no nonempty polygons."
        )
    union = QgsGeometry.unaryUnion(geometries)
    if union.isEmpty() or not union.isGeosValid():
        raise QgsProcessingException(
            "The crop polygons could not be combined into a valid geometry."
        )
    return union.asWkt()


def _dtm_path(algorithm, parameters, context, crs):
    layer = algorithm.parameterAsRasterLayer(parameters, "DTM", context)
    if layer is None or not layer.isValid():
        raise QgsProcessingException(
            "Choose a terrain raster for the DTM height method."
        )
    if not layer.crs().isValid() or not _same_horizontal_crs(layer.crs(), crs):
        raise QgsProcessingException(
            "The terrain raster and point cloud must use the same horizontal CRS. "
            "Reproject the terrain raster with QGIS first."
        )
    if layer.providerType() != "gdal":
        raise QgsProcessingException(
            "Export the terrain raster to a local GDAL-readable raster first."
        )
    decoded = QgsProviderRegistry.instance().decodeUri(
        layer.providerType(), layer.source()
    )
    path = decoded.get("path", layer.source())
    if not os.path.isfile(path):
        raise QgsProcessingException(
            "The terrain raster must be a local GDAL-readable file."
        )
    import rasterio

    with rasterio.open(path) as raster:
        stored_crs = (
            QgsCoordinateReferenceSystem(raster.crs.to_wkt())
            if raster.crs
            else QgsCoordinateReferenceSystem()
        )
        if not stored_crs.isValid() or not _same_horizontal_crs(stored_crs, crs):
            raise QgsProcessingException(
                "The terrain raster's stored CRS must match the point cloud; assigning a layer CRS does not reproject its data."
            )
    return path


def prepare_point_cloud(
    algorithm,
    parameters,
    context,
    feedback,
    hag_method=None,
    input_name="INPUT",
    crs_name="INPUT_CRS",
):
    """Call from prepareAlgorithm so QGIS layers never cross the worker boundary."""
    if feedback.isCanceled():
        raise QgsProcessingException("Processing canceled.")
    supplied_layer = _provided(parameters, "INPUT_LAYER")
    path = algorithm.parameterAsString(parameters, input_name, context).strip()
    if supplied_layer and path:
        raise QgsProcessingException(
            "Choose an input point cloud layer OR a file / URL, not both."
        )
    layer_crs = QgsCoordinateReferenceSystem()
    if supplied_layer:
        layer = algorithm.parameterAsPointCloudLayer(
            parameters,
            "INPUT_LAYER",
            context,
            QgsProcessing.LayerOptionsFlag.SkipIndexGeneration,
        )
        if layer is None or not layer.isValid():
            raise QgsProcessingException(
                "The input point cloud layer could not be read."
            )
        if layer.providerType() not in ("pdal", "ept", "copc"):
            raise QgsProcessingException(
                "Export this point cloud to LAS/LAZ/COPC before using PyForestScan."
            )
        if layer.isModified():
            raise QgsProcessingException(
                "Save the point cloud's pending edits before processing it."
            )
        if layer.subsetString():
            raise QgsProcessingException(
                "The point cloud layer has a filter. Export the filtered points first, "
                "or remove the filter to process the complete source."
            )
        decoded = QgsProviderRegistry.instance().decodeUri(
            layer.providerType(), layer.source()
        )
        path = decoded.get("path", layer.source())
        layer_crs = layer.crs()
    if not path:
        raise QgsProcessingException(
            "Choose a point cloud layer or provide a point cloud file / EPT URL."
        )
    path = _source_path(path)
    reader = {"type": point_cloud_reader(path), "filename": path}
    info = _metadata(reader)
    native_crs = _metadata_crs(info)
    assigned_crs = algorithm.parameterAsCrs(parameters, crs_name, context)
    if native_crs.isValid():
        for supplied in (layer_crs, assigned_crs):
            if supplied.isValid() and not _same_horizontal_crs(supplied, native_crs):
                raise QgsProcessingException(
                    "The supplied CRS differs from the point cloud's stored CRS. "
                    "Reproject the point cloud with QGIS first; assigning a CRS does not transform coordinates."
                )
        crs = native_crs
    elif layer_crs.isValid():
        if assigned_crs.isValid() and not _same_horizontal_crs(assigned_crs, layer_crs):
            raise QgsProcessingException(
                "The assigned CRS conflicts with the input point cloud layer CRS."
            )
        crs = layer_crs
        reader["override_srs"] = crs.toWkt()
    elif assigned_crs.isValid():
        crs = assigned_crs
        reader["override_srs"] = crs.toWkt()
    else:
        raise QgsProcessingException(
            "The point cloud has no CRS. Assign its actual CRS in the input parameters."
        )
    if crs.isGeographic():
        raise QgsProcessingException(
            "Use a projected point cloud CRS; angular coordinates are unsuitable for forest metrics."
        )

    bounds = None
    filters = []
    if _provided(parameters, "BOUNDS"):
        rectangle = algorithm.parameterAsExtent(parameters, "BOUNDS", context, crs)
        if rectangle.isEmpty() or not rectangle.isFinite():
            raise QgsProcessingException(
                "The processing extent must have finite, nonzero width and height."
            )
        bounds = (
            [rectangle.xMinimum(), rectangle.xMaximum()],
            [rectangle.yMinimum(), rectangle.yMaximum()],
        )
        value = f"([{bounds[0][0]},{bounds[0][1]}],[{bounds[1][0]},{bounds[1][1]}])"
        if reader["type"] in ("readers.ept", "readers.copc"):
            reader["bounds"] = value
        else:
            filters.append({"type": "filters.crop", "bounds": value})
    polygon = _crop_polygon(algorithm, parameters, context, crs)
    if polygon:
        filters.append({"type": "filters.crop", "polygon": polygon})
    if hag_method is None:
        hag_method = ["none", "delaunay", "dtm"][
            algorithm.parameterAsEnum(parameters, "HAG_METHOD", context)
        ]
    if hag_method not in ("none", "delaunay", "dtm"):
        raise QgsProcessingException("Unknown height above ground method.")
    dtm_path = None
    if hag_method == "delaunay":
        if "Classification" not in info.get("dimensions", ""):
            raise QgsProcessingException(
                "Delaunay height above ground requires points classified as ground (class 2)."
            )
        filters.append({"type": "filters.hag_delaunay"})
    elif hag_method == "dtm":
        dtm_path = _dtm_path(algorithm, parameters, context, crs)
        # Older supported PDAL builds lack nodata_hag. Validate every sampled
        # terrain value explicitly instead of accepting PDAL's zero-height fallback.
        filters.append({"type": "filters.hag_dem", "raster": dtm_path})
    return PreparedPointCloud(
        path,
        crs.toWkt(),
        crs.authid(),
        bounds,
        polygon,
        reader,
        tuple(filters),
        hag_method,
        dtm_path,
    )


def with_point_cloud_bounds(prepared, bounds):
    """Replace the read window while retaining polygon/HAG stages for a buffered tile."""
    reader = dict(prepared.reader)
    filters = [
        dict(stage)
        for stage in prepared.filters
        if not (stage.get("type") == "filters.crop" and "bounds" in stage)
    ]
    value = f"([{bounds[0][0]},{bounds[0][1]}],[{bounds[1][0]},{bounds[1][1]}])"
    if reader["type"] in ("readers.ept", "readers.copc"):
        reader["bounds"] = value
    else:
        filters.insert(0, {"type": "filters.crop", "bounds": value})
    return replace(prepared, reader=reader, filters=tuple(filters), bounds=bounds)


def validate_points(points, prepared):
    """Reject empty/invalid heights, including terrain NoData and uncovered points."""
    import numpy as np

    if points.size == 0:
        raise QgsProcessingException(
            "No points were returned for the selected source and crop."
        )
    if not all(np.isfinite(points[name]).all() for name in ("X", "Y", "Z")):
        raise QgsProcessingException("The point cloud contains nonfinite coordinates.")
    if prepared.hag_method != "none":
        if "HeightAboveGround" not in (points.dtype.names or ()):
            raise QgsProcessingException("HeightAboveGround was not produced by PDAL.")
        if (
            not np.isfinite(points["HeightAboveGround"]).all()
            or (points["HeightAboveGround"] <= -1e29).any()
        ):
            raise QgsProcessingException(
                "HeightAboveGround contains invalid heights. Check the terrain raster coverage and NoData."
            )
    if prepared.hag_method == "delaunay":
        ground = points[points["Classification"] == 2]
        if ground.size < 3:
            raise QgsProcessingException(
                "Delaunay height above ground requires at least three ground points (class 2) in the processing area."
            )
        xy = np.column_stack((ground["X"], ground["Y"]))
        if np.linalg.matrix_rank(xy - xy[0]) < 2:
            raise QgsProcessingException(
                "Delaunay height above ground requires ground points that are not all on one line."
            )
    if prepared.dtm_path:
        import rasterio

        with rasterio.open(prepared.dtm_path) as raster:
            # Check actual point locations, rather than rejecting holes elsewhere in the DTM.
            for start in range(0, points.size, 65536):
                chunk = points[start : start + 65536]
                rows, columns = rasterio.transform.rowcol(
                    raster.transform, chunk["X"], chunk["Y"]
                )
                rows, columns = np.asarray(rows), np.asarray(columns)
                if (
                    (rows < 0)
                    | (columns < 0)
                    | (rows >= raster.height)
                    | (columns >= raster.width)
                ).any():
                    raise QgsProcessingException(
                        "The terrain raster must cover every processed point with valid band 1 elevations."
                    )
                locations = zip(chunk["X"], chunk["Y"])
                values = np.ma.vstack(
                    list(raster.sample(locations, indexes=1, masked=True))
                )
                if (
                    np.ma.getmaskarray(values).any()
                    or not np.isfinite(values.data).all()
                ):
                    raise QgsProcessingException(
                        "The terrain raster must cover every processed point with valid band 1 elevations."
                    )


def read_point_cloud(prepared, feedback, allow_empty=False):
    import numpy as np
    import pdal

    if feedback.isCanceled():
        raise QgsProcessingException("Processing canceled.")
    feedback.pushInfo("Reading full-resolution point cloud data.")
    try:
        pipeline = pdal.Pipeline(json.dumps(prepared.stages))
        pipeline.execute()
        arrays = pipeline.arrays
    except RuntimeError as exc:
        raise QgsProcessingException(f"Point cloud processing failed: {exc}") from exc
    if feedback.isCanceled():
        raise QgsProcessingException("Processing canceled.")
    if not arrays:
        if allow_empty:
            return np.empty(0, dtype=[])
        raise QgsProcessingException(
            "No points were returned for the selected source and crop."
        )
    points = arrays[0] if len(arrays) == 1 else np.concatenate(arrays)
    if allow_empty and points.size == 0:
        return points
    validate_points(points, prepared)
    return points
