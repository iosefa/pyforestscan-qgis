"""Exercise conditional fields with QGIS's real Processing parameter panel."""

import importlib.util
import os
from pathlib import Path
import sys
import unittest

from qgis_test_support import qgis_application

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from qgis.core import QgsApplication
    from qgis.gui import QgsMessageBar
    from qgis.PyQt import sip
    from qgis.PyQt.QtWidgets import QDialog
except ImportError:
    QgsApplication = None


@unittest.skipIf(QgsApplication is None, "QGIS Python is required")
class ParameterWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qgis_application()
        plugin_path = Path(QgsApplication.pkgDataPath()) / "python" / "plugins"
        if str(plugin_path) not in sys.path:
            sys.path.append(str(plugin_path))
        if "pyforestscan_qgis" not in sys.modules:
            root = Path(
                os.environ.get(
                    "PYFORESTSCAN_PLUGIN_PATH", Path(__file__).resolve().parents[1]
                )
            )
            spec = importlib.util.spec_from_file_location(
                "pyforestscan_qgis",
                root / "__init__.py",
                submodule_search_locations=[str(root)],
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
        cls.plugin = sys.modules["pyforestscan_qgis"].classFactory(None)
        cls.plugin.initGui()

    @classmethod
    def tearDownClass(cls):
        cls.plugin.unload()
        cls.plugin = None
        cls.app = None

    def panel(self, name):
        from processing.gui.ParametersPanel import ParametersPanel

        class AlgorithmDialog(QDialog):
            def __init__(self):
                super().__init__()
                self.bar = QgsMessageBar(self)

            def messageBar(self):
                return self.bar

        algorithm = QgsApplication.processingRegistry().createAlgorithmById(
            f"pyforestscan:{name}"
        )
        dialog = AlgorithmDialog()
        panel = ParametersPanel(dialog, algorithm)
        # Preserve the definition and delete Qt objects while QGIS is still active.
        dialog.algorithm = algorithm
        self.addCleanup(sip.delete, dialog)
        return panel

    def assert_field_visible(self, panel, name, visible):
        wrapper = panel.wrappers[name]
        self.assertEqual(not wrapper.wrappedWidget().isHidden(), visible, name)
        self.assertEqual(
            not wrapper.wrappedLabel().isHidden(), visible, f"{name} label"
        )

    def test_dtm_only_visible_for_dtm_method_and_retains_selection(self):
        panel = self.panel("addheightaboveground")
        wrappers = panel.wrappers
        self.assert_field_visible(panel, "DTM", False)
        wrappers["HAG_METHOD"].setParameterValue(1, panel.processing_context)
        self.assert_field_visible(panel, "DTM", True)
        wrappers["DTM"].setParameterValue("/tmp/terrain.tif", panel.processing_context)
        wrappers["HAG_METHOD"].setParameterValue(0, panel.processing_context)
        self.assert_field_visible(panel, "DTM", False)
        wrappers["HAG_METHOD"].setParameterValue(1, panel.processing_context)
        self.assert_field_visible(panel, "DTM", True)
        self.assertEqual(wrappers["DTM"].parameterValue(), "/tmp/terrain.tif")

    def test_ept_fields_follow_metric_and_preserve_values(self):
        panel = self.panel("processepttiles")
        wrappers = panel.wrappers
        expected = {
            "VOXEL_Z": {1, 2, 3},
            "MIN_HEIGHT": {1},
            "FHD_MIN_HEIGHT": {2},
            "COVER_MIN_HEIGHT": {3},
            "COVER_K": {3},
        }
        wrappers["COVER_K"].setParameterValue(0.7, panel.processing_context)
        for metric in (0, 1, 2, 3, 0, 3):
            wrappers["METRIC"].setParameterValue(metric, panel.processing_context)
            for name, visible_metrics in expected.items():
                self.assert_field_visible(panel, name, metric in visible_metrics)
        self.assertEqual(wrappers["COVER_K"].parameterValue(), 0.7)
        self.assertNotIn("HAG_METHOD", wrappers)
        self.assertNotIn("DTM", wrappers)

    def test_ept_buffer_only_visible_with_outlier_filtering(self):
        panel = self.panel("processepttiles")
        for enabled in (False, True, False):
            panel.wrappers["REMOVE_OUTLIERS"].setParameterValue(
                enabled, panel.processing_context
            )
            self.assert_field_visible(panel, "BUFFER_FRACTION", enabled)

    def test_batch_retains_standard_widget_and_conditional_columns(self):
        from processing.gui.wrappers import WidgetWrapperFactory
        from pyforestscan_qgis.algorithms.parameter_widgets import (
            _ConditionalStandardWrapper,
        )
        from qgis.core import QgsProcessingContext

        class BatchAlgorithmDialog(QDialog):
            pass

        algorithm = QgsApplication.processingRegistry().createAlgorithmById(
            "pyforestscan:processepttiles"
        )
        dialog = BatchAlgorithmDialog()
        self.addCleanup(sip.delete, dialog)
        parameter = algorithm.parameterDefinition("COVER_K")
        wrapper = WidgetWrapperFactory.create_wrapper(parameter, dialog)
        self.assertNotIsInstance(wrapper, _ConditionalStandardWrapper)
        context = QgsProcessingContext()
        widget = wrapper.createWrappedWidget(context)
        widget.setParent(dialog)
        wrapper.setParameterValue(0.7, context)
        self.assertEqual(wrapper.parameterValue(), 0.7)

    def test_modeler_retains_native_parameter_source_choices(self):
        from processing.gui.wrappers import WidgetWrapperFactory
        from qgis.core import (
            QgsProcessingContext,
            QgsProcessingModelAlgorithm,
            QgsProcessingModelChildParameterSource,
        )
        from qgis.gui import QgsProcessingModelerParameterWidget

        class ModelerParametersDialog(QDialog):
            def __init__(self):
                super().__init__()
                self.model = QgsProcessingModelAlgorithm()
                self.childId = "forest_tiles"
                self.context = QgsProcessingContext()

        algorithm = QgsApplication.processingRegistry().createAlgorithmById(
            "pyforestscan:processepttiles"
        )
        dialog = ModelerParametersDialog()
        self.addCleanup(sip.delete, dialog)
        wrapper = WidgetWrapperFactory.create_wrapper(
            algorithm.parameterDefinition("COVER_K"), dialog
        )
        self.assertIsInstance(wrapper, QgsProcessingModelerParameterWidget)
        wrapper.setParent(dialog)
        source = QgsProcessingModelChildParameterSource.fromExpression("@cover_k")
        wrapper.setWidgetValue(source)
        self.assertEqual(wrapper.value().expression(), "@cover_k")


if __name__ == "__main__":
    unittest.main()
