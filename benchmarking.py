"""Versioned, deterministic scoring for Fieldwork's local logic benchmarks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _matches(result: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, value in expected.items():
        actual = result.get(key)
        if isinstance(value, dict) and set(value) == {"present"}:
            if bool(actual) is not bool(value["present"]):
                return False
        elif actual != value:
            return False
    return True


def score_logic_benchmark(
    manifest: dict[str, Any], results: list[dict[str, Any]], verified_findings: int = 0,
) -> dict[str, Any]:
    case_results = []
    for case in manifest["cases"]:
        matched = next((result for result in results if _matches(result, case["match"])), None)
        case_results.append({
            "id": case["id"], "class": case["class"], "passed": matched is not None,
            "matched_kind": matched.get("kind") if matched else None,
        })
    positives = [item for item in case_results if item["class"] == "positive"]
    controls = [item for item in case_results if item["class"] == "negative_control"]
    recall = sum(item["passed"] for item in positives) / len(positives) if positives else 1.0
    control_failure_rate = sum(not item["passed"] for item in controls) / len(controls) if controls else 0.0
    gates = manifest["gates"]
    passed = (
        recall >= float(gates["minimum_positive_recall"])
        and control_failure_rate <= float(gates["maximum_negative_control_failure_rate"])
        and verified_findings <= int(gates["maximum_verified_findings"])
    )
    return {
        "benchmark_id": manifest["id"], "version": manifest["version"], "passed": passed,
        "metrics": {
            "positive_recall": round(recall, 4),
            "negative_control_failure_rate": round(control_failure_rate, 4),
            "verified_findings": verified_findings,
        },
        "cases": case_results,
        "proof_gate_passed": verified_findings <= int(gates["maximum_verified_findings"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Score captured Campaign results against a versioned logic benchmark")
    parser.add_argument("results", type=Path, help="JSON file containing a results array or an object with a results field")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).parent / "benchmarks" / "logic-v3.json")
    parser.add_argument("--verified-findings", type=int, default=0)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    payload = json.loads(args.results.read_text())
    results = payload.get("results", payload) if isinstance(payload, dict) else payload
    score = score_logic_benchmark(manifest, results, args.verified_findings)
    print(json.dumps(score, ensure_ascii=False, indent=2))
    return 0 if score["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
