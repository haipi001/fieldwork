from pathlib import Path
import json, sys

root = Path(__file__).resolve().parents[1]

required = [
    "README.md",
    "codex/FINAL_MASTER_PROMPT.md",
    "tasks/FINAL_TASKS.json",
    "schemas/CanonicalFinding.schema.json",
    "reports/adapters/hackerone.json",
    "reports/adapters/bugcrowd.json",
    "reports/adapters/intigriti.json",
    "reports/adapters/immunefi.json",
    "prototype/index.html",
]
missing = [x for x in required if not (root/x).exists()]
if missing:
    print("Missing:", missing)
    sys.exit(1)

tasks=json.loads((root/"tasks/FINAL_TASKS.json").read_text(encoding="utf-8"))
ids={t["id"] for t in tasks}
baddeps=[]
for t in tasks:
    for d in t.get("dependencies",[]):
        if d not in ids:
            baddeps.append((t["id"], d))
if baddeps:
    print("Bad task dependencies:", baddeps)
    sys.exit(1)

finding=json.loads((root/"schemas/CanonicalFinding.schema.json").read_text(encoding="utf-8"))
for key in ["title","status","impact","scope","evidence_ids"]:
    if key not in finding["properties"]:
        print("Finding schema missing:", key)
        sys.exit(1)

print(f"PASS: static contracts; {len(tasks)} tasks")
