import os

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputString,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
)

from .common import (
    ensure_output_folder,
    extent_to_ept_bounds,
    import_tiled_runtime,
    interpolation_value,
    qgis_crs_to_string,
    raise_for_missing_dependencies,
    raster_layer_path,
)


class ProcessEptTilesAlgorithm(QgsProcessingAlgorithm):
    EPT_INPUT = "EPT_INPUT"
    SRS = "SRS"
    BOUNDS = "BOUNDS"
    TILE_WIDTH = "TILE_WIDTH"
    TILE_HEIGHT = "TILE_HEIGHT"
    BUFFER_FRACTION = "BUFFER_FRACTION"
    METRIC = "METRIC"
    VOXEL_XY = "VOXEL_XY"
    VOXEL_Z = "VOXEL_Z"
    DTM = "DTM"
    HAG_METHOD = "HAG_METHOD"
    INTERPOLATION = "INTERPOLATION"
    REMOVE_OUTLIERS = "REMOVE_OUTLIERS"
    OUTPUT_FOLDER = "OUTPUT_FOLDER"
    OUTPUT_FILES = "OUTPUT_FILES"

    METRIC_OPTIONS = ["CHM", "PAI", "FHD"]
    HAG_OPTIONS = ["none", "delaunay", "dtm"]
    INTERPOLATION_OPTIONS = ["none", "linear", "nearest", "cubic"]

    def name(self):
        return "processepttiles"

    def displayName(self):
        return "Process EPT Tiles"

    def group(self):
        return "PyForestScan"

    def groupId(self):
        return "pyforestscan"

    def shortHelpString(self):
        return (
            "Processes large EPT inputs in tiles using PyForestScan's tiled backend. "
            "Current PyForestScan core support is CHM, PAI, and FHD."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterString(
                self.EPT_INPUT,
                "EPT ept.json path or URL",
            )
        )
        self.addParameter(
            QgsProcessingParameterCrs(
                self.SRS,
                "SRS override",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterExtent(
                self.BOUNDS,
                "Optional bounds",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.TILE_WIDTH,
                "Tile width",
                QgsProcessingParameterNumber.Type.Double,
                defaultValue=100.0,
                minValue=0.000001,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.TILE_HEIGHT,
                "Tile height",
                QgsProcessingParameterNumber.Type.Double,
                defaultValue=100.0,
                minValue=0.000001,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.BUFFER_FRACTION,
                "Buffer fraction",
                QgsProcessingParameterNumber.Type.Double,
                defaultValue=0.1,
                minValue=0.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.METRIC,
                "Metric",
                self.METRIC_OPTIONS,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.VOXEL_XY,
                "XY grid cell size",
                QgsProcessingParameterNumber.Type.Double,
                defaultValue=1.0,
                minValue=0.000001,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.VOXEL_Z,
                "Height bin size",
                QgsProcessingParameterNumber.Type.Double,
                defaultValue=1.0,
                minValue=0.000001,
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
            QgsProcessingParameterEnum(
                self.INTERPOLATION,
                "CHM interpolation",
                self.INTERPOLATION_OPTIONS,
                defaultValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.REMOVE_OUTLIERS,
                "Remove outliers",
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUTPUT_FOLDER,
                "Output folder",
            )
        )
        self.addOutput(QgsProcessingOutputString(self.OUTPUT_FILES, "Created tile rasters"))

    def processAlgorithm(self, parameters, context, feedback):
        raise_for_missing_dependencies(QgsProcessingException)
        process = import_tiled_runtime()

        ept_input = self.parameterAsString(parameters, self.EPT_INPUT, context).strip()
        if not ept_input:
            raise QgsProcessingException("EPT input is required.")

        output_folder = ensure_output_folder(
            self.parameterAsString(parameters, self.OUTPUT_FOLDER, context)
        )
        srs = qgis_crs_to_string(self.parameterAsCrs(parameters, self.SRS, context))
        bounds = None
        if parameters.get(self.BOUNDS):
            bounds = extent_to_ept_bounds(self.parameterAsExtent(parameters, self.BOUNDS, context))

        hag_method = self.HAG_OPTIONS[self.parameterAsEnum(parameters, self.HAG_METHOD, context)]
        dtm_layer = self.parameterAsRasterLayer(parameters, self.DTM, context)
        dtm_path = raster_layer_path(dtm_layer)
        if hag_method == "dtm" and not dtm_path:
            raise QgsProcessingException("A DTM raster is required when HAG method is 'dtm'.")

        metric = self.METRIC_OPTIONS[self.parameterAsEnum(parameters, self.METRIC, context)].lower()
        interpolation_label = self.INTERPOLATION_OPTIONS[
            self.parameterAsEnum(parameters, self.INTERPOLATION, context)
        ]

        if feedback.isCanceled():
            return {self.OUTPUT_FOLDER: output_folder, self.OUTPUT_FILES: ""}

        feedback.pushInfo("Processing EPT tiles with PyForestScan. Progress is reported by PyForestScan core.")
        process.process_with_tiles(
            ept_file=ept_input,
            tile_size=(
                self.parameterAsDouble(parameters, self.TILE_WIDTH, context),
                self.parameterAsDouble(parameters, self.TILE_HEIGHT, context),
            ),
            output_path=output_folder,
            metric=metric,
            voxel_size=(
                self.parameterAsDouble(parameters, self.VOXEL_XY, context),
                self.parameterAsDouble(parameters, self.VOXEL_XY, context),
                self.parameterAsDouble(parameters, self.VOXEL_Z, context),
            ),
            buffer_size=self.parameterAsDouble(parameters, self.BUFFER_FRACTION, context),
            srs=srs,
            hag=hag_method == "delaunay",
            hag_dtm=hag_method == "dtm",
            dtm=dtm_path,
            bounds=bounds,
            interpolation=interpolation_value(interpolation_label),
            remove_outliers=self.parameterAsBool(parameters, self.REMOVE_OUTLIERS, context),
        )

        created_files = [
            os.path.join(output_folder, filename)
            for filename in sorted(os.listdir(output_folder))
            if filename.lower().endswith((".tif", ".tiff"))
        ]
        return {
            self.OUTPUT_FOLDER: output_folder,
            self.OUTPUT_FILES: "\n".join(created_files),
        }

    def createInstance(self):
        return ProcessEptTilesAlgorithm()
