# PyForestScan QGIS

PyForestScan QGIS is a QGIS plugin for processing forest structure products from LiDAR with [PyForestScan](https://pyforestscan.sefa.ai/).

## What it does

- Guided Mission Control workflow for folder and polygon processing.
- Products: CHM, DTM, PAD, PAI, FHD, canopy cover, rumple, point density, voxel statistics, and height-above-ground point clouds.
- EPT and local LiDAR support with bounded tiling, aligned mosaics, resume/checkpoints, retry, pause, and cancellation.
- Live progress with stage, product, tile, elapsed time, percentage, and ETA.
- Processing Toolbox tools for expert workflows.

Point-viewer functionality is not included in this baseline.

## Install

1. Download or build `pyforestscan_qgis-v0.2.0-beta.7.zip`.
2. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Open **PyForestScan → Mission Control**, then **Tools & Setup**.
4. Set up/check the Processing Engine before choosing data and running **Prerun Check**.

The ZIP contains the plugin and backend recipes, not the scientific runtime.
The managed engine installer is enabled for Windows and Linux x86_64, with
experimental Apple Silicon support. It downloads the scientific dependencies
into a separate user-local environment; an internet connection is needed for
initial setup. On Apple Silicon it selects native ARM packages even when QGIS
runs under Rosetta. Mac installation and processing still need testing in QGIS.
Wait for **Processing Engine: Ready** before running tools.
See [Compatibility](docs/COMPATIBILITY.md).

## Build and validate

```bash
python3 scripts/package_plugin.py
python3 scripts/validate_release.py
```

The packager requires a clean Git worktree for release builds. For local testing
of uncommitted changes, use `python3 scripts/package_plugin.py --allow-dirty`;
the embedded build identity records that it is a development build.

Run the regression suite with a Python environment containing NumPy:

```bash
python3 -m unittest discover tests
```

Test the extracted ZIP in real QGIS using its Python interpreter (Linux example):

```bash
QT_QPA_PLATFORM=offscreen /usr/bin/python3 scripts/smoke_test_plugin.py
```

This uses temporary settings, verifies all 14 Processing tools, and exercises
Mission Control startup and unload. Results go to `dist/qgis-smoke.json`.
It does not install the plugin into your normal QGIS profile or run LiDAR science.

## Documentation

- [Quick Start](docs/getting-started/QUICK_START.md)
- [User Guide](docs/user-guide/README.md)
- [Product methods](docs/scientific-methods/README.md)
- [Compatibility](docs/COMPATIBILITY.md)
- [Current release notes](docs/releases/v0.2.0-beta.7.md)
- [Release Roadmap](docs/releases/RELEASE_ROADMAP.md)
- [Documentation index](docs/README.md)

## Development

The QGIS UI is in `pyforestscan_qgis/ui/`; processing and scheduling logic is in
`pyforestscan_qgis/core/`. `processing_provider.py` registers expert toolbox
algorithms, while `backend_runner/` executes scientific jobs in a separate
managed Python environment. PyForestScan calls belong behind `core/adapter.py`.
Historical phase notes remain in `docs/` but are not the primary specification.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [LICENSE](LICENSE).
