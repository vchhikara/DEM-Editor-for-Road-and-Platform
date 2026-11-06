import os
from datetime import datetime

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
    QgsRasterLayer,
    QgsTask,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox

from .buffer_dialog import BufferDialog
from .dem_editor import EditCancelled, edit_dem
from .map_tool import DirectionalBufferMapTool, build_buffer
from .road_output import write_corridor

ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.png')


class DemRoadBufferEditor:
    """QGIS plugin entry point for corridor-based DEM editing."""

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.map_tool = None
        self.task = None
        self.digitize_settings = None
        self.dem_path = None
        self.source_dem_path = None

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

    def unload(self):
        if self.action:
            self.iface.removePluginMenu(self.tr('&DEM Editor for Roads and Platforms'), self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.task is not None:
            self.task.cancel()
            self.task = None
        self._clear_map_tool()

    def start(self):
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

        active = self.iface.activeLayer()
        dialog = BufferDialog(
            self.iface.mainWindow(), active.id() if active is not None else None
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        settings = dialog.settings()
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
        else:
            self._start_digitizing(settings)

    def _clear_map_tool(self):
        if self.map_tool is None:
            return
        canvas = self.iface.mapCanvas()
        if canvas.mapTool() is self.map_tool:
            canvas.unsetMapTool(self.map_tool)
        self.map_tool.deactivate()
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
        self._run_edit(settings, corridor, None)

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
            'Digitize the centerline: click vertices, double-click/Enter/right-click to finish, '
            'Esc to cancel.',
        )

    def _centerline_finished(self, line):
        settings = self.digitize_settings
        if settings is None:
            return
        corridor = build_buffer(line, settings['distance'], settings['side'])
        if corridor is None or corridor.isEmpty():
            self._warn('The buffer corridor could not be built from that centerline.')
            return
        corridor = self._make_valid(corridor)
        self._run_edit(settings, corridor, line)

    def _run_edit(self, settings, corridor, line):
        if self.task is not None:
            self._warn('A DEM edit is already running. Wait for it to finish.')
            return

        dem_path = self.dem_path
        raster_path = self._unique_raster_path(settings['raster_path'])
        shapefile_path = self._unique_shapefile_path(settings['shapefile_path'])
        if os.path.normcase(os.path.abspath(dem_path)) == os.path.normcase(
            os.path.abspath(raster_path)
        ):
            self._warn('The output GeoTIFF must differ from the source DEM.')
            return

        attributes = {
            'mode': 'Road' if settings['mode'] == 'road' else 'Flat',
            'dist_m': float(settings['distance']) if settings['source'] == 'digitize' else 0.0,
            'side': settings['side'] if settings['source'] == 'digitize' else '',
            'stat': self._statistic_label(settings),
            'source': 'digitized' if settings['source'] == 'digitize' else 'existing polygon',
            'dem_path': dem_path,
            'edit_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

        task = QgsTask.fromFunction(
            'DEM road and platform edit',
            self._run_task,
            on_finished=self._task_finished,
            flags=QgsTask.CanCancel,
            dem_path=dem_path,
            project_crs_wkt=QgsProject.instance().crs().toWkt(),
            corridor_wkt=corridor.asWkt(),
            line_wkt=line.asWkt() if line is not None else None,
            settings=dict(settings),
            output_path=raster_path,
            shapefile_path=shapefile_path,
            attributes=attributes,
            transform_context=QgsProject.instance().transformContext(),
        )
        self.task = task
        QgsApplication.taskManager().addTask(task)
        self.iface.messageBar().pushInfo(
            'DEM Editor for Roads and Platforms', 'Editing the DEM corridor in the background...'
        )

    @staticmethod
    def _statistic_label(settings):
        if settings['mode'] != 'flat':
            return 'terrain'
        if settings['flat_source'] == 'edge':
            return settings['statistic']
        return 'value'

    def _run_task(self, task, **kwargs):
        def progress(percent):
            task.setProgress(float(percent))
            return not task.isCanceled()

        try:
            result = edit_dem(
                kwargs['dem_path'],
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
                pass
            raise
        if task.isCanceled():
            try:
                os.remove(result['output_path'])
            except OSError:
                pass
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
                pass
            raise
        result['shapefile_path'] = kwargs['shapefile_path']
        return result

    def _task_finished(self, exception, result):
        self.task = None
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
        self.dem_path = result['output_path']
        self.iface.mapCanvas().refresh()

        if result['mode'] == 'road':
            detail = 'terrain-following road'
        elif result['applied_value'] is not None:
            detail = 'flat at {:.3f}'.format(result['applied_value'])
        else:
            detail = 'flat corridor'
        message = ('{} corridor pixels, {} transition pixels; {} elevation values changed; '
                   '{} nodata pixels skipped ({}). Max cut {:.2f}, fill {:.2f} (DEM elevation units).').format(
            result['edited_pixels'], result['transition_pixels'], result['changed_pixels'],
            result['skipped_nodata_pixels'], detail, result['max_cut'], result['max_fill']
        )
        if result['changed_pixels'] == 0:
            self.iface.messageBar().pushWarning('DEM Editor for Roads and Platforms', message + ' No values changed; check DEM resolution and settings.')
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
            os.path.exists(candidate)
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
        while (any(os.path.exists(os.path.splitext(candidate)[0] + suffix)
                   for suffix in ('.shp', '.shx', '.dbf', '.prj', '.cpg'))
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
