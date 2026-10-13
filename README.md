# PyForestScan for QGIS

Forest structure analysis in the **QGIS Processing Toolbox**. Select a point cloud
already in your project, add `HeightAboveGround`, and calculate canopy height,
plant area, foliage height diversity, and other forest structure metrics. The
plugin uses ordinary Processing dialogs, batch processing, and models; it has no
separate control panel.

**0.4.4 is an experimental release.** It targets QGIS **3.40–3.x and
4.x** with bundled **PyForestScan 0.4.1**. QGIS's own point-cloud tools and
scientific libraries are used first, with an optional private engine for
installations that need it. See [tests/README.md](tests/README.md) for verification.
Metadata allows QGIS through 4.99; this does not mean every later minor release
has been tested. QGIS 4.0 itself has not been runtime-tested.

## Install

1. Obtain the release ZIP (`pyforestscan_qgis-0.4.4.zip`), or build it with
   `scripts/package_plugin.sh` (output: `dist/pyforestscan_qgis.zip`).
2. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**,
   select the ZIP, and enable **PyForestScan**.
3. Open **Processing → Toolbox**, search for **Check PyForestScan Dependencies**,
   double-click it, and click **Run**. Leave the report destination temporary
   or choose an HTML file to keep. Read the **Log** tab or open the report in
   **Processing → Results Viewer**. An overall **OK** means the selected backend
   supports the primary forest workflows.
4. If the dependency check fails or a tool needs the optional engine, open **Plugins →
   PyForestScan → Set up PyForestScan**, then click **Install engine**. Once
   validation finishes, rerun the tool. No terminal commands are needed.

The ZIP includes the complete pure-Python source of released PyForestScan 0.4.1
and laspy 2.7.0, their licenses, and offline help. Native processing uses QGIS's
PDAL Processing provider, NumPy ≥2.3.1, SciPy ≥1.16, and GDAL Python bindings.
It does **not** need Python PDAL bindings. Native point-cloud workflows require
QGIS 4.2 or later for attribute-preserving export and bounded EPT reads; older
QGIS versions use the isolated engine. Availability
is checked for the selected workflow, rather than assumed from the QGIS version.

The optional engine has its own Python and pinned scientific libraries in your
user data directory. Setup downloads packages over HTTPS, verifies their SHA256
checksums, and tests actual HAG, metric, and raster operations before activating
them. It does not install into QGIS, run pip in QGIS, or change your shell or
other Python environments. Repair keeps the previous working engine until its
replacement passes validation. Removal affects only this plugin's owned engine
files; verified downloads remain cached for reinstall. Internet access is needed
for the first installation. An already installed engine can process local data
offline; remote EPT still needs network access.

On Windows, setup uses a shallow temporary cache and checks the pinned packages'
path lengths before downloading. Normal AppData locations need no environment
variable, administrator access, or Windows long-path setting changes. After
upgrading the plugin, restart QGIS and retry **Install engine**; verified downloads
are reused and incomplete unpacking caches are rebuilt. Existing
`PYFORESTSCAN_ENGINE_ROOT` overrides still work. Keep an override in place if an
engine is already installed there, so the plugin can continue finding it.

Setup shows the platform's download size and required free space. Pinned engine
packages are provided for **macOS Apple Silicon and Intel, Windows x64, and
Linux x64**. ARM Windows/Linux engine builds are not provided. Platform package
availability does not imply that every operating system has passed end-to-end
testing; see the verification record. Native processing remains available where
its capability checks pass, even without an engine build for that platform.

| Optional engine | First download, including installer | Free space required for setup |
| --- | ---: | ---: |
| macOS Apple Silicon | 171 MB | 2.1 GB |
| macOS Intel | 183 MB | 2.2 GB |
| Windows x64 | 306 MB | 3.7 GB |
| Linux x64 | 228 MB | 2.8 GB |

These are decimal sizes for the pinned engine `0.4.1-1` manifests, rounded up. On the tested
Apple Silicon installation, the engine occupies about 702 MB, plus 171 MB of
cached downloads. Setup removes its unpacking cache after validation. Previous
working generations remain until **Remove engine**, so repairs can use more
disk space.

Leave **Processing backend** set to **Automatic** to use QGIS when possible.
For a source that QGIS's native reader cannot open, install the engine and choose
**Isolated engine** in setup to retry with its PDAL reader. All analysis still
runs through the Processing Toolbox.

For development, install or symlink the repository as `pyforestscan_qgis` under
`python/plugins` in the active QGIS profile. Find that profile with **Settings →
User Profiles → Open Active Profile Folder**. The hyphenated repository name
`pyforestscan-qgis` is not a valid Python plugin package name. Restart QGIS after
upgrading the plugin. `environment.yml` is a developer/test environment, not a
community installation prerequisite. The bundled [setup guide](docs/index.html#setup)
also explains installation and troubleshooting.

## Start with a loaded point cloud

1. Add a LAS/LAZ/COPC or EPT point cloud to the QGIS Layers panel. Confirm its CRS
   and inspect the data. Forest metrics require a projected CRS in metres;
   elevations and terrain heights must also be in metres.
2. Run **Add Height Above Ground** and select that point cloud layer. Choose
   **Delaunay** when it contains ground points classified as `2`, or choose
   **DTM** and supply a terrain GeoTIFF covering the cloud in the same CRS and
   vertical reference. The terrain field appears only for the DTM method.
   Skip this step if the source already contains `HeightAboveGround`. A QGIS
   height-above-ground tool is also suitable if its saved output contains that
   exact dimension; replacing Z with normalized heights alone is insufficient.
3. Choose a new `.las` or `.laz` output and enable QGIS's option to load the
   result. The output adds `HeightAboveGround` and retains other point
   dimensions. LAS/LAZ/COPC inputs keep their native XYZ precision; EPT
   coordinates are serialized at the precision supported by the output LAS
   encoding. Elevation Z remains elevation, and the source is not edited.
4. Run **Canopy Height Model**, **Plant Area Index**, or another forest metric
   using the new layer. The tool reads `HeightAboveGround` automatically. Set
   the XY cell size and, where applicable, vertical bin size in metres, and
   save the result as a GeoTIFF.
5. Style the result in QGIS. For PAD, select a height band in raster symbology;
   bands run from the lowest height interval upward.

Every point-cloud metric, including **Process EPT Tiles**, requires a saved
`HeightAboveGround` dimension before it starts. Metric dialogs have no HAG
method or terrain inputs. A missing dimension produces an error explaining
how to prepare the source. Rumple from a CHM continues to accept a height raster.

When upgrading older models or scripts that calculated HAG inside a metric
step, add a separate **Add Height Above Ground** step and connect its output
to the metric input. Legacy `HAG_METHOD`/`DTM` arguments no longer calculate
heights inside metrics, including the hidden combined algorithm.

In the ordinary **Process EPT Tiles** dialog, metric-specific settings appear
only for the selected metric. The buffer setting appears only when outlier
cleaning is enabled; it supplies neighboring points for that cleaning. Modeler
and batch interfaces retain their parameter lists so values can be supplied
by other steps or separately for each row.

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
| Process EPT Tiles | `processepttiles` | Advanced tiled CHM, PAI, FHD, or canopy cover outputs |
| Check PyForestScan Dependencies | `checkdependencies` | HTML report and log of native capabilities, selected backend, and installed-engine self-test |

Single-metric raster tools expose `OUTPUT` as a raster destination, so results
can feed the next step in a Processing model. Choose persistent destinations
for work you intend to keep. The old combined **Calculate Forest Metrics** entry
is hidden from the toolbox and model builder. Its `calculatelidarmetrics` ID
remains available so existing models and scripts continue to run.

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

Native processing stages point clouds through temporary uncompressed LAS files;
allow disk space for those files even when the input is compressed. Single-area
processing reads points into memory and can create large voxel
arrays. Dense raster/voxel grids are limited to five million cells/voxels; this
does not limit the source point-read memory. Start with a small explicit extent,
use a suitable grid size, and use the advanced EPT tiling tool for large EPT
inputs. Cancellation is checked between processing stages and during engine
jobs. Completed outputs remain on disk; a forced stop may leave a hidden staging
file in the output directory. Remote EPT access depends on network access and
the selected reader.

QGIS's native LAS export can quantize floating-coordinate EPT data to **0.01 m**.
Choose **Isolated engine** for EPT metric calculations needing finer source
precision. LAS/LAZ/COPC inputs retain their stored coordinate scales; persistent
HAG outputs from EPT still use LAS coordinate encoding.

An empty height interval or missing returns can yield NoData; this is different
from a measured zero. Missing terrain coverage stops HAG processing. Inspect
coverage and the Processing log before interpreting a raster. Rumple requires valid adjacent
2×2 CHM patches, and changes with raster resolution and gap filling.

Terrain and CHM rasters must store physical metre elevations/heights directly.
Rasters declaring another vertical unit or a nonidentity band scale/offset are
rejected. Export the raster with scale/offset applied and values converted to
metres before using it for HAG or rumple; changing its CRS alone does not convert
the pixel values. If the raster has no band-unit metadata, confirm the values
are metres yourself.

See [CHANGELOG.md](CHANGELOG.md) for migration from 0.1.1 and
[tests/README.md](tests/README.md) for release verification commands. Maintainers
can follow the [QGIS publication guide](docs/RELEASING.md) to submit a community
release.

## License

PyForestScan for QGIS is licensed under [GPL-2.0-or-later](LICENSE).
Bundled PyForestScan retains its [MIT license](vendor/pyforestscan/LICENSE), and
laspy retains its [BSD-2-Clause license](vendor/laspy/LICENSE.txt). Their original
copyright and license notices are included in the plugin. Separately downloaded
engine packages retain their own licenses.
