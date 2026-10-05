# Compatibility

PyForestScan QGIS supports only the combinations listed as tested here. A
recognized platform is not automatically a supported Processing Engine target.

Official QGIS release information checked on 2026-09-03 lists QGIS 3.44.14
Solothurn as the LTR and QGIS 4.2.2 Belem do Para as the current release.
QGIS 4 uses Qt 6; QGIS 3.44 LTR uses Qt 5.

## Support tiers

- SUPPORTED: plugin, Processing Engine, and science evidence passed.
- SUPPORTED WITH LIMITATIONS: the tested chain passed with documented scope.
- UI-COMPATIBLE: plugin/UI matrix passed; engine/science chain is incomplete.
- EXPERIMENTAL: partial or QGIS-free evidence only.
- NOT TESTED: recognized target without executable evidence.
- UNSUPPORTED: outside current product scope or blocked by known constraints.

| OS | Architecture | QGIS | Qt | Plugin UI | Processing Engine | Science canary | Status | Test date | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Windows | x86_64 | 3.34.x | Qt 5 | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | - | Official release/API generation investigated; no runnable 3.34 installation was available. |
| Windows | x86_64 | 3.40.x | Qt 5 | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | 2026-09-03 | Local 3.40.5/3.40.15 directories contain setup stubs only, not a runnable QGIS application. |
| Windows | x86_64 | 3.44.13 | Qt 5.15.13 | PASS | PASS | PASS | SUPPORTED WITH LIMITATIONS | 2026-09-03 | Phase 32Z 24-case scientific UI matrix passed; established Windows CHM canary remains exact. Current 3.44.14 still needs repetition. |
| Windows | x86_64 | 4.0.0 | Qt 6 | PASS | NOT TESTED | NOT TESTED | UI-COMPATIBLE | 2026-09-03 | Scientific matrix plus 100 construction/unload and 100 navigation/state cycles passed from a clean extracted ZIP. Full provider/Prerun/engine/science gate remains open. |
| Windows | x86_64 | 4.2.2 | Qt 6 | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | - | Current stable build was not installed on the test host. |
| macOS | Apple Silicon | 3.44 target | Qt 5 | NOT TESTED | SOLVE ONLY | NOT TESTED | EXPERIMENTAL | 2026-10-04 | Installer enabled with native ARM bootstrap and Rosetta detection. All 287 Conda dependencies resolved for osx-arm64/macOS 11; bootstrap checksum and ARM64 Mach-O verified. Routing tests run on Linux. Actual Mac install, UI, repair, and science remain open. |
| macOS | Intel | current 3.x/4.x | Qt 5/6 | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | - | Viability depends on current upstream package availability. |
| Linux | x86_64 | 3.44.7 | Qt 5.15.13 | PASS | PASS | PASS | SUPPORTED WITH LIMITATIONS | 2026-10-04 | beta.7 engine repair: real first install, dependency consistency, final-prefix imports, and QGIS Toolbox Normalize Heights followed by 2 m CHM passed on a 3,936,192-point EPSG:32605 cloud. Prior extracted-ZIP UI lifecycle checks passed. Other products and polygon/batch workflows are not qualified by this check. |
| Linux | x86_64 | other 3.x/4.x | Qt 5/6 | NOT TESTED | NOT TESTED | NOT TESTED | EXPERIMENTAL | - | The 3.44.7 startup result does not qualify other QGIS versions. |
| Linux | ARM64 | current | varies | EXPERIMENTAL | NOT TESTED | NOT TESTED | EXPERIMENTAL | 2026-09-03 | Platform mapping only. |
| Linux Flatpak | any | any | varies | NOT TESTED | UNSUPPORTED | NOT TESTED | UNSUPPORTED | - | Sandbox filesystem and subprocess behavior need a dedicated design. |
| BSD | any | any | varies | EXPERIMENTAL | UNSUPPORTED | NOT TESTED | UNSUPPORTED | - | Not a first-release target. |

QField and mobile QGIS are outside the desktop Mission Control scope.

## QGIS and Qt boundary

`core/qgis_compat.py` owns guarded QGIS operations and version reporting.
Application code imports Qt through `qgis.PyQt`; no direct PyQt5 dependency is
used. `compat/qt.py` resolves Qt 5 unscoped and Qt 6 scoped enums at the UI
boundary. A local QGIS 4.0.0 spike exposed and repaired dock-area, dock-feature,
size-policy, frame, form-layout, cursor, focus, and keyboard enum differences.
Phase 32Z promoted QGIS 4.0 from a construction spike to UI-COMPATIBLE after an
isolated 24-case scientific-layout matrix. QGIS 4 remains unqualified for
engine/science support until Processing
provider registration, settings, layer loading, tasks/signals, CRS selection,
engine verification, and a deterministic science canary pass on a real build.
`metadata.txt` declares QGIS 3.28 as the minimum and intentionally has no
maximum; this is an installability declaration, not evidence that every newer
QGIS release is supported.

## Managed runtime inventory

`backend_manifest.json` and `backend_specs/environment*.yml` are the authority.
The current recipe owns Python 3.12, PyForestScan 0.4.x from PyPI, and the
conda-forge scientific stack: PDAL and python-pdal, GDAL/libgdal, Rasterio,
NumPy, SciPy, pandas, Shapely, pyproj, Fiona, GeoPandas, Matplotlib, tqdm,
Requests, and pip. PyForestScan is pinned to 0.4.1; dependency bounds now match
its published requirements, including NumPy >=2.3.1 and SciPy >=1.16.
The installer runs `pip check` before accepting the environment.
PyForestScan product execution and point-cloud I/O use managed PDAL;
QGIS Processing PDAL algorithms are a separate optional toolbox surface.

The platform YAML files are solve specifications, not lock files. Installer
execution is enabled for Windows and Linux x86_64, with experimental Apple
Silicon support. Micromamba 2.9.0 is pinned
to exact conda-forge archives and SHA-256 values in the manifest; the installer
uses and verifies those values. POSIX activation clones the Conda environment
into its final prefix, reinstalls pip packages there, and verifies the active
environment before deleting staging. A production RC still needs solved locks
and Windows repetition with the updated dependency recipe. The earlier Windows
science evidence above predates this dependency repair.

Phase 32Z attempted isolated Micromamba 2.8.1 dry-run solves for `win-64`,
`linux-64`, `osx-arm64`, and `osx-64`. Resolution did not complete within the
bounded test window and was stopped without creating an environment. This is
an attempted test, not lock or engine-install evidence.

On 2026-10-04, the updated recipe successfully resolved 287 packages for
`osx-arm64` using Micromamba 2.9.0 with `CONDA_OVERRIDE_OSX=11.0` and a dry run.
No Mac packages were installed or executed. The selected stack includes Python
3.12, PDAL 2.10.0, python-pdal 3.5.3, GDAL 3.12.2, and Rasterio 1.5.0.
The ARM bootstrap archive's SHA-256 and Mach-O CPU type were also verified.
This is dependency-resolution evidence, not a claim that QGIS or the complete
engine has run on macOS 11 or any other Mac version.

Apple Silicon selection uses the native CPU, including when QGIS runs as Intel
code under Rosetta. The process-local `sysctl.proc_translated` flag follows
[Apple's documented detection method](https://developer.apple.com/documentation/apple-silicon/about-the-rosetta-translation-environment).
The ARM scientific stack runs in separate subprocesses. Intel Mac installation
remains disabled in ordinary beta builds.

## Process policy

Backend paths are built with `pathlib`. The user-local root is LocalAppData on
Windows, Application Support on macOS, and XDG-style local data on Linux.
Managed subprocesses receive a sanitized environment. Windows children use
hidden-console flags; POSIX detached coordinators use a new process session.
Owned-tree cancellation uses `taskkill /T` on Windows and process-group signals
on POSIX. No path modifies QGIS Python, system Python, global PATH, or shell
profiles.

## Test strategy

- CORE: pure Python on Windows, Linux, and macOS.
- PLUGIN: construct Mission Control and register the provider in real QGIS.
- ENGINE: install/repair/verify each supported architecture from pinned locks.
- SCIENCE: deterministic product fixtures, including the CHM canary.
- PACKAGE: ZIP validation, source-to-ZIP parity, and clean-profile install.
- LIVE: release-gate QGIS interaction, cancellation, loading, and long-job QA.

CI should eventually cover Windows, Linux, and macOS against current LTR and
stable QGIS where runners are practical. New QGIS releases trigger a Qt/API
audit and package/engine/science smoke before support is declared.

Linux QGIS 3.44.7 has limited engine and real-data processing evidence described
above. QGIS 4.0 is UI-COMPATIBLE, not fully supported. Expanding support requires
real QGIS 4.2, macOS Apple Silicon, and additional Linux runners or manual gates;
QGIS-free unit tests alone do not promote a target to supported.

Sources: [QGIS Download](https://qgis.org/download/), [QGIS Installation
Guide](https://qgis.org/resources/installation-guide/), and the [official
PyForestScan calculation reference](https://pyforestscan.sefa.ai/api/calculate/).
