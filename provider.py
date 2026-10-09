import os

from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider

from .algorithms.add_height_above_ground import AddHeightAboveGroundAlgorithm
from .algorithms.calculate_lidar_metrics import CalculateLidarMetricsAlgorithm
from .algorithms.dependency_check import DependencyCheckAlgorithm
from .algorithms.process_ept_tiles import ProcessEptTilesAlgorithm
from .algorithms.rumple_index import RumpleIndexAlgorithm


class PyForestScanProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(DependencyCheckAlgorithm())
        self.addAlgorithm(AddHeightAboveGroundAlgorithm())
        for metric in ("CHM", "PAD", "PAI", "FHD", "canopy cover", "voxel statistic"):
            self.addAlgorithm(CalculateLidarMetricsAlgorithm(metric))
        self.addAlgorithm(RumpleIndexAlgorithm())
        self.addAlgorithm(CalculateLidarMetricsAlgorithm())
        self.addAlgorithm(ProcessEptTilesAlgorithm())

    def id(self):
        return "pyforestscan"

    def name(self):
        return "PyForestScan"

    def longName(self):
        return self.name()

    def supportedOutputRasterLayerExtensions(self):
        return ["tif"]

    def supportedOutputPointCloudLayerExtensions(self):
        return ["las", "laz"]

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icons", "icon.svg"))
