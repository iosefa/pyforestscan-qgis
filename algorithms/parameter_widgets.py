"""Conditional visibility for standard QGIS Processing parameter widgets.

Batch columns and model inputs remain available: their controlling values can
differ between rows or be supplied by an upstream model step at run time.
"""

from qgis.gui import QgsAbstractProcessingParameterWidgetWrapper, QgsProcessingGui


def show_parameter_when(parameter, controller, values):
    """Use the normal QGIS widget, visible only for selected controller values."""
    metadata = parameter.metadata()
    metadata["widget_wrapper"] = {
        "class": f"{__name__}.ConditionalWidgetWrapper",
        "controller": controller,
        "values": list(values),
    }
    parameter.setMetadata(metadata)
    return parameter


class ConditionalWidgetWrapper:
    """Factory retaining QGIS's own batch and modeler parameter behavior."""

    def __new__(cls, parameter, dialog, row=0, col=0, controller=None, values=()):
        from processing.gui.wrappers import WidgetWrapperFactory, dialogTypes

        # Remove just this factory's metadata before asking QGIS for its widget.
        # Keep the cloned definition alive for the lifetime of that wrapper.
        definition = parameter.clone()
        metadata = definition.metadata()
        metadata.pop("widget_wrapper", None)
        definition.setMetadata(metadata)
        delegate = WidgetWrapperFactory.create_wrapper(definition, dialog, row, col)
        delegate._conditional_definition = definition
        mode = dialogTypes.get(
            dialog.__class__.__name__, QgsProcessingGui.WidgetType.Standard
        )
        if mode != QgsProcessingGui.WidgetType.Standard:
            return delegate
        return _ConditionalStandardWrapper(
            parameter, dialog, delegate, controller, values
        )


class _ConditionalStandardWrapper(QgsAbstractProcessingParameterWidgetWrapper):
    def __init__(self, parameter, dialog, delegate, controller, values):
        super().__init__(parameter, QgsProcessingGui.WidgetType.Standard, dialog)
        self._delegate = delegate
        self._context_generator = None
        self._controller_name = controller
        self._visible_values = tuple(values)
        self.setDialog(dialog)
        delegate.widgetValueHasChanged.connect(self._value_changed)

    def _value_changed(self, *_):
        self.widgetValueHasChanged.emit(self)

    def registerProcessingContextGenerator(self, generator):
        super().registerProcessingContextGenerator(generator)
        self._context_generator = generator
        self._delegate.registerProcessingContextGenerator(generator)

    def registerProcessingParametersGenerator(self, generator):
        super().registerProcessingParametersGenerator(generator)
        self._delegate.registerProcessingParametersGenerator(generator)

    def createWidget(self):
        from qgis.core import QgsProcessingContext

        context = (
            self._context_generator.processingContext()
            if self._context_generator is not None
            else QgsProcessingContext()
        )
        self._delegate.setWidgetContext(self.widgetContext())
        return self._delegate.createWrappedWidget(context)

    def createLabel(self):
        return self._delegate.createWrappedLabel()

    def widgetValue(self):
        return self._delegate.parameterValue()

    def setWidgetValue(self, value, context):
        self._delegate.setParameterValue(value, context)

    def postInitialize(self, wrappers):
        self._delegate.postInitialize(wrappers)
        self._controller = next(
            wrapper
            for wrapper in wrappers
            if wrapper.parameterDefinition().name() == self._controller_name
        )
        self._controller.widgetValueHasChanged.connect(self._update_visibility)
        self._update_visibility()

    def _update_visibility(self, *_):
        visible = self._controller.parameterValue() in self._visible_values
        for widget in (self.wrappedWidget(), self.wrappedLabel()):
            if widget is not None:
                widget.setVisible(visible)
