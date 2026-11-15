# Releasing

## Layout

- `main` is the only long-lived branch. Every commit on `main` passes the tests.
- Work happens on short-lived branches named `feature/<topic>` or `fix/<topic>`. Merge them into `main` with a pull request.
- A release is an annotated tag named `vMAJOR.MINOR.PATCH` on a commit of `main`. A pre-release uses a suffix, for example `v0.4.3-rc.1`.
- Each tag has a GitHub Release. The Release carries the plugin ZIP as its asset.
- The repository holds no ZIP files. `dist/` and `*.zip` are ignored.
- For a fix to an old version, create a branch `release/0.3` from its tag. Tag the fix from that branch.

## Version rules

- Versions follow Semantic Versioning: MAJOR.MINOR.PATCH. Write `0.4.1`, not `0.41`.
- `metadata.txt` `version=` equals the tag without the leading `v`. A pre-release tag `v0.4.3-rc.1` ships `version=0.4.3`.
- QGIS compares version parts as numbers. `0.40` is higher than `0.4.3`. Never lower a published version.
- Set `experimental=True` for every pre-release.

## Steps

1. Update `CHANGELOG.md`. Move items from `[Unreleased]` to a new dated section. Update the compare links.
2. Set `version=` and the `changelog=` lines in `metadata.txt`.
3. Run the tests:
   ```bash
   QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python:/usr/lib/python3/dist-packages python -m pytest -q tests
   ```
4. Commit to `main` with the message `Release X.Y.Z`.
5. Tag the commit:
   ```bash
   git tag -a vX.Y.Z -m "Release X.Y.Z"
   ```
6. Build and validate the ZIP from the tagged commit:
   ```bash
   python3 scripts/package_plugin.py --output dist/DEM_Editor_for_Road_and_Platform-vX.Y.Z.zip
   python3 scripts/validate_plugin.py dist/DEM_Editor_for_Road_and_Platform-vX.Y.Z.zip
   ```
7. Push the commit and the tag:
   ```bash
   git push origin main vX.Y.Z
   ```
8. Create the GitHub Release from the tag. Attach the ZIP. Add `--prerelease` for a pre-release:
   ```bash
   gh release create vX.Y.Z dist/DEM_Editor_for_Road_and_Platform-vX.Y.Z.zip --title "X.Y.Z" --notes-file <notes>
   ```
9. Upload the same ZIP to plugins.qgis.org. See [PUBLISHING.md](PUBLISHING.md) for the checks.
