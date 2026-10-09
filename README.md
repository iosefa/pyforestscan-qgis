# PyForestScan for QGIS

Forest structure analysis in the **QGIS Processing Toolbox**. Select a point cloud
already in your project, add `HeightAboveGround`, and calculate canopy height,
plant area, foliage height diversity, and other forest structure metrics. The
plugin uses ordinary Processing dialogs, batch processing, and models; it has no
separate control panel.

**0.2.0 is an experimental internal release.** It targets QGIS **3.40–3.x and
4.x** with **PyForestScan 0.4.1**. The 28 analytic and integration tests passed
on QGIS 3.40.3 and 4.2.3; see [tests/README.md](tests/README.md) for their scope.
Metadata allows QGIS through 4.99; this does not mean every later minor release
has been tested. QGIS 4.0 itself has not been runtime-tested.

## Install

The plugin ZIP contains the QGIS integration and offline help. PyForestScan,
PDAL, and the compiled geospatial dependencies must be installed in **the Python
environment used by QGIS**. Installing them into an unrelated Python or conda
environment does not make them available to a standalone QGIS application.

For a conda installation, run these commands from this repository:

```bash
conda env create -f environment.yml
conda activate pyforestscan-qgis
qgis
```

Launch QGIS from that activated environment. The environment pins
`pyforestscan==0.4.1`. Use a separate environment when retaining an existing QGIS
installation; for QGIS 3, change the QGIS dependency to `qgis=3.40` in a copy of
the environment file before creating it. Do not replace a working installation's
compiled libraries with an unrelated pip stack.

1. Obtain the internal `pyforestscan_qgis.zip`, or build it with
   `scripts/package_plugin.sh` (output: `dist/pyforestscan_qgis.zip`).
2. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**,
   select the ZIP, and enable **PyForestScan**.
3. Open **Processing → Toolbox**, search for **Check PyForestScan Dependencies**,
   and run it. Resolve reported imports or version mismatches before processing.

For development, install or symlink the repository as `pyforestscan_qgis` under
`python/plugins` in the active QGIS profile. Find that profile with **Settings →
User Profiles → Open Active Profile Folder**. The hyphenated repository name
`pyforestscan-qgis` is not a valid Python plugin package name. Restart QGIS after
upgrading the plugin or its Python dependencies.

## Start with a loaded point cloud

1. Add a LAS/LAZ/COPC or EPT point cloud to the QGIS Layers panel. Confirm its CRS
   and inspect the data. Forest metrics require a projected CRS in metres;
   elevations and terrain heights must also be in metres.
2. Run **Add Height Above Ground** and select that point cloud layer. Choose
   **Delaunay** when it contains ground points classified as `2`, or choose
   **DTM** and supply a terrain GeoTIFF covering the cloud in the same CRS and
   vertical reference.
3. Choose a new `.las` or `.laz` output and enable QGIS's option to load the
   result. The output adds `HeightAboveGround` and retains other point
   dimensions. LAS/LAZ/COPC inputs keep their native XYZ precision; EPT
   coordinates are serialized at the precision supported by the output LAS
   encoding. Elevation Z remains elevation, and the source is not edited.
4. Run **Canopy Height Model**, **Plant Area Index**, or another forest metric
   using the new layer. Choose the existing-height HAG option, set the XY cell
   size and vertical bin size in metres, and save the result as a GeoTIFF.
5. Style the result in QGIS. For PAD, select a height band in raster symbology;
   bands run from the lowest height interval upward.

Metric tools can also calculate HAG during a run. Use the separate HAG tool when
you want to keep a normalized-height point cloud for inspection or reuse.

Choose an output directory whose full parent path contains no `#`: PDAL treats
that character in writer paths as a filename placeholder. This restriction is
on the HAG output directory, not on input filenames.

Delaunay needs enough suitably distributed classified ground points for a terrain
surface (at least three non-collinear ground points); selecting the method does
not classify the cloud for you. Cropping also limits the available ground points.

Choose **either** a loaded point cloud (`INPUT_LAYER`) **or** a file/EPT source
(`INPUT`), not both. Supported sources are local LAS/LAZ/COPC, local `ept.json`,
and HTTP(S) EPT URLs supported by your PDAL build. Selecting a layer reads its
underlying source; display settings do not define the processing area. Layer
subsets or unsaved edits are rejected; export filtered points or save pending
edits to a source first. Use the
explicit extent or polygon inputs to restrict the work.

The source CRS comes from the layer or data metadata. Supply `INPUT_CRS` only
when that information is missing: this assigns a CRS and does not reproject
coordinates. The project CRS is never used as a silent fallback. Reproject a
cloud beforehand with QGIS when needed. Crop extents and polygon geometries are
transformed to the source CRS. To crop only selected polygons, use Processing's
**Selected features only** option; otherwise the polygon input includes all
features.

## Toolbox tools

All algorithm IDs below have the prefix `pyforestscan:`. Detailed parameter,
output, and interpretation guidance is bundled in [offline help](docs/index.html)
and is available from each algorithm's help link.

| Tool | Algorithm ID | Result |
| --- | --- | --- |
| Add Height Above Ground | `addheightaboveground` | New LAS/LAZ with `HeightAboveGround` |
| Canopy Height Model | `canopyheightmodel` | Maximum height above ground per XY cell, GeoTIFF |
| Plant Area Density | `plantareadensity` | PAD volume as a multiband GeoTIFF, lowest height first |
| Plant Area Index | `plantareaindex` | PAD integrated over height, GeoTIFF |
| Foliage Height Diversity | `foliageheightdiversity` | Entropy of vertical return proportions, GeoTIFF |
| Canopy Cover | `canopycover` | PAD-derived Beer–Lambert cover fraction, GeoTIFF |
| Forest Voxel Statistics | `voxelstatistics` | A selected point dimension summarized per XY column, GeoTIFF |
| Rumple Index from CHM | `rumpleindex` | Loaded CHM raster → CSV report and numeric result |
| Calculate Forest Metrics | `calculatelidarmetrics` | Several metrics from one read, including point density |
| Process EPT Tiles | `processepttiles` | Advanced tiled CHM, PAI, FHD, or canopy cover outputs |
| Check PyForestScan Dependencies | `checkdependencies` | Runtime import and version report |

Single-metric raster tools expose `OUTPUT` as a raster destination, so results
can feed the next step in a Processing model. The combined tool retains its
algorithm ID and metric selection order and also exposes individual typed
outputs. Choose persistent destinations for work you intend to keep.

PAD uses PyForestScan's Beer–Lambert constant of 1. Canopy cover is
`1 - exp(-k × PAI_above_threshold)`, with default `k = 0.5`; it is not the fraction
of points above a threshold. Height thresholds are metres, not array indices.
Negative/nonfinite HAG values are excluded from forest metrics.
Compare results using the same resolution, vertical bins, thresholds, and
coefficients. Single-area CHM and voxel metrics can have different grid origins;
align rasters explicitly before pixelwise combinations. CHM outputs retain gaps;
use QGIS raster gap-filling tools when interpolation is appropriate, and record
that extra processing step.

## Scope and practical limits

The plugin focuses on normalization and forest structure. Use QGIS's point cloud
and raster tools for general filtering, clipping, reprojection, ground
classification where available, raster conversion, mosaicking, and display. The
[PyForestScan API coverage map](docs/API_COVERAGE.md) accounts for every public
function in the pinned library and explains which are exposed, used internally,
or left to QGIS/Python. Not every Python helper needs a toolbox entry.

Single-area processing reads points into memory and can create large voxel
arrays. Dense raster/voxel grids are limited to five million cells/voxels; this
does not limit the source point-read memory. Start with a small explicit extent,
use a suitable grid size, and use the advanced EPT tiling tool for large EPT
inputs. Cancellation is checked between
processing stages; an active PDAL call must finish before cancellation can take
effect. Remote EPT access depends on network access and the installed reader.

An empty height interval or missing returns can yield NoData; this is different
from a measured zero. Missing terrain coverage stops HAG processing. Inspect
coverage and the Processing log before interpreting a raster. Rumple requires valid adjacent
2×2 CHM patches, and changes with raster resolution and gap filling.

See [CHANGELOG.md](CHANGELOG.md) for migration from 0.1.1 and
[tests/README.md](tests/README.md) for release verification commands. This release
is prepared for internal distribution, not publication in the public plugin
repository.
