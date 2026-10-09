from qgis.core import QgsApplication

from .provider import PyForestScanProvider


class PyForestScanPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None

    def initProcessing(self):
        if self.provider is not None:
            return
        self.provider = PyForestScanProvider()
        if not QgsApplication.processingRegistry().addProvider(self.provider):
            self.provider = None

    def initGui(self):
        self.initProcessing()

    def unload(self):
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
