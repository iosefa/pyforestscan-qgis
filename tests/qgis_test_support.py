"""One QGIS application and isolated profile for the entire test process.

Qt supports one QApplication per process. Recreating it between test classes
also leaves QGIS GUI registries pointing to objects from the previous instance.
"""

import atexit
import tempfile


_application = None
_profile = None


def qgis_application():
    global _application, _profile
    if _application is None:
        from qgis.core import QgsApplication

        _profile = tempfile.TemporaryDirectory(prefix="pyforestscan-qgis-profile-")
        _application = QgsApplication([], False, _profile.name)
        _application.initQgis()
        atexit.register(_shutdown_qgis)
    return _application


def _shutdown_qgis():
    global _application, _profile
    if _application is not None:
        _application.exitQgis()
        _application = None
    if _profile is not None:
        _profile.cleanup()
        _profile = None
