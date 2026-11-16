"""Level platform (flat corridor) workflows on real terrain."""
import numpy as np
import pytest
from qgis.core import QgsGeometry
from scipy.ndimage import binary_erosion

import helpers as h

FEATHER = 20.0


@pytest.fixture
def pad(open_ground):
    """A 60 m x 40 m building pad drawn off the cell grid, on gently sloping real ground."""
    x, y = open_ground
    return QgsGeometry.fromWkt(
        'POLYGON(({a} {b},{c} {b},{c} {d},{a} {d},{a} {b}))'.format(
            a=x - 30.7, c=x + 29.3, b=y - 20.4, d=y + 19.6))


def run_pad(session, pad, name, **over):
    out = session.run_flat_polygon(pad, session.settings(name, mode='flat', **over))
    assert out.ran and out.ok, "Edit failed: {!r}".format(out.exception)
    result = out.result
    edited, _ = h.read_raster(result['output_path'])
    return result, edited


def assert_platform(session, terrain, pad, result, edited, elevation, tol=1e-3):
    src = terrain['array']
    pixel = terrain['pixel']
    inner = h.cells_with_centres_inside(pad, terrain['gt'], src.shape, shrink=0.75 * pixel)
    assert inner.sum() > 20
    assert np.abs(edited[inner].astype(float) - elevation).max() <= tol, "Platform is not level at the requested elevation"
    changed = edited != src
    reach = h.max_distance_to(pad, terrain['gt'], changed)
    assert reach <= FEATHER + 1.5 * pixel, "Cells changed {:.1f} m from the platform".format(reach)
    assert edited.shape == src.shape


def test_platform_at_typed_elevation(session, terrain, pad):
    """A builder wants a flat pad at a typed level, 2 m above the average ground
    under it. Every cell of the pad must sit at that level. Ground beyond the
    20 m cut/fill transition must not change."""
    src = terrain['array']
    inner = h.cells_with_centres_inside(pad, terrain['gt'], src.shape)
    level = float(np.floor(src[inner].mean())) + 2.0 + 0.25
    result, edited = run_pad(session, pad, 'pad_typed', flat_source='value', flat_value=level)
    assert result['applied_value'] == pytest.approx(level)
    assert_platform(session, terrain, pad, result, edited, level, tol=1e-4)
    # Raising the ground only: the pad has fill, and the cut/fill slopes make a transition.
    assert result['max_fill'] > 0
    assert result['transition_pixels'] > 0


def _expected_edge_value(src, pad, gt, statistic):
    """Same rule as the help text: statistic of the cells on the corridor edge."""
    mask = h.cells_with_centres_inside(pad, gt, src.shape)
    ring = mask & ~binary_erosion(mask, structure=np.ones((3, 3), bool))
    values = src[ring].astype(float)
    return float(values.mean() if statistic == 'mean' else np.median(values))


@pytest.mark.parametrize('statistic', ['mean', 'median'])
def test_platform_at_edge_mean_and_median(session, terrain, pad, statistic):
    """A builder asks for a pad at the average (or median) height of its own
    edge, so cut and fill are about balanced at the boundary. The platform must
    sit at that statistic of the real edge cells, recomputed here with NumPy."""
    expected = _expected_edge_value(terrain['array'], pad, terrain['gt'], statistic)
    result, edited = run_pad(session, pad, 'pad_' + statistic, flat_source='edge', statistic=statistic)
    assert result['applied_value'] == pytest.approx(expected, abs=1e-3)
    assert_platform(session, terrain, pad, result, edited, expected, tol=1e-3)


def test_edge_mean_and_median_differ_on_real_ground(terrain, pad):
    """On real, uneven ground the mean and the median of the pad edge are not the
    same number. If they were equal, the two tests above could not tell the
    options apart."""
    mean = _expected_edge_value(terrain['array'], pad, terrain['gt'], 'mean')
    median = _expected_edge_value(terrain['array'], pad, terrain['gt'], 'median')
    if abs(mean - median) < 1e-3:
        pytest.skip("Mean and median are equal on this pad; the DEM is too regular to separate them.")
    assert mean != median


def test_platform_transition_ties_back_to_ground(session, terrain, pad):
    """A pad 6 m above the ground, with a 20 m transition and a 1:2 fill slope
    (rise 0.5 for each 1 m out). Filled cells outside the pad must lie on or
    above the fill slope line, and no cell may change beyond the transition.
    Ground that already lies inside the slope stays as it was."""
    from qgis.core import QgsPointXY
    src = terrain['array']
    inner = h.cells_with_centres_inside(pad, terrain['gt'], src.shape)
    level = float(np.median(src[inner])) + 6.0
    result, edited = run_pad(session, pad, 'pad_slopes', flat_source='value', flat_value=level,
                             feather_m=20.0, cut_slope=1.0, fill_slope=0.5)
    delta = edited.astype(float) - src.astype(float)
    rows, cols = np.nonzero((delta != 0) & ~inner)
    assert rows.size > 20, "A raised pad must make a fill transition around it"
    slack = terrain['pixel'] * 1.5
    for r, c in zip(rows, cols):
        x, y = h.pixel_to_xy(terrain['gt'], c, r)
        d = pad.distance(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        assert d <= 20.0 + slack
        assert delta[r, c] > 0, "Ground below a raised pad can only be filled"
        # On or above the fill slope: level - 0.5 x distance (grid slack allowed).
        assert edited[r, c] >= level - 0.5 * (d + slack) - 1e-3
        assert edited[r, c] <= level + 1e-3
