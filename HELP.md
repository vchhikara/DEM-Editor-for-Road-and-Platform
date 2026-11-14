# DEM Editor for Roads and Platforms — user guide (0.4.3)

This QGIS plugin creates a new terrain model for a road corridor or level platform. It can raise and lower DEM cells and apply cut and fill transitions outside the edited area. The result is a preliminary model: a DEM alone cannot establish soil stability, drainage, safe slopes or a construction-ready road.

## Install and prepare

1. In QGIS, open **Plugins > Manage and Install Plugins > Install from ZIP**, choose the plugin ZIP, and enable it. The declared QGIS range is 3.34–3.99; QGIS 4 is not supported. The plugin uses the GDAL, NumPy and SciPy libraries available to QGIS Python.

Elevation values must be in metres for grade and cut/fill calculations. Recognized non-metre vertical-unit labels, such as feet, are rejected. A missing, unfamiliar or incorrect label is not proof that values are in metres: check the source data and vertical datum independently. Convert the elevation values, not just the label, if needed.

If you installed a local test build numbered 0.40 or 0.40.1, QGIS regards that number as newer than 0.4.3 and may not offer an automatic update. Save your work, keep a copy of the test ZIP if you need to return to it, and install the 0.4.3 ZIP explicitly using **Install from ZIP**. This replaces the plugin files for the current QGIS profile. Verify the installed version in the plugin manager and restart QGIS if the old plugin is still loaded. Keep your DEMs and outputs separately; review your settings before running a new edit.
2. Load a file-based, single-band DEM. Set both the project CRS and DEM CRS to suitable projected coordinate systems with metre units. The DEM must have default scale and offset; convert scaled values to physical elevations first. Check the vertical units, vertical datum and NoData metadata yourself.
3. Use a suitable local CRS. The review checks the CRS area of use and estimates grid-distance distortion at one representative point. This is only a prompt, not a survey check. Reproject data when changing CRS; changing its label alone does not transform coordinates.
4. Preserve the original DEM and choose output paths. The plugin creates new files and chooses a numeric suffix if an output name or common sidecar already exists.

## Choose corridor and width

Open **DEM Editor for Roads and Platforms**. The form scrolls on smaller displays; OK and Cancel stay below it.

- **Digitize a new centerline:** choose the side and distance. Click OK and click vertices on the map. Backspace or Ctrl+Z removes the last vertex. Right-click and choose **Finish digitization** or **Clear digitization**. If another tool interrupts an unfinished line, open the plugin again to resume it. Escape clears the current drawing. Finish opens a review: OK starts the edit; Cancel returns to the unfinished line.
- **Use selected line feature:** select exactly one continuous line in QGIS, choose its layer, and set the buffer. The corridor and line are shown for review before editing. Line-buffer ends are flat.
- **Use selected polygon features:** select polygons in QGIS or choose the explicit **All features** scope. In road mode, the plugin suggests a centerline, smooths it when the result remains inside the polygon, and asks for review. Process one continuous road polygon at a time; disconnected road polygons are rejected. Branches or holes can yield several possible routes: the one suggested route determines the profile for the entire polygon. Cancel if it is unsuitable. The source layer is not changed.

For a line buffer, the distance applies to each selected side. Both sides at 5 m gives an approximately 10 m-wide corridor; one side at 5 m gives approximately 5 m. Polygon width comes from the polygon itself.

## DEM resolution and optional interpolation

The dialog reports approximate cells across a line-buffer road. Fewer than 3 cells is coarse; 3–5 is rough; 5 is a practical review guideline, not an engineering minimum or a guarantee. A 10 m road on a 2.5 m grid spans about four cells. For line-based roads, **Use 5 cells** sets a working pixel size of approximately one fifth of the road width and enables interpolation; you may enter another value.

**Interpolate working DEM** creates a separate bilinear-resampled Float64 working raster. Confirm the estimated dimensions and size before processing. A finer grid can improve the shape of the rasterized road, but it does not restore missing terrain detail and uses more memory, storage and time. For skewed raster grids, a north-up working grid is required for cut/fill transitions. Rotated orthogonal grids are supported.

## Road settings

- **Terrain following** uses mean cross-section terrain samples and smoothing. It does not apply the user-entered maximum grade.
- **Gradient controlled** fits a terrain-based longitudinal profile subject to the selected maximum grade.
- **Median terrain fit (not volume balance)** uses median cross-section samples with a grade limit. It does not guarantee equal or minimum cut/fill volumes.

The cross-road quality check samples between 20% and 80% of each tested section, with more points for wider sections and a limit on the number of samples. The report lists the approximate centerline distances of up to ten sections with the largest flagged differences. Raster interpolation can mix neighboring road stations, especially at bends or abrupt grade changes, so a warning does not by itself prove a cross-road defect. The check can also miss problems near road edges, between sections or between samples; inspect the saved raster and cross-sections independently.

Maximum grade is rise divided by horizontal distance, expressed as a percentage. Profile smoothing is a distance in metres; zero selects an automatic window of about five pixels. In grade-controlled modes, the plugin may refit with a lower internal grade limit if raster samples exceed the request. Both values appear in the report. The grade check flags samples more than 0.1 percentage points above the request; it does not verify every point.

The **cross-road review threshold** is a configurable elevation difference for sampled cross-sections, not an engineering standard. Sharp turns and some conflicting curved sections, including unambiguous polygon routes, can be levelled locally. This and adjacent adjustments can increase cut or fill, so inspect the reported distances and earthworks.

Cut and fill slopes are vertical change divided by horizontal distance. The outside transition width limits how far the transition can extend; zero creates a sharp edge. A warning identifies tested edge cells where the transition has not met the original ground and reports their largest elevation difference. It may estimate a distance to the sampled edge elevation assuming level ground beyond the edge; actual terrain can require more or less distance. The plugin warns if no valid transition cells are represented or if no valid outer edge cells are available for the tie-in check. These settings are not a slope-stability design.

## Level platform

Choose **Flat corridor**. Select either a single elevation or the corridor-edge statistic and choose mean or median. The area can come from a digitized line buffer, a selected line buffer or a polygon. Review the corridor before applying. The result and report show both the calculated elevation and the value stored in the chosen output type. Integer output can round values; floating-point output also has finite precision.

## Review, outputs and checks

Before processing, inspect the displayed geometry and cautions. The preview shows the corridor and any suggested centerline; it does not show the final edited surface. A corridor or transition outside the DEM cannot be fully processed. NoData cells remain unchanged. If an edited value would equal the input NoData marker, processing stops; adjust the edit or prepare a copy with a suitable NoData value.

Each completed edit creates an edited GeoTIFF, a corridor shapefile, a text report, and a centerline shapefile when a line is used. Optional outputs include a difference raster and road sample-point shapefile. The difference is **edited minus baseline**: positive is fill and negative is cut. It uses Float64 for Float64 output or a 32-bit integer baseline, otherwise Float32. If interpolation is selected, the baseline is the interpolated working DEM, not the original grid. Integer DEMs wider than 32 bits and complex-valued rasters are rejected because they cannot be safely treated as real-valued elevations by these calculations.

The report describes the methods, changed areas, approximate cut/fill volumes, sampled grades and cross-road differences, and affected road sections. Volumes equal cubic metres only when vertical elevations are in metres; compaction, shrinkage, transport and material suitability are not modelled. Changes of at most 0.000001 elevation units are excluded from cut/fill counts and totals.

When checks need attention, an acknowledgement window and the report explain the warning. Review NoData gaps, incomplete sampling, steep grades, locally levelled bends, fallback cells, endpoint projections, large cut/fill and unfinished transitions. A cut or fill over five DEM elevation units triggers a review prompt; that number is not an engineering limit. Use QGIS Elevation Profile or equivalent tools to inspect the original and output along and across the road, particularly at bends and steep slopes.

## Limitations

Grade and cross-road checks sample the final array before writing; they do not certify every location. The plugin has been tested on selected examples, not every terrain or QGIS 3 release. It reads full DEM arrays into memory and may ask you to crop a large input. An approximate polygon centerline may be unsuitable even when a preview is produced. Independent engineering and field review remain essential.

Author: Vipul Chhikara — vipul.chhikara@gmail.com


