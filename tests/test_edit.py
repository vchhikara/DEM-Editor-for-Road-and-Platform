"""Full DEM edit tests with a synthetic sloped GeoTIFF."""
import os, sys
import numpy as np
import pytest
pytest.importorskip("qgis.core")
gdal = pytest.importorskip("osgeo.gdal")
pytest.importorskip("scipy")
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRS = QgsCoordinateReferenceSystem("EPSG:32643")

@pytest.fixture
def edit(app):
    import importlib.util
    spec = importlib.util.spec_from_file_location("demplugin", os.path.join(ROOT, "__init__.py"),
                                                  submodule_search_locations=[ROOT])
    m = importlib.util.module_from_spec(spec); sys.modules["demplugin"] = m; spec.loader.exec_module(m)
    from demplugin.dem_editor import edit_dem
    return edit_dem

@pytest.fixture
def dem(tmp_path):
    path = str(tmp_path / "dem.tif")
    ds = gdal.GetDriverByName("GTiff").Create(path, 100, 100, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((500000, 1, 0, 3000100, 0, -1)); ds.SetProjection(CRS.toWkt())
    yy, xx = np.mgrid[0:100, 0:100]
    ds.GetRasterBand(1).WriteArray((100 + 0.1 * xx + 0.05 * yy).astype("float32")); ds = None
    return path

def read(path):
    ds = gdal.Open(path); a = ds.GetRasterBand(1).ReadAsArray(); ds = None; return a

def test_flat_mode(edit, dem, tmp_path):
    out = str(tmp_path / "out.tif")
    box = "POLYGON((500030 3000070,500070 3000070,500070 3000030,500030 3000030,500030 3000070))"
    edit(dem, CRS.toWkt(), box, None,
         {"mode": "flat", "flat_source": "value", "flat_value": 150.0, "feather_m": 10.0}, out)
    a, b = read(out), read(dem)
    assert np.allclose(a[40:60, 40:60], 150.0)
    assert (a != b).sum() > 400
    assert np.array_equal(a[0:5, 0:5], b[0:5, 0:5])

def test_road_mode(edit, dem, tmp_path):
    out = str(tmp_path / "road.tif")
    line = QgsGeometry.fromWkt("LINESTRING(500010 3000050,500090 3000050)")
    corridor = line.buffer(5, 12).asWkt()
    edit(dem, CRS.toWkt(), corridor, line.asWkt(),
         {"mode": "road", "distance": 5.0, "smooth_m": 10.0, "feather_m": 10.0}, out)
    a, b = read(out), read(dem)
    rows = slice(47, 53)
    assert np.allclose(a[rows, 50], a[50, 50], atol=1e-3)
    assert (a != b).sum() > 100

def test_refuses_missing_dem(edit, tmp_path):
    from demplugin.dem_editor import DemEditError
    with pytest.raises(DemEditError):
        edit(str(tmp_path / "x.tif"), CRS.toWkt(), "POLYGON((0 0,1 0,1 1,0 0))", None, {}, str(tmp_path / "o.tif"))
