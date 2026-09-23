from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
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


SECRET_KEY = re.compile(r"(?i)^(?:authorization|proxy.authorization|cookie|set.cookie|password|passwd|secret|client.secret|api[ _-]?key|access[ _-]?token|refresh[ _-]?token|id[ _-]?token|token|private[ _-]?key)$")


def redact(value: str) -> str:
    value = SECRET_RE.sub(lambda m: m.group(1) + "[REDACTED]", value)
    value = re.sub(r"(?im)^((?:set-cookie|cookie|authorization|proxy-authorization)\s*:\s*).*$", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)([?&](?:api[_-]?key|access_token|refresh_token|token|password|secret)=)[^&#\s]+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", value)
    return value


def redact_structure(value: Any) -> Any:
    """Sanitize at the presentation/export boundary, without mutating stored evidence."""
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if SECRET_KEY.fullmatch(str(k)) else redact_structure(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_structure(v) for v in value]
    return redact(value) if isinstance(value, str) else value


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
    editorial = verification.get("editorial") or {}
    impact_editorial = impact.get("editorial") or {}
    report_impact = {**impact, **impact_editorial}
    eligibility = finding.get("eligibility") or {}
    weakness_value = verification.get("weakness")
    weakness = weakness_value if isinstance(weakness_value, dict) else ({"id": weakness_value, "platform_taxonomy": weakness_value} if weakness_value else None)
    return {
        "id": finding["id"], "mode": finding["mode"], "title": finding["title"],
        "affected_asset": finding["target"], "location": verification.get("location"),
        "summary": editorial.get("summary") or verification.get("summary"), "root_cause": verification.get("root_cause"),
        "weakness": weakness, "severity": finding["severity"],
        "scope": {"snapshot_id": scope_id, "in_scope": eligibility.get("in_scope")},
        "reproduction": {
            "steps": verification.get("steps"), "expected": verification.get("expected"),
            "actual": verification.get("actual"), "poc_artifact_ids": verification.get("poc_artifact_ids"),
        },
        "prerequisites": editorial.get("prerequisites"),
        "impact": report_impact, "verification": verification, "eligibility": eligibility,
        "counterevidence": {"summary": editorial.get("counterevidence_summary"),
                            "evidence_ids": editorial.get("counterevidence_ids")},
        "program_snapshot": eligibility.get("program_snapshot_id"),
        "impact_in_scope": eligibility.get("in_scope"),
        "known_issue_check": eligibility.get("known_issue_checked"),
        "previous_audit_check": eligibility.get("previous_audit_checked"),
        "primacy_mode": eligibility.get("primacy_mode") or "human-reviewed",
        "poc_rule_check": eligibility.get("poc_rule_checked"),
        "evidence_ids": [item["id"] for item in evidence],
        "evidence": [{"id": x["id"], "type": x["evidence_type"], "summary": redact(x["summary"]), "artifact_id": x.get("artifact_id")} for x in evidence],
        "remediation": editorial.get("remediation") or verification.get("remediation"),
        "platform_custom": editorial.get("platform_custom") or verification.get("platform_custom", {}),
    }


def completeness(model: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    required = [*config.get("required", []), *config.get("conditional", [])]
    missing = [field for field in required if nested_get(model, field) in (None, "", [], {})]
    recommended = list(dict.fromkeys([*config.get("recommended", []), "prerequisites",
        "impact.affected_users", "impact.conditions", "impact.evidence_ids",
        "counterevidence.summary", "counterevidence.evidence_ids", "remediation"]))
    recommended_missing = [field for field in recommended if nested_get(model, field) in (None, "", [], {})]
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
    prerequisites = "\n".join(f"- {redact(str(item))}" for item in (model.get("prerequisites") or []))
    impact_evidence = ", ".join(model.get("impact", {}).get("evidence_ids") or [])
    counterevidence_ids = ", ".join(model.get("counterevidence", {}).get("evidence_ids") or [])
    return redact(f"""# {model['title']}

Platform adapter: {platform}\nFinding: {model['id']}\nStatus: {'READY_FOR_HUMAN_REVIEW' if check['ready'] else 'DRAFT_INCOMPLETE'}

## Target / Asset\n{model['affected_asset']}

## Vulnerable Location\n{model.get('location') or 'MISSING_REQUIRED_FIELD: `location`'}

## Summary\n{model.get('summary') or 'MISSING_REQUIRED_FIELD: `summary`'}

## Root Cause\n{model.get('root_cause') or 'MISSING_REQUIRED_FIELD: `root_cause`'}

## Steps to Reproduce\n{steps or 'MISSING_REQUIRED_FIELD: `reproduction.steps`'}

## Prerequisites\n{prerequisites or 'MISSING_RECOMMENDED_FIELD: `prerequisites`'}

## Impact\n{model['impact'].get('description') or 'MISSING_REQUIRED_FIELD: `impact.description`'}

Affected users or assets: {model['impact'].get('affected_users') or 'MISSING_RECOMMENDED_FIELD: `impact.affected_users`'}

Conditions and limits: {model['impact'].get('conditions') or 'MISSING_RECOMMENDED_FIELD: `impact.conditions`'}

Impact evidence: {impact_evidence or 'MISSING_RECOMMENDED_FIELD: `impact.evidence_ids`'}

## Counterevidence / Negative Control\n{model.get('counterevidence', {}).get('summary') or 'MISSING_RECOMMENDED_FIELD: `counterevidence.summary`'}

Evidence: {counterevidence_ids or 'MISSING_RECOMMENDED_FIELD: `counterevidence.evidence_ids`'}

## Severity\n{model['severity']}

## Evidence\n{evidence}

## Remediation\n{model.get('remediation') or 'MISSING_RECOMMENDED_FIELD: `remediation`'}

## Completeness\n{missing or 'All required adapter fields are present.'}

> This package is generated for human review. It is never submitted automatically.
""")


def render(model: dict[str, Any], platform: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    model = redact_structure(model)
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


def export_bundle(package_id: str, model: dict[str, Any], platform: str, content: str, check: dict[str, Any],
                  attachments: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    EXPORTS.mkdir(parents=True, exist_ok=True)
    path = EXPORTS / f"{package_id}.zip"
    model, check = redact_structure(model), redact_structure(check)
    encode = lambda value: json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    ext = platform if platform in {"json", "html", "sarif"} else "md"
    if ext in {"json", "sarif"}:
        rendered = encode(redact_structure(json.loads(content)))
    else:
        rendered = redact(content).encode("utf-8")
    # The canonical model and platform rendering must never share the same ZIP name.
    rendered_name = "platform-report.json" if ext == "json" else f"report.{ext}"
    files = {
        rendered_name: rendered,
        "report.json": encode(model),
        "completeness.json": encode(check),
        "evidence_manifest.json": encode(model["evidence"]),
    }
    for name, value in (attachments or {}).items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or name in files or name == "package_manifest.json":
            raise ValueError("Invalid or duplicate proof attachment name")
        files[name] = encode(redact_structure(value)) if not isinstance(value, str) else redact(value).encode("utf-8")
    replay_contract = (attachments or {}).get("proof/replay-contract.json")
    replay_summary = None
    if isinstance(replay_contract, dict):
        replay_summary = {
            "schema": replay_contract.get("schema"), "mode": replay_contract.get("mode"),
            "kind": (replay_contract.get("assertions") or {}).get("kind"),
            "active_execution_supported": replay_contract.get("mode") == "isolated_local_execution",
        }
    manifest = {
        "schema": "fieldwork-package/2", "package_id": package_id,
        "finding_id": model["id"], "platform": platform,
        "ready_for_human_review": check["ready"], "auto_submit": False,
        "proof_replay": replay_summary,
        "files": [*files, "package_manifest.json"],
        "checksums": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        "integrity_note": "Checksums detect corruption; they do not establish authenticity or successful replay.",
    }
    files["package_manifest.json"] = encode(manifest)
    temporary = path.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in files.items():
                zf.writestr(name, data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    manifest["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return str(path), manifest


def verify_bundle(path: Path) -> dict[str, Any]:
    """Verify without extracting or executing anything from an untrusted archive."""
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if len(names) != len(set(names)) or sum(i.file_size for i in zf.infolist()) > 100_000_000:
            raise ValueError("Duplicate entries or oversized proof bundle")
        if any(Path(n).is_absolute() or ".." in Path(n).parts or "\\" in n for n in names):
            raise ValueError("Unsafe archive path")
        manifest = json.loads(zf.read("package_manifest.json"))
        if manifest.get("schema") != "fieldwork-package/2" or set(names) != set(manifest.get("files", [])):
            raise ValueError("Proof manifest does not match archive")
        checksums = manifest.get("checksums", {})
        if set(checksums) != set(names) - {"package_manifest.json"}:
            raise ValueError("Incomplete checksum manifest")
        for name, digest in checksums.items():
            if hashlib.sha256(zf.read(name)).hexdigest() != digest:
                raise ValueError(f"Proof checksum mismatch: {name}")
    return {"integrity_ok": True, "file_count": len(names), "finding_id": manifest["finding_id"],
            "replay_performed": False, "authenticity_verified": False}


def replay_bundle(path: Path, execute: bool = False) -> dict[str, Any]:
    """Evaluate a bundled proof contract; execute only an explicitly requested local Foundry replay."""
    integrity = verify_bundle(path)
    with zipfile.ZipFile(path) as zf:
        if "proof/replay-contract.json" not in zf.namelist():
            raise ValueError("Proof bundle has no replay contract")
        contract = json.loads(zf.read("proof/replay-contract.json"))
        if contract.get("schema") != "fieldwork-replay-contract/1":
            raise ValueError("Unsupported replay contract")
        artifacts = []
        for artifact_id, name in contract.get("artifact_files", {}).items():
            if name not in zf.namelist() or not name.startswith("proof/evidence/"):
                raise ValueError(f"Replay artifact missing: {artifact_id}")
            artifacts.append(json.loads(zf.read(name)))
        assertions = contract.get("assertions") or {}
        kind = assertions.get("kind")
        recorded_ok = False
        recorded = {"kind": kind, "artifact_count": len(artifacts)}
        if kind == "http_authorization_read_v2":
            minimum = int(assertions.get("minimum_rounds", 2))
            recorded_ok = bool(artifacts) and all(
                item.get("oracle") == "http-authorization-read-v2"
                and len(item.get("rounds", [])) >= minimum
                and item.get("reproduced") is True and item.get("stable") is True
                and len(item.get("semantic_checks", [])) >= minimum
                and all(check.get("passed") is True for check in item.get("semantic_checks", []))
                for item in artifacts
            )
        elif kind == "forge_property_replay_v1":
            property_name = str(assertions.get("property") or "")
            base_name = property_name.split("(", 1)[0]
            reasons, seeds, counterexamples, rounds = [], [], [], 0
            for item in artifacts:
                for replay in item.get("rounds", []):
                    match = next((test for test in replay.get("tests", [])
                                  if str(test.get("name", "")).split("(", 1)[0] == base_name), None)
                    if match and str(match.get("status", "")).lower() not in {"success", "passed"}:
                        rounds += 1
                        reasons.append(match.get("reason"))
                        seeds.append(replay.get("seed"))
                        counterexamples.append(match.get("counterexample"))
            minimum = int(assertions.get("minimum_rounds", 2))
            recorded_ok = (rounds >= minimum and len(set(seeds)) >= minimum
                           and all(counterexamples) and len(set(reasons)) == 1 and all(reasons))
            recorded.update({"rounds": rounds, "distinct_seeds": len(set(seeds)), "failure_reasons": sorted(set(reasons))})
        elif kind == "ptai_recorded_replay":
            successes = []
            for item in artifacts:
                replay = item.get("replay") or {}
                value = str(replay.get("replay") or "")
                try:
                    success, total = [int(part) for part in value.split("/", 1)]
                except (ValueError, AttributeError):
                    success, total = 0, 0
                successes.append(replay.get("integrity_ok") is True and replay.get("verdict") == "verified"
                                 and success == total and total >= int(assertions.get("minimum_successful_rounds", 2)))
            recorded_ok = bool(successes) and all(successes)
        else:
            raise ValueError("Replay adapter is not supported")
        if not recorded_ok:
            raise ValueError("Recorded proof does not satisfy replay assertions")
        execution = {"requested": execute, "performed": False, "supported": contract.get("mode") == "isolated_local_execution"}
        if execute:
            if contract.get("mode") != "isolated_local_execution" or kind != "forge_property_replay_v1":
                raise ValueError("This proof supports recorded verification only")
            from web3_lab import binary
            forge = binary("forge")
            if not forge:
                raise ValueError("Forge is required for isolated replay")
            from web3_analysis import parse_forge_test_json
            with tempfile.TemporaryDirectory(prefix="fieldwork-replay-") as temporary:
                root = Path(temporary)
                prefix = "proof/source/"
                source_names = [name for name in zf.namelist() if name.startswith(prefix) and not name.endswith("/")]
                if not source_names:
                    raise ValueError("Portable source tree is missing")
                for name in source_names:
                    relative = Path(name.removeprefix(prefix))
                    destination = root / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(zf.read(name))
                outputs = []
                property_base = str(assertions["property"]).split("(", 1)[0]
                for command in contract.get("commands", []):
                    if not isinstance(command, list) or not command or command[0] != "forge":
                        raise ValueError("Unsafe replay command")
                    argv = [forge, *[str(value) for value in command[1:]]]
                    process = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=600, shell=False)
                    tests = [item for item in parse_forge_test_json(process.stdout)
                             if str(item.get("name", "")).split("(", 1)[0] == property_base]
                    failed = [item for item in tests if str(item.get("status", "")).lower() not in {"success", "passed"}
                              and item.get("counterexample")]
                    if not failed:
                        raise ValueError("Isolated Forge replay did not reproduce the named property failure")
                    outputs.append({"exit_code": process.returncode, "matched_failures": len(failed),
                                    "reason": failed[0].get("reason")})
                if len({item["reason"] for item in outputs}) != 1:
                    raise ValueError("Isolated Forge replay failure reason was unstable")
                execution.update({"performed": True, "rounds": outputs})
    return {**integrity, "recorded_replay_verified": True, "replay_performed": execution["performed"],
            "replay_kind": kind, "recorded": recorded, "execution": execution}
