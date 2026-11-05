from qgis.core import Qgis, QgsGeometry, QgsPointXY, QgsWkbTypes
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor

DEFAULT_SEGMENTS = 12
MITER_JOIN = Qgis.JoinStyle.Miter
MITER_LIMIT = 5.0


def build_buffer(line, distance, side, segments=DEFAULT_SEGMENTS):
    """Build a left, right, or both-side buffer around a line geometry."""
    if side == 'both':
        return line.buffer(distance, segments)
    buffer_side = Qgis.BufferSide.Left if side == 'left' else Qgis.BufferSide.Right
    return line.singleSidedBuffer(distance, segments, buffer_side, MITER_JOIN, MITER_LIMIT)


class DirectionalBufferMapTool(QgsMapTool):
    """Digitize a centerline and report the finished geometry through a callback."""

    def __init__(self, canvas, distance, side, on_finish):
        super().__init__(canvas)
        self.canvas = canvas
        self.distance = distance
        self.side = side
        self.on_finish = on_finish
        self.points = []
        self.cursor_point = None

        self.center_band = QgsRubberBand(self.canvas, QgsWkbTypes.LineGeometry)
        self.center_band.setColor(QColor(110, 110, 110, 190))
        self.center_band.setWidth(2)
        self.center_band.setLineStyle(Qt.DotLine)

        self.buffer_band = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.buffer_band.setColor(QColor(36, 130, 170, 220))
        self.buffer_band.setFillColor(QColor(100, 190, 220, 55))
        self.buffer_band.setWidth(2)
        self.buffer_band.setLineStyle(Qt.DotLine)

        self.setCursor(Qt.CrossCursor)

    def canvasPressEvent(self, event):
        point = self.toMapCoordinates(event.pos())
        if event.button() == Qt.LeftButton:
            self.points.append(QgsPointXY(point))
            self.cursor_point = QgsPointXY(point)
            self.refresh_preview()
        elif event.button() == Qt.RightButton:
            self.finish()

    def canvasMoveEvent(self, event):
        self.cursor_point = QgsPointXY(self.toMapCoordinates(event.pos()))
        if self.points:
            self.refresh_preview(include_cursor=True)

    def canvasDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            point = QgsPointXY(self.toMapCoordinates(event.pos()))
            if not self.points or point != self.points[-1]:
                self.points.append(point)
            self.finish()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancel()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish()

    def _line_points(self, include_cursor=False):
        points = list(self.points)
        if (
            include_cursor
            and self.cursor_point
            and (not points or self.cursor_point != points[-1])
        ):
            points.append(self.cursor_point)
        return points

    def refresh_preview(self, include_cursor=False):
        points = self._line_points(include_cursor)
        self.center_band.reset(QgsWkbTypes.LineGeometry)
        self.buffer_band.reset(QgsWkbTypes.PolygonGeometry)
        if len(points) < 2:
            if points:
                self.center_band.addPoint(points[0], False)
                self.center_band.addPoint(points[0], True)
            return
        line = QgsGeometry.fromPolylineXY(points)
        self.center_band.setToGeometry(line, None)
        polygon = build_buffer(line, self.distance, self.side)
        if polygon and not polygon.isEmpty():
            self.buffer_band.setToGeometry(polygon, None)

    def finish(self):
        if len(self.points) < 2:
            return
        line = QgsGeometry.fromPolylineXY(self.points)
        self.reset_drawing()
        self.canvas.refresh()
        if self.on_finish:
            self.on_finish(line)

    def reset_drawing(self):
        self.points = []
        self.cursor_point = None
        self.center_band.reset(QgsWkbTypes.LineGeometry)
        self.buffer_band.reset(QgsWkbTypes.PolygonGeometry)

    def cancel(self):
        had_points = bool(self.points)
        self.reset_drawing()
        self.canvas.refresh()
        if not had_points:
            self.canvas.unsetMapTool(self)

    def deactivate(self):
        super().deactivate()
        self.reset_drawing()
        self.canvas.refresh()
