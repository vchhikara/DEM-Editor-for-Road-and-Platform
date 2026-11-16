import logging
import os
import math
from datetime import datetime

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsTask,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon, QColor
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.gui import QgsRubberBand

from .buffer_dialog import BufferDialog
from .dem_editor import EditCancelled, edit_dem, _transform_to_dem, _outside_raster_area, _check_vertical_unit
from .map_tool import DirectionalBufferMapTool, build_buffer
from .road_output import write_corridor
from .centerline import polygon_centerline, sample_widths
from .edit_report import write_report
from .release_info import should_show, show_release_info
from .output_transaction import OutputTransaction, output_family

ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.png')


LOG = logging.getLogger(__name__)


class DemRoadBufferEditor:
    """QGIS plugin entry point for corridor-based DEM editing."""

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.help_action = None
        self.map_tool = None
        self.task = None
        self.digitize_settings = None
        self.dem_path = None
        self.source_dem_path = None
        self.settings_dialog = None
        self._unloaded = False

    def tr(self, message):
        return QCoreApplication.translate('DemRoadBufferEditor', message)

    def initGui(self):
        self.action = QAction(
            QIcon(ICON_PATH),
            self.tr('DEM Editor for Roads and Platforms'),
            self.iface.mainWindow(),
        )
        self.action.setObjectName('demRoadBufferEditorAction')
        self.action.setToolTip(self.tr('Edit a DEM inside a digitized or selected road corridor'))
        self.action.setStatusTip(
            self.tr('Flatten a road corridor in a DEM, following the terrain along the road')
        )
        self.action.triggered.connect(self.start)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(self.tr('&DEM Editor for Roads and Platforms'), self.action)
        self.help_action = QAction(self.tr("What's new and user guide"), self.iface.mainWindow())
        self.help_action.triggered.connect(self.show_help)
        self.iface.addPluginToMenu(self.tr('&DEM Editor for Roads and Platforms'), self.help_action)

    def show_help(self):
        show_release_info(self.iface.mainWindow(), mark_as_seen=False)

    def unload(self):
        # A cancelled background task may still deliver its completion callback
        # after the plugin's actions and interface are being torn down.
        self._unloaded = True
        if self.help_action:
            self.iface.removePluginMenu(self.tr('&DEM Editor for Roads and Platforms'), self.help_action)
            self.help_action = None
        if self.action:
            self.iface.removePluginMenu(self.tr('&DEM Editor for Roads and Platforms'), self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.task is not None:
            self.task.cancel()
            self.task = None
        if self.settings_dialog is not None:
            self.settings_dialog.close()
            self.settings_dialog = None
        self._clear_map_tool()

    def start(self):
        if self.map_tool is not None and self.map_tool.points:
            self.iface.mapCanvas().setMapTool(self.map_tool)
            self.map_tool.refresh_preview()
            self.iface.messageBar().pushInfo('DEM Editor for Roads and Platforms', 'Resumed the unfinished line from its last vertex.')
            return
        if should_show():
            show_release_info(self.iface.mainWindow(), mark_as_seen=True)
        crs = QgsProject.instance().crs()
        if not crs.isValid() or crs.isGeographic() or crs.mapUnits() != Qgis.DistanceUnit.Meters:
            QMessageBox.warning(
                self.iface.mainWindow(),
                self.tr('Projected meter-based CRS required'),
                self.tr(
                    'Set the QGIS project to a projected CRS whose map units are meters '
                    'before using this tool.'
                ),
            )
            return

        if self.settings_dialog is not None:
            self.settings_dialog.showNormal()
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()
            return
        active = self.iface.activeLayer()
        dialog = BufferDialog(
            self.iface.mainWindow(), active.id() if active is not None else None
        )
        self.settings_dialog = dialog
        dialog.accepted.connect(lambda: self._settings_accepted(dialog))
        dialog.rejected.connect(lambda: setattr(self, 'settings_dialog', None))
        dialog.setModal(False)
        dialog.show()

    def _settings_accepted(self, dialog):
        settings = dialog.settings()
        self.settings_dialog = None
        dem_layer = QgsProject.instance().mapLayer(settings['dem_layer_id'])
        if not isinstance(dem_layer, QgsRasterLayer) or not dem_layer.isValid():
            self._warn('Choose a valid DEM raster layer.')
            return
        dem_path = dem_layer.source()
        if not dem_path or not os.path.isfile(dem_path):
            QMessageBox.warning(
                self.iface.mainWindow(),
                self.tr('File-based DEM required'),
                self.tr(
                    'The selected DEM is not a file-based raster. Load a GeoTIFF '
                    '(or another GDAL raster file) and try again.'
                ),
            )
            return

        if not dem_layer.crs().isValid() or dem_layer.crs().isGeographic() or dem_layer.crs().mapUnits() != Qgis.DistanceUnit.Meters:
            self._warn('Reproject the DEM to a projected CRS with meter units before editing.')
            return

        self.dem_path = dem_path
        self.source_dem_path = dem_path
        if settings['source'] == 'polygon':
            self._start_from_polygon(settings)
        elif settings['source'] == 'line':
            self._start_from_line(settings)
        else:
            self._start_digitizing(settings)

    def _start_from_line(self, settings):
        self._clear_map_tool()
        layer = QgsProject.instance().mapLayer(settings['line_layer_id'])
        if not isinstance(layer, QgsVectorLayer) or layer.selectedFeatureCount() != 1:
            self._warn('Select exactly one line feature.')
            return
        line = QgsGeometry(layer.selectedFeatures()[0].geometry())
        if line.isEmpty() or line.isMultipart():
            self._warn('Select a single, continuous line geometry.')
            return
        if layer.crs() != QgsProject.instance().crs():
            line.transform(QgsCoordinateTransform(layer.crs(), QgsProject.instance().crs(), QgsProject.instance().transformContext()))
        corridor = build_buffer(line, settings['distance'], settings['side'])
        if self._review_geometry(corridor, line, settings):
            self._run_edit(settings, corridor, line)

    def _clear_map_tool(self):
        if self.map_tool is None:
            return
        canvas = self.iface.mapCanvas()
        if canvas.mapTool() is self.map_tool:
            canvas.unsetMapTool(self.map_tool)
        # unsetMapTool deactivates the active tool; an inactive tool was
        # already deactivated when another map tool took over.
        self.map_tool.reset_drawing()
        # Rubber bands are canvas-scene items and can outlive their Python
        # map-tool wrapper. Remove both so abandoned previews do not recur or
        # accumulate when changing corridor sources or unloading the plugin.
        for band in (self.map_tool.center_band, self.map_tool.buffer_band):
            canvas.scene().removeItem(band)
        self.map_tool = None

    def _start_from_polygon(self, settings):
        self._clear_map_tool()
        layer = QgsProject.instance().mapLayer(settings['polygon_layer_id'])
        if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            self._warn('Choose a valid polygon layer.')
            return
        source_features = (layer.getFeatures() if settings.get('polygon_scope') == 'all'
                           else layer.selectedFeatures())
        features = [
            feature
            for feature in source_features
            if not feature.geometry().isEmpty()
        ]
        if not features:
            self._warn('No usable polygon features were found in the chosen layer and scope.')
            return

        project_crs = QgsProject.instance().crs()
        layer_crs = layer.crs()
        transform = None
        if layer_crs.isValid() and layer_crs != project_crs:
            transform = QgsCoordinateTransform(
                layer_crs, project_crs, QgsProject.instance().transformContext()
            )

        geometries = []
        for feature in features:
            geometry = QgsGeometry(feature.geometry())
            if transform is not None:
                geometry.transform(transform)
            geometries.append(geometry)
        corridor = QgsGeometry.unaryUnion(geometries)
        if corridor is None or corridor.isEmpty():
            self._warn('The selected polygons could not be combined into a corridor.')
            return
        corridor = self._make_valid(corridor)
        line = None
        if settings['mode'] == 'road':
            try:
                dem = QgsProject.instance().mapLayer(settings['dem_layer_id'])
                from .resource_check import raster_pixel_dimensions
                pixel = max(raster_pixel_dimensions(dem))
                if settings.get('resample'):
                    pixel = float(settings['target_resolution'])
                line, ambiguous = polygon_centerline(corridor, pixel, project_crs.toWkt())
                widths = sample_widths(corridor, line)
                if widths:
                    minimum = min(widths)
                    self.iface.messageBar().pushInfo('DEM Editor for Roads and Platforms',
                        'Approximate narrowest sampled polygon width {:.2f} m ({:.1f} working-grid pixels); fewer than 3 cells is coarse, 5 is a review guideline.'.format(minimum, minimum/pixel))
                if ambiguous:
                    settings = dict(settings, polygon_centerline_ambiguous=True)
                    self.iface.messageBar().pushWarning('DEM Editor for Roads and Platforms', 'The polygon has branches, holes or competing routes. Carefully review the suggested centerline.')
            except Exception as error:
                self._warn('Could not derive a centerline: {}'.format(error))
                return
        if self._review_geometry(corridor, line, settings):
            self._run_edit(settings, corridor, line)

    def _review_geometry(self, corridor, line, settings):
        canvas = self.iface.mapCanvas()
        bands = []
        for geometry, kind, color in ((corridor, QgsWkbTypes.GeometryType.PolygonGeometry, QColor(10, 140, 210, 150)),
                                      (line, QgsWkbTypes.GeometryType.LineGeometry, QColor(230, 50, 40, 220))):
            if geometry is not None and not geometry.isEmpty():
                band = QgsRubberBand(canvas, kind); band.setColor(color); band.setWidth(2); band.setToGeometry(geometry, None); bands.append(band)
        cautions = []
        if settings.get('mode') == 'road' and settings.get('profile_method', 'terrain') == 'terrain':
            cautions.append('Terrain following does not apply a maximum grade. The road may be steep; use a grade-controlled profile if a limit is needed and review the output.')
        if settings.get('source') == 'polygon' and settings.get('mode') == 'road':
            cautions.append('The suggested polygon centerline may be smoothed inside the polygon. Inspect the full route before editing.')
        if settings.get('polygon_centerline_ambiguous'):
            cautions.append('The polygon has branches, holes or competing routes. Only one suggested route determines the road profile across the whole polygon. Confirm that it is appropriate before editing.')
        dem_for_coverage = QgsProject.instance().mapLayer(settings.get('dem_layer_id'))
        if isinstance(dem_for_coverage, QgsRasterLayer):
            try:
                from osgeo import gdal
                from .crs_check import inspect_crs, crs_cautions
                coverage_ds = gdal.Open(dem_for_coverage.source(), gdal.GA_ReadOnly)
                if coverage_ds is None:
                    raise ValueError('DEM could not be opened')
                coverage_geom = _transform_to_dem(corridor.asWkt(), QgsProject.instance().crs(),
                                                  dem_for_coverage.crs(), QgsProject.instance().transformContext())
                context = QgsProject.instance().transformContext()
                project_crs = QgsProject.instance().crs()
                if project_crs == dem_for_coverage.crs():
                    cautions.extend(crs_cautions('Project and DEM CRS', inspect_crs(project_crs, corridor.pointOnSurface().asPoint(), context)))
                else:
                    cautions.extend(crs_cautions('Project CRS', inspect_crs(project_crs, corridor.pointOnSurface().asPoint(), context)))
                    cautions.extend(crs_cautions('DEM CRS', inspect_crs(dem_for_coverage.crs(), coverage_geom.pointOnSurface().asPoint(), context)))
                outside = _outside_raster_area(coverage_ds, coverage_geom)
                transition = float(settings.get('feather_m', 0.0))
                outside_transition = _outside_raster_area(coverage_ds, coverage_geom, transition) if transition > 0 else 0.0
                coverage_ds = None
                if outside > 0:
                    cautions.append('Approximately {:.1f} square metres of the corridor are outside the source DEM and cannot be edited.'.format(outside))
                if outside_transition > 0:
                    cautions.append('The corridor or transition envelope extends beyond the source DEM. Review the edge and consider a larger DEM.')
            except Exception:
                cautions.append('Could not check the corridor against the source DEM extent; verify coverage before editing.')
        if line is not None and not line.isEmpty():
            vertices = list(line.vertices())
            sharp = []
            for first, point, last in zip(vertices, vertices[1:], vertices[2:]):
                a = (point.x()-first.x(), point.y()-first.y())
                b = (last.x()-point.x(), last.y()-point.y())
                lengths = math.hypot(*a)*math.hypot(*b)
                if lengths and math.degrees(math.acos(max(-1., min(1., (a[0]*b[0]+a[1]*b[1])/lengths)))) >= 45:
                    sharp.append(point)
            if sharp:
                cautions.append('{} sharp line vertices (45 degrees or more) are marked red. Their profiles may be locally levelled, increasing cut or fill. Check road width and cross-sections.'.format(len(sharp)))
                for point in sharp:
                    band = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PointGeometry)
                    band.setColor(QColor(240, 30, 30, 255)); band.setWidth(8)
                    band.setToGeometry(QgsGeometry.fromPointXY(QgsPointXY(point)), None); bands.append(band)
            dem = QgsProject.instance().mapLayer(settings.get('dem_layer_id'))
            if isinstance(dem, QgsRasterLayer) and line.length() > 0:
                transform = QgsCoordinateTransform(QgsProject.instance().crs(), dem.crs(), QgsProject.instance().transformContext())
                last_value = None; max_ground_grade = 0.0
                step = line.length()/100.0
                for i in range(101):
                    try:
                        p = line.interpolate(i*step).asPoint()
                        point = transform.transform(QgsPointXY(p))
                        value, ok = dem.dataProvider().sample(point, 1)
                    except Exception:
                        last_value = None
                        continue
                    if ok and math.isfinite(value):
                        if last_value is not None:
                            max_ground_grade = max(max_ground_grade, abs(value-last_value)/step*100.)
                        last_value = value
                    else:
                        last_value = None
                if settings.get('profile_method') in ('grade', 'balanced') and max_ground_grade > settings.get('maximum_grade', 10.):
                    cautions.append('Sampled ground grade reaches approximately {:.1f}%, above the requested road grade of {:.1f}%. Substantial cut or fill may be required.'.format(max_ground_grade, settings.get('maximum_grade',10.)))
        if line is not None and not line.isEmpty():
            cautions.append('Other curves with conflicting cross-sections may also be locally levelled. Review the final report and earthworks.')
        canvas.refresh()
        try:
            note = '\n\nTAKE CARE:\n' + '\n'.join(cautions) if cautions else ''
            return QMessageBox.question(self.iface.mainWindow(), 'Review road corridor',
                'Review the displayed corridor and centerline. Apply the DEM edit?' + note,
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Ok
        finally:
            for band in bands: canvas.scene().removeItem(band)
            canvas.refresh()

    def _start_digitizing(self, settings):
        self._clear_map_tool()
        canvas = self.iface.mapCanvas()
        self.digitize_settings = settings
        self.map_tool = DirectionalBufferMapTool(
            canvas,
            settings['distance'],
            settings['side'],
            self._centerline_finished,
        )
        canvas.setMapTool(self.map_tool)
        self.iface.messageBar().pushInfo(
            'DEM Editor for Roads and Platforms',
            'Click vertices; Backspace or Ctrl+Z removes the last. Right-click for Finish or Clear.',
        )

    def _centerline_finished(self, line):
        settings = self.digitize_settings
        if settings is None:
            return False
        corridor = build_buffer(line, settings['distance'], settings['side'])
        if corridor is None or corridor.isEmpty():
            self._warn('The buffer corridor could not be built from that centerline.')
            return False
        corridor = self._make_valid(corridor)
        if not self._review_geometry(corridor, line, settings):
            return False
        return self._run_edit(settings, corridor, line)

    def _run_edit(self, settings, corridor, line):
        if self.task is not None:
            self._warn('A DEM edit is already running. Wait for it to finish.')
            return False

        dem_path = self.dem_path
        raster_path = self._unique_raster_path(settings['raster_path'])
        shapefile_path = self._unique_shapefile_path(settings['shapefile_path'])
        settings = dict(settings)
        settings['report_path'] = self._unique_raster_path(os.path.splitext(raster_path)[0] + '_report.txt')
        if settings.get('diagnostics') and settings.get('mode') == 'road':
            settings['diagnostics_path'] = self._unique_shapefile_path(os.path.splitext(shapefile_path)[0] + '_samples.shp')
        if settings.get('difference'):
            settings['difference_path'] = self._unique_raster_path(os.path.splitext(raster_path)[0] + '_difference.tif')
        centerline_path = self._unique_shapefile_path(os.path.splitext(shapefile_path)[0] + '_centerline.shp') if line is not None else None
        working_path = None
        if settings.get('resample'):
            from osgeo import gdal
            from .resource_check import grid_geometry, warped_grid_size
            ds = gdal.Open(dem_path)
            if ds is None:
                self._warn('Could not inspect the DEM for interpolation.')
                return False
            gt = ds.GetGeoTransform()
            resolution = float(settings['target_resolution'])
            try:
                x_pixel, y_pixel, _ = grid_geometry(gt)
                w, h = warped_grid_size(ds.RasterXSize, ds.RasterYSize, gt, resolution)
            except ValueError as error:
                self._warn(str(error))
                return False
            if resolution >= max(x_pixel, y_pixel):
                self._warn('The working pixel size must be finer than the source DEM.')
                return False
            ds = None
            if QMessageBox.question(self.iface.mainWindow(), 'Create interpolated DEM',
                'Create a {:.2f} m working grid (approximately {} x {} pixels; {:.1f} MiB uncompressed Float64)?\n\nInterpolation cannot restore missing terrain detail.'.format(resolution, w, h, w*h*8/1048576.0),
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Ok:
                return False
            working_path = self._unique_raster_path(os.path.splitext(raster_path)[0] + '_working.tif')
        if os.path.normcase(os.path.abspath(dem_path)) == os.path.normcase(
            os.path.abspath(raster_path)
        ):
            self._warn('The output GeoTIFF must differ from the source DEM.')
            return False

        attributes = {
            'mode': 'Road' if settings['mode'] == 'road' else 'Flat',
            'dist_m': float(settings['distance']) if settings['source'] in ('digitize', 'line') else 0.0,
            'side': settings['side'] if settings['source'] in ('digitize', 'line') else '',
            'stat': self._statistic_label(settings),
            'source': {'digitize': 'digitized', 'line': 'existing line', 'polygon': 'existing polygon'}[settings['source']],
            'dem_path': dem_path,
            'edit_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

        task = QgsTask.fromFunction(
            'DEM road and platform edit',
            self._run_task,
            on_finished=self._task_finished,
            flags=QgsTask.Flag.CanCancel,
            dem_path=dem_path,
            working_path=working_path,
            project_crs_wkt=QgsProject.instance().crs().toWkt(),
            corridor_wkt=corridor.asWkt(),
            line_wkt=line.asWkt() if line is not None else None,
            settings=dict(settings),
            output_path=raster_path,
            shapefile_path=shapefile_path,
            centerline_path=centerline_path,
            attributes=attributes,
            transform_context=QgsProject.instance().transformContext(),
        )
        self.task = task
        QgsApplication.taskManager().addTask(task)
        self.iface.messageBar().pushInfo(
            'DEM Editor for Roads and Platforms', 'Editing the DEM corridor in the background...'
        )
        return True

    @staticmethod
    def _statistic_label(settings):
        if settings['mode'] != 'flat':
            return 'terrain'
        if settings['flat_source'] == 'edge':
            return settings['statistic']
        return 'value'

    def _run_task(self, task, **kwargs):
        """Stage the complete result before publishing any user-visible files."""
        final = dict(kwargs)
        final_settings = dict(kwargs['settings'])
        with OutputTransaction(kwargs['output_path']) as transaction:
            staged = dict(kwargs)
            staged_settings = dict(final_settings)
            for key in ('output_path', 'shapefile_path', 'centerline_path', 'working_path'):
                if final.get(key):
                    staged[key] = transaction.stage(final[key])
            for key in ('report_path', 'difference_path', 'diagnostics_path'):
                if final_settings.get(key):
                    staged_settings[key] = transaction.stage(final_settings[key])
            staged['settings'] = staged_settings
            staged['display_paths'] = {
                'dem': final['output_path'],
                'corridor': final['shapefile_path'],
                'centerline': final.get('centerline_path'),
                'difference': final_settings.get('difference_path'),
                'working_dem': final.get('working_path'),
                'sample_points': final_settings.get('diagnostics_path'),
            }
            result = self._run_staged_task(task, **staged)
            if task.isCanceled():
                raise EditCancelled()
            try:
                transaction.publish(is_cancelled=task.isCanceled)
            except InterruptedError:
                raise EditCancelled()
            for key in ('output_path', 'shapefile_path', 'centerline_path', 'working_path'):
                if final.get(key):
                    result[key] = final[key]
            for key in ('report_path', 'difference_path', 'diagnostics_path'):
                if final_settings.get(key):
                    result[key] = final_settings[key]
            return result

    def _run_staged_task(self, task, **kwargs):
        def progress(percent):
            task.setProgress(float(percent))
            return not task.isCanceled()

        dem_path = kwargs['dem_path']
        working_path = kwargs.get('working_path')
        from osgeo import gdal
        from .resource_check import assess_grid, grid_geometry, warped_grid_size
        source = gdal.Open(dem_path, gdal.GA_ReadOnly)
        if source is None or source.RasterCount != 1:
            raise ValueError('A readable single-band DEM is required.')
        band = source.GetRasterBand(1)
        _check_vertical_unit(band.GetUnitType())
        if gdal.DataTypeIsComplex(band.DataType):
            raise ValueError('Complex-valued rasters are not supported as DEM elevations. Convert to a suitable real-valued DEM first.')
        unsafe_types = tuple(getattr(gdal, name) for name in ('GDT_Int64', 'GDT_UInt64') if hasattr(gdal, name))
        if band.DataType in unsafe_types:
            raise ValueError('This 64-bit integer DEM cannot be safely processed with floating-point elevation calculations. Convert it to a suitable DEM first.')
        scale, offset = band.GetScale(), band.GetOffset()
        if (scale is not None and scale != 1.0) or (offset is not None and offset != 0.0):
            raise ValueError('Scaled or offset DEMs must be converted to physical elevations before editing.')
        source_nodata = band.GetNoDataValue()
        width, height = source.RasterXSize, source.RasterYSize
        gt = source.GetGeoTransform()
        _, _, orthogonal = grid_geometry(gt)
        if not orthogonal and not working_path and float(kwargs['settings'].get('feather_m', 0.0)) > 0:
            raise ValueError('Cut/fill transitions require orthogonal DEM pixels. Resample the skewed DEM to a north-up grid first.')
        if working_path:
            resolution = float(kwargs['settings']['target_resolution'])
            width, height = warped_grid_size(width, height, gt, resolution)
        band = None; source = None
        cells, estimate, available = assess_grid(width, height)
        if available is not None and estimate > available * 0.7:
            raise MemoryError('This edit may exceed available memory (estimated {:.1f} GiB; {:.1f} GiB available). Crop the DEM or use a coarser working grid.'.format(estimate / 2**30, available / 2**30))
        if working_path:
            resolution = float(kwargs['settings']['target_resolution'])
            # An explicit destination NoData is essential: a masked source
            # without a NoData value otherwise becomes valid zero on warp.
            target_nodata = source_nodata if source_nodata is not None else -1.7976931348623157e308
            output = gdal.Warp(working_path, dem_path, format='GTiff', xRes=resolution, yRes=resolution,
                               resampleAlg='bilinear', outputType=gdal.GDT_Float64, dstNodata=target_nodata,
                               creationOptions=['TILED=YES', 'COMPRESS=LZW'],
                               callback=lambda complete, msg, data: 0 if task.isCanceled() else 1)
            if output is None:
                if task.isCanceled(): raise EditCancelled()
                raise RuntimeError('Could not create the interpolated working DEM.')
            output = None
            dem_path = working_path
        try:
            result = edit_dem(
                dem_path,
                kwargs['project_crs_wkt'],
                kwargs['corridor_wkt'],
                kwargs['line_wkt'],
                kwargs['settings'],
                kwargs['output_path'],
                progress=progress,
                transform_context=kwargs.get('transform_context'),
            )
        except EditCancelled:
            try:
                os.remove(kwargs['output_path'])
            except OSError:
                LOG.debug('Could not remove the unfinished output file.', exc_info=True)
            raise
        if task.isCanceled():
            try:
                os.remove(result['output_path'])
            except OSError:
                LOG.debug('Could not remove the unfinished output file.', exc_info=True)
            raise EditCancelled()
        try:
            write_corridor(
                kwargs['shapefile_path'],
                result['corridor_wkt'],
                result['dem_crs_wkt'],
                kwargs['attributes'],
            )
        except Exception:
            try:
                os.remove(result['output_path'])
            except OSError:
                LOG.debug('Could not remove the unfinished output file.', exc_info=True)
            raise
        result['shapefile_path'] = kwargs['shapefile_path']
        result['working_path'] = working_path
        if kwargs.get('centerline_path'):
            from osgeo import ogr, osr
            from qgis.core import QgsCoordinateReferenceSystem
            project_crs = QgsCoordinateReferenceSystem(); project_crs.createFromWkt(kwargs['project_crs_wkt'])
            dem_crs = QgsCoordinateReferenceSystem(); dem_crs.createFromWkt(result['dem_crs_wkt'])
            line = _transform_to_dem(kwargs['line_wkt'], project_crs, dem_crs, kwargs.get('transform_context'))
            srs = osr.SpatialReference(); srs.ImportFromWkt(result['dem_crs_wkt'])
            ds = ogr.GetDriverByName('ESRI Shapefile').CreateDataSource(kwargs['centerline_path'])
            if ds is None: raise RuntimeError('Could not save the centerline.')
            lyr = ds.CreateLayer('centerline', srs, ogr.wkbLineString)
            feat = ogr.Feature(lyr.GetLayerDefn()); feat.SetGeometry(ogr.CreateGeometryFromWkt(line.asWkt()))
            if lyr.CreateFeature(feat) != 0: raise RuntimeError('Could not save the centerline feature.')
            feat = None; lyr = None; ds = None
            result['centerline_path'] = kwargs['centerline_path']
        report_path = kwargs['settings'].get('report_path')
        if report_path:
            display = kwargs.get('display_paths') or {
                'dem':result['output_path'], 'corridor':result['shapefile_path'],
                'centerline':result.get('centerline_path'), 'difference':result.get('difference_path'),
                'working_dem':working_path, 'sample_points':result.get('diagnostics_path')}
            baseline = display.get('working_dem') or kwargs['dem_path']
            write_report(report_path, result, kwargs['settings'], kwargs['dem_path'], baseline, display)
            result['report_path'] = report_path
        return result

    def _task_finished(self, exception, result):
        self.task = None
        if self._unloaded:
            return
        if exception is not None:
            if isinstance(exception, EditCancelled):
                self.iface.messageBar().pushInfo(
                    'DEM Editor for Roads and Platforms', 'The DEM edit was cancelled.'
                )
            else:
                message = str(exception) or exception.__class__.__name__
                QMessageBox.critical(
                    self.iface.mainWindow(),
                    self.tr('DEM edit failed'),
                    self.tr('The DEM edit could not be completed:\n\n{}').format(message),
                )
            return

        self._load_raster(result['output_path'])
        self._load_shapefile(result['shapefile_path'])
        if result.get('centerline_path'):
            self._load_shapefile(result['centerline_path'])
        if result.get('diagnostics_path'):
            self._load_shapefile(result['diagnostics_path'])
        if result.get('difference_path'):
            self._load_raster(result['difference_path'])
        self.dem_path = result['output_path']
        self.iface.mapCanvas().refresh()

        if result['mode'] == 'road':
            detail = 'terrain-following road'
        elif result['applied_value'] is not None:
            detail = 'flat at {:.3f}'.format(result.get('flat_output_elevation', result['applied_value']))
        else:
            detail = 'flat corridor'
        message = ('{} corridor pixels, {} transition pixels; {} elevation values changed; '
                   '{} nodata pixels skipped ({}). Max cut {:.2f}, fill {:.2f} (DEM elevation units).').format(
            result['edited_pixels'], result['transition_pixels'], result['changed_pixels'],
            result['skipped_nodata_pixels'], detail, result['max_cut'], result['max_fill']
        )
        if result.get('max_grade') is not None:
            message += ' Calculated profile grade {:.2f}%.'.format(result['max_grade'])
        if result.get('fitted_grade_limit') is not None:
            message += ' Profile fitting limit {:.2f}%; see report for requested limit.'.format(result['fitted_grade_limit'])
        if result.get('raster_max_grade') is not None:
            message += ' Sampled output-raster grade {:.2f}% ({} intervals).'.format(result['raster_max_grade'], result.get('raster_grade_sample_count', 0))
        if result.get('raster_crossfall_max') is not None:
            message += ' Maximum sampled cross-road difference {:.2f} elevation units.'.format(result['raster_crossfall_max'])
        if result.get('turn_plateau_ranges'):
            message += ' {} bend sections were locally levelled; review the report and earthworks.'.format(len(result['turn_plateau_ranges']))
        if result.get('skipped_nodata_pixels', 0):
            message += ' WARNING: missing DEM cells within the corridor could not be edited.'
        if result.get('unmet_transition_pixels', 0):
            message += ' WARNING: {} edge pixels have not met the original terrain; increase transition width or adjust slopes.'.format(result['unmet_transition_pixels'])
            if result.get('max_unmet_transition_delta') is not None:
                message += ' Largest sampled edge difference {:.2f} DEM elevation units.'.format(result['max_unmet_transition_delta'])
            if result.get('estimated_tie_distance') is not None:
                message += ' Estimated distance to sampled edge elevation {:.1f} m assuming level ground beyond the edge; actual terrain may differ.'.format(result['estimated_tie_distance'])
        if result.get('transition_unrepresented'):
            message += ' WARNING: no valid outside transition cells were sampled; check grid resolution and DEM coverage.'
        if result.get('transition_edge_unchecked'):
            message += ' WARNING: no valid outer transition edge cells were available for the tie-in check; inspect DEM coverage and NoData.'
        if result.get('outside_dem_area', 0) > 0:
            message += ' WARNING: {:.1f} square metres of the corridor are outside the DEM and were not edited.'.format(result['outside_dem_area'])
        if result.get('transition_outside_dem_area', 0) > 0:
            message += ' WARNING: part of the corridor or its transition extends outside the DEM.'
        if result.get('transition_nodata_pixels', 0):
            message += ' WARNING: {} transition-area NoData pixels were left unchanged.'.format(result['transition_nodata_pixels'])
        from .crs_check import crs_cautions
        crs_warnings = []
        for label, key in (('Project CRS', 'project_crs_review'), ('DEM CRS', 'dem_crs_review')):
            if isinstance(result.get(key), dict):
                crs_warnings.extend(crs_cautions(label, result[key]))
        if crs_warnings:
            message += ' WARNING: ' + ' '.join(crs_warnings)
        large_earthwork = max(result['max_cut'], result['max_fill']) > 5.0
        if large_earthwork:
            message += ' WARNING: a cut or fill exceeds 5 DEM elevation units. Review the earthwork; this is a review prompt, not an engineering limit.'
        if result.get('ribbon_gap_pixels', 0):
            message += ' WARNING: {} road pixels used a nearest-line fallback; inspect bends and ends.'.format(result['ribbon_gap_pixels'])
            if result.get('ribbon_endpoint_pixels', 0):
                message += ' {} fallback pixels project to a centerline endpoint.'.format(result['ribbon_endpoint_pixels'])
        platform_rounding = (result['mode'] == 'flat' and
                             abs(result.get('flat_rounding_difference') or 0.0) >= 0.0005)
        if platform_rounding:
            message += ' WARNING: the output pixel type changed the platform elevation by {:.3f} DEM elevation units.'.format(result['flat_rounding_difference'])
        if result.get('raster_grade_exceedances', 0):
            message += ' WARNING: {} sampled output-grade intervals exceeded the requested limit.'.format(result['raster_grade_exceedances'])
        grade_incomplete = (result['mode'] == 'road' and result.get('raster_grade_attempts') is not None
                            and result.get('raster_grade_sample_count', 0) < result['raster_grade_attempts'])
        if grade_incomplete:
            message += ' WARNING: output grade sampling was incomplete.'
        if result.get('raster_crossfall_exceedances', 0):
            message += ' WARNING: {} sampled cross-sections exceeded the review threshold.'.format(result['raster_crossfall_exceedances'])
        validation_missing = (result['mode'] == 'road' and
                              (result.get('raster_max_grade') is None or result.get('raster_crossfall_max') is None))
        if validation_missing:
            message += ' WARNING: saved-raster grade or cross-section validation was unavailable.'
        if result['mode'] == 'road' and result.get('raster_crossfall_samples', 0) < result.get('raster_crossfall_attempts', 0):
            message += ' WARNING: output cross-section sampling was incomplete.'
        if result.get('report_path'):
            message += ' Report: {}'.format(result['report_path'])
        if (result['changed_pixels'] == 0 or result.get('unmet_transition_pixels', 0) or result.get('transition_unrepresented') or result.get('transition_edge_unchecked') or result.get('skipped_nodata_pixels', 0)
                or result.get('outside_dem_area', 0) > 0 or result.get('transition_outside_dem_area', 0) > 0 or result.get('transition_nodata_pixels', 0)
                or result.get('ribbon_gap_pixels', 0) or result.get('raster_grade_exceedances', 0)
                or result.get('raster_crossfall_exceedances', 0) or result.get('turn_plateau_ranges') or validation_missing or grade_incomplete or platform_rounding or large_earthwork or crs_warnings
                or (result['mode'] == 'road' and result.get('raster_crossfall_samples', 0) < result.get('raster_crossfall_attempts', 0))):
            if result['changed_pixels'] == 0:
                message += ' No values changed; check DEM resolution and settings.'
            self.iface.messageBar().pushWarning('DEM Editor for Roads and Platforms', message)
            # The QGIS message bar can be too small or disappear before a
            # long quality warning is read. Require acknowledgement while
            # retaining the detailed text report for later review.
            QMessageBox.warning(
                self.iface.mainWindow(),
                self.tr('DEM output needs review'),
                self.tr('The output was created, but one or more results need review.\n\n{}\n\n'
                        'Inspect the output and the text report before using it.').format(message),
            )
        else:
            self.iface.messageBar().pushSuccess('DEM Editor for Roads and Platforms', message)

    def _load_raster(self, path):
        existing = self._find_loaded_layer(path)
        if existing is not None:
            existing.reload()
            existing.triggerRepaint()
            return existing
        name = os.path.splitext(os.path.basename(path))[0]
        layer = QgsRasterLayer(path, name, 'gdal')
        if not layer.isValid():
            self._warn('The edited DEM was written but could not be loaded as a layer.')
            return None
        QgsProject.instance().addMapLayer(layer)
        return layer

    def _load_shapefile(self, path):
        existing = self._find_loaded_layer(path)
        if existing is not None:
            existing.reload()
            existing.triggerRepaint()
            return existing
        name = os.path.splitext(os.path.basename(path))[0]
        layer = QgsVectorLayer(path, name, 'ogr')
        if not layer.isValid():
            self._warn('The corridor shapefile was written but could not be loaded as a layer.')
            return None
        QgsProject.instance().addMapLayer(layer)
        return layer

    def _unique_raster_path(self, requested):
        root, extension = os.path.splitext(requested)
        if not extension:
            extension = '.tif'
        candidate = requested
        index = 2
        while (
            any(os.path.exists(path) for path in output_family(candidate))
            or self._find_loaded_layer(candidate) is not None
            or self._same_path(candidate, self.source_dem_path)
            or self._same_path(candidate, self.dem_path)
        ):
            candidate = '{}_{}{}'.format(root, index, extension)
            index += 1
        return candidate

    def _unique_shapefile_path(self, requested):
        root, extension = os.path.splitext(requested)
        extension = extension or '.shp'
        candidate = root + extension
        index = 2
        # Check all standard shapefile components to avoid mixing datasets or
        # replacing an existing layer, even if its .shp is missing.
        while (any(os.path.exists(path) for path in output_family(candidate))
               or self._find_loaded_layer(candidate) is not None):
            candidate = '{}_{}{}'.format(root, index, extension)
            index += 1
        return candidate

    @staticmethod
    def _same_path(first, second):
        if not first or not second:
            return False
        return os.path.normcase(os.path.abspath(first)) == os.path.normcase(
            os.path.abspath(second)
        )

    @staticmethod
    def _find_loaded_layer(path):
        target = os.path.normcase(os.path.abspath(path))
        for layer in QgsProject.instance().mapLayers().values():
            source = layer.source().split('|')[0]
            if source and os.path.normcase(os.path.abspath(source)) == target:
                return layer
        return None

    @staticmethod
    def _make_valid(geometry):
        if geometry is None or geometry.isEmpty():
            return geometry
        if geometry.isGeosValid():
            return geometry
        fixed = geometry.makeValid()
        if isinstance(fixed, tuple):
            fixed = fixed[0]
        return fixed if fixed is not None and not fixed.isEmpty() else geometry

    def _warn(self, message):
        self.iface.messageBar().pushWarning('DEM Editor for Roads and Platforms', message)
