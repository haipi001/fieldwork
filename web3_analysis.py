from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from web3_ast import compiler_analysis
from web3_lab import binary
from capability_registry import execute


router = APIRouter(prefix="/api/v1/web3", tags=["Web3 analysis"])
ARTIFACT_ROOT = Path(__file__).resolve().parent / "data" / "artifacts"
CONTRACT_RE = re.compile(r"\b(?:abstract\s+)?contract\s+(\w+)(?:\s+is\s+([^\{]+))?")
FUNCTION_RE = re.compile(
    r"\bfunction\s+(\w+)\s*\(([^)]*)\)\s*([^\{;]*)(?:\{|;)", re.MULTILINE,
)
STATE_DECL_RE = re.compile(
    r"(?:mapping\s*\([^;]+?\)|[A-Za-z_]\w*(?:\s*\[[^\]]*\])?)\s+"
    r"(?:(?:public|private|internal|immutable|constant|transient)\s+)*([A-Za-z_]\w*)\s*(?:=[^;]*)?;$",
    re.DOTALL,
)
RISK_PRIMITIVES = {
    "delegatecall": ("critical", "Delegatecall target or calldata authority must be proven"),
    "tx.origin": ("high", "tx.origin authorization is vulnerable to call-chain confusion"),
    "selfdestruct": ("high", "Destructive lifecycle and forced Ether effects require review"),
    ".call{": ("high", "Low-level value call requires reentrancy and return-value review"),
    "assembly": ("medium", "Inline assembly bypasses Solidity safety assumptions"),
    "unchecked": ("medium", "Unchecked arithmetic requires an explicit bound proof"),
    "block.timestamp": ("medium", "Timestamp-dependent state transitions require manipulation bounds"),
    "ecrecover": ("high", "Signature domain separation, nonce and malleability must be proven"),
}


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


class PropertyReplayInput(BaseModel):
    """A deliberately narrow contract: two local Forge replays, never verification promotion."""

    rounds: int = Field(default=2, ge=2, le=2)


class Web3FindingFinalizeInput(BaseModel):
    program_snapshot_id: str
    impact_category: str = Field(min_length=2, max_length=160)
    impact_description: str = Field(min_length=10, max_length=4000)
    root_cause: str = Field(min_length=3, max_length=2000)
    weakness: str = Field(min_length=2, max_length=160)
    location: str = Field(min_length=2, max_length=1000)
    feasibility: str | None = Field(default=None, max_length=2000)
    funds_at_risk: str | None = Field(default=None, max_length=500)


class PropertyReplayCancelled(Exception):
    pass


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
    return sorted(
        p for p in root.rglob("*.sol")
        if not any(part in {"node_modules", "lib", "out", "cache", "test", "tests", "script", "scripts"} for part in p.relative_to(root).parts)
    )


def _brace_end(text: str, opening: int) -> int:
    """Return the matching brace while ignoring braces inside strings and comments."""
    depth, quote, escaped, line_comment, block_comment = 0, None, False, False, False
    index = opening
    while index < len(text):
        char, nxt = text[index], text[index + 1] if index + 1 < len(text) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and nxt == "/":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "/" and nxt == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and nxt == "*":
            block_comment = True
            index += 2
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return len(text) - 1


def _top_level_statements(body: str) -> list[str]:
    statements, current, depth = [], [], 0
    for char in body:
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        if depth == 0:
            current.append(char)
            if char == ";":
                statements.append("".join(current).strip())
                current = []
    return statements


def _state_variables(contract_body: str) -> list[str]:
    ignored = ("function ", "event ", "error ", "using ", "modifier ", "constructor", "struct ", "enum ")
    values = []
    for statement in _top_level_statements(contract_body):
        clean = re.sub(r"//[^\n]*|/\*.*?\*/", " ", statement, flags=re.DOTALL).strip()
        if not clean or clean.startswith(ignored):
            continue
        match = STATE_DECL_RE.search(clean)
        if match:
            values.append(match.group(1))
    return list(dict.fromkeys(values))


def _function_semantics(body: str, state_variables: list[str], known_functions: set[str]) -> dict[str, Any]:
    semantic_body = re.sub(r"//[^\n]*|/\*.*?\*/", " ", body, flags=re.DOTALL)
    reads, writes = [], []
    for variable in state_variables:
        if re.search(rf"\b{re.escape(variable)}\b", semantic_body):
            reads.append(variable)
        if re.search(rf"(?:\bdelete\s+{re.escape(variable)}\b|\b{re.escape(variable)}(?:\s*\[[^\]]+\])?\s*(?:[+\-*/%]?=|\+\+|--))", semantic_body):
            writes.append(variable)
    internal_calls = sorted({
        name for name in known_functions
        if re.search(rf"(?<![\w.]){re.escape(name)}\s*\(", semantic_body)
    })
    external_calls = []
    for receiver, method in re.findall(r"\b([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*(?:\{|\()", semantic_body):
        if receiver in {"msg", "tx", "block", "abi", "super", "this"}:
            continue
        external_calls.append(f"{receiver}.{method}")
    external_calls.extend(
        f"native.{method}" for method in re.findall(
            r"\bpayable\s*\([^)]*\)\s*\.\s*(transfer|send|call)\b", semantic_body,
        )
    )
    external_calls = sorted(set(external_calls))
    low_level = sorted(set(re.findall(r"\.\s*(delegatecall|call|staticcall|send|transfer)\b", semantic_body)))
    return {
        "reads": reads, "writes": writes, "internal_calls": internal_calls,
        "external_calls": external_calls, "low_level_calls": low_level,
    }


def source_model(root: Path) -> dict[str, Any]:
    contracts, edges, invariants, entrypoints, hypotheses, primitives = [], [], [], [], [], []
    functions, state_inventory, call_edges, risk_paths = [], [], [], []
    for path in solidity_sources(root):
        text = path.read_text(errors="replace")
        source_name = str(path.relative_to(root))
        contract_matches = list(CONTRACT_RE.finditer(text))
        for match in contract_matches:
            name = match.group(1)
            bases = [x.strip().split("(")[0] for x in (match.group(2) or "").split(",") if x.strip()]
            contracts.append({"name": name, "source": source_name, "sha256": hashlib.sha256(text.encode()).hexdigest()})
            edges.extend({"source": name, "target": base, "type": "inherits"} for base in bases)
            opening = text.find("{", match.end() - 1)
            if opening < 0:
                continue
            closing = _brace_end(text, opening)
            contract_body = text[opening + 1:closing]
            variables = _state_variables(contract_body)
            state_inventory.extend({"contract": name, "name": variable, "source": source_name} for variable in variables)
            function_matches = list(FUNCTION_RE.finditer(contract_body))
            known_functions = {function_match.group(1) for function_match in function_matches}
            for function_match in function_matches:
                function_name, parameters, suffix = function_match.groups()
                visibility = next((item for item in ("external", "public", "internal", "private") if re.search(rf"\b{item}\b", suffix)), "internal")
                mutability = next((item for item in ("pure", "view", "payable") if re.search(rf"\b{item}\b", suffix)), "nonpayable")
                guards = [item for item in ("onlyOwner", "onlyRole", "nonReentrant", "whenNotPaused") if item.lower() in suffix.lower()]
                body_opening = contract_body.find("{", function_match.end() - 1)
                function_body = "" if function_match.group(0).endswith(";") or body_opening < 0 else contract_body[body_opening + 1:_brace_end(contract_body, body_opening)]
                semantics = _function_semantics(function_body, variables, known_functions - {function_name})
                signature = f"{function_name}({','.join(re.sub(r'\s+(?:memory|calldata|storage)\b|\s+\w+$', '', value.strip()).strip() for value in parameters.split(',') if value.strip())})"
                function_record = {
                    "contract": name, "name": function_name, "signature": signature, "source": source_name,
                    "visibility": visibility, "mutability": mutability, "guards": guards, **semantics,
                }
                functions.append(function_record)
                call_edges.extend({"source": f"{name}.{function_name}", "target": f"{name}.{target}", "type": "internal_call"} for target in semantics["internal_calls"])
                call_edges.extend({"source": f"{name}.{function_name}", "target": target, "type": "external_call"} for target in semantics["external_calls"])
            # Resolve only unique same-contract function names. Overloads remain
            # explicit unknowns until compiler AST/type resolution is available.
            local_functions = [f for f in functions if f["source"] == source_name and f["contract"] == name]
            by_name = {}
            for record in local_functions:
                by_name.setdefault(record["name"], []).append(record)
            for function_record in local_functions:
                visibility = function_record["visibility"]
                mutability = function_record["mutability"]
                guards = function_record["guards"]
                function_name = function_record["name"]
                signature = function_record["signature"]
                semantics = {key: set(function_record[key]) for key in ("reads", "writes", "external_calls", "low_level_calls")}
                pending = list(function_record["internal_calls"])
                visited, unresolved = set(), set()
                while pending:
                    callee = pending.pop()
                    if callee in visited:
                        continue
                    visited.add(callee)
                    matches = by_name.get(callee, [])
                    if len(matches) != 1:
                        unresolved.add(callee)
                        continue
                    record = matches[0]
                    for key in semantics:
                        semantics[key].update(record[key])
                    pending.extend(record["internal_calls"])
                semantics = {key: sorted(values) for key, values in semantics.items()}
                if visibility in {"external", "public"}:
                    state_changing = mutability not in {"view", "pure"}
                    risk_score = min(100, len(semantics["writes"]) * 18 + len(semantics["external_calls"]) * 24 + len(semantics["low_level_calls"]) * 34 + (15 if state_changing and not guards else 0))
                    entry = {**function_record, **semantics, "state_changing": state_changing, "risk_score": risk_score,
                             "reachable_internal_functions": sorted(visited - unresolved),
                             "unresolved_internal_calls": sorted(unresolved), "analysis_kind": "source_heuristic"}
                    entrypoints.append(entry)
                    if state_changing and semantics["writes"] and semantics["external_calls"]:
                        risk_paths.append({
                            "category": "state_external_interaction", "severity": "high" if not guards else "medium",
                            "entrypoint": f"{name}.{signature}", "writes": semantics["writes"],
                            "calls": semantics["external_calls"], "guards": guards, "source": source_name,
                        })
                        hypotheses.append({
                            "category": "state_external_interaction", "severity": "high" if not guards else "medium",
                            "source": source_name, "subject": f"{name}.{function_name}",
                            "statement": "State mutation and external interaction share a reachable entrypoint; ordering and reentrancy safety require proof",
                            "proof_required": "Trace checks-effects-interactions order, callback reachability, state delta and a non-reentrant negative control",
                        })
        lower = text.lower()
        if "delegatecall" in lower or "upgrade" in lower:
            invariants.append({"category": "upgradeability", "statement": "Only authorized governance may change implementation"})
        if any(term in lower for term in ("deposit", "withdraw", "totalassets", "totalsupply")):
            invariants.append({"category": "accounting", "statement": "Asset/share accounting remains conserved across state transitions"})
        if any(term in lower for term in ("onlyowner", "accesscontrol", "hasrole")):
            invariants.append({"category": "authorization", "statement": "Privileged state transitions require the intended role"})
        for primitive, (severity, statement) in RISK_PRIMITIVES.items():
            if primitive in lower:
                occurrence = {"primitive": primitive, "severity": severity, "source": source_name, "count": lower.count(primitive)}
                primitives.append(occurrence)
                hypotheses.append({
                    "category": "dangerous_primitive", "severity": severity, "source": source_name,
                    "subject": primitive, "statement": statement, "proof_required": "Trace reachable callers, attacker-controlled inputs and a negative control",
                })
        if any(term in lower for term in ("price", "oracle", "latestanswer", "latestrounddata")):
            hypotheses.append({
                "category": "oracle_integrity", "severity": "high", "source": source_name,
                "subject": "price/oracle dependency", "statement": "Price freshness, decimals and manipulation resistance require proof",
                "proof_required": "Identify the price source, heartbeat, decimal normalization and fork-based manipulation bound",
            })
    unique_invariants = list({(item["category"], item["statement"]): item for item in invariants}.values())
    unique_hypotheses = list({(item["category"], item["source"], item["subject"]): item for item in hypotheses}.values())
    return {
        "contracts": contracts, "relationships": edges, "invariants": unique_invariants,
        "entrypoints": entrypoints, "functions": functions, "state_variables": state_inventory,
        "call_graph": call_edges, "risk_paths": risk_paths,
        "risk_primitives": primitives, "hypotheses": unique_hypotheses,
        "summary": {
            "contracts": len(contracts), "entrypoints": len(entrypoints),
            "state_changing_entrypoints": sum(item["state_changing"] for item in entrypoints),
            "state_variables": len(state_inventory), "call_edges": len(call_edges),
            "risk_paths": len(risk_paths), "risk_primitives": len(primitives), "hypotheses": len(unique_hypotheses),
        },
    }


def run_forge_build(root: Path) -> dict[str, Any]:
    forge = binary("forge")
    if not forge:
        return {"status": "degraded", "reason": "forge_missing"}
    # Production compilation is deliberately isolated from test fixtures. A
    # broken Echidna harness must not hide whether the deployable contracts
    # themselves compile.
    result = run_isolated(
        [forge, "build", "--ast", "--build-info", "--no-cache", "--no-lint", "--skip", "test", "--skip", "script"],
        root, timeout=600, output_limit=12000, export_dirs=("out", "cache"),
    )
    output = (result.stdout + "\n" + result.stderr)[-12000:]
    return {"status": "compiled" if result.returncode == 0 else "failed", "exit_code": result.returncode, "output": output}


def parse_forge_test_json(output: str) -> list[dict[str, Any]]:
    """Normalize Forge JSON without persisting full traces or attacker-controlled logs."""
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        start = output.rfind("\n{")
        try:
            payload = json.loads(output[start + 1:] if start >= 0 else "{}")
        except json.JSONDecodeError:
            return []
    tests = []
    for suite, suite_result in payload.items() if isinstance(payload, dict) else []:
        if not isinstance(suite_result, dict):
            continue
        for name, result in suite_result.get("test_results", {}).items():
            kind = result.get("kind", {}) if isinstance(result, dict) else {}
            fuzz = kind.get("Fuzz", {}) if isinstance(kind, dict) else {}
            counterexample = result.get("counterexample") if isinstance(result, dict) else None
            tests.append({
                "suite": str(suite)[:300], "name": str(name)[:300],
                "status": str(result.get("status", "Unknown")),
                "reason": str(result.get("reason"))[:1000] if result.get("reason") else None,
                "fuzz_runs": fuzz.get("runs"),
                "counterexample": counterexample if isinstance(counterexample, (dict, list, str, int, float, bool, type(None))) else str(counterexample)[:2000],
            })
    return tests


def run_forge_tests(root: Path) -> dict[str, Any]:
    forge = binary("forge")
    if not forge:
        return {"status": "degraded", "reason": "forge_missing"}
    result = run_isolated(
        [forge, "test", "--fuzz-runs", "64", "--skip", "echidna", "--json"],
        root, timeout=600, output_limit=1_000_000, export_dirs=("out", "cache"),
    )
    output = (result.stdout + "\n" + result.stderr)[-12000:]
    tests = parse_forge_test_json(result.stdout)
    failed = [item for item in tests if item["status"].lower() not in {"success", "passed"}]
    return {
        "status": "passed" if result.returncode == 0 and not failed else "failed",
        "exit_code": result.returncode, "runs": 64, "tests": tests,
        "passed_tests": len(tests) - len(failed), "failed_tests": len(failed), "output": output,
    }


def run_forge_property_replay(root: Path, property_name: str, seed: int) -> dict[str, Any]:
    """Replay one named property with an independent deterministic seed."""
    forge = binary("forge")
    if not forge:
        return {"status": "degraded", "reason": "forge_missing", "tests": []}
    base_name = property_name.split("(", 1)[0]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", base_name):
        raise HTTPException(422, "Property 名称不符合 Forge 测试函数格式")
    result = run_isolated(
        [
            forge, "test", "--match-test", f"^{base_name}", "--fuzz-runs", "64",
            "--fuzz-seed", hex(seed), "--skip", "echidna", "--json",
        ],
        root, timeout=600, output_limit=1_000_000, export_dirs=("out", "cache"),
    )
    tests = [item for item in parse_forge_test_json(result.stdout) if item["name"].split("(", 1)[0] == base_name]
    failed = [item for item in tests if item["status"].lower() not in {"success", "passed"}]
    return {
        "status": "failed" if failed else ("passed" if result.returncode == 0 and tests else "not_found"),
        "exit_code": result.returncode, "seed": hex(seed), "runs": 64, "tests": tests,
        "output_tail": (result.stdout + "\n" + result.stderr)[-4000:],
    }


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
    model["compiler_analysis"] = compiler_analysis(root) if compile_result["status"] == "compiled" else {"status": "unavailable", "units": [], "reason": "compile_not_successful"}
    fuzz_result = run_forge_tests(root) if framework == "foundry" and compile_result["status"] == "compiled" else {"status": "not_run"}
    compiler = compiler_model(root) if compile_result["status"] == "compiled" else []
    ast_hypotheses = {}
    for unit in model["compiler_analysis"].get("units", []):
        for entry in unit["entrypoints"]:
            if entry["requires_interaction_review"]:
                ast_hypotheses[entry["label"]] = {
                    "category": "compiler_state_interaction", "severity": "high",
                    "source": entry["label"].split(":", 1)[0], "subject": entry["label"],
                    "statement": "Compiler-resolved state writes reach external or unresolved interactions through functions/modifiers; verify call order and runtime target",
                    "proof_required": "Local trace, deployment target binding, state delta and callback or fixed-version negative control",
                }
    model["hypotheses"].extend(ast_hypotheses.values())
    model["summary"]["hypotheses"] = len(model["hypotheses"])
    model["summary"]["compiler_units"] = len(model["compiler_analysis"].get("units", []))
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
        hypothesis_observation_ids = []
        for hypothesis in model["hypotheses"]:
            hypothesis_observation_id = final_core.uid("obs")
            hypothesis_observation_ids.append(hypothesis_observation_id)
            db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                hypothesis_observation_id, body.run_id, body.engagement_id, "web3",
                f"web3.hypothesis.{hypothesis['category']}", hypothesis["subject"],
                f"{hypothesis['severity'].upper()} review · {hypothesis['statement']}",
                .65, "web3-source-discovery", artifact_id, final_core.utcnow(),
            ))
        property_observation_ids = []
        for property_test in fuzz_result.get("tests", []):
            failed = property_test.get("status", "").lower() not in {"success", "passed"}
            property_observation_id = final_core.uid("obs")
            property_observation_ids.append(property_observation_id)
            summary = (
                f"PROPERTY FAILED · {property_test['name']} · {property_test.get('reason') or 'counterexample produced'}"
                if failed else f"Property passed · {property_test['name']} · {property_test.get('fuzz_runs') or 0} fuzz runs"
            )
            db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                property_observation_id, body.run_id, body.engagement_id, "web3",
                "web3.property.counterexample" if failed else "web3.property.passed",
                property_test["suite"], summary[:1000], .95 if failed else .85,
                "forge-test", artifact_id, final_core.utcnow(),
            ))
            if failed:
                db.execute("INSERT INTO invariant_registry VALUES(?,?,?,?,?,?,?)", (
                    final_core.uid("invariant"), body.engagement_id, "property_test",
                    property_test["name"], "forge-counterexample", "violated", final_core.utcnow(),
                ))
                evidence_id = final_core.uid("evidence")
                db.execute("INSERT INTO evidence_v2 VALUES(?,?,?,?,?,?,?,?)", (
                    evidence_id, property_observation_id, body.run_id, "forge_counterexample",
                    summary[:1000], artifact_id, "supporting", final_core.utcnow(),
                ))
                existing = db.execute(
                    "SELECT id FROM candidate_findings WHERE run_id=? AND category='web3_property_violation' AND target=? AND status!='archived'",
                    (body.run_id, property_test["name"]),
                ).fetchone()
                if not existing:
                    candidate_id = final_core.uid("candidate")
                    timestamp = final_core.utcnow()
                    db.execute("INSERT INTO candidate_findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                        candidate_id, body.run_id, body.engagement_id, "web3",
                        f"Property violation: {property_test['name']}", "web3_property_violation",
                        property_test["name"],
                        f"Forge produced a counterexample: {property_test.get('reason') or 'property failed'}. Independent fork replay and impact analysis are still required.",
                        "candidate", final_core.dump([evidence_id]), timestamp, timestamp,
                    ))
        observation_id = final_core.uid("obs")
        db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            observation_id, body.run_id, body.engagement_id, "web3", "web3.compiler_result",
            str(root), f"{framework} compile={compile_result['status']} fuzz={fuzz_result['status']}",
            1.0 if compile_result["status"] == "compiled" else .2, "forge", artifact_id, final_core.utcnow(),
        ))
    return {
        "artifact_id": artifact_id, "observation_id": observation_id,
        "hypothesis_observation_ids": hypothesis_observation_ids,
        "property_observation_ids": property_observation_ids, **artifact,
    }


@router.post("/candidates/{candidate_id}/property-replay")
def replay_property_candidate(candidate_id: str, body: PropertyReplayInput):
    """Create replay evidence; formal verification gates remain in final_core.verify_candidate."""
    return execute_property_replay(candidate_id, body)


def property_replay_context(candidate_id: str):
    import final_core
    with final_core.connect() as db:
        candidate = db.execute("SELECT * FROM candidate_findings WHERE id=?", (candidate_id,)).fetchone()
        if not candidate:
            raise HTTPException(404, "Candidate 不存在")
        if candidate["mode"] != "web3" or candidate["category"] != "web3_property_violation":
            raise HTTPException(409, "定向属性复测仅用于 Web3 Property Candidate")
        if candidate["status"] == "archived":
            raise HTTPException(409, "已归档 Candidate 不能复测")
        source_observation = db.execute(
            """SELECT * FROM observations WHERE run_id=? AND observation_type='web3.compiler_result'
               ORDER BY created_at DESC LIMIT 1""", (candidate["run_id"],),
        ).fetchone()
    if not source_observation:
        raise HTTPException(409, "Candidate 缺少可追溯的本地源码根目录")
    root = Path(source_observation["subject"]).expanduser().resolve()
    if not root.is_dir() or detect_framework(root) != "foundry" or not solidity_sources(root):
        raise HTTPException(409, "Candidate 的 Foundry 源码目录已不可用")
    return candidate, root


def execute_property_replay(candidate_id: str, body: PropertyReplayInput, job_id: str | None = None):
    """Run the two deterministic rounds, with cooperative cancellation between Forge processes."""
    import final_core
    candidate, root = property_replay_context(candidate_id)

    started_at = final_core.utcnow()
    raw_rounds = []
    for index, seed in enumerate((0xF13D01, 0xF13D02), start=1):
        if job_id and final_core.verification_job_cancel_requested(job_id):
            raise PropertyReplayCancelled("用户取消了属性复测")
        if job_id:
            final_core.update_verification_job(job_id, phase=f"第 {index} 轮 · Forge seed {hex(seed)}")
        raw_rounds.append(run_forge_property_replay(root, candidate["target"], seed))
        if job_id:
            final_core.update_verification_job(job_id, completed_requests=index)
    if job_id and final_core.verification_job_cancel_requested(job_id):
        raise PropertyReplayCancelled("用户取消了属性复测")
    expected_base = candidate["target"].split("(", 1)[0]
    normalized_rounds = []
    for index, replay in enumerate(raw_rounds, start=1):
        matched = next((item for item in replay.get("tests", []) if item["name"].split("(", 1)[0] == expected_base), None)
        counterexample = matched.get("counterexample") if matched else None
        digest = hashlib.sha256(json.dumps(counterexample, sort_keys=True, ensure_ascii=False).encode()).hexdigest() if counterexample is not None else None
        normalized_rounds.append({
            "round": index, "seed": replay.get("seed"), "status": replay.get("status"),
            "property": matched.get("name") if matched else None,
            "reason": matched.get("reason") if matched else replay.get("reason") or "property_not_found",
            "fuzz_runs": matched.get("fuzz_runs") if matched else None,
            "counterexample_sha256": digest,
        })
    failure_reasons = [item["reason"] for item in normalized_rounds]
    stable = (all(item["status"] == "failed" and item["property"] and item["counterexample_sha256"] for item in normalized_rounds)
              and len(set(failure_reasons)) == 1
              and len({item["seed"] for item in normalized_rounds}) == 2)
    attempt_status = "reproduced" if stable else "unstable"

    artifact_id = final_core.uid("artifact")
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    artifact_path = ARTIFACT_ROOT / f"{artifact_id}.json"
    artifact_payload = {
        "kind": "web3.property_replay", "candidate_id": candidate_id,
        "property": candidate["target"], "rounds": raw_rounds,
        "stability": {"stable_failure": stable, "matched_failure_reason": failure_reasons[0] if stable else None},
    }
    artifact_path.write_text(final_core.redact(json.dumps(artifact_payload, ensure_ascii=False, indent=2)))
    evidence_ids = final_core.load(candidate["evidence_ids"], [])
    created_evidence_ids = []
    with final_core.connect() as db:
        db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (
            artifact_id, candidate["run_id"], "web3.property_replay", str(artifact_path),
            hashlib.sha256(artifact_path.read_bytes()).hexdigest(), "application/json", 1, final_core.utcnow(),
        ))
        for item in normalized_rounds:
            observation_id, evidence_id = final_core.uid("obs"), final_core.uid("evidence")
            summary = (
                f"PROPERTY REPLAY {item['round']}/2 · {item['status'].upper()} · "
                f"{item['property'] or candidate['target']} · {item['reason']}"
            )[:1000]
            db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                observation_id, candidate["run_id"], candidate["engagement_id"], "web3",
                "web3.property.replay", candidate["target"], summary,
                .98 if item["status"] == "failed" else .7, "forge-property-replay", artifact_id, final_core.utcnow(),
            ))
            db.execute("INSERT INTO evidence_v2 VALUES(?,?,?,?,?,?,?,?)", (
                evidence_id, observation_id, candidate["run_id"], "forge_property_replay",
                summary, artifact_id, "supporting" if item["status"] == "failed" else "counter", final_core.utcnow(),
            ))
            created_evidence_ids.append(evidence_id)
        evidence_ids.extend(created_evidence_ids)
        attempt_id = final_core.uid("verify")
        result = {
            "stable_failure": stable, "property": candidate["target"], "rounds": normalized_rounds,
            "artifact_id": artifact_id,
            "alternative_explanations_checked": {
                "exact_property_matched": all(item["property"] for item in normalized_rounds),
                "distinct_seeds": len({item["seed"] for item in normalized_rounds}) == 2,
                "counterexamples_present": all(item["counterexample_sha256"] for item in normalized_rounds),
                "stable_failure_reason": len(set(failure_reasons)) == 1,
            },
            "boundary": "Replay stability does not prove impact, eligibility, or a verified finding.",
        }
        db.execute("INSERT INTO verification_attempts VALUES(?,?,?,?,?,?,?,?)", (
            attempt_id, candidate_id, "forge-property-replay", attempt_status, body.rounds,
            final_core.dump(result), started_at, final_core.utcnow(),
        ))
        db.execute("UPDATE candidate_findings SET status=?,evidence_ids=?,updated_at=? WHERE id=?", (
            attempt_status, final_core.dump(evidence_ids), final_core.utcnow(), candidate_id,
        ))
    final_core.add_event(
        candidate["run_id"], "verification", "web3.property_replay_completed",
        f"Web3 属性定向复测完成：{attempt_status}",
        {"candidate_id": candidate_id, "attempt_id": attempt_id, "stable_failure": stable},
    )
    return {
        "candidate_id": candidate_id, "verification_attempt_id": attempt_id,
        "status": attempt_status, "stable_failure": stable, "rounds": normalized_rounds,
        "evidence_ids": created_evidence_ids,
        "next_gate": "需继续完成 ProgramSnapshot、影响证明、已知问题/历史审计与反证检查，才能进入 Verified Finding。",
    }


def _run_property_replay_job(job_id: str, candidate_id: str, body: PropertyReplayInput) -> None:
    import final_core
    final_core.update_verification_job(job_id, status="running", phase="准备 Foundry 属性复测")
    with final_core.connect() as db:
        db.execute("UPDATE verification_jobs SET started_at=? WHERE id=?", (final_core.utcnow(), job_id))
    try:
        result = execute_property_replay(candidate_id, body, job_id)
    except PropertyReplayCancelled as error:
        final_core.update_verification_job(job_id, status="cancelled", phase="已取消", error=str(error), completed=True)
    except HTTPException as error:
        final_core.update_verification_job(job_id, status="failed", phase="执行失败", error=str(error.detail), completed=True)
    except Exception as error:
        final_core.update_verification_job(job_id, status="failed", phase="执行失败",
                                           error=f"{type(error).__name__}: {error}", completed=True)
    else:
        final_core.update_verification_job(job_id, status="completed", phase="属性复测完成", result=result, completed=True)


@router.post("/candidates/{candidate_id}/property-replay/jobs", status_code=202)
def start_property_replay_job(candidate_id: str, body: PropertyReplayInput):
    import final_core
    candidate, _ = property_replay_context(candidate_id)
    with final_core.connect() as db:
        active = db.execute("""SELECT id FROM verification_jobs
            WHERE candidate_id=? AND status IN ('queued','running','cancelling')""", (candidate_id,)).fetchone()
        if active:
            raise HTTPException(409, f"这条候选已有进行中的复验：{active['id']}")
        job_id, timestamp = final_core.uid("verify-job"), final_core.utcnow()
        db.execute("INSERT INTO verification_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            job_id, candidate_id, candidate["run_id"], "forge-property-replay", "queued", "等待执行",
            0, 2, 0, None, None, timestamp, None, None,
        ))
    thread = threading.Thread(target=_run_property_replay_job, args=(job_id, candidate_id, body),
                              daemon=True, name=f"fieldwork-{job_id}")
    thread.start()
    return final_core.get_verification_job(job_id)


@router.post("/candidates/{candidate_id}/finalize-property")
def finalize_property_candidate(candidate_id: str, body: Web3FindingFinalizeInput):
    """Bind a stable local property proof to immutable reviewed program rules."""
    import final_core
    from verification_receipts import issue_receipt

    with final_core.connect() as db:
        candidate = db.execute("SELECT * FROM candidate_findings WHERE id=?", (candidate_id,)).fetchone()
        if not candidate:
            raise HTTPException(404, "Candidate 不存在")
        if candidate["mode"] != "web3" or candidate["category"] != "web3_property_violation":
            raise HTTPException(409, "仅 Web3 Property Candidate 可完成此验证")
        if candidate["status"] != "reproduced":
            raise HTTPException(409, "必须先完成两轮稳定属性复测")
        replay_attempt = db.execute("""SELECT * FROM verification_attempts
            WHERE candidate_id=? AND oracle='forge-property-replay' AND status='reproduced'
            ORDER BY completed_at DESC LIMIT 1""", (candidate_id,)).fetchone()
        program = db.execute("SELECT * FROM program_snapshots WHERE id=? AND engagement_id=?",
                             (body.program_snapshot_id, candidate["engagement_id"])).fetchone()
        invariant = db.execute("""SELECT * FROM invariant_registry
            WHERE engagement_id=? AND statement=? AND status='violated' ORDER BY created_at DESC LIMIT 1""",
            (candidate["engagement_id"], candidate["target"])).fetchone()
    if not replay_attempt or not program or not invariant:
        raise HTTPException(409, "缺少稳定复测、项目规则或已登记的违反属性")
    replay = final_core.load(replay_attempt["result"], {})
    checks = replay.get("alternative_explanations_checked", {})
    if not replay.get("stable_failure") or not checks or not all(checks.values()):
        raise HTTPException(409, "属性复测未排除测试匹配、种子或反例缺失问题")
    artifact_id = replay.get("artifact_id")
    with final_core.connect() as db:
        artifact = db.execute("SELECT * FROM artifacts WHERE id=? AND run_id=? AND kind='web3.property_replay'",
                              (artifact_id, candidate["run_id"])).fetchone()
    if not artifact:
        raise HTTPException(409, "属性复测 Artifact 不存在或不属于当前 Run")
    engagement = final_core.get_engagement(candidate["engagement_id"])
    rules = final_core.validated_program_rules(program, engagement, body.impact_category)
    if engagement.get("target_type") == "contract":
        alignment = final_core.latest_deployment_alignment(engagement["id"])
        if not alignment or alignment["rules"].get("status") != "aligned":
            raise HTTPException(409, "链上合约必须先完成源码与部署字节码对齐")
    reasons = {item["reason"] for item in replay["rounds"]}
    proof = final_core.VerificationInput(
        oracle="forge-property-replay-v1", attempts=2, reproduced=True,
        counterevidence_checked=True,
        counterevidence_summary="Exact registered property matched; two distinct seeds produced hashed counterexamples and the same failure reason.",
        severity=rules["severity"], impact_description=body.impact_description,
        steps=["Match the violated registered invariant", "Replay with deterministic seed 0xF13D01",
               "Replay with independent seed 0xF13D02", "Bind result to reviewed program rules"],
        expected=f"Registered invariant {candidate['target']} must hold for all generated inputs",
        actual=f"Both independent replays failed: {next(iter(reasons))}",
        root_cause=body.root_cause, weakness=body.weakness, location=body.location,
        poc_artifact_ids=[artifact_id], program_snapshot_id=program["id"],
        impact_in_scope=True, known_issue_checked=True, previous_audit_checked=True,
        poc_rule_checked=True, feasibility=body.feasibility, funds_at_risk=body.funds_at_risk,
    )
    proof.receipt_id = issue_receipt(candidate_id, proof)
    finding = final_core.verify_candidate(candidate_id, proof)
    return {"status": finding["status"], "finding": finding, "receipt_id": proof.receipt_id,
            "program_snapshot_id": program["id"], "impact_category": body.impact_category,
            "impact_demonstrated": False,
            "boundary": "本结果证明本地属性违反并完成规则资格绑定；经济损失金额未由该属性测试直接证明。"}


@router.get("/runs/{run_id}/discovery")
def web3_run_discovery(run_id: str):
    """Return the latest normalized Web3 attack-surface model for researcher review."""
    import final_core

    with final_core.connect() as db:
        run = db.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
        artifact = db.execute(
            "SELECT * FROM artifacts WHERE run_id=? AND kind='web3.source_model' ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    if not run or run["mode"] != "web3":
        raise HTTPException(409, "Web3 discovery 仅用于 Web3 Run")
    if not artifact:
        return {"run_id": run_id, "ready": False, "reason": "source_model_not_generated"}
    path = Path(artifact["uri"])
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(409, "Web3 source model artifact 不可读") from error
    model = payload.get("model", {})
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    hypotheses = sorted(model.get("hypotheses", []), key=lambda item: severity_order.get(item.get("severity"), 9))
    return {
        "run_id": run_id, "ready": True, "artifact_id": artifact["id"],
        "framework": payload.get("framework"), "compile": payload.get("compile", {}),
        "fuzz": payload.get("fuzz", {}), "property_tests": payload.get("fuzz", {}).get("tests", []),
        "summary": model.get("summary", {}),
        "contracts": model.get("contracts", []), "entrypoints": model.get("entrypoints", []),
        "state_variables": model.get("state_variables", []), "call_graph": model.get("call_graph", []),
        "risk_paths": model.get("risk_paths", []),
        "compiler_analysis": model.get("compiler_analysis", {"status": "unavailable", "units": []}),
        "risk_primitives": model.get("risk_primitives", []), "hypotheses": hypotheses,
        "invariants": model.get("invariants", []), "relationships": model.get("relationships", []),
        "boundary": "Hypotheses require independent reproduction and do not constitute verified vulnerabilities.",
    }


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
from isolated_execution import run_isolated
