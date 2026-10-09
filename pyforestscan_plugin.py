from qgis.core import QgsApplication

from .provider import PyForestScanProvider


class PyForestScanPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.setup_action = None

    def initProcessing(self):
        if self.provider is not None:
            return
        self.provider = PyForestScanProvider()
        if not QgsApplication.processingRegistry().addProvider(self.provider):
            self.provider = None

    def initGui(self):
        self.initProcessing()
        if self.iface is not None and self.setup_action is None:
            try:
                from qgis.PyQt.QtGui import QAction
            except ImportError:
                from qgis.PyQt.QtWidgets import QAction
            self.setup_action = QAction("Set up PyForestScan…", self.iface.mainWindow())
            self.setup_action.triggered.connect(self.show_setup)
            self.iface.addPluginToMenu("&PyForestScan", self.setup_action)

    def show_setup(self):
        from .setup_dialog import SetupDialog

        dialog = SetupDialog(self.iface.mainWindow())
        dialog.exec()

    def unload(self):
        if self.setup_action is not None:
            self.iface.removePluginMenu("&PyForestScan", self.setup_action)
            self.setup_action.deleteLater()
            self.setup_action = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
