"""Fixtures for the real-terrain tests. They skip when no real DEM exists."""
import os
import sys

import numpy as np
import pytest

pytest.importorskip("qgis.core")
gdal = pytest.importorskip("osgeo.gdal")
pytest.importorskip("scipy")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from qgis.core import QgsCoordinateReferenceSystem, Qgis  # noqa: E402


@pytest.fixture(scope="session")
def plugin(app):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "demplugin", os.path.join(helpers.ROOT, "__init__.py"),
        submodule_search_locations=[helpers.ROOT])
    module = importlib.util.module_from_spec(spec)
    sys.modules["demplugin"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def real_dem_path():
    path, note = helpers.find_real_dem()
    if path is None:
        pytest.skip(note)
    ds = gdal.Open(path)
    crs = QgsCoordinateReferenceSystem()
    crs.createFromWkt(ds.GetProjection())
    bands = ds.RasterCount
    ds = None
    if bands != 1 or crs.isGeographic() or not crs.isValid() or crs.mapUnits() != Qgis.DistanceUnit.Meters:
        pytest.skip("DEM_TEST_FILE must be single band, in a projected CRS with metre units: " + path)
    return path


@pytest.fixture(scope="session")
def terrain(real_dem_path):
    array, info = helpers.read_raster(real_dem_path)
    return {'path': real_dem_path, 'array': array, 'info': info, 'gt': info['gt'],
            'pixel': abs(info['gt'][1])}


@pytest.fixture
def session(plugin, terrain, tmp_path, monkeypatch):
    """A QGIS-like session on the unchanged real DEM, with an empty output folder."""
    out = tmp_path / "out"
    out.mkdir()
    return helpers.Session(plugin, terrain['path'], out, monkeypatch)


@pytest.fixture
def make_session(plugin, tmp_path, monkeypatch):
    """Session factory for a DEM variant (for example one with NoData holes)."""
    def build(dem_path, project_crs_wkt=None):
        out = tmp_path / "out"
        out.mkdir(exist_ok=True)
        return helpers.Session(plugin, dem_path, out, monkeypatch, project_crs_wkt)
    return build


@pytest.fixture(scope="session")
def hillside(terrain):
    """A 200 m straight line over a real sidehill with a 5-8 % along grade."""
    line = helpers.find_hillside_line(terrain['array'], terrain['gt'], 200.0, 5.0, 8.0, 80.0)
    if line is None:
        pytest.skip("No 200 m hillside line with 5-8 % grade found on this DEM.")
    return line


@pytest.fixture(scope="session")
def steep_hillside(terrain):
    """A 150 m straight line on the steepest consistent real slope found."""
    for lo, hi in ((14.0, 30.0), (11.0, 14.0), (9.0, 11.0)):
        line = helpers.find_hillside_line(terrain['array'], terrain['gt'], 150.0, lo, hi, 80.0, 2.0)
        if line is not None:
            return line
    pytest.skip("No steep hillside line (9 % or more) found on this DEM.")


@pytest.fixture(scope="session")
def open_ground(terrain):
    """Centre of a 200 m square of real, gently sloping ground (3-9 %)."""
    centre = helpers.find_open_ground(terrain['array'], terrain['gt'], 200.0, 60.0, 3.0, 9.0)
    if centre is None:
        pytest.skip("No 200 m square of 3-9 % ground found on this DEM.")
    return centre
