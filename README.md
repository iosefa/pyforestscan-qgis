# PyForestScan QGIS Plugin

QGIS Processing plugin wrappers for PyForestScan LiDAR forest structure workflows.

This repository is the plugin source. It intentionally does not vendor PyForestScan, PDAL, GDAL, rasterio, geopandas, or other compiled geospatial dependencies. Those packages must be installed in the Python environment used by QGIS.

## Current Status

Initial MVP scaffold:

- Processing provider: `PyForestScan`
- Algorithms:
  - `Check PyForestScan Dependencies`
  - `Calculate LiDAR Metrics`
  - `Process EPT Tiles`
- Single-area metrics:
  - Canopy Height Model
  - PAD as compressed NumPy output
  - PAI
  - FHD
  - canopy cover
  - point density
  - rumple report derived from CHM
- Tiled EPT metrics currently supported by the installed PyForestScan API:
  - CHM
  - PAI
  - FHD

## Install For Development

QGIS plugin folder names must be importable Python package names. If this repo is named `pyforestscan-qgis`, install or symlink it into QGIS as `pyforestscan_qgis`.

Typical QGIS profile plugin locations:

- macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/pyforestscan_qgis`
- Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/pyforestscan_qgis`
- Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\pyforestscan_qgis`

Then restart QGIS and enable `PyForestScan` in the Plugin Manager. The provider should appear in the Processing Toolbox.

## Build A Plugin ZIP

Run:

```bash
scripts/package_plugin.sh
```

The ZIP is written to `dist/pyforestscan_qgis.zip` with the correct top-level folder name for QGIS.

## Dependencies

Use the `Check PyForestScan Dependencies` processing algorithm first. It imports the runtime modules that the metric algorithms need and reports broken dynamic-library links as well as missing Python packages.

Recommended dependency strategy for the first release is a conda environment that includes QGIS-compatible geospatial binaries:

```bash
conda env create -f environment.yml
conda activate pyforestscan-qgis
```

If you are adding dependencies to an existing QGIS Python environment, install the compiled packages with conda first, then install PyForestScan without replacing those binaries:

```bash
conda install -c conda-forge qgis python-pdal rasterio geopandas pyproj shapely scipy numpy pandas matplotlib tqdm
python -m pip install --no-deps pyforestscan==0.2.4
```

Exact environment management differs by QGIS distribution. The important constraint is that `import pyforestscan`, `import pdal`, `import rasterio`, and `import geopandas` must work from QGIS's Python console.

## Limitations

- EPT URL support depends on the installed PyForestScan and PDAL build.
- Tiled processing does not yet expose QGIS progress/cancellation inside the tile loop because the current PyForestScan `process_with_tiles` API does not accept callbacks.
- Tiled processing currently supports CHM, PAI, and FHD through PyForestScan core.
- Polygon crop assumes the polygon is already in the LiDAR CRS.
- Publishing to the QGIS plugin repository is out of scope for the initial scaffold.
