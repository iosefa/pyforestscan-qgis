# Tests

The suite covers core services, dependency management, processing plans,
scientific adapter contracts, QGIS integration boundaries, and packaging.

Run from the repository root with NumPy installed:

```bash
python3 -m unittest discover tests
```

Tests that require the real scientific stack skip when its dependencies are
unavailable. Many adapter tests use mocked scientific calls; passing them does
not establish end-to-end LiDAR correctness.

For actual QGIS startup and provider registration, build the ZIP and run
`scripts/smoke_test_plugin.py` with QGIS's Python interpreter. See the
[build instructions](../README.md#build-and-validate). This smoke test uses
temporary settings and leaves the regular QGIS profile alone.
