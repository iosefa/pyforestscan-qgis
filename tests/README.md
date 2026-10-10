# Release verification

Test the two execution paths separately. Native processing uses QGIS's compiled
libraries and point-cloud tools, plus the plugin's bundled PyForestScan 0.4.1 and
pure-Python LAS reader. The optional engine runs Python PDAL and its other
libraries in a separate process. Do not install Python PDAL or PyForestScan into
the QGIS interpreter to run these tests.

Native point-cloud processing requires QGIS **4.2 or later**. QGIS 3.40 and 4.0
use the isolated engine for point-cloud workflows, including metrics from an
existing HAG layer: older native conversion writers can drop extra dimensions.
Raster-only rumple may use the QGIS libraries when its dependencies are present.

Fixtures use generated local point clouds, temporary outputs, and an isolated
QGIS profile. Widget and integration tests share one QGIS application for the
process; recreating Qt's application between classes is unsupported. Rasterio
is a **test-only** dependency for fixture creation and
independent raster assertions; plugin raster operations use QGIS's GDAL bindings.

## Native QGIS tests

Use the Python interpreter belonging to QGIS. The repository's `environment.yml`
is a developer/test environment, not an installation requirement for users with
an existing QGIS application.

```bash
conda env create -f environment.yml
conda activate pyforestscan-qgis
export QGIS_PREFIX_PATH="$CONDA_PREFIX"
export PYTHONPATH="$CONDA_PREFIX/share/qgis/python${PYTHONPATH:+:$PYTHONPATH}"
QT_QPA_PLATFORM=offscreen PYFORESTSCAN_BACKEND=native \
  PYFORESTSCAN_TEST_RUNTIME=1 python -m unittest discover -s tests -v
```

For the standalone macOS application, use its wrapper, which configures the
application's Python paths, and set the application's GDAL/PROJ data locations.
Adjust the application name to the installed version:

```bash
env -u PYTHONHOME -u PYTHONPATH -u QGIS_PREFIX_PATH -u PROJ_LIB \
  QT_QPA_PLATFORM=offscreen PYFORESTSCAN_BACKEND=native \
  PYFORESTSCAN_TEST_RUNTIME=1 \
  GDAL_DATA=/Applications/QGIS-final-4_2_3.app/Contents/Resources/qgis/gdal \
  PROJ_DATA=/Applications/QGIS-final-4_2_3.app/Contents/Resources/qgis/proj \
  /Applications/QGIS-final-4_2_3.app/Contents/MacOS/python \
  -m unittest discover -s tests -v
```

`PYFORESTSCAN_TEST_RUNTIME=1` enables QGIS processing tests and makes missing
analytic dependencies fail. `PYFORESTSCAN_BACKEND=native` prevents an installed
engine from concealing a missing native capability. The scientific worker class
is intentionally skipped in this run because QGIS does not need Python PDAL.
The three worker protocol tests still run without it.

## Isolated-engine tests

Install the private engine using **Plugins → PyForestScan → Set up PyForestScan**.
For a disposable test installation, set `PYFORESTSCAN_ENGINE_ROOT` before
starting QGIS or the installer. Use an empty dedicated folder: the installer
refuses to adopt or remove unrecognized directories.

Run the same QGIS suite with the engine selected:

```bash
QT_QPA_PLATFORM=offscreen PYFORESTSCAN_BACKEND=engine \
  PYFORESTSCAN_TEST_RUNTIME=1 python -m unittest discover -s tests -v
```

This checks the complete QGIS frontend → serialized request → external worker →
saved outputs path. Also rerun the HAG → LAS/LAZ → loaded layer → CHM test with
`PYFORESTSCAN_BACKEND=auto` on QGIS 3.40 to verify automatic engine selection:

```bash
QT_QPA_PLATFORM=offscreen PYFORESTSCAN_BACKEND=auto \
  PYFORESTSCAN_TEST_RUNTIME=1 python -m unittest discover -s tests \
  -p test_qgis_runtime.py -k test_hag_layers_precision_and_metrics_reuse -v
```

The QGIS 3.40 fixtures use the private interpreter for lossless compressed-cloud
reads and COPC creation because that version's native writers omit dimensions
or CRS metadata. The tests still create real QGIS layers and run the plugin's
actual Processing algorithms; Python PDAL remains outside the host process.

Run the worker's actual PDAL/GDAL tests with the private interpreter separately:

```bash
python - <<'PY'
import os
import subprocess
from runtime import manager
from runtime.client import sanitized_environment

interpreter = manager.engine_python()
assert interpreter is not None, "Install the private engine first"
environment = sanitized_environment(interpreter)
environment["PYFORESTSCAN_TEST_ENGINE"] = "1"
if os.environ.get("PYFORESTSCAN_PLUGIN_PATH"):
    environment["PYFORESTSCAN_PLUGIN_PATH"] = os.environ["PYFORESTSCAN_PLUGIN_PATH"]
subprocess.run(
    [str(interpreter), "-I", "-m", "unittest", "discover",
     "-s", "tests", "-p", "test_worker.py", "-v"],
    env=environment, check=True,
)
PY
```

`PYFORESTSCAN_TEST_ENGINE=1` is independent of `PYFORESTSCAN_TEST_RUNTIME`.
It requires every worker scientific dependency and must produce no skips in
`test_worker.py`. Keep a custom `PYFORESTSCAN_ENGINE_ROOT` set for both setup and
tests so they resolve the same engine. Installer and protocol checks can also
run in ordinary Python without QGIS:

```bash
python -m unittest discover -s tests -p test_engine_manager.py -v
python -m unittest discover -s tests -p test_worker.py -v
```

## What is checked

- `test_metric_engine.py`: analytic PAD, PAI, FHD, canopy cover, density, rumple
  and voxel statistics; non-unit height bins; invalid/negative HAG; degenerate
  grids; allocation limits; GeoTIFF orientation, CRS, NoData, PAD band metadata,
  and failed-write preservation. Calculations use the bundled released core.
- `test_qgis_runtime.py`: all 11 algorithm registrations (10 visible tools and
  the hidden legacy combined API), parameters and help;
  visible HTML dependency reports, complete diagnostic logs, backend-specific
  readiness, engine failures and cancellation;
  required exact `HeightAboveGround`, rejection of inline preparation requests,
  reuse of saved heights, and inactive tiled metric settings;
  loaded LAS/LAZ/COPC layers; persistent HAG with unchanged source bytes, XYZ and
  extra dimensions; DTM coverage/NoData/CRS checks; crop transformations; typed
  outputs and layer-loading requests; all eight combined metrics; focused tools;
  rumple from a loaded raster; and local binary EPT processing and tiling.
  Fractional-grid checks include a return exactly on a 0.3 m tile boundary at
  UTM coordinates, an inclusive outer boundary, and empty cells remaining NoData.
- `test_worker.py`: protocol errors and nonfinite JSON values; a real isolated
  LAS → HAG → PAI → GeoTIFF self-test; metadata; preserved dimensions/precision;
  output folders containing `#`; DTM failure rollback; physical units; raster
  CRS checks; all point views; fractional tile placement and overwrite guards.
  Metric/tile jobs reject inline height preparation and missing HAG; inactive
  tiled settings do not affect CHM, and validation preserves existing outputs.
- `test_parameter_widgets.py`: real Processing panels on Qt 5/6; conditional
  DTM, metric settings and outlier buffer; retained values after switching;
  standard batch widgets and modeler parameter-source choices.
- `test_engine_manager.py`: trusted package manifests, checksum verification,
  download caching, ownership/symlink/deletion boundaries, repair rollback,
  activation only after a successful self-test, sanitized process environments,
  pipe draining, cancellation, and protocol errors. These unit tests mock
  downloads and installation; a real engine installation is a separate check.

For a quick provider/parameter check, omit `PYFORESTSCAN_TEST_RUNTIME`. The
processing tests will skip; that is not full release verification. Analytic
checks can run without QGIS when NumPy, SciPy, GDAL and test-only rasterio are
available:

```bash
PYFORESTSCAN_TEST_RUNTIME=1 \
  python -m unittest discover -s tests -p test_metric_engine.py -v
```

## Verify the packaged plugin

```bash
scripts/package_plugin.sh
export PYFORESTSCAN_PLUGIN_PATH="$PWD/dist/pyforestscan_qgis"
QT_QPA_PLATFORM=offscreen PYFORESTSCAN_BACKEND=native \
  PYFORESTSCAN_TEST_RUNTIME=1 python -m unittest discover -s tests -v
```

All test modules honor `PYFORESTSCAN_PLUGIN_PATH`; the tests stay in the
repository while implementation, bundled dependencies and runtime manifests are
loaded from the staged ZIP content. Repeat with `PYFORESTSCAN_BACKEND=engine`
and run the private worker tests against the same staged directory. Unset the
path variable before returning to source-tree tests.

GitHub Actions is configured to build/test the ZIP with Linux QGIS **3.40 using
the isolated engine** and **4.2 using native processing**. Its optional manual
`engine_platforms` input additionally performs real locked-engine installation
and worker tests on Linux, Windows and macOS runners without installing QGIS.
The macOS runner tests its detected architecture; this does not cover both Mac
architectures unless both are run separately. Workflow configuration alone is
not evidence of a passing remote CI run.

## Recorded 0.3.0 validation and remaining scope

Local verification on macOS Apple Silicon:

| Runtime | Result |
| --- | --- |
| Standalone QGIS 4.2.3, native processing | 49 tests passed |
| Standalone QGIS 4.2.3, isolated engine | 49 tests passed |
| Conda QGIS 3.40.3, isolated engine | 49 tests passed |
| QGIS 3.40.3, automatic backend choice | LAS/LAZ → HAG → loaded layer → CHM passed |
| Actual installed 0.4.1-1 osx-arm64 engine | All 11 worker tests passed |

The QGIS suite intentionally skips the worker scientific class; those cases
run separately with the private interpreter. The 49-test runs include all 15
installer safety tests. A fresh pinned engine installation and repair also
passed actual processing self-tests. QGIS network-manager downloads were
verified from a worker thread, and setup cancellation/close behavior was smoke
tested with Qt. These are local results, not evidence of Windows/Linux or
remote CI execution.

The staged 0.3.0 ZIP contents also passed the 49-test QGIS 4.2.3 suite with
each backend and all 11 private-worker tests. Implementation revision:
`457033b`. Archive checks confirmed all four engine manifests, worker files,
bundled source/licenses, and offline help are present, with no compiled binaries.

Record the application version, operating system/architecture, backend, package
revision and actual test result for each release. The declared plugin range is
QGIS 3.40–4.99; each later QGIS version still requires validation. QGIS 4.0 itself
has not yet been runtime-tested for this refactor.

The suite does not replace testing installation on other platforms, interactive
Processing dialogs, point-cloud indexing/rendering, live remote EPT access or
production-sized datasets. It does not establish native Windows/Linux support
from a macOS run. Native point-cloud subprocesses receive cancellation; a forced
engine termination can leave hidden temporary staging files, while completed
tiles remain available. Validate these workflows on each supported platform
before claiming broad release coverage.

Qt imports must use `qgis.PyQt`, with scoped enums compatible with the supported
Qt 5/Qt 6 builds. Relevant upstream guidance:

- [QGIS 4 plugin metadata and migration](https://plugins.qgis.org/docs/migrate-qgis4)
- [Qt 5 / Qt 6 plugin migration guide](https://github.com/qgis/QGIS/wiki/Plugin-migration-to-be-compatible-with-Qt5-and-Qt6)

## 0.3.1 Windows installer regression

The reported failure used a cache filename exactly 260 characters long.
Micromamba's local-URL cache namespace repeated the download path, and Python
cleanup could then fail at the Windows path limit. The revised installer seeds
verified archives into its private cache and records original HTTPS URLs with
SHA256 pins. A real macOS installation with this layout completed with zero
additional package downloads and passed its scientific self-test.

The installer suite now contains 22 tests, including original-error preservation,
ownership restoration after partial deletion, safe retry beside unmarked legacy
remnants, and cache preparation. A Windows-only test creates a real directory
tree beyond 260 characters and removes it through the owned-generation path;
it is skipped on macOS. GitHub Actions now runs these guards on Windows for
every push/PR, independently of the optional full engine installation matrix.
This configuration is not a recorded passing Windows run. The reporting user
subsequently confirmed that the corrected setup installs successfully on
Windows; Windows Processing workflows still need validation.

Local 0.3.1 verification: the packaged plugin passed the 56-test QGIS 4.2.3
native suite, with the Windows filesystem test and the separate worker
scientific class skipped. All 11 scientific/protocol worker tests passed in
the newly installed private engine. Ruff and archive/source consistency checks
also passed. No point-cloud calculation code changed in this patch release.

## 0.3.2 toolbox verification

On macOS Apple Silicon, the packaged plugin passed the 56-test native QGIS
4.2.3 suite, with the Windows filesystem test and separate worker scientific
class skipped. This includes running the hidden combined algorithm and the
focused tools. Direct checks of QGIS's toolbox and model-builder proxy models
confirmed that all 10 remaining tools are visible and **Calculate Forest
Metrics** is hidden in both. The original combined algorithm ID remains
registered and can still be instantiated. No engine or calculation code changed.

## 0.3.3 dependency-report verification

On macOS Apple Silicon, the packaged plugin passed the 60-test native QGIS
4.2.3 suite, with the Windows filesystem test and separate worker scientific
class skipped. All five diagnostic tests also passed on QGIS 3.40.3 / Qt 5.
The regression checks cover a visible report destination, HTML output type,
temporary and explicit report files, complete log output, escaped error text,
selected-backend readiness, engine status errors, and cancellation.

A separate smoke check invoked QGIS 4.2.3's actual toolbox execution handler
with an isolated temporary profile. Its standard Processing widget opened,
defaulted to a temporary report, completed a native dependency check, and
registered the generated HTML file in Processing Results. No private-engine
or forest-calculation code changed. Windows GUI execution was not tested locally.

## 0.4.0 prepared-height workflow verification

Local verification of the staged plugin on macOS Apple Silicon:

| Runtime | Result |
| --- | --- |
| QGIS 4.2.3 / Qt 6, native processing | 69-test suite passed, 2 intentional skips |
| QGIS 3.40.3 / Qt 5, isolated engine | 69-test suite passed, 2 intentional skips |
| Installed osx-arm64 engine, staged worker | All 14 scientific/protocol tests passed |

The QGIS skips are the Windows filesystem regression and worker scientific
class, which runs separately. Both QGIS suites include five tests of real
Processing parameter panels, covering dynamic field visibility and preserved
batch/modeler controls. QGIS 4.2's actual Processing widgets were also opened,
switched between methods, and visually checked for HAG, canopy cover, and EPT.

Processing checks reject missing or similarly named HAG dimensions, reject
normalized Z as a substitute, preserve existing HAG values, and prevent old
inline-HAG arguments from changing that behavior. Tiled CHM results stay
identical with irrelevant hidden settings changed. Ruff and packaged-source
consistency checks passed. The optional engine package lock is unchanged;
existing installations do not need reinstalling. Windows GUI testing and
QGIS 4.0 runtime testing remain outside this local verification.

## 0.4.3 community release review

Local review on 2026-10-09, macOS Apple Silicon, using the staged 0.4.3 code:

| Runtime | Result |
| --- | --- |
| QGIS 4.2.3 / Qt 6, native processing | 70 tests, OK with 2 intentional skips |
| QGIS 3.40.3 / Qt 5, isolated engine | 70 tests, OK with 2 intentional skips |
| Installed osx-arm64 engine, staged worker | All 15 scientific/protocol tests passed |

The QGIS skips remain the Windows filesystem test and separately executed
worker scientific class. New regression checks reject terrain and CHM band
scale/offset and feet-valued rasters without replacing existing outputs. A fresh
private-engine generation installed from the verified cache and passed its
real HAG/metric/raster self-test. No engine package lock changed.

Actual QGIS 4.2 Processing widgets were opened and visually reviewed for HAG
(Delaunay and DTM), canopy cover, EPT tiles, dependency checking and engine
setup. Labels, help, default coefficient and conditional fields were readable
and consistent. Ruff and Flake8 critical checks passed, including vendored
source. Local Bandit and secret scans found only QGIS warning/info rules:
reviewed subprocess/network/assertion patterns and manifest checksum strings.
These local scans do not establish QGIS's server-side validation or approval.

The staged package contains all released PyForestScan functions and both
vendored dependency licenses. Original source hashes were verified after
accounting for documented import namespacing; laspy now also has documented
type-checking imports without new runtime dependencies.

The maintainer subsequently confirmed that the plugin works well in their
Windows work and that they completed a visual review of all tools. This is
manual Windows validation; the exact QGIS version, backend, architecture and
individual datasets were not recorded, and no automated Windows run is claimed.
Earlier release sections above retain the scope known at those reviews.

The maintainer approved GPL-2.0-or-later for the plugin. The release ZIP includes
its full `LICENSE` and the unchanged third-party license notices. The package
builder refuses a missing/empty `LICENSE` before replacing existing artifacts.
Publication still requires enabling GitHub Issues and publishing the matching
source revision; this review is not a declaration of repository approval.
See [the publication guide](../docs/RELEASING.md) for the remaining steps.
Linux execution, Intel macOS, QGIS 4.0, live remote EPT and production-sized
workloads have not been verified by the recorded automated review.
