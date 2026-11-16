"""Shared helpers for the real-terrain tests.

The helpers drive the plugin through the same methods the QGIS UI calls
(`_centerline_finished`, `_start_from_line`, `_start_from_polygon`, `_run_edit`,
`_run_task`). Only the modal review window and the QGIS task manager are
replaced, because a test has no user and no event loop.
"""
import math
import os
import sys

import numpy as np
from osgeo import gdal
from qgis.core import (QgsApplication, QgsFeature, QgsGeometry, QgsPointXY,
                       QgsProject, QgsRasterLayer, QgsVectorLayer,
                       QgsCoordinateReferenceSystem)
from scipy.ndimage import map_coordinates

gdal.UseExceptions()

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Search list used when DEM_TEST_FILE is not set.
KNOWN_DEM_PATHS = [
    '/home/vipul/My Projects/Qto3D/tasks/live-test-scene/sf_dem_real.tif',
    '/usr/share/qgis/resources/data',
    '/usr/share/qgis/sample_data',
    '/usr/share/gdal',
    os.path.expanduser('~/Downloads'),
    os.path.expanduser('~/Documents'),
]
DEM_SUFFIXES = ('.tif', '.tiff', '.asc')


def find_real_dem():
    """Return (path, note). Path is None when no usable DEM exists."""
    env = os.environ.get('DEM_TEST_FILE')
    if env:
        if os.path.isfile(env):
            return env, 'DEM_TEST_FILE'
        return None, 'DEM_TEST_FILE points to a missing file: {}'.format(env)
    for entry in KNOWN_DEM_PATHS:
        if os.path.isfile(entry):
            return entry, 'known path'
        if os.path.isdir(entry):
            for name in sorted(os.listdir(entry)):
                if name.lower().endswith(DEM_SUFFIXES):
                    return os.path.join(entry, name), 'known folder'
    return None, 'No real DEM found. Set DEM_TEST_FILE to a GeoTIFF or ASC with real elevations.'


def read_raster(path):
    ds = gdal.Open(path)
    band = ds.GetRasterBand(1)
    array = band.ReadAsArray()
    info = {'gt': ds.GetGeoTransform(), 'nodata': band.GetNoDataValue(),
            'wkt': ds.GetProjection(), 'size': (ds.RasterXSize, ds.RasterYSize),
            'dtype': array.dtype}
    ds = None
    return array, info


def write_raster(path, array, gt, wkt, nodata=None):
    gdal_type = {np.dtype('float32'): gdal.GDT_Float32, np.dtype('float64'): gdal.GDT_Float64,
                 np.dtype('int16'): gdal.GDT_Int16, np.dtype('int32'): gdal.GDT_Int32}[array.dtype]
    ds = gdal.GetDriverByName('GTiff').Create(path, array.shape[1], array.shape[0], 1, gdal_type)
    ds.SetGeoTransform(gt)
    ds.SetProjection(wkt)
    band = ds.GetRasterBand(1)
    if nodata is not None:
        band.SetNoDataValue(float(nodata))
    band.WriteArray(array)
    band.FlushCache()
    ds = None
    return path


# ---------------------------------------------------------------- geometry

def pixel_to_xy(gt, col, row):
    return gt[0] + (col + 0.5) * gt[1], gt[3] + (row + 0.5) * gt[5]


def xy_to_pixel(gt, x, y):
    return (x - gt[0]) / gt[1] - 0.5, (y - gt[3]) / gt[5] - 0.5


def sample(array, gt, xs, ys):
    """Bilinear sample at map coordinates (cell centres are at +0.5)."""
    cols, rows = xy_to_pixel(gt, np.asarray(xs, float), np.asarray(ys, float))
    return map_coordinates(array.astype(float), [rows, cols], order=1, mode='nearest')


def line_points(line, step):
    """Stations every `step` metres: arrays x, y, tx, ty (unit tangent)."""
    length = line.length()
    stations = np.arange(0.0, length + 1e-9, step)
    xs, ys, tx, ty = [], [], [], []
    for s in stations:
        p = line.interpolate(float(s)).asPoint()
        a = line.interpolate(max(0.0, float(s) - 1.0)).asPoint()
        b = line.interpolate(min(length, float(s) + 1.0)).asPoint()
        dx, dy = b.x() - a.x(), b.y() - a.y()
        n = math.hypot(dx, dy)
        xs.append(p.x()); ys.append(p.y()); tx.append(dx / n); ty.append(dy / n)
    return stations, np.array(xs), np.array(ys), np.array(tx), np.array(ty)


def road_properties(out, gt, line, offsets, step, trim):
    """Measure a finished road on the output array.

    offsets: perpendicular offsets in metres (positive = left of travel).
    trim: metres skipped at both line ends (end effects are not judged).
    Returns dict with the cross-road range per station, and the grade (%)
    along the middle offset line between neighbouring stations.
    """
    stations, x, y, tx, ty = line_points(line, step)
    keep = (stations >= trim) & (stations <= line.length() - trim)
    nx, ny = -ty, tx
    z = np.array([sample(out, gt, x + nx * o, y + ny * o) for o in offsets])  # offsets x stations
    cross_range = (z.max(axis=0) - z.min(axis=0))[keep]
    middle = z[len(offsets) // 2][keep]
    kept_stations = stations[keep]
    grade = np.abs(np.diff(middle) / np.diff(kept_stations)) * 100.0
    return {'cross_range': cross_range, 'grade': grade, 'profile': middle, 'stations': kept_stations}


def cells_with_centres_inside(geometry, gt, shape, shrink=0.0):
    """Mask of cells whose centre is inside the geometry (optionally shrunk)."""
    g = geometry.buffer(-shrink, 8) if shrink else geometry
    box = g.boundingBox()
    c0, r1 = xy_to_pixel(gt, box.xMinimum(), box.yMinimum())
    c1, r0 = xy_to_pixel(gt, box.xMaximum(), box.yMaximum())
    c0 = max(0, int(math.floor(c0)) - 1); c1 = min(shape[1] - 1, int(math.ceil(c1)) + 1)
    r0 = max(0, int(math.floor(r0)) - 1); r1 = min(shape[0] - 1, int(math.ceil(r1)) + 1)
    mask = np.zeros(shape, bool)
    prepared = QgsGeometry(g)
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            x, y = pixel_to_xy(gt, c, r)
            if prepared.contains(QgsGeometry.fromPointXY(QgsPointXY(x, y))):
                mask[r, c] = True
    return mask


def max_distance_to(geometry, gt, cells):
    """Largest distance from the geometry to any listed cell centre."""
    rows, cols = np.nonzero(cells)
    worst = 0.0
    for r, c in zip(rows, cols):
        x, y = pixel_to_xy(gt, c, r)
        worst = max(worst, geometry.distance(QgsGeometry.fromPointXY(QgsPointXY(x, y))))
    return worst


# ----------------------------------------------------- finding real places

def _slope_pct(array, pixel):
    gy, gx = np.gradient(array.astype(float), pixel)
    return np.hypot(gx, gy) * 100.0


def find_hillside_line(array, gt, length, grade_lo, grade_hi, margin, want_cross=3.0):
    """Find a straight line on the real terrain whose along-line grade lies in
    [grade_lo, grade_hi] percent, with a consistent up or down direction and a
    sideways slope of at least `want_cross` percent. Search is deterministic.
    Returns a QgsGeometry line, or None.
    """
    pixel = abs(gt[1])
    rows, cols = array.shape
    best, best_score = None, None
    step = max(1, int(round(20.0 / pixel)))
    for r in range(int(margin / pixel), rows - int(margin / pixel), step):
        for c in range(int(margin / pixel), cols - int(margin / pixel), step):
            for angle in range(0, 360, 15):
                a = math.radians(angle)
                x0, y0 = pixel_to_xy(gt, c, r)
                x1, y1 = x0 + length * math.cos(a), y0 + length * math.sin(a)
                c1, r1 = xy_to_pixel(gt, x1, y1)
                if not (margin / pixel <= c1 < cols - margin / pixel and margin / pixel <= r1 < rows - margin / pixel):
                    continue
                n = int(length / pixel)
                xs = np.linspace(x0, x1, n + 1); ys = np.linspace(y0, y1, n + 1)
                z = sample(array, gt, xs, ys)
                g = np.diff(z) / (length / n) * 100.0
                if np.mean(np.sign(g) == np.sign(g.sum())) < 0.9:
                    continue
                mean_g = abs(g.mean())
                if not grade_lo <= mean_g <= grade_hi or np.abs(g).max() > grade_hi * 1.6:
                    continue
                nx, ny = -math.sin(a), math.cos(a)
                zl = sample(array, gt, xs + nx * 10, ys + ny * 10)
                zr = sample(array, gt, xs - nx * 10, ys - ny * 10)
                cross = np.abs(zl - zr).mean() / 20.0 * 100.0
                if cross < want_cross:
                    continue
                score = abs(mean_g - (grade_lo + grade_hi) / 2.0)
                if best_score is None or score < best_score:
                    best_score = score
                    best = QgsGeometry.fromPolylineXY([QgsPointXY(x0, y0), QgsPointXY(x1, y1)])
    return best


def find_open_ground(array, gt, size, margin, slope_lo, slope_hi):
    """Find the centre (x, y) of a size x size m area with an average slope
    between slope_lo and slope_hi percent, away from the DEM edge."""
    pixel = abs(gt[1])
    slope = _slope_pct(array, pixel)
    half = int(size / 2 / pixel)
    m = int(margin / pixel)
    rows, cols = array.shape
    best, best_score = None, None
    for r in range(m + half, rows - m - half, max(1, half // 2)):
        for c in range(m + half, cols - m - half, max(1, half // 2)):
            window = slope[r - half:r + half, c - half:c + half]
            mean = window.mean()
            if slope_lo <= mean <= slope_hi and window.max() < 60:
                score = abs(mean - (slope_lo + slope_hi) / 2)
                if best_score is None or score < best_score:
                    best_score, best = score, pixel_to_xy(gt, c, r)
    return best


def polyline(points):
    return QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in points])


def bend_line(origin, heading_deg, leg, turn_deg, radius=0.0, arc_step_deg=7.5):
    """Leg, turn (sharp corner or arc of `radius`), leg. Returns a QgsGeometry."""
    x, y = origin
    h = math.radians(heading_deg)
    pts = [(x, y)]
    x += leg * math.cos(h); y += leg * math.sin(h); pts.append((x, y))
    if radius > 0:
        sign = 1 if turn_deg > 0 else -1
        cx, cy = x - sign * radius * math.sin(h), y + sign * radius * math.cos(h)
        start = math.atan2(y - cy, x - cx)
        steps = int(abs(turn_deg) / arc_step_deg)
        for i in range(1, steps + 1):
            a = start + sign * math.radians(abs(turn_deg) * i / steps)
            pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
        h += math.radians(turn_deg)
        x, y = pts[-1]
    else:
        h += math.radians(turn_deg)
    x += leg * math.cos(h); y += leg * math.sin(h); pts.append((x, y))
    return polyline(pts)


# ------------------------------------------------- drive the plugin like QGIS

class Bar:
    def __init__(self): self.messages = []
    def pushInfo(self, title, text): self.messages.append(('info', text))
    def pushWarning(self, title, text): self.messages.append(('warning', text))
    def pushCritical(self, title, text): self.messages.append(('critical', text))


class FakeIface:
    def __init__(self):
        self.bar = Bar()
    def messageBar(self): return self.bar


class Outcome:
    """What the QGIS task manager would hand to the finish callback."""
    def __init__(self):
        self.ran = False
        self.ok = None
        self.exception = None
        self.result = None
        self.cancelled = False


class FakeTaskManager:
    """Runs the task at once. Can cancel by progress value (`cancel_at`), or
    after the edit reports `cancel_after_progress` and then answers "not
    cancelled" to `checks_before_cancel` questions (to hit the publish step)."""

    def __init__(self, editor, outcome, cancel_at, cancel_after_progress=None, checks_before_cancel=0):
        self.editor = editor
        self.outcome = outcome
        self.cancel_at = cancel_at
        self.cancel_after_progress = cancel_after_progress
        self.checks_before_cancel = checks_before_cancel

    def addTask(self, task):
        state = {'cancel': False, 'armed': False, 'checks': 0}
        original = task.setProgress

        def set_progress(value):
            original(value)
            if self.cancel_at is not None and value >= self.cancel_at:
                state['cancel'] = True
            if self.cancel_after_progress is not None and value >= self.cancel_after_progress:
                state['armed'] = True

        def is_cancelled():
            if state['armed']:
                state['checks'] += 1
                return state['checks'] > self.checks_before_cancel
            return state['cancel']
        task.setProgress = set_progress
        task.isCanceled = is_cancelled
        self.outcome.ran = True
        self.outcome.ok = task.run()
        self.outcome.exception = task.exception
        self.outcome.result = task.returned_values
        self.outcome.cancelled = state['cancel'] or state['checks'] > self.checks_before_cancel
        self.editor.task = None
        return 1


def base_settings(**over):
    """Settings with the defaults of the dialog (see buffer_dialog.py)."""
    s = {'source': 'digitize', 'polygon_layer_id': None, 'line_layer_id': None,
         'polygon_scope': 'selected', 'distance': 10.0, 'side': 'both', 'mode': 'road',
         'flat_source': 'value', 'flat_value': 0.0, 'statistic': 'median', 'smooth_m': 0.0,
         'profile_method': 'terrain', 'maximum_grade': 10.0, 'crossfall_tolerance': 0.10,
         'resample': False, 'target_resolution': 2.0, 'difference': True, 'diagnostics': False,
         'feather_m': 20.0, 'cut_slope': 1.0, 'fill_slope': 0.5, 'dem_layer_id': None,
         'output_dtype': 'float', 'raster_path': '', 'shapefile_path': ''}
    s.update(over)
    return s


class Session:
    """One QGIS-like session: a DEM layer in a project, the plugin object."""

    def __init__(self, plugin_module, dem_path, outdir, monkeypatch, project_crs_wkt=None):
        from demplugin import dem_road_buffer
        self.module = dem_road_buffer
        self.dem_path = dem_path
        self.outdir = str(outdir)
        self.inputdir = os.path.join(os.path.dirname(self.outdir), 'inputs')
        os.makedirs(self.inputdir, exist_ok=True)
        self.monkeypatch = monkeypatch
        QgsProject.instance().clear()
        ds = gdal.Open(dem_path)
        wkt = ds.GetProjection(); ds = None
        crs = QgsCoordinateReferenceSystem(); crs.createFromWkt(project_crs_wkt or wkt)
        QgsProject.instance().setCrs(crs)
        self.project_crs = crs
        self.dem_layer = QgsRasterLayer(dem_path, 'dem')
        QgsProject.instance().addMapLayer(self.dem_layer)
        self.iface = FakeIface()
        self.editor = dem_road_buffer.DemRoadBufferEditor(self.iface)
        self.editor.dem_path = dem_path
        self.editor.source_dem_path = dem_path
        self.reviewed = []
        monkeypatch.setattr(self.editor, '_review_geometry', self._review)

    def _review(self, corridor, line, settings):
        self.reviewed.append({'corridor': corridor, 'line': line, 'settings': settings})
        return True

    def settings(self, name='edit', **over):
        s = base_settings(dem_layer_id=self.dem_layer.id(),
                          raster_path=os.path.join(self.outdir, name + '.tif'),
                          shapefile_path=os.path.join(self.outdir, name + '_corridor.shp'))
        s.update(over)
        return s

    def _drive(self, starter, cancel_at=None, **cancel_options):
        outcome = Outcome()
        self.monkeypatch.setattr(QgsApplication, 'taskManager',
                                 staticmethod(lambda: FakeTaskManager(self.editor, outcome, cancel_at, **cancel_options)))
        self.monkeypatch.setattr(self.editor, '_task_finished', lambda e, r: None)
        starter()
        return outcome

    def run_digitized(self, line, settings, cancel_at=None, **cancel_options):
        """User clicked vertices, then Finish digitization."""
        self.editor.digitize_settings = settings
        return self._drive(lambda: self.editor._centerline_finished(line), cancel_at, **cancel_options)

    def run_selected_line(self, layer, feature_id, settings, cancel_at=None):
        layer.selectByIds([feature_id])
        settings['line_layer_id'] = layer.id()
        settings['source'] = 'line'
        return self._drive(lambda: self.editor._start_from_line(settings), cancel_at)

    def run_selected_polygon(self, layer, feature_ids, settings, cancel_at=None):
        layer.selectByIds(feature_ids)
        settings['polygon_layer_id'] = layer.id()
        settings['source'] = 'polygon'
        return self._drive(lambda: self.editor._start_from_polygon(settings), cancel_at)

    def run_flat_polygon(self, polygon, settings, cancel_at=None):
        """Platform from a drawn area. Uses the polygon route of the UI."""
        layer = make_layer('Polygon', self.project_crs, self.inputdir, 'platform_area', [polygon])
        return self.run_selected_polygon(layer, [1], settings, cancel_at)


def make_layer(kind, crs, outdir, name, geometries):
    """Write a GeoPackage layer and return it as a QGIS layer in the project."""
    from osgeo import ogr, osr
    path = os.path.join(str(outdir), name + '.gpkg')
    ds = ogr.GetDriverByName('GPKG').CreateDataSource(path)
    srs = osr.SpatialReference(); srs.ImportFromWkt(crs.toWkt())
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    geom_type = ogr.wkbLineString if kind == 'Line' else ogr.wkbPolygon
    layer = ds.CreateLayer(name, srs, geom_type)
    for g in geometries:
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(ogr.CreateGeometryFromWkt(g.asWkt()))
        layer.CreateFeature(feature)
    feature = None; layer = None; ds = None
    qgs = QgsVectorLayer('{}|layername={}'.format(path, name), name, 'ogr')
    assert qgs.isValid(), 'GeoPackage layer did not load'
    QgsProject.instance().addMapLayer(qgs)
    return qgs


# ----------------------------------------------------- reading the outputs

def parse_report(path):
    text = open(path, encoding='utf-8').read()
    return text


def recompute_cut_fill(source, output, valid):
    """Independent cut/fill numbers from the two arrays."""
    delta = output.astype(np.float64) - source.astype(np.float64)
    delta = delta[valid]
    cut, fill = delta < -1e-6, delta > 1e-6
    return {'cut_pixels': int(cut.sum()), 'fill_pixels': int(fill.sum()),
            'cut_volume_per_area': float(-delta[cut].sum()), 'fill_volume_per_area': float(delta[fill].sum()),
            'max_cut': float(max(0.0, -delta.min())), 'max_fill': float(max(0.0, delta.max()))}


def files_in(folder):
    found = []
    for base, dirs, names in os.walk(folder):
        for n in names:
            found.append(os.path.relpath(os.path.join(base, n), folder))
        for d in dirs:
            found.append(os.path.relpath(os.path.join(base, d), folder) + '/')
    return sorted(found)
