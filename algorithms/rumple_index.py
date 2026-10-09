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
    ensure_backend,
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
        self._backend = ensure_backend("rumple", parameters, context, self, feedback)
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
        output = self.parameterAsFileOutput(parameters, "OUTPUT", context)
        protect_input_paths([output], [self._source])
        check_canceled(feedback)
        band_number = self.parameterAsInt(parameters, "BAND", context)
        minimum = (
            self.parameterAsDouble(parameters, "MIN_HEIGHT", context)
            if parameters.get("MIN_HEIGHT") is not None
            else None
        )
        if self._backend == "engine":
            from ..runtime.client import call_engine

            result = call_engine(
                "rumple",
                {
                    "source": self._source,
                    "band": band_number,
                    "min_height": minimum,
                    "output": output,
                    "crs_wkt": self._crs,
                },
                feedback,
            )
            return {"OUTPUT": output, "RUMPLE": result["rumple"]}
        import numpy as np
        from osgeo import gdal, osr
        from ..vendor.pyforestscan.calculate import calculate_rumple

        raster = None
        try:
            raster = gdal.Open(self._source, gdal.GA_ReadOnly)
            if raster is None:
                raise ValueError("Could not read the canopy height raster.")
            stored, expected = osr.SpatialReference(), osr.SpatialReference()
            if (
                not raster.GetProjection()
                or stored.ImportFromWkt(raster.GetProjection()) != 0
            ):
                raise ValueError("The canopy height raster has no stored CRS.")
            expected.ImportFromWkt(self._crs)
            for crs in (stored, expected):
                crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            if not stored.IsSame(expected):
                raise ValueError(
                    "The layer CRS differs from its stored raster CRS; reproject the raster first."
                )
            transform = raster.GetGeoTransform()
            if (
                transform[2] != 0
                or transform[4] != 0
                or transform[1] <= 0
                or transform[5] >= 0
            ):
                raise ValueError(
                    "Use a north-up CHM raster; warp rotated rasters with QGIS first."
                )
            if raster.RasterXSize * raster.RasterYSize > 20_000_000:
                raise ValueError(
                    "The CHM is too large for an in-memory rumple calculation. Clip it to the study area first."
                )
            if not 1 <= band_number <= raster.RasterCount:
                raise ValueError("Choose an existing height band.")
            band = raster.GetRasterBand(band_number)
            values, mask = band.ReadAsArray(), band.GetMaskBand().ReadAsArray()
            if values is None or mask is None:
                raise ValueError("Could not read the canopy height band.")
            chm = values.astype(float)
            chm[mask == 0] = np.nan
            chm = chm.T
            resolution = (abs(transform[1]), abs(transform[5]))
            band = raster = None
            value = float(calculate_rumple(chm, resolution, min_height=minimum))
            if not math.isfinite(value):
                raise ValueError(
                    "No valid 2 × 2 canopy surface patches remain; enlarge the area or lower the height threshold."
                )
            check_canceled(feedback)
            write_rumple_report(output, value)
            feedback.pushInfo(f"Rumple index: {value:.6g}")
            return {"OUTPUT": output, "RUMPLE": value}
        except (ValueError, RuntimeError, IndexError) as exc:
            raise QgsProcessingException(f"Rumple calculation failed: {exc}") from exc
        finally:
            raster = None

    def createInstance(self):
        return RumpleIndexAlgorithm()
