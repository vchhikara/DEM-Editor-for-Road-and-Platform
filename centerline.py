"""Approximate a polygon centreline for review; never edits the source geometry."""
import math
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from qgis.core import QgsGeometry, QgsPointXY


def sample_widths(geometry, line, count=100):
    """Approximate cross-section widths for a reviewed line in a polygon."""
    bounds = geometry.boundingBox()
    span = math.hypot(bounds.width(), bounds.height()) + 1.0
    length = line.length()
    if length <= 0: return []
    step = length / max(1, count - 1)
    widths = []
    for station in np.linspace(0, length, count):
        p = line.interpolate(float(station)).asPoint()
        before = line.interpolate(max(0.0, station-step)).asPoint()
        after = line.interpolate(min(length, station+step)).asPoint()
        dx, dy = after.x()-before.x(), after.y()-before.y()
        norm = math.hypot(dx,dy)
        if norm == 0: continue
        nx, ny = -dy/norm, dx/norm
        cross = QgsGeometry.fromPolylineXY([QgsPointXY(p.x()-nx*span,p.y()-ny*span), QgsPointXY(p.x()+nx*span,p.y()+ny*span)])
        section = geometry.intersection(cross)
        if section and not section.isEmpty():
            parts = section.asMultiPolyline() if section.isMultipart() else [section.asPolyline()]
            parts = [part for part in parts if len(part) >= 2]
            if parts:
                center = QgsGeometry.fromPointXY(QgsPointXY(p))
                nearest = min((QgsGeometry.fromPolylineXY(part) for part in parts), key=lambda part: part.distance(center))
                if nearest.length() > 0:
                    widths.append(nearest.length())
    return widths


def polygon_centerline(geometry, pixel_size, crs_wkt=None):
    from osgeo import gdal, ogr, osr
    if geometry.isEmpty():
        raise ValueError('The polygon is empty.')
    polygons = geometry.asMultiPolygon() if geometry.isMultipart() else [geometry.asPolygon()]
    if len(polygons) != 1:
        raise ValueError('The road polygon has disconnected parts. Process one continuous corridor at a time.')
    has_holes = len(polygons[0]) > 1
    bounds = geometry.boundingBox()
    scale = max(float(pixel_size), max(bounds.width(), bounds.height()) / 1200.0, 0.01)
    width = max(3, int(math.ceil(bounds.width() / scale)) + 4)
    height = max(3, int(math.ceil(bounds.height() / scale)) + 4)
    if width * height > 2000000:
        raise ValueError('Polygon is too large to derive a centerline at this resolution.')
    origin_x, origin_y = bounds.xMinimum() - 2*scale, bounds.yMaximum() + 2*scale
    vector = ogr.GetDriverByName('Memory').CreateDataSource('')
    srs = None
    if crs_wkt:
        srs = osr.SpatialReference()
        if srs.ImportFromWkt(crs_wkt) != 0:
            raise ValueError('Could not read the polygon project coordinate system.')
    layer = vector.CreateLayer('polygon', srs=srs, geom_type=ogr.wkbUnknown)
    feat = ogr.Feature(layer.GetLayerDefn()); feat.SetGeometry(ogr.CreateGeometryFromWkt(geometry.asWkt()))
    layer.CreateFeature(feat)
    raster = gdal.GetDriverByName('MEM').Create('', width, height, 1, gdal.GDT_Byte)
    raster.SetGeoTransform((origin_x, scale, 0, origin_y, 0, -scale))
    if srs is not None:
        raster.SetProjection(srs.ExportToWkt())
    gdal.RasterizeLayer(raster, [1], layer, burn_values=[1])
    image = raster.ReadAsArray().astype(bool)
    if image.sum() < 4: raise ValueError('Polygon is too narrow relative to the DEM grid to derive a centerline.')
    # Zhang-Suen thinning, with an all-false border.
    image[[0, -1], :] = False; image[:, [0, -1]] = False
    for iteration in range(max(width, height)):
        changed = False
        for phase in (0, 1):
            c=image[1:-1,1:-1]; p=[image[:-2,1:-1],image[:-2,2:],image[1:-1,2:],image[2:,2:],image[2:,1:-1],image[2:,:-2],image[1:-1,:-2],image[:-2,:-2]]
            count=sum(x.astype(np.uint8) for x in p)
            transitions=sum((~p[i] & p[(i+1)%8]).astype(np.uint8) for i in range(8))
            if phase==0: cond=(~(p[0]&p[2]&p[4])) & (~(p[2]&p[4]&p[6]))
            else: cond=(~(p[0]&p[2]&p[6])) & (~(p[0]&p[4]&p[6]))
            remove=c & (count>=2) & (count<=6) & (transitions==1) & cond
            if remove.any(): c[remove]=False; changed=True
        if not changed: break
    coords=np.argwhere(image)
    if len(coords)<2: raise ValueError('Could not derive a usable centerline; use an existing line instead.')
    index={tuple(p):i for i,p in enumerate(coords)}; rr=[];cc=[];weights=[]
    for i,(r,c) in enumerate(coords):
        for dr,dc in ((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)):
            j=index.get((r+dr,c+dc))
            if j is not None: rr.append(i);cc.append(j);weights.append(math.hypot(dr,dc)*scale)
    graph=csr_matrix((weights,(rr,cc)),shape=(len(coords),len(coords)))
    degree=np.diff(graph.indptr); endpoints=np.flatnonzero(degree==1)
    if len(endpoints)<2: raise ValueError('The derived centerline is closed or ambiguous. Select an existing centerline instead.')
    # Two sweeps on the connected skeleton approximate the longest route.
    d=dijkstra(graph,indices=int(endpoints[0]),directed=False); finite=endpoints[np.isfinite(d[endpoints])]
    if len(finite)<2: raise ValueError('The polygon centerline is disconnected. Use a single continuous corridor.')
    start=int(finite[np.argmax(d[finite])]); d,prev=dijkstra(graph,indices=start,directed=False,return_predecessors=True)
    finite=endpoints[np.isfinite(d[endpoints])]; end=int(finite[np.argmax(d[finite])]); path=[]; current=end
    while current>=0:
        path.append(current)
        if current==start: break
        current=int(prev[current])
    if not path or path[-1]!=start: raise ValueError('The centerline could not be traced.')
    points=[QgsPointXY(origin_x+(coords[i,1]+0.5)*scale, origin_y-(coords[i,0]+0.5)*scale) for i in reversed(path)]
    line=QgsGeometry.fromPolylineXY(points).simplify(scale*0.35)
    # Raster skeletons tend to zigzag at the grid scale. Smooth only when the
    # entire suggested line stays inside the polygon; otherwise retain the
    # original route for review instead of cutting across a boundary or hole.
    smooth = line.smooth(3, 0.25, -1, 180).simplify(scale*0.2)
    if smooth is not None and not smooth.isEmpty() and smooth.isSimple():
        outside_smooth = smooth.difference(geometry)
        if (outside_smooth is not None and not outside_smooth.isNull()
                and (outside_smooth.isEmpty() or outside_smooth.length() <= max(1e-8, scale*1e-8))):
            line = smooth
    if not line.isSimple():
        raise ValueError('The suggested centerline crosses itself. Use a reviewed existing centerline instead.')
    outside = line.difference(geometry)
    if outside and not outside.isEmpty() and outside.length() > max(1e-8, scale * 1e-8):
        raise ValueError('The suggested centerline leaves the polygon. Use a reviewed existing centerline instead.')
    return line, has_holes or len(endpoints)!=2 or len(finite)!=len(endpoints)
