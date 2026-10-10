# PyForestScan 0.4.1 API coverage

This inventory covers every public function defined by the **released
PyForestScan 0.4.1** Python modules. The plugin includes the complete released
source under `vendor/pyforestscan`, with its MIT license and source hashes in
`vendor/sources.json`. Internal imports are made relative to keep it separate
from other plugins. A local development checkout is not the release contract.
Bundling source does not create a Processing algorithm for every helper:
PDAL-dependent helpers need Python PDAL and the plotting helpers need their
plotting dependencies. The optional isolated engine supplies that scientific
stack; its worker exposes the supported forest workflows. For direct upstream
scripting, use a separate Python environment with the released library.

**Tool** means an exposed forest workflow. **Internal** means supporting behavior
inside a tool, which may use a QGIS/PDAL adapter instead of directly calling the
similarly named upstream function. **QGIS/Python** means there is no dedicated
plugin tool: use the relevant installed QGIS processing provider, or the Python
function when exact upstream behavior is needed. QGIS provider availability
varies by installation; these are workflow alternatives, not claims of identical
algorithms or results.

Since plugin 0.3.2, the combined metric algorithm is hidden from the toolbox
and model builder and retained only for existing models/scripts. References to
combined metrics below describe this legacy API, not a separate user-facing
tool. The focused forest tools are the interface for new analyses.

## Forest calculations (`pyforestscan.calculate`)

| Public function | Availability in the plugin | Notes |
| --- | --- | --- |
| `assign_voxels` | Internal: PAD, PAI, FHD, canopy cover, combined metrics | Builds the height-binned return grid. XY and Z sizes are metres. |
| `calculate_pad` | Tool: Plant Area Density; internal to PAI/cover | Multiband GeoTIFF replaces the old NPZ delivery. Uses actual bin height and the library's default Beer–Lambert constant of 1. |
| `calculate_pai` | Tool: Plant Area Index; combined and tiled metrics | Integrates PAD using height thresholds in metres and actual bin height. |
| `calculate_fhd` | Tool: Foliage Height Diversity; combined and tiled metrics | Entropy of vertical return proportions. |
| `calculate_chm` | Tool: Canopy Height Model; combined metrics | Maximum `HeightAboveGround` per XY cell. Interpolation is disabled; use QGIS raster gap filling separately. |
| `calculate_rumple` | Tool: Rumple Index from CHM; combined metrics | Uses valid adjacent 2×2 CHM patches and physical pixel sizes. Standalone tool accepts a loaded CHM. |
| `calculate_point_density` | Legacy combined API; Python | Retained returns per m²; no dedicated plugin dialog. Negative/nonfinite HAG is excluded; use QGIS for general density of all source points. |
| `calculate_voxel_stat` | Tool: Forest Voxel Statistics; combined metrics; internal to tiled CHM | Dimension and statistic selection: mean, sum, count, min, max, median, standard deviation. Produces a 2D column statistic, not a 3D voxel cube. Tiled CHM uses maximum HAG on a common grid. |
| `generate_dtm` | QGIS/Python | Terrain generation is outside the forest-metric tool set. Prepare a suitable terrain raster with QGIS/PDAL, or call the upstream function in Python. |
| `calculate_canopy_cover` | Tool: Canopy Cover; combined and tiled metrics | PAD-based Beer–Lambert cover; default extinction coefficient 0.5. Replaces the old plugin's return-fraction calculation. |

All point-cloud metric tools require a saved `HeightAboveGround` dimension and
read it automatically. Since plugin 0.4.0 they do not calculate heights inside
a metric run. Prepare heights in a separate QGIS/PyForestScan step first.

These ten functions are also exported by `pyforestscan.__init__`.

## Point-cloud filters (`pyforestscan.filters`)

| Public function | Availability in the plugin | Notes |
| --- | --- | --- |
| `add_height_above_ground` | Tool: Add Height Above Ground | Delaunay terrain from ground class 2, or a DTM. Writes a new LAS/LAZ with a persistent dimension for later metrics. |
| `filter_hag` | QGIS/Python | Use a QGIS point-cloud expression/filter for general height filtering. Metric height thresholds control the individual calculation, not a persistent filtered-cloud output. |
| `filter_ground` | QGIS/Python | General classification filtering is left to QGIS/PDAL. |
| `filter_select_ground` | QGIS/Python | Select class 2 with a point-cloud filter when preparing a terrain workflow. |
| `filter_pointsourceid` | QGIS/Python | General point-source filtering is outside the forest toolbox. |
| `remove_outliers_and_clean` | Advanced EPT option; otherwise QGIS/Python | Optional cleaning in tiled processing requires the isolated engine; no dedicated cleaning tool. |
| `classify_ground_points` | QGIS/Python | Ground classification is a prerequisite, not an automatic side effect of selecting Delaunay HAG. Use an available QGIS/PDAL ground classifier. |
| `downsample_poisson` | QGIS/Python | General thinning is left to point-cloud preprocessing tools. |
| `downsample_voxel` | QGIS/Python | General voxel thinning is left to point-cloud preprocessing tools. |

## Reading, writing, and validation (`pyforestscan.handlers`)

| Public function | Availability in the plugin | Notes |
| --- | --- | --- |
| `simplify_crs` | Internal CRS responsibility | QGIS CRS handling accepts source definitions without requiring every CRS to reduce to an EPSG code. |
| `load_polygon_from_file` | Internal crop input | Processing accepts polygon layers and selected-feature sources; geometries are transformed to the point-cloud CRS. |
| `get_raster_epsg` | Internal DTM/raster validation | The plugin checks raster CRS and units through its raster/QGIS adapters. |
| `validate_extensions` | Internal input validation | The plugin checks the formats actually supported by each tool. |
| `validate_crs` | Internal CRS validation | Missing or conflicting source CRS is reported; the project CRS is not silently substituted. |
| `read_lidar` | Internal source-reading responsibility | Loaded point-cloud layers resolve to their saved source; local LAS/LAZ/COPC and EPT sources are supported. Explicit extent/polygon crop is applied in source coordinates. |
| `write_las` | Internal output responsibility: Add Height Above Ground | The plugin retains additional dimensions and native LAS/LAZ/COPC XYZ precision. EPT coordinates are serialized to LAS precision. Elevation Z is not normalized in place. |
| `create_geotiff` | Internal raster-output responsibility | The plugin supplies georeferencing, NoData, band metadata, and typed Processing outputs, including a multiband PAD writer. |

These rows describe supported behavior, not a guarantee that each upstream
helper is called directly. Source resolution, output preservation, and QGIS
integration require adapters.

## Tiling and metadata

| Module / public function | Availability in the plugin | Notes |
| --- | --- | --- |
| `process.process_with_tiles` | Advanced tool: Process EPT Tiles | The plugin orchestrates its own tiles around the core metric functions. It does not call the upstream buffered tile writer. This provides an aligned grid, trims buffered reads to the core tile, and checks cancellation between tiles. Supports CHM, PAI, FHD, and canopy cover. |
| `utils.get_srs_from_ept` | Internal EPT metadata responsibility | Source CRS is inferred from metadata; an explicit CRS is an assignment only when missing. |
| `utils.get_bounds_from_ept` | Internal EPT metadata responsibility | Used to determine the processing footprint alongside an optional requested extent. |
| `utils.tile_las_in_memory` | QGIS/Python | No dedicated LAS splitter. Use QGIS/PDAL preprocessing for general tiling; the upstream helper reads its entire input into memory. |

## Visualization (`pyforestscan.visualize`)

| Public function | Availability in the plugin | Notes |
| --- | --- | --- |
| `plot_2d` | QGIS/Python | QGIS point-cloud rendering supplies interactive inspection. The Matplotlib function remains available in Python. |
| `plot_metric` | QGIS/Python | Metric outputs are QGIS raster layers with normal symbology and layouts. |
| `plot_pad` | QGIS/Python | Inspect PAD height bands in QGIS; use the Python function for vertical slice plots. |

`pyforestscan.pipeline` has no public functions: its names begin with `_`.
Private PDAL-stage builders and other underscore-prefixed helpers are excluded
from this public inventory. General preprocessing and Matplotlib plotting are
intentional omissions from the plugin UI, not missing installations of the
library.

## Release-specific behavior

The 0.4.1 CHM interpolation path has a coordinate/grid orientation issue; the
plugin does not expose it. Tiled CHM uses the library's maximum voxel statistic
on an aligned grid. Review raster interpolation and mosaicking explicitly in
QGIS when needed.

Outputs are not promised to reproduce plugin 0.1.1 numerically. This release
updates the library, corrects metre/bin-height handling, uses core canopy-cover
and rumple definitions, and delivers PAD as GeoTIFF. See
[the migration notes](../CHANGELOG.md) before rerunning existing analyses.
