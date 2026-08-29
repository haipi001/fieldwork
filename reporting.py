from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
FINAL = ROOT / "FINAL" / "SRC_AI_Security_Research_OS_FINAL_2026-08-27"
ADAPTERS = FINAL / "reports" / "adapters"
EXPORTS = ROOT / "data" / "exports"
SECRET_RE = re.compile(
    r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+|cookie\s*:\s*|api[-_ ]?key\s*[=:]\s*|password\s*[=:]\s*|private[-_ ]?key\s*[=:]\s*)([^\s,;]+)"
)


def redact(value: str) -> str:
    return SECRET_RE.sub(lambda m: m.group(1) + "[REDACTED]", value)


def adapter_config(platform: str) -> dict[str, Any]:
    aliases = {"markdown": "generic_src", "html": "generic_src", "json": "generic_src", "sarif": "generic_src"}
    path = ADAPTERS / f"{aliases.get(platform, platform)}.json"
    if not path.is_file():
        raise ValueError(f"未知报告适配器: {platform}")
    return json.loads(path.read_text())


def nested_get(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def universal_model(finding: dict[str, Any], evidence: list[dict[str, Any]], scope_id: str) -> dict[str, Any]:
    impact = finding.get("impact") or {}
    verification = finding.get("verification") or {}
    eligibility = finding.get("eligibility") or {}
    weakness_value = verification.get("weakness")
    weakness = weakness_value if isinstance(weakness_value, dict) else ({"id": weakness_value, "platform_taxonomy": weakness_value} if weakness_value else None)
    return {
        "id": finding["id"], "mode": finding["mode"], "title": finding["title"],
        "affected_asset": finding["target"], "location": verification.get("location"),
        "summary": verification.get("summary"), "root_cause": verification.get("root_cause"),
        "weakness": weakness, "severity": finding["severity"],
        "scope": {"snapshot_id": scope_id, "in_scope": eligibility.get("in_scope")},
        "reproduction": {
            "steps": verification.get("steps"), "expected": verification.get("expected"),
            "actual": verification.get("actual"), "poc_artifact_ids": verification.get("poc_artifact_ids"),
        },
        "impact": impact, "verification": verification, "eligibility": eligibility,
        "program_snapshot": eligibility.get("program_snapshot_id"),
        "impact_in_scope": eligibility.get("in_scope"),
        "known_issue_check": eligibility.get("known_issue_checked"),
        "previous_audit_check": eligibility.get("previous_audit_checked"),
        "primacy_mode": eligibility.get("primacy_mode") or "human-reviewed",
        "poc_rule_check": eligibility.get("poc_rule_checked"),
        "evidence_ids": [item["id"] for item in evidence],
        "evidence": [{"id": x["id"], "type": x["evidence_type"], "summary": redact(x["summary"]), "artifact_id": x.get("artifact_id")} for x in evidence],
        "remediation": verification.get("remediation"), "platform_custom": verification.get("platform_custom", {}),
    }


def completeness(model: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    required = [*config.get("required", []), *config.get("conditional", [])]
    missing = [field for field in required if nested_get(model, field) in (None, "", [], {})]
    recommended_missing = [field for field in config.get("recommended", []) if nested_get(model, field) in (None, "", [], {})]
    return {
        "ready": not missing, "required": required, "missing_required": missing,
        "missing_recommended": recommended_missing, "score": round(100 * (len(required) - len(missing)) / max(1, len(required))),
    }


def render_markdown(model: dict[str, Any], platform: str, check: dict[str, Any]) -> str:
    missing = "\n".join(f"- MISSING_REQUIRED_FIELD: `{x}`" for x in check["missing_required"])
    steps = model["reproduction"].get("steps")
    if isinstance(steps, list):
        steps = "\n".join(f"{i}. {redact(str(step))}" for i, step in enumerate(steps, 1))
    evidence = "\n".join(f"- `{x['id']}` {x['type']}: {x['summary']}" for x in model["evidence"]) or "MISSING_REQUIRED_FIELD: `evidence_ids`"
    return redact(f"""# {model['title']}

Platform adapter: {platform}\nFinding: {model['id']}\nStatus: {'READY_FOR_HUMAN_REVIEW' if check['ready'] else 'DRAFT_INCOMPLETE'}

## Target / Asset\n{model['affected_asset']}

## Vulnerable Location\n{model.get('location') or 'MISSING_REQUIRED_FIELD: `location`'}

## Summary\n{model.get('summary') or 'MISSING_REQUIRED_FIELD: `summary`'}

## Root Cause\n{model.get('root_cause') or 'MISSING_REQUIRED_FIELD: `root_cause`'}

## Steps to Reproduce\n{steps or 'MISSING_REQUIRED_FIELD: `reproduction.steps`'}

## Impact\n{model['impact'].get('description') or 'MISSING_REQUIRED_FIELD: `impact.description`'}

## Severity\n{model['severity']}

## Evidence\n{evidence}

## Completeness\n{missing or 'All required adapter fields are present.'}

> This package is generated for human review. It is never submitted automatically.
""")


def render(model: dict[str, Any], platform: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    config = adapter_config(platform)
    check = completeness(model, config)
    markdown = render_markdown(model, platform, check)
    if platform == "json":
        content = json.dumps({"report": model, "completeness": check}, ensure_ascii=False, indent=2)
    elif platform == "html":
        import html
        content = f"<!doctype html><meta charset=utf-8><title>{html.escape(model['title'])}</title><pre>{html.escape(markdown)}</pre>"
    elif platform == "sarif":
        applicable = model.get("mode") == "traditional" and model.get("location")
        content = json.dumps({"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "Security Research OS"}}, "results": ([{"ruleId": model.get("weakness") or "security-finding", "message": {"text": model["title"]}, "locations": []}] if applicable else [])}], "properties": {"applicable": bool(applicable)}}, ensure_ascii=False, indent=2)
    else:
        content = markdown
    return content, check, config


def export_bundle(package_id: str, model: dict[str, Any], platform: str, content: str, check: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    EXPORTS.mkdir(parents=True, exist_ok=True)
    path = EXPORTS / f"{package_id}.zip"
    manifest = {
        "package_id": package_id, "finding_id": model["id"], "platform": platform,
        "ready_for_human_review": check["ready"], "auto_submit": False,
        "files": ["report.md" if platform not in {"json", "html", "sarif"} else f"report.{platform}", "report.json", "completeness.json", "evidence_manifest.json"],
    }
    ext = platform if platform in {"json", "html", "sarif"} else "md"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"report.{ext}", redact(content))
        zf.writestr("report.json", json.dumps(model, ensure_ascii=False, indent=2))
        zf.writestr("completeness.json", json.dumps(check, ensure_ascii=False, indent=2))
        zf.writestr("evidence_manifest.json", json.dumps(model["evidence"], ensure_ascii=False, indent=2))
        zf.writestr("package_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    manifest["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return str(path), manifest
