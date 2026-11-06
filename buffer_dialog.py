import os

from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer, QgsWkbTypes
from qgis.PyQt.QtWidgets import (
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
    QVBoxLayout,
    QWidget,
)


class BufferDialog(QDialog):
    def __init__(self, parent=None, active_layer_id=None):
        super().__init__(parent)
        self.setWindowTitle('DEM Editor for Roads and Platforms')
        self.setMinimumWidth(470)
        self._active_layer_id = active_layer_id
        self._build_ui()
        self._populate_layers()
        self._connect()
        self._update_state()
        self._on_dem_changed()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        intro = QLabel(
            'Choose how the corridor is defined, the DEM edit mode, and where the '
            'edited raster and separate corridor shapefile are written.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.source_combo = QComboBox()
        self.source_combo.addItem('Digitize a new centerline', 'digitize')
        self.source_combo.addItem('Use selected polygon features', 'polygon')
        form.addRow('Corridor source:', self.source_combo)

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

        self.dem_combo = QComboBox()
        form.addRow('DEM layer:', self.dem_combo)

        self.raster_edit, raster_row, raster_button = self._path_row('Browse...')
        raster_button.clicked.connect(self._browse_raster)
        form.addRow('Output GeoTIFF:', raster_row)

        self.shp_edit, shp_row, shp_button = self._path_row('Browse...')
        shp_button.clicked.connect(self._browse_shapefile)
        form.addRow('Corridor shapefile:', shp_row)

        layout.addLayout(form)

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
            if isinstance(layer, QgsRasterLayer) and layer.isValid():
                self.dem_combo.addItem(layer.name(), layer.id())
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
            return
        source = layer.source()
        if source and os.path.isfile(source):
            directory = os.path.dirname(source)
            base = os.path.splitext(os.path.basename(source))[0]
        else:
            directory = QgsProject.instance().homePath() or os.path.expanduser('~')
            base = layer.name()
        self.raster_edit.setText(os.path.join(directory, base + '_road_edited.tif'))
        self.shp_edit.setText(os.path.join(directory, base + '_road_corridor.shp'))

    def _update_state(self):
        digitize = self.source_combo.currentData() == 'digitize'
        self.polygon_combo.setEnabled(not digitize)
        self.polygon_scope.setEnabled(not digitize)
        self.selection_label.setEnabled(not digitize)
        self.distance.setEnabled(digitize)
        self.side_combo.setEnabled(digitize)

        if not digitize and self.mode_combo.currentData() == 'road':
            self.mode_combo.setCurrentIndex(self.mode_combo.findData('flat'))
        self.mode_combo.setEnabled(digitize)

        mode = self.mode_combo.currentData()
        flat = mode == 'flat'
        self.flat_source_combo.setEnabled(flat)
        edge = flat and self.flat_source_combo.currentData() == 'edge'
        self.statistic_combo.setEnabled(edge)
        self.flat_value.setEnabled(flat and self.flat_source_combo.currentData() == 'value')
        self.smoothing.setEnabled(mode == 'road')

    def accept(self):
        layer = self.dem_layer()
        if layer is None:
            self._warn('Choose a DEM raster layer.')
            return
        if layer.bandCount() != 1:
            self._warn('The DEM must be a single-band raster.')
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
            'polygon_scope': self.polygon_scope.currentData(),
            'distance': self.distance.value(),
            'side': self.side_combo.currentData(),
            'mode': self.mode_combo.currentData(),
            'flat_source': self.flat_source_combo.currentData(),
            'flat_value': self.flat_value.value(),
            'statistic': self.statistic_combo.currentData(),
            'smooth_m': self.smoothing.value(),
            'feather_m': self.feather.value(),
            'dem_layer_id': self.dem_combo.currentData(),
            'raster_path': self.raster_edit.text().strip(),
            'shapefile_path': self.shp_edit.text().strip(),
        }

    def _warn(self, message):
        from qgis.PyQt.QtWidgets import QMessageBox

        QMessageBox.warning(self, 'DEM Editor for Roads and Platforms', message)
