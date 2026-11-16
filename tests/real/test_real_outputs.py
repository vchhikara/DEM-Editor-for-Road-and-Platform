"""Output safety, CRS rules, data types and report accuracy, on real terrain."""
import hashlib
import os
import re

import numpy as np
import pytest
from osgeo import gdal
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsProject

import helpers as h
from test_real_platform import pad, run_pad  # noqa: F401  (fixture reuse)


def digest(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def run_road(session, line, name, **over):
    over.setdefault('distance', 10.0)
    out = session.run_digitized(line, session.settings(name, **over))
    return out


# --------------------------------------------------------------- (h) no overwrite

def test_existing_output_is_never_overwritten(session, terrain, hillside):
    """The user picks an output name that already holds an earlier result (with
    a sidecar and a corridor shapefile). The plugin must keep the old files
    byte for byte and write the new result under a numbered name."""
    out_dir = session.outdir
    old = {}
    for name, payload in (('keep.tif', b'earlier result'), ('keep.tif.aux.xml', b'<x/>'),
                          ('keep_corridor.shp', b'old shape'), ('keep_corridor.dbf', b'old dbf'),
                          ('keep_report.txt', b'old report')):
        path = os.path.join(out_dir, name)
        with open(path, 'wb') as f:
            f.write(payload)
        old[path] = digest(path)
    out = run_road(session, hillside, 'keep')
    assert out.ok, out.exception
    result = out.result
    assert os.path.basename(result['output_path']) != 'keep.tif'
    assert os.path.isfile(result['output_path'])
    for path, value in old.items():
        assert digest(path) == value, "An existing file was changed: " + path
    # The new files do not use any name of the old family.
    for key in ('output_path', 'report_path', 'shapefile_path', 'difference_path'):
        assert result[key] not in old


def test_core_edit_refuses_existing_output(plugin, terrain, tmp_path):
    """Called directly with an output path that already exists, the edit
    function must raise an error and leave the file as it was."""
    from demplugin.dem_editor import edit_dem, DemEditError
    target = tmp_path / 'already_there.tif'
    target.write_bytes(b'precious')
    before = digest(str(target))
    box = 'POLYGON((-13628700 4547900,-13628650 4547900,-13628650 4547860,-13628700 4547860,-13628700 4547900))'
    with pytest.raises(DemEditError):
        edit_dem(terrain['path'], terrain['info']['wkt'], box, None,
                 {'mode': 'flat', 'flat_source': 'value', 'flat_value': 50.0, 'feather_m': 5.0}, str(target))
    assert digest(str(target)) == before
    assert not [n for n in os.listdir(tmp_path) if n.startswith('.dem_editor_')]


def test_source_dem_is_never_changed(session, terrain, hillside):
    """The original DEM is the user's survey. Running a road edit must leave
    the source file untouched, byte for byte."""
    before = digest(terrain['path'])
    out = run_road(session, hillside, 'safe')
    assert out.ok, out.exception
    assert digest(terrain['path']) == before


# ------------------------------------------------------ (i) cancel and failure

def folder_state(session):
    return h.files_in(session.outdir)


@pytest.mark.parametrize('cancel_at', [20, 66, 100])
def test_cancel_during_edit_leaves_no_files(session, hillside, cancel_at):
    """The user presses Cancel while the edit runs (early, in the middle, and at
    the very end). No output file, report, shapefile or temporary folder may remain."""
    before = folder_state(session)
    out = session.run_digitized(hillside, session.settings('cancelled', distance=10.0), cancel_at=cancel_at)
    assert out.ran and out.ok is False
    assert type(out.exception).__name__ == 'EditCancelled'
    assert folder_state(session) == before, "Partial files remained: {}".format(folder_state(session))


@pytest.mark.parametrize('checks_before_cancel', [2, 3, 6])
def test_cancel_while_publishing_rolls_back(session, hillside, checks_before_cancel):
    """The user cancels at the last moment, while the finished files are being
    copied to the output folder. Files already copied must be removed again."""
    before = folder_state(session)
    out = session.run_digitized(hillside, session.settings('late', distance=10.0),
                                cancel_after_progress=100, checks_before_cancel=checks_before_cancel)
    assert out.ran and out.ok is False
    assert type(out.exception).__name__ == 'EditCancelled'
    assert folder_state(session) == before, "Partial files remained: {}".format(folder_state(session))


def test_failure_while_writing_report_leaves_no_files(session, hillside, monkeypatch):
    """The disk fails when the report is written, after the DEM was already
    produced. The user must not be left with a DEM that has no report."""
    def broken(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(session.module, 'write_report', broken)
    before = folder_state(session)
    out = session.run_digitized(hillside, session.settings('broken', distance=10.0))
    assert out.ok is False and 'disk full' in str(out.exception)
    assert folder_state(session) == before


def test_failure_when_pad_level_equals_nodata_leaves_no_files(make_session, terrain, pad, tmp_path):  # noqa: F811
    """A DEM uses 40.0 as its NoData marker. The user types 40.0 as platform
    level. The plugin must stop with a clear message and write nothing."""
    array = terrain['array'].copy()
    assert not (array == 40.0).any()
    path = h.write_raster(str(tmp_path / 'nodata40.tif'), array, terrain['gt'], terrain['info']['wkt'], nodata=40.0)
    session = make_session(path)
    before = folder_state(session)
    out = session.run_flat_polygon(pad, session.settings('clash', mode='flat', flat_source='value', flat_value=40.0))
    assert out.ok is False
    assert 'NoData' in str(out.exception)
    assert folder_state(session) == before


# ------------------------------------------------------------------- (j) CRS

def warp(src, dst, srs):
    gdal.Warp(dst, src, dstSRS=srs, xRes=5, yRes=5, resampleAlg='bilinear', outputType=gdal.GDT_Float32,
              dstNodata=-9999)
    return dst


def test_projected_metre_crs_is_accepted(make_session, terrain, hillside, tmp_path):
    """The same real terrain in UTM zone 10N (metres) while the QGIS project is
    in Web Mercator. The line is drawn in the project CRS. The plugin must
    move it into the DEM CRS and make a road that is level across its width."""
    utm = warp(terrain['path'], str(tmp_path / 'utm10.tif'), 'EPSG:32610')
    session = make_session(utm, project_crs_wkt=terrain['info']['wkt'])
    out = run_road(session, hillside, 'utm', distance=10.0)
    assert out.ok, out.exception
    result = out.result
    edited, info = h.read_raster(result['output_path'])
    src, src_info = h.read_raster(utm)
    assert info['gt'] == src_info['gt'] and edited.shape == src.shape
    assert 'UTM zone 10N' in info['wkt']
    # The line in DEM coordinates.
    to_dem = QgsCoordinateTransform(session.project_crs, QgsCoordinateReferenceSystem('EPSG:32610'),
                                    QgsProject.instance().transformContext())
    line = QgsGeometry(hillside); line.transform(to_dem)
    props = h.road_properties(edited, info['gt'], line, np.linspace(-3.0, 3.0, 5), 5.0, trim=30.0)
    grade = 100.0 * np.abs(np.diff(props['profile'])).max() / 5.0
    assert props['cross_range'].max() <= 0.10 + 5.0 * grade / 100.0 + 0.3


def test_geographic_crs_is_rejected(make_session, terrain, hillside, tmp_path):
    """A DEM in latitude and longitude (degrees) cannot give metre widths or
    grades. The plugin must refuse and say to reproject. No files remain."""
    geographic = str(tmp_path / 'wgs84.tif')
    gdal.Warp(geographic, terrain['path'], dstSRS='EPSG:4326', resampleAlg='bilinear', outputType=gdal.GDT_Float32)
    session = make_session(geographic, project_crs_wkt=terrain['info']['wkt'])
    before = folder_state(session)
    out = run_road(session, hillside, 'degrees')
    assert out.ok is False
    assert 'projected' in str(out.exception).lower()
    assert folder_state(session) == before


def test_projected_crs_in_feet_is_rejected(make_session, terrain, hillside, tmp_path):
    """A DEM in a projected CRS that counts in US survey feet. The plugin says
    metres are required. The user must reproject first."""
    feet = str(tmp_path / 'ca3_feet.tif')
    gdal.Warp(feet, terrain['path'], dstSRS='EPSG:2227', resampleAlg='bilinear', outputType=gdal.GDT_Float32)
    session = make_session(feet, project_crs_wkt=terrain['info']['wkt'])
    out = run_road(session, hillside, 'feet')
    assert out.ok is False
    assert 'meter' in str(out.exception).lower()


# --------------------------------------------------- (k) integer and float DEMs

@pytest.fixture
def int_dem(terrain, tmp_path):
    """The real terrain stored as whole metres (Int16), as many public DEMs are."""
    array = np.rint(terrain['array']).astype('int16')
    return h.write_raster(str(tmp_path / 'dem_int16.tif'), array, terrain['gt'], terrain['info']['wkt'])


def test_float_output_keeps_fractions_on_integer_dem(make_session, int_dem, hillside):
    """With the default floating-point output, an Int16 DEM gets a Float32
    result. Road cells keep fractions. Cells outside the edit keep their original whole numbers."""
    session = make_session(int_dem)
    out = run_road(session, hillside, 'int_to_float', output_dtype='float')
    assert out.ok, out.exception
    edited, info = h.read_raster(out.result['output_path'])
    src, _ = h.read_raster(int_dem)
    assert edited.dtype == np.float32
    changed = edited != src
    assert changed.sum() > 100
    assert (np.abs(edited[changed] - np.rint(edited[changed])) > 1e-3).mean() > 0.5
    assert np.array_equal(edited[~changed], src[~changed].astype('float32'))


def test_source_type_output_rounds_integer_dem(make_session, int_dem, hillside):
    """With 'keep source data type', an Int16 DEM stays Int16. Every edited
    value is a whole number, within half a metre of the floating-point answer."""
    session = make_session(int_dem)
    as_float = run_road(session, hillside, 'f', output_dtype='float')
    as_int = run_road(session, hillside, 'i', output_dtype='source')
    assert as_float.ok and as_int.ok, (as_float.exception, as_int.exception)
    f, _ = h.read_raster(as_float.result['output_path'])
    i, info = h.read_raster(as_int.result['output_path'])
    assert i.dtype == np.int16
    assert np.abs(i.astype(float) - f.astype(float)).max() <= 0.5 + 1e-6
    # NumPy rounding is the rule: nearest whole number.
    inside_changes = f != np.rint(f)
    assert inside_changes.any()
    assert np.array_equal(i[inside_changes], np.rint(f[inside_changes]).astype('int16')) or \
        np.abs(i[inside_changes].astype(float) - f[inside_changes]).max() <= 0.5 + 1e-6


def test_float_dem_keeps_fractions(session, terrain, hillside):
    """A Float32 DEM keeps its type and fractional road elevations when the user
    keeps the source type."""
    out = run_road(session, hillside, 'float_keep', output_dtype='source')
    assert out.ok, out.exception
    edited, _ = h.read_raster(out.result['output_path'])
    assert edited.dtype == np.float32
    changed = edited != terrain['array']
    assert (np.abs(edited[changed] - np.rint(edited[changed])) > 1e-3).mean() > 0.5


def test_platform_rounding_is_reported_for_integer_dem(make_session, int_dem, terrain, pad, tmp_path):  # noqa: F811
    """A builder types 100.4 m for a pad on an Int16 DEM and keeps the source
    type. The stored level is 100. The result and the report must say the
    rounding (-0.4 m) openly."""
    session = make_session(int_dem)
    src, _ = h.read_raster(int_dem)
    inner = h.cells_with_centres_inside(pad, terrain['gt'], src.shape)
    level = float(np.floor(src[inner].mean())) + 0.4
    result, edited = run_pad(session, pad, 'int_pad', flat_source='value', flat_value=level, output_dtype='source')
    assert edited.dtype == np.int16
    assert result['flat_output_elevation'] == round(level)
    assert result['flat_rounding_difference'] == pytest.approx(round(level) - level)
    text = open(result['report_path'], encoding='utf-8').read()
    assert 'changed the platform elevation by' in text


def test_platform_keeps_fraction_on_float_dem(session, terrain, pad):
    """The same typed level 100.4 on a Float32 DEM keeps its fraction (within Float32 precision)."""
    src = terrain['array']
    inner = h.cells_with_centres_inside(pad, terrain['gt'], src.shape)
    level = float(np.floor(src[inner].mean())) + 0.4
    result, edited = run_pad(session, pad, 'float_pad', flat_source='value', flat_value=level, output_dtype='float')
    shrunk = h.cells_with_centres_inside(pad, terrain['gt'], src.shape, shrink=terrain['pixel'])
    assert np.abs(edited[shrunk] - level).max() < 1e-4
    assert abs(result['flat_rounding_difference']) < 1e-4


# ------------------------------------------ (l) report and difference raster

def number(text, pattern):
    match = re.search(pattern, text)
    assert match, "Report line not found: " + pattern
    return match.groups()


def test_report_and_difference_match_the_edited_dem(session, terrain, hillside):
    """The user reads the report: how many cells were cut and filled, how much
    earth moves, and the deepest cut and highest fill. A NumPy recomputation
    from the source DEM and the edited DEM must give the same figures, and the
    difference raster must equal edited minus source."""
    out = run_road(session, hillside, 'report', distance=10.0)
    assert out.ok, out.exception
    result = out.result
    src = terrain['array']
    edited, _ = h.read_raster(result['output_path'])
    mine = h.recompute_cut_fill(src, edited, np.ones(src.shape, bool))
    cell_area = terrain['pixel'] ** 2
    data = result['report_data']
    assert data['cut_pixels'] == mine['cut_pixels'] and data['fill_pixels'] == mine['fill_pixels']
    assert data['cut_volume'] == pytest.approx(mine['cut_volume_per_area'] * cell_area, rel=1e-9)
    assert data['fill_volume'] == pytest.approx(mine['fill_volume_per_area'] * cell_area, rel=1e-9)
    assert result['max_cut'] == pytest.approx(mine['max_cut'], abs=1e-9)
    assert result['max_fill'] == pytest.approx(mine['max_fill'], abs=1e-9)
    # The text file says the same as the numbers.
    text = open(result['report_path'], encoding='utf-8').read()
    cut_cells, fill_cells = number(text, r'lowered ground at ([\d,]+) grid cells and raised ground at ([\d,]+)')
    assert int(cut_cells.replace(',', '')) == mine['cut_pixels']
    assert int(fill_cells.replace(',', '')) == mine['fill_pixels']
    cut_v, fill_v = number(text, r'Estimated cut: ([\d.]+); estimated fill: ([\d.]+)')
    assert float(cut_v) == pytest.approx(mine['cut_volume_per_area'] * cell_area, abs=0.051)
    assert float(fill_v) == pytest.approx(mine['fill_volume_per_area'] * cell_area, abs=0.051)
    deepest, highest = number(text, r'deepest calculated cut is ([\d.]+); the highest fill is ([\d.]+)')
    assert float(deepest) == pytest.approx(mine['max_cut'], abs=0.0051)
    assert float(highest) == pytest.approx(mine['max_fill'], abs=0.0051)
    # Difference raster: edited minus source, positive = fill.
    diff, diff_info = h.read_raster(result['difference_path'])
    assert diff_info['gt'] == terrain['gt'] and diff.shape == src.shape
    assert np.array_equal(diff, edited.astype(np.float32) - src.astype(np.float32))
    assert (diff[edited > src] > 0).all() and (diff[edited < src] < 0).all()
    # Sum of positive differences times cell area equals the reported fill volume.
    assert diff[diff > 1e-6].astype(float).sum() * cell_area == pytest.approx(data['fill_volume'], rel=1e-5)


def test_report_and_difference_with_nodata(make_session, terrain, hillside, tmp_path):
    """The same checks on a DEM with a NoData void. NoData cells are not counted
    as cut or fill, and the difference raster is empty (NaN) there."""
    array = terrain['array'].copy()
    mid = hillside.interpolate(hillside.length() / 2).asPoint()
    c, r = h.xy_to_pixel(terrain['gt'], mid.x(), mid.y())
    c, r = int(round(c)), int(round(r))
    hole = np.zeros(array.shape, bool)
    hole[r - 2:r + 3, c - 2:c + 3] = True
    array[hole] = -9999.0
    path = h.write_raster(str(tmp_path / 'void.tif'), array, terrain['gt'], terrain['info']['wkt'], nodata=-9999.0)
    session = make_session(path)
    out = run_road(session, hillside, 'void_report', distance=10.0)
    assert out.ok, out.exception
    result = out.result
    edited, _ = h.read_raster(result['output_path'])
    mine = h.recompute_cut_fill(array, edited, ~hole)
    data = result['report_data']
    assert data['cut_pixels'] == mine['cut_pixels'] and data['fill_pixels'] == mine['fill_pixels']
    diff, _ = h.read_raster(result['difference_path'])
    assert np.isnan(diff[hole]).all() and np.isfinite(diff[~hole]).all()
    assert np.array_equal(diff[~hole], edited[~hole].astype(np.float32) - array[~hole].astype(np.float32))
