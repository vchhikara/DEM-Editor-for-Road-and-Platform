import os

FIELDS = (
    ('mode', 'string', 32),
    ('dist_m', 'real', 0),
    ('side', 'string', 16),
    ('stat', 'string', 16),
    ('source', 'string', 24),
    ('dem_path', 'string', 254),
    ('edit_date', 'string', 24),
)


class CorridorWriteError(Exception):
    pass


def _same_srs(first, second):
    from osgeo import osr

    if first is None or second is None:
        return True
    a = first.Clone()
    b = second.Clone()
    a.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    b.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return bool(a.IsSame(b))


def _ensure_fields(layer):
    from osgeo import ogr

    definition = layer.GetLayerDefn()
    existing = {
        definition.GetFieldDefn(index).GetName().lower()
        for index in range(definition.GetFieldCount())
    }
    for name, kind, width in FIELDS:
        if name.lower() in existing:
            continue
        field = ogr.FieldDefn(name, ogr.OFTReal if kind == 'real' else ogr.OFTString)
        if kind == 'string':
            field.SetWidth(width)
        layer.CreateField(field)
        existing.add(name.lower())


def write_corridor(shapefile_path, corridor_wkt, dem_crs_wkt, attributes):
    from osgeo import ogr, osr

    directory = os.path.dirname(os.path.abspath(shapefile_path))
    if not os.path.isdir(directory):
        raise CorridorWriteError('The shapefile folder does not exist: {}'.format(directory))

    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromWkt(dem_crs_wkt)

    driver = ogr.GetDriverByName('ESRI Shapefile')
    if driver is None:
        raise CorridorWriteError('The ESRI Shapefile driver is not available.')

    dataset = None
    try:
        if os.path.exists(shapefile_path):
            raise CorridorWriteError('The corridor shapefile already exists: {}'.format(shapefile_path))
        else:
            dataset = driver.CreateDataSource(shapefile_path)
            if dataset is None:
                raise CorridorWriteError(
                    'Could not create the corridor shapefile: {}'.format(shapefile_path)
                )
            layer_name = os.path.splitext(os.path.basename(shapefile_path))[0]
            layer = dataset.CreateLayer(
                layer_name, spatial_ref, ogr.wkbMultiPolygon, options=['ENCODING=UTF-8']
            )
            if layer is None:
                raise CorridorWriteError('Could not create the corridor layer.')
            _ensure_fields(layer)

        geometry = ogr.CreateGeometryFromWkt(corridor_wkt)
        if geometry is None or geometry.IsEmpty():
            raise CorridorWriteError('The corridor geometry is empty.')
        if geometry.GetGeometryType() not in (ogr.wkbMultiPolygon, ogr.wkbMultiPolygon25D):
            geometry = ogr.ForceToMultiPolygon(geometry)

        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(geometry)

        definition = layer.GetLayerDefn()
        available = {
            definition.GetFieldDefn(index).GetName().lower()
            for index in range(definition.GetFieldCount())
        }
        for name, value in attributes.items():
            if name.lower() in available:
                feature.SetField(name, value)

        if layer.CreateFeature(feature) != 0:
            raise CorridorWriteError('Could not write the corridor feature.')
    finally:
        dataset = None

    return shapefile_path
