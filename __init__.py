def classFactory(iface):
    from .pyforestscan_plugin import PyForestScanPlugin

    return PyForestScanPlugin(iface)
