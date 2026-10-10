"""Resolve QGIS inputs once for the native backend or isolated processing engine."""

from dataclasses import dataclass, field, replace
import os
from urllib.parse import unquote, urlsplit

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProcessing,
    QgsProcessingException,
    QgsProcessingParameterCrs,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterPointCloudLayer,
    QgsProcessingParameterString,
    QgsProviderRegistry,
)

from .metric_engine import MISSING_HAG_MESSAGE


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
    backend: str = "native"
    info: dict = field(default_factory=dict)

    @property
    def stages(self):
        return [dict(self.reader), *(dict(stage) for stage in self.filters)]


def add_point_cloud_parameters(
    algorithm, crop=True, input_name="INPUT", crs_name="INPUT_CRS"
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


def _metadata(reader, backend, context, feedback):
    try:
        if backend == "native":
            from .native_cloud import native_metadata

            return native_metadata(
                reader["filename"], reader["type"], context, feedback
            )
        from ..runtime.client import call_engine

        info = call_engine("metadata", {"reader": reader}, feedback=feedback)
        # Accept a reader preview directly, as well as PDAL's wrapped quickinfo.
        return (
            info if "srs" in info or "num_points" in info else next(iter(info.values()))
        )
    except (RuntimeError, ValueError, StopIteration, TypeError, OSError) as exc:
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
    a = first.horizontalCrs()
    b = second.horizontalCrs()
    return (a if a.isValid() else first) == (b if b.isValid() else second)


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
    from osgeo import gdal

    raster = gdal.OpenEx(path, gdal.OF_RASTER | gdal.OF_READONLY)
    if raster is None:
        raise QgsProcessingException("The terrain raster could not be opened by GDAL.")
    try:
        stored_crs = QgsCoordinateReferenceSystem(raster.GetProjectionRef())
        if not stored_crs.isValid() or not _same_horizontal_crs(stored_crs, crs):
            raise QgsProcessingException(
                "The terrain raster's stored CRS must match the point cloud; assigning a layer CRS does not reproject its data."
            )
        from .common import require_metric_crs

        require_metric_crs(stored_crs)
        unit = raster.GetRasterBand(1).GetUnitType().strip().lower()
        if unit and unit not in ("m", "metre", "meter", "metres", "meters"):
            raise QgsProcessingException(
                f"The terrain raster band 1 declares elevation units '{unit}'. "
                "Convert terrain elevations to metres before calculating height above ground."
            )
    finally:
        raster = None
    return path


def prepare_point_cloud(
    algorithm,
    parameters,
    context,
    feedback,
    hag_method="none",
    input_name="INPUT",
    crs_name="INPUT_CRS",
    backend="native",
):
    """Call from prepareAlgorithm so QGIS layers never cross the worker boundary."""
    if feedback.isCanceled():
        raise QgsProcessingException("Processing canceled.")
    if backend not in ("native", "engine"):
        raise QgsProcessingException("Unknown point cloud processing backend.")
    if hag_method not in ("none", "delaunay", "dtm"):
        raise QgsProcessingException("Unknown height above ground method.")
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
    info = _metadata(reader, backend, context, feedback)
    dimensions = info.get("dimensions") or ()
    if isinstance(dimensions, str):
        dimensions = {name.strip() for name in dimensions.split(",")}
    if hag_method == "none" and "HeightAboveGround" not in dimensions:
        raise QgsProcessingException(MISSING_HAG_MESSAGE)
    if backend == "native" and reader["type"] == "readers.ept":
        feedback.pushInfo(
            "QGIS stages EPT points through LAS. Floating-coordinate EPT without stored "
            "LAS scales may be rounded to 0.01 m; select the isolated engine in "
            "PyForestScan setup when finer source precision is required."
        )
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
    dtm_path = None
    if hag_method == "delaunay":
        if "Classification" not in dimensions:
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
        backend,
        info,
    )


def with_point_cloud_bounds(prepared, bounds):
    """Replace the read window while retaining other stages for a buffered tile."""
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


def validate_points(points, prepared, feedback=None):
    """Reject empty/invalid heights, including terrain NoData and uncovered points."""
    import numpy as np

    if points.size == 0:
        raise QgsProcessingException(
            "No points were returned for the selected source and crop."
        )
    if prepared.hag_method == "none" and "HeightAboveGround" not in (
        points.dtype.names or ()
    ):
        raise QgsProcessingException(MISSING_HAG_MESSAGE)
    if not all(np.isfinite(points[name]).all() for name in ("X", "Y", "Z")):
        raise QgsProcessingException("The point cloud contains nonfinite coordinates.")
    if prepared.hag_method != "none":
        if "HeightAboveGround" not in (points.dtype.names or ()):
            raise QgsProcessingException(
                "HeightAboveGround was not produced by the point cloud backend."
            )
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
        from .native_cloud import sample_terrain

        sample_terrain(prepared.dtm_path, points["X"], points["Y"], feedback)


def read_point_cloud(prepared, feedback, allow_empty=False, context=None):
    if feedback.isCanceled():
        raise QgsProcessingException("Processing canceled.")
    feedback.pushInfo("Reading full-resolution point cloud data.")
    if prepared.backend != "native":
        raise QgsProcessingException(
            "Engine point arrays must be processed inside the isolated engine job."
        )
    from .native_cloud import read_native

    try:
        return read_native(prepared, feedback, context=context, allow_empty=allow_empty)
    except (RuntimeError, ValueError, OSError) as exc:
        raise QgsProcessingException(
            f"Native point cloud processing failed: {exc}"
        ) from exc
