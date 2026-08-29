from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from web3_lab import binary
from capability_registry import execute


router = APIRouter(prefix="/api/v1/web3", tags=["Web3 analysis"])
ARTIFACT_ROOT = Path(__file__).resolve().parent / "data" / "artifacts"
CONTRACT_RE = re.compile(r"\b(?:abstract\s+)?contract\s+(\w+)(?:\s+is\s+([^\{]+))?")


class SourceInspectInput(BaseModel):
    engagement_id: str
    run_id: str
    source_path: str
    source_commit: str | None = None
    deployed_address: str | None = None
    deployed_bytecode_hash: str | None = None


class Web3ToolInput(BaseModel):
    engagement_id: str
    run_id: str
    source_path: str
    args: list[str] = Field(default_factory=list)


def detect_framework(root: Path) -> str:
    if (root / "foundry.toml").is_file():
        return "foundry"
    if any((root / name).is_file() for name in ("hardhat.config.js", "hardhat.config.ts", "hardhat.config.cjs")):
        return "hardhat"
    return "solidity"


def solidity_sources(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.sol") if not any(part in {"node_modules", "lib", "out", "cache"} for part in p.parts))


def source_model(root: Path) -> dict[str, Any]:
    contracts, edges, invariants = [], [], []
    for path in solidity_sources(root):
        text = path.read_text(errors="replace")
        for match in CONTRACT_RE.finditer(text):
            name = match.group(1)
            bases = [x.strip().split("(")[0] for x in (match.group(2) or "").split(",") if x.strip()]
            contracts.append({"name": name, "source": str(path.relative_to(root)), "sha256": hashlib.sha256(text.encode()).hexdigest()})
            edges.extend({"source": name, "target": base, "type": "inherits"} for base in bases)
        lower = text.lower()
        if "delegatecall" in lower or "upgrade" in lower:
            invariants.append({"category": "upgradeability", "statement": "Only authorized governance may change implementation"})
        if any(term in lower for term in ("deposit", "withdraw", "totalassets", "totalsupply")):
            invariants.append({"category": "accounting", "statement": "Asset/share accounting remains conserved across state transitions"})
        if any(term in lower for term in ("onlyowner", "accesscontrol", "hasrole")):
            invariants.append({"category": "authorization", "statement": "Privileged state transitions require the intended role"})
    return {"contracts": contracts, "relationships": edges, "invariants": invariants}


def run_forge_build(root: Path) -> dict[str, Any]:
    forge = binary("forge")
    if not forge:
        return {"status": "degraded", "reason": "forge_missing"}
    # Production compilation is deliberately isolated from test fixtures. A
    # broken Echidna harness must not hide whether the deployable contracts
    # themselves compile.
    result = subprocess.run(
        [forge, "build", "--root", str(root), "--no-lint", "--skip", "test", "--skip", "script"],
        capture_output=True, text=True, timeout=600,
    )
    output = (result.stdout + "\n" + result.stderr)[-12000:]
    return {"status": "compiled" if result.returncode == 0 else "failed", "exit_code": result.returncode, "output": output}


def run_forge_tests(root: Path) -> dict[str, Any]:
    forge = binary("forge")
    if not forge:
        return {"status": "degraded", "reason": "forge_missing"}
    result = subprocess.run(
        [forge, "test", "--root", str(root), "--fuzz-runs", "64", "--skip", "echidna"],
        capture_output=True, text=True, timeout=600,
    )
    output = (result.stdout + "\n" + result.stderr)[-12000:]
    return {"status": "passed" if result.returncode == 0 else "failed", "exit_code": result.returncode, "runs": 64, "output": output}


def compiler_model(root: Path) -> list[dict[str, Any]]:
    normalized = []
    for path in sorted((root / "out").rglob("*.json")) if (root / "out").is_dir() else []:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if "abi" not in data or "bytecode" not in data:
            continue
        bytecode = data.get("bytecode", {}).get("object", "")
        ast = data.get("ast") or {}
        normalized.append({
            "contract": path.stem, "artifact": str(path.relative_to(root)), "abi": data.get("abi", []),
            "bytecode_sha256": hashlib.sha256(bytecode.encode()).hexdigest() if bytecode else None,
            "bytecode_bytes": max(0, (len(bytecode) - 2) // 2) if bytecode else 0,
            "ast": {"nodeType": ast.get("nodeType"), "src": ast.get("src"), "nodes": len(ast.get("nodes", []))},
        })
    return normalized


@router.post("/source/inspect")
def inspect_source(body: SourceInspectInput):
    import final_core
    engagement = final_core.get_engagement(body.engagement_id)
    if engagement["mode"] != "web3":
        raise HTTPException(409, "源码归一化仅用于 Web3 Engagement")
    with final_core.connect() as db:
        run = db.execute("SELECT * FROM analysis_runs WHERE id=? AND engagement_id=?", (body.run_id, body.engagement_id)).fetchone()
    if not run:
        raise HTTPException(409, "源码分析必须绑定当前 Engagement 的 Run")
    root = Path(body.source_path).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(422, "source_path 必须是存在的本地目录")
    sources = solidity_sources(root)
    if not sources:
        raise HTTPException(422, "未发现 Solidity 源码")
    framework = detect_framework(root)
    model = source_model(root)
    compile_result = run_forge_build(root) if framework == "foundry" else {"status": "not_run", "reason": f"{framework}_adapter_not_installed"}
    fuzz_result = run_forge_tests(root) if framework == "foundry" and compile_result["status"] == "compiled" else {"status": "not_run"}
    compiler = compiler_model(root) if compile_result["status"] == "compiled" else []
    artifact_id = final_core.uid("artifact")
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    artifact_path = ARTIFACT_ROOT / f"{artifact_id}.json"
    artifact = {
        "framework": framework, "source_commit": body.source_commit,
        "deployed_address": body.deployed_address, "deployed_bytecode_hash": body.deployed_bytecode_hash,
        "source_files": len(sources), "compile": compile_result, "fuzz": fuzz_result,
        "compiler_model": compiler, "model": model,
    }
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2))
    with final_core.connect() as db:
        db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (
            artifact_id, body.run_id, "web3.source_model", str(artifact_path),
            hashlib.sha256(artifact_path.read_bytes()).hexdigest(), "application/json", 1, final_core.utcnow(),
        ))
        for contract in model["contracts"]:
            db.execute("""INSERT OR IGNORE INTO entities VALUES(?,?,?,?,?,?,?)""", (
                final_core.uid("entity"), body.engagement_id, "contract", f"contract:{contract['name']}",
                contract["name"], final_core.dump(contract), final_core.utcnow(),
            ))
        entity_rows = {row["label"]: row["id"] for row in db.execute("SELECT id,label FROM entities WHERE engagement_id=?", (body.engagement_id,))}
        for edge in model["relationships"]:
            if edge["source"] in entity_rows and edge["target"] in entity_rows:
                db.execute("INSERT INTO relationships VALUES(?,?,?,?,?,?,?)", (
                    final_core.uid("rel"), body.engagement_id, entity_rows[edge["source"]], entity_rows[edge["target"]],
                    edge["type"], final_core.dump([artifact_id]), final_core.utcnow(),
                ))
        for invariant in model["invariants"]:
            db.execute("INSERT INTO invariant_registry VALUES(?,?,?,?,?,?,?)", (
                final_core.uid("invariant"), body.engagement_id, invariant["category"], invariant["statement"],
                "source-heuristic", "candidate", final_core.utcnow(),
            ))
        observation_id = final_core.uid("obs")
        db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            observation_id, body.run_id, body.engagement_id, "web3", "web3.compiler_result",
            str(root), f"{framework} compile={compile_result['status']} fuzz={fuzz_result['status']}",
            1.0 if compile_result["status"] == "compiled" else .2, "forge", artifact_id, final_core.utcnow(),
        ))
    return {"artifact_id": artifact_id, "observation_id": observation_id, **artifact}


@router.post("/tools/{capability_id}/run")
def run_web3_tool(capability_id: str, body: Web3ToolInput):
    import final_core
    allowed = {"forge", "slither", "aderyn", "echidna", "medusa", "halmos"}
    if capability_id not in allowed:
        raise HTTPException(422, "该 capability 不是可用的 Web3 源码工具")
    engagement = final_core.get_engagement(body.engagement_id)
    if engagement["mode"] != "web3":
        raise HTTPException(409, "Web3 tool 仅能在 Web3 Engagement 中运行")
    with final_core.connect() as db:
        run = db.execute("SELECT * FROM analysis_runs WHERE id=? AND engagement_id=?", (body.run_id, body.engagement_id)).fetchone()
    if not run:
        raise HTTPException(409, "Tool execution 必须绑定当前 Engagement 的 Run")
    root = Path(body.source_path).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(422, "source_path 必须是存在的本地目录")
    consumed, reason = final_core.consume_run_budget(body.run_id, "tool_call", 1)
    if not consumed:
        raise HTTPException(409, reason)
    envelope = execute(capability_id, body.args, root, timeout=300)
    if envelope.status == "unavailable":
        raise HTTPException(409, f"{capability_id} capability degraded/unavailable")
    artifact_id = final_core.uid("artifact")
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    artifact_path = ARTIFACT_ROOT / f"{artifact_id}.json"
    artifact_path.write_text(json.dumps(envelope.__dict__, ensure_ascii=False, indent=2))
    detector_blocks = []
    if capability_id == "slither":
        combined = f"{envelope.stdout}\n{envelope.stderr}"
        try:
            slither_json = json.loads(envelope.stdout)
            for detector in slither_json.get("results", {}).get("detectors", []):
                detector_blocks.append((
                    detector.get("check", "detector"),
                    detector.get("description") or detector.get("markdown") or "Slither detector result",
                ))
        except (json.JSONDecodeError, AttributeError):
            detector_blocks = re.findall(r"Detector:\s*([^\n]+)\n(.*?)(?=\nReference:)", combined, flags=re.S)
    elif capability_id == "echidna":
        try:
            json_start = envelope.stdout.rfind('{"coverage"')
            echidna_json = json.loads(envelope.stdout[json_start:] if json_start >= 0 else envelope.stdout)
            detector_blocks = [
                (f"{test.get('name', 'property')}#{index + 1}", f"Echidna property {test.get('status', 'unknown')}" + (f": {test['reason']}" if test.get("reason") else ""))
                for index, test in enumerate(echidna_json.get("tests", []))
            ]
        except (json.JSONDecodeError, AttributeError):
            detector_blocks = []
    elif capability_id == "medusa":
        combined = f"{envelope.stdout}\n{envelope.stderr}"
        detector_blocks = [
            (name.strip(), f"Medusa assertion {status.lower()}")
            for status, name in re.findall(r"\[(PASSED|FAILED)\]\s+Assertion Test:\s*([^\n]+)", combined)
        ]
    with final_core.connect() as db:
        db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (
            artifact_id, body.run_id, f"web3.tool.{capability_id}", str(artifact_path),
            hashlib.sha256(artifact_path.read_bytes()).hexdigest(), "application/json", 1, final_core.utcnow(),
        ))
        observation_ids = []
        blocks = detector_blocks or [("tool-result", f"{capability_id} status={envelope.status} exit={envelope.exit_code}")]
        for detector, detail in blocks:
            observation_id = final_core.uid("obs")
            observation_ids.append(observation_id)
            summary = re.sub(r"\s+", " ", detail).strip()[:1000]
            db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                observation_id, body.run_id, body.engagement_id, "web3", f"web3.{capability_id}.{detector.strip()}",
                str(root), summary or f"{capability_id} detector={detector.strip()}",
                .8 if envelope.status == "completed" else .2, capability_id, artifact_id, final_core.utcnow(),
            ))
    return {"artifact_id": artifact_id, "observation_id": observation_ids[0], "observation_ids": observation_ids, "observation_count": len(observation_ids), "tool_result": envelope.__dict__}


def execute_repository_pipeline(engagement_id: str, run_id: str, source_path: str, source_commit: str | None = None) -> dict[str, Any]:
    """Run the deterministic Web3 source pipeline for a checked-out repository."""
    import final_core
    from traditional_tools import record_coverage

    root = Path(source_path).resolve()
    final_core.add_event(run_id, "analysis", "web3.pipeline.started", "Web3 生产合约编译与安全分析已启动", {"source_path": str(root)})
    inspected = inspect_source(SourceInspectInput(
        engagement_id=engagement_id, run_id=run_id, source_path=str(root), source_commit=source_commit,
    ))
    compile_status = inspected["compile"]["status"]
    fuzz_status = inspected["fuzz"]["status"]
    record_coverage(run_id, "capability:forge-build", "tested" if compile_status == "compiled" else "not_tested", compile_status, [inspected["observation_id"]])
    record_coverage(run_id, "capability:forge-test", "tested" if fuzz_status == "passed" else "not_tested", fuzz_status, [inspected["observation_id"]])
    final_core.add_event(run_id, "analysis", "web3.compile.completed", f"生产合约编译 {compile_status}；Forge 测试 {fuzz_status}", {"artifact_id": inspected["artifact_id"]})

    results: dict[str, Any] = {"inspect": inspected, "tools": {}}
    tool_args = {
        "slither": [".", "--hardhat-ignore-compile", "--filter-paths", "contracts/test/|test/", "--exclude-dependencies", "--json", "-"],
        "aderyn": [".", "--output", f"/tmp/fieldwork-aderyn-{run_id}.json"],
        "halmos": ["--root", ".", "--solver-timeout-assertion", "5000", "--early-exit", "--no-status"],
    }
    echidna_root = root / "test" / "echidna"
    harnesses = sorted(echidna_root.glob("*Echidna.sol")) if echidna_root.is_dir() else []
    configs = sorted(echidna_root.glob("*.yaml")) if echidna_root.is_dir() else []
    if harnesses:
        harness = str(harnesses[0].relative_to(root))
        contract = harnesses[0].stem
        echidna_args = [harness, "--contract", contract, "--timeout", "60", "--test-limit", "1000", "--workers", "2", "--format", "json"]
        if configs:
            preferred_config = echidna_root / "echidna.yaml"
            selected_config = preferred_config if preferred_config.is_file() else configs[0]
            echidna_args.extend(["--config", str(selected_config.relative_to(root))])
        tool_args["echidna"] = echidna_args
        tool_args["medusa"] = [
            "fuzz", "--compilation-target", harness, "--target-contracts", contract,
            "--timeout", "60", "--test-limit", "1000", "--workers", "2", "--no-color",
        ]
    for capability, args in tool_args.items():
        try:
            result = run_web3_tool(capability, Web3ToolInput(
                engagement_id=engagement_id, run_id=run_id, source_path=str(root), args=args,
            ))
            envelope = result["tool_result"]
            status = envelope["status"]
            record_coverage(run_id, f"capability:{capability}", "tested" if status == "completed" else "not_tested", status, result["observation_ids"])
            final_core.add_event(run_id, "analysis", f"web3.{capability}.completed", f"{capability} 执行状态：{status}，记录 {result['observation_count']} 个检测项", {"artifact_id": result["artifact_id"], "exit_code": envelope["exit_code"]})
            results["tools"][capability] = result
        except HTTPException as error:
            record_coverage(run_id, f"capability:{capability}", "not_tested", str(error.detail))
            final_core.add_event(run_id, "analysis", f"web3.{capability}.failed", f"{capability}: {error.detail}")
            results["tools"][capability] = {"status": "failed", "reason": str(error.detail)}
    return results
