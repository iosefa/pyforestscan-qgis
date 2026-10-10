"""Processing Toolbox entry points sharing the same released PyForestScan backend."""

import csv
import os
from pathlib import Path
import tempfile

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputFile,
    QgsProcessingOutputNumber,
    QgsProcessingOutputRasterLayer,
    QgsProcessingOutputString,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterString,
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
)

METRIC_INFO = {
    "CHM": ("canopyheightmodel", "Canopy Height Model", "chm", "CHM"),
    "PAD": ("plantareadensity", "Plant Area Density", "pad", "PAD"),
    "PAI": ("plantareaindex", "Plant Area Index", "pai", "PAI"),
    "FHD": ("foliageheightdiversity", "Foliage Height Diversity", "fhd", "FHD"),
    "canopy cover": ("canopycover", "Canopy Cover", "canopy_cover", "CANOPY_COVER"),
    "point density": (
        "pointdensity",
        "Point Density",
        "point_density",
        "POINT_DENSITY",
    ),
    "rumple": ("rumpleindex", "Rumple Index", "rumple", "RUMPLE"),
    "voxel statistic": (
        "voxelstatistics",
        "Forest Voxel Statistics",
        "voxel_statistic",
        "VOXEL_STATISTIC",
    ),
}
CANOPY_COVER_COEFFICIENT_HELP = (
    "The extinction coefficient k is a dimensionless factor describing how strongly "
    "plant area blocks light in this model. Higher k gives higher estimated cover "
    "for the same plant area. Keep 0.5 unless your method or calibration specifies "
    "another value."
)

METRIC_HELP = {
    "CHM": "Maximum HeightAboveGround in each XY cell, in metres. Empty cells remain NoData. Gap filling is left to QGIS raster tools.",
    "PAD": "Plant area density in inverse metres. The output is a multiband GeoTIFF: band 1 is the lowest height bin, with height ranges in band descriptions. Ground, undefined bins, and empty columns are NoData; a sampled bin with no estimated foliage may be zero.",
    "PAI": "Plant area index is the vertical integral of PAD above the minimum height, with vertical bin size included. Output is dimensionless; unsampled columns remain NoData.",
    "FHD": "Shannon entropy of return proportions across height bins above the selected minimum height, in natural-log units. Empty columns are NoData.",
    "canopy cover": "PAD-derived canopy cover: 1 − exp(−k × PAI above the height threshold). Values are fractions from 0 to 1, not percentages or a raw above-height return ratio. "
    + CANOPY_COVER_COEFFICIENT_HELP,
    "point density": "Number of nonnegative HeightAboveGround returns per square metre in each voxel column. Native QGIS tools also provide density without requiring HeightAboveGround.",
    "rumple": "Ratio of CHM surface area to its projected ground area. A flat valid canopy has value 1. The standalone Rumple Index tool accepts a CHM raster.",
    "voxel statistic": "A column statistic for a point-cloud dimension over the height-normalized voxel grid. Available statistics: mean, sum, count, minimum, maximum, median, standard deviation.",
}


def write_rumple_report(path, value):
    directory = Path(path).absolute().parent
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".pyforestscan-rumple-", suffix=".csv", dir=directory
    )
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["metric", "value"])
            writer.writerow(["rumple", value])
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


class CalculateLidarMetricsAlgorithm(QgsProcessingAlgorithm):
    """Batch metrics; fixed-metric instances expose focused Toolbox tools."""

    METRIC_OPTIONS = list(METRIC_INFO)
    STAT_OPTIONS = ["mean", "sum", "count", "min", "max", "median", "std"]

    def __init__(self, metric=None):
        super().__init__()
        self.metric = metric
        self._prepared = None

    def name(self):
        return METRIC_INFO[self.metric][0] if self.metric else "calculatelidarmetrics"

    def displayName(self):
        return (
            METRIC_INFO[self.metric][1] if self.metric else "Calculate Forest Metrics"
        )

    def flags(self):
        flags = super().flags()
        if self.metric is None:
            # Keep existing models/scripts runnable without offering a second
            # interface alongside the focused forest tools.
            flags |= (
                Qgis.ProcessingAlgorithmFlag.HideFromToolbox
                | Qgis.ProcessingAlgorithmFlag.HideFromModeler
            )
        return flags

    def group(self):
        return "Forest structure"

    def groupId(self):
        return "foreststructure"

    def helpUrl(self):
        return help_url(self.name())

    def shortHelpString(self):
        specific = (
            METRIC_HELP[self.metric]
            if self.metric
            else "Legacy combined algorithm retained for existing models and scripts. Use the focused forest tools for new workflows. PAD is a multiband raster; rumple is a CSV report and numeric result."
        )
        return (
            f"<p>{specific}</p>"
            "<p>Select a loaded point cloud layer, or enter a LAS/LAZ/COPC path or EPT URL. "
            "Use one input only. Full-resolution saved points are processed, independent of renderer styling. "
            "Save pending edits and export layer filters first.</p>"
            "<p>The source must use a projected CRS in metres, with Z and HeightAboveGround in metres. "
            "The source CRS is read from its metadata; assigning a missing CRS does not reproject data. "
            "Optional extents and crop polygons are transformed into the source CRS.</p>"
            "<p>The point cloud must already contain the HeightAboveGround dimension, which is read "
            "automatically. If it is missing, first run PyForestScan Add Height Above Ground or a "
            "QGIS height-above-ground tool that saves this dimension, then select its output. "
            "Changing Z alone is not sufficient. Negative or nonfinite HeightAboveGround "
            "values are excluded; invalid XYZ coordinates must be corrected. All height thresholds are in metres.</p>"
            "<p>These tools read the selected area into memory. Reduce the area or increase grid size "
            "for large clouds; use Process EPT Tiles for large EPT datasets. Cancellation is checked "
            "between backend operations. See the bundled Help for formulas and output interpretation.</p>"
        )

    def _uses(self, *metrics):
        return self.metric is None or self.metric in metrics

    def _number(self, name, label, default, minimum=0.0):
        self.addParameter(
            QgsProcessingParameterNumber(
                name,
                label,
                Qgis.ProcessingNumberParameterType.Double,
                defaultValue=default,
                minValue=minimum,
            )
        )

    def initAlgorithm(self, config=None):
        add_point_cloud_parameters(self)
        self._number("VOXEL_XY", "XY grid cell size (m)", 1.0, 0.000001)
        if self._uses(
            "PAD", "PAI", "FHD", "canopy cover", "point density", "voxel statistic"
        ):
            self._number("VOXEL_Z", "Height bin size (m)", 1.0, 0.000001)
        if self.metric is None:
            self.addParameter(
                QgsProcessingParameterEnum(
                    "METRICS",
                    "Metrics",
                    self.METRIC_OPTIONS,
                    allowMultiple=True,
                    defaultValue=[0, 2, 3, 4],
                )
            )
        if self._uses("PAI"):
            self._number("MIN_HEIGHT", "Minimum height for PAI (m)", 1.0)
        if self._uses("FHD"):
            self._number("FHD_MIN_HEIGHT", "Minimum height for FHD (m)", 0.0)
        if self._uses("canopy cover"):
            self._number("COVER_MIN_HEIGHT", "Minimum height for canopy cover (m)", 2.0)
            self._number("COVER_K", "Canopy cover extinction coefficient", 0.5)
            self.parameterDefinition("COVER_K").setHelp(CANOPY_COVER_COEFFICIENT_HELP)
        if self._uses("voxel statistic"):
            self.addParameter(
                QgsProcessingParameterString(
                    "VOXEL_DIMENSION",
                    "Dimension for voxel statistic",
                    defaultValue="HeightAboveGround",
                )
            )
            self.addParameter(
                QgsProcessingParameterEnum(
                    "VOXEL_STAT",
                    "Voxel column statistic",
                    self.STAT_OPTIONS,
                    defaultValue=0,
                )
            )
        if self.metric:
            self.addParameter(
                QgsProcessingParameterRasterDestination("OUTPUT", self.displayName())
            )
        else:
            self.addParameter(
                QgsProcessingParameterFolderDestination(
                    "OUTPUT_FOLDER", "Output folder"
                )
            )
            self.addParameter(
                QgsProcessingParameterBoolean(
                    "LOAD_OUTPUTS",
                    "Load output rasters into the project",
                    defaultValue=True,
                )
            )
            self.addParameter(
                QgsProcessingParameterBoolean(
                    "OVERWRITE", "Overwrite existing metric files", defaultValue=False
                )
            )
            for metric, (_, label, _, key) in METRIC_INFO.items():
                if metric != "rumple":
                    self.addOutput(QgsProcessingOutputRasterLayer(key, label))
            self.addOutput(QgsProcessingOutputFile("RUMPLE_REPORT", "Rumple report"))
            self.addOutput(QgsProcessingOutputNumber("RUMPLE", "Rumple index"))
            self.addOutput(QgsProcessingOutputString("OUTPUT_FILES", "Created files"))

    def prepareAlgorithm(self, parameters, context, feedback):
        self._backend = ensure_backend("metrics", parameters, context, self, feedback)
        self._prepared = prepare_point_cloud(
            self, parameters, context, feedback, backend=self._backend
        )
        require_metric_crs(QgsCoordinateReferenceSystem(self._prepared.crs_wkt))
        return True

    def processAlgorithm(self, parameters, context, feedback):
        from .metric_engine import compute_metrics, write_metric_raster

        selected = (
            [self.metric]
            if self.metric
            else [
                self.METRIC_OPTIONS[index]
                for index in self.parameterAsEnums(parameters, "METRICS", context)
            ]
        )
        if not selected:
            raise QgsProcessingException("Select at least one forest metric.")
        if self.metric:
            paths = {
                self.metric: self.parameterAsOutputLayer(parameters, "OUTPUT", context)
            }
            if not paths[self.metric].lower().endswith((".tif", ".tiff")):
                raise QgsProcessingException("Choose a GeoTIFF (.tif) output file.")
            folder = None
        else:
            folder = ensure_output_folder(
                self.parameterAsString(parameters, "OUTPUT_FOLDER", context)
            )
            paths = {
                key: os.path.join(
                    folder,
                    METRIC_INFO[key][2] + (".csv" if key == "rumple" else ".tif"),
                )
                for key in selected
            }
            if not self.parameterAsBool(parameters, "OVERWRITE", context):
                existing = [path for path in paths.values() if os.path.exists(path)]
                if existing:
                    raise QgsProcessingException(
                        "Output files already exist. Choose a new folder or enable Overwrite: "
                        + ", ".join(existing)
                    )
        protect_input_paths(
            paths.values(), [self._prepared.path, self._prepared.dtm_path]
        )
        check_canceled(feedback)
        z_resolution = (
            self.parameterAsDouble(parameters, "VOXEL_Z", context)
            if self.parameterDefinition("VOXEL_Z")
            else 1.0
        )
        options = {"z_resolution": z_resolution, "feedback": feedback}
        for parameter, option in (
            ("MIN_HEIGHT", "min_height"),
            ("FHD_MIN_HEIGHT", "fhd_min_height"),
            ("COVER_MIN_HEIGHT", "cover_min_height"),
            ("COVER_K", "cover_k"),
        ):
            if self.parameterDefinition(parameter):
                options[option] = self.parameterAsDouble(parameters, parameter, context)
        if "voxel statistic" in selected:
            options["voxel_dimension"] = self.parameterAsString(
                parameters, "VOXEL_DIMENSION", context
            ).strip()
            options["voxel_stat"] = self.STAT_OPTIONS[
                self.parameterAsEnum(parameters, "VOXEL_STAT", context)
            ]
        try:
            if self._backend == "engine":
                from dataclasses import asdict
                from ..runtime.client import call_engine

                engine_options = {
                    key: value for key, value in options.items() if key != "feedback"
                }
                engine_options["xy_resolution"] = self.parameterAsDouble(
                    parameters, "VOXEL_XY", context
                )
                engine_result = call_engine(
                    "metrics",
                    {
                        "prepared": asdict(self._prepared),
                        "metrics": selected,
                        "options": engine_options,
                        "paths": paths,
                        "z_resolution": z_resolution,
                    },
                    feedback,
                )
                results = {
                    "rumple": engine_result.get("rumple")
                    if engine_result.get("rumple") is not None
                    else float("nan")
                }
            else:
                points = read_point_cloud(self._prepared, feedback, context=context)
                feedback.setProgress(25)
                results = compute_metrics(
                    points,
                    selected,
                    xy_resolution=self.parameterAsDouble(
                        parameters, "VOXEL_XY", context
                    ),
                    **options,
                )
            check_canceled(feedback)
            outputs = (
                {}
                if self.metric
                else {"OUTPUT_FOLDER": folder, "OUTPUT_FILES": "", "RUMPLE_REPORT": ""}
            )
            created = []
            for index, key in enumerate(selected):
                check_canceled(feedback)
                path = paths[key]
                if key == "rumple":
                    if self._backend != "engine":
                        write_rumple_report(path, results[key])
                    outputs.update(RUMPLE=results[key], RUMPLE_REPORT=path)
                else:
                    if self._backend != "engine":
                        write_metric_raster(
                            results[key],
                            path,
                            self._prepared.crs_wkt,
                            z_resolution=z_resolution,
                        )
                    output_key = "OUTPUT" if self.metric else METRIC_INFO[key][3]
                    outputs[output_key] = path
                    if not self.metric and self.parameterAsBool(
                        parameters, "LOAD_OUTPUTS", context
                    ):
                        add_raster_to_load_on_completion(
                            context, path, METRIC_INFO[key][1], output_key
                        )
                created.append(path)
                feedback.setProgress(75 + 25 * (index + 1) / len(selected))
            if not self.metric:
                outputs["OUTPUT_FILES"] = "\n".join(created)
            return outputs
        except (ValueError, KeyError, RuntimeError, MemoryError) as exc:
            raise QgsProcessingException(
                f"Forest metric processing failed: {exc}"
            ) from exc

    def createInstance(self):
        return CalculateLidarMetricsAlgorithm(self.metric)
