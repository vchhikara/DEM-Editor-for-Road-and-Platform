import pytest

@pytest.fixture(scope="session")
def app():
    qgis_core = pytest.importorskip("qgis.core")
    a = qgis_core.QgsApplication([], False)
    a.initQgis()
    yield a
    a.exitQgis()
