"""Persist height above ground as an extra dimension in a new LAS/LAZ layer."""

import json
import os
import tempfile

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterEnum,
    QgsProcessingParameterPointCloudDestination,
    QgsProcessingParameterRasterLayer,
)

from .common import help_url, protect_input_paths, raise_for_missing_dependencies
from .point_cloud import (
    add_point_cloud_parameters,
    prepare_point_cloud,
    validate_points,
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
            "without NoData. Point Z and terrain elevations must use the same units and vertical datum.</p>"
            "<p>Use a projected CRS. Bounds and crop polygons are transformed to the point cloud CRS. "
            "Crop limits also limit the ground points available for interpolation. Save pending "
            "point cloud edits and export any layer filter before running. This tool processes "
            "full-resolution points in memory; choose a manageable area for large remote datasets.</p>"
            "<p>Choose a different .las or .laz output path. COPC output is not supported by this tool. "
            "Keep <i>Open output file after running algorithm</i> enabled to add the result to Layers. "
            "QGIS may then index the output. Select HeightAboveGround in the layer's attribute "
            "symbology, or use the result in the forest structure tools. Cancel is checked between "
            "PDAL operations; an active PDAL operation must finish first.</p>"
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
        raise_for_missing_dependencies(QgsProcessingException)
        self._prepared = prepare_point_cloud(
            self,
            parameters,
            context,
            feedback,
            hag_method=self.HAG_OPTIONS[
                self.parameterAsEnum(parameters, self.HAG_METHOD, context)
            ],
        )
        return True

    def processAlgorithm(self, parameters, context, feedback):
        import numpy as np
        import pdal

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
        directory = os.path.dirname(os.path.abspath(output))
        if "#" in directory:
            raise QgsProcessingException(
                "PDAL treats # in output folder names as a filename placeholder. "
                "Choose an output folder without #."
            )
        os.makedirs(directory, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".pyforestscan-hag-",
            suffix=os.path.splitext(output)[1],
            dir=directory,
        )
        os.close(descriptor)
        writer = {
            "type": "writers.las",
            "filename": temporary,
            "minor_version": 4,
            "forward": "all",
            "extra_dims": "all",
            "compression": lower.endswith(".laz"),
            "a_srs": prepared.crs_wkt,
        }
        if prepared.reader["type"] == "readers.ept":
            # EPT has no LAS header to forward. Use auto scales to preserve as much
            # coordinate precision as fits in LAS's signed 32-bit coordinate integers.
            writer.update({f"scale_{axis}": "auto" for axis in "xyz"})
            writer.update({f"offset_{axis}": "auto" for axis in "xyz"})
        try:
            feedback.pushInfo(
                "Calculating HeightAboveGround and writing a new point cloud."
            )
            pipeline = pdal.Pipeline(json.dumps(prepared.stages + [writer]))
            count = pipeline.execute()
            arrays = pipeline.arrays
            if not arrays or not count:
                raise QgsProcessingException(
                    "No points were returned for the selected source and crop."
                )
            points = arrays[0] if len(arrays) == 1 else np.concatenate(arrays)
            validate_points(points, prepared)
            if feedback.isCanceled():
                return {}
            os.replace(temporary, output)
            feedback.pushInfo(
                f"Wrote {count:,} points with HeightAboveGround. Original Z elevations were retained."
            )
            return {self.OUTPUT: output}
        except RuntimeError as exc:
            raise QgsProcessingException(
                f"Height above ground processing failed: {exc}"
            ) from exc
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)

    def createInstance(self):
        return AddHeightAboveGroundAlgorithm()
