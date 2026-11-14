# Version 0.4.3 release notes

Compared with 0.3.9, this release adds selected line and polygon workflows, review before editing, digitising undo and resumption, DEM resolution guidance and optional resampling, road profile and grade controls, and cut/fill reporting. Version 0.4.3 improves handling of sharp line turns, retains fractional elevations by default, protects existing files and reports sampled output-raster grade and cross-road differences separately from the calculated profile.

Sharp line bends or short curves with conflicting cross-sections may be levelled locally; this can increase earthwork. The report identifies the affected distances. The median terrain fit does not guarantee equal cut and fill. The cross-road threshold is a review indicator, not an engineering standard. Grade and cross-road checks sample the final output array before writing; they do not certify every cell or the reopened file. Grade checks sample road-section midpoints using road cells only, reducing hillside contamination on one-sided buffers. Skipped intervals and genuine exceedances require review. Warnings, incomplete samples and high gradients require review. Interpolating a DEM does not add terrain information. Vertical units and datum must be verified by the user; cubic metres require elevations in metres. Results are preliminary terrain models, not construction designs.

Inputs must be single-band, projected in metres and have default raster scale and offset. Crop large DEMs if the memory estimate is too high. Ambiguous or conflicting road geometry may be rejected; inspect and simplify it. Outputs must use new file names: existing files are not overwritten. QGIS compatibility declared: 3.34–3.99, not QGIS 4. Author: Vipul Chhikara (vipul.chhikara@gmail.com).
---

# Version 0.40.1 upgrade plan

1. Preserve centerline vertices, densify around bends and sample the corridor at adaptive stations; reduce corner rounding and improve cross-section continuity.
2. Keep the road bench separate from outside cut/fill transitions; improve edge-to-ground distances and warn at unresolved transitions.
3. Improve constrained longitudinal profile fitting using terrain samples and a maximum-grade constraint; avoid claiming globally optimized earthworks.
4. Keep existing width, profile, slope and transition controls; add inspectable road-section and cut/fill reporting.
5. Write a plain-language TXT report for every edit, with readable comparison, affected chainage and Take care cautions; optionally save the difference raster.
6. Preserve resolution advice and explicit interpolation workflow. Comparisons use matching source/output grids and disclose interpolation and volume assumptions.
7. Update help, bug-fix notes, changelog and metadata, keeping QGIS 3.34–3.99 and confirmed author information.
8. Validate straight/curved/steep roads, grade constraints, NoData, differences, report data and QGIS 3.34/3.44; assess real DEM before recommending release.

Important limits: DEM-only output is a preliminary terrain model; no geological stability can be inferred. Human review, especially at sharp bends, remains required.



