from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingOutputString,
)

from .common import check_dependencies, dependency_report


class DependencyCheckAlgorithm(QgsProcessingAlgorithm):
    STATUS = "STATUS"

    def name(self):
        return "checkdependencies"

    def displayName(self):
        return "Check PyForestScan Dependencies"

    def group(self):
        return "PyForestScan"

    def groupId(self):
        return "pyforestscan"

    def shortHelpString(self):
        return (
            "Checks whether PyForestScan and its compiled geospatial dependencies "
            "can be imported from the Python environment used by QGIS."
        )

    def initAlgorithm(self, config=None):
        self.addOutput(QgsProcessingOutputString(self.STATUS, "Status"))

    def processAlgorithm(self, parameters, context, feedback):
        results = check_dependencies(include_runtime=True)
        report = dependency_report(results)

        for line in report.splitlines():
            if line.startswith("FAIL:"):
                feedback.reportError(line)
            else:
                feedback.pushInfo(line)

        status = "OK" if all(result.ok for result in results) else "FAIL"
        return {self.STATUS: f"{status}\n{report}"}

    def createInstance(self):
        return DependencyCheckAlgorithm()
