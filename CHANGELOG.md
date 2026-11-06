# Release notes

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




