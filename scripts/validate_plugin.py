"""Validate a plugin ZIP against the QGIS repository rules."""
import sys, zipfile, configparser, io

REQUIRED = ["name","qgisMinimumVersion","description","about","version","author","email","repository"]
zp = sys.argv[1]
z = zipfile.ZipFile(zp)
names = z.namelist()
roots = {n.split("/")[0] for n in names}
errs = []
if len(roots) != 1: errs.append(f"need one root folder, found {roots}")
root = next(iter(roots))
for f in ("__init__.py","metadata.txt","LICENSE","icon.png"):
    if f"{root}/{f}" not in names: errs.append(f"missing {f}")
for n in names:
    if "__pycache__" in n or n.endswith(".pyc") or ".git" in n or ".." in n: errs.append(f"bad path {n}")
cp = configparser.ConfigParser(); cp.read_string(z.read(f"{root}/metadata.txt").decode())
for k in REQUIRED:
    if not cp["general"].get(k): errs.append(f"metadata key {k} empty")
if "classFactory" not in z.read(f"{root}/__init__.py").decode(): errs.append("no classFactory")
size = sum(i.compress_size for i in z.infolist())
if size > 25*1024*1024: errs.append("ZIP over 25 MB")
print("FAIL" if errs else "PASS", errs or f"{len(names)} files, {size} bytes")
sys.exit(1 if errs else 0)
