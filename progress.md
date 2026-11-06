# Progress

## State (2026-11-07)

- Version in `metadata.txt`: 0.3.8
- Last pushed commit: aeb240b on `main` (check `git log` for newer commits)
- Repository: public. Homepage, issues, and raw metadata URLs return HTTP 200.
- plugins.qgis.org: NOT uploaded yet. The upload was paused for the owner's confirmation of the 6 checklist statements.
- Approval: not applicable until an upload exists.

## Tested

- QGIS 4.2.3 (system install): 5 tests pass (load, initGui, unload, map tool, buffer, full Flat edit, full Road edit, missing DEM error). SciPy ran from a venv with system site packages.
- QGIS 3.x: not tested.
- `experimental=False` since 0.3.8, after the full edit tests passed.

## Open items

1. Upload `dist/DEM_Editor_for_Road_and_Platform.zip` and read the Security Scan and Qt6 Check results.
2. Fix any reported errors, raise the version, repackage, push, upload again.
4. Add screenshots to the README after real images exist.

## Commands

```bash
python3 scripts/package_plugin.py --output dist/DEM_Editor_for_Road_and_Platform.zip
python3 scripts/validate_plugin.py dist/DEM_Editor_for_Road_and_Platform.zip
QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python:/usr/lib/python3/dist-packages /usr/bin/python3 -m pytest -q tests
```

## Notes

- ZIP root folder: `DEM_Editor_for_Road_and_Platform`. The site URL id comes from this name.
- The ZIP leaves out `README.md` because its banner path does not exist inside the ZIP.
- Never add a co-author line to commits or pull requests.
