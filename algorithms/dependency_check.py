from qgis.core import (
    Qgis,
    QgsProcessingAlgorithm,
    QgsProcessingOutputString,
)

from .common import check_dependencies, dependency_report, help_url


class DependencyCheckAlgorithm(QgsProcessingAlgorithm):
    STATUS = "STATUS"

    def name(self):
        return "checkdependencies"

    def displayName(self):
        return "Check PyForestScan Dependencies"

    def group(self):
        return "Diagnostics"

    def groupId(self):
        return "diagnostics"

    def helpUrl(self):
        return help_url(self.name())

    def shortHelpString(self):
        return (
            "Checks QGIS's native forest-processing capabilities and the optional isolated engine. "
            "PyForestScan and the pure-Python LAS reader are bundled. To install or repair an engine, "
            "open Plugins → PyForestScan → Set up PyForestScan. No packages are installed into QGIS."
        )

    def flags(self):
        return super().flags() | Qgis.ProcessingAlgorithmFlag.NoThreading

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

        from ..runtime.manager import status as engine_status

        engine = engine_status()
        engine_ok = False
        if engine.get("installed"):
            from ..runtime.client import call_engine

            try:
                response = call_engine("selftest", {}, feedback)
                if response.get("ready") is not True:
                    raise RuntimeError(
                        "The processing self-test did not report a ready engine."
                    )
                engine_ok = True
                report += "\nOK: Isolated engine passed its processing self-test."
            except Exception as exc:
                report += f"\nFAIL: Isolated engine: {exc}"
        else:
            report += "\nINFO: Optional isolated engine is not installed."
        if not all(result.ok for result in results) and not engine_ok:
            report += "\nOpen Plugins → PyForestScan → Set up PyForestScan."
        status = "OK" if all(result.ok for result in results) or engine_ok else "FAIL"
        return {self.STATUS: f"{status}\n{report}"}

    def createInstance(self):
        return DependencyCheckAlgorithm()
