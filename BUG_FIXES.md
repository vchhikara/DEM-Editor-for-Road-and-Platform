# Version 0.4.3 — changes and verification

Compared with the published 0.3.9 baseline, this candidate adds selected line and polygon workflows, digitizing undo and resumption, explicit geometry review, resolution guidance and optional interpolation, profile and grade controls, cut/fill reporting, and optional difference and sample-point outputs.

The 0.4.3 work additionally improves fractional output precision, detects NoData collisions and incomplete coverage, protects existing output files and common sidecars, cleans staged outputs on failures and cancellation, checks CRS suitability at a representative location, and reports sampled output grades and cross-road differences. Sharp or conflicting bends can be locally levelled and are reported. The median profile is not a guaranteed earthwork balance. An acknowledgement dialog makes quality warnings visible.

Controlled and selected real-DEM tests were run with QGIS 3.34.14 and 3.44.12. They covered road and platform outputs, parameter sensitivity, selected and digitized geometry, polygon review, interpolation, raster precision and masks, report consistency, cancellation and package lifecycle. These tests do not cover every QGIS version, terrain, geometry or operating system. The candidate remains experimental until broader interactive and terrain checks are completed.

Important limitations include sampled rather than exhaustive output checks, possible cross-road warnings on steep curves, potentially large earthworks when a small grade limit is requested, approximate polygon centerlines, and uncertain vertical units/datum. CRS area-of-use and distortion checks are only local prompts. Engineering review is required.

For operating steps, see [HELP.md](HELP.md). For publication checks, see [PUBLISHING.md](PUBLISHING.md).
