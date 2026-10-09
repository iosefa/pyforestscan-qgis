# Changelog

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
