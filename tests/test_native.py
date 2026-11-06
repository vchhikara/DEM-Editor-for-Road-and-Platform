"""Native QGIS test: load the plugin, run initGui and unload."""
import os, sys
import pytest
qgis_core = pytest.importorskip("qgis.core")
from qgis.core import QgsApplication, QgsGeometry, QgsPointXY
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtWidgets import QWidget

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load(app):
    import importlib.util
    spec = importlib.util.spec_from_file_location("demplugin", os.path.join(ROOT, "__init__.py"),
                                                  submodule_search_locations=[ROOT])
    m = importlib.util.module_from_spec(spec); sys.modules["demplugin"] = m; spec.loader.exec_module(m)
    return m

class Iface:
    def __init__(self): self.win = QWidget(); self.canvas = QgsMapCanvas()
    def mainWindow(self): return self.win
    def mapCanvas(self): return self.canvas
    def addPluginToMenu(self, *a): pass
    def addToolBarIcon(self, *a): pass
    def removePluginMenu(self, *a): pass
    def removeToolBarIcon(self, *a): pass

def test_load_init_unload(app):
    p = load(app).classFactory(Iface()); p.initGui(); p.unload()

def test_map_tool_and_buffer(app):
    from demplugin.map_tool import DirectionalBufferMapTool, build_buffer
    c = QgsMapCanvas(); t = DirectionalBufferMapTool(c, 5.0, "both", None)
    t.points = [QgsPointXY(0, 0), QgsPointXY(10, 0)]; t.refresh_preview(); t.deactivate()
    for side in ("left", "right", "both"):
        assert build_buffer(QgsGeometry.fromWkt("LINESTRING(0 0,10 0)"), 5, side).area() > 0
