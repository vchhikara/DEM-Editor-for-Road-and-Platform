# Progress

## State (2026-11-16, milestone done)

- The owner reported that portal versions 0.4.0, 0.4.1, 0.4.2 and 0.4.3 are approved. I did not check the portal myself.
- Open items: test on QGIS 3.x, add README screenshots, publish the next release from `main` with `RELEASING.md`.

## State (2026-11-16, later)

- Portal versions 0.4.0 to 0.4.3 were deleted and re-uploaded with the `exec()` fix (the Qt6 Check had flagged `map_tool.py:56`). 0.3.8 and 0.3.9 were not touched.
- 0.4.3 comes from tag `v0.4.3-rc.3` (GitHub pre-release). 0.4.0 to 0.4.2 are repacked old builds with the same one-line fix.
- At the last read the Security Scan passed on 0.4.0 and was still validating on the others. The Qt6 Check was still running on all four. Re-read each version page for the results.
- Approved: no for all four.

## State (2026-11-16)

- plugins.qgis.org uploads on 2026-11-16: 0.4.0, 0.4.1, 0.4.2 (repacked old test builds, metadata only changed, silent handlers patched to log, QGIS 3.34-3.99, experimental=False) and 0.4.3 (from tag v0.4.3-rc.2, QGIS 3.34-4.99, experimental=True).
- Security Scan passed on all four. Qt6 Check failed on all four. For 0.4.3 the only issue is `exec_` in `map_tool.py:56`. Fixed on main, not yet released.
- Approved: no for all four. A moderator must approve them.
- Version 0.4.0 shows the changelog line `0.40` on the site. This is cosmetic. Portal versions cannot be re-uploaded under the same number.
- Real-terrain tests: `tests/real/` (36 tests, SF DEM at `/home/vipul/My Projects/Qto3D/tasks/live-test-scene/sf_dem_real.tif`). 41 tests pass on QGIS 4.2.3. QGIS 3.x is untested here.
- Run command: `QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python:/usr/lib/python3/dist-packages /tmp/demvenv/bin/python -m pytest -q tests`

## State (2026-11-15)

- Repo layout: `main` only, annotated tags `v0.3.8`, `v0.3.9`, `v0.4.3-rc.1`, one GitHub Release per tag with the ZIP as asset. No ZIP files in the tree. Steps are in `RELEASING.md`.
- v0.4.0 and v0.4.1 are not tagged. Their ZIPs say `0.40` and `0.40.1` inside and no source commit exists. ZIPs stay in git history (commits 2f11580, 1ee3490, f5354e3).
- `scripts/package_plugin.py` now includes every top-level `*.py`. The old allowlist dropped 12 modules.
- 0.4.3 is a work in progress on `main` (pre-release).

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
