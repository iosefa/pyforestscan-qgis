# Tests

Run these tests with the Python interpreter belonging to QGIS. They use real
PyQGIS and Qt bindings, an isolated temporary QGIS profile, and temporary output
files. The system Python usually cannot import `qgis`.

## Provider and parameter checks

From the repository root, in a QGIS-enabled shell:

```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
```

This checks plugin loading, all three algorithm registrations, unloading and
reloading, the provider icon, file/URL input parameters, decimal parameters, and
dependency error reporting. It does not require PyForestScan or PDAL. The full
processing test is explicitly skipped unless enabled below.

## Full processing checks

In the conda environment created from `environment.yml`:

```bash
conda activate pyforestscan-qgis
export QGIS_PREFIX_PATH="$CONDA_PREFIX"
export PYTHONPATH="$CONDA_PREFIX/share/qgis/python${PYTHONPATH:+:$PYTHONPATH}"
QT_QPA_PLATFORM=offscreen PYFORESTSCAN_TEST_RUNTIME=1 \
  python -m unittest discover -s tests -v
```

This additionally requires every runtime dependency to import successfully,
generates a small classified LAS file and local binary EPT dataset, and runs:

- All seven single-area metrics on both LAS and EPT, with Delaunay HAG.
- Tiled CHM, PAI, and FHD on the EPT dataset.
- Checks that GeoTIFFs open in both QGIS and rasterio with the expected CRS and
  finite values, that PAD contains a 3D array, and that the rumple report contains
  a finite value of at least one.

The fixtures are generated locally; no remote point cloud downloads are needed.
Remote EPT access, DTM-based HAG, polygon cropping, and GUI interactions still
need separate checks when those behaviors change.

## Packaged plugin

To run the same suite against the files shipped in the ZIP:

```bash
scripts/package_plugin.sh
export PYFORESTSCAN_PLUGIN_PATH="$PWD/dist/pyforestscan_qgis"
QT_QPA_PLATFORM=offscreen PYFORESTSCAN_TEST_RUNTIME=1 \
  python -m unittest discover -s tests -v
```

GitHub Actions builds the package and runs the full suite on QGIS 3.40 and 4.2
using conda-forge environments.

Local validation on macOS passed on QGIS 3.40.3 / Qt 5.15.15 and QGIS 4.2.3 /
Qt 6.11.0, including the full processing checks. Provider and parameter checks
also passed in the standalone QGIS 4.2.3 app (Qt 6.11.1). QGIS 4.0 itself was not
runtime-tested: conda-forge did not offer a 4.0 build for macOS ARM or Linux x86-64
when checked. The declared 4.0 minimum for the 4.x series follows the shared Qt 6
API used by these algorithms.

## Compatibility references

- [QGIS 4 plugin metadata and migration](https://plugins.qgis.org/docs/migrate-qgis4)
- [Qt 5 / Qt 6 plugin migration guide](https://github.com/qgis/QGIS/wiki/Plugin-migration-to-be-compatible-with-Qt5-and-Qt6)

The metadata covers QGIS 3.22 through 4.99. QGIS 3.22 remains the declared
minimum; the automated runtime matrix starts at 3.40. Qt imports must continue
to use `qgis.PyQt`, and scoped enum names are used for Qt 5 / Qt 6 compatibility.
