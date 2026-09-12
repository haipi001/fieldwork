from pathlib import Path
import json, hashlib, sys

root = Path(__file__).resolve().parents[1]
manifest_path = root / "PACKAGE_MANIFEST.json"
if not manifest_path.exists():
    print("Manifest not found")
    sys.exit(1)

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
bad = []
for item in manifest["files"]:
    p = root / item["path"]
    if not p.exists():
        bad.append((item["path"], "missing"))
        continue
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    if digest != item["sha256"]:
        bad.append((item["path"], "hash mismatch"))

if bad:
    print("FAIL")
    for x in bad: print(x)
    sys.exit(1)

print(f"PASS: {len(manifest['files'])} files verified")
