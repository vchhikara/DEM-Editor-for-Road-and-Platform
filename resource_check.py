"""Conservative resource checks for the in-memory DEM implementation."""
import os
import math


def grid_geometry(geotransform):
    """Return physical pixel dimensions and whether pixel axes are orthogonal."""
    gt = geotransform
    x = math.hypot(gt[1], gt[4])
    y = math.hypot(gt[2], gt[5])
    if not math.isfinite(x) or not math.isfinite(y) or x <= 0 or y <= 0:
        raise ValueError('The DEM has invalid pixel geometry.')
    if abs(gt[1]*gt[5] - gt[2]*gt[4]) <= 1e-12*x*y:
        raise ValueError('The DEM pixel axes are degenerate.')
    cosine = abs((gt[1]*gt[2] + gt[4]*gt[5]) / (x*y))
    return x, y, cosine <= 1e-8


def warped_grid_size(width, height, geotransform, resolution):
    """Conservative north-up bounding grid for a rotated input raster."""
    grid_geometry(geotransform)
    if not math.isfinite(resolution) or resolution <= 0:
        raise ValueError('The working pixel size must be positive.')
    gt = geotransform
    extent_x = abs(width*gt[1]) + abs(height*gt[2])
    extent_y = abs(width*gt[4]) + abs(height*gt[5])
    return int(math.ceil(extent_x / resolution)) + 1, int(math.ceil(extent_y / resolution)) + 1


def raster_pixel_dimensions(layer):
    """Use the affine pixel vectors, including on rotated file-based rasters."""
    from osgeo import gdal
    try:
        dataset = gdal.Open(layer.source(), gdal.GA_ReadOnly)
    except RuntimeError as error:
        raise ValueError('The DEM could not be opened to read its pixel geometry.') from error
    if dataset is None:
        raise ValueError('The DEM could not be opened to read its pixel geometry.')
    try:
        x, y, _ = grid_geometry(dataset.GetGeoTransform())
        return x, y
    finally:
        dataset = None


def available_memory_bytes():
    if os.name == 'nt':
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                        ('total_phys', ctypes.c_ulonglong), ('avail_phys', ctypes.c_ulonglong),
                        ('total_page', ctypes.c_ulonglong), ('avail_page', ctypes.c_ulonglong),
                        ('total_virtual', ctypes.c_ulonglong), ('avail_virtual', ctypes.c_ulonglong),
                        ('avail_extended', ctypes.c_ulonglong)]
        value = MemoryStatus(); value.length = ctypes.sizeof(value)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            return int(value.avail_phys)
    try:
        with open('/proc/meminfo', encoding='ascii') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def assess_grid(width, height, bytes_per_cell=112):
    cells = int(width) * int(height)
    if cells <= 0:
        raise ValueError('The input DEM has invalid dimensions.')
    estimate = cells * bytes_per_cell
    available = available_memory_bytes()
    return cells, estimate, available
