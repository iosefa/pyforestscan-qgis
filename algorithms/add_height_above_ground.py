"""Persist height above ground as an extra dimension in a new LAS/LAZ layer."""

from dataclasses import asdict
import os

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterEnum,
    QgsProcessingParameterPointCloudDestination,
    QgsProcessingParameterRasterLayer,
)

from .common import help_url, protect_input_paths, require_metric_crs
from .point_cloud import (
    add_point_cloud_parameters,
    prepare_point_cloud,
)


class AddHeightAboveGroundAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER = "INPUT_LAYER"
    INPUT = "INPUT"
    INPUT_CRS = "INPUT_CRS"
    BOUNDS = "BOUNDS"
    POLYGON = "POLYGON"
    HAG_METHOD = "HAG_METHOD"
    DTM = "DTM"
    OUTPUT = "OUTPUT"
    HAG_OPTIONS = ["delaunay", "dtm"]

    def name(self):
        return "addheightaboveground"

    def displayName(self):
        return "Add Height Above Ground"

    def group(self):
        return "Forest structure"

    def groupId(self):
        return "foreststructure"

    def helpUrl(self):
        return help_url("addheightaboveground")

    def shortHelpString(self):
        return (
            "<p>Adds a persistent <b>HeightAboveGround</b> dimension to a new LAS or LAZ "
            "point cloud. Select a point cloud already in the Layers panel, or supply a "
            "LAS/LAZ/COPC path or EPT URL. The original source and its Z elevations are preserved.</p>"
            "<p><b>Delaunay</b> uses ground points classified as 2; at least three ground points "
            "that are not on one line are required. <b>DTM</b> uses band 1 of a local terrain "
            "raster in the point cloud's CRS. The terrain raster must cover the processed points "
            "without NoData. Point Z and terrain elevations must be in metres and use the same vertical datum.</p>"
            "<p>Use a projected CRS in metres. Bounds and crop polygons are transformed to the point cloud CRS. "
            "Crop limits also limit the ground points available for interpolation. Save pending "
            "point cloud edits and export any layer filter before running. This tool processes "
            "full-resolution points in memory; choose a manageable area for large remote datasets.</p>"
            "<p>Choose a different .las or .laz output path. COPC output is not supported by this tool. "
            "Keep <i>Open output file after running algorithm</i> enabled to add the result to Layers. "
            "QGIS may then index the output. Select HeightAboveGround in the layer's attribute "
            "symbology, or use the result in the forest structure tools. Cancel is checked between "
            "backend operations. Native QGIS jobs receive cancellation requests.</p>"
        )

    def initAlgorithm(self, config=None):
        add_point_cloud_parameters(self, hag=False)
        self.addParameter(
            QgsProcessingParameterEnum(
                self.HAG_METHOD,
                "Height above ground method",
                ["Delaunay (classified ground)", "DTM raster"],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.DTM, "Terrain raster for DTM method", optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterPointCloudDestination(
                self.OUTPUT, "Point cloud with HeightAboveGround"
            )
        )

    def prepareAlgorithm(self, parameters, context, feedback):
        from .common import ensure_backend

        backend = ensure_backend("hag", parameters, context, self, feedback)
        self._prepared = prepare_point_cloud(
            self,
            parameters,
            context,
            feedback,
            backend=backend,
            hag_method=self.HAG_OPTIONS[
                self.parameterAsEnum(parameters, self.HAG_METHOD, context)
            ],
        )
        require_metric_crs(QgsCoordinateReferenceSystem(self._prepared.crs_wkt))
        return True

    def processAlgorithm(self, parameters, context, feedback):
        prepared = self._prepared
        output = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        lower = output.lower()
        if not lower.endswith((".las", ".laz")) or lower.endswith(".copc.laz"):
            raise QgsProcessingException(
                "Choose a .las or .laz output filename; this tool does not write COPC."
            )
        if os.path.realpath(output) == os.path.realpath(prepared.path):
            raise QgsProcessingException(
                "The output must differ from the input point cloud; the original source is preserved."
            )
        if (
            os.path.isfile(output)
            and os.path.isfile(prepared.path)
            and os.path.samefile(output, prepared.path)
        ):
            raise QgsProcessingException(
                "The output points to the input file. Choose a different output file."
            )
        protect_input_paths([output], [prepared.dtm_path])
        if feedback.isCanceled():
            return {}
        try:
            if prepared.backend == "native":
                from .native_cloud import write_native_hag

                result = write_native_hag(prepared, output, context, feedback)
            else:
                from ..runtime.client import call_engine

                result = call_engine(
                    "hag",
                    {"prepared": asdict(prepared), "output": output},
                    feedback=feedback,
                )
            feedback.pushInfo(
                f"Wrote {result.get('count', 0):,} points with HeightAboveGround. Original Z elevations were retained."
            )
            return {self.OUTPUT: result.get("output", output)}
        except (RuntimeError, ValueError, OSError) as exc:
            raise QgsProcessingException(
                f"Height above ground processing failed: {exc}"
            ) from exc

    def createInstance(self):
        return AddHeightAboveGroundAlgorithm()
