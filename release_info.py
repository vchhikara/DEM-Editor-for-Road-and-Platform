"""Version-scoped release information shown when the plugin is first opened."""
import os
from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMessageBox, QPushButton, QTextBrowser, QVBoxLayout

PLUGIN_VERSION = '0.4.3'
SETTINGS_KEY = 'DEMEditorRoadsPlatforms/last_seen_version'


def should_show(settings=None):
    settings = settings or QSettings()
    return settings.value(SETTINGS_KEY, '', type=str) != PLUGIN_VERSION


def mark_seen(settings=None):
    settings = settings or QSettings()
    settings.setValue(SETTINGS_KEY, PLUGIN_VERSION)


def show_guide(parent=None):
    """Show bundled help inside QGIS without relying on a Markdown app."""
    path = os.path.join(os.path.dirname(__file__), 'HELP.md')
    try:
        with open(path, encoding='utf-8') as source:
            contents = source.read()
    except OSError as error:
        QMessageBox.warning(parent, 'User guide unavailable', 'Could not open the bundled guide: {}'.format(error))
        return
    dialog = QDialog(parent)
    dialog.setWindowTitle('User guide — DEM Editor for Roads and Platforms')
    screen = dialog.screen() or QApplication.primaryScreen()
    available = screen.availableGeometry() if screen is not None else None
    dialog.resize(min(720, available.width() - 40) if available else 720,
                  min(600, available.height() - 80) if available else 600)
    layout = QVBoxLayout(dialog)
    text = QTextBrowser(dialog)
    if hasattr(text, 'setMarkdown'):
        text.setMarkdown(contents)
    else:
        text.setPlainText(contents)
    layout.addWidget(text)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()


def show_release_info(parent=None, mark_as_seen=False):
    dialog = QDialog(parent)
    dialog.setWindowTitle("What's new — DEM Editor for Roads and Platforms")
    dialog.resize(570, 430)
    layout = QVBoxLayout(dialog)
    content = QTextBrowser(dialog)
    content.setPlainText(
        'DEM Editor for Roads and Platforms — version ' + PLUGIN_VERSION + '\n\n'
        'WHAT IS NEW SINCE VERSION 0.3.9\n'
        '• Use a selected line or review a suggested centerline from a polygon.\n'
        '• Undo an unfinished vertex with Backspace or Ctrl+Z; review the corridor before applying.\n'
        '• Review terrain-following and gradient-controlled road profiles and cut/fill slopes.\n'
        '• See DEM resolution guidance and choose a finer working grid when needed.\n'
        '• Review elevation changes, output-grade checks and cautions in a text report.\n\n'
        'FIXES AND IMPROVEMENTS IN 0.4.3\n'
        '• Protect existing output files and retain valid-data masks.\n'
        '• Improve fractional output precision and report rounding of platform elevations.\n'
        '• Adjust road profiles at selected bends and check sampled output grades and cross-sections.\n'
        '• Warn when checks fail or are incomplete; choose a five-cell working-grid suggestion.\n'
        '• Review polygon routes, branches and holes; disconnected road polygons must be processed separately.\n\n'
        'BEFORE YOU START\n'
        'Choose a projected, single-band DEM in metres with enough detail for the road width. '
        'Keep the source DEM and review the output and cross-sections, especially on bends and steep ground.\n\n'
        'Checks sample the output and cannot certify every location. Interpolation does not add terrain detail. '
        'The plugin produces a preliminary terrain model. Confirm road engineering, '
        'ground stability and drainage separately.\n\n'
        'Use the User Guide button for operating steps and known limitations.'
    )
    layout.addWidget(content)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
    guide = QPushButton('Open User Guide', dialog)
    buttons.addButton(guide, QDialogButtonBox.ButtonRole.ActionRole)
    guide.clicked.connect(lambda: show_guide(dialog))
    buttons.rejected.connect(dialog.reject)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    dialog.exec()
    if mark_as_seen:
        mark_seen()
