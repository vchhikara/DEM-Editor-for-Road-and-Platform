# Changelog

All notable changes to this project are listed here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.3-rc.1] - 2026-11-14

Pre-release. Experimental. Broader terrain and interactive tests remain open.

### Added
- Bundled in-QGIS help and version-specific first-open notes.
- CRS area-of-use and local distance-distortion review, DEM coverage and NoData warnings.
- Report of sampled output grade, cross-road checks, local levelling and large earthworks.
- Polygon-centerline review. The plugin rejects disconnected road polygons.
- Resolution guidance and a scrollable settings window.

### Changed
- Declared QGIS range is 3.34 to 3.99.
- Fractional output is retained where the chosen data type permits it.
- Platform rounding is reported. NoData collisions are rejected.
- Sharp-bend and grade handling.

### Fixed
- Online XYZ and WMS basemaps no longer appear in the DEM selector.
- A GDAL open error no longer crashes the settings window.
- Abandoned digitizing preview bands are removed when the corridor source changes or the plugin unloads.
- Unfinished lines survive a temporary switch of map tool.

### Safety
- Existing outputs and common sidecar files are protected.
- Output files are staged before publication and cleaned up after cancellation or failure.

## [0.4.1] - 2026-11-11

Local test build. The installed plugin showed the version string `0.40.1`.
QGIS compares `0.40.1` as higher than `0.4.3`. Install 0.4.3 explicitly from ZIP to replace it.

### Added
- Road preview cautions and a plain-language cut/fill and DEM comparison report.

### Changed
- Digitized corners are preserved. Road stations are densified. Cross-sections join as a triangular road surface.
- Grade-limited profiles fit the sampled terrain with constrained optimization.

### Fixed
- The no-change warning no longer appears when a transition remains unfinished.

## [0.4.0] - 2026-11-11

Local test build. The installed plugin showed the version string `0.40`.

### Added
- Selected line and polygon roads, digitizing review and undo, resolution guidance and optional interpolation.
- Road cross-section sampling, profile and grade choices, cut/fill slopes, centerline and difference output.

### Changed
- QGIS compatibility is 3.34 to 3.99.

## [0.3.9] - 2026-11-07

### Fixed
- Ignored cleanup errors are logged instead of passed silently.
- Scoped Qt 6 names for `QgsTask.Flag` and `QFormLayout.FieldGrowthPolicy`.

## [0.3.8] - 2026-11-07

### Added
- Tests that run full Flat and Road edits on a synthetic DEM.

### Changed
- The plugin is no longer marked experimental.

## [0.3.7] - 2026-11-07

### Changed
- Scoped Qt 6 and QGIS 4 enum names.
- Map units compare with `Qgis.DistanceUnit.Meters`.
- `qgisMaximumVersion` is 4.99.

## [0.3.6]

### Added
- Project homepage, source repository and issue tracker links.

## [0.3.5]

### Changed
- Plugin description revised for clarity.

[Unreleased]: https://github.com/vchhikara/DEM-Editor-for-Road-and-Platform/compare/v0.4.3-rc.1...HEAD
[0.4.3-rc.1]: https://github.com/vchhikara/DEM-Editor-for-Road-and-Platform/compare/v0.3.9...v0.4.3-rc.1
[0.3.9]: https://github.com/vchhikara/DEM-Editor-for-Road-and-Platform/compare/v0.3.8...v0.3.9
[0.3.8]: https://github.com/vchhikara/DEM-Editor-for-Road-and-Platform/releases/tag/v0.3.8
