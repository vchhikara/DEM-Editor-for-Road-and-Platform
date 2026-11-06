# Progress

## State (2026-11-07)

- Version in `metadata.txt`: 0.3.9
- Last pushed code commit: 4f2dfa4 on `main` (check `git log` for newer commits)
- Repository: public. Homepage, issues, and raw metadata URLs return HTTP 200.
- plugins.qgis.org: plugin ID 6879. Versions 0.3.8 and 0.3.9 uploaded.
  - 0.3.8: Security Scan Blocked (2 Bandit try/except/pass findings). Qt6 Check failed (2 enum names).
  - 0.3.9: Security Scan passed (0 issues). Qt6 Check passed ("No issues found").
- Approval: "Approved: no". A moderator must approve the version.

## Tested

- QGIS 4.2.3 (system install): 5 tests pass (load, initGui, unload, map tool, buffer, full Flat edit, full Road edit, missing DEM error). SciPy ran from a venv with system site packages.
- QGIS 3.x: not tested.
- `experimental=False` since 0.3.8.

## Open items

1. Wait for moderator approval of 0.3.9. Do not delete 0.3.8.
2. Remove unused variables `line_dx` and `line_dy` in `dem_editor.py` (flake8 F841 locally).
3. Add screenshots to the README after real images exist.
4. Test on QGIS 3.x.

## Commands

```bash
python3 scripts/package_plugin.py --output dist/DEM_Editor_for_Road_and_Platform.zip
python3 scripts/validate_plugin.py dist/DEM_Editor_for_Road_and_Platform.zip
QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python:/usr/lib/python3/dist-packages python -m pytest -q tests
```

The test Python needs GDAL, NumPy, SciPy and pytest. System Python lacks SciPy, so use a venv with `--system-site-packages`.

## Notes

- ZIP root folder: `DEM_Editor_for_Road_and_Platform`. The site URL id comes from this name.
- The ZIP leaves out `README.md` because its banner path does not exist inside the ZIP.
- Never add a co-author line to commits or pull requests.
