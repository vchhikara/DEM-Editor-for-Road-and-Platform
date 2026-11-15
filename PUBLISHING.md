# Publication checklist — 0.4.3 candidate

Status: experimental test candidate. Do not describe it as a verified construction design tool. Broader terrain and interactive QGIS tests remain outstanding.

## Before upload

- Confirm the public repository source and documentation match the exact candidate ZIP. Release steps and tagging rules are in [RELEASING.md](RELEASING.md).
- Run representative manual road and platform edits, including difficult bends, polygons, NoData, cancellation and saved-output review. Test additional operating systems and supported QGIS versions where available.
- Confirm the declared range remains QGIS 3.34–3.99; do not claim QGIS 4 support until separately established.
- Validate the ZIP: one ASCII top-level plugin directory, required `__init__.py`, `metadata.txt` and `LICENSE`, correct icon and no caches, test rasters, output files or nested archives. The package must be under 25 MB.
- Confirm the public homepage, source repository and issue tracker links work, the metadata discloses GDAL, NumPy and SciPy, and the licence and third-party resources are appropriate.
- Review the current QGIS publication and approval requirements: https://plugins.qgis.org/docs/publish and https://docs.qgis.org/3.44/en/docs/pyqgis_developer_cookbook/plugins/releasing.html .

## Version and public listing

The QGIS plugin page inspected on 14 November 2026 lists 0.3.9 as the latest stable release and advertises a maximum QGIS version of 4.99. The 0.4.3 candidate declares 3.99. The public listing will not reflect that correction until a suitable update or other repository change is made.

QGIS compares 0.4.3 as newer than 0.3.9, but older than the local test builds that showed the version strings 0.40 and 0.40.1 (changelog entries 0.4.0 and 0.4.1). Users who installed those test builds may not be offered 0.4.3 as an automatic upgrade. The guide describes explicitly installing the new ZIP in the current profile and verifying the installed version. Check this replacement workflow interactively before submission, and confirm that no version later than 0.3.9 was published in the meantime.

No upload or source-repository push for 0.4.3 has been performed as part of this candidate preparation.
