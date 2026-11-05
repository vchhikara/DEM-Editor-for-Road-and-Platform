import math
import os

import numpy as np
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsGeometry,
)


class DemEditError(Exception):
    pass


class EditCancelled(Exception):
    pass


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
            pass
    return mask


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
    spacing = max(pixel, length / 4000.0)
    count = max(2, int(math.ceil(length / spacing)) + 1)

    points = []
    distances = []
    for index in range(count):
        along = length * index / float(count - 1)
        point_geometry = line_geometry.interpolate(along)
        point = point_geometry.asPoint() if hasattr(point_geometry, 'asPoint') else point_geometry
        points.append((point.x(), point.y()))
        distances.append(along)
    _report(progress, 28)

    # Elevations are derived from the full corridor after pixel projection.
    return points, np.array(distances, dtype=float), pixel


def _corridor_profile(distances, along, terrain, pixel, smooth_m):
    """Median terrain per station balances cut/fill across the sampled corridor."""
    stations = np.searchsorted((distances[:-1] + distances[1:]) / 2.0, along)
    order = np.argsort(stations, kind='stable')
    ordered_stations = stations[order]
    starts = np.r_[0, np.flatnonzero(np.diff(ordered_stations)) + 1]
    ends = np.r_[starts[1:], order.size]
    profile = np.full(distances.size, np.nan, dtype=float)
    for start, end in zip(starts, ends):
        profile[ordered_stations[start]] = np.median(terrain[order[start:end]])
    good = np.isfinite(profile)
    if not good.any():
        raise DemEditError('No valid terrain samples were found inside the road corridor.')
    profile = np.interp(distances, distances[good], profile[good])
    spacing = distances[-1] / max(1, distances.size - 1)
    width = smooth_m if smooth_m > 0.0 else 5.0 * pixel
    window = max(1, int(round(width / spacing)))
    return _moving_average(profile, window)


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

    temporary = output_path + '.part.tif'
    if os.path.exists(temporary):
        try:
            os.remove(temporary)
        except OSError:
            pass

    source = gdal.Open(dem_path, gdal.GA_ReadOnly)
    if source is None:
        raise DemEditError('Could not reopen the source DEM for writing.')
    try:
        driver = gdal.GetDriverByName('GTiff')
        if driver is None:
            raise DemEditError('The GeoTIFF driver is not available.')
        destination = driver.CreateCopy(
            temporary, source, 0, options=['TILED=YES', 'COMPRESS=LZW']
        )
        if destination is None:
            raise DemEditError('Could not create the output GeoTIFF.')
        try:
            band = destination.GetRasterBand(1)
            band.WriteArray(array.astype(dtype))
            if nodata is not None:
                band.SetNoDataValue(float(nodata))
            destination.FlushCache()
            try:
                band.ComputeStatistics(False)
            except Exception:
                pass
        finally:
            destination = None
    finally:
        source = None

    if os.path.exists(output_path):
        raise DemEditError('The output file already exists. Choose a new output path.')
    os.replace(temporary, output_path)
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
    line_len = max(np.hypot(line_dx, line_dy), 1.0)

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
        geotransform = dataset.GetGeoTransform()
        projection = dataset.GetProjection()
        if not projection:
            raise DemEditError('The DEM has no coordinate reference system.')

        array = band.ReadAsArray()
        nodata = band.GetNoDataValue()
        dtype = array.dtype
        valid = _valid_mask(array, nodata)
        _report(progress, 8)

        dem_crs = QgsCoordinateReferenceSystem()
        dem_crs.createFromWkt(projection)
        if not dem_crs.isValid() or dem_crs.isGeographic() or dem_crs.mapUnits() != 0:
            raise DemEditError('Reproject the DEM to a projected CRS with meter units first.')
        project_crs = QgsCoordinateReferenceSystem()
        project_crs.createFromWkt(project_crs_wkt)

        corridor_geometry = _transform_to_dem(
            corridor_wkt, project_crs, dem_crs, transform_context
        )
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
            along = _nearest_profile(
                points,
                distances,
                distances,
                x_values,
                y_values,
                progress,
                max_distance=max_distance,
            )
            elevations = _corridor_profile(
                distances, along, array[rows, columns].astype(float), pixel,
                float(settings.get('smooth_m', 0.0)),
            )
            new_values = np.interp(along, distances, elevations)
        else:
            if settings.get('flat_source') == 'edge':
                applied_value, edge_samples = _edge_statistic(
                    array, valid, mask, settings.get('statistic', 'median')
                )
            else:
                applied_value = float(settings.get('flat_value', 0.0))
            new_values = np.full(rows.size, applied_value, dtype=float)
        _report(progress, 66)

        output = array.copy()
        values = new_values.astype(float)

        if np.issubdtype(dtype, np.integer):
            limits = np.iinfo(dtype)
            values = np.rint(values)
            values = np.clip(values, limits.min, limits.max)
        output[rows, columns] = values.astype(dtype)

        # Blend outside the road or platform, preserving its full flat width.
        transition_pixels = 0
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
            lr, lc = np.nonzero(local_transition)
            tr, tc = lr + r0, lc + c0
            if tr.size:
                t = np.clip(distance[lr, lc] / transition_m, 0.0, 1.0)
                weight = 1.0 - t * t * (3.0 - 2.0 * t)
                surface = output[nearest[0, lr, lc] + r0, nearest[1, lr, lc] + c0].astype(float)
                blended = weight * surface + (1.0 - weight) * array[tr, tc].astype(float)
                if np.issubdtype(dtype, np.integer):
                    blended = np.clip(np.rint(blended), limits.min, limits.max)
                output[tr, tc] = blended.astype(dtype)
                transition_pixels = int(tr.size)
        changed = (output != array) & valid
        changed_pixels = int(np.count_nonzero(changed))
        differences = output[changed].astype(float) - array[changed].astype(float)
        max_cut = float(max(0.0, -differences.min())) if differences.size else 0.0
        max_fill = float(max(0.0, differences.max())) if differences.size else 0.0
        _report(progress, 75)

        _write_output(dem_path, output_path, output, dtype, nodata, progress)
    finally:
        dataset = None

    corridor_multipart = QgsGeometry(corridor_geometry)
    if not corridor_multipart.isMultipart():
        corridor_multipart.convertToMultiType()

    if progress is not None:
        progress(100.0)
    return {
        'output_path': output_path,
        'dem_crs_wkt': projection,
        'corridor_wkt': corridor_multipart.asWkt(),
        'mode': mode,
        'applied_value': applied_value,
        'edge_samples': edge_samples,
        'corridor_pixels': mask_pixels,
        'edited_pixels': edited_pixels,
        'transition_pixels': transition_pixels,
        'changed_pixels': changed_pixels,
        'max_cut': max_cut,
        'max_fill': max_fill,
        'skipped_nodata_pixels': int((mask & ~valid).sum()),
        'z_min': float(output[rows, columns].min()),
        'z_max': float(output[rows, columns].max()),
    }
