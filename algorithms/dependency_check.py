from html import escape
import os
from pathlib import Path

from qgis.core import (
    Qgis,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputString,
    QgsProcessingParameterFileDestination,
    QgsSettings,
)

from .common import check_canceled, check_dependencies, dependency_report, help_url


class DependencyCheckAlgorithm(QgsProcessingAlgorithm):
    STATUS = "STATUS"
    REPORT = "REPORT"

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
            "Click Run to see the results in the Log tab and create an HTML report, available "
            "from the Results Viewer. Leave Dependency report set to a temporary file or choose a file to keep. "
            "PyForestScan and the pure-Python LAS reader are bundled. To install or repair an engine, "
            "open Plugins → PyForestScan → Set up PyForestScan. No packages are installed into QGIS."
        )

    def flags(self):
        return super().flags() | Qgis.ProcessingAlgorithmFlag.NoThreading

    def initAlgorithm(self, config=None):
        # A visible destination also makes QGIS open its normal Processing dialog.
        # Without parameters, toolbox execution silently discards string results.
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.REPORT,
                "PyForestScan dependency report",
                "HTML files (*.html)",
                defaultValue=QgsProcessing.TEMPORARY_OUTPUT,
            )
        )
        self.addOutput(QgsProcessingOutputString(self.STATUS, "Status"))

    def processAlgorithm(self, parameters, context, feedback):
        check_canceled(feedback)
        feedback.setProgressText("Checking native QGIS capabilities…")
        results = check_dependencies(include_runtime=True)
        native_ok = all(result.ok for result in results)
        lines = []

        def report_line(line):
            lines.append(line)
            if line.startswith("FAIL:"):
                feedback.reportError(line)
            else:
                feedback.pushInfo(line)

        backend = os.environ.get(
            "PYFORESTSCAN_BACKEND",
            QgsSettings().value("pyforestscan/backend", "auto", type=str),
        )
        report_line(f"INFO: Processing backend: {backend}.")
        for line in dependency_report(results).splitlines():
            report_line(line)
        check_canceled(feedback)

        from ..runtime.manager import status as engine_status
        from ..runtime.client import EngineCanceled

        engine = engine_status()
        engine_ok = False
        if engine.get("error"):
            report_line(f"FAIL: Isolated engine status: {engine['error']}")
        if engine.get("installed"):
            from ..runtime.client import call_engine

            feedback.setProgressText("Testing the isolated processing engine…")
            report_line("INFO: Running the isolated engine's processing self-test…")
            try:
                response = call_engine("selftest", {}, feedback)
                if response.get("ready") is not True:
                    raise RuntimeError(
                        "The processing self-test did not report a ready engine."
                    )
                engine_ok = True
                report_line("OK: Isolated engine passed its processing self-test.")
            except EngineCanceled as exc:
                raise QgsProcessingException(str(exc)) from exc
            except Exception as exc:
                report_line(f"FAIL: Isolated engine: {exc}")
        else:
            report_line("INFO: Optional isolated engine is not installed.")
        check_canceled(feedback)

        ready = {
            "auto": native_ok or engine_ok,
            "native": native_ok,
            "engine": engine_ok,
        }
        available = ready.get(backend, False)
        status = "OK" if available else "FAIL"
        summary = (
            f"{status}: Primary forest workflows are available with the selected backend."
            if available
            else f"{status}: The selected backend is not ready for primary forest workflows."
        )
        report_line(summary)
        if backend not in ready:
            report_line("FAIL: PYFORESTSCAN_BACKEND must be auto, native, or engine.")
        if (
            not available
            or engine.get("error")
            or (engine.get("installed") and not engine_ok)
        ):
            report_line(
                "Open Plugins → PyForestScan → Set up PyForestScan to install or repair "
                "the engine, or select Automatic to use available QGIS capabilities."
            )
        report = f"{status}\n" + "\n".join(lines)
        destination = self.parameterAsFileOutput(parameters, self.REPORT, context)
        try:
            path = Path(destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                '<!doctype html><html lang="en"><meta charset="utf-8">'
                "<title>PyForestScan dependency report</title>"
                "<style>body{font-family:sans-serif;max-width:60rem;margin:2rem auto;"
                "padding:0 1rem}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
                "<h1>PyForestScan dependency report</h1>"
                f"<h2>{escape(summary)}</h2><pre>{escape(report)}</pre>"
                f'<p><a href="{escape(help_url("setup"), quote=True)}">Engine setup help</a></p>'
                "<p>Individual tools check their own requirements. This report does not "
                "validate your point clouds or remote data access.</p></html>",
                encoding="utf-8",
            )
        except OSError as exc:
            raise QgsProcessingException(
                f"Could not write the dependency report: {exc}"
            ) from exc
        feedback.setProgress(100)
        return {self.STATUS: report, self.REPORT: destination}

    def createInstance(self):
        return DependencyCheckAlgorithm()
