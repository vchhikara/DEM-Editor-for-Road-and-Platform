# DEM Editor for Roads and Platforms

Version 0.4.3 creates a new DEM for a terrain-following road or a level platform in QGIS. Define a corridor by digitizing a line, selecting a line, or selecting polygons and reviewing an approximate centerline. The plugin can apply cut/fill transitions, estimate changes, save a difference raster and provide a plain-language report.

The declared compatibility range is QGIS 3.34–3.99. The plugin is marked experimental while broader terrain and interactive testing continue. GDAL, NumPy and SciPy are required in QGIS Python. Use a file-based, single-band DEM and suitable projected metre-based project and DEM coordinate systems.

Start with the [user guide](HELP.md). The [changelog](CHANGELOG.md), [bug-fix notes](BUG_FIXES.md) and [publication checklist](PUBLISHING.md) describe the release and remaining checks. The source repository is https://github.com/vchhikara/DEM-Editor-for-Road-and-Platform .

Results are preliminary terrain models. Sampled grade and flatness checks do not certify every location, and a DEM does not determine soil stability, drainage or safe road design. Validate inputs and outputs independently before construction use.

Author: Vipul Chhikara — vipul.chhikara@gmail.com. Licensed under GPL; see [LICENSE](LICENSE).
