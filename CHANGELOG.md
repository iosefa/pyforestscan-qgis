# Changelog

## 0.4.2 — installation help cleanup

- Remove the obsolete Windows 0.3.0 setup notice from installation instructions
  and bundled help.

## 0.4.1 — explain the canopy-cover coefficient

- Explain the extinction coefficient beside the canopy-cover formula in the
  Processing help panel, in the coefficient's field help (including EPT), and
  in bundled documentation. Describe its effect and when to retain 0.5.
- No calculation, default-value, or engine changes.

## 0.4.0 — prepare heights before forest metrics

- Require an existing, exactly named `HeightAboveGround` dimension for all
  point-cloud metrics, including EPT tiles and the hidden combined algorithm.
  Read it automatically; remove HAG-method and terrain fields from metric dialogs.
- Fail early with instructions to run PyForestScan **Add Height Above Ground**
  or a QGIS tool that writes this dimension, then select the saved output.
  Normalized Z alone is insufficient. Checks apply to native and isolated processing.
- Show the standalone HAG tool's terrain field only for the DTM method. In
  the ordinary EPT dialog, show only settings relevant to the selected metric
  and show the buffer only for outlier cleaning. Batch/modeler parameter lists
  stay available for per-row values and model connections.
- Older models/scripts that requested inline HAG must add a separate height
  preparation step. Their metric IDs remain registered, but legacy HAG/DTM
  arguments no longer synthesize heights. Existing engines need no reinstall.

## 0.3.3 — visible dependency diagnostics

- Fix **Check PyForestScan Dependencies** appearing to do nothing: it now opens
  a normal Processing dialog and creates an HTML report, available from the
  Results Viewer. A temporary report is the default; no file selection is needed.
- Show engine self-test results, engine status errors, the overall outcome,
  and repair instructions in the Processing log as well as the report.
- Respect the selected backend when reporting readiness. A broken or missing
  explicitly selected engine no longer reports OK just because native QGIS
  capabilities are available. Cancellation does not publish a success report.
- Keep the existing algorithm ID and text `STATUS` output for scripts/models.
  Existing engines do not need reinstalling.

## 0.3.2 — focused toolbox

- Remove **Calculate Forest Metrics** from the Processing Toolbox and model
  builder. Use the focused canopy height, plant area, foliage diversity,
  canopy cover, voxel statistics, and rumple tools for new workflows.
- Retain the hidden `pyforestscan:calculatelidarmetrics` algorithm for existing
  models and scripts, including its combined outputs and point-density option.
- Update the toolbox list and bundled help. The processing engine and metric
  calculations are unchanged; an installed engine does not need reinstalling.

## 0.3.1 — Windows setup fix

- Avoid duplicating the full download directory inside micromamba's package
  cache. The installer supplies verified local archives while retaining their
  original HTTPS URLs and SHA256 hashes in its explicit package manifest.
- Delete owned Windows setup directories through extended-length paths,
  including package files beyond the traditional 260-character limit.
- Preserve the original setup failure if cleanup also fails, and retain a
  generation's ownership marker after an interrupted deletion.
- Allow a new installation beside unrecognized remnants of a failed 0.3.0
  setup without deleting those remnants. Previously verified downloads are
  reused; existing working engines do not need to be reinstalled.

After a failed Windows setup, install the 0.3.1 ZIP, restart QGIS, and retry
**Plugins → PyForestScan → Set up PyForestScan → Install engine**. No Windows
registry changes or administrator instructions are part of this fix.

## 0.3.0 — internal release

- Use QGIS's PDAL Processing tools for native point-cloud reading, crops,
  compression, and Delaunay HAG. Read uncompressed LAS with bundled laspy and
  calculate forest metrics with bundled released PyForestScan 0.4.1. Python
  PDAL bindings are no longer required in the QGIS interpreter.
- Write rasters and sample terrain through GDAL supplied by QGIS. Preserve
  original point dimensions and coordinate precision in HAG outputs.
- Add **Plugins → PyForestScan → Set up PyForestScan** for an optional private
  engine with exact package manifests for both macOS architectures, Windows
  x64, and Linux x64. Downloads use HTTPS and SHA256 verification. Installation
  is cancellable, repairs activate only after a scientific self-test, and no
  QGIS Python packages or shell settings are changed.
- Keep analysis in the Processing Toolbox. Select native capabilities per
  workflow and fall back to an installed engine; setup also permits explicitly
  choosing the isolated engine for source-specific reader limitations.
- Correct native crop precision at large map coordinates and fractional tile
  boundary handling. Safely stage HAG outputs whose directory contains `#`.
- Include all released PyForestScan source, dependency licenses/provenance, and
  offline installation and backend guidance in the ZIP.

### Migrating from 0.2.0

Install the new ZIP and restart QGIS. Run **Check PyForestScan Dependencies**;
install the optional engine only if the chosen workflow needs it. Existing
Processing algorithm IDs, parameter choices, and output types are retained.
The old conda environment is no longer required for community installation.
Native point-cloud workflows need QGIS 4.2 or later; older versions and
statistical outlier cleaning use the optional engine. This avoids older native
converters dropping extra point dimensions during LAS/LAZ conversion.

## 0.2.0 — internal release

Experimental release for internal distribution with QGIS 3.40–3.x and 4.x,
pinned to released PyForestScan 0.4.1.

- Make the Processing Toolbox the complete user interface, with standalone
  forest tools, ordinary model/batch inputs and outputs, and bundled offline
  help. No separate control panel is required.
- Accept loaded point-cloud layers, plus a file/EPT source alternative. Resolve
  the source CRS from the layer or metadata, reject ambiguous inputs, and
  transform explicit crop extents and polygon geometry to the source CRS.
- Add a persistent **Add Height Above Ground** LAS/LAZ workflow using classified
  ground or a DTM, retaining native LAS/LAZ/COPC XYZ precision and additional
  point dimensions. EPT coordinates use the output LAS encoding precision; Z
  remains elevation.
- Add focused CHM, PAD, PAI, FHD, canopy cover, voxel statistics, and raster-input
  rumple tools. Keep the combined metrics algorithm for one-read workflows.
- Use PyForestScan's forest metric definitions, including PAD-based canopy cover
  and triangulated CHM rumple. Pass height thresholds in metres and propagate
  the actual vertical bin size.
- Deliver PAD as a multiband GeoTIFF ordered from the lowest height bin upward.
  Expose typed outputs for Processing models.
- Replace the upstream EPT tile-writing path with aligned per-tile metric
  processing, buffered HAG reads, and cancellation checks between tiles. Add
  canopy cover to the advanced EPT metric choices.
- Document the full public PyForestScan 0.4.1 API coverage and the general
  preprocessing/visualization workflows left to QGIS or Python.

### Migrating from 0.1.1

Use the updated environment definition and restart QGIS. The old
`pyforestscan==0.2.4` environment is not the dependency contract for this release.
QGIS versions older than 3.40 are no longer declared supported.

`pyforestscan:calculatelidarmetrics` keeps its ID and the first seven metric
indices: CHM (0), PAD (1), PAI (2), FHD (3), canopy cover (4), point density (5),
and rumple (6). Voxel statistics is appended at index 7. The `INPUT` path/URL
parameter remains available; use `INPUT_LAYER` for a loaded point cloud, and
leave the other source input empty. HAG method indices remain existing heights
(0), Delaunay (1), and DTM (2) for metric tools.

Review saved models and scripts before rerunning them:

- `INPUT_CRS` assigns only missing source CRS information. It does not reproject
  a cloud, and the project CRS no longer supplies a fallback. Reproject to a
  projected CRS in metres before forest metric processing.
- PAD is now `pad.tif`, a multiband raster, rather than `pad.npz`. Update any
  downstream NumPy readers.
- Canopy cover now follows `1 - exp(-k × PAI_above_threshold)` with default
  `k = 0.5`; the old return-fraction calculation is not retained. Rumple and
  non-unit vertical-bin results can also change. Recalculate baselines rather
  than treating old and new outputs as interchangeable.
- CHM interpolation is disabled because of an upstream grid-coordinate issue.
  Use an explicit QGIS raster gap-filling step if your analysis needs it.
- Explicit extent/polygon inputs determine the processing area. Display
  styling or viewport clipping is not an input filter, and unsupported layer
  subsets or unsaved changes must be saved to a source first.

The package remains experimental and is not published to the public QGIS plugin
repository. Runtime verification scope and commands are recorded in
[tests/README.md](tests/README.md).

## 0.1.1

- Add QGIS 4/Qt 6 compatibility alongside QGIS 3.
- Accept file paths and EPT URLs in the combined metrics tool.
- Add package and runtime checks, including recorded QGIS 3.40.3 and 4.2.3
  validation using the preceding dependency set.
