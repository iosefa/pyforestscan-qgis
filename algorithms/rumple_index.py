"""Calculate canopy surface complexity from a CHM already loaded in QGIS."""

import math
import os

from qgis.core import (
    Qgis,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingParameterBand,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProviderRegistry,
)

from .calculate_lidar_metrics import write_rumple_report
from .common import (
    check_canceled,
    help_url,
    protect_input_paths,
    raise_for_missing_dependencies,
    require_metric_crs,
)


class RumpleIndexAlgorithm(QgsProcessingAlgorithm):
    def name(self):
        return "rumpleindex"

    def displayName(self):
        return "Rumple Index from CHM"

    def group(self):
        return "Forest structure"

    def groupId(self):
        return "foreststructure"

    def helpUrl(self):
        return help_url(self.name())

    def shortHelpString(self):
        return (
            "<p>Calculate canopy surface area divided by projected ground area from a loaded "
            "canopy height raster. The result is a numeric Processing output and a CSV report.</p>"
            "<p>A flat canopy has rumple 1; a rough canopy has a larger value. Use a north-up "
            "CHM in a projected metre CRS, with heights in metres. NoData cells and optional "
            "below-threshold cells are excluded. At least one valid 2 × 2 patch is required. "
            "Interpolating a CHM can change the result.</p>"
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterRasterLayer("INPUT", "Canopy height model")
        )
        self.addParameter(
            QgsProcessingParameterBand(
                "BAND", "Height band", defaultValue=1, parentLayerParameterName="INPUT"
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "MIN_HEIGHT",
                "Minimum canopy height (m; optional)",
                Qgis.ProcessingNumberParameterType.Double,
                optional=True,
                minValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                "OUTPUT", "Rumple report", "CSV (*.csv)"
            )
        )
        self.addOutput(QgsProcessingOutputNumber("RUMPLE", "Rumple index"))

    def prepareAlgorithm(self, parameters, context, feedback):
        raise_for_missing_dependencies(QgsProcessingException)
        layer = self.parameterAsRasterLayer(parameters, "INPUT", context)
        if layer is None or not layer.isValid():
            raise QgsProcessingException("Choose a valid canopy height raster.")
        require_metric_crs(layer.crs())
        if layer.providerType() != "gdal":
            raise QgsProcessingException(
                "Export the canopy height raster to a local GeoTIFF first."
            )
        self._source = (
            QgsProviderRegistry.instance()
            .decodeUri("gdal", layer.source())
            .get("path", layer.source())
        )
        if not os.path.isfile(self._source):
            raise QgsProcessingException(
                "The canopy height raster must be a local GDAL-readable file."
            )
        self._crs = layer.crs().toWkt()
        return True

    def processAlgorithm(self, parameters, context, feedback):
        import numpy as np
        import rasterio
        from pyforestscan.calculate import calculate_rumple
        from pyproj import CRS

        output = self.parameterAsFileOutput(parameters, "OUTPUT", context)
        protect_input_paths([output], [self._source])
        check_canceled(feedback)
        try:
            with rasterio.open(self._source) as raster:
                if raster.crs is None or not CRS(raster.crs).equals(
                    CRS(self._crs), ignore_axis_order=True
                ):
                    raise QgsProcessingException(
                        "The layer CRS differs from its stored raster CRS; reproject the raster first."
                    )
                if (
                    raster.transform.b != 0
                    or raster.transform.d != 0
                    or raster.transform.a <= 0
                    or raster.transform.e >= 0
                ):
                    raise QgsProcessingException(
                        "Use a north-up CHM raster; warp rotated rasters with QGIS first."
                    )
                if raster.width * raster.height > 20_000_000:
                    raise QgsProcessingException(
                        "The CHM is too large for an in-memory rumple calculation. Clip it to the study area first."
                    )
                chm = (
                    raster.read(
                        self.parameterAsInt(parameters, "BAND", context), masked=True
                    )
                    .astype(float)
                    .filled(np.nan)
                    .T
                )
                resolution = (abs(raster.transform.a), abs(raster.transform.e))
            minimum = (
                self.parameterAsDouble(parameters, "MIN_HEIGHT", context)
                if parameters.get("MIN_HEIGHT") is not None
                else None
            )
            value = float(calculate_rumple(chm, resolution, min_height=minimum))
            if not math.isfinite(value):
                raise QgsProcessingException(
                    "No valid 2 × 2 canopy surface patches remain; enlarge the area or lower the height threshold."
                )
            check_canceled(feedback)
            write_rumple_report(output, value)
            feedback.pushInfo(f"Rumple index: {value:.6g}")
            return {"OUTPUT": output, "RUMPLE": value}
        except (ValueError, RuntimeError, IndexError) as exc:
            raise QgsProcessingException(f"Rumple calculation failed: {exc}") from exc

    def createInstance(self):
        return RumpleIndexAlgorithm()
