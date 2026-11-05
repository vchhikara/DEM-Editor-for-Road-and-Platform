# DEM Editor for Roads and Platforms

Copyright (c) Vipul Chhikara. Licensed under the GNU General Public License,
version 3 or (at your option) any later version; see `LICENSE`.

QGIS plugin that edits a DEM inside a road corridor. The corridor is either
digitized as a new centerline or taken from selected polygons, buffered when
needed, and used to define the edited surface. An optional outside transition
blends the surrounding terrain into that surface.

Requires QGIS 3.34 or newer, with the GDAL, NumPy and SciPy Python modules that
QGIS bundles.

## Installation

Copy this folder into your QGIS profile's `python/plugins` directory (or install
the folder as a ZIP from the Plugin Manager), then enable **DEM Editor for Roads and Platforms** in the Plugins manager.

## Edit modes

- **Road (terrain-hugging):** the corridor is flattened across its width while
  following the terrain along the road. At each station the plugin uses the
  median valid DEM elevation across the buffer, then smooths this profile along
  the road. This tends to balance cut and fill within the sampled corridor;
  it does not guarantee a minimum cut volume or an engineered road design.
- **Flat corridor:** the whole corridor is set to a single elevation, taken
  either from an entered value or from the average height of the corridor edge
  (median by default, mean optional).

The outside transition width blends the edited road or flat area into the
original DEM outside the corridor. Ground above the edited surface is lowered
and ground below it is raised, giving a smooth visual cut-and-fill transition.
The transition is a smooth blend, not an engineered slope with a specified
grade, and may extend beyond the selected buffer or polygon. Use zero for a
sharp edge. The default transition width is 20 m.

## Workflow

1. Load a single-band DEM raster and set the project to a projected CRS with
   meter units.
2. Start **DEM Editor for Roads and Platforms** from the toolbar or the plugin menu.
3. Choose the corridor source:
   - **Digitize a new centerline:** after accepting the dialog, click vertices
     on the canvas. Double-click, press Enter, or right-click to finish. Escape
     cancels the current line; pressing Escape again exits the tool.
   - **Use selected polygon features:** choose the polygon layer and select
     either **Selected features only** (select polygons before opening the
     tool) or **All features in this layer**. The dialog reports the selection
     and layer counts. Only Flat mode is available for polygons.
4. Choose the edit mode and the DEM. The output paths default to the DEM
   folder.
5. The edit runs in a background task with progress and can be cancelled.

## Outputs

- A new GeoTIFF (`<dem>_road_edited.tif` by default) with the same CRS, extent,
  resolution and pixel type as the source. The source DEM is never modified.
  If the chosen path already exists, a numeric suffix is added
  (`_2`, `_3`, ...) so no earlier result or loaded layer is overwritten.
- A separate corridor shapefile (`<dem>_road_corridor.shp` by default) in the
  DEM CRS. Each edit creates a new shapefile containing only its corridor, with
  `mode`, `dist_m`, `side`, `stat`, `source`, `dem_path` and `edit_date`
  attributes. If the path already exists, a numeric suffix is added.
- Both outputs are added to the project when the edit finishes.
- Earlier corridor layers remain in the project and can be hidden or removed
  using the Layers panel. They are not copied into the new corridor file.
- While the digitizing tool stays active, every new line edits the result of the
  previous run (so several roads accumulate in the terrain) and writes the next
  suffixed GeoTIFF.

## Notes and limitations

- The DEM must be a file-based single-band raster (for example a GeoTIFF). Both
  the project and DEM must use projected CRSs in meters. Reproject a geographic
  DEM before editing. Elevations are assumed to be in the desired vertical units.
- A DEM in a different CRS from the project is supported; the corridor is
  transformed automatically.
- Nodata pixels inside the corridor are left untouched and reported in the
  result message.
- Road mode needs a digitized centerline; polygon input is flat-only because a
  polygon alone does not define a reliable longitudinal axis.
- The edited raster is a new file; there is no in-place overwrite or undo of the
  raster edit.
- Sharp bends can still produce self-intersecting buffers; invalid geometry is
  repaired where possible, otherwise the run is refused with a message.
- The output uses the original DEM resolution and data type. A buffer narrower
  than a pixel may contain no pixel centers; integer DEMs round elevations.
- Both sides uses the entered distance on each side of the centerline: a 10 m
  buffer gives a road approximately 20 m wide, plus the outside transition.
- The result message reports how many elevation values changed. If this is zero,
  check the selected DEM, corridor, resolution, and elevation settings.
