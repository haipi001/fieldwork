from __future__ import annotations

import hashlib
import json
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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


class DeploymentAlignmentInput(BaseModel):
    engagement_id: str
    source_path: str
    rpc_url: str
    contract_address: str
    artifact_contract: str | None = None
    block_number: int | None = Field(default=None, ge=0)
    expected_chain_id: int | None = Field(default=None, ge=1)


EIP1967_IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"


def _evm_address(value: str) -> str:
    address = value.strip().lower()
    if not re.fullmatch(r"0x[0-9a-f]{40}", address):
        raise HTTPException(422, "无效 EVM 合约地址")
    return address


def _rpc_endpoint(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.username or parsed.password:
        raise HTTPException(422, "RPC URL 不能包含内嵌凭据")
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
        raise HTTPException(422, "远程 RPC 必须使用 HTTPS；本机 RPC 可使用 localhost")
    if not parsed.hostname:
        raise HTTPException(422, "无效 RPC URL")
    return value.strip()


def _rpc_call(endpoint: str, method: str, params: list[Any]) -> Any:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read(2_000_000))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise HTTPException(409, f"RPC 只读检查失败：{type(error).__name__}") from error
    if payload.get("error"):
        message = str(payload["error"].get("message", "RPC error"))[:300]
        raise HTTPException(409, f"RPC 只读检查失败：{message}")
    return payload.get("result")


def _hex_bytes(value: str | None) -> bytes:
    raw = (value or "").removeprefix("0x")
    if not raw:
        return b""
    try:
        return bytes.fromhex(raw)
    except ValueError as error:
        raise HTTPException(409, "RPC 或编译产物返回了无效 bytecode") from error


def deployment_artifacts(root: Path) -> list[dict[str, Any]]:
    values = []
    for path in sorted((root / "out").rglob("*.json")) if (root / "out").is_dir() else []:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        deployed = data.get("deployedBytecode", {}).get("object", "")
        code = _hex_bytes(deployed)
        if not code:
            continue
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        compiler = metadata.get("compiler") if isinstance(metadata.get("compiler"), dict) else {}
        settings = metadata.get("settings") if isinstance(metadata.get("settings"), dict) else {}
        optimizer = settings.get("optimizer") if isinstance(settings.get("optimizer"), dict) else {}
        values.append({
            "contract": path.stem,
            "artifact": str(path.relative_to(root)),
            "runtime_bytecode_sha256": hashlib.sha256(code).hexdigest(),
            "runtime_bytecode_bytes": len(code),
            "compiler_version": compiler.get("version"),
            "optimizer_enabled": optimizer.get("enabled"),
            "optimizer_runs": optimizer.get("runs"),
            "evm_version": settings.get("evmVersion"),
        })
    return values


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


@router.post("/deployment-alignments", status_code=201)
def align_deployment(body: DeploymentAlignmentInput):
    """Bind a local build artifact to read-only chain state without persisting the RPC URL."""
    import final_core

    engagement = final_core.get_engagement(body.engagement_id)
    if engagement["mode"] != "web3" or engagement.get("target_type") != "contract":
        raise HTTPException(409, "部署对齐必须绑定 Web3 合约地址项目")
    address = _evm_address(body.contract_address)
    if address != engagement["normalized_target"].lower():
        raise HTTPException(409, "合约地址不属于当前冻结 Scope")
    root = Path(body.source_path).expanduser().resolve()
    if not root.is_dir() or not solidity_sources(root):
        raise HTTPException(422, "source_path 必须包含 Solidity 源码")
    endpoint = _rpc_endpoint(body.rpc_url)
    compile_result = run_forge_build(root) if detect_framework(root) == "foundry" else {"status": "not_run"}
    if compile_result.get("status") != "compiled":
        raise HTTPException(409, "本地源码未成功编译，不能执行部署字节码对齐")
    artifacts = deployment_artifacts(root)
    if body.artifact_contract:
        artifacts = [item for item in artifacts if item["contract"] == body.artifact_contract]
        if not artifacts:
            raise HTTPException(409, "未找到指定合约的 deployedBytecode 编译产物")
    if not artifacts:
        raise HTTPException(409, "编译完成但未找到 deployedBytecode 产物")

    chain_id = int(_rpc_call(endpoint, "eth_chainId", []), 16)
    block = body.block_number if body.block_number is not None else int(_rpc_call(endpoint, "eth_blockNumber", []), 16)
    block_tag = hex(block)
    target_code = _hex_bytes(_rpc_call(endpoint, "eth_getCode", [address, block_tag]))
    if not target_code:
        raise HTTPException(409, "固定区块上未发现目标合约 Runtime Bytecode")
    slot = _rpc_call(endpoint, "eth_getStorageAt", [address, EIP1967_IMPLEMENTATION_SLOT, block_tag]) or "0x"
    implementation = None
    slot_bytes = _hex_bytes(slot)
    if len(slot_bytes) >= 20 and any(slot_bytes[-20:]):
        implementation = "0x" + slot_bytes[-20:].hex()
    comparison_address = implementation or address
    chain_code = _hex_bytes(_rpc_call(endpoint, "eth_getCode", [comparison_address, block_tag])) if implementation else target_code
    chain_hash = hashlib.sha256(chain_code).hexdigest()
    matches = [item for item in artifacts if item["runtime_bytecode_sha256"] == chain_hash]
    selected = matches[0] if matches else (artifacts[0] if len(artifacts) == 1 else None)
    chain_ok = body.expected_chain_id is None or body.expected_chain_id == chain_id
    bytecode_ok = bool(matches)
    status = "aligned" if chain_ok and bytecode_ok else "blocked"
    blockers = []
    if not chain_ok:
        blockers.append(f"chain_id_mismatch: expected {body.expected_chain_id}, received {chain_id}")
    if not bytecode_ok:
        blockers.append("runtime_bytecode_mismatch")
    try:
        source_commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5,
        ).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        source_commit = None
    alignment = {
        "kind": "deployment_alignment",
        "status": status,
        "chain_id": chain_id,
        "block_number": block,
        "contract_address": address,
        "proxy_detected": bool(implementation),
        "implementation_address": implementation,
        "comparison_address": comparison_address,
        "source_commit": source_commit,
        "artifact_contract": selected["contract"] if selected else body.artifact_contract,
        "artifact_path": selected["artifact"] if selected else None,
        "compiler_version": selected["compiler_version"] if selected else None,
        "optimizer_enabled": selected["optimizer_enabled"] if selected else None,
        "optimizer_runs": selected["optimizer_runs"] if selected else None,
        "evm_version": selected["evm_version"] if selected else None,
        "local_runtime_sha256": selected["runtime_bytecode_sha256"] if selected else None,
        "chain_runtime_sha256": chain_hash,
        "runtime_bytecode_match": bytecode_ok,
        "blockers": blockers,
    }
    snapshot = final_core.create_program_snapshot(final_core.ProgramSnapshotInput(
        engagement_id=body.engagement_id,
        platform="immunefi",
        rules=alignment,
        source_uri=str(root),
    ))
    # The RPC URL and raw bytecode are intentionally absent from ProgramSnapshot and API output.
    return {"program_snapshot_id": snapshot["id"], "program_snapshot_version": snapshot["version"], **alignment}


@router.get("/engagements/{engagement_id}/deployment-alignments")
def list_deployment_alignments(engagement_id: str):
    import final_core

    engagement = final_core.get_engagement(engagement_id)
    if engagement["mode"] != "web3":
        raise HTTPException(409, "部署对齐仅用于 Web3 Engagement")
    with final_core.connect() as db:
        rows = db.execute(
            "SELECT * FROM program_snapshots WHERE engagement_id=? ORDER BY version DESC", (engagement_id,),
        ).fetchall()
    values = []
    for row in rows:
        value = dict(row)
        value["rules"] = final_core.load(value["rules"], {})
        if value["rules"].get("kind") == "deployment_alignment":
            values.append(value)
    return values


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
