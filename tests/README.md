# Release verification

The full suite contains **28 tests**: 13 analytic metric/output tests and 15
PyQGIS provider/integration tests. It uses **PyForestScan 0.4.1**, real PDAL and
rasterio processing, generated local fixtures, temporary outputs, and an isolated
QGIS profile. Run it with the Python interpreter belonging to QGIS; a system
Python usually cannot import `qgis`.

## Run the full suite

From the repository root, using the environment created from `environment.yml`:

```bash
conda activate pyforestscan-qgis
export QGIS_PREFIX_PATH="$CONDA_PREFIX"
export PYTHONPATH="$CONDA_PREFIX/share/qgis/python${PYTHONPATH:+:$PYTHONPATH}"
QT_QPA_PLATFORM=offscreen PYFORESTSCAN_TEST_RUNTIME=1 \
  python -m unittest discover -s tests -v
```

`PYFORESTSCAN_TEST_RUNTIME=1` enables all integration tests and makes missing
metric dependencies fail instead of skipping that test module. A successful
release run must report all 28 tests passing without skips.

## What is checked

The 13 tests in `test_metric_engine.py` check scientific values and raster
behavior against analytic examples and the pinned core library:

- PAI, PAD, canopy cover, and FHD with non-unit vertical bins; metre thresholds;
  NoData in unoccupied cells; and zero above-canopy cover/PAI in sampled cells.
- Rumple on a known inclined plane, uninterpolated CHM gaps, ground-only and
  single-coordinate clouds, and consistent exclusion of negative/invalid HAG.
- Voxel statistics, point-density units, invalid parameters, and grid/height-bin
  allocation limits before core arrays are created.
- GeoTIFF orientation, extent, CRS, float32/NoData encoding, PAD band metadata,
  the GeoTIFF band-count limit, and preservation of an existing file on a failed
  raster write.

The 15 tests in `test_qgis_runtime.py` exercise the actual Processing provider:

- All **11 algorithm registrations**, idempotent loading/unloading, native point
  cloud and raster parameters, output types, decimal grid sizes, bundled-help
  URLs, reader detection, and useful dependency error reports.
- Loaded LAS and LAZ layers → Delaunay HAG → loaded HAG layer → CHM. Source bytes,
  XYZ precision, and every original point dimension are preserved. COPC input
  also checks dimension/coordinate preservation in its LAS output.
- DTM HAG and rejection of wrong CRS, NoData, or absent terrain coverage; failed
  writes leave existing outputs intact and remove temporary files.
- Ambiguous/missing inputs, conflicting source CRS, source/terrain overwrite
  protection including file aliases, point-cloud layer filters, output folders
  containing `#`, geographic source CRS, and missing HAG.
- Extents and multiple crop polygons transformed from a different CRS; source
  CRS remains independent of the deliberately different project CRS.
- All eight combined metrics on local LAS and binary EPT, using known heights
  and a 2 m vertical bin. Checks include exact expected metric values, typed
  outputs, multiband PAD metadata, rumple CSV, and valid QGIS raster layers.
- All six focused raster tools, output-loading preferences, and rumple from a
  loaded CHM with a known physical cell size.
- Local EPT tiled CHM/PAI/FHD values, correct treatment of CHM cell-edge points,
  and fractional tile sizes without spurious zero-width edge tiles.

Fixtures are generated locally; the suite does not download a point cloud.

## Smaller checks during development

Run only analytic tests with a Python environment containing the pinned metric
runtime; QGIS is not needed for this module:

```bash
PYFORESTSCAN_TEST_RUNTIME=1 \
  python -m unittest discover -s tests -p test_metric_engine.py -v
```

In a QGIS-enabled shell, run the provider/parameter subset without enabling full
runtime tests:

```bash
QT_QPA_PLATFORM=offscreen \
  python -m unittest discover -s tests -p test_qgis_runtime.py -v
```

That command runs four provider/parameter checks and skips the 11 processing
integration tests. It does not establish that the scientific runtime works and
is not a release verification run.

## Verify the packaged plugin

Run these commands in the same activated QGIS environment, with
`QGIS_PREFIX_PATH` and `PYTHONPATH` set as above:

```bash
scripts/package_plugin.sh
export PYFORESTSCAN_PLUGIN_PATH="$PWD/dist/pyforestscan_qgis"
QT_QPA_PLATFORM=offscreen PYFORESTSCAN_TEST_RUNTIME=1 \
  python -m unittest discover -s tests -v
```

The tests remain in the repository, while `PYFORESTSCAN_PLUGIN_PATH` directs
both test modules to the implementation staged for the ZIP. Unset that variable
before returning to source-tree tests. The ZIP's top-level package must be
`pyforestscan_qgis`, including `docs/index.html` and its algorithm help anchors.

GitHub Actions is configured to build and test the package on Linux with
conda-forge QGIS **3.40** and **4.2**. Workflow configuration alone is not evidence
of a passing remote CI run.

## Recorded compatibility and remaining scope

Local full-suite runs for this refactor passed on macOS with **QGIS 3.40.3** and
**QGIS 4.2.3**, using PyForestScan 0.4.1. This verifies headless PyQGIS/PDAL
processing in those environments. In the standalone macOS QGIS 4.2.3 app,
the four provider/parameter checks also passed; its 11 full integration checks
were explicitly skipped because that application lacks the scientific runtime.
The metadata minimum is **3.40**, with a maximum of **4.99**; each later QGIS
version still needs its own validation.

**QGIS 4.0 itself has not been runtime-tested.** Live remote EPT access,
interactive GUI workflows from installation through rendering, and Windows have
not been validated end to end. The suite checks QGIS layer objects and output
loading requests, not mouse-driven rendering or asynchronous indexing. It also
does not establish performance on production-scale clouds or validate every
advanced EPT option (such as tiled canopy cover, DTM HAG, outlier cleaning, or
cancellation).

Qt imports must continue to use `qgis.PyQt`. Keep scoped enum names compatible
with the supported Qt 5 and Qt 6 builds. Relevant upstream guidance:

- [QGIS 4 plugin metadata and migration](https://plugins.qgis.org/docs/migrate-qgis4)
- [Qt 5 / Qt 6 plugin migration guide](https://github.com/qgis/QGIS/wiki/Plugin-migration-to-be-compatible-with-Qt5-and-Qt6)
