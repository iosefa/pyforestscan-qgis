# Scripts

This directory contains development, testing, packaging, and release helpers.

Scripts should be documented, deterministic, and safe to run from a clean
checkout. They should not silently modify a user's QGIS or Python environment.


## Available Scripts

- `package_plugin.py`: builds a versioned ZIP and `dist/pyforestscan_qgis.zip`, with build identity and source verification. Use `--allow-dirty` for testing uncommitted changes. Optional `--sync-local` changes a local QGIS profile.
- `validate_plugin_package.py`: validates the ZIP structure and required plugin files.
- `validate_packaged_import_graph.py`: checks packaged Python imports for missing plugin modules.
- `validate_release.py`: checks version consistency, package contents, source parity, clean-profile replacement, and documentation links.
- `smoke_test_plugin.py [zip_path]`: runs the extracted ZIP using real QGIS, temporary settings, and a temporary backend root. Checks provider registration, all 14 algorithms, toolbar launch, engine-unavailable startup, 100 UI construction/unload cycles, and 100 navigation cycles. Writes `dist/qgis-smoke.json` by default; override with `--output`. On Linux, use `QT_QPA_PLATFORM=offscreen /usr/bin/python3 scripts/smoke_test_plugin.py`.
