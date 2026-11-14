# Changelog

## 0.4.3 — experimental candidate

- Exclude online XYZ/WMS basemaps from the DEM selector and handle GDAL open errors without crashing the settings window.
- Remove abandoned digitizing preview bands when changing corridor sources or unloading; preserve unfinished lines when temporarily switching map tools.
- Add CRS area-of-use and local distance-distortion review, DEM coverage and NoData warnings.
- Improve sharp-bend and grade handling; report sampled output grade, cross-road checks, local levelling and large earthworks.
- Retain fractional output where the chosen type permits it; report platform rounding and reject NoData collisions.
- Protect existing outputs and common sidecars, stage files before publication and clean up after cancellation or failure.
- Improve polygon-centerline review and reject disconnected road polygons; provide resolution guidance and a scrollable settings window.
- Show version-specific first-open notes and bundled in-QGIS help; declare QGIS 3.34–3.99.

The following 0.40-series numbers were local test builds. QGIS compares 0.40 and 0.40.1 as higher than 0.4.3 despite their chronological order here.
## 0.40.1 (local test)

- Preserve digitized corners, densify road stations and connect cross-sections as a triangular road surface.
- Fit grade-limited profiles to the sampled terrain using constrained optimization.
- Add road preview cautions and a plain-language cut/fill and DEM comparison report.
- Correct the misleading no-change warning when a transition remains unfinished.

## 0.40

- Add selected line and polygon roads, digitizing review and undo, resolution guidance and optional interpolation.
- Add road cross-section sampling, profile and grade choices, cut/fill slopes, centerline and difference output.
- Restrict QGIS compatibility to 3.34–3.99.

## 0.3.9

- Log ignored cleanup errors instead of passing silently.
- Use scoped Qt 6 names for QgsTask.Flag and QFormLayout.FieldGrowthPolicy.

## 0.3.8

- Add tests that run full Flat and Road edits on a synthetic DEM.
- Mark the plugin as not experimental.

## 0.3.7

- Use scoped Qt 6 and QGIS 4 enum names.
- Compare map units with Qgis.DistanceUnit.Meters.
- Set qgisMaximumVersion to 4.99.

## 0.3.6

- Add project homepage, source repository and issue tracker links.

## 0.3.5

- Revise the plugin description for clarity.

## 0.3.4

- Expand the plugin description and clarify road and platform workflows.

## 0.3.3

- Rename the plugin to DEM Editor for Roads and Platforms.

## 0.3.2

- Add an explicit option to use all features of a polygon layer.

## 0.3.1

- Prefer the active or uniquely selected polygon layer and refresh selection counts.

## 0.3.0

- Calculate each road station from the median terrain across the corridor to help balance cut and fill.
- Report maximum cut and fill in DEM elevation units.

## 0.2.1

- Save each corridor in its own numbered shapefile, rather than appending earlier edits.

## 0.2.0

- Flatten the full corridor across a road while following a smoothed longitudinal terrain profile.
- Level selected polygon or digitized corridors to an elevation or boundary statistic.
- Blend cuts and fills outside the corridor over a configurable transition width.
- Report changed and NoData pixels; require a projected DEM in meters.
- Reduce transition memory by limiting the distance calculation to the corridor surroundings.

The outside transition is a visual smooth blend, not an engineered slope or earthworks design. Check results against the source DEM and project requirements.








