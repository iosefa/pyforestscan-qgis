import os

from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider

from .algorithms.calculate_lidar_metrics import CalculateLidarMetricsAlgorithm
from .algorithms.dependency_check import DependencyCheckAlgorithm
from .algorithms.process_ept_tiles import ProcessEptTilesAlgorithm


class PyForestScanProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(DependencyCheckAlgorithm())
        self.addAlgorithm(CalculateLidarMetricsAlgorithm())
        self.addAlgorithm(ProcessEptTilesAlgorithm())

    def id(self):
        return "pyforestscan"

    def name(self):
        return "PyForestScan"

    def longName(self):
        return self.name()

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icons", "icon.svg"))
