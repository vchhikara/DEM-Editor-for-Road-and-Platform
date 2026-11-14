import logging
import math
import os
import shutil
import tempfile

import numpy as np
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsGeometry,
    QgsPointXY,
)

LOG = logging.getLogger(__name__)


class DemEditError(Exception):
    pass


class EditCancelled(Exception):
    pass


def _check_vertical_unit(unit):
    """Reject recognized non-metre labels; missing/unknown labels need review."""
    label = ' '.join((unit or '').strip().lower().replace('_', ' ').replace('-', ' ').split())
    non_metre = {'ft', 'foot', 'feet', 'international foot', 'international feet',
                 'us survey foot', 'us survey feet', 'us foot', 'us feet', 'us ft', 'ftus',
                 'cm', 'centimeter', 'centimeters', 'centimetre', 'centimetres',
                 'mm', 'millimeter', 'millimeters', 'millimetre', 'millimetres',
                 'km', 'kilometer', 'kilometers', 'kilometre', 'kilometres'}
    if label in non_metre:
        raise DemEditError('The DEM declares vertical units "{}". Convert elevation values to metres before editing; changing the unit label alone is not enough.'.format(unit))


def _report(progress, percent):
    if progress is not None:
        if progress(float(percent)) is False:
            raise EditCancelled()
    return True


def _pixel_size(geotransform):
    x = math.hypot(geotransform[1], geotransform[4])
    y = math.hypot(geotransform[2], geotransform[5])
    return (x + y) / 2.0


def _inverse_geotransform(geotransform):
    from osgeo import gdal

    result = gdal.InvGeoTransform(geotransform)
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], int):
        if not result[0]:
            raise DemEditError('The DEM geotransform cannot be inverted.')
        return result[1]
    if result is None or len(result) != 6:
        raise DemEditError('The DEM geotransform cannot be inverted.')
    return result


def _valid_mask(array, nodata):
    if array.dtype.kind == 'f':
        valid = np.isfinite(array)
        if nodata is not None and not (isinstance(nodata, float) and math.isnan(nodata)):
            valid &= array != nodata
        return valid
    if nodata is not None:
        return array != nodata
    return np.ones(array.shape, dtype=bool)


def _ensure_valid(geometry):
    if geometry is None or geometry.isEmpty():
        raise DemEditError('The corridor geometry is empty.')
    if not geometry.isGeosValid():
        fixed = geometry.makeValid()
        if isinstance(fixed, tuple):
            fixed = fixed[0]
        if fixed is not None and not fixed.isEmpty():
            geometry = fixed
    return geometry


def _transform_to_dem(wkt, project_crs, dem_crs, transform_context=None):
    geometry = QgsGeometry.fromWkt(wkt)
    geometry = _ensure_valid(geometry)
    if project_crs.isValid() and project_crs != dem_crs:
        try:
            context = (
                transform_context
                if transform_context is not None
                else QgsCoordinateTransformContext()
            )
            transform = QgsCoordinateTransform(project_crs, dem_crs, context)
            geometry.transform(transform)
        except Exception as error:
            raise DemEditError('Could not transform geometry to the DEM CRS: {}'.format(error))
    return geometry


def _raster_mask(dem_dataset, corridor_geometry):
    from osgeo import gdal, ogr, osr
    import uuid

    width = dem_dataset.RasterXSize
    height = dem_dataset.RasterYSize
    geotransform = dem_dataset.GetGeoTransform()
    projection = dem_dataset.GetProjection()

    geometry = QgsGeometry(corridor_geometry)
    if not geometry.isMultipart():
        geometry.convertToMultiType()

    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromWkt(projection)

    vector_path = '/vsimem/dem_road_buffer_{}.geojson'.format(uuid.uuid4().hex)
    vector_dataset = None
    try:
        vector_driver = ogr.GetDriverByName('GeoJSON')
        if vector_driver is None:
            raise DemEditError('The GeoJSON driver is not available for corridor rasterization.')
        vector_dataset = vector_driver.CreateDataSource(vector_path)
        if vector_dataset is None:
            raise DemEditError('Could not create the temporary corridor dataset.')
        layer = vector_dataset.CreateLayer('corridor', spatial_ref, ogr.wkbMultiPolygon)
        ogr_geometry = ogr.CreateGeometryFromWkt(geometry.asWkt())
        if ogr_geometry is None:
            raise DemEditError('Could not build the corridor geometry.')
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(ogr_geometry)
        layer.CreateFeature(feature)
        vector_dataset = None

        vector_dataset = gdal.OpenEx(vector_path, gdal.OF_VECTOR)
        if vector_dataset is None:
            raise DemEditError('Could not reopen the temporary corridor dataset.')

        mask_dataset = gdal.GetDriverByName('MEM').Create('', width, height, 1, gdal.GDT_Byte)
        mask_dataset.SetGeoTransform(geotransform)
        mask_dataset.SetProjection(projection)
        gdal.Rasterize(mask_dataset, vector_dataset, burnValues=[1])
        mask = mask_dataset.GetRasterBand(1).ReadAsArray() > 0
        mask_dataset = None
    finally:
        vector_dataset = None
        try:
            gdal.Unlink(vector_path)
        except Exception:
            LOG.debug('Could not remove the temporary vector file.', exc_info=True)
    return mask


def _outside_raster_area(dataset, geometry, distance=0.0):
    """Area of a corridor or its transition envelope outside the DEM grid."""
    gt = dataset.GetGeoTransform()
    width, height = dataset.RasterXSize, dataset.RasterYSize
    def point(col, row):
        return QgsPointXY(gt[0] + col*gt[1] + row*gt[2],
                          gt[3] + col*gt[4] + row*gt[5])
    footprint = QgsGeometry.fromPolygonXY([[point(0, 0), point(width, 0),
                                             point(width, height), point(0, height), point(0, 0)]])
    envelope = geometry.buffer(distance, 8) if distance > 0 else geometry
    if envelope is None or envelope.isNull():
        raise DemEditError('Could not inspect corridor coverage against the DEM extent.')
    outside = envelope.difference(footprint)
    if outside is None or outside.isNull():
        raise DemEditError('Could not inspect corridor coverage against the DEM extent.')
    area = max(0.0, float(outside.area()))
    pixel_area = abs(gt[1]*gt[5] - gt[2]*gt[4])
    return area if area > max(pixel_area * 1e-8, 1e-8) else 0.0


def _sample_bilinear(array, valid, inverse_geotransform, x, y):
    column = (
        inverse_geotransform[0]
        + inverse_geotransform[1] * x
        + inverse_geotransform[2] * y
    ) - 0.5
    row = (
        inverse_geotransform[3]
        + inverse_geotransform[4] * x
        + inverse_geotransform[5] * y
    ) - 0.5

    height, width = array.shape
    if column < -1 or row < -1 or column > width or row > height:
        return float('nan')

    column_floor = int(math.floor(column))
    row_floor = int(math.floor(row))
    column_fraction = column - column_floor
    row_fraction = row - row_floor

    total = 0.0
    weight_sum = 0.0
    for row_offset, row_weight in ((0, 1.0 - row_fraction), (1, row_fraction)):
        sample_row = row_floor + row_offset
        if sample_row < 0 or sample_row >= height:
            continue
        for column_offset, column_weight in ((0, 1.0 - column_fraction), (1, column_fraction)):
            sample_column = column_floor + column_offset
            if sample_column < 0 or sample_column >= width:
                continue
            weight = row_weight * column_weight
            if weight <= 0.0:
                continue
            if not valid[sample_row, sample_column]:
                continue
            total += float(array[sample_row, sample_column]) * weight
            weight_sum += weight

    if weight_sum <= 0.0:
        return float('nan')
    return total / weight_sum


def _moving_average(values, window):
    if window <= 1 or values.size < 3:
        return values
    if window % 2 == 0:
        window += 1
    if window > values.size:
        window = values.size if values.size % 2 == 1 else values.size - 1
    if window <= 1:
        return values
    kernel = np.ones(window, dtype=float)
    padded = np.pad(values, window // 2, mode='reflect')
    smoothed = np.convolve(padded, kernel, mode='valid') / float(window)
    return smoothed[: values.size]


def _distance_moving_average(distances, values, width):
    """Average the piecewise-linear profile over physical distance, not index."""
    distances = np.asarray(distances, dtype=float)
    values = np.asarray(values, dtype=float)
    if values.size < 2 or width <= 0:
        return values.copy()
    lengths = np.diff(distances)
    if np.any(lengths <= 0):
        raise DemEditError('Road profile stations are not strictly increasing.')
    slopes = np.diff(values) / lengths
    cumulative = np.r_[0.0, np.cumsum((values[:-1] + values[1:]) * lengths / 2.0)]

    def integral(query):
        indices = np.clip(np.searchsorted(distances, query, side='right') - 1, 0, len(slopes) - 1)
        offset = query - distances[indices]
        return cumulative[indices] + values[indices]*offset + 0.5*slopes[indices]*offset**2

    left = np.maximum(distances[0], distances - width / 2.0)
    right = np.minimum(distances[-1], distances + width / 2.0)
    return (integral(right) - integral(left)) / (right - left)


def _road_profile(
    array,
    valid,
    geotransform,
    inverse_geotransform,
    line_geometry,
    smooth_m,
    progress,
):
    length = line_geometry.length()
    if length <= 0.0:
        raise DemEditError('The digitized centerline has zero length.')

    pixel = _pixel_size(geotransform)
    spacing = max(pixel / 2.0, length / 5000.0)
    count = max(2, int(math.ceil(length / spacing)) + 1)
    # Preserve the real digitized corners: a uniformly sampled line alone
    # replaces bends with straight chords and can miss the inside road edge.
    vertices = list(line_geometry.vertices())
    corner_distances = []
    cumulative = 0.0
    for first, second in zip(vertices, vertices[1:]):
        cumulative += math.hypot(second.x() - first.x(), second.y() - first.y())
        corner_distances.append(min(length, cumulative))
    stations = list(np.linspace(0.0, length, count))
    for along in corner_distances:
        stations.extend((max(0.0, along-spacing/2.0), along, min(length, along+spacing/2.0)))
    mandatory = sorted(set([0.0, length] + [float(s) for s in corner_distances if 0 < s < length]))
    minimum_spacing = min(pixel * 0.1, spacing * 0.2)
    # Keep actual line corners; discard only extra sampling stations that are
    # too close to a corner, endpoint, or another supplementary station.
    supplementary = []
    for s in sorted(set(float(s) for s in stations if 0.0 < s < length)):
        j = np.searchsorted(mandatory, s)
        if ((j and s - mandatory[j-1] < minimum_spacing) or
                (j < len(mandatory) and mandatory[j] - s < minimum_spacing) or
                (supplementary and s - supplementary[-1] < minimum_spacing)):
            continue
        supplementary.append(s)
    stations = sorted(set(mandatory + supplementary))

    points = []
    distances = []
    for along in stations:
        point_geometry = line_geometry.interpolate(along)
        point = point_geometry.asPoint() if hasattr(point_geometry, 'asPoint') else point_geometry
        points.append((point.x(), point.y()))
        distances.append(along)
    _report(progress, 28)

    # Elevations are derived from the full corridor after pixel projection.
    return points, np.array(distances, dtype=float), pixel


def _corridor_profile(distances, along, terrain, pixel, smooth_m, method='terrain', maximum_grade=10.0, plateau_ranges=(), grade_intervals=None):
    """Median terrain per station balances cut/fill across the sampled corridor."""
    stations = np.searchsorted((distances[:-1] + distances[1:]) / 2.0, along)
    order = np.argsort(stations, kind='stable')
    ordered_stations = stations[order]
    starts = np.r_[0, np.flatnonzero(np.diff(ordered_stations)) + 1]
    ends = np.r_[starts[1:], order.size]
    profile = np.full(distances.size, np.nan, dtype=float)
    for start, end in zip(starts, ends):
        samples = terrain[order[start:end]]
        profile[ordered_stations[start]] = np.median(samples) if method == 'balanced' else np.mean(samples)
    good = np.isfinite(profile)
    if not good.any():
        raise DemEditError('No valid terrain samples were found inside the road corridor.')
    profile = np.interp(distances, distances[good], profile[good])
    width = smooth_m if smooth_m > 0.0 else 5.0 * pixel
    profile = _distance_moving_average(distances, profile, width)
    if method in ('grade', 'balanced'):
        profile = _fit_grade_limited_profile(distances, profile, maximum_grade, plateau_ranges, grade_intervals)
    elif plateau_ranges:
        # A tiny station interval at a bend can make a one-cell change look
        # like a very steep approach. Use the terrain profile measured over a
        # five-pixel interval as a smoothing bound for local levelling.
        # This is not the user's grade-controlled mode: the bound comes from
        # the terrain and can still be steep; it may change nearby earthwork.
        terrain_max_grade = _sampled_grade(distances, profile, 5.0 * pixel)
        if terrain_max_grade is None:
            raise DemEditError('Could not assess the terrain profile near the bend.')
        profile = _fit_grade_limited_profile(distances, profile, terrain_max_grade, plateau_ranges, grade_intervals)
    return profile


def _fit_grade_limited_profile(distances, terrain_profile, maximum_grade, plateau_ranges=(), grade_intervals=None):
    """Fit the profile to ground while enforcing grade; not a full earthwork model."""
    from scipy.optimize import linprog
    from scipy.sparse import coo_matrix
    n = len(distances)
    if n < 2:
        return terrain_profile.copy()
    # z_i is free; e_i >= abs(z_i - target_i). A small grade-change
    # penalty limits abrupt kinks while enforcing adjacent grade bounds.
    turns = max(0, n-2)
    rows, cols, data, rhs = [], [], [], []
    row = 0
    for i in range(n):
        rows.extend((row, row)); cols.extend((i, n+i)); data.extend((1., -1.)); rhs.append(float(terrain_profile[i])); row += 1
        rows.extend((row, row)); cols.extend((i, n+i)); data.extend((-1., -1.)); rhs.append(float(-terrain_profile[i])); row += 1
    if grade_intervals is None:
        grade_intervals = np.diff(distances)
    if (len(grade_intervals) != n-1 or not np.all(np.isfinite(grade_intervals))
            or np.any(np.asarray(grade_intervals) < 0)):
        raise DemEditError('Invalid road grade intervals.')
    for i in range(n-1):
        limit = max(0., float(maximum_grade)) / 100.0 * float(grade_intervals[i])
        rows.extend((row, row)); cols.extend((i+1, i)); data.extend((1., -1.)); rhs.append(limit); row += 1
        rows.extend((row, row)); cols.extend((i+1, i)); data.extend((-1., 1.)); rhs.append(limit); row += 1
    for i in range(1,n-1):
        left = float(distances[i]-distances[i-1]); right = float(distances[i+1]-distances[i])
        for sign in (1., -1.):
            rows.extend((row,row,row,row)); cols.extend((i-1,i,i+1,2*n+i-1))
            data.extend((sign/left,-sign*(1./left+1./right),sign/right,-1.))
            rhs.append(0.); row += 1
    matrix = coo_matrix((data, (rows, cols)), shape=(row, 2*n+turns)).tocsr()
    weight = np.empty(n); weight[0] = (distances[1]-distances[0])/2; weight[-1] = (distances[-1]-distances[-2])/2
    if n > 2: weight[1:-1] = (distances[2:] - distances[:-2]) / 2
    bend_weight = 0.1 * float(np.median(np.diff(distances)))**2
    erows, ecols, edata = [], [], []
    for i in range(n-1):
        if any(distances[i] >= start and distances[i+1] <= end for start, end in plateau_ranges):
            row = len(erows) // 2
            erows.extend((row,row)); ecols.extend((i,i+1)); edata.extend((1.,-1.))
    equalities = (coo_matrix((edata,(erows,ecols)),shape=(len(erows)//2,2*n+turns)).tocsr()
                  if erows else None)
    outcome = linprog(np.r_[np.zeros(n), weight, np.full(turns,bend_weight)], A_ub=matrix, b_ub=np.asarray(rhs),
                      A_eq=equalities, b_eq=np.zeros(len(erows)//2) if erows else None,
                      bounds=[(None, None)]*n + [(0, None)]*(n+turns), method='highs')
    if not outcome.success:
        raise DemEditError('Could not calculate a grade-limited road profile: {}'.format(outcome.message))
    return outcome.x[:n]


def _sharp_turn_plateaus(line_geometry, width):
    """Return short chainage intervals for real digitized turns over 45°."""
    vertices = list(line_geometry.vertices())
    cumulative = 0.0
    ranges = []
    for a, b, c in zip(vertices, vertices[1:], vertices[2:]):
        ax, ay = b.x()-a.x(), b.y()-a.y()
        bx, by = c.x()-b.x(), c.y()-b.y()
        first_len, second_len = math.hypot(ax, ay), math.hypot(bx, by)
        cumulative += first_len
        if not first_len or not second_len: continue
        cosine = max(-1.0, min(1.0, (ax*bx+ay*by)/(first_len*second_len)))
        if math.degrees(math.acos(cosine)) >= 45.0:
            radius = max(float(width)*1.5, 0.01)
            ranges.append((max(0.0, cumulative-radius), min(line_geometry.length(), cumulative+radius)))
    return ranges


def _merge_overlapping_ranges(ranges):
    """Merge adjacent level sections so their elevations cannot compete."""
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _sampled_grade(distances, elevations, spacing):
    """Grade at a stated practical interval, rather than tiny adjacent gaps."""
    length = float(distances[-1] - distances[0])
    if length <= 0:
        return None
    intervals = max(1, min(4000, int(math.ceil(length / spacing))))
    chainage = np.linspace(float(distances[0]), float(distances[-1]), intervals + 1)
    samples = np.interp(chainage, distances, elevations)
    return float(np.max(np.abs(np.diff(samples) / np.diff(chainage))) * 100.0)


def _midpoint_grade_intervals(distances, edges):
    """Bound grades along both the alignment and section-midpoint path.

    On the inside of a bend the midpoint path can be shorter than the input
    alignment. Missing sections retain the alignment distance and are flagged
    separately by the output sampling checks.
    """
    intervals = np.diff(distances).astype(float)
    for i, (first, second) in enumerate(zip(edges, edges[1:])):
        if first is None or second is None:
            continue
        x1 = (first[0][0] + first[1][0]) / 2.0
        y1 = (first[0][1] + first[1][1]) / 2.0
        x2 = (second[0][0] + second[1][0]) / 2.0
        y2 = (second[0][1] + second[1][1]) / 2.0
        intervals[i] = min(intervals[i], math.hypot(x2-x1, y2-y1))
    return intervals


def _sample_raster_grade(array, valid, road_mask, inverse, distances, edges, pixel, grade_limit=None):
    """Sample road-section midpoints using only raster cells in the road.

    The input alignment is the *edge* of a one-sided buffer. Sampling there
    mixes road and hillside. Section midpoints represent the edited surface;
    distances between them, rather than alignment chainage, measure its grade.
    Missing sections break the sequence instead of being silently bridged.
    """
    if len(distances) < 2 or len(distances) != len(edges) or pixel <= 0:
        return None, None, 0, 0, None
    road_valid = valid & road_mask
    length = float(distances[-1] - distances[0])
    if length <= 0:
        return None, None, 0, 0, None
    intervals = max(1, min(4000, int(math.ceil(length / pixel))))
    indices = np.unique(np.searchsorted(distances, np.linspace(distances[0], distances[-1], intervals + 1)).clip(0, len(distances)-1))
    values, positions = [], []
    for index in indices:
        edge = edges[index]
        if edge is None:
            positions.append(None)
            values.append(float('nan'))
            continue
        left, right = edge
        point = ((left[0]+right[0])/2.0, (left[1]+right[1])/2.0)
        positions.append(point)
        values.append(_sample_bilinear(array, road_valid, inverse, *point))
    grades = []
    spacings = []
    attempts = max(0, len(indices)-1)
    for i in range(attempts):
        if not (math.isfinite(values[i]) and math.isfinite(values[i+1])):
            continue
        spacing = math.hypot(positions[i+1][0]-positions[i][0], positions[i+1][1]-positions[i][1])
        if spacing <= pixel * 0.1:
            continue
        spacings.append(spacing)
        grades.append(abs(values[i+1]-values[i]) / spacing * 100.0)
    if not grades:
        return None, None, 0, attempts, None
    exceedances = (int(np.count_nonzero(np.asarray(grades) > grade_limit + 0.1))
                   if grade_limit is not None else None)
    return float(max(grades)), float(np.median(spacings)), len(grades), attempts, exceedances


def _sample_raster_crossfall(array, valid, road_mask, inverse, edges, tolerance, pixel, distances=None, flagged=None):
    """Approximate road-only raster crossfall; unavailable samples are skipped."""
    road_valid = valid & road_mask
    ranges = []
    # This is a diagnostic sample rather than a proof over every raster point.
    stride = max(1, int(math.ceil(len(edges) / 1000.0)))
    total = 0
    for index in range(0, len(edges), stride):
        edge = edges[index]
        total += 1
        if edge is None:
            continue
        left, right = edge
        samples = []
        # Keep the original 20–80% interval and midpoint, but sample at
        # approximately half-pixel spacing where practical. Cap work for wide
        # corridors; this remains a diagnostic, not exhaustive validation.
        span = math.hypot(right[0] - left[0], right[1] - left[1])
        intervals = min(64, max(2, int(math.ceil(0.6 * span / max(pixel * 0.5, 1e-12)))))
        intervals += intervals % 2
        for t in np.linspace(0.2, 0.8, intervals + 1):
            x = left[0] + t*(right[0] - left[0])
            y = left[1] + t*(right[1] - left[1])
            samples.append(_sample_bilinear(array, road_valid, inverse, x, y))
        if all(math.isfinite(value) for value in samples):
            difference = max(samples) - min(samples)
            ranges.append(difference)
            if difference > tolerance and flagged is not None and distances is not None:
                flagged.append((float(distances[index]), float(difference)))
    if not ranges:
        return None, 0, total, None
    return float(max(ranges)), len(ranges), total, int(np.count_nonzero(np.asarray(ranges) > tolerance))


def _cross_section_samples(points, distances, corridor, array, valid, inverse, pixel, progress, diagnostics=False, max_cross_offset=None):
    """Sample DEM across the corridor, normal to each centerline station."""
    from qgis.core import QgsPointXY
    coords = np.asarray(points, dtype=float)
    bounds = corridor.boundingBox()
    span = math.hypot(bounds.width(), bounds.height()) + 2.0 * pixel
    station_values, terrain_values, sample_points = [], [], []
    section_edges = [None] * len(coords)
    for i, (x, y) in enumerate(coords):
        tangent = coords[min(i+1, len(coords)-1)] - coords[max(i-1, 0)]
        norm = np.hypot(*tangent)
        if norm <= 0: continue
        nx, ny = -tangent[1]/norm, tangent[0]/norm
        cutline = QgsGeometry.fromPolylineXY([QgsPointXY(x-nx*span,y-ny*span), QgsPointXY(x+nx*span,y+ny*span)])
        intersection = corridor.intersection(cutline)
        if intersection is None or intersection.isEmpty(): continue
        try:
            parts = intersection.asMultiPolyline() if intersection.isMultipart() else [intersection.asPolyline()]
        except Exception:
            continue
        parts = [part for part in parts if len(part) >= 2]
        if not parts: continue
        # For winding/concave shapes, select the interval nearest this station.
        center_point = QgsGeometry.fromPointXY(QgsPointXY(x, y))
        parts.sort(key=lambda part: QgsGeometry.fromPolylineXY(part).distance(center_point))
        part = parts[0]
        xs = [p.x() for p in part]; ys = [p.y() for p in part]
        offsets = [(px-x)*nx + (py-y)*ny for px,py in zip(xs,ys)]
        start, end = min(offsets), max(offsets)
        # An infinite normal can run along another arm of an L/hairpin and
        # contaminate a local road cross-section with distant ground.
        # The line-buffer width is known, so cap the section to its local
        # miter allowance. Polygon inputs have no reliable fixed width.
        if max_cross_offset is not None:
            start, end = max(start, -max_cross_offset), min(end, max_cross_offset)
            if end <= start:
                continue
        section_edges[i] = ((float(x+nx*start),float(y+ny*start)),
                            (float(x+nx*end),float(y+ny*end)))
        count = max(2, min(5000, int(math.ceil((end-start)/pixel))+1))
        offsets = np.linspace(start, end, count)
        selected = {0, count-1, int(np.argmin(np.abs(offsets)))}
        for sample_index, off in enumerate(offsets):
            value = _sample_bilinear(array, valid, inverse, x+nx*off, y+ny*off)
            if math.isfinite(value):
                station_values.append(distances[i]); terrain_values.append(value)
                if diagnostics and sample_index in selected:
                    sample_points.append((float(x+nx*off),float(y+ny*off),float(distances[i]),float(value)))
        if i % max(1, len(coords)//10) == 0:
            _report(progress, 28 + 5.0*i/len(coords))
    if not terrain_values:
        raise DemEditError('No valid terrain samples were found in road cross-sections.')
    return np.asarray(station_values), np.asarray(terrain_values), sample_points, section_edges


def _road_ribbon_values(edges, distances, elevations, x, y, along, fallback, progress, crossfall_tolerance=0.10, use_spatial_index=True):
    """Interpolate adjacent cross-sections as a connected triangle ribbon.

    Overlaps choose the triangle whose chainage is closest to the road-line
    projection. Cells in untriangulable gaps retain the safe line fallback.
    """
    values = fallback.copy()
    best = np.full(x.size, np.inf)
    best_segment = np.full(x.size, -1, dtype=np.int32)
    covered = np.zeros(x.size, dtype=bool)
    conflicts = np.zeros(x.size, dtype=bool)
    max_conflict = 0.0
    conflict_ranges = []
    tree = None
    if use_spatial_index and x.size >= 75000:
        from scipy.spatial import cKDTree
        tree = cKDTree(np.column_stack((x, y)))
    for i in range(len(edges)-1):
        if edges[i] is None or edges[i+1] is None: continue
        a,b = edges[i]; c,d = edges[i+1]
        for p,q,r,za,zb,zc,sa,sb,sc in (
            (a,b,c,elevations[i],elevations[i],elevations[i+1],distances[i],distances[i],distances[i+1]),
            (b,d,c,elevations[i],elevations[i+1],elevations[i+1],distances[i],distances[i+1],distances[i+1]),
        ):
            denom=(q[1]-r[1])*(p[0]-r[0])+(r[0]-q[0])*(p[1]-r[1])
            if abs(denom) < 1e-12: continue
            xmin,xmax=min(p[0],q[0],r[0]),max(p[0],q[0],r[0])
            ymin,ymax=min(p[1],q[1],r[1]),max(p[1],q[1],r[1])
            if tree is None:
                candidates=np.flatnonzero((x>=xmin) & (x<=xmax) & (y>=ymin) & (y<=ymax))
            else:
                # The circle encloses the triangle's bounding box. Exact box
                # filtering below retains the same cells as the full scan.
                cx,cy=(xmin+xmax)/2.,(ymin+ymax)/2.
                radius=math.hypot(xmax-xmin,ymax-ymin)/2.
                margin=max(1e-8,radius*1e-12,4.0*np.spacing(max(abs(cx),abs(cy))))
                nearby=np.asarray(tree.query_ball_point((cx,cy),radius + margin),dtype=np.intp)
                candidates=nearby[(x[nearby]>=xmin)&(x[nearby]<=xmax)&(y[nearby]>=ymin)&(y[nearby]<=ymax)]
            if not candidates.size: continue
            xx=x[candidates]; yy=y[candidates]
            wa=((q[1]-r[1])*(xx-r[0])+(r[0]-q[0])*(yy-r[1]))/denom
            wb=((r[1]-p[1])*(xx-r[0])+(p[0]-r[0])*(yy-r[1]))/denom
            wc=1.-wa-wb
            inside=(wa>=-1e-9)&(wb>=-1e-9)&(wc>=-1e-9)
            if not inside.any(): continue
            idx=candidates[inside]; aa=wa[inside]; bb=wb[inside]; cc=wc[inside]
            proposed = aa*za+bb*zb+cc*zc
            overlap = covered[idx]
            if overlap.any():
                disagreements = np.abs(proposed[overlap] - values[idx[overlap]])
                max_conflict = max(max_conflict, float(disagreements.max()))
                bad = idx[overlap][disagreements > crossfall_tolerance]
                conflicts[bad] = True
                if bad.size:
                    for previous in np.unique(best_segment[bad]):
                        start = min(i, int(previous))
                        end = max(i, int(previous)) + 1
                        conflict_ranges.append((float(distances[start]), float(distances[end])))
            distance=np.abs(aa*sa+bb*sb+cc*sc-along[idx])
            use=distance < best[idx]
            idx=idx[use]
            values[idx]=proposed[use]
            best[idx]=distance[use]; best_segment[idx]=i; covered[idx]=True
        if i % max(1,(len(edges)-1)//10)==0:
            _report(progress, 65.0 + 1.0*i/max(1,len(edges)-1))
    gaps = ~covered
    # Endpoint projection is descriptive only; it does not certify that an
    # uncovered cell is geometrically safe or flat across the entire cap.
    endpoint_gaps = gaps & ((np.abs(along-distances[0]) <= 1e-8) |
                            (np.abs(along-distances[-1]) <= 1e-8))
    return (values, int(gaps.sum()), int(conflicts.sum()), max_conflict,
            _merge_overlapping_ranges(conflict_ranges), int(endpoint_gaps.sum()))


def _nearest_profile(
    points, distances, elevations, x_values, y_values, progress, max_distance=None
):
    count = x_values.size
    if count == 0:
        return np.empty(0, dtype=float)

    best_distance = np.full(count, np.inf, dtype=float)
    best_along = np.zeros(count, dtype=float)

    point_array = np.asarray(points, dtype=float)
    segment_count = len(point_array) - 1
    if segment_count < 1:
        return np.full(count, elevations[0], dtype=float)

    chunk_size = 1500000
    for start in range(0, count, chunk_size):
        stop = min(start + chunk_size, count)
        x = x_values[start:stop]
        y = y_values[start:stop]
        chunk_best = best_distance[start:stop]
        chunk_along = best_along[start:stop]
        for segment in range(segment_count):
            x0, y0 = point_array[segment]
            x1, y1 = point_array[segment + 1]
            dx = x1 - x0
            dy = y1 - y0
            length_squared = dx * dx + dy * dy
            if length_squared == 0.0:
                continue
            index = None
            if max_distance is not None and math.isfinite(max_distance):
                near = (
                    (x >= min(x0, x1) - max_distance)
                    & (x <= max(x0, x1) + max_distance)
                    & (y >= min(y0, y1) - max_distance)
                    & (y <= max(y0, y1) + max_distance)
                )
                index = np.flatnonzero(near)
                if index.size == 0:
                    continue
                xs = x[index]
                ys = y[index]
            else:
                xs = x
                ys = y
            projection = ((xs - x0) * dx + (ys - y0) * dy) / length_squared
            projection = np.clip(projection, 0.0, 1.0)
            nearest_x = x0 + projection * dx
            nearest_y = y0 + projection * dy
            distance_squared = (xs - nearest_x) ** 2 + (ys - nearest_y) ** 2
            current = chunk_best if index is None else chunk_best[index]
            closer = distance_squared < current
            if closer.any():
                target = np.flatnonzero(closer) if index is None else index[closer]
                chunk_best[target] = distance_squared[closer]
                along = distances[segment] + projection[closer] * (
                    distances[segment + 1] - distances[segment]
                )
                chunk_along[target] = along
            if progress is not None and segment % max(1, segment_count // 10) == 0:
                fraction = (start + (stop - start) * segment / segment_count) / count
                _report(progress, 35.0 + 30.0 * fraction)

    if not np.isfinite(best_distance).all():
        raise DemEditError('Some corridor pixels could not be matched to the road line. Check the buffer and coordinate systems.')
    return np.interp(best_along, distances, elevations)


def _edge_statistic(array, valid, mask, statistic):
    from scipy.ndimage import binary_erosion

    eroded = binary_erosion(mask, structure=np.ones((3, 3), dtype=bool), iterations=1)
    ring = mask & ~eroded
    if not ring.any():
        ring = mask
    samples = array[ring & valid]
    if samples.size == 0:
        raise DemEditError('No valid DEM values were found on the corridor edge.')
    if statistic == 'mean':
        return float(np.mean(samples)), int(samples.size)
    return float(np.median(samples)), int(samples.size)


def _feather_alpha(mask, geotransform, projection, feather_m, progress):
    from osgeo import gdal

    inverse_mask = (~mask).astype(np.uint8)
    source_dataset = gdal.GetDriverByName('MEM').Create(
        '', mask.shape[1], mask.shape[0], 1, gdal.GDT_Byte
    )
    source_dataset.SetGeoTransform(geotransform)
    source_dataset.SetProjection(projection)
    source_band = source_dataset.GetRasterBand(1)
    source_band.WriteArray(inverse_mask)
    source_band.FlushCache()

    destination_dataset = gdal.GetDriverByName('MEM').Create(
        '', mask.shape[1], mask.shape[0], 1, gdal.GDT_Float64
    )
    destination_dataset.SetGeoTransform(geotransform)
    destination_dataset.SetProjection(projection)
    destination_band = destination_dataset.GetRasterBand(1)

    gdal.ComputeProximity(
        source_band, destination_band, options=['VALUES=1', 'DISTUNITS=PIXEL']
    )
    distance = destination_band.ReadAsArray().astype(float)
    source_dataset = None
    destination_dataset = None
    _report(progress, 70)

    pixel = _pixel_size(geotransform)
    if feather_m <= 0.0 or pixel <= 0.0:
        return np.ones(mask.shape, dtype=float)
    alpha = distance * pixel / feather_m
    return np.clip(alpha, 0.0, 1.0)


def _write_output(dem_path, output_path, array, dtype, nodata, progress):
    from osgeo import gdal

    directory = os.path.dirname(os.path.abspath(output_path))
    if not os.path.isdir(directory):
        raise DemEditError('The output folder does not exist: {}'.format(directory))

    source = gdal.Open(dem_path, gdal.GA_ReadOnly)
    if source is None:
        raise DemEditError('Could not reopen the source DEM for writing.')
    # Use a private temporary directory instead of deleting a predictable
    # <output>.part.tif that might belong to the user or another task.
    with tempfile.TemporaryDirectory(prefix='.dem_editor_', dir=directory) as temp_dir:
        temporary = os.path.join(temp_dir, 'output.tif')
        previous_mask_config = gdal.GetThreadLocalConfigOption('GDAL_TIFF_INTERNAL_MASK')
        gdal.SetThreadLocalConfigOption('GDAL_TIFF_INTERNAL_MASK', 'YES')
        try:
            source_type = source.GetRasterBand(1).DataType
            from osgeo import gdal_array
            requested_type = gdal_array.NumericTypeCodeToGDALTypeCode(np.dtype(dtype).type)
            if requested_type == source_type:
                destination = gdal.GetDriverByName('GTiff').CreateCopy(
                    temporary, source, 0, options=['TILED=YES', 'COMPRESS=LZW'])
            else:
                destination = gdal.Translate(
                    temporary, source, format='GTiff', outputType=requested_type,
                    creationOptions=['TILED=YES', 'COMPRESS=LZW'])
            if destination is None:
                raise DemEditError('Could not create the output GeoTIFF.')
            try:
                out_band = destination.GetRasterBand(1)
                out_band.WriteArray(array.astype(dtype, copy=False))
                unit = source.GetRasterBand(1).GetUnitType()
                if unit:
                    out_band.SetUnitType(unit)
                if nodata is not None:
                    out_band.SetNoDataValue(float(nodata))
                # Source statistics no longer describe the edited pixels.
                for key in tuple((out_band.GetMetadata() or {}).keys()):
                    if key.upper().startswith('STATISTICS_'):
                        out_band.SetMetadataItem(key, None)
                out_band.FlushCache()
                out_band = None
                destination.FlushCache()
            finally:
                destination = None
        finally:
            source = None
            gdal.SetThreadLocalConfigOption('GDAL_TIFF_INTERNAL_MASK', previous_mask_config)

        # Reserve the output atomically. A regular copy works on filesystems
        # and Windows sandboxes that do not allow hard links.
        created = False
        try:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0)
            fd = os.open(output_path, flags, 0o666)
            created = True
            with os.fdopen(fd, 'wb') as target, open(temporary, 'rb') as staged:
                shutil.copyfileobj(staged, target, length=1024 * 1024)
        except FileExistsError:
            raise DemEditError('The output file already exists. Choose a new output path.')
        except OSError as error:
            if created:
                try:
                    os.remove(output_path)
                except OSError:
                    LOG.warning('Could not remove incomplete output %s', output_path, exc_info=True)
            raise DemEditError('Could not safely create the output GeoTIFF: {}'.format(error))
    _report(progress, 95)


def _cross_distance_from_line(
    points, distances, x_values, y_values, side, buffer_m
):
    """Compute perpendicular distance from the centerline for each sample point.
    Returns an array of distances with sign indicating side of the line:
    positive = left side, negative = right side (relative to line direction).
    """
    import numpy as np
    points = np.asarray(points, dtype=float)
    count = x_values.size
    if count == 0:
        return np.zeros(count, dtype=float)

    # Build line segments from interpolated points
    segment_count = len(points) - 1
    if segment_count < 1:
        # Single point: all cross-distance is 0
        return np.zeros(count, dtype=float)

    # For each sample point, find perpendicular distance to nearest segment
    # and determine which side of the line it's on
    cross = np.full(count, np.nan, dtype=float)
    side_sign = np.full(count, 0, dtype=int)

    # Build segment arrays
    seg_start = points[:segment_count]
    seg_end = points[1:segment_count + 1]
    seg_dx = seg_end[:, 0] - seg_start[:, 0]
    seg_dy = seg_end[:, 1] - seg_start[:, 1]
    seg_len_sq = seg_dx ** 2 + seg_dy ** 2

    # Reference direction for 'both' side: overall line direction (first to last point)
    line_dx = points[-1, 0] - points[0, 0]
    line_dy = points[-1, 1] - points[0, 1]

    for i in range(count):
        xi, yi = float(x_values[i]), float(y_values[i])
        best_dist = np.inf
        best_side = 0
        for s in range(segment_count):
            sx, sy = seg_start[s]
            ex, ey = seg_end[s]
            dx, dy = ex - sx, ey - sy
            if seg_len_sq[s] == 0:
                continue
            # Project point onto infinite line
            t = ((xi - sx) * dx + (yi - sy) * dy) / seg_len_sq[s]
            t = max(0.0, min(1.0, t))  # clamp to segment
            # Closest point on segment
            cx = sx + t * dx
            cy = sy + t * dy
            # Perpendicular distance
            d = np.hypot(xi - cx, yi - cy)
            if d < best_dist:
                best_dist = d
                # Determine side: cross product of segment vector and point vector
                # (dx, dy) x (xi - sx, yi - sy) = dx*(yi-sy) - dy*(xi-sx)
                cross_prod = dx * (yi - sy) - dy * (xi - sx)
                best_side = 1 if cross_prod >= 0 else -1
        cross[i] = best_dist
        side_sign[i] = best_side

    # Apply side preference
    if side == 'right':
        # Right side only: keep negative distances (right of direction)
        cross = -np.abs(cross)
    elif side == 'left':
        # Left side only: keep positive distances (left of direction)
        cross = np.abs(cross)
    else:  # 'both' - keep signed distances (left=positive, right=negative)
        cross = cross * side_sign

    return cross


def _write_sample_points(path, projection, points):
    from osgeo import ogr, osr
    if os.path.exists(path):
        raise DemEditError('The sample-point layer already exists.')
    srs = osr.SpatialReference(); srs.ImportFromWkt(projection)
    ds = ogr.GetDriverByName('ESRI Shapefile').CreateDataSource(path)
    if ds is None:
        raise DemEditError('Could not create the sample-point layer.')
    try:
        layer = ds.CreateLayer(os.path.splitext(os.path.basename(path))[0], srs, ogr.wkbPoint)
        if layer is None: raise DemEditError('Could not create the sample-point layer.')
        layer.CreateField(ogr.FieldDefn('station_m', ogr.OFTReal))
        layer.CreateField(ogr.FieldDefn('ground_z', ogr.OFTReal))
        for x, y, station, elevation in points:
            feature = ogr.Feature(layer.GetLayerDefn())
            geom = ogr.Geometry(ogr.wkbPoint); geom.AddPoint_2D(x, y)
            feature.SetGeometry(geom); feature.SetField('station_m',station); feature.SetField('ground_z',elevation)
            if layer.CreateFeature(feature) != 0: raise DemEditError('Could not write a sample point.')
            feature = None
        layer = None
    finally:
        ds = None


def edit_dem(
    dem_path,
    project_crs_wkt,
    corridor_wkt,
    line_wkt,
    settings,
    output_path,
    progress=None,
    transform_context=None,
):
    from osgeo import gdal

    if not os.path.isfile(dem_path):
        raise DemEditError('The DEM source is not a readable file.')

    _report(progress, 1)
    dataset = gdal.Open(dem_path, gdal.GA_ReadOnly)
    if dataset is None:
        raise DemEditError('Could not open the DEM with GDAL.')
    try:
        if dataset.RasterCount != 1:
            raise DemEditError('The DEM must have exactly one band.')
        band = dataset.GetRasterBand(1)
        _check_vertical_unit(band.GetUnitType())
        geotransform = dataset.GetGeoTransform()
        from .resource_check import grid_geometry
        try:
            _, _, orthogonal = grid_geometry(geotransform)
        except ValueError as error:
            raise DemEditError(str(error))
        if not orthogonal and float(settings.get('feather_m', 0.0)) > 0:
            raise DemEditError('Cut/fill transitions require orthogonal DEM pixels. Resample the skewed DEM to a north-up grid first.')
        projection = dataset.GetProjection()
        if not projection:
            raise DemEditError('The DEM has no coordinate reference system.')

        scale = band.GetScale()
        offset = band.GetOffset()
        if ((scale is not None and not math.isclose(scale, 1.0, rel_tol=0.0, abs_tol=1e-12)) or
                (offset is not None and not math.isclose(offset, 0.0, rel_tol=0.0, abs_tol=1e-12))):
            raise DemEditError('This DEM uses a scale or offset. Convert it to physical elevation values before editing.')

        array = band.ReadAsArray()
        nodata = band.GetNoDataValue()
        input_dtype = array.dtype
        if np.issubdtype(input_dtype, np.complexfloating):
            raise DemEditError('Complex-valued rasters are not supported as DEM elevations. Convert to a suitable real-valued DEM first.')
        if np.issubdtype(input_dtype, np.integer) and input_dtype.itemsize > 4:
            raise DemEditError('This 64-bit integer DEM cannot be safely processed with floating-point elevation calculations. Convert it to a suitable DEM first.')
        valid = _valid_mask(array, nodata)
        if not (band.GetMaskFlags() & gdal.GMF_ALL_VALID):
            valid &= band.GetMaskBand().ReadAsArray() != 0
        requested_dtype = settings.get('output_dtype', 'float')
        if requested_dtype not in ('source', 'float'):
            raise DemEditError('Unknown output precision setting.')
        if requested_dtype == 'float' and np.issubdtype(input_dtype, np.integer):
            # Float32 represents every 8/16-bit integer exactly; Float64
            # preserves every 32-bit integer. Reject int64 rather than round.
            if input_dtype.itemsize <= 2:
                dtype = np.dtype(np.float32)
            elif input_dtype.itemsize <= 4:
                dtype = np.dtype(np.float64)
            else:
                raise DemEditError('This 64-bit integer DEM cannot be safely converted to a floating output.')
        else:
            dtype = input_dtype
        _report(progress, 8)

        dem_crs = QgsCoordinateReferenceSystem()
        dem_crs.createFromWkt(projection)
        if not dem_crs.isValid() or dem_crs.isGeographic() or dem_crs.mapUnits() != Qgis.DistanceUnit.Meters:
            raise DemEditError('Reproject the DEM to a projected CRS with meter units first.')
        project_crs = QgsCoordinateReferenceSystem()
        project_crs.createFromWkt(project_crs_wkt)

        corridor_geometry = _transform_to_dem(
            corridor_wkt, project_crs, dem_crs, transform_context
        )
        from .crs_check import inspect_crs
        context = transform_context if transform_context is not None else QgsCoordinateTransformContext()
        dem_crs_review = inspect_crs(dem_crs, corridor_geometry.pointOnSurface().asPoint(), context)
        project_geometry = _ensure_valid(QgsGeometry.fromWkt(corridor_wkt))
        project_crs_review = inspect_crs(project_crs, project_geometry.pointOnSurface().asPoint(), context)
        outside_dem_area = _outside_raster_area(dataset, corridor_geometry)
        transition_m = float(settings.get('feather_m', 0.0))
        transition_outside_dem_area = (_outside_raster_area(dataset, corridor_geometry, transition_m)
                                       if transition_m > 0 else 0.0)
        _report(progress, 12)

        mask = _raster_mask(dataset, corridor_geometry)
        mask_pixels = int(mask.sum())
        if mask_pixels == 0:
            raise DemEditError('No DEM pixel centers fall inside the corridor. Check overlap or use a wider corridor or finer-resolution DEM.')
        _report(progress, 18)

        inside = mask & valid
        edited_pixels = int(inside.sum())
        if edited_pixels == 0:
            raise DemEditError('The corridor contains no valid DEM pixels to edit.')
        rows, columns = np.nonzero(inside)

        inverse_geotransform = _inverse_geotransform(geotransform)
        x_values = (
            geotransform[0]
            + (columns + 0.5) * geotransform[1]
            + (rows + 0.5) * geotransform[2]
        )
        y_values = (
            geotransform[3]
            + (columns + 0.5) * geotransform[4]
            + (rows + 0.5) * geotransform[5]
        )

        mode = settings.get('mode', 'flat')
        applied_value = None
        edge_samples = 0
        max_grade = None
        raster_max_grade = None
        raster_grade_spacing = None
        raster_grade_sample_count = 0
        raster_grade_attempts = 0
        raster_grade_exceedances = None
        raster_crossfall_max = None
        crossfall_flagged_sections = []
        raster_crossfall_samples = 0
        raster_crossfall_attempts = 0
        raster_crossfall_exceedances = None
        plateau_ranges = []
        if mode == 'road':
            if not line_wkt:
                raise DemEditError('Road mode requires a digitized centerline.')
            line_geometry = _transform_to_dem(
                line_wkt, project_crs, dem_crs, transform_context
            )
            points, distances, pixel = _road_profile(
                array,
                valid,
                geotransform,
                inverse_geotransform,
                line_geometry,
                settings.get('smooth_m', 0.0),
                progress,
            )
            max_distance = 6.0 * float(settings.get('distance', 0.0)) + 2.0 * _pixel_size(
                geotransform
            )
            if settings.get('source') == 'polygon':
                max_distance = None
            max_cross_offset = (1.5 * float(settings['distance'])
                                if settings.get('source') in ('line', 'digitize') and settings.get('distance', 0) > 0
                                else None)
            section_along, section_terrain, diagnostic_points, section_edges = _cross_section_samples(
                points, distances, corridor_geometry, array, valid, inverse_geotransform, pixel, progress,
                bool(settings.get('diagnostics_path')), max_cross_offset)
            # Add mid-stations only where sampled cross-section terrain changes
            # quickly. This improves numerical coverage, not DEM information.
            if len(distances) < 7000:
                initial = _corridor_profile(distances, section_along, section_terrain, pixel,
                                            float(settings.get('smooth_m', 0.0)), 'terrain')
                slopes = np.abs(np.diff(initial) / np.diff(distances))
                threshold = max(0.15, float(settings.get('maximum_grade', 10.0)) / 100.0)
                # Do not introduce sub-pixel near-duplicates that amplify
                # sampling noise and make slope calculations ill-conditioned.
                min_spacing = min(pixel * 0.1, float(np.median(np.diff(distances))) * 0.2)
                candidates = np.flatnonzero((slopes > threshold) & (np.diff(distances) >= 2.0 * min_spacing))
                if candidates.size:
                    ranked = candidates[np.argsort(slopes[candidates])[-min(1000,7000-len(distances)):]]
                    midpoints = (distances[ranked] + distances[ranked+1]) / 2.0
                    distances = np.sort(np.r_[distances, midpoints])
                    points = [(line_geometry.interpolate(float(d)).asPoint().x(),
                               line_geometry.interpolate(float(d)).asPoint().y()) for d in distances]
                    section_along, section_terrain, diagnostic_points, section_edges = _cross_section_samples(
                        points, distances, corridor_geometry, array, valid, inverse_geotransform, pixel, progress,
                        bool(settings.get('diagnostics_path')), max_cross_offset)
            along = _nearest_profile(
                points,
                distances,
                distances,
                x_values,
                y_values,
                progress,
                max_distance=max_distance,
            )
            plateau_ranges = (_sharp_turn_plateaus(line_geometry, settings.get('distance', 0.0))
                              if settings.get('source') in ('digitize', 'line') else [])
            grade_intervals = _midpoint_grade_intervals(distances, section_edges)
            requested_grade = float(settings.get('maximum_grade', 10.0))
            fitted_grade = requested_grade
            grade_refits = 0
            conflict_refits = 0
            best_trial = None
            trial_output = None
            # Curved section normals can cross on the inside of an otherwise
            # ordinary bend. When their elevations conflict, try a short,
            # explicitly reported level section, then validate the ribbon
            # again. Do not automatically level long or ambiguous polygons.
            for attempt in range(8):
                elevations = _corridor_profile(
                    distances, section_along, section_terrain, pixel,
                    float(settings.get('smooth_m', 0.0)),
                    settings.get('profile_method', 'terrain'),
                    fitted_grade,
                    plateau_ranges,
                    grade_intervals,
                )
                max_grade = _sampled_grade(distances, elevations, pixel)
                initial_values = np.interp(along, distances, elevations)
                (new_values, ribbon_gaps, overlap_conflicts, overlap_max,
                 conflict_ranges, endpoint_gaps) = _road_ribbon_values(
                    section_edges, distances, elevations, x_values, y_values, along, initial_values,
                    progress, float(settings.get('crossfall_tolerance', 0.10)))
                if overlap_conflicts:
                    source_kind = settings.get('source')
                    if (conflict_refits >= 3 or source_kind not in ('line', 'digitize', 'polygon')
                            or not conflict_ranges or (source_kind == 'polygon' and settings.get('polygon_centerline_ambiguous'))):
                        break
                    if source_kind == 'polygon':
                        widths = [math.hypot(edge[1][0]-edge[0][0], edge[1][1]-edge[0][1])
                                  for edge in section_edges if edge is not None]
                        if not widths:
                            break
                        maximum_level_length = 6.0 * float(np.median(widths)) + 4.0 * pixel
                    else:
                        maximum_level_length = 6.0 * float(settings.get('distance', 0.0)) + 4.0 * pixel
                    if any(end-start > maximum_level_length for start, end in conflict_ranges):
                        break
                    proposed_ranges = _merge_overlapping_ranges(plateau_ranges + conflict_ranges)
                    if any(b-a > maximum_level_length for a, b in proposed_ranges
                           if any(a <= start and b >= end for start, end in conflict_ranges)):
                        break
                    # Avoid silently levelling a substantial fraction of a
                    # polygon-derived route to resolve an ambiguous skeleton.
                    if source_kind == 'polygon' and sum(b-a for a,b in proposed_ranges) > min(0.25 * float(distances[-1]), max(50.0 * pixel, 0.1 * float(distances[-1]))):
                        break
                    plateau_ranges = proposed_ranges
                    conflict_refits += 1
                    continue
                if settings.get('profile_method', 'terrain') not in ('grade', 'balanced'):
                    break
                # Assess the actual cast road cells before constructing side
                # slopes. If rasterization raises sampled grade above the
                # requested maximum, refit a little flatter and check again.
                # This remains a sampled check, never a compliance guarantee.
                if trial_output is None:
                    trial_output = array.astype(dtype, copy=True)
                trial_values = new_values
                if np.issubdtype(dtype, np.integer):
                    bounds = np.iinfo(dtype)
                    trial_values = np.clip(np.rint(trial_values), bounds.min, bounds.max)
                trial_output[rows, columns] = trial_values.astype(dtype)
                trial_grade, _, trial_count, _, trial_exceed = _sample_raster_grade(
                    trial_output, valid, mask, inverse_geotransform, distances,
                    section_edges, pixel, requested_grade)
                if trial_grade is None or trial_exceed is None:
                    break
                score = (trial_exceed, trial_grade)
                if best_trial is None or score < best_trial[0]:
                    best_trial = (score, new_values.copy(), elevations.copy(), ribbon_gaps,
                                  max_grade, list(plateau_ranges), fitted_grade, endpoint_gaps)
                if not trial_exceed or grade_refits >= 3 or requested_grade <= 0:
                    break
                fitted_grade *= min(0.95, requested_grade / trial_grade) * 0.99
                grade_refits += 1
            if best_trial is not None:
                _, new_values, elevations, ribbon_gaps, max_grade, plateau_ranges, fitted_grade, endpoint_gaps = best_trial
                overlap_conflicts = 0
            del trial_output
            if overlap_conflicts:
                raise DemEditError(
                    'Road cross-sections overlap with conflicting elevations at {} cells '
                    '(largest difference {:.2f} DEM elevation units). No edited DEM was written. Review '
                    'tight bends, hairpins or an ambiguous polygon centerline.'.format(
                        overlap_conflicts, overlap_max))
        else:
            ribbon_gaps = 0
            endpoint_gaps = 0
            if settings.get('flat_source') == 'edge':
                applied_value, edge_samples = _edge_statistic(
                    array, valid, mask, settings.get('statistic', 'median')
                )
            else:
                applied_value = float(settings.get('flat_value', 0.0))
            new_values = np.full(rows.size, applied_value, dtype=float)
        _report(progress, 66)

        output = array.astype(dtype, copy=True)
        values = new_values.astype(float)

        if np.issubdtype(dtype, np.integer):
            limits = np.iinfo(dtype)
            values = np.rint(values)
            values = np.clip(values, limits.min, limits.max)
        output[rows, columns] = values.astype(dtype)
        # Retain both the requested/calculated platform elevation and the
        # value actually representable by the chosen output pixel type.
        flat_output_elevation = float(output[rows[0], columns[0]]) if mode == 'flat' else None
        flat_rounding_difference = (flat_output_elevation - applied_value) if mode == 'flat' else None

        # Blend outside the road or platform, preserving its full flat width.
        transition_pixels = 0
        transition_nodata_pixels = 0
        unmet_transition_pixels = 0
        max_unmet_transition_delta = 0.0
        transition_edge_samples = 0
        estimated_tie_distance = None
        transition_along = None
        tr = tc = None
        transition_m = float(settings.get('feather_m', 0.0))
        if transition_m > 0.0:
            from scipy.ndimage import distance_transform_edt
            x_pixel = math.hypot(geotransform[1], geotransform[4])
            y_pixel = math.hypot(geotransform[2], geotransform[5])
            # Limit distance calculations to the corridor and its surroundings.
            # A full-DEM distance and index array can exhaust RAM on large DEMs.
            row_pad = int(math.ceil(transition_m / y_pixel)) + 1
            col_pad = int(math.ceil(transition_m / x_pixel)) + 1
            r0 = max(0, int(rows.min()) - row_pad)
            r1 = min(array.shape[0], int(rows.max()) + row_pad + 1)
            c0 = max(0, int(columns.min()) - col_pad)
            c1 = min(array.shape[1], int(columns.max()) + col_pad + 1)
            local_inside = inside[r0:r1, c0:c1]
            distance, nearest = distance_transform_edt(
                ~local_inside, sampling=(y_pixel, x_pixel), return_indices=True
            )
            local_transition = ((~mask[r0:r1, c0:c1]) & valid[r0:r1, c0:c1]
                                & (distance < transition_m))
            transition_nodata_pixels = int(np.count_nonzero(
                (~mask[r0:r1, c0:c1]) & (~valid[r0:r1, c0:c1]) & (distance < transition_m)))
            lr, lc = np.nonzero(local_transition)
            tr, tc = lr + r0, lc + c0
            if tr.size:
                if mode == 'road':
                    station_grid = np.full(local_inside.shape, np.nan, dtype=np.float64)
                    station_grid[rows-r0, columns-c0] = along
                    transition_along = station_grid[nearest[0, lr, lc], nearest[1, lr, lc]]
                surface = output[nearest[0, lr, lc] + r0, nearest[1, lr, lc] + c0].astype(float)
                original = array[tr, tc].astype(float)
                d = distance[lr, lc]
                cut = np.minimum(original, surface + float(settings.get('cut_slope', 1.0)) * d)
                fill = np.maximum(original, surface - float(settings.get('fill_slope', 0.5)) * d)
                blended = np.where(original >= surface, cut, fill)
                if np.issubdtype(dtype, np.integer):
                    blended = np.clip(np.rint(blended), limits.min, limits.max)
                # Diagnose the value actually representable in the output.
                blended = blended.astype(dtype).astype(float)
                edge = d >= transition_m - max(x_pixel, y_pixel)
                transition_edge_samples = int(np.count_nonzero(edge))
                residual = np.abs(blended - original)
                unmet = edge & (residual > 1e-6)
                unmet_transition_pixels = int(np.count_nonzero(unmet))
                if unmet_transition_pixels:
                    max_unmet_transition_delta = float(np.max(residual[unmet]))
                    slopes = np.where(original >= surface, float(settings.get('cut_slope', 1.0)),
                                      float(settings.get('fill_slope', 0.5)))
                    if np.all(slopes[unmet] > 0):
                        estimated_tie_distance = float(np.max(d[unmet] + residual[unmet] / slopes[unmet]))
                output[tr, tc] = blended.astype(dtype)
                transition_pixels = int(tr.size)
        for changed_rows, changed_columns in ((rows, columns), (tr, tc)):
            if changed_rows is None or not changed_rows.size:
                continue
            stored = output[changed_rows, changed_columns]
            if not np.isfinite(stored).all():
                raise DemEditError('The chosen output type cannot represent the edited elevations as finite numbers. Choose a suitable DEM and output precision.')
            if nodata is not None and not math.isnan(float(nodata)) and np.any(stored == nodata):
                raise DemEditError('An edited elevation equals the DEM NoData value. Adjust the edit or change the NoData value in a copy of the input DEM.')
        if mode == 'road':
            grade_limit = (float(settings.get('maximum_grade', 10.0))
                           if settings.get('profile_method', 'terrain') in ('grade', 'balanced') else None)
            (raster_max_grade, raster_grade_spacing, raster_grade_sample_count, raster_grade_attempts,
             raster_grade_exceedances) = _sample_raster_grade(
                output, valid, mask, inverse_geotransform, distances, section_edges, pixel, grade_limit)
            tolerance = float(settings.get('crossfall_tolerance', 0.10))
            (raster_crossfall_max, raster_crossfall_samples, raster_crossfall_attempts,
             raster_crossfall_exceedances) = _sample_raster_crossfall(
                output, valid, mask, inverse_geotransform, section_edges, tolerance, pixel,
                distances, crossfall_flagged_sections)
        changed = (output != array) & valid
        changed_pixels = int(np.count_nonzero(changed))
        differences = output[changed].astype(float) - array[changed].astype(float)
        max_cut = float(max(0.0, -differences.min())) if differences.size else 0.0
        max_fill = float(max(0.0, differences.max())) if differences.size else 0.0
        report_path = settings.get('report_path')
        if report_path:
            # Only materialize changed cells for the report. A full-size
            # Float64 difference could exhaust memory on regional DEMs.
            changed_rows, changed_cols = np.nonzero(changed)
            delta = output[changed_rows, changed_cols].astype(np.float64) - array[changed_rows, changed_cols].astype(np.float64)
            cut_mask = delta < -1e-6
            fill_mask = delta > 1e-6
            pixel_area = abs(geotransform[1] * geotransform[5] - geotransform[2] * geotransform[4])
            report_data = {
                'cut_pixels': int(cut_mask.sum()), 'fill_pixels': int(fill_mask.sum()),
                'unchanged_pixels': int(valid.sum() - cut_mask.sum() - fill_mask.sum()),
                'cut_area': float(cut_mask.sum() * pixel_area),
                'fill_area': float(fill_mask.sum() * pixel_area),
                'cut_volume': float(-delta[cut_mask].sum() * pixel_area),
                'fill_volume': float(delta[fill_mask].sum() * pixel_area),
                'pixel_area': float(pixel_area),
                'pixel_x': float(math.hypot(geotransform[1], geotransform[4])),
                'pixel_y': float(math.hypot(geotransform[2], geotransform[5])),
                'source_min': float(array[inside].min()), 'source_max': float(array[inside].max()),
                'result_min': float(output[inside].min()), 'result_max': float(output[inside].max()),
            }
            sections = []
            if mode == 'road':
                interval = max(25.0, min(100.0, float(distances[-1]) / 20.0))
                section_positions = along
                road_delta = output[rows, columns].astype(np.float64) - array[rows, columns].astype(np.float64)
                if transition_along is not None:
                    section_positions = np.concatenate((along, transition_along))
                    transition_delta = output[tr, tc].astype(np.float64) - array[tr, tc].astype(np.float64)
                    road_delta = np.concatenate((road_delta, transition_delta))
                section_positions = np.minimum(section_positions, np.nextafter(float(distances[-1]), -np.inf))
                section_index = np.floor(section_positions / interval).astype(int)
                for number in np.unique(section_index):
                    d = road_delta[section_index == number]
                    if not d.size: continue
                    sections.append((float(number*interval), min(float((number+1)*interval),float(distances[-1])),
                                     float(max(0, -d.min())), float(max(0, d.max())), int(d.size)))
            report_data['sections'] = sections
            del delta, cut_mask, fill_mask
        _report(progress, 75)

        _write_output(dem_path, output_path, output, dtype, nodata, progress)
        diagnostics_path = settings.get('diagnostics_path') if mode == 'road' else None
        if diagnostics_path:
            _write_sample_points(diagnostics_path, projection, diagnostic_points)
        difference_path = settings.get('difference_path')
        if difference_path:
            if os.path.exists(difference_path):
                raise DemEditError('The difference raster already exists.')
            # Float32 cannot exactly represent all 32-bit integer elevations.
            # Cast before subtracting, so small edits at large values survive.
            needs_double = (np.dtype(dtype) == np.dtype(np.float64) or
                            (np.issubdtype(input_dtype, np.integer) and input_dtype.itemsize >= 4))
            difference_type = gdal.GDT_Float64 if needs_double else gdal.GDT_Float32
            diff_ds = gdal.GetDriverByName('GTiff').Create(difference_path, dataset.RasterXSize,
                dataset.RasterYSize, 1, difference_type, options=['TILED=YES', 'COMPRESS=LZW'])
            if diff_ds is None:
                raise DemEditError('Could not create the difference raster.')
            diff_ds.SetGeoTransform(geotransform); diff_ds.SetProjection(projection)
            # NaN avoids misclassifying a genuine -9999 elevation change.
            diff_band = diff_ds.GetRasterBand(1); diff_band.SetNoDataValue(float('nan'))
            diff_dtype = np.float64 if difference_type == gdal.GDT_Float64 else np.float32
            diff = np.full(array.shape, np.nan, dtype=diff_dtype)
            diff[valid] = output[valid].astype(diff_dtype) - array[valid].astype(diff_dtype)
            diff_band.WriteArray(diff); diff_band.FlushCache(); diff_band = None; diff_ds = None
    finally:
        dataset = None

    corridor_multipart = QgsGeometry(corridor_geometry)
    if not corridor_multipart.isMultipart():
        corridor_multipart.convertToMultiType()

    if progress is not None:
        progress(100.0)
    return {
        'output_path': output_path,
        'difference_path': settings.get('difference_path'),
        'diagnostics_path': settings.get('diagnostics_path') if mode == 'road' else None,
        'report_data': report_data if report_path else None,
        'dem_crs_wkt': projection,
        'dem_crs_review': dem_crs_review,
        'project_crs_review': project_crs_review,
        'corridor_wkt': corridor_multipart.asWkt(),
        'mode': mode,
        'applied_value': applied_value,
        'flat_output_elevation': flat_output_elevation,
        'flat_rounding_difference': flat_rounding_difference,
        'edge_samples': edge_samples,
        'corridor_pixels': mask_pixels,
        'edited_pixels': edited_pixels,
        'transition_pixels': transition_pixels,
        'outside_dem_area': outside_dem_area,
        'transition_outside_dem_area': transition_outside_dem_area,
        'transition_nodata_pixels': transition_nodata_pixels,
        'unmet_transition_pixels': unmet_transition_pixels,
        'max_unmet_transition_delta': max_unmet_transition_delta,
        'transition_edge_samples': transition_edge_samples,
        'transition_edge_unchecked': bool(transition_m > 0 and transition_pixels > 0 and transition_edge_samples == 0),
        'estimated_tie_distance': estimated_tie_distance,
        'transition_unrepresented': bool(transition_m > 0 and transition_pixels == 0),
        'changed_pixels': changed_pixels,
        'max_cut': max_cut,
        'max_fill': max_fill,
        'max_grade': max_grade,
        'profile_max_grade': max_grade,
        'fitted_grade_limit': fitted_grade if mode == 'road' and settings.get('profile_method', 'terrain') in ('grade', 'balanced') else None,
        'raster_max_grade': raster_max_grade,
        'raster_grade_spacing': raster_grade_spacing,
        'raster_grade_sample_count': raster_grade_sample_count,
        'raster_grade_attempts': raster_grade_attempts,
        'raster_grade_exceedances': raster_grade_exceedances,
        'raster_crossfall_max': raster_crossfall_max,
        'crossfall_flagged_sections': sorted(crossfall_flagged_sections, key=lambda item: item[1], reverse=True)[:10],
        'raster_crossfall_samples': raster_crossfall_samples,
        'raster_crossfall_attempts': raster_crossfall_attempts,
        'raster_crossfall_exceedances': raster_crossfall_exceedances,
        'crossfall_tolerance': float(settings.get('crossfall_tolerance', 0.10)) if mode == 'road' else None,
        'turn_plateau_ranges': plateau_ranges,
        'output_dtype': str(dtype),
        'ribbon_gap_pixels': ribbon_gaps,
        'ribbon_endpoint_pixels': endpoint_gaps,
        'skipped_nodata_pixels': int((mask & ~valid).sum()),
        'z_min': float(output[rows, columns].min()),
        'z_max': float(output[rows, columns].max()),
    }

