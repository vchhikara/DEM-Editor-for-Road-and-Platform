"""Approximate local CRS suitability check for a representative edit point."""
import math
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsDistanceArea, QgsPointXY


def inspect_crs(crs, point, context):
    """Return a local grid-vs-geodesic estimate; not a survey certification."""
    result = {'checked': False, 'outside_area': False, 'scale_error_pct': None}
    try:
        transform = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem('EPSG:4326'), context)
        geo = transform.transform(QgsPointXY(point))
        bounds = crs.bounds()
        if not bounds.isEmpty():
            result['outside_area'] = not bounds.contains(geo)
        measure = QgsDistanceArea()
        measure.setSourceCrs(crs, context)
        if not measure.setEllipsoid('WGS84'):
            return result
        grid_distance = 1000.0
        x, y = point.x(), point.y()
        ground_x = measure.measureLine(QgsPointXY(x, y), QgsPointXY(x + grid_distance, y))
        ground_y = measure.measureLine(QgsPointXY(x, y), QgsPointXY(x, y + grid_distance))
        if not all(math.isfinite(v) and v > 0 for v in (ground_x, ground_y)):
            return result
        result['scale_error_pct'] = max(abs(grid_distance/ground_x - 1.0), abs(grid_distance/ground_y - 1.0))*100.0
        result['checked'] = True
    except Exception:
        return result
    return result


def crs_cautions(label, result):
    cautions = []
    if result.get('outside_area'):
        cautions.append('{} lies outside its CRS stated area of use at the sampled location. Choose an appropriate local projected CRS.'.format(label))
    if result.get('checked') and result.get('scale_error_pct', 0.0) > 1.0:
        cautions.append('{} has approximately {:.1f}% local grid-distance distortion at the sampled location. Review or choose a better local CRS; 1% is a review prompt, not an engineering limit.'.format(label, result['scale_error_pct']))
    if not result.get('checked'):
        cautions.append('{} local distance distortion could not be checked. Verify the CRS independently.'.format(label))
    return cautions
