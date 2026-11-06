"""Build a reproducible plugin ZIP from an allowlist of runtime files."""
import argparse, zipfile
from pathlib import Path

PLUGIN_FOLDER = "DEM_Editor_for_Road_and_Platform"
INCLUDE = ["__init__.py", "metadata.txt", "LICENSE", "icon.png", "CHANGELOG.md",
           "buffer_dialog.py", "dem_editor.py", "dem_road_buffer.py", "map_tool.py", "road_output.py"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--output", default="dist/DEM_Editor_for_Road_and_Platform.zip")
    a = ap.parse_args()
    src, out = Path(a.source), Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(INCLUDE):
            info = zipfile.ZipInfo(f"{PLUGIN_FOLDER}/{name}", (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, (src / name).read_bytes())
    print(out)

main()
