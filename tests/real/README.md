# Real-terrain tests

These tests run the plugin on a real DEM, not on a made-up surface. They call the same plugin methods that the QGIS dialog calls. Only the review window and the QGIS task manager are replaced, because a test has no user.

## Choose the DEM

1. Set `DEM_TEST_FILE` to a GeoTIFF or ASC with real elevations.
2. If the variable is not set, the tests search a short list of known folders (see `KNOWN_DEM_PATHS` in `helpers.py`).
3. If no DEM is found, every test skips and prints the reason.

The DEM must have one band and a projected CRS with metre units. Tests that need other CRS types or data types make their copies in a temporary folder from the same terrain.

```bash
export DEM_TEST_FILE="/path/to/real_dem.tif"
```

## Run

Create the venv once (system Python lacks SciPy):

```bash
python3 -m venv --system-site-packages /tmp/demvenv && /tmp/demvenv/bin/pip install pytest scipy
```

Run the tests:

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python:/usr/lib/python3/dist-packages /tmp/demvenv/bin/python -m pytest -q tests/real
```

Tests find their places on the terrain by themselves (a 5 to 8 percent hillside, a steep slope, open ground). They skip with a message if the DEM has none.

## What each file models

`test_real_road.py`
- Road across a hillside, both sides, then left side only.
- Road from one selected line in a GeoPackage layer.
- Road from a selected polygon with the suggested centerline.
- Sharp 90 degree bend and a short 25 m radius curve.
- Steep slope with a 4 percent grade limit.
- Road over a NoData void, road that leaves the DEM, road fully outside the DEM.

`test_real_platform.py`
- Flat pad at a typed elevation.
- Flat pad at the mean and at the median of its edge.
- Cut and fill transition around a raised pad.

`test_real_outputs.py`
- Existing output files are never overwritten. The source DEM is never changed.
- Cancel at three stages and during publication, report failure, NoData clash: no files remain.
- Projected metre CRS accepted (UTM with a Web Mercator project). Geographic and US-foot CRS refused.
- Int16 DEM and Float32 DEM keep their rounding rules.
- Report, difference raster and cut/fill numbers equal a NumPy recomputation.

## What the tests check

- Level across the road: range across the width is at most 0.10 m plus cell size times the road grade.
- Grade along the road: at most the limit plus 1 percentage point.
- Cells further than the transition width (plus 1.5 cells) from the corridor are unchanged.
- NoData cells stay NoData. Output grid equals source grid.
