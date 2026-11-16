"""Stage all output files and publish them without overwriting existing data."""
import logging
import os
import tempfile


LOG = logging.getLogger(__name__)


def output_family(path):
    """Paths whose presence could alter or be mixed with this output."""
    root, extension = os.path.splitext(path)
    extension = extension.lower()
    if extension == '.shp':
        return [root + suffix for suffix in ('.shp', '.shx', '.dbf', '.prj', '.cpg', '.qix', '.sbn', '.sbx', '.shp.xml')]
    if extension in ('.tif', '.tiff'):
        return [path + suffix for suffix in ('', '.msk', '.aux.xml', '.ovr', '.xml')] + [root + suffix for suffix in ('.tfw', '.tifw', '.wld')]
    return [path]


class OutputTransaction:
    def __init__(self, output_path):
        self.parent = os.path.dirname(os.path.abspath(output_path))
        self._temp = None
        self._entries = []
        self._created = []

    def __enter__(self):
        self._temp = tempfile.TemporaryDirectory(prefix='.dem-editor-', dir=self.parent)
        return self

    def stage(self, final_path):
        if not final_path:
            return None
        final_path = os.path.abspath(final_path)
        if any(os.path.normcase(final_path) == os.path.normcase(dest) for _, dest in self._entries):
            raise ValueError('Two outputs have the same destination: {}'.format(final_path))
        name = '{:02d}_{}'.format(len(self._entries), os.path.basename(final_path))
        path = os.path.join(self._temp.name, name)
        self._entries.append((path, final_path))
        return path

    @staticmethod
    def _members(stage, final):
        if os.path.splitext(stage)[1].lower() == '.shp':
            stage_root = os.path.splitext(stage)[0]
            final_root = os.path.splitext(final)[0]
            return [(stage_root + ext, final_root + ext) for ext in ('.shp', '.shx', '.dbf', '.prj', '.cpg', '.qix') if os.path.isfile(stage_root + ext)]
        return [(stage + ext, final + ext) for ext in ('', '.msk', '.aux.xml', '.ovr') if os.path.isfile(stage + ext)]

    def publish(self, is_cancelled=None):
        members = []
        for stage, final in self._entries:
            if not os.path.isfile(stage):
                raise OSError('An expected output was not created: {}'.format(final))
            # A stale sidecar can change how a newly written raster or vector
            # is read even when the primary file itself does not yet exist.
            for path in output_family(final):
                if os.path.exists(path):
                    raise FileExistsError('An output file or sidecar already exists: {}'.format(path))
            members.extend(self._members(stage, final))
        targets = [os.path.normcase(os.path.abspath(dest)) for _, dest in members]
        if len(targets) != len(set(targets)):
            raise OSError('Two output components resolve to the same file.')
        try:
            for src, dest in members:
                if is_cancelled and is_cancelled():
                    raise InterruptedError('The edit was cancelled during output publication.')
                # Exclusive creation never deletes or overwrites an existing file.
                with open(dest, 'xb') as output:
                    self._created.append(dest)
                    with open(src, 'rb') as source:
                        while True:
                            if is_cancelled and is_cancelled():
                                raise InterruptedError('The edit was cancelled during output publication.')
                            chunk = source.read(1024*1024)
                            if not chunk:
                                break
                            output.write(chunk)
        except BaseException:
            for path in reversed(self._created):
                try:
                    os.remove(path)
                except OSError:
                    LOG.debug('Could not remove a staged file.', exc_info=True)
            self._created.clear()
            raise

    def __exit__(self, kind, error, tb):
        self._temp.cleanup()
        return False
