# Tests

Initial validation is manual because QGIS is not available in the default system Python environment.

Suggested checks:

1. Run `python -m py_compile` on the plugin Python files.
2. Install the plugin into a QGIS profile as `pyforestscan_qgis`.
3. Run `Check PyForestScan Dependencies` from the Processing Toolbox.
4. Run `Calculate LiDAR Metrics` against a small local LAZ with Delaunay HAG.
5. Run `Process EPT Tiles` against a small bounded `ept.json`.
