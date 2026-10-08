import csv
import math
import os

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputString,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
)

from .common import (
    add_raster_to_load_on_completion,
    calculate_canopy_cover,
    calculate_point_density,
    calculate_rumple,
    ensure_output_folder,
    extent_to_ept_bounds,
    first_point_array,
    import_metric_runtime,
    interpolation_value,
    qgis_crs_to_string,
    raise_for_missing_dependencies,
    raster_layer_path,
    require_height_above_ground,
    safe_filename,
)


class CalculateLidarMetricsAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    INPUT_CRS = "INPUT_CRS"
    BOUNDS = "BOUNDS"
    POLYGON = "POLYGON"
    DTM = "DTM"
    HAG_METHOD = "HAG_METHOD"
    VOXEL_XY = "VOXEL_XY"
    VOXEL_Z = "VOXEL_Z"
    METRICS = "METRICS"
    INTERPOLATION = "INTERPOLATION"
    MIN_HEIGHT = "MIN_HEIGHT"
    COVER_MIN_HEIGHT = "COVER_MIN_HEIGHT"
    COVER_K = "COVER_K"
    OUTPUT_FOLDER = "OUTPUT_FOLDER"
    OUTPUT_FILES = "OUTPUT_FILES"
    RUMPLE_REPORT = "RUMPLE_REPORT"

    HAG_OPTIONS = ["none", "delaunay", "dtm"]
    METRIC_OPTIONS = [
        "CHM",
        "PAD",
        "PAI",
        "FHD",
        "canopy cover",
        "point density",
        "rumple",
    ]
    INTERPOLATION_OPTIONS = ["none", "linear", "nearest", "cubic"]

    def name(self):
        return "calculatelidarmetrics"

    def displayName(self):
        return "Calculate LiDAR Metrics"

    def group(self):
        return "PyForestScan"

    def groupId(self):
        return "pyforestscan"

    def shortHelpString(self):
        return (
            "Calculates common PyForestScan forest structure metrics from a local "
            "LAS/LAZ/COPC file, local EPT ept.json path, or remote EPT URL. Most height metrics require "
            "HeightAboveGround; choose Delaunay or DTM HAG if the input does not "
            "already provide it."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterString(
                self.INPUT,
                "Input point cloud source",
            )
        )
        self.addParameter(
            QgsProcessingParameterCrs(
                self.INPUT_CRS,
                "Input CRS",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterExtent(
                self.BOUNDS,
                "Optional EPT bounds",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.POLYGON,
                "Optional crop polygon",
                [QgsProcessing.TypeVectorPolygon],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.DTM,
                "Optional DTM raster for HAG",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.HAG_METHOD,
                "HAG method",
                self.HAG_OPTIONS,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.VOXEL_XY,
                "XY grid cell size",
                QgsProcessingParameterNumber.Double,
                defaultValue=1.0,
                minValue=0.000001,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.VOXEL_Z,
                "Height bin size",
                QgsProcessingParameterNumber.Double,
                defaultValue=1.0,
                minValue=0.000001,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.METRICS,
                "Metrics",
                self.METRIC_OPTIONS,
                allowMultiple=True,
                defaultValue=[0, 2, 3, 4, 5],
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.INTERPOLATION,
                "CHM interpolation",
                self.INTERPOLATION_OPTIONS,
                defaultValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MIN_HEIGHT,
                "Minimum height for PAI",
                QgsProcessingParameterNumber.Double,
                defaultValue=1.0,
                minValue=0.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.COVER_MIN_HEIGHT,
                "Canopy cover minimum height",
                QgsProcessingParameterNumber.Double,
                defaultValue=2.0,
                minValue=0.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.COVER_K,
                "Canopy cover gap-fraction exponent",
                QgsProcessingParameterNumber.Double,
                defaultValue=0.5,
                minValue=0.000001,
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUTPUT_FOLDER,
                "Output folder",
            )
        )
        self.addOutput(QgsProcessingOutputString(self.OUTPUT_FILES, "Created files"))
        self.addOutput(QgsProcessingOutputString(self.RUMPLE_REPORT, "Rumple report"))

    def processAlgorithm(self, parameters, context, feedback):
        raise_for_missing_dependencies(QgsProcessingException)
        handlers, calculate = import_metric_runtime()

        input_path = self.parameterAsString(parameters, self.INPUT, context).strip()
        if not input_path:
            raise QgsProcessingException(
                "Input point cloud source is required. Provide a LAS/LAZ/COPC path, "
                "an EPT ept.json path, or an EPT URL."
            )

        input_crs = qgis_crs_to_string(self.parameterAsCrs(parameters, self.INPUT_CRS, context))
        if not input_crs and context.project() is not None:
            input_crs = qgis_crs_to_string(context.project().crs())
        if not input_crs:
            raise QgsProcessingException("Input CRS is required when it cannot be inferred from the QGIS project.")

        output_folder = ensure_output_folder(
            self.parameterAsString(parameters, self.OUTPUT_FOLDER, context)
        )
        bounds = None
        if parameters.get(self.BOUNDS):
            bounds = extent_to_ept_bounds(self.parameterAsExtent(parameters, self.BOUNDS, context))

        hag_method = self.HAG_OPTIONS[self.parameterAsEnum(parameters, self.HAG_METHOD, context)]
        dtm_layer = self.parameterAsRasterLayer(parameters, self.DTM, context)
        dtm_path = raster_layer_path(dtm_layer)
        if hag_method == "dtm" and not dtm_path:
            raise QgsProcessingException("A DTM raster is required when HAG method is 'dtm'.")

        polygon_wkt = self._polygon_wkt(parameters, context)

        feedback.pushInfo("Reading LiDAR with PyForestScan.")
        arrays = handlers.read_lidar(
            input_file=input_path,
            srs=input_crs,
            bounds=bounds,
            hag=hag_method == "delaunay",
            hag_dtm=hag_method == "dtm",
            dtm=dtm_path,
            crop_poly=bool(polygon_wkt),
            poly=polygon_wkt,
        )
        points = first_point_array(arrays)
        if points is None or points.size == 0:
            raise QgsProcessingException("PyForestScan returned no points for the selected input and bounds.")

        metric_indexes = self.parameterAsEnums(parameters, self.METRICS, context)
        if not metric_indexes:
            raise QgsProcessingException("Select at least one metric.")
        selected_metrics = [self.METRIC_OPTIONS[index] for index in metric_indexes]

        xy_resolution = self.parameterAsDouble(parameters, self.VOXEL_XY, context)
        z_resolution = self.parameterAsDouble(parameters, self.VOXEL_Z, context)
        voxel_resolution = (xy_resolution, xy_resolution, z_resolution)
        interpolation_label = self.INTERPOLATION_OPTIONS[
            self.parameterAsEnum(parameters, self.INTERPOLATION, context)
        ]
        interpolation = interpolation_value(interpolation_label)
        min_height = self.parameterAsDouble(parameters, self.MIN_HEIGHT, context)
        min_height_index = max(0, int(math.floor(min_height / z_resolution)))
        cover_min_height = self.parameterAsDouble(parameters, self.COVER_MIN_HEIGHT, context)
        cover_k = self.parameterAsDouble(parameters, self.COVER_K, context)

        created_files = []
        rumple_report = ""
        chm = None
        chm_extent = None

        if any(metric in selected_metrics for metric in ["CHM", "rumple"]):
            require_height_above_ground(points)
            feedback.pushInfo("Calculating CHM.")
            chm, chm_extent = calculate.calculate_chm(points, voxel_resolution, interpolation=interpolation)
            if "CHM" in selected_metrics:
                created_files.append(
                    self._write_raster(
                        handlers,
                        chm,
                        output_folder,
                        "chm",
                        input_crs,
                        chm_extent,
                        context,
                    )
                )

        voxels = None
        voxel_extent = None
        pad = None
        if any(metric in selected_metrics for metric in ["PAD", "PAI", "FHD"]):
            require_height_above_ground(points)
            feedback.pushInfo("Assigning voxels.")
            voxels, voxel_extent = calculate.assign_voxels(points, voxel_resolution)

        if "PAD" in selected_metrics:
            feedback.pushInfo("Calculating PAD.")
            pad = calculate.calculate_pad(voxels, z_resolution)
            pad_file = os.path.join(output_folder, "pad.npz")
            self._save_numpy(pad_file, pad, voxel_extent, voxel_resolution)
            created_files.append(pad_file)

        if "PAI" in selected_metrics:
            feedback.pushInfo("Calculating PAI.")
            if pad is None:
                pad = calculate.calculate_pad(voxels, z_resolution)
            pai = calculate.calculate_pai(pad, min_height=min_height_index)
            created_files.append(
                self._write_raster(
                    handlers,
                    pai,
                    output_folder,
                    "pai",
                    input_crs,
                    voxel_extent,
                    context,
                )
            )

        if "FHD" in selected_metrics:
            feedback.pushInfo("Calculating FHD.")
            fhd = calculate.calculate_fhd(voxels)
            created_files.append(
                self._write_raster(
                    handlers,
                    fhd,
                    output_folder,
                    "fhd",
                    input_crs,
                    voxel_extent,
                    context,
                )
            )

        if "canopy cover" in selected_metrics:
            feedback.pushInfo("Calculating canopy cover.")
            canopy_cover, cover_extent = calculate_canopy_cover(
                points,
                xy_resolution,
                min_height=cover_min_height,
                k=cover_k,
            )
            created_files.append(
                self._write_raster(
                    handlers,
                    canopy_cover,
                    output_folder,
                    "canopy_cover",
                    input_crs,
                    cover_extent,
                    context,
                )
            )

        if "point density" in selected_metrics:
            feedback.pushInfo("Calculating point density.")
            density, density_extent = calculate_point_density(points, xy_resolution)
            created_files.append(
                self._write_raster(
                    handlers,
                    density,
                    output_folder,
                    "point_density",
                    input_crs,
                    density_extent,
                    context,
                )
            )

        if "rumple" in selected_metrics:
            feedback.pushInfo("Calculating rumple.")
            if chm is None:
                chm, chm_extent = calculate.calculate_chm(points, voxel_resolution, interpolation=interpolation)
            rumple_value = calculate_rumple(chm, xy_resolution)
            rumple_report = os.path.join(output_folder, "rumple.csv")
            with open(rumple_report, "w", newline="", encoding="utf-8") as report_file:
                writer = csv.writer(report_file)
                writer.writerow(["metric", "value"])
                writer.writerow(["rumple", rumple_value])
            created_files.append(rumple_report)

        return {
            self.OUTPUT_FOLDER: output_folder,
            self.OUTPUT_FILES: "\n".join(created_files),
            self.RUMPLE_REPORT: rumple_report,
        }

    def _polygon_wkt(self, parameters, context):
        source = self.parameterAsSource(parameters, self.POLYGON, context)
        if source is None:
            return None
        for feature in source.getFeatures():
            geometry = feature.geometry()
            if geometry and not geometry.isEmpty():
                return geometry.asWkt()
        return None

    def _write_raster(self, handlers, layer, output_folder, metric_name, crs, extent, context):
        output_file = os.path.join(output_folder, f"{safe_filename(metric_name)}.tif")
        handlers.create_geotiff(layer, output_file, crs, extent)
        add_raster_to_load_on_completion(context, output_file, metric_name.replace("_", " ").title(), self.OUTPUT_FILES)
        return output_file

    def _save_numpy(self, output_file, array, extent, voxel_resolution):
        import numpy as np

        np.savez_compressed(
            output_file,
            pad=array,
            extent=extent,
            voxel_resolution=voxel_resolution,
        )

    def createInstance(self):
        return CalculateLidarMetricsAlgorithm()
