"""QGIS-native point-cloud jobs and a pure-Python LAS/NumPy bridge.

Compiled geospatial libraries are supplied by QGIS. No Python PDAL bindings,
laspy compression extension, rasterio, or pyproj are imported here.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import tempfile

from qgis.core import (
    QgsApplication,
    QgsPointCloudLayer,
    QgsProcessingContext,
    QgsProcessingException,
)


HAG_ALGORITHM = "pdal:heightabovegroundtriangulation"


def _canceled(feedback):
    if feedback is not None and feedback.isCanceled():
        raise QgsProcessingException("Processing canceled.")


@contextmanager
def _uncached_source(path, reader_type, feedback):
    """Stop native algorithms silently substituting QGIS's sibling COPC cache."""
    if reader_type == "readers.copc" or not os.path.isfile(path):
        yield path
        return
    cache = Path(path).with_suffix(".copc.laz")
    if not cache.is_file():
        yield path
        return
    if reader_type == "readers.ept":
        raise QgsProcessingException(
            "This local EPT source has a sibling COPC index which QGIS would process "
            "instead of the selected source. Select Isolated engine in PyForestScan "
            "setup to read the original EPT data."
        )
    _canceled(feedback)
    with tempfile.TemporaryDirectory(prefix="pyforestscan-source-") as folder:
        alias = str(Path(folder) / ("source" + Path(path).suffix))
        try:
            os.link(path, alias)
        except OSError:
            # A different filesystem or a restricted Windows account may not
            # permit hard links. The private copy still leaves the source intact.
            shutil.copyfile(path, alias)
        _canceled(feedback)
        yield alias


def _source_layer(path, reader_type):
    """Create a thread-local source, retaining PDAL-readable EPT schema support."""
    provider_name = {
        "readers.las": "pdal",
        "readers.copc": "copc",
        "readers.ept": "ept",
    }[reader_type]
    options = QgsPointCloudLayer.LayerOptions()
    options.skipIndexGeneration = True
    options.loadDefaultStyle = False
    layer = QgsPointCloudLayer(path, "PyForestScan input", provider_name, options)
    if not layer.isValid() and provider_name != "pdal":
        # The dedicated EPT renderer rejects some valid floating-coordinate EPT
        # schemas which PDAL can still process at full resolution.
        layer = QgsPointCloudLayer(path, "PyForestScan input", "pdal", options)
    provider = layer.dataProvider()
    if provider is None or not layer.isValid():
        detail = (
            layer.error().message()
            if layer.error().message()
            else "the source could not be opened"
        )
        raise QgsProcessingException(
            f"QGIS could not read this point cloud: {detail}. "
            "Check the source, or select the optional processing engine in PyForestScan setup."
        )
    return layer


def native_metadata(path, reader_type, context=None, feedback=None):
    """Read metadata from a fresh provider, without starting point-cloud indexing."""
    _canceled(feedback)
    with _uncached_source(path, reader_type, feedback) as source:
        return _metadata_from_path(source, reader_type)


def _metadata_from_path(path, reader_type):
    layer = _source_layer(path, reader_type)
    provider = layer.dataProvider()
    rectangle = layer.extent()
    crs = provider.crs()
    attributes = provider.attributes().attributes()
    return {
        "num_points": int(provider.pointCount()),
        "dimensions": ", ".join(attribute.name() for attribute in attributes),
        "srs": {"wkt": crs.toWkt() if crs.isValid() else ""},
        "bounds": {
            "minx": rectangle.xMinimum(),
            "maxx": rectangle.xMaximum(),
            "miny": rectangle.yMinimum(),
            "maxy": rectangle.yMaximum(),
        },
    }


def _run(algorithm_id, parameters, context, feedback):
    """Run a child with thread-local source layers, rather than project-owned layers."""
    from .point_cloud import point_cloud_reader

    parameters = dict(parameters)
    reader_type = point_cloud_reader(parameters["INPUT"])
    with _uncached_source(parameters["INPUT"], reader_type, feedback) as source:
        parameters["INPUT"] = source
        return _run_source(algorithm_id, parameters, context, feedback, reader_type)


def _run_source(algorithm_id, parameters, context, feedback, reader_type):
    _canceled(feedback)
    algorithm = QgsApplication.processingRegistry().createAlgorithmById(algorithm_id)
    if algorithm is None:
        raise QgsProcessingException(
            f"QGIS does not provide {algorithm_id}. Enable the QGIS PDAL Processing provider "
            "or select the optional processing engine in PyForestScan setup."
        )
    child = QgsProcessingContext()
    if context is not None:
        child.setTransformContext(context.transformContext())
    source = _source_layer(parameters["INPUT"], reader_type)
    parameters["INPUT"] = source
    valid, message = algorithm.checkParameterValues(parameters, child)
    if not valid:
        raise QgsProcessingException(
            f"Native point cloud parameters are invalid: {message}"
        )
    result, ok = algorithm.run(parameters, child, feedback)
    _canceled(feedback)
    if not ok:
        raise QgsProcessingException(
            f"QGIS {algorithm.displayName()} failed. See the Processing log for details."
        )
    output = result.get("OUTPUT", parameters.get("OUTPUT"))
    if not output or not os.path.isfile(output):
        raise QgsProcessingException(
            f"QGIS {algorithm.displayName()} did not create its output."
        )
    return output


def _crop_file(polygon, bounds, crs_wkt, folder):
    from osgeo import ogr, osr

    geometry = ogr.CreateGeometryFromWkt(polygon) if polygon else None
    if bounds is not None:
        (xmin, xmax), (ymin, ymax) = bounds
        rectangle = ogr.CreateGeometryFromWkt(
            f"POLYGON (({xmin} {ymin}, {xmax} {ymin}, {xmax} {ymax}, "
            f"{xmin} {ymax}, {xmin} {ymin}))"
        )
        geometry = geometry.Intersection(rectangle) if geometry else rectangle
    if geometry is None or geometry.IsEmpty() or geometry.GetDimension() < 2:
        return None
    if ogr.GT_Flatten(geometry.GetGeometryType()) == ogr.wkbGeometryCollection:
        polygons = ogr.Geometry(ogr.wkbMultiPolygon)

        def collect_polygons(part):
            if ogr.GT_Flatten(part.GetGeometryType()) == ogr.wkbPolygon:
                polygons.AddGeometry(part)
            else:
                for child in part:
                    collect_polygons(child)

        collect_polygons(geometry)
        geometry = polygons
        if geometry.IsEmpty():
            return None
    path = str(Path(folder) / "crop.gpkg")
    dataset = ogr.GetDriverByName("GPKG").CreateDataSource(path)
    if dataset is None:
        raise QgsProcessingException("Could not create the temporary crop polygon.")
    srs = osr.SpatialReference()
    srs.ImportFromWkt(crs_wkt)
    layer = dataset.CreateLayer("crop", srs=srs, geom_type=ogr.wkbUnknown)
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(geometry)
    if layer.CreateFeature(feature) != 0:
        raise QgsProcessingException("Could not write the temporary crop polygon.")
    feature = None
    layer = None
    dataset = None
    return path


def _las_module():
    from ..vendor import laspy

    return laspy


def las_to_points(las):
    """Map scaled LAS coordinates and standard dimension names to the PDAL schema."""
    import numpy as np

    names = {
        "intensity": "Intensity",
        "return_number": "ReturnNumber",
        "number_of_returns": "NumberOfReturns",
        "scan_direction_flag": "ScanDirectionFlag",
        "edge_of_flight_line": "EdgeOfFlightLine",
        "classification": "Classification",
        "synthetic": "Synthetic",
        "key_point": "KeyPoint",
        "withheld": "Withheld",
        "overlap": "Overlap",
        "scanner_channel": "ScanChannel",
        "scan_angle_rank": "ScanAngleRank",
        "scan_angle": "ScanAngleRank",
        "user_data": "UserData",
        "point_source_id": "PointSourceId",
        "gps_time": "GpsTime",
        "red": "Red",
        "green": "Green",
        "blue": "Blue",
        "nir": "Infrared",
        "wavepacket_index": "WavePacketDescriptorIndex",
        "wavepacket_offset": "ByteOffsetToWaveformData",
        "wavepacket_size": "WaveformPacketSizeInBytes",
        "return_point_wave_location": "ReturnPointWaveformLocation",
        "x_t": "Xt",
        "y_t": "Yt",
        "z_t": "Zt",
    }
    arrays = {
        name: np.asarray(getattr(las, name.lower()), dtype=np.float64)
        for name in ("X", "Y", "Z")
    }
    for name in las.point_format.dimension_names:
        if name in ("X", "Y", "Z"):
            continue
        values = np.asarray(las[name])
        if name == "scan_angle":
            values = values.astype(np.float64) * 0.006
        arrays[names.get(name, name)] = values
    dtype = []
    for name, values in arrays.items():
        dtype.append(
            (name, values.dtype, values.shape[1:])
            if values.ndim > 1
            else (name, values.dtype)
        )
    points = np.empty(len(las.points), dtype=dtype)
    for name, values in arrays.items():
        points[name] = values
    return points


def sample_terrain(path, x, y, feedback=None):
    """Sample GDAL band 1 at the containing pixel, validating coverage and NoData."""
    import numpy as np
    from osgeo import gdal
    from .raster_validation import validate_elevation_band

    dataset = gdal.OpenEx(path, gdal.OF_RASTER | gdal.OF_READONLY)
    if dataset is None or dataset.RasterCount < 1:
        raise QgsProcessingException("The terrain raster could not be opened by GDAL.")
    try:
        inverse = gdal.InvGeoTransform(dataset.GetGeoTransform())
        if inverse is None:
            raise QgsProcessingException(
                "The terrain raster has an invalid geotransform."
            )
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        if not (np.isfinite(x).all() and np.isfinite(y).all()):
            raise QgsProcessingException("Point cloud coordinates must be finite.")
        columns = np.floor(inverse[0] + inverse[1] * x + inverse[2] * y).astype(
            np.int64
        )
        rows = np.floor(inverse[3] + inverse[4] * x + inverse[5] * y).astype(np.int64)
        if (
            (rows < 0)
            | (columns < 0)
            | (rows >= dataset.RasterYSize)
            | (columns >= dataset.RasterXSize)
        ).any():
            raise QgsProcessingException(
                "The terrain raster must cover every processed point with valid band 1 elevations."
            )
        band = dataset.GetRasterBand(1)
        validate_elevation_band(band, "Terrain raster band 1", "terrain")
        nodata = band.GetNoDataValue()
        mask = None if band.GetMaskFlags() & gdal.GMF_ALL_VALID else band.GetMaskBand()
        block_width, block_height = band.GetBlockSize()
        block_width, block_height = max(1, block_width), max(1, block_height)
        values = np.empty(x.size, dtype=np.float64)
        # Group by GDAL block so a million points do not trigger a million reads.
        block_columns = columns // block_width
        block_rows = rows // block_height
        blocks_across = (dataset.RasterXSize + block_width - 1) // block_width
        codes = block_rows * blocks_across + block_columns
        order = np.argsort(codes, kind="stable")
        boundaries = np.r_[0, np.flatnonzero(np.diff(codes[order])) + 1, len(order)]
        for start, end in zip(boundaries[:-1], boundaries[1:]):
            _canceled(feedback)
            indexes = order[start:end]
            if not indexes.size:
                continue
            left = int(block_columns[indexes[0]] * block_width)
            top = int(block_rows[indexes[0]] * block_height)
            width = min(block_width, dataset.RasterXSize - left)
            height = min(block_height, dataset.RasterYSize - top)
            block = band.ReadAsArray(left, top, width, height)
            if block is None:
                raise QgsProcessingException(
                    "Could not read terrain raster elevations."
                )
            sampled = block[rows[indexes] - top, columns[indexes] - left]
            valid = np.isfinite(sampled)
            if nodata is not None:
                valid &= sampled != nodata
            if mask is not None:
                mask_values = mask.ReadAsArray(left, top, width, height)
                if mask_values is None:
                    raise QgsProcessingException(
                        "Could not read the terrain raster validity mask."
                    )
                valid &= mask_values[rows[indexes] - top, columns[indexes] - left] != 0
            if not valid.all():
                raise QgsProcessingException(
                    "The terrain raster must cover every processed point with valid band 1 elevations."
                )
            values[indexes] = sampled
        return values
    finally:
        dataset = None


def _set_header_crs(header, crs_wkt):
    """Write WKT directly; laspy's add_crs helper would otherwise require pyproj."""
    from ..vendor.laspy.vlrs.known import WktCoordinateSystemVlr

    # LAS 1.4 formally defines both WKT and the Extra Bytes dimension records.
    header.version = _las_module().header.Version(1, 4)
    header.vlrs[:] = [
        vlr
        for vlr in header.vlrs
        if not (vlr.user_id == "LASF_Projection" and vlr.record_id == 2112)
    ]
    header.vlrs.append(WktCoordinateSystemVlr(crs_wkt))
    header.global_encoding.wkt = True


def _assign_crs(path, crs_wkt, output, feedback):
    """Assign a missing source CRS to a private copy using bounded memory."""
    laspy = _las_module()
    with laspy.open(path) as reader:
        header = reader.header.copy()
        _set_header_crs(header, crs_wkt)
        with laspy.open(output, mode="w", header=header, do_compress=False) as writer:
            for chunk in reader.chunk_iterator(250_000):
                _canceled(feedback)
                writer.write_points(chunk)
            if reader.header.evlrs:
                writer.write_evlrs(reader.header.evlrs)
    return output


def _check_ground(path, feedback):
    import numpy as np

    laspy = _las_module()
    seen = 0
    first = None
    direction = None
    with laspy.open(path) as reader:
        for chunk in reader.chunk_iterator(250_000):
            _canceled(feedback)
            ground = np.asarray(chunk.classification) == 2
            xy = np.column_stack(
                (np.asarray(chunk.x)[ground], np.asarray(chunk.y)[ground])
            )
            seen += len(xy)
            if not len(xy):
                continue
            if first is None:
                first = xy[0]
            shifted = xy - first
            if direction is None:
                distinct = np.flatnonzero(np.any(shifted != 0, axis=1))
                if distinct.size:
                    direction = shifted[distinct[0]]
            if direction is not None:
                cross = shifted[:, 0] * direction[1] - shifted[:, 1] * direction[0]
                if np.any(cross != 0):
                    return
    reason = (
        "at least three ground points (class 2)"
        if seen < 3
        else "ground points that are not all on one line"
    )
    raise QgsProcessingException(
        f"Delaunay height above ground requires {reason} in the processing area."
    )


@contextmanager
def materialize_native(prepared, context, feedback):
    """Yield an uncompressed LAS while keeping all intermediate files private."""
    laspy = _las_module()
    _canceled(feedback)
    unsupported = [
        stage["type"]
        for stage in prepared.filters
        if stage["type"]
        not in ("filters.crop", "filters.hag_dem", "filters.hag_delaunay")
    ]
    if unsupported:
        raise QgsProcessingException(
            "This operation requires the optional processing engine: "
            + ", ".join(unsupported)
        )
    with tempfile.TemporaryDirectory(prefix="pyforestscan-native-") as folder:
        path = prepared.path
        crop = None
        if prepared.polygon or prepared.bounds is not None:
            # Native FILTER_EXTENT uses six significant figures in some QGIS
            # releases, collapsing metre/submetre extents at UTM coordinates.
            # Wrench 4.2 clip derives exact reader bounds from this polygon,
            # retaining efficient spatial queries for COPC/EPT inputs.
            crop = _crop_file(
                prepared.polygon, prepared.bounds, prepared.crs_wkt, folder
            )
            if crop is None:
                empty = laspy.create(point_format=6, file_version="1.4")
                _set_header_crs(empty.header, prepared.crs_wkt)
                path = str(Path(folder) / "empty.las")
                empty.write(path)
                yield path
                return
        if prepared.reader.get("override_srs"):
            if not (os.path.isfile(path) and path.lower().endswith(".las")):
                path = _run(
                    "pdal:convertformat",
                    {"INPUT": path, "OUTPUT": str(Path(folder) / "unassigned.las")},
                    context,
                    feedback,
                )
            else:
                with laspy.open(path) as reader:
                    compressed = reader.header.are_points_compressed
                if compressed:
                    path = _run(
                        "pdal:convertformat",
                        {"INPUT": path, "OUTPUT": str(Path(folder) / "unassigned.las")},
                        context,
                        feedback,
                    )
            path = _assign_crs(
                path, prepared.crs_wkt, str(Path(folder) / "assigned.las"), feedback
            )
        if crop:
            path = _run(
                "pdal:clip",
                {
                    "INPUT": path,
                    "OVERLAY": crop,
                    "OUTPUT": str(Path(folder) / "cropped.las"),
                },
                context,
                feedback,
            )
        elif not (os.path.isfile(path) and path.lower().endswith(".las")):
            path = _run(
                "pdal:convertformat",
                {"INPUT": path, "OUTPUT": str(Path(folder) / "converted.las")},
                context,
                feedback,
            )
        else:
            with laspy.open(path) as source:
                compressed = source.header.are_points_compressed
            if compressed:
                path = _run(
                    "pdal:convertformat",
                    {"INPUT": path, "OUTPUT": str(Path(folder) / "converted.las")},
                    context,
                    feedback,
                )
        with laspy.open(path) as reader:
            count = reader.header.point_count
        if count and prepared.hag_method == "delaunay":
            _check_ground(path, feedback)
            path = _run(
                HAG_ALGORITHM,
                {
                    "INPUT": path,
                    "REPLACE_Z": False,
                    "COUNT": 10,
                    "OUTPUT": str(Path(folder) / "height.las"),
                },
                context,
                feedback,
            )
        if count and prepared.hag_method == "dtm":
            import numpy as np

            las = laspy.read(path)
            terrain = sample_terrain(prepared.dtm_path, las.x, las.y, feedback)
            heights = np.asarray(las.z) - terrain
            heights[np.asarray(las.classification) == 2] = 0.0
            if "HeightAboveGround" in las.point_format.dimension_names:
                # An existing integer or scaled extra dimension must not
                # silently round the newly calculated fractional heights.
                las.remove_extra_dim("HeightAboveGround")
            las.add_extra_dim(
                laspy.ExtraBytesParams(name="HeightAboveGround", type=np.float64)
            )
            las["HeightAboveGround"] = heights
            _set_header_crs(las.header, prepared.crs_wkt)
            path = str(Path(folder) / "height.las")
            las.write(path)
        _canceled(feedback)
        yield path


def read_native(prepared, feedback, context=None, allow_empty=False):
    from .point_cloud import validate_points

    with materialize_native(prepared, context, feedback) as path:
        points = las_to_points(_las_module().read(path))
        if points.size == 0 and allow_empty:
            return points
        validate_points(points, prepared, feedback=feedback)
        _canceled(feedback)
        return points


def write_native_hag(prepared, output, context, feedback):
    """Publish a complete LAS/LAZ atomically, retaining all source dimensions."""
    from .point_cloud import validate_points

    laspy = _las_module()
    with materialize_native(prepared, context, feedback) as path:
        las = laspy.read(path)
        points = las_to_points(las)
        validate_points(points, prepared, feedback=feedback)
        with tempfile.TemporaryDirectory(prefix="pyforestscan-publish-") as folder:
            staged = str(Path(folder) / "height.las")
            _set_header_crs(las.header, prepared.crs_wkt)
            las.write(staged)
            if output.lower().endswith(".laz"):
                staged = _run(
                    "pdal:convertformat",
                    {"INPUT": staged, "OUTPUT": str(Path(folder) / "height.laz")},
                    context,
                    feedback,
                )
            _canceled(feedback)
            directory = os.path.dirname(os.path.abspath(output))
            os.makedirs(directory, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=".pyforestscan-hag-", suffix=Path(output).suffix, dir=directory
            )
            os.close(descriptor)
            try:
                shutil.copyfile(staged, temporary)
                _canceled(feedback)
                os.replace(temporary, output)
            finally:
                if os.path.exists(temporary):
                    os.remove(temporary)
        return {"output": output, "count": len(points)}
