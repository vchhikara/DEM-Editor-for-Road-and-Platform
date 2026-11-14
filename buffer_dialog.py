import os

from qgis.core import Qgis, QgsProject, QgsRasterLayer, QgsVectorLayer, QgsWkbTypes
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qgis.PyQt.QtCore import Qt


class BufferDialog(QDialog):
    def __init__(self, parent=None, active_layer_id=None):
        super().__init__(parent)
        self.setWindowTitle('DEM Editor for Roads and Platforms')
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinimizeButtonHint)
        self.setMinimumWidth(470)
        self._active_layer_id = active_layer_id
        self._last_auto_raster_path = None
        self._last_auto_shapefile_path = None
        self._build_ui()
        self._populate_layers()
        self._connect()
        self._update_state()
        self._on_dem_changed()
        screen = self.screen() or QApplication.primaryScreen()
        available_height = screen.availableGeometry().height() if screen is not None else 800
        self.resize(560, max(320, min(760, available_height - 80)))

    def _build_ui(self):
        layout = QVBoxLayout(self)
        intro = QLabel(
            'Choose how the corridor is defined, the DEM edit mode, and where the '
            'edited raster and separate corridor shapefile are written.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.source_combo = QComboBox()
        self.source_combo.addItem('Digitize a new centerline', 'digitize')
        self.source_combo.addItem('Use selected line feature', 'line')
        self.source_combo.addItem('Use selected polygon features', 'polygon')
        form.addRow('Corridor source:', self.source_combo)

        self.line_combo = QComboBox()
        form.addRow('Line layer:', self.line_combo)

        self.polygon_combo = QComboBox()
        form.addRow('Polygon layer:', self.polygon_combo)
        self.polygon_scope = QComboBox()
        self.polygon_scope.addItem('Selected features only', 'selected')
        self.polygon_scope.addItem('All features in this layer', 'all')
        form.addRow('Polygon features:', self.polygon_scope)
        self.selection_label = QLabel('')
        form.addRow('', self.selection_label)

        self.distance = QDoubleSpinBox()
        self.distance.setRange(0.01, 100000000.0)
        self.distance.setDecimals(3)
        self.distance.setValue(10.0)
        self.distance.setSuffix(' m')
        self.distance.setToolTip('Distance from the centerline to each selected side. Both sides gives twice this total width.')
        form.addRow('Buffer distance:', self.distance)

        self.side_combo = QComboBox()
        self.side_combo.addItem('Both sides', 'both')
        self.side_combo.addItem('Left side', 'left')
        self.side_combo.addItem('Right side', 'right')
        form.addRow('Buffer side:', self.side_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem('Road - level across, follows corridor terrain', 'road')
        self.mode_combo.addItem('Flat corridor', 'flat')
        form.addRow('Edit mode:', self.mode_combo)

        self.flat_source_combo = QComboBox()
        self.flat_source_combo.addItem('Single elevation value', 'value')
        self.flat_source_combo.addItem('Average of corridor edge', 'edge')
        form.addRow('Flat elevation from:', self.flat_source_combo)

        self.flat_value = QDoubleSpinBox()
        self.flat_value.setRange(-10000000.0, 10000000.0)
        self.flat_value.setDecimals(3)
        self.flat_value.setValue(0.0)
        form.addRow('Flat elevation:', self.flat_value)

        self.statistic_combo = QComboBox()
        self.statistic_combo.addItem('Median (recommended)', 'median')
        self.statistic_combo.addItem('Mean', 'mean')
        form.addRow('Edge statistic:', self.statistic_combo)

        self.smoothing = QDoubleSpinBox()
        self.smoothing.setRange(0.0, 1000000.0)
        self.smoothing.setDecimals(1)
        self.smoothing.setValue(0.0)
        self.smoothing.setSuffix(' m')
        self.smoothing.setToolTip(
            'Smooths the road elevations calculated from terrain across the buffer. 0 uses a small automatic '
            'window of about five DEM pixels.'
        )
        form.addRow('Profile smoothing:', self.smoothing)

        self.profile_method = QComboBox()
        self.profile_method.addItem('Terrain following', 'terrain')
        self.profile_method.addItem('Gradient controlled', 'grade')
        self.profile_method.addItem('Median terrain fit (not volume balance)', 'balanced')
        self.profile_method.setToolTip('Terrain following does not apply a maximum grade and can produce steep roads. Gradient controlled and median terrain fit apply the selected grade limit to the calculated profile. The median fit does not guarantee balanced earthworks. Sharp turns may be levelled locally; inspect the report.')
        form.addRow('Road profile:', self.profile_method)
        self.maximum_grade = QDoubleSpinBox()
        self.maximum_grade.setRange(0.1, 100.0)
        self.maximum_grade.setValue(10.0)
        self.maximum_grade.setSuffix(' %')
        form.addRow('Maximum grade:', self.maximum_grade)
        self.crossfall_tolerance = QDoubleSpinBox()
        self.crossfall_tolerance.setRange(0.01, 100.0)
        self.crossfall_tolerance.setDecimals(2)
        self.crossfall_tolerance.setValue(0.10)
        self.crossfall_tolerance.setSuffix(' DEM elevation units')
        self.crossfall_tolerance.setToolTip('Review threshold for differences across sampled road sections. This is a configurable quality indicator, not an engineering standard.')
        form.addRow('Cross-road review threshold:', self.crossfall_tolerance)

        self.feather = QDoubleSpinBox()
        self.feather.setRange(0.0, 1000000.0)
        self.feather.setDecimals(1)
        self.feather.setValue(20.0)
        self.feather.setSuffix(' m')
        self.feather.setToolTip(
            'Blend cuts and fills into the original ground outside the flat corridor. '
            'Set to 0 for a sharp edge.'
        )
        form.addRow('Outside transition width:', self.feather)
        self.cut_slope = QDoubleSpinBox()
        self.cut_slope.setRange(0.05, 10.0); self.cut_slope.setValue(1.0)
        self.cut_slope.setSuffix(' vertical / horizontal')
        form.addRow('Cut slope:', self.cut_slope)
        self.fill_slope = QDoubleSpinBox()
        self.fill_slope.setRange(0.05, 10.0); self.fill_slope.setValue(0.5)
        self.fill_slope.setSuffix(' vertical / horizontal')
        form.addRow('Fill slope:', self.fill_slope)

        self.dem_combo = QComboBox()
        form.addRow('DEM layer:', self.dem_combo)
        self.output_dtype = QComboBox()
        self.output_dtype.addItem('Floating point (retain fractional elevations)', 'float')
        self.output_dtype.addItem('Keep source data type (may round)', 'source')
        self.output_dtype.setToolTip('Floating point retains fractional edits within its numeric precision; it does not add terrain detail.')
        form.addRow('Output precision:', self.output_dtype)
        self.resolution_label = QLabel('')
        self.resolution_label.setWordWrap(True)
        form.addRow('DEM / road resolution:', self.resolution_label)
        self.resample = QCheckBox('Interpolate working DEM')
        self.resample.setToolTip('Create a working DEM at the chosen pixel size. Interpolation does not add terrain detail.')
        form.addRow('', self.resample)
        self.target_resolution = QDoubleSpinBox()
        self.target_resolution.setRange(0.01, 1000000.0)
        self.target_resolution.setDecimals(2)
        self.target_resolution.setValue(2.0)
        self.target_resolution.setSuffix(' m')
        self.target_resolution.setToolTip('Interpolation changes the grid, but cannot restore terrain detail missing in the original DEM.')
        resolution_row = QWidget()
        resolution_row.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        resolution_layout = QHBoxLayout(resolution_row)
        resolution_layout.setContentsMargins(0, 0, 0, 0)
        resolution_layout.addWidget(self.target_resolution)
        self.suggest_resolution = QPushButton('Use 5 cells')
        self.suggest_resolution.setToolTip('Use road width divided by five as a working pixel size. This improves grid representation, not the underlying terrain detail.')
        resolution_layout.addWidget(self.suggest_resolution)
        form.addRow('Working pixel size:', resolution_row)
        self.difference = QCheckBox('Save difference raster')
        self.difference.setToolTip('Save a raster of edited minus original elevations on the working grid.')
        form.addRow('', self.difference)
        self.diagnostics = QCheckBox('Save road sample points')
        self.diagnostics.setToolTip('Save up to three sampled points per road station as a diagnostic point layer.')
        form.addRow('', self.diagnostics)

        self.raster_edit, raster_row, raster_button = self._path_row('Browse...')
        raster_button.clicked.connect(self._browse_raster)
        form.addRow('Output GeoTIFF:', raster_row)

        self.shp_edit, shp_row, shp_button = self._path_row('Browse...')
        shp_button.clicked.connect(self._browse_shapefile)
        form.addRow('Corridor shapefile:', shp_row)

        # Keep the action buttons visible on small or high-DPI displays.
        # The growing form scrolls rather than extending beyond the screen.
        form_widget = QWidget()
        form_widget.setLayout(form)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        for combo in form_widget.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(10)
        for checkbox in form_widget.findChildren(QCheckBox):
            checkbox.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setWidget(form_widget)
        self.form_scroll.setMinimumHeight(200)
        layout.addWidget(self.form_scroll, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _path_row(self, button_text):
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit()
        button = QPushButton(button_text)
        row.addWidget(edit)
        row.addWidget(button)
        return edit, container, button

    def _connect(self):
        self.source_combo.currentIndexChanged.connect(self._update_state)
        self.mode_combo.currentIndexChanged.connect(self._update_state)
        self.flat_source_combo.currentIndexChanged.connect(self._update_state)
        self.dem_combo.currentIndexChanged.connect(self._on_dem_changed)
        self.polygon_combo.currentIndexChanged.connect(self._refresh_selection_count)
        self.polygon_scope.currentIndexChanged.connect(self._refresh_selection_count)
        self.distance.valueChanged.connect(self._update_resolution)
        self.side_combo.currentIndexChanged.connect(self._update_resolution)
        self.source_combo.currentIndexChanged.connect(self._update_resolution)
        self.profile_method.currentIndexChanged.connect(self._update_state)
        self.target_resolution.valueChanged.connect(self._update_resolution)
        self.resample.toggled.connect(self._update_resolution)
        self.suggest_resolution.clicked.connect(self._use_suggested_resolution)
        for index in range(self.polygon_combo.count()):
            layer = self._layer_by_id(self.polygon_combo.itemData(index))
            if layer is not None:
                layer.selectionChanged.connect(self._refresh_selection_count)

    def _browse_raster(self):
        path, _ = QFileDialog.getSaveFileName(
            self, 'Save edited GeoTIFF', self.raster_edit.text(), 'GeoTIFF (*.tif *.tiff)'
        )
        if path:
            self.raster_edit.setText(path)

    def _browse_shapefile(self):
        path, _ = QFileDialog.getSaveFileName(
            self, 'Save corridor shapefile', self.shp_edit.text(), 'ESRI Shapefile (*.shp)'
        )
        if path:
            self.shp_edit.setText(path)

    def _populate_layers(self):
        project = QgsProject.instance()
        for layer in project.mapLayers().values():
            # Online XYZ/WMS layers are raster layers too, but this tool edits
            # local file-based DEMs and must not pass their URIs to GDAL Open.
            if (isinstance(layer, QgsRasterLayer) and layer.isValid()
                    and layer.providerType() == 'gdal' and os.path.isfile(layer.source())):
                self.dem_combo.addItem(layer.name(), layer.id())
            if isinstance(layer, QgsVectorLayer) and layer.isValid() and layer.geometryType() == QgsWkbTypes.GeometryType.LineGeometry:
                self.line_combo.addItem(layer.name(), layer.id())
            if (
                isinstance(layer, QgsVectorLayer)
                and layer.isValid()
                and layer.geometryType() == QgsWkbTypes.GeometryType.PolygonGeometry
            ):
                self.polygon_combo.addItem(layer.name(), layer.id())

        if self._active_layer_id:
            index = self.dem_combo.findData(self._active_layer_id)
            if index >= 0:
                self.dem_combo.setCurrentIndex(index)
            index = self.polygon_combo.findData(self._active_layer_id)
            if index >= 0:
                self.polygon_combo.setCurrentIndex(index)
            index = self.line_combo.findData(self._active_layer_id)
            if index >= 0:
                self.line_combo.setCurrentIndex(index)
        # If the active layer is not a polygon, prefer the sole polygon layer
        # with a selection, instead of an unrelated first entry.
        if self.polygon_combo.findData(self._active_layer_id) < 0:
            selected = [i for i in range(self.polygon_combo.count())
                        if self._layer_by_id(self.polygon_combo.itemData(i)).selectedFeatureCount()]
            if len(selected) == 1:
                self.polygon_combo.setCurrentIndex(selected[0])
        self._refresh_selection_count()

    def _layer_by_id(self, layer_id):
        if not layer_id:
            return None
        layer = QgsProject.instance().mapLayer(layer_id)
        return layer if layer is not None and layer.isValid() else None

    def dem_layer(self):
        return self._layer_by_id(self.dem_combo.currentData())

    def polygon_layer(self):
        return self._layer_by_id(self.polygon_combo.currentData())

    def _refresh_selection_count(self):
        layer = self.polygon_layer()
        if layer is None:
            self.selection_label.setText('No polygon layer available.')
            return
        count = layer.selectedFeatureCount()
        total = layer.featureCount()
        if self.polygon_scope.currentData() == 'all':
            self.selection_label.setText('Using all {} features ({} selected)'.format(total, count))
        elif count:
            self.selection_label.setText('Selected features: {} of {}'.format(count, total))
        else:
            self.selection_label.setText('Selected features: 0 of {} (select features in the map or attribute table)'.format(total))

    def _on_dem_changed(self):
        layer = self.dem_layer()
        if layer is None:
            self._update_resolution()
            return
        source = layer.source()
        if source and os.path.isfile(source):
            directory = os.path.dirname(source)
            base = os.path.splitext(os.path.basename(source))[0]
        else:
            directory = QgsProject.instance().homePath() or os.path.expanduser('~')
            base = layer.name()
        raster_auto = os.path.join(directory, base + '_road_edited.tif')
        shp_auto = os.path.join(directory, base + '_road_corridor.shp')
        if not self.raster_edit.text().strip() or self.raster_edit.text() == self._last_auto_raster_path:
            self.raster_edit.setText(raster_auto)
            self._last_auto_raster_path = raster_auto
        if not self.shp_edit.text().strip() or self.shp_edit.text() == self._last_auto_shapefile_path:
            self.shp_edit.setText(shp_auto)
            self._last_auto_shapefile_path = shp_auto
        self._update_resolution()

    def _update_resolution(self):
        layer = self.dem_layer()
        if layer is None:
            self.resolution_label.setText('Choose a DEM.')
            self.suggest_resolution.setEnabled(False)
            return
        from .resource_check import raster_pixel_dimensions
        try:
            x, y = raster_pixel_dimensions(layer)
        except ValueError:
            self.resolution_label.setText('DEM pixel size unavailable.')
            self.suggest_resolution.setEnabled(False)
            return
        if not x or not y:
            self.resolution_label.setText('DEM pixel size unavailable.')
            self.suggest_resolution.setEnabled(False)
            return
        if self.source_combo.currentData() == 'polygon':
            self.resolution_label.setText('Pixel size {:.2f} x {:.2f} map units. Polygon width varies; inspect its narrow sections. Interpolation cannot restore missing detail.'.format(x, y))
            self.suggest_resolution.setEnabled(False)
            return
        width = self.distance.value() * (2 if self.side_combo.currentData() == 'both' else 1)
        cells = width / max(x, y)
        note = 'coarse (<3 pixels)' if cells < 3 else 'rough (3–5 pixels)' if cells < 5 else 'at least 5 pixels across'
        working = (' Selected working grid: {:.1f} pixels across.'.format(width / self.target_resolution.value())
                   if self.resample.isChecked() else '')
        self.resolution_label.setText('Pixel {:.2f} x {:.2f} m; road {:.2f} m: {:.1f} pixels ({}). For 5 pixels use {:.2f} m or finer.{} Interpolation adds no terrain detail.'.format(x, y, width, cells, note, width / 5, working))
        self.suggest_resolution.setEnabled(width > 0 and width / 5 >= self.target_resolution.minimum() and cells < 5)

    def _use_suggested_resolution(self):
        width = self.distance.value() * (2 if self.side_combo.currentData() == 'both' else 1)
        if self.dem_layer() is not None and self.source_combo.currentData() != 'polygon' and width > 0:
            # Round down so the displayed value does not suggest fewer than
            # five cells; never silently replace a user's existing value.
            import math
            suggested = math.floor((width / 5.0) * 100.0) / 100.0
            if suggested >= self.target_resolution.minimum():
                self.target_resolution.setValue(suggested)
                self.resample.setChecked(True)

    def _update_state(self):
        source = self.source_combo.currentData()
        polygon = source == 'polygon'
        self.line_combo.setEnabled(source == 'line')
        self.polygon_combo.setEnabled(polygon)
        self.polygon_scope.setEnabled(polygon)
        self.selection_label.setEnabled(polygon)
        self.distance.setEnabled(not polygon)
        self.side_combo.setEnabled(not polygon)

        mode = self.mode_combo.currentData()
        flat = mode == 'flat'
        self.flat_source_combo.setEnabled(flat)
        edge = flat and self.flat_source_combo.currentData() == 'edge'
        self.statistic_combo.setEnabled(edge)
        self.flat_value.setEnabled(flat and self.flat_source_combo.currentData() == 'value')
        self.smoothing.setEnabled(mode == 'road')
        self.profile_method.setEnabled(mode == 'road')
        self.maximum_grade.setEnabled(mode == 'road' and self.profile_method.currentData() != 'terrain')
        self.crossfall_tolerance.setEnabled(mode == 'road')

    def accept(self):
        layer = self.dem_layer()
        if layer is None:
            self._warn('Please select a proper local DEM raster layer. Online satellite basemaps are not DEMs.')
            return
        if not layer.crs().isValid() or layer.crs().isGeographic() or layer.crs().mapUnits() != Qgis.DistanceUnit.Meters:
            self._warn('Reproject the DEM to a projected CRS with metre units first.')
            return
        if not os.path.isfile(layer.source()):
            self._warn('Choose a readable file-based DEM.')
            return
        from osgeo import gdal
        from .resource_check import assess_grid, grid_geometry, warped_grid_size
        try:
            source_ds = gdal.Open(layer.source(), gdal.GA_ReadOnly)
        except RuntimeError:
            source_ds = None
        if source_ds is None:
            self._warn('The selected DEM could not be opened.')
            return
        # QGIS may add a virtual alpha band when displaying a rotated raster;
        # validate the actual file, not the provider's display band count.
        if source_ds.RasterCount != 1:
            self._warn('The DEM must be a single-band raster.')
            return
        band = source_ds.GetRasterBand(1)
        from .dem_editor import _check_vertical_unit, DemEditError
        try:
            _check_vertical_unit(band.GetUnitType())
        except DemEditError as error:
            self._warn(str(error))
            return
        if gdal.DataTypeIsComplex(band.DataType):
            self._warn('Complex-valued rasters are not supported as DEM elevations. Convert to a suitable real-valued DEM first.')
            return
        unsafe_types = tuple(getattr(gdal, name) for name in ('GDT_Int64', 'GDT_UInt64') if hasattr(gdal, name))
        if band.DataType in unsafe_types:
            self._warn('This 64-bit integer DEM cannot be safely processed with floating-point elevation calculations. Convert it to a suitable DEM first.')
            return
        scale, offset = band.GetScale(), band.GetOffset()
        if (scale is not None and scale != 1.0) or (offset is not None and offset != 0.0):
            self._warn('This DEM uses a scale or offset. Convert its elevations to physical values before editing.')
            return
        width, height = source_ds.RasterXSize, source_ds.RasterYSize
        gt = source_ds.GetGeoTransform()
        try:
            _, _, orthogonal = grid_geometry(gt)
        except ValueError as error:
            self._warn(str(error))
            return
        if not orthogonal and not self.resample.isChecked() and self.feather.value() > 0:
            self._warn('Cut/fill transitions require orthogonal DEM pixels. Select a finer interpolated working DEM or reproject to a north-up grid.')
            return
        if self.resample.isChecked():
            resolution = self.target_resolution.value()
            width, height = warped_grid_size(width, height, gt, resolution)
        band = None; source_ds = None
        cells, estimate, available = assess_grid(width, height)
        if available is not None and estimate > available * 0.7:
            self._warn('This edit may exceed available memory: estimated {:.1f} GiB for {} cells; {:.1f} GiB currently available. Crop the DEM or use a coarser working grid.'.format(estimate / 2**30, cells, available / 2**30))
            return

        if self.source_combo.currentData() == 'line':
            line = self._layer_by_id(self.line_combo.currentData())
            if line is None or line.selectedFeatureCount() != 1:
                self._warn('Select exactly one line feature in the chosen line layer.')
                return
        if self.source_combo.currentData() == 'polygon':
            polygon_layer = self.polygon_layer()
            if polygon_layer is None:
                self._warn('Choose a polygon layer.')
                return
            if self.polygon_scope.currentData() == 'selected' and polygon_layer.selectedFeatureCount() == 0:
                self._warn('Select one or more polygons in the chosen layer first.')
                return
            if self.polygon_scope.currentData() == 'all' and polygon_layer.featureCount() == 0:
                self._warn('The chosen polygon layer has no features.')
                return

        raster_path = self.raster_edit.text().strip()
        if not raster_path:
            self._warn('Choose an output GeoTIFF path.')
            return
        if not raster_path.lower().endswith(('.tif', '.tiff')):
            raster_path += '.tif'
        self.raster_edit.setText(raster_path)

        shapefile_path = self.shp_edit.text().strip()
        if not shapefile_path:
            self._warn('Choose an output corridor shapefile path.')
            return
        if not shapefile_path.lower().endswith('.shp'):
            shapefile_path += '.shp'
        self.shp_edit.setText(shapefile_path)

        for label, path in (
            ('GeoTIFF', raster_path),
            ('shapefile', shapefile_path),
        ):
            folder = os.path.dirname(os.path.abspath(path))
            if not os.path.isdir(folder):
                self._warn(
                    'The {} output folder does not exist:\n{}'.format(label, folder)
                )
                return

        source = layer.source()
        if source and os.path.isfile(source):
            if os.path.normcase(os.path.abspath(source)) == os.path.normcase(
                os.path.abspath(raster_path)
            ):
                self._warn('The output GeoTIFF must differ from the source DEM.')
                return

        super().accept()

    def settings(self):
        return {
            'source': self.source_combo.currentData(),
            'polygon_layer_id': self.polygon_combo.currentData(),
            'line_layer_id': self.line_combo.currentData(),
            'polygon_scope': self.polygon_scope.currentData(),
            'distance': self.distance.value(),
            'side': self.side_combo.currentData(),
            'mode': self.mode_combo.currentData(),
            'flat_source': self.flat_source_combo.currentData(),
            'flat_value': self.flat_value.value(),
            'statistic': self.statistic_combo.currentData(),
            'smooth_m': self.smoothing.value(),
            'profile_method': self.profile_method.currentData(),
            'maximum_grade': self.maximum_grade.value(),
            'crossfall_tolerance': self.crossfall_tolerance.value(),
            'resample': self.resample.isChecked(),
            'target_resolution': self.target_resolution.value(),
            'difference': self.difference.isChecked(),
            'diagnostics': self.diagnostics.isChecked(),
            'feather_m': self.feather.value(),
            'cut_slope': self.cut_slope.value(),
            'fill_slope': self.fill_slope.value(),
            'dem_layer_id': self.dem_combo.currentData(),
            'output_dtype': self.output_dtype.currentData(),
            'raster_path': self.raster_edit.text().strip(),
            'shapefile_path': self.shp_edit.text().strip(),
        }

    def _warn(self, message):
        from qgis.PyQt.QtWidgets import QMessageBox

        QMessageBox.warning(self, 'DEM Editor for Roads and Platforms', message)


