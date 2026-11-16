"""Road workflows on real terrain, run through the plugin's own UI code path."""
import os

import numpy as np
import pytest
from qgis.core import QgsGeometry

import helpers as h

FEATHER = 20.0


def finished(outcome):
    assert outcome.ran, "The plugin never started the edit task"
    assert outcome.ok, "The edit failed: {!r}".format(outcome.exception)
    return outcome.result


def assert_engineering(session, terrain, outcome, line, side, distance, grade_limit=None,
                       cross_extra=0.0, grade_extra=1.0):
    """Check the properties a road designer cares about.

    Cross-road level: on every station, the output surface across the road
    (inner 60 % of the width) differs by at most
        crossfall setting (0.10) + pixel * longitudinal grade
    because a sample line that is not square to the grid mixes two stations.
    Grade: sampled along the road, at most the limit + `grade_extra` points.
    """
    result = finished(outcome)
    out, info = h.read_raster(result['output_path'])
    src = terrain['array']
    pixel = terrain['pixel']
    # Output grid equals source grid.
    assert info['gt'] == terrain['gt'] and info['size'] == terrain['info']['size']
    assert info['wkt'] and out.shape == src.shape
    # Cells beyond corridor + transition are unchanged.
    corridor = session.reviewed[-1]['corridor']
    changed = out != src
    assert changed.any()
    reach = h.max_distance_to(corridor, terrain['gt'], changed)
    assert reach <= FEATHER + 1.5 * pixel, "Cells changed {:.1f} m from the corridor".format(reach)
    # Level across the width, and grade along the length.
    # A bilinear sample reads up to one cell beyond its point. Keep every
    # sample one cell inside the corridor edge, so it never reads transition cells.
    half = min(0.6 * distance, distance - pixel)
    assert half > 0, "Corridor is narrower than one cell; the test cannot judge it"
    if side == 'both':
        offsets = np.linspace(-half, half, 7)
    elif side == 'left':
        offsets = np.linspace(pixel, distance - pixel, 5) if distance > 2 * pixel else np.array([distance / 2])
    else:
        offsets = -np.linspace(pixel, distance - pixel, 5) if distance > 2 * pixel else np.array([-distance / 2])
    props = h.road_properties(out, terrain['gt'], line, offsets, pixel, trim=1.5 * distance)
    limit_grade = grade_limit if grade_limit is not None else 100.0 * (np.abs(np.diff(props['profile'])) / pixel).max()
    cross_tol = 0.10 + pixel * limit_grade / 100.0 + cross_extra
    assert props['cross_range'].max() <= cross_tol, (
        "Road is not level across: worst range {:.3f} m, allowed {:.3f} m".format(props['cross_range'].max(), cross_tol))
    if grade_limit is not None:
        assert props['grade'].max() <= grade_limit + grade_extra, (
            "Grade {:.2f} % above the {:.1f} % limit".format(props['grade'].max(), grade_limit))
    return result, out, props


def test_hillside_road_both_sides(session, terrain, hillside):
    """A surveyor draws a straight road across a real hillside and asks for
    10 m of corridor on each side. The road must be level across its 20 m
    width, must follow the ground along its length, and must not change
    ground beyond the 20 m cut/fill transition."""
    out = session.run_digitized(hillside, session.settings('both', distance=10.0, side='both'))
    result, edited, props = assert_engineering(session, terrain, out, hillside, 'both', 10.0)
    # The road edits the ground, so the corridor itself holds many changed cells.
    expected_cells = 200 * 20 / terrain['pixel'] ** 2
    assert result['edited_pixels'] == pytest.approx(expected_cells, rel=0.15)
    # Corridor width: 2 x 10 m at 200 m length is about 4000 m2.
    corridor = session.reviewed[-1]['corridor']
    assert corridor.area() == pytest.approx(200 * 20, rel=0.02)
    # Terrain following: the road profile stays near the ground along the line.
    ground = h.sample(terrain['array'], terrain['gt'], *h.line_points(hillside, terrain['pixel'])[1:3])
    assert abs(props['profile'].mean() - ground.mean()) < 3.0


def test_hillside_road_left_side_only(session, terrain, hillside):
    """The same hillside road, but the owner has land only on the left of the
    drawn line. Only the left 10 m is corridor. The right side of the line stays
    untouched except for the transition."""
    out = session.run_digitized(hillside, session.settings('left', distance=10.0, side='left'))
    result, edited, props = assert_engineering(session, terrain, out, hillside, 'left', 10.0)
    corridor = session.reviewed[-1]['corridor']
    assert corridor.area() == pytest.approx(200 * 10, rel=0.02)
    # Right of the line there is no corridor. Compare the change 12 m to the right
    # (transition only) with the change in the middle of the left corridor (road).
    stations, x, y, tx, ty = h.line_points(hillside, terrain['pixel'])
    change = edited - terrain['array'].astype(float)
    away = (stations > 30) & (stations < hillside.length() - 30)
    right = np.abs(h.sample(change, terrain['gt'], x + ty * 12.0, y - tx * 12.0))[away]
    road = np.abs(h.sample(change, terrain['gt'], x - ty * 5.0, y + tx * 5.0))[away]
    assert right.mean() < road.mean(), "The right side changed as much as the road itself"


def test_road_from_selected_geopackage_line(session, terrain, hillside):
    """A planner has the road centerline in a GeoPackage layer. They select the
    one line feature and run the edit with a 8 m buffer on each side. The result
    must equal what a digitized line with the same geometry produces."""
    layer = h.make_layer('Line', session.project_crs, session.inputdir, 'roads', [hillside])
    # An unselected second road must not be used.
    other = h.polyline([(hillside.asPolyline()[0].x() + 40, hillside.asPolyline()[0].y() + 60),
                        (hillside.asPolyline()[0].x() + 40, hillside.asPolyline()[0].y() + 100)])
    layer2 = h.make_layer('Line', session.project_crs, session.inputdir, 'roads2', [other, hillside])
    settings = session.settings('line_road', distance=8.0, side='both')
    out = session.run_selected_line(layer2, 2, settings)
    result, edited, props = assert_engineering(session, terrain, out, hillside, 'both', 8.0)
    centerline = session.reviewed[-1]['line']
    assert centerline.length() == pytest.approx(hillside.length(), rel=1e-6)
    shp = result['centerline_path']
    assert os.path.isfile(shp)
    # The saved centerline is the selected feature, not the other one.
    from osgeo import ogr
    ds = ogr.Open(shp); feat = ds.GetLayer(0).GetNextFeature()
    assert ds.GetLayer(0).GetFeatureCount() == 1
    assert feat.GetGeometryRef().Length() == pytest.approx(hillside.length(), rel=1e-6)


def test_road_from_selected_polygon_with_suggested_centerline(session, terrain, hillside):
    """A road is mapped as a polygon (a 14 m wide strip). The plugin suggests a
    centerline inside it. The user accepts it. The road must be level across the
    strip and follow the terrain along the suggested line."""
    from qgis.core import Qgis
    strip = hillside.buffer(7.0, 8, Qgis.EndCapStyle.Flat, Qgis.JoinStyle.Miter, 2.0)
    layer = h.make_layer('Polygon', session.project_crs, session.inputdir, 'road_polygons', [strip])
    settings = session.settings('poly_road', mode='road')
    out = session.run_selected_polygon(layer, [1], settings)
    result = finished(out)
    reviewed = session.reviewed[-1]
    suggested = reviewed['line']
    assert suggested is not None and suggested.length() > 0.8 * hillside.length()
    # The suggestion stays inside the polygon.
    assert strip.buffer(0.01, 4).contains(suggested)
    edited, info = h.read_raster(result['output_path'])
    pixel = terrain['pixel']
    props = h.road_properties(edited, terrain['gt'], suggested, np.linspace(-4.2, 4.2, 7), pixel, trim=15.0)
    # Polygon centerlines come from a raster skeleton, so allow one extra cell of mixing.
    grade = 100.0 * np.abs(np.diff(props['profile'])).max() / pixel
    assert props['cross_range'].max() <= 0.10 + pixel * grade / 100.0 + 0.30
    changed = edited != terrain['array']
    assert h.max_distance_to(reviewed['corridor'], terrain['gt'], changed) <= FEATHER + 1.5 * pixel


@pytest.fixture
def bend_site(terrain, open_ground):
    return open_ground


def test_sharp_bend_road(session, terrain, open_ground):
    """A road makes a 90 degree corner on open hillside (90 m legs, 10 m buffer
    each side). Engineers expect the road to be level across at the corner, and
    the plugin must either build it or say clearly why it cannot."""
    x, y = open_ground
    line = h.bend_line((x - 90, y - 40), 0.0, 90.0, 90.0)
    out = session.run_digitized(line, session.settings('bend', distance=10.0, side='both'))
    result, edited, props = assert_engineering(session, terrain, out, line, 'both', 10.0, cross_extra=0.15)
    assert result['turn_plateau_ranges'], "A 90 degree corner should be reported as a levelled turn"
    text = open(result['report_path'], encoding='utf-8').read()
    assert 'levelled' in text


def test_short_curve_road(session, terrain, open_ground):
    """A road turns through a tight 25 m radius curve (a short, 90 degree arc)
    with straight tails. The corridor is 8 m on each side. The surface must be
    level across the curve and unchanged far from the road."""
    x, y = open_ground
    line = h.bend_line((x - 90, y - 50), 0.0, 40.0, 90.0, radius=25.0)
    out = session.run_digitized(line, session.settings('curve', distance=8.0, side='both'))
    assert_engineering(session, terrain, out, line, 'both', 8.0, cross_extra=0.15)


def test_steep_slope_with_small_grade_limit(session, terrain, steep_hillside):
    """A road is drawn straight up a steep real slope, but the owner allows
    only 4 % grade. The gradient-controlled profile must stay at or under
    about 4 % along the road, and it needs bigger cuts and fills than the plain
    terrain-following road."""
    slope = abs(np.diff(h.sample(terrain['array'], terrain['gt'], *h.line_points(steep_hillside, terrain['pixel'])[1:3])))
    ground_grade = 100.0 * slope.mean() / terrain['pixel']
    assert ground_grade > 6.0, "Ground must be steeper than the limit for this to mean anything"
    free = session.run_digitized(steep_hillside, session.settings('free', distance=12.0, profile_method='terrain'))
    free_result = finished(free)
    limited = session.run_digitized(
        steep_hillside, session.settings('limited', distance=12.0, profile_method='grade', maximum_grade=4.0))
    result, edited, props = assert_engineering(session, terrain, limited, steep_hillside, 'both', 12.0, grade_limit=4.0)
    assert result['max_cut'] + result['max_fill'] >= free_result['max_cut'] + free_result['max_fill']
    # The plugin's own sampled number must also respect the limit it was given.
    assert result['raster_max_grade'] <= 4.0 + 0.1 + 1e-6 or result['raster_grade_exceedances'] is not None


def _punch_nodata(terrain, tmp_path, line, hole_cells=(6, 6)):
    """Copy of the real DEM with a block of cells set to NoData (-9999) beside
    the middle of the road, as a void in a real survey would be."""
    array = terrain['array'].astype('float32').copy()
    mid = line.interpolate(line.length() / 2).asPoint()
    c, r = h.xy_to_pixel(terrain['gt'], mid.x(), mid.y())
    c, r = int(round(c)), int(round(r))
    hole = np.zeros(array.shape, bool)
    hole[r - 1:r - 1 + hole_cells[0], c - 1:c - 1 + hole_cells[1]] = True
    array[hole] = -9999.0
    path = str(tmp_path / 'dem_with_void.tif')
    h.write_raster(path, array, terrain['gt'], terrain['info']['wkt'], nodata=-9999.0)
    return path, hole


def test_road_across_nodata_void(make_session, terrain, hillside, tmp_path):
    """The DEM has a small void (NoData) where the road runs. The road is built
    around it. NoData cells must stay NoData with the same marker, and the
    report must say that cells were skipped."""
    path, hole = _punch_nodata(terrain, tmp_path, hillside)
    session = make_session(path)
    out = session.run_digitized(hillside, session.settings('void', distance=10.0))
    result = finished(out)
    edited, info = h.read_raster(result['output_path'])
    assert info['nodata'] == -9999.0
    assert (edited[hole] == -9999.0).all(), "NoData cells were changed"
    assert ((edited == -9999.0) == hole).all(), "NoData appeared in new places"
    assert result['skipped_nodata_pixels'] > 0
    assert 'NoData cells in the corridor were left unchanged' in open(result['report_path'], encoding='utf-8').read()
    assert info['gt'] == terrain['gt'] and edited.shape == terrain['array'].shape
    # Away from the void the road is still level across.
    props = h.road_properties(np.where(edited == -9999.0, np.nan, edited), terrain['gt'], hillside,
                              np.linspace(-6, 6, 7), terrain['pixel'], trim=15.0)
    ok = np.isfinite(props['cross_range'])
    assert ok.sum() > 10
    assert props['cross_range'][ok].max() <= 0.10 + terrain['pixel'] * 0.15 + 0.3


def test_road_leaves_the_dem(session, terrain):
    """The drawn road runs off the east edge of the DEM. The part inside is
    built, the part outside cannot be, and the report warns about it. The grid
    size must not change."""
    gt, shape = terrain['gt'], terrain['array'].shape
    x_edge = gt[0] + shape[1] * gt[1]
    y_mid = gt[3] + shape[0] * gt[5] / 2
    line = h.polyline([(x_edge - 150.0, y_mid), (x_edge + 100.0, y_mid)])
    out = session.run_digitized(line, session.settings('off_edge', distance=8.0, feather_m=10.0))
    result = finished(out)
    edited, info = h.read_raster(result['output_path'])
    assert edited.shape == shape and info['gt'] == gt
    assert result['outside_dem_area'] > 0
    assert 'outside the DEM' in open(result['report_path'], encoding='utf-8').read()
    assert (edited != terrain['array']).any()


def test_road_fully_outside_the_dem_is_refused(session, terrain):
    """The user draws the road in the wrong place, beside the DEM. The plugin
    must refuse and leave no files."""
    gt, shape = terrain['gt'], terrain['array'].shape
    x_edge = gt[0] + shape[1] * gt[1]
    line = h.polyline([(x_edge + 200.0, gt[3] - 100.0), (x_edge + 400.0, gt[3] - 100.0)])
    before = h.files_in(session.outdir)
    out = session.run_digitized(line, session.settings('nowhere', distance=8.0))
    assert out.ok is False
    assert 'corridor' in str(out.exception).lower() or 'pixel' in str(out.exception).lower()
    assert h.files_in(session.outdir) == before
