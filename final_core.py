from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, StreamingResponse
from reporting import export_bundle, redact, render, universal_model
from capability_registry import inventory as capability_inventory
from lifecycle import database_integrity, list_backups
from version import APP_VERSION, BUILD_NUMBER, SCHEMA_VERSION


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "src_control.db"
LOCAL_DATA_ROOT = ROOT / "data"
router = APIRouter(prefix="/api/v1", tags=["FINAL v1"])


@router.get("/system/version")
def system_version():
    return {
        "app_version": APP_VERSION,
        "build_number": BUILD_NUMBER,
        "schema_version": SCHEMA_VERSION,
        "database_integrity": database_integrity(DB),
        "backups": len([item for item in list_backups(LOCAL_DATA_ROOT / "backups") if item["valid"]]),
    }


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def load(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def init_final_db() -> None:
    DB.parent.mkdir(exist_ok=True)
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS target_specs (
              id TEXT PRIMARY KEY, mode TEXT NOT NULL, raw_target TEXT NOT NULL,
              target_type TEXT NOT NULL, normalized_target TEXT NOT NULL,
              chain_id INTEGER, metadata TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS engagements_v2 (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, mode TEXT NOT NULL,
              target_spec_id TEXT NOT NULL, status TEXT NOT NULL,
              current_scope_snapshot_id TEXT, current_policy_id TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              FOREIGN KEY(target_spec_id) REFERENCES target_specs(id)
            );
            CREATE TABLE IF NOT EXISTS scope_snapshots (
              id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, version INTEGER NOT NULL,
              mode TEXT NOT NULL, rules TEXT NOT NULL, source TEXT NOT NULL,
              confirmed_at TEXT, created_at TEXT NOT NULL,
              UNIQUE(engagement_id, version)
            );
            CREATE TABLE IF NOT EXISTS execution_policies (
              id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, version INTEGER NOT NULL,
              policy TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(engagement_id, version)
            );
            CREATE TABLE IF NOT EXISTS analysis_runs (
              id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, mode TEXT NOT NULL,
              scope_snapshot_id TEXT NOT NULL, policy_id TEXT NOT NULL,
              status TEXT NOT NULL, current_stage TEXT NOT NULL,
              synthetic INTEGER NOT NULL DEFAULT 0, started_at TEXT,
              paused_at TEXT, stopped_at TEXT, completed_at TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS run_events_v2 (
              id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
              stage TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL,
              payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL,
              state TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS checkpoints_run_stage_uq ON checkpoints(run_id,stage);
            CREATE TABLE IF NOT EXISTS observations (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, engagement_id TEXT NOT NULL,
              mode TEXT NOT NULL, observation_type TEXT NOT NULL,
              subject TEXT NOT NULL, summary TEXT NOT NULL, confidence REAL NOT NULL,
              source_capability TEXT NOT NULL, raw_ref TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL,
              uri TEXT NOT NULL, sha256 TEXT, media_type TEXT NOT NULL,
              redacted INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_v2 (
              id TEXT PRIMARY KEY, observation_id TEXT, run_id TEXT NOT NULL,
              evidence_type TEXT NOT NULL, summary TEXT NOT NULL,
              artifact_id TEXT, polarity TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entities (
              id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, entity_type TEXT NOT NULL,
              canonical_key TEXT NOT NULL, label TEXT NOT NULL, attributes TEXT NOT NULL,
              created_at TEXT NOT NULL, UNIQUE(engagement_id, canonical_key)
            );
            CREATE TABLE IF NOT EXISTS relationships (
              id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, source_id TEXT NOT NULL,
              target_id TEXT NOT NULL, relation_type TEXT NOT NULL,
              evidence_ids TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS candidate_findings (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, engagement_id TEXT NOT NULL,
              mode TEXT NOT NULL, title TEXT NOT NULL, category TEXT NOT NULL,
              target TEXT NOT NULL, hypothesis TEXT NOT NULL, status TEXT NOT NULL,
              evidence_ids TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS verification_attempts (
              id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, oracle TEXT NOT NULL,
              status TEXT NOT NULL, attempts INTEGER NOT NULL, result TEXT NOT NULL,
              started_at TEXT NOT NULL, completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS canonical_findings (
              id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL UNIQUE,
              engagement_id TEXT NOT NULL, mode TEXT NOT NULL, title TEXT NOT NULL,
              category TEXT NOT NULL, severity TEXT NOT NULL, target TEXT NOT NULL,
              impact TEXT NOT NULL, eligibility TEXT NOT NULL, verification TEXT NOT NULL,
              evidence_ids TEXT NOT NULL, status TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS report_previews (
              id TEXT PRIMARY KEY, finding_id TEXT NOT NULL, platform TEXT NOT NULL,
              adapter_version TEXT NOT NULL, completeness TEXT NOT NULL,
              content TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS submission_packages_v2 (
              id TEXT PRIMARY KEY, finding_id TEXT NOT NULL, platform TEXT NOT NULL,
              status TEXT NOT NULL, manifest TEXT NOT NULL, export_path TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS program_snapshots (
              id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, version INTEGER NOT NULL,
              platform TEXT NOT NULL, rules TEXT NOT NULL, source_uri TEXT,
              created_at TEXT NOT NULL, UNIQUE(engagement_id,version)
            );
            CREATE TABLE IF NOT EXISTS identities (
              id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, label TEXT NOT NULL,
              role TEXT NOT NULL, tenant TEXT, credential_ref TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS identity_profiles (
              identity_id TEXT PRIMARY KEY, auth_type TEXT NOT NULL,
              session_status TEXT NOT NULL, expires_at TEXT, last_validated_at TEXT,
              notes TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS coverage_v2 (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, surface_key TEXT NOT NULL,
              state TEXT NOT NULL, reason TEXT NOT NULL, observation_ids TEXT NOT NULL,
              updated_at TEXT NOT NULL, UNIQUE(run_id,surface_key)
            );
            CREATE TABLE IF NOT EXISTS graveyard (
              id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, hypothesis_key TEXT NOT NULL,
              reason TEXT NOT NULL, counterevidence_ids TEXT NOT NULL,
              resurrect_when TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS invariant_registry (
              id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, category TEXT NOT NULL,
              statement TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS web3_forks (
              id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, chain_id INTEGER NOT NULL,
              rpc_class TEXT NOT NULL, fork_block INTEGER, status TEXT NOT NULL,
              write_enabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS run_budgets_v2 (
              run_id TEXT PRIMARY KEY, request_limit INTEGER NOT NULL, requests_used INTEGER NOT NULL,
              tool_call_limit INTEGER NOT NULL, tool_calls_used INTEGER NOT NULL,
              model_budget_micros INTEGER NOT NULL, model_cost_micros INTEGER NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS request_slots_v2 (
              run_id TEXT PRIMARY KEY, last_allowed_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS run_configs_v2 (
              run_id TEXT PRIMARY KEY, config TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS http_exchanges (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, engagement_id TEXT NOT NULL,
              identity_id TEXT, method TEXT NOT NULL, url TEXT NOT NULL,
              request_headers TEXT NOT NULL, request_body TEXT,
              response_status INTEGER NOT NULL, response_headers TEXT NOT NULL,
              response_body_preview TEXT NOT NULL, response_sha256 TEXT NOT NULL,
              response_bytes INTEGER NOT NULL, source TEXT NOT NULL,
              parent_exchange_id TEXT, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS http_exchanges_run_created_idx ON http_exchanges(run_id,created_at);
            """
        )
        migrate_legacy_findings(db)
        # A process restart never leaves a v1 run looking live. Checkpoints stay
        # intact and an explicit Resume continues from the first missing stage.
        db.execute("UPDATE analysis_runs SET status='paused',paused_at=? WHERE status IN ('queued','running')", (utcnow(),))


def migrate_legacy_findings(db: sqlite3.Connection) -> None:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='findings'").fetchone():
        return
    for old in db.execute("SELECT * FROM findings").fetchall():
        candidate_id = f"legacy-{old['id']}"
        if db.execute("SELECT 1 FROM candidate_findings WHERE id=?", (candidate_id,)).fetchone():
            continue
        evidence_ids = [r["id"] for r in db.execute("SELECT id FROM evidence_ledger WHERE finding_id=?", (old["id"],)).fetchall()]
        db.execute("INSERT INTO candidate_findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            candidate_id, "legacy-run", old["engagement_id"], "traditional", old["title"],
            "legacy_import", old["target"], "Legacy finding migrated for human re-verification",
            "human_review", dump(evidence_ids), old["created_at"], utcnow(),
        ))


class ResolveTargetInput(BaseModel):
    target: str = Field(min_length=2, max_length=2048)
    mode: Literal["traditional", "web3"] = "traditional"
    chain_id: int | None = None


class EngagementInput(ResolveTargetInput):
    name: str = Field(default="New security analysis", min_length=2, max_length=160)
    scope: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
    scope_source: str = "manual"


class EngagementUpdateInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)


class FindingUpdateInput(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=240)
    hypothesis: str | None = Field(default=None, min_length=2, max_length=4000)
    severity: str | None = Field(default=None, min_length=2, max_length=32)


class ReportRequest(BaseModel):
    format: str = "markdown"


class ObservationInput(BaseModel):
    observation_type: str
    subject: str
    summary: str
    source_capability: str
    confidence: float = Field(default=.5, ge=0, le=1)
    raw_ref: str | None = None


class ProgramSnapshotInput(BaseModel):
    engagement_id: str
    platform: str = "immunefi"
    rules: dict[str, Any]
    source_uri: str | None = None


class Web3ExecutionCheck(BaseModel):
    engagement_id: str
    network_class: Literal["production", "public_testnet", "local_fork", "local_devnet"]
    action: Literal["read", "write", "broadcast", "sign"]
    uses_real_private_key: bool = False


class IdentityInput(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=80)
    tenant: str | None = None
    credential_ref: str | None = None
    auth_type: Literal["cookie", "bearer", "basic", "oauth", "totp", "keychain_reference", "none"] = "none"
    session_status: Literal["ready", "needs_login", "expired", "disabled"] = "needs_login"
    expires_at: str | None = None
    notes: str = Field(default="", max_length=1000)


class IdentityUpdateInput(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    role: str | None = Field(default=None, min_length=1, max_length=80)
    tenant: str | None = None
    credential_ref: str | None = None
    auth_type: Literal["cookie", "bearer", "basic", "oauth", "totp", "keychain_reference", "none"] | None = None
    session_status: Literal["ready", "needs_login", "expired", "disabled"] | None = None
    expires_at: str | None = None
    notes: str | None = Field(default=None, max_length=1000)


class CandidateInput(BaseModel):
    title: str
    category: str
    target: str
    hypothesis: str
    observation_ids: list[str] = Field(min_length=1)


class VerificationInput(BaseModel):
    oracle: str
    attempts: int = Field(ge=1, le=20)
    reproduced: bool
    counterevidence_checked: bool
    counterevidence_summary: str
    severity: str
    impact_description: str
    steps: list[str] = Field(min_length=1)
    expected: str
    actual: str
    root_cause: str
    weakness: str
    location: str
    poc_artifact_ids: list[str] = Field(default_factory=list)
    program_snapshot_id: str | None = None
    impact_in_scope: bool | None = None
    known_issue_checked: bool | None = None
    previous_audit_checked: bool | None = None
    poc_rule_checked: bool | None = None
    feasibility: str | None = None
    funds_at_risk: str | None = None


class PolicyCheckInput(BaseModel):
    engagement_id: str
    target: str
    action: str = "read"
    destructive: bool = False
    third_party_active: bool = False
    third_party_passive: bool = False


class RequestAuthorizationInput(BaseModel):
    target: str
    action: str = "read"


class RoutingPlanInput(BaseModel):
    mode: Literal["traditional", "web3"]
    task: Literal["inventory", "http", "code", "static", "fuzz", "verification", "report"]
    model_budget_usd: float = Field(default=0, ge=0)


class StartAnalysisInput(BaseModel):
    execution_mode: Literal["demo", "real"] = "real"
    include_recon: bool = True
    include_code: bool = False
    source_path: str | None = None
    fork_rpc_url: str | None = None
    fork_block_number: int | None = Field(default=None, ge=0)
    chain_id: int | None = Field(default=None, ge=1)
    timeout_seconds: int = Field(default=120, ge=5, le=7200)
    max_discovered_targets: int = Field(default=25, ge=1, le=200)
    scan_profile: Literal["quick", "standard", "deep"] = "quick"
    include_strix: bool = False
    include_shannon: bool = False
    include_native_agent: bool = True


def _planned_capabilities(engagement: dict[str, Any], body: StartAnalysisInput) -> list[str]:
    repository = engagement.get("target_type") == "repository"
    if engagement["mode"] == "web3":
        planned = ["forge", "slither", "aderyn", "echidna", "medusa", "halmos", "gitleaks", "trivy"]
        if not repository:
            planned.extend(["anvil", "cast"])
        return planned
    planned = [] if repository or not body.include_recon else ["subfinder", "httpx", "katana", "nuclei"]
    if repository or body.include_code or body.source_path:
        planned.extend(["semgrep", "gitleaks", "trivy"])
    if body.include_native_agent:
        planned.append("native-agent")
    if body.include_strix:
        planned.append("strix")
    if body.include_shannon:
        planned.append("shannon")
    return list(dict.fromkeys(planned))


def latest_deployment_alignment(engagement_id: str) -> dict[str, Any] | None:
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM program_snapshots WHERE engagement_id=? ORDER BY version DESC", (engagement_id,),
        ).fetchall()
    for row in rows:
        value = dict(row)
        rules = load(value["rules"], {})
        if rules.get("kind") == "deployment_alignment":
            value["rules"] = rules
            return value
    return None


def build_execution_plan(engagement: dict[str, Any], body: StartAnalysisInput) -> dict[str, Any]:
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    source = Path(body.source_path).expanduser() if body.source_path else None
    repository = engagement.get("target_type") == "repository"
    if engagement["status"] != "ready" or not engagement.get("confirmed_at"):
        blockers.append({"id": "scope", "message": "ScopeSnapshot 尚未人工确认"})
    if repository and engagement["mode"] == "traditional" and body.execution_mode == "real":
        local_source = source or Path(engagement["normalized_target"]).expanduser()
        if not local_source.is_dir():
            blockers.append({"id": "source", "message": "远程仓库需先检出到本地源码目录"})
    if engagement["mode"] == "web3" and not repository and body.execution_mode == "real":
        if source is None or not source.is_dir():
            blockers.append({"id": "web3_source", "message": "链上目标需要可编译的本地 Solidity source"})
        parsed_rpc = urlparse(body.fork_rpc_url or "")
        if parsed_rpc.scheme != "https" or not parsed_rpc.netloc:
            blockers.append({"id": "fork_rpc", "message": "链上目标需要 HTTPS RPC 创建本地 Fork"})
        alignment = latest_deployment_alignment(engagement["id"])
        rules = alignment["rules"] if alignment else {}
        if not alignment:
            blockers.append({"id": "deployment_alignment", "message": "必须先证明本地源码与链上 Runtime Bytecode 一致"})
        elif rules.get("status") != "aligned":
            blockers.append({"id": "deployment_alignment", "message": "最新部署对齐未通过，不能把本地结果归因到链上合约"})
        else:
            if source and Path(alignment.get("source_uri") or "").resolve() != source.resolve():
                blockers.append({"id": "deployment_alignment", "message": "当前源码目录与已对齐 ProgramSnapshot 不一致"})
            if body.chain_id and rules.get("chain_id") != body.chain_id:
                blockers.append({"id": "deployment_alignment", "message": "当前 Chain ID 与已对齐 ProgramSnapshot 不一致"})
            if body.fork_block_number is not None and rules.get("block_number") != body.fork_block_number:
                blockers.append({"id": "deployment_alignment", "message": "当前 Fork 区块与已对齐 ProgramSnapshot 不一致"})
    if body.include_shannon and (engagement["mode"] != "traditional" or repository or not body.source_path):
        blockers.append({"id": "shannon", "message": "Shannon 需要 Traditional 运行 URL 和本地源码"})

    inventory = {item["id"]: item for item in capabilities()}
    from native_agent import readiness as native_agent_readiness
    native = native_agent_readiness()
    tools = []
    for capability in _planned_capabilities(engagement, body):
        item = native if capability == "native-agent" else inventory.get(capability, {})
        ready = bool(item.get("ready", item.get("available") and item.get("configured", True)))
        reason = "已就绪" if ready else ("需要模型 API 或系统 Chrome" if capability == "native-agent" else "未安装、未配置或运行时不可用")
        tools.append({"id": capability, "ready": ready, "version": item.get("version") or item.get("browser") or "未探测", "reason": reason, "optional": capability in {"native-agent", "strix", "shannon", "aderyn", "echidna", "medusa", "halmos"}})
        if not ready:
            warnings.append({"id": capability, "message": f"{capability} 会记录为 NOT TESTED：{reason}"})

    identities = list_identities(engagement["id"]) if engagement["mode"] == "traditional" else []
    ready_identities = [item for item in identities if item["session_status"] == "ready"]
    if identities and len(ready_identities) < len(identities):
        warnings.append({"id": "identity_session", "message": f"{len(identities)-len(ready_identities)} 个测试身份会话未就绪"})
    if len(ready_identities) < 2 and engagement["mode"] == "traditional":
        warnings.append({"id": "role_coverage", "message": "少于 2 个就绪身份，本次不能证明跨角色/跨租户隔离"})
    policy = engagement["policy"]
    return {
        "engagement_id": engagement["id"], "name": engagement["name"], "mode": engagement["mode"],
        "target": engagement["normalized_target"], "target_type": engagement["target_type"],
        "ready": not blockers, "blockers": blockers, "warnings": warnings, "tools": tools,
        "identities": {"total": len(identities), "ready": len(ready_identities)},
        "budget": {
            "requests": int(policy.get("max_requests", 100)),
            "requests_per_second": float(policy.get("max_requests_per_second", 1)),
            "runtime_minutes": int(policy.get("max_runtime_minutes", 30)),
            "tool_calls": int(policy.get("max_tool_calls", 200)),
            "model_usd": float(policy.get("max_model_budget_usd", 10)),
        },
        "actions": ["read", "analyze", "local_fork_write"] if engagement["mode"] == "web3" else ["read", "analyze", "scoped_http"],
        "denied_actions": ["destructive", "credential_attack", "fund_transfer", "production_write", "transaction_broadcast"],
        "remaining_stages": ["target", "scope", "surface", "analysis", "processing", "verification", "impact", "report"],
        "disclaimer": "这是有界执行计划，不预测结束时间；未覆盖项将进入 Coverage Ledger。",
    }


class MaintenanceConfirmInput(BaseModel):
    confirmation: str


def resolve_target_value(body: ResolveTargetInput) -> dict[str, Any]:
    raw = body.target.strip()
    if body.mode == "traditional":
        local = Path(raw).expanduser()
        if local.is_dir():
            normalized = str(local.resolve())
            return {
                "mode": body.mode, "raw_target": raw, "target_type": "repository",
                "normalized_target": normalized, "chain_id": None,
                "metadata": {"repository_kind": "local", "path": normalized},
            }
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        if raw.startswith("git@"):
            return {
                "mode": body.mode, "raw_target": raw, "target_type": "repository",
                "normalized_target": raw, "chain_id": None,
                "metadata": {"repository_kind": "git_ssh"},
            }
        if not parsed.hostname:
            raise HTTPException(422, "无法识别传统 SRC 目标")
        if parsed.path.lower().endswith(".git"):
            normalized = f"{parsed.scheme or 'https'}://{parsed.hostname.lower()}{parsed.path}"
            return {
                "mode": body.mode, "raw_target": raw, "target_type": "repository",
                "normalized_target": normalized, "chain_id": None,
                "metadata": {"repository_kind": "git_http", "host": parsed.hostname.lower()},
            }
        normalized = f"{parsed.scheme or 'https'}://{parsed.hostname.lower()}"
        if parsed.port:
            normalized += f":{parsed.port}"
        target_type = "url" if parsed.path not in ("", "/") else "host"
        metadata = {"host": parsed.hostname.lower(), "path": parsed.path or "/", "scheme": parsed.scheme or "https"}
    else:
        lower = raw.lower()
        if lower.startswith("0x") and len(lower) == 42:
            target_type, normalized = "contract", lower
        elif raw.endswith((".sol", ".vy")) or "/" in raw or raw.startswith("git@"):
            target_type, normalized = "repository", raw
        else:
            target_type, normalized = "protocol", raw
        metadata = {"chain_id": body.chain_id, "write_default": False, "fork_required_for_writes": True}
    return {
        "mode": body.mode,
        "raw_target": raw,
        "target_type": target_type,
        "normalized_target": normalized,
        "chain_id": body.chain_id,
        "metadata": metadata,
    }


@router.post("/targets/resolve")
def resolve_target(body: ResolveTargetInput):
    return resolve_target_value(body)


@router.post("/engagements", status_code=201)
def create_engagement(body: EngagementInput):
    resolved = resolve_target_value(body)
    timestamp = utcnow()
    target_id, engagement_id = uid("target"), uid("eng")
    scope_id, policy_id = uid("scope"), uid("policy")
    scope = {
        "allowed_targets": [resolved["normalized_target"]],
        "denied_targets": [],
        "allowed_actions": ["read", "analyze"],
        "denied_actions": ["destructive", "fund_transfer", "credential_attack"],
        "requires_confirmation": True,
        **body.scope,
    }
    policy = {
        "max_requests": 100,
        "max_requests_per_second": 1,
        "max_runtime_minutes": 30,
        "max_tool_calls": 200,
        "max_model_budget_usd": 10,
        "network_mode": "read_only" if body.mode == "web3" else "scoped",
        "allow_state_change": False,
        "allow_real_keys": False,
        **body.policy,
    }
    with connect() as db:
        db.execute("INSERT INTO target_specs VALUES(?,?,?,?,?,?,?,?)", (
            target_id, body.mode, resolved["raw_target"], resolved["target_type"],
            resolved["normalized_target"], body.chain_id, dump(resolved["metadata"]), timestamp,
        ))
        db.execute("INSERT INTO engagements_v2 VALUES(?,?,?,?,?,?,?,?,?)", (
            engagement_id, body.name, body.mode, target_id, "draft", scope_id, policy_id, timestamp, timestamp,
        ))
        db.execute("INSERT INTO scope_snapshots VALUES(?,?,?,?,?,?,?,?)", (
            scope_id, engagement_id, 1, body.mode, dump(scope), body.scope_source, None, timestamp,
        ))
        db.execute("INSERT INTO execution_policies VALUES(?,?,?,?,?)", (
            policy_id, engagement_id, 1, dump(policy), timestamp,
        ))
    return get_engagement(engagement_id)


@router.get("/engagements")
def list_engagements(mode: Literal["traditional", "web3"] | None = None):
    sql = """SELECT e.*,t.raw_target,t.target_type,t.normalized_target,t.chain_id,
             s.rules AS scope_rules,s.confirmed_at,p.policy
             FROM engagements_v2 e JOIN target_specs t ON t.id=e.target_spec_id
             JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id
             JOIN execution_policies p ON p.id=e.current_policy_id"""
    params: tuple[Any, ...] = ()
    if mode:
        sql += " WHERE e.mode=?"
        params = (mode,)
    else:
        sql += " WHERE e.status!='archived'"
    if mode:
        sql += " AND e.status!='archived'"
    sql += " ORDER BY e.created_at DESC"
    with connect() as db:
        rows = db.execute(sql, params).fetchall()
    return [hydrate_engagement(row) for row in rows]


def hydrate_engagement(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["scope"] = load(value.pop("scope_rules"), {})
    value["policy"] = load(value.pop("policy"), {})
    return value


@router.get("/engagements/{engagement_id}")
def get_engagement(engagement_id: str):
    with connect() as db:
        row = db.execute(
            """SELECT e.*,t.raw_target,t.target_type,t.normalized_target,t.chain_id,
               s.rules AS scope_rules,s.confirmed_at,p.policy
               FROM engagements_v2 e JOIN target_specs t ON t.id=e.target_spec_id
               JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id
               JOIN execution_policies p ON p.id=e.current_policy_id WHERE e.id=?""",
            (engagement_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Engagement 不存在")
    return hydrate_engagement(row)


@router.post("/engagements/{engagement_id}/confirm")
def confirm_engagement(engagement_id: str):
    timestamp = utcnow()
    with connect() as db:
        engagement = db.execute("SELECT * FROM engagements_v2 WHERE id=?", (engagement_id,)).fetchone()
        if not engagement:
            raise HTTPException(404, "Engagement 不存在")
        db.execute("UPDATE scope_snapshots SET confirmed_at=? WHERE id=? AND confirmed_at IS NULL", (timestamp, engagement["current_scope_snapshot_id"]))
        db.execute("UPDATE engagements_v2 SET status='ready',updated_at=? WHERE id=?", (timestamp, engagement_id))
    return get_engagement(engagement_id)


@router.patch("/engagements/{engagement_id}")
def update_engagement(engagement_id: str, body: EngagementUpdateInput):
    with connect() as db:
        changed = db.execute("UPDATE engagements_v2 SET name=?,updated_at=? WHERE id=?", (body.name.strip(), utcnow(), engagement_id))
    if not changed.rowcount:
        raise HTTPException(404, "Engagement 不存在")
    return get_engagement(engagement_id)


@router.delete("/engagements/{engagement_id}")
def archive_engagement(engagement_id: str):
    with connect() as db:
        active = db.execute("SELECT 1 FROM analysis_runs WHERE engagement_id=? AND status IN ('queued','running','paused')", (engagement_id,)).fetchone()
        if active:
            raise HTTPException(409, "运行中的项目不能归档，请先停止任务")
        changed = db.execute("UPDATE engagements_v2 SET status='archived',updated_at=? WHERE id=?", (utcnow(), engagement_id))
    if not changed.rowcount:
        raise HTTPException(404, "Engagement 不存在")
    return {"id": engagement_id, "status": "archived"}


@router.post("/engagements/bulk-archive")
def archive_recent_engagements(mode: Literal["traditional", "web3"]):
    """Hide a work domain's recent projects without deleting its evidence chain."""
    with connect() as db:
        active = db.execute(
            """SELECT COUNT(*) FROM analysis_runs r
               JOIN engagements_v2 e ON e.id=r.engagement_id
               WHERE e.mode=? AND e.status!='archived'
               AND r.status IN ('queued','running','paused')""",
            (mode,),
        ).fetchone()[0]
        if active:
            raise HTTPException(409, f"当前工作域有 {active} 个活动任务，请先停止或等待任务结束")
        changed = db.execute(
            "UPDATE engagements_v2 SET status='archived',updated_at=? WHERE mode=? AND status!='archived'",
            (utcnow(), mode),
        )
    return {"mode": mode, "archived": changed.rowcount, "evidence_preserved": True}


def add_event(run_id: str, stage: str, kind: str, message: str, payload: dict[str, Any] | None = None) -> None:
    with connect() as db:
        db.execute("INSERT INTO run_events_v2(run_id,stage,kind,message,payload,created_at) VALUES(?,?,?,?,?,?)", (
            run_id, stage, kind, redact(message), dump(load(redact(dump(payload or {})), {})), utcnow(),
        ))


def consume_run_budget(run_id: str, category: str, amount: int = 1) -> tuple[bool, str]:
    columns = {
        "request": ("requests_used", "request_limit"),
        "tool_call": ("tool_calls_used", "tool_call_limit"),
        "model_cost_micros": ("model_cost_micros", "model_budget_micros"),
    }
    if category not in columns or amount < 0:
        return False, "invalid_budget_category"
    used, limit = columns[category]
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM run_budgets_v2 WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            db.rollback()
            return False, "budget_not_found"
        if row[used] + amount > row[limit]:
            db.execute("UPDATE analysis_runs SET status='budget_exhausted',stopped_at=? WHERE id=?", (utcnow(), run_id))
            db.commit()
            return False, f"{category}_budget_exhausted"
        db.execute(f"UPDATE run_budgets_v2 SET {used}={used}+?,updated_at=? WHERE run_id=?", (amount, utcnow(), run_id))
        db.commit()
        return True, "budget_consumed"
    finally:
        db.close()


async def safe_demo_pipeline(run_id: str) -> None:
    stages = [
        ("target", "目标解析", "目标已规范化并绑定 TargetSpec"),
        ("scope", "Scope", "不可变 ScopeSnapshot 已加载"),
        ("surface", "攻击面", "本地安全演示未发起外部扫描"),
        ("analysis", "自动分析", "能力路由已完成；未配置真实工具"),
        ("processing", "数据处理", "Tool Result 仅转换为 synthetic Observation"),
        ("verification", "漏洞验证", "无真实候选，未生成 CanonicalFinding"),
        ("impact", "影响判断", "无已验证漏洞，跳过影响评估"),
        ("report", "报告", "ReportCompiler 无可消费 CanonicalFinding"),
    ]
    with connect() as db:
        db.execute("UPDATE analysis_runs SET status='running',started_at=?,current_stage='target' WHERE id=?", (utcnow(), run_id))
    for stage, label, message in stages:
        with connect() as db:
            if db.execute("SELECT 1 FROM checkpoints WHERE run_id=? AND stage=?", (run_id, stage)).fetchone():
                continue
        await asyncio.sleep(.18)
        with connect() as db:
            status = db.execute("SELECT status FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
            if not status or status["status"] in {"stopped", "paused"}:
                return
            if db.execute("SELECT 1 FROM checkpoints WHERE run_id=? AND stage=?", (run_id, stage)).fetchone():
                continue
            db.execute("UPDATE analysis_runs SET current_stage=? WHERE id=?", (stage, run_id))
            inserted = db.execute("INSERT OR IGNORE INTO checkpoints VALUES(?,?,?,?,?)", (uid("checkpoint"), run_id, stage, dump({"label": label}), utcnow()))
            if not inserted.rowcount:
                continue
        add_event(run_id, stage, "stage.completed", message, {"label": label, "synthetic": True})
    with connect() as db:
        db.execute("UPDATE analysis_runs SET status='completed',current_stage='report',completed_at=? WHERE id=?", (utcnow(), run_id))
    add_event(run_id, "report", "run.completed", "安全演示完成；不代表真实扫描或漏洞验证", {"synthetic": True})


@router.post("/engagements/{engagement_id}/execution-plan")
def execution_plan(engagement_id: str, body: StartAnalysisInput | None = None):
    return build_execution_plan(get_engagement(engagement_id), body or StartAnalysisInput())


@router.post("/engagements/{engagement_id}/start", status_code=202)
async def start_analysis(engagement_id: str, body: StartAnalysisInput | None = None):
    test_demo_enabled = os.getenv("SRC_ENABLE_SYNTHETIC_DEMO") == "1"
    body = body or StartAnalysisInput(execution_mode="demo" if test_demo_enabled else "real")
    if body.execution_mode == "demo" and not test_demo_enabled:
        raise HTTPException(422, "生产模式不提供演示执行，请启动真实工具链")
    engagement = get_engagement(engagement_id)
    if engagement["status"] != "ready" or not engagement["confirmed_at"]:
        raise HTTPException(409, "必须先人工确认 ScopeSnapshot")
    if body.execution_mode == "real" and engagement["mode"] == "traditional" and engagement.get("target_type") == "repository":
        source_path = body.source_path or engagement["normalized_target"]
        if not Path(source_path).expanduser().is_dir():
            raise HTTPException(422, "远程仓库必须先在本地检出，并通过 source_path 指向目录")
        body = body.model_copy(update={"source_path": str(Path(source_path).expanduser().resolve()), "include_code": True, "include_recon": False})
    if body.include_shannon and (engagement["mode"] != "traditional" or engagement.get("target_type") != "host" or not body.source_path):
        raise HTTPException(422, "Shannon 仅用于同时提供运行 URL 与本地源码目录的 Traditional 白盒任务")
    with connect() as db:
        active_count = db.execute("SELECT COUNT(*) AS n FROM analysis_runs WHERE status IN ('queued','running','paused')").fetchone()["n"]
        if active_count >= 4:
            raise HTTPException(409, "当前并发任务已达到 4 个，请等待或停止一个任务")
        run_id = uid("run")
        synthetic = body.execution_mode == "demo"
        if body.execution_mode == "real" and engagement["mode"] == "web3" and engagement.get("target_type") != "repository":
            source = Path(body.source_path).expanduser() if body.source_path else None
            if source is None or not source.is_dir():
                raise HTTPException(422, "Web3 链上目标必须配置包含 Solidity 源码的本地 source 目录")
            if not body.fork_rpc_url:
                raise HTTPException(422, "Web3 链上目标必须配置 HTTPS RPC Fork 地址")
            parsed_rpc = urlparse(body.fork_rpc_url)
            if parsed_rpc.scheme != "https" or not parsed_rpc.netloc:
                raise HTTPException(422, "RPC Fork 地址必须使用 HTTPS")
            alignment = latest_deployment_alignment(engagement_id)
            rules = alignment["rules"] if alignment else {}
            if not alignment or rules.get("status") != "aligned":
                raise HTTPException(409, "必须先完成 source/compiler/runtime bytecode 部署对齐")
            if Path(alignment.get("source_uri") or "").resolve() != source.resolve():
                raise HTTPException(409, "当前 source 与已对齐 ProgramSnapshot 不一致")
            if body.chain_id and rules.get("chain_id") != body.chain_id:
                raise HTTPException(409, "当前 Chain ID 与已对齐 ProgramSnapshot 不一致")
            if body.fork_block_number is not None and rules.get("block_number") != body.fork_block_number:
                raise HTTPException(409, "当前 Fork 区块与已对齐 ProgramSnapshot 不一致")
            body = body.model_copy(update={"source_path": str(source.resolve()), "include_code": True, "include_recon": False, "include_native_agent": False})
        db.execute("INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            run_id, engagement_id, engagement["mode"], engagement["current_scope_snapshot_id"],
            engagement["current_policy_id"], "queued", "target", int(synthetic), None, None, None, None, utcnow(),
        ))
        policy = engagement["policy"]
        db.execute("INSERT INTO run_budgets_v2 VALUES(?,?,?,?,?,?,?,?)", (
            run_id, int(policy.get("max_requests", 100)), 0, int(policy.get("max_tool_calls", 200)), 0,
            int(float(policy.get("max_model_budget_usd", 10)) * 1_000_000), 0, utcnow(),
        ))
        stored_config = body.model_dump()
        if stored_config.get("fork_rpc_url"):
            stored_config["fork_rpc_url"] = None
            stored_config["fork_rpc_configured"] = True
        db.execute("INSERT INTO run_configs_v2 VALUES(?,?,?)", (run_id, dump(stored_config), utcnow()))
    if synthetic:
        add_event(run_id, "target", "run.queued", "已进入本地安全演示队列", {"synthetic": True})
        asyncio.create_task(safe_demo_pipeline(run_id))
    else:
        from traditional_tools import TraditionalToolchainInput, checkout_and_execute_repository, execute_toolchain
        tool_input = TraditionalToolchainInput(
            include_recon=body.include_recon, include_code=body.include_code,
            source_path=body.source_path, timeout_seconds=body.timeout_seconds,
            max_discovered_targets=body.max_discovered_targets,
            scan_profile=body.scan_profile,
            include_strix=body.include_strix,
            include_shannon=body.include_shannon,
            include_native_agent=body.include_native_agent,
        )
        if engagement["mode"] == "web3" and engagement.get("target_type") != "repository":
            from traditional_tools import execute_web3_source_pipeline
            add_event(run_id, "target", "run.queued", "已进入 Web3 源码 + 本地 Fork 真实审计队列", {"synthetic": False})
            asyncio.create_task(execute_web3_source_pipeline(run_id, tool_input, body.fork_rpc_url, body.fork_block_number, body.chain_id))
        elif engagement["mode"] == "web3" and engagement.get("target_type") == "repository" and not body.source_path:
            add_event(run_id, "target", "run.queued", "已进入真实 Web3 仓库代码审计队列", {"synthetic": False})
            asyncio.create_task(checkout_and_execute_repository(run_id, engagement["normalized_target"], tool_input))
        else:
            add_event(run_id, "target", "run.queued", "已进入真实 Traditional 工具链队列", {"synthetic": False})
            asyncio.create_task(execute_toolchain(run_id, tool_input))
    return {"id": run_id, "status": "queued", "synthetic": synthetic}


def hydrate_run(row: sqlite3.Row, events: list[sqlite3.Row]) -> dict[str, Any]:
    value = dict(row)
    value["synthetic"] = bool(value["synthetic"])
    value["events"] = [{**dict(e), "payload": load(e["payload"], {})} for e in events]
    return value


@router.get("/runs")
def list_runs(mode: Literal["traditional", "web3"] | None = None):
    sql, params = "SELECT * FROM analysis_runs", ()
    if mode:
        sql += " WHERE mode=?"
        params = (mode,)
    sql += " ORDER BY created_at DESC"
    with connect() as db:
        rows = db.execute(sql, params).fetchall()
        return [hydrate_run(row, db.execute("SELECT * FROM run_events_v2 WHERE run_id=? ORDER BY id", (row["id"],)).fetchall()) for row in rows]


@router.get("/task-center")
def task_center(mode: Literal["traditional", "web3"] | None = None):
    runs = list_runs(mode)
    ordered_queued = [run["id"] for run in reversed(runs) if run["status"] == "queued"]
    result = []
    for run in runs:
        completed = {event["stage"] for event in run["events"] if event["kind"] == "stage.completed"}
        all_stages = ("target", "scope", "surface", "analysis", "processing", "verification", "impact", "report")
        if run["status"] == "completed":
            completed = set(all_stages)
        remaining = [stage for stage in all_stages if stage not in completed]
        last = run["events"][-1] if run["events"] else None
        last_at = last["created_at"] if last else run["created_at"]
        try:
            quiet_seconds = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(last_at)).total_seconds()))
        except (TypeError, ValueError):
            quiet_seconds = 0
        stalled = run["status"] == "running" and quiet_seconds >= 180
        if stalled:
            next_action = "运行已超过 3 分钟无新事件，请检查工具详情，必要时停止后重跑"
        elif run["status"] == "paused":
            next_action = "恢复后从首个缺失 checkpoint 继续"
        elif run["status"] == "queued":
            next_action = "等待执行槽位"
        elif run["status"] in {"failed", "timeout", "budget_exhausted"}:
            next_action = "查看最后事件，修复阻塞项后重新运行"
        elif run["status"] == "completed":
            next_action = "查看覆盖报告和漏洞结果"
        else:
            next_action = "继续监看实时证据"
        result.append({
            "id": run["id"], "engagement_id": run["engagement_id"], "mode": run["mode"],
            "status": run["status"], "current_stage": run["current_stage"],
            "remaining_stages": remaining, "completed_stages": len(completed),
            "queue_position": ordered_queued.index(run["id"]) + 1 if run["id"] in ordered_queued else None,
            "last_event_at": last_at, "last_event": last["message"] if last else "尚无执行事件",
            "quiet_seconds": quiet_seconds, "stalled": stalled, "next_action": next_action,
        })
    counts = {status: sum(item["status"] == status for item in result) for status in ("queued", "running", "paused", "completed", "failed")}
    return {"counts": counts, "stall_timeout_seconds": 180, "items": result}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
        events = db.execute("SELECT * FROM run_events_v2 WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
    if not row:
        raise HTTPException(404, "Run 不存在")
    return hydrate_run(row, events)


@router.get("/runs/{run_id}/details")
def get_run_details(run_id: str):
    run = get_run(run_id)
    engagement = get_engagement(run["engagement_id"])
    with connect() as db:
        config_row = db.execute("SELECT config FROM run_configs_v2 WHERE run_id=?", (run_id,)).fetchone()
        coverage_rows = db.execute("SELECT * FROM coverage_v2 WHERE run_id=? ORDER BY updated_at", (run_id,)).fetchall()
        observations = [dict(row) for row in db.execute(
            "SELECT id,observation_type,subject,summary,confidence,source_capability,raw_ref,created_at FROM observations WHERE run_id=? ORDER BY created_at", (run_id,),
        )]
        artifacts = [dict(row) for row in db.execute(
            "SELECT id,kind,sha256,media_type,redacted,created_at FROM artifacts WHERE run_id=? ORDER BY created_at", (run_id,),
        )]
        verified_count = db.execute(
            "SELECT COUNT(*) FROM canonical_findings f JOIN candidate_findings c ON c.id=f.candidate_id WHERE c.run_id=? AND f.status='verified'", (run_id,),
        ).fetchone()[0]
        candidate_count = db.execute("SELECT COUNT(*) FROM candidate_findings WHERE run_id=? AND status!='archived'", (run_id,)).fetchone()[0]
    config = load(config_row["config"], {}) if config_row else {}
    coverage = []
    by_capability: dict[str, list[dict[str, Any]]] = {}
    for row in coverage_rows:
        item = dict(row)
        item["observation_ids"] = load(item["observation_ids"], [])
        coverage.append(item)
        parts = item["surface_key"].split(":", 2)
        if len(parts) > 1 and parts[0] == "capability":
            by_capability.setdefault(parts[1], []).append(item)
    repository = engagement.get("target_type") == "repository"
    if repository:
        planned = [
            ("checkout", "检出并固定仓库版本", "target", "以浅克隆获取授权源码并记录 commit"),
            ("semgrep", "静态代码规则分析", "analysis", "检查危险 API、注入、权限和语言规则命中"),
            ("gitleaks", "密钥与凭据泄露检查", "analysis", "检查当前源码树中的 Secret 模式"),
            ("trivy", "依赖、Secret 与错误配置检查", "analysis", "扫描依赖漏洞、配置风险和敏感信息"),
        ]
        if engagement.get("mode") == "web3":
            planned[1:1] = [
                ("forge-build", "生产合约编译", "analysis", "仅编译可部署合约，隔离测试夹具故障"),
                ("forge-test", "Forge 单元与 Fuzz 测试", "analysis", "运行仓库测试并单独记录失败与覆盖"),
                ("slither", "Slither Solidity 静态分析", "analysis", "检查重入、权限、数据流和危险调用"),
                ("aderyn", "Aderyn Rust 静态分析", "analysis", "使用独立 Rust AST 检测器交叉检查 Solidity 风险"),
                ("echidna", "Echidna 属性测试", "analysis", "执行仓库提供的安全不变量与状态序列测试"),
                ("medusa", "Medusa 并行属性测试", "analysis", "并行探索合约状态空间和不变量反例"),
                ("halmos", "Halmos 符号执行", "analysis", "执行 check_/invariant_ 符号测试；无测试入口时明确标记未测试"),
            ]
        if config.get("include_strix"):
            planned.append(("strix", "智能代码审计", "analysis", "在沙箱内执行模型驱动的代码探索"))
        if config.get("include_shannon"):
            planned.append(("shannon", "Shannon 白盒漏洞证明", "verification", "结合源码与运行目标执行真实 PoC 验证"))
    else:
        planned = [
            ("subfinder", "子域资产发现", "surface", "发现授权域名下的子域资产"),
            ("httpx", "HTTP 服务与技术识别", "surface", "确认响应服务、状态码和技术特征"),
            ("katana", "页面与接口路径爬取", "surface", "发现可访问路由、页面和接口入口"),
            ("nuclei", "漏洞模板与配置检测", "analysis", "执行所选档位的安全模板并排除 DoS、fuzz、intrusive"),
        ]
        if config.get("include_code") or config.get("source_path"):
            planned.extend([
                ("semgrep", "静态代码规则分析", "analysis", "将本地源码与运行目标进行白盒关联"),
                ("gitleaks", "密钥与凭据泄露检查", "analysis", "检查本地源码树中的 Secret 模式"),
                ("trivy", "依赖与错误配置检查", "analysis", "扫描本地源码依赖、配置和敏感信息"),
            ])
        if config.get("include_strix"):
            planned.append(("strix", "智能 Web 审计", "analysis", "在授权范围内执行模型驱动探索"))
        if config.get("include_shannon"):
            planned.append(("shannon", "Shannon 白盒漏洞证明", "verification", "需要同时提供运行 URL 与本地源码目录"))
        if config.get("include_native_agent", True):
            planned.append(("native-agent", "Native Agent 只读研究", "analysis", "在 Scope、DNS/IP 与请求预算约束下使用系统 Chrome 探索页面并提出待复验假设"))
    event_kinds = {event["kind"] for event in run["events"]}
    terminal = run["status"] in {"completed", "failed", "stopped", "timeout", "budget_exhausted"}
    test_items = []
    for capability, label, stage, description in planned:
        rows = by_capability.get(capability, [])
        if capability == "checkout":
            if "repository.checkout_completed" in event_kinds:
                status, result, count = "completed", next((e["message"] for e in reversed(run["events"]) if e["kind"] == "repository.checkout_completed"), "仓库检出完成"), 0
            elif "repository.checkout_failed" in event_kinds:
                status, result, count = "failed", next((e["message"] for e in reversed(run["events"]) if e["kind"] == "repository.checkout_failed"), "仓库检出失败"), 0
            elif Path(engagement["normalized_target"]).is_dir():
                status, result, count = "completed", "已固定本地授权仓库路径，无需远程检出", 0
            else:
                status, result = ("running", "正在检出") if run["status"] in {"queued", "running"} else ("not_tested", "本地仓库无需远程检出")
                count = 0
        elif rows:
            failed = any(row["reason"] not in {"completed", "passed", "compiled"} for row in rows)
            status = "failed" if failed else "completed"
            result = "；".join(dict.fromkeys(row["reason"] for row in rows))
            count = sum(len(row["observation_ids"]) for row in rows)
        elif terminal:
            status, result, count = "not_tested", "本次运行未产生该测试项的覆盖记录", 0
        else:
            status, result, count = "queued", "等待上游测试完成", 0
        test_items.append({"id": capability, "label": label, "stage": stage, "description": description, "status": status, "result": result, "observation_count": count})
    stages_detail = [
        {"id": "target", "label": "目标与版本", "status": "completed" if ("repository.checkout_completed" in event_kinds or "toolchain.started" in event_kinds) else run["status"], "summary": engagement["normalized_target"]},
        {"id": "scope", "label": "授权范围与策略", "status": "completed", "summary": f"{engagement['policy'].get('max_requests_per_second', 1)} req/s · {engagement['policy'].get('max_runtime_minutes', 30)} min · read/analyze"},
        {"id": "surface", "label": "攻击面与输入清单", "status": "completed" if terminal else run["status"], "summary": f"{len([x for x in test_items if x['stage']=='surface'])} 个表面测试项"},
        {"id": "analysis", "label": "工具执行", "status": "completed" if terminal else run["status"], "summary": f"{len(test_items)} 个计划项，{sum(x['status']=='completed' for x in test_items)} 完成，{sum(x['status']=='failed' for x in test_items)} 失败"},
        {"id": "processing", "label": "结果归一化", "status": "completed" if terminal else "queued", "summary": f"{len(observations)} 条 Observation · {len(artifacts)} 个 Artifact"},
        {"id": "verification", "label": "候选与独立复验", "status": "completed" if terminal else "queued", "summary": f"{candidate_count} 个 Candidate · {verified_count} 个 Verified"},
        {"id": "impact", "label": "影响与可提交性", "status": "completed" if terminal else "queued", "summary": "无 Verified Finding，未形成影响结论" if not verified_count else f"{verified_count} 个结果进入影响评估"},
        {"id": "report", "label": "覆盖与总结报告", "status": "completed" if terminal else "queued", "summary": "运行总结与 Coverage Ledger 已生成" if terminal else "等待运行进入终态"},
    ]
    return {"run_id": run_id, "engagement": {"id": engagement["id"], "name": engagement["name"], "target": engagement["normalized_target"], "mode": engagement["mode"], "target_type": engagement["target_type"]}, "config": config, "stages": stages_detail, "test_items": test_items, "observations": observations, "artifacts": artifacts, "coverage": coverage}


@router.get("/runs/{run_id}/events")
async def stream_run_events(run_id: str):
    with connect() as db:
        if not db.execute("SELECT 1 FROM analysis_runs WHERE id=?", (run_id,)).fetchone():
            raise HTTPException(404, "Run 不存在")
    async def generate():
        cursor, quiet = 0, 0
        while quiet < 60:
            with connect() as db:
                rows = db.execute("SELECT * FROM run_events_v2 WHERE run_id=? AND id>? ORDER BY id", (run_id, cursor)).fetchall()
                run = db.execute("SELECT status FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
            if rows:
                quiet = 0
                for row in rows:
                    cursor = row["id"]
                    value = dict(row)
                    value["payload"] = load(value["payload"], {})
                    yield f"data: {json.dumps(value, ensure_ascii=False)}\n\n"
            else:
                quiet += 1
            if run and run["status"] in {"completed", "stopped", "failed"} and not rows:
                break
            await asyncio.sleep(.25)
    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/runs/{run_id}/observations", status_code=201)
def record_observation(run_id: str, body: ObservationInput):
    consumed, reason = consume_run_budget(run_id, "tool_call", 1)
    if not consumed:
        raise HTTPException(409, reason)
    with connect() as db:
        run = db.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "Run 不存在")
        if run["status"] not in {"running", "paused", "completed"}:
            raise HTTPException(409, "Run 状态不允许记录 Observation")
        observation_id = uid("obs")
        db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            observation_id, run_id, run["engagement_id"], run["mode"], body.observation_type,
            body.subject, body.summary, body.confidence, body.source_capability, body.raw_ref, utcnow(),
        ))
        entity_key = f"{body.observation_type}:{body.subject}".lower()
        entity_id = uid("entity")
        db.execute("""INSERT INTO entities VALUES(?,?,?,?,?,?,?)
          ON CONFLICT(engagement_id,canonical_key) DO UPDATE SET attributes=excluded.attributes""", (
            entity_id, run["engagement_id"], body.observation_type, entity_key, body.subject,
            dump({"source": body.source_capability, "confidence": body.confidence}), utcnow(),
        ))
        coverage_state = "tested" if body.confidence >= .7 else "observed"
        db.execute("""INSERT INTO coverage_v2 VALUES(?,?,?,?,?,?,?)
          ON CONFLICT(run_id,surface_key) DO UPDATE SET state=excluded.state,reason=excluded.reason,
          observation_ids=excluded.observation_ids,updated_at=excluded.updated_at""", (
            uid("coverage"), run_id, body.subject, coverage_state,
            f"Observed by {body.source_capability}", dump([observation_id]), utcnow(),
        ))
    add_event(run_id, "processing", "observation.recorded", f"{body.source_capability} 输出已规范化为 Observation", {"observation_id": observation_id})
    return {"id": observation_id, **body.model_dump()}


@router.post("/runs/{run_id}/candidates", status_code=201)
def create_candidate(run_id: str, body: CandidateInput):
    with connect() as db:
        run = db.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "Run 不存在")
        placeholders = ",".join("?" for _ in body.observation_ids)
        observations = db.execute(
            f"SELECT * FROM observations WHERE run_id=? AND id IN ({placeholders})",
            (run_id, *body.observation_ids),
        ).fetchall()
        if len(observations) != len(set(body.observation_ids)):
            raise HTTPException(422, "Observation 必须存在且属于当前 Run")
        evidence_ids = []
        for observation in observations:
            evidence_id = uid("evidence")
            evidence_ids.append(evidence_id)
            db.execute("INSERT INTO evidence_v2 VALUES(?,?,?,?,?,?,?,?)", (
                evidence_id, observation["id"], run_id, observation["observation_type"],
                observation["summary"], None, "supporting", utcnow(),
            ))
        candidate_id = uid("candidate")
        timestamp = utcnow()
        db.execute("INSERT INTO candidate_findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            candidate_id, run_id, run["engagement_id"], run["mode"], body.title,
            body.category, body.target, body.hypothesis, "candidate", dump(evidence_ids), timestamp, timestamp,
        ))
    add_event(run_id, "verification", "candidate.created", "Observation 已关联为候选，尚未验证", {"candidate_id": candidate_id})
    return {"id": candidate_id, "status": "candidate", "evidence_ids": evidence_ids, **body.model_dump()}


@router.post("/runs/{run_id}/correlate")
def correlate_observations(run_id: str):
    with connect() as db:
        run = db.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "Run 不存在")
        rows = db.execute("""SELECT subject,COUNT(*) AS count,AVG(confidence) AS confidence,
          GROUP_CONCAT(DISTINCT source_capability) AS sources
          FROM observations WHERE run_id=? GROUP BY subject HAVING COUNT(*)>=2""", (run_id,)).fetchall()
    created = []
    for row in rows:
        with connect() as db:
            existing = db.execute("SELECT id FROM candidate_findings WHERE run_id=? AND target=?", (run_id, row["subject"])).fetchone()
            observation_ids = [x["id"] for x in db.execute("SELECT id FROM observations WHERE run_id=? AND subject=?", (run_id, row["subject"]))]
        if existing:
            continue
        result = create_candidate(run_id, CandidateInput(
            title=f"Correlated hypothesis: {row['subject']}", category="correlated_observation",
            target=row["subject"], hypothesis=f"{row['count']} observations from {row['sources']} require controlled verification",
            observation_ids=observation_ids,
        ))
        created.append(result)
    return {"run_id": run_id, "created": created, "count": len(created)}


@router.post("/candidates/{candidate_id}/verify")
def verify_candidate(candidate_id: str, body: VerificationInput):
    with connect() as db:
        candidate = db.execute("SELECT * FROM candidate_findings WHERE id=?", (candidate_id,)).fetchone()
        if not candidate:
            raise HTTPException(404, "Candidate 不存在")
        if candidate["status"] == "verified":
            raise HTTPException(409, "Candidate 已验证")
        run = db.execute("SELECT * FROM analysis_runs WHERE id=?", (candidate["run_id"],)).fetchone()
        if not run:
            raise HTTPException(409, "Candidate 没有可追溯 Run")
        scope = db.execute("SELECT * FROM scope_snapshots WHERE id=? AND confirmed_at IS NOT NULL", (run["scope_snapshot_id"],)).fetchone()
        evidence_ids = load(candidate["evidence_ids"], [])
        if not scope or not evidence_ids:
            raise HTTPException(409, "Verified Finding 必须绑定已确认 ScopeSnapshot 和 Evidence")
        program_snapshot = None
        if candidate["mode"] == "web3":
            if not body.program_snapshot_id:
                raise HTTPException(409, "Web3 Verification 必须绑定 ProgramSnapshot")
            program_snapshot = db.execute("SELECT * FROM program_snapshots WHERE id=? AND engagement_id=?", (body.program_snapshot_id, candidate["engagement_id"])).fetchone()
            if not program_snapshot:
                raise HTTPException(409, "ProgramSnapshot 不存在或不属于当前 Engagement")
        unsafe_oracle = "demo" in body.oracle.lower() or "synthetic" in body.oracle.lower()
        web3_gates = candidate["mode"] != "web3" or all(x is True for x in (
            body.impact_in_scope, body.known_issue_checked, body.previous_audit_checked, body.poc_rule_checked,
        ))
        passed = body.reproduced and body.attempts >= 2 and body.counterevidence_checked and bool(body.counterevidence_summary.strip()) and not unsafe_oracle and web3_gates
        attempt_id = uid("verify")
        result = {"reproduced": body.reproduced, "counterevidence_checked": body.counterevidence_checked, "unsafe_oracle": unsafe_oracle}
        db.execute("INSERT INTO verification_attempts VALUES(?,?,?,?,?,?,?,?)", (
            attempt_id, candidate_id, body.oracle, "passed" if passed else "human_review", body.attempts, dump(result), utcnow(), utcnow(),
        ))
        if not passed:
            db.execute("UPDATE candidate_findings SET status='human_review',updated_at=? WHERE id=?", (utcnow(), candidate_id))
            return {"candidate_id": candidate_id, "status": "human_review", "verification_attempt_id": attempt_id, "reasons": {
                "requires_two_replays": body.attempts < 2, "counterevidence_required": not body.counterevidence_checked,
                "reproduction_required": not body.reproduced, "synthetic_oracle_forbidden": unsafe_oracle,
                "web3_program_gates_required": not web3_gates,
            }}
        counter_id = uid("evidence")
        db.execute("INSERT INTO evidence_v2 VALUES(?,?,?,?,?,?,?,?)", (
            counter_id, None, run["id"], "counterevidence", body.counterevidence_summary, None, "counter", utcnow(),
        ))
        evidence_ids.append(counter_id)
        finding_id = uid("finding")
        verification = {
            "oracle": body.oracle, "attempts": body.attempts, "attempt_id": attempt_id,
            "steps": body.steps, "expected": body.expected, "actual": body.actual,
            "root_cause": body.root_cause, "weakness": body.weakness, "location": body.location,
            "poc_artifact_ids": body.poc_artifact_ids, "summary": candidate["hypothesis"],
        }
        impact = {"description": body.impact_description, "demonstrated": True, "feasibility": body.feasibility, "funds_at_risk": body.funds_at_risk}
        eligibility = {
            "in_scope": True if candidate["mode"] == "traditional" else body.impact_in_scope,
            "scope_snapshot_id": run["scope_snapshot_id"], "program_snapshot_id": body.program_snapshot_id,
            "known_issue_checked": body.known_issue_checked, "previous_audit_checked": body.previous_audit_checked,
            "poc_rule_checked": body.poc_rule_checked,
        }
        timestamp = utcnow()
        db.execute("INSERT INTO canonical_findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            finding_id, candidate_id, candidate["engagement_id"], candidate["mode"], candidate["title"],
            candidate["category"], body.severity, candidate["target"], dump(impact), dump(eligibility),
            dump(verification), dump(evidence_ids), "verified", timestamp, timestamp,
        ))
        db.execute("UPDATE candidate_findings SET status='verified',updated_at=? WHERE id=?", (timestamp, candidate_id))
    add_event(run["id"], "verification", "finding.verified", "独立 Oracle 与反证门槛通过，已生成 CanonicalFinding", {"finding_id": finding_id})
    return {"id": finding_id, "candidate_id": candidate_id, "status": "verified", "evidence_ids": evidence_ids, "scope_snapshot_id": run["scope_snapshot_id"]}


@router.post("/findings/{candidate_id}/verify")
def verify_finding_contract(candidate_id: str, body: VerificationInput):
    """OpenAPI compatibility route; the identifier is a Candidate until verification succeeds."""
    return verify_candidate(candidate_id, body)


@router.post("/runs/{run_id}/pause")
def pause_run(run_id: str):
    with connect() as db:
        cur = db.execute("UPDATE analysis_runs SET status='paused',paused_at=? WHERE id=? AND status='running'", (utcnow(), run_id))
    if not cur.rowcount:
        raise HTTPException(409, "仅运行中的任务可暂停")
    add_event(run_id, "control", "run.paused", "用户暂停了分析")
    return get_run(run_id)


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str):
    with connect() as db:
        run = db.execute("SELECT * FROM analysis_runs WHERE id=? AND status='paused'", (run_id,)).fetchone()
        config_row = db.execute("SELECT config FROM run_configs_v2 WHERE run_id=?", (run_id,)).fetchone()
        cur = db.execute("UPDATE analysis_runs SET status='running',paused_at=NULL WHERE id=? AND status='paused'", (run_id,))
    if not cur.rowcount:
        raise HTTPException(409, "仅暂停任务可恢复")
    add_event(run_id, "control", "run.resumed", "用户恢复了分析")
    if run and not bool(run["synthetic"]) and run["mode"] == "traditional":
        from traditional_tools import TraditionalToolchainInput, execute_toolchain
        config = load(config_row["config"], {}) if config_row else {}
        asyncio.create_task(execute_toolchain(run_id, TraditionalToolchainInput(
            include_recon=bool(config.get("include_recon", True)),
            include_code=bool(config.get("include_code", False)),
            source_path=config.get("source_path"), timeout_seconds=int(config.get("timeout_seconds", 120)),
            max_discovered_targets=int(config.get("max_discovered_targets", 25)),
            scan_profile=config.get("scan_profile", "quick"),
            include_strix=bool(config.get("include_strix", False)),
            include_shannon=bool(config.get("include_shannon", False)),
        )))
    elif os.getenv("SRC_ENABLE_SYNTHETIC_DEMO") == "1":
        asyncio.create_task(safe_demo_pipeline(run_id))
    else:
        raise HTTPException(409, "演示运行不能在生产模式恢复")
    return get_run(run_id)


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str):
    with connect() as db:
        cur = db.execute("UPDATE analysis_runs SET status='stopped',stopped_at=? WHERE id=? AND status IN ('queued','running','paused')", (utcnow(), run_id))
    if not cur.rowcount:
        raise HTTPException(409, "任务不存在或已结束")
    add_event(run_id, "control", "run.stopped", "用户安全停止了分析")
    return get_run(run_id)


@router.get("/findings")
def list_findings(mode: Literal["traditional", "web3"] | None = None, run_id: str | None = None):
    with connect() as db:
        clauses, params = ["c.status!='archived'"], []
        if mode:
            clauses.append("c.mode=?")
            params.append(mode)
        if run_id:
            clauses.append("c.run_id=?")
            params.append(run_id)
        where = " WHERE " + " AND ".join(clauses)
        candidates = [dict(row) for row in db.execute("SELECT c.* FROM candidate_findings c" + where + " ORDER BY c.created_at DESC", tuple(params))]
        verified = [dict(row) for row in db.execute("SELECT f.*,c.run_id AS run_id FROM canonical_findings f JOIN candidate_findings c ON c.id=f.candidate_id" + where.replace("c.status!='archived'", "c.status!='archived' AND f.status!='archived'") + " ORDER BY f.created_at DESC", tuple(params))]
    for row in verified:
        for key in ("impact", "eligibility", "verification", "evidence_ids"):
            row[key] = load(row[key], {} if key != "evidence_ids" else [])
    for row in candidates:
        row["evidence_ids"] = load(row["evidence_ids"], [])
    return {"verified": verified, "candidates": candidates}


@router.patch("/findings/{finding_id}")
def update_finding(finding_id: str, body: FindingUpdateInput):
    values = body.model_dump(exclude_none=True)
    if not values:
        raise HTTPException(422, "至少提供一个可编辑字段")
    with connect() as db:
        candidate = db.execute("SELECT id FROM candidate_findings WHERE id=?", (finding_id,)).fetchone()
        if candidate:
            allowed = {k: v.strip() for k, v in values.items() if k in {"title", "hypothesis"}}
            if not allowed:
                raise HTTPException(422, "候选记录只允许编辑标题和判断说明")
            db.execute("UPDATE candidate_findings SET " + ",".join(f"{key}=?" for key in allowed) + ",updated_at=? WHERE id=?", (*allowed.values(), utcnow(), finding_id))
            return {"id": finding_id, "kind": "candidate", **allowed}
        canonical = db.execute("SELECT id FROM canonical_findings WHERE id=?", (finding_id,)).fetchone()
        if canonical:
            allowed = {k: v.strip() for k, v in values.items() if k in {"title", "severity"}}
            if not allowed:
                raise HTTPException(422, "已验证记录只允许编辑标题和严重性")
            db.execute("UPDATE canonical_findings SET " + ",".join(f"{key}=?" for key in allowed) + ",updated_at=? WHERE id=?", (*allowed.values(), utcnow(), finding_id))
            return {"id": finding_id, "kind": "verified", **allowed}
    raise HTTPException(404, "Finding 不存在")


@router.delete("/findings/{finding_id}")
def archive_finding(finding_id: str):
    with connect() as db:
        candidate = db.execute("SELECT id FROM candidate_findings WHERE id=?", (finding_id,)).fetchone()
        if candidate:
            db.execute("UPDATE candidate_findings SET status='archived',updated_at=? WHERE id=?", (utcnow(), finding_id))
            db.execute("UPDATE canonical_findings SET status='archived',updated_at=? WHERE candidate_id=?", (utcnow(), finding_id))
            return {"id": finding_id, "status": "archived"}
        canonical = db.execute("SELECT candidate_id FROM canonical_findings WHERE id=?", (finding_id,)).fetchone()
        if canonical:
            db.execute("UPDATE canonical_findings SET status='archived',updated_at=? WHERE id=?", (utcnow(), finding_id))
            return {"id": finding_id, "status": "archived"}
    raise HTTPException(404, "Finding 不存在")


@router.post("/runs/{run_id}/findings/archive-candidates")
def archive_run_candidates(run_id: str):
    """Clear non-verified candidate output for one terminal run, preserving evidence."""
    run = get_run(run_id)
    if run["status"] in {"queued", "running", "paused"}:
        raise HTTPException(409, "运行中的任务仍可能产生候选，请先停止或等待任务结束")
    with connect() as db:
        changed = db.execute(
            """UPDATE candidate_findings SET status='archived',updated_at=?
               WHERE run_id=? AND status NOT IN ('archived','verified')""",
            (utcnow(), run_id),
        )
        verified = db.execute(
            "SELECT COUNT(*) FROM candidate_findings WHERE run_id=? AND status='verified'",
            (run_id,),
        ).fetchone()[0]
    return {
        "run_id": run_id,
        "archived": changed.rowcount,
        "verified_preserved": verified,
        "evidence_preserved": True,
    }


@router.get("/runs/{run_id}/summary-report")
def run_summary_report(run_id: str):
    run = get_run(run_id)
    engagement = get_engagement(run["engagement_id"])
    findings = list_findings(mode=run["mode"], run_id=run_id)
    details = get_run_details(run_id)
    coverage = details["coverage"]
    tests = details["test_items"]
    tested = sum(1 for item in coverage if item["state"] == "tested")
    completed_tests = sum(1 for item in tests if item["status"] == "completed")
    failed_tests = sum(1 for item in tests if item["status"] == "failed")
    not_tested = sum(1 for item in tests if item["status"] == "not_tested")
    verdict = "发现已验证漏洞" if findings["verified"] else "本次分析未形成已验证漏洞"
    content_lines = [
        f"# {engagement['name']} 分析总结", "", f"- 目标：{engagement['normalized_target']}",
        f"- 运行：{run_id}", "- 执行模式：真实扫描",
        f"- 状态：{run['status']}", f"- 开始时间：{run['started_at'] or run['created_at']}",
        f"- 完成时间：{run['completed_at'] or run['stopped_at'] or '未结束'}", f"- 结论：{verdict}", "",
        "## 结果统计", "", f"- 候选：{len(findings['candidates'])}", f"- 已验证漏洞：{len(findings['verified'])}",
        f"- 测试项：{len(tests)}（完成 {completed_tests} / 失败 {failed_tests} / 未测试 {not_tested}）",
        f"- 覆盖项：{len(coverage)}", f"- 已测试覆盖项：{tested}", "", "## 实际测试与反馈", "",
    ]
    if tests:
        for item in tests:
            content_lines.extend([
                f"### {item['label']}", "", f"- 工具/能力：{item['id']}", f"- 状态：{item['status']}",
                f"- 测试目的：{item['description']}", f"- 执行反馈：{item['result']}",
                f"- Observation：{item['observation_count']}", "",
            ])
    else:
        content_lines.extend(["本次运行没有形成可核验的测试项记录。", ""])
    content_lines.extend(["## Coverage Ledger", ""])
    if coverage:
        for item in coverage:
            content_lines.append(f"- [{item['state']}] {item['surface_key']}：{item['reason']}")
    else:
        content_lines.append("- 没有覆盖账本记录；不能据此推断目标安全。")
    content_lines.extend(["", "## 说明", "", "未形成已验证漏洞不等于目标绝对安全。本报告只陈述本次授权范围、实际测试反馈和覆盖账本内的事实。"])
    return {
        "run_id": run_id, "engagement_id": run["engagement_id"], "name": engagement["name"],
        "target": engagement["normalized_target"], "status": run["status"], "verdict": verdict,
        "counts": {"candidates": len(findings["candidates"]), "verified": len(findings["verified"]),
                   "coverage": len(coverage), "tested": tested, "tests": len(tests),
                   "completed_tests": completed_tests, "failed_tests": failed_tests, "not_tested": not_tested},
        "tests": tests, "coverage": coverage, "content": "\n".join(content_lines),
    }


@router.get("/findings/{finding_id}")
def get_finding(finding_id: str):
    finding, evidence, scope_id = finding_report_inputs(finding_id)
    return {**finding, "scope_snapshot_id": scope_id, "evidence": evidence}


@router.get("/findings/{finding_id}/proof-capsule")
def proof_capsule(finding_id: str):
    finding, evidence, scope_id = finding_report_inputs(finding_id)
    capsule = {
        "schema": "proof-capsule/1.0", "finding_id": finding_id,
        "scope_snapshot_id": scope_id, "verification": finding["verification"],
        "impact": finding["impact"], "eligibility": finding["eligibility"],
        "evidence": evidence, "portable": True,
    }
    capsule["sha256"] = hashlib.sha256(dump(capsule).encode()).hexdigest()
    return capsule


def finding_report_inputs(finding_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    with connect() as db:
        row = db.execute("SELECT * FROM canonical_findings WHERE id=? AND status='verified'", (finding_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Verified CanonicalFinding 不存在")
        finding = dict(row)
        for key in ("impact", "eligibility", "verification", "evidence_ids"):
            finding[key] = load(finding[key], {} if key != "evidence_ids" else [])
        evidence = [dict(e) for e in db.execute(
            f"SELECT * FROM evidence_v2 WHERE id IN ({','.join('?' for _ in finding['evidence_ids'])})" if finding["evidence_ids"] else "SELECT * FROM evidence_v2 WHERE 0",
            tuple(finding["evidence_ids"]),
        )]
        candidate = db.execute("SELECT run_id FROM candidate_findings WHERE id=?", (finding["candidate_id"],)).fetchone()
        run = db.execute("SELECT scope_snapshot_id FROM analysis_runs WHERE id=?", (candidate["run_id"],)).fetchone() if candidate else None
    return finding, evidence, run["scope_snapshot_id"] if run else "MISSING_SCOPE_SNAPSHOT"


@router.post("/findings/{finding_id}/reports/{platform}/preview")
def preview_report(finding_id: str, platform: str):
    finding, evidence, scope_id = finding_report_inputs(finding_id)
    model = universal_model(finding, evidence, scope_id)
    try:
        content, check, config = render(model, platform)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    preview_id = uid("preview")
    with connect() as db:
        db.execute("INSERT INTO report_previews VALUES(?,?,?,?,?,?,?)", (preview_id, finding_id, platform, "1.0.0", dump(check), content, utcnow()))
    return {"id": preview_id, "finding_id": finding_id, "platform": platform, "adapter": config["display_name"], "completeness": check, "content": content}


@router.post("/findings/{finding_id}/reports/{platform}/export", status_code=202)
def export_report(finding_id: str, platform: str):
    finding, evidence, scope_id = finding_report_inputs(finding_id)
    model = universal_model(finding, evidence, scope_id)
    try:
        content, check, _ = render(model, platform)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    package_id = uid("submission")
    path, manifest = export_bundle(package_id, model, platform, content, check)
    with connect() as db:
        db.execute("INSERT INTO submission_packages_v2 VALUES(?,?,?,?,?,?,?,?)", (
            package_id, finding_id, platform, "review_ready" if check["ready"] else "draft_incomplete",
            dump(manifest), path, utcnow(), utcnow(),
        ))
    return {"id": package_id, "status": "review_ready" if check["ready"] else "draft_incomplete", "download_url": f"/api/v1/submission-packages/{package_id}/download", "manifest": manifest}


@router.get("/submission-packages/{package_id}/download")
def download_package(package_id: str):
    with connect() as db:
        row = db.execute("SELECT export_path FROM submission_packages_v2 WHERE id=?", (package_id,)).fetchone()
    if not row or not row["export_path"] or not Path(row["export_path"]).is_file():
        raise HTTPException(404, "Submission Package 不存在")
    return FileResponse(row["export_path"], media_type="application/zip", filename=f"{package_id}.zip")


@router.get("/capabilities")
def capabilities(refresh: bool = False):
    from traditional_tools import shannon_configured, shannon_ready, strix_configured, strix_sandbox_ready
    try:
        items = capability_inventory(refresh=refresh)
    except TypeError:  # test or extension adapters may expose the legacy no-argument contract
        items = capability_inventory()
    for item in items:
        item["requires_docker"] = item["id"] in {"strix", "shannon"}
        item["execution_backend"] = "docker_optional" if item["requires_docker"] else "local_native"
        if item["id"] == "strix":
            item["configured"] = strix_configured()
        elif item["id"] == "shannon":
            item["configured"] = shannon_configured()
        else:
            item["configured"] = item["available"]
        if item["id"] == "strix":
            item["sandbox_ready"] = strix_sandbox_ready()
            item["ready"] = item["available"] and item["configured"] and item["sandbox_ready"]
        elif item["id"] == "shannon":
            item["sandbox_ready"] = shannon_ready() if item["configured"] else bool(shutil.which("docker"))
            item["ready"] = item["available"] and shannon_ready()
        else:
            item["ready"] = item["available"] and item["configured"]
    return items


@router.get("/runtime/readiness")
def runtime_readiness():
    """Summarize the default Docker-free production runtime."""
    items = capabilities()
    by_id = {item["id"]: item for item in items}
    traditional_core = ["httpx", "katana", "nuclei", "semgrep", "gitleaks", "trivy"]
    web3_core = ["forge", "anvil", "cast", "slither", "aderyn", "echidna", "medusa", "halmos"]
    required = list(dict.fromkeys([*traditional_core, *web3_core]))
    available = [name for name in required if by_id.get(name, {}).get("available")]
    from native_agent import readiness as native_agent_readiness
    agent = native_agent_readiness()
    return {
        "backend": "local_native",
        "docker_required": False,
        "ready": len(available) == len(required),
        "required_core": len(required),
        "available_core": len(available),
        "missing_core": [name for name in required if name not in available],
        "traditional": {name: bool(by_id.get(name, {}).get("available")) for name in traditional_core},
        "web3": {name: bool(by_id.get(name, {}).get("available")) for name in web3_core},
        "optional_docker_agents": ["strix", "shannon"],
        "native_agent": agent,
    }


@router.get("/runtime/status")
def runtime_status():
    from native_agent import readiness as native_agent_readiness
    from traditional_tools import strix_provider_settings, strix_configured

    network_started = time.perf_counter()
    try:
        probe = urllib.request.Request("https://www.apple.com/library/test/success.html", method="HEAD")
        with urllib.request.urlopen(probe, timeout=4) as response:
            connected = 200 <= response.status < 500
        network = {"connected": connected, "latency_ms": round((time.perf_counter() - network_started) * 1000)}
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        network = {"connected": False, "latency_ms": None, "detail": redact(str(error))}

    values = strix_provider_settings()
    base = values.get("LLM_API_BASE") or values.get("OPENAI_API_BASE") or ""
    model = values.get("STRIX_LLM", "").removeprefix("openai/")
    provider = {"configured": strix_configured(), "connected": False, "model": model, "endpoint": ""}
    if base:
        parsed = urlparse(base)
        provider["endpoint"] = parsed.hostname or ""
    key = values.get("LLM_API_KEY") or values.get("OPENAI_API_KEY")
    if provider["configured"] and base and key:
        request = urllib.request.Request(base.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                provider.update({"connected": 200 <= response.status < 300, "http_status": response.status})
        except urllib.error.HTTPError as error:
            provider.update({"connected": False, "http_status": error.code})
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            provider["detail"] = redact(str(error))

    docker_path = shutil.which("docker")
    docker_running = False
    if docker_path:
        try:
            docker_running = subprocess.run([docker_path, "info"], capture_output=True, timeout=3).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    usage = shutil.disk_usage(ROOT)
    return {
        "service": {"connected": True, "endpoint": "127.0.0.1:8000", "backend": "local_native"},
        "network": network,
        "provider": provider,
        "native_agent": native_agent_readiness(),
        "docker": {"installed": bool(docker_path), "running": docker_running, "required": False},
        "storage": {"free_bytes": usage.free, "total_bytes": usage.total, "free_percent": round(usage.free / usage.total * 100, 1)},
    }


def onboarding_checks(run_fixture_tests: bool = False) -> dict[str, Any]:
    readiness = runtime_readiness()
    status = runtime_status()
    version = system_version()
    chrome = status["native_agent"].get("available", False)
    traditional_fixture = ROOT / "fixtures" / "traditional-vulnerable"
    web3_fixture = ROOT / "fixtures" / "web3-vault"
    fixture = {
        "traditional": {"available": traditional_fixture.is_dir() and (traditional_fixture / ".semgrep.yml").is_file(), "tested": False, "detail": "夹具与规则文件已找到"},
        "web3": {"available": web3_fixture.is_dir() and (web3_fixture / "foundry.toml").is_file(), "tested": False, "detail": "Foundry 测试夹具已找到"},
    }
    if run_fixture_tests:
        import capability_registry
        from web3_analysis import run_forge_build
        if fixture["traditional"]["available"]:
            result = capability_registry.execute(
                "semgrep", ["scan", "--json", "--config", str(traditional_fixture / ".semgrep.yml"), str(traditional_fixture)], ROOT, timeout=60,
            )
            fixture["traditional"].update({"tested": result.status == "completed", "detail": f"Semgrep 本地夹具 exit={result.exit_code}"})
        if fixture["web3"]["available"]:
            result = run_forge_build(web3_fixture)
            fixture["web3"].update({"tested": result.get("status") == "compiled", "detail": f"Forge 本地夹具 {result.get('status')}"})
    checks = [
        {"id": "tools", "label": "工具版本", "required": True, "ok": readiness["ready"], "detail": f"{readiness['available_core']}/{readiness['required_core']} 原生核心工具"},
        {"id": "model", "label": "模型连接", "required": True, "ok": bool(status["provider"]["connected"]), "detail": "Provider 已连接" if status["provider"]["connected"] else "请在设置页配置并验证模型 API"},
        {"id": "chrome", "label": "系统 Chrome", "required": True, "ok": bool(chrome), "detail": status["native_agent"].get("browser", "missing")},
        {"id": "storage", "label": "磁盘空间", "required": True, "ok": status["storage"]["free_bytes"] >= 10 * 1024**3, "detail": f"剩余 {status['storage']['free_bytes'] / 1024**3:.1f} GB（{status['storage']['free_percent']}%）"},
        {"id": "traditional", "label": "Traditional 能力", "required": True, "ok": all(readiness["traditional"].values()), "detail": "6 项本机能力"},
        {"id": "web3", "label": "Web3 能力", "required": True, "ok": all(readiness["web3"].values()), "detail": "8 项本机能力"},
        {"id": "database", "label": "本地数据库", "required": True, "ok": version["database_integrity"], "detail": f"Schema {version['schema_version']} · {version['backups']} 个可恢复备份"},
        {"id": "fixture_traditional", "label": "Traditional 测试项目", "required": True, "ok": fixture["traditional"]["tested"] if run_fixture_tests else fixture["traditional"]["available"], "detail": fixture["traditional"]["detail"]},
        {"id": "fixture_web3", "label": "Web3 测试项目", "required": True, "ok": fixture["web3"]["tested"] if run_fixture_tests else fixture["web3"]["available"], "detail": fixture["web3"]["detail"]},
    ]
    blockers = [item for item in checks if item["required"] and not item["ok"]]
    return {"ready": not blockers, "checks": checks, "blockers": blockers, "self_tested": run_fixture_tests, "version": version}


@router.get("/onboarding/status")
def onboarding_status():
    return onboarding_checks(False)


@router.post("/onboarding/self-test")
def onboarding_self_test():
    return onboarding_checks(True)


def _directory_usage(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    files = [item for item in path.rglob("*") if item.is_file()]
    return len(files), sum(item.stat().st_size for item in files if item.exists())


@router.post("/maintenance/cache/clear")
def clear_local_cache(body: MaintenanceConfirmInput):
    if body.confirmation != "CLEAR_CACHE":
        raise HTTPException(422, "确认文本不匹配")
    with connect() as db:
        active = db.execute("SELECT COUNT(*) FROM analysis_runs WHERE status IN ('queued','running','paused')").fetchone()[0]
    if active:
        raise HTTPException(409, "存在运行中或暂停的任务，请先停止后再清理缓存")
    removed_files = removed_bytes = 0
    for path in (LOCAL_DATA_ROOT / "agent_workspaces", LOCAL_DATA_ROOT / "repositories"):
        files, size = _directory_usage(path)
        if path.exists():
            shutil.rmtree(path)
        removed_files += files
        removed_bytes += size
    return {"status": "cleared", "removed_files": removed_files, "removed_bytes": removed_bytes}


@router.delete("/maintenance/recent-records")
def clear_recent_records(body: MaintenanceConfirmInput):
    if body.confirmation != "CLEAR_RECENT_RECORDS":
        raise HTTPException(422, "确认文本不匹配")
    with connect() as db:
        active = db.execute("SELECT COUNT(*) FROM analysis_runs WHERE status IN ('queued','running','paused')").fetchone()[0]
    if active:
        raise HTTPException(409, "存在运行中或暂停的任务，请先停止后再清空记录")
    backup_root = LOCAL_DATA_ROOT / "backups"
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup_path = backup_root / f"before-clear-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3"
    source = connect()
    backup = sqlite3.connect(backup_path)
    try:
        source.backup(backup)
    finally:
        backup.close()
        source.close()
    backup_path.chmod(0o600)
    tables = (
        "http_exchanges", "request_slots_v2", "run_configs_v2", "run_budgets_v2", "web3_forks", "invariant_registry",
        "graveyard", "coverage_v2", "identity_profiles", "identities", "program_snapshots", "submission_packages_v2",
        "report_previews", "canonical_findings", "verification_attempts", "candidate_findings",
        "relationships", "entities", "evidence_v2", "artifacts", "observations", "checkpoints",
        "run_events_v2", "analysis_runs", "execution_policies", "scope_snapshots", "engagements_v2", "target_specs",
        "budgets", "dns_pins", "counterevidence_ledger", "evidence_ledger", "vulnerability_memory",
        "hypotheses", "surfaces", "submissions", "executions", "audit_log", "coverage", "findings",
        "events", "runs", "engagements",
    )
    with connect() as db:
        db.execute("PRAGMA foreign_keys=OFF")
        for table in tables:
            db.execute(f'DELETE FROM "{table}"')
        db.execute("DELETE FROM sqlite_sequence")
    removed_files = removed_bytes = 0
    for path in (
        LOCAL_DATA_ROOT / "agent_workspaces", LOCAL_DATA_ROOT / "repositories",
        LOCAL_DATA_ROOT / "artifacts", LOCAL_DATA_ROOT / "exports",
    ):
        files, size = _directory_usage(path)
        if path.exists():
            shutil.rmtree(path)
        removed_files += files
        removed_bytes += size
    return {
        "status": "cleared", "backup": str(backup_path),
        "removed_files": removed_files, "removed_bytes": removed_bytes,
    }


@router.post("/router/plan")
def route_capabilities(body: RoutingPlanInput):
    available = {item["id"]: item for item in capability_inventory()}
    maps = {
        ("traditional", "inventory"): ["httpx", "katana", "subfinder", "nuclei"],
        ("traditional", "http"): ["shannon", "strix", "httpx"],
        ("traditional", "code"): ["semgrep", "gitleaks", "trivy"],
        ("traditional", "verification"): ["pentest-ai"],
        ("web3", "static"): ["slither", "aderyn", "forge"],
        ("web3", "fuzz"): ["forge", "echidna", "medusa", "halmos"],
        ("web3", "verification"): ["anvil", "cast", "forge"],
    }
    requested = maps.get((body.mode, body.task), [])
    selected = [name for name in requested if available.get(name, {}).get("available")]
    degraded = [name for name in requested if name not in selected]
    model_route = "none" if body.model_budget_usd <= 0 else "configured-provider-required"
    return {
        "mode": body.mode, "task": body.task, "selected_capabilities": selected,
        "degraded_capabilities": degraded, "model_route": model_route,
        "estimated_tool_cost_usd": 0, "budget_ceiling_usd": body.model_budget_usd,
        "policy": "local_deterministic_first",
    }


@router.post("/engagements/{engagement_id}/identities", status_code=201)
def create_identity(engagement_id: str, body: IdentityInput):
    get_engagement(engagement_id)
    identity_id = uid("identity")
    # credential_ref is an opaque local reference; raw credentials are never accepted here.
    if body.credential_ref and any(x in body.credential_ref.lower() for x in ("bearer ", "password=", "private_key=", "cookie:", "token=")):
        raise HTTPException(422, "credential_ref 必须是脱敏引用，不能包含凭据原文")
    with connect() as db:
        db.execute("INSERT INTO identities VALUES(?,?,?,?,?,?,?)", (identity_id, engagement_id, body.label, body.role, body.tenant, body.credential_ref, utcnow()))
        db.execute("INSERT INTO identity_profiles VALUES(?,?,?,?,?,?,?)", (identity_id, body.auth_type, body.session_status, body.expires_at, None, body.notes, utcnow()))
    return get_identity(identity_id)


def get_identity(identity_id: str) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("""SELECT i.*,p.auth_type,p.session_status,p.expires_at,p.last_validated_at,p.notes,p.updated_at
          FROM identities i JOIN identity_profiles p ON p.identity_id=i.id WHERE i.id=?""", (identity_id,)).fetchone()
    if not row:
        raise HTTPException(404, "测试身份不存在")
    value = dict(row)
    value["credential_configured"] = bool(value.pop("credential_ref", None))
    return value


@router.get("/engagements/{engagement_id}/identities")
def list_identities(engagement_id: str):
    get_engagement(engagement_id)
    with connect() as db:
        ids = [row["id"] for row in db.execute("SELECT id FROM identities WHERE engagement_id=? ORDER BY created_at", (engagement_id,))]
    return [get_identity(identity_id) for identity_id in ids]


@router.patch("/identities/{identity_id}")
def update_identity(identity_id: str, body: IdentityUpdateInput):
    current = get_identity(identity_id)
    values = body.model_dump(exclude_unset=True)
    if values.get("credential_ref") and any(x in values["credential_ref"].lower() for x in ("bearer ", "password=", "private_key=", "cookie:", "token=")):
        raise HTTPException(422, "credential_ref 必须是脱敏引用，不能包含凭据原文")
    base = {key: values[key] for key in ("label", "role", "tenant", "credential_ref") if key in values}
    profile = {key: values[key] for key in ("auth_type", "session_status", "expires_at", "notes") if key in values}
    with connect() as db:
        if base:
            db.execute(f"UPDATE identities SET {','.join(f'{key}=?' for key in base)} WHERE id=?", (*base.values(), identity_id))
        if profile:
            profile["updated_at"] = utcnow()
            if profile.get("session_status") == "ready":
                profile["last_validated_at"] = utcnow()
            db.execute(f"UPDATE identity_profiles SET {','.join(f'{key}=?' for key in profile)} WHERE identity_id=?", (*profile.values(), identity_id))
    return get_identity(identity_id)


@router.delete("/identities/{identity_id}")
def delete_identity(identity_id: str):
    get_identity(identity_id)
    with connect() as db:
        db.execute("DELETE FROM identity_profiles WHERE identity_id=?", (identity_id,))
        db.execute("DELETE FROM identities WHERE id=?", (identity_id,))
    return {"id": identity_id, "status": "deleted"}


@router.get("/engagements/{engagement_id}/role-matrix")
def role_matrix(engagement_id: str):
    identities = list_identities(engagement_id)
    pairs = []
    for left in identities:
        for right in identities:
            if left["id"] >= right["id"]:
                continue
            pairs.append({
                "left_id": left["id"], "right_id": right["id"],
                "cross_role": left["role"] != right["role"],
                "cross_tenant": bool(left.get("tenant") and right.get("tenant") and left["tenant"] != right["tenant"]),
                "ready": left["session_status"] == right["session_status"] == "ready",
            })
    return {"engagement_id": engagement_id, "identities": identities, "pairs": pairs, "ready_pairs": sum(1 for pair in pairs if pair["ready"])}


@router.post("/program-snapshots", status_code=201)
def create_program_snapshot(body: ProgramSnapshotInput):
    engagement = get_engagement(body.engagement_id)
    if engagement["mode"] != "web3":
        raise HTTPException(409, "ProgramSnapshot 仅用于 Web3 Engagement")
    with connect() as db:
        version = db.execute("SELECT COALESCE(MAX(version),0)+1 AS v FROM program_snapshots WHERE engagement_id=?", (body.engagement_id,)).fetchone()["v"]
        snapshot_id = uid("program")
        db.execute("INSERT INTO program_snapshots VALUES(?,?,?,?,?,?,?)", (snapshot_id, body.engagement_id, version, body.platform, dump(body.rules), body.source_uri, utcnow()))
    return {"id": snapshot_id, "version": version, **body.model_dump()}


@router.get("/program-snapshots/{snapshot_id}")
def get_program_snapshot(snapshot_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM program_snapshots WHERE id=?", (snapshot_id,)).fetchone()
    if not row:
        raise HTTPException(404, "ProgramSnapshot 不存在")
    value = dict(row)
    value["rules"] = load(value["rules"], {})
    return value


@router.post("/web3/execution/check")
def web3_execution_check(body: Web3ExecutionCheck):
    engagement = get_engagement(body.engagement_id)
    if engagement["mode"] != "web3":
        raise HTTPException(409, "该 Engagement 不是 Web3 模式")
    allowed = True
    reason = "read_only_allowed"
    if body.uses_real_private_key:
        allowed, reason = False, "real_private_key_forbidden"
    elif body.action in {"write", "broadcast", "sign"} and body.network_class in {"production", "public_testnet"}:
        allowed, reason = False, f"{body.network_class}_write_forbidden"
    elif body.action in {"write", "broadcast", "sign"} and body.network_class in {"local_fork", "local_devnet"}:
        allowed, reason = True, "local_write_allowed_by_environment"
    return {"allowed": allowed, "reason": reason, "requires_real_private_key": False, **body.model_dump()}


@router.post("/policy/check")
def execution_policy_check(body: PolicyCheckInput):
    engagement = get_engagement(body.engagement_id)
    normalized = resolve_target_value(ResolveTargetInput(target=body.target, mode=engagement["mode"], chain_id=engagement.get("chain_id")))["normalized_target"]
    allowed_targets = engagement["scope"].get("allowed_targets", [])
    target_allowed = normalized in allowed_targets or any(
        normalized.startswith(item.rstrip("/") + "/") for item in allowed_targets if isinstance(item, str)
    )
    if not target_allowed and engagement["mode"] == "traditional" and engagement["scope"].get("allow_subdomains"):
        candidate = urlparse(normalized)
        for item in allowed_targets:
            allowed = urlparse(item) if isinstance(item, str) else None
            if not allowed or not allowed.hostname or not candidate.hostname:
                continue
            same_origin_class = candidate.scheme == allowed.scheme and candidate.port == allowed.port
            if same_origin_class and candidate.hostname.endswith("." + allowed.hostname):
                target_allowed = True
                break
    if not target_allowed:
        return {"allowed": False, "reason": "out_of_scope", "target": normalized}
    if body.destructive and not engagement["policy"].get("allow_state_change", False):
        return {"allowed": False, "reason": "destructive_action_not_approved", "target": normalized}
    if body.third_party_active and not engagement["scope"].get("allow_third_party_active", False):
        return {"allowed": False, "reason": "third_party_active_test_denied", "target": normalized}
    if body.third_party_passive and not engagement["scope"].get("allow_third_party_passive", False):
        return {"allowed": False, "reason": "third_party_passive_query_denied", "target": normalized}
    if body.action not in engagement["scope"].get("allowed_actions", []):
        return {"allowed": False, "reason": "action_not_allowed", "target": normalized}
    return {"allowed": True, "reason": "scope_and_policy_pass", "target": normalized}


@router.get("/runs/{run_id}/budget")
def run_budget(run_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM run_budgets_v2 WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Run budget 不存在")
    return dict(row)


@router.post("/runs/{run_id}/requests/authorize")
def authorize_request(run_id: str, body: RequestAuthorizationInput):
    import time
    with connect() as db:
        run = db.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        raise HTTPException(404, "Run 不存在")
    policy_result = execution_policy_check(PolicyCheckInput(
        engagement_id=run["engagement_id"], target=body.target, action=body.action,
    ))
    if not policy_result["allowed"]:
        return policy_result
    engagement = get_engagement(run["engagement_id"])
    rate = max(.001, float(engagement["policy"].get("max_requests_per_second", 1)))
    interval = 1.0 / rate
    moment = time.monotonic()
    with connect() as db:
        row = db.execute("SELECT last_allowed_at FROM request_slots_v2 WHERE run_id=?", (run_id,)).fetchone()
        if row and moment - row["last_allowed_at"] < interval:
            return {"allowed": False, "reason": "rate_limit", "retry_after_seconds": round(interval - (moment - row["last_allowed_at"]), 3)}
        consumed, reason = consume_run_budget(run_id, "request", 1)
        if not consumed:
            return {"allowed": False, "reason": reason}
        db.execute("INSERT INTO request_slots_v2 VALUES(?,?) ON CONFLICT(run_id) DO UPDATE SET last_allowed_at=excluded.last_allowed_at", (run_id, moment))
    return {"allowed": True, "reason": "request_slot_granted", "target": policy_result["target"]}


@router.get("/runs/{run_id}/coverage")
def run_coverage(run_id: str):
    with connect() as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM coverage_v2 WHERE run_id=? ORDER BY updated_at DESC", (run_id,))]
        grave = [dict(row) for row in db.execute("""SELECT g.* FROM graveyard g JOIN analysis_runs r ON r.engagement_id=g.engagement_id WHERE r.id=?""", (run_id,))]
    for row in rows:
        row["observation_ids"] = load(row["observation_ids"], [])
    for row in grave:
        row["counterevidence_ids"] = load(row["counterevidence_ids"], [])
    return {"coverage": rows, "graveyard": grave}


@router.post("/candidates/{candidate_id}/graveyard")
def graveyard_candidate(candidate_id: str, reason: str, resurrect_when: str | None = None):
    with connect() as db:
        candidate = db.execute("SELECT * FROM candidate_findings WHERE id=?", (candidate_id,)).fetchone()
        if not candidate:
            raise HTTPException(404, "Candidate 不存在")
        counter_ids = [r["id"] for r in db.execute("SELECT id FROM evidence_v2 WHERE run_id=? AND polarity='counter'", (candidate["run_id"],))]
        grave_id = uid("grave")
        db.execute("INSERT INTO graveyard VALUES(?,?,?,?,?,?,?)", (
            grave_id, candidate["engagement_id"], f"{candidate['category']}:{candidate['target']}", reason,
            dump(counter_ids), resurrect_when, utcnow(),
        ))
        db.execute("UPDATE candidate_findings SET status='graveyard',updated_at=? WHERE id=?", (utcnow(), candidate_id))
    return {"id": grave_id, "candidate_id": candidate_id, "status": "graveyard", "resurrect_when": resurrect_when}


@router.post("/graveyard/{grave_id}/resurrect")
def resurrect_candidate(grave_id: str):
    with connect() as db:
        grave = db.execute("SELECT * FROM graveyard WHERE id=?", (grave_id,)).fetchone()
        if not grave:
            raise HTTPException(404, "Graveyard entry 不存在")
        category, target = grave["hypothesis_key"].split(":", 1)
        row = db.execute("SELECT id FROM candidate_findings WHERE engagement_id=? AND category=? AND target=? ORDER BY updated_at DESC LIMIT 1", (grave["engagement_id"], category, target)).fetchone()
        if not row:
            raise HTTPException(409, "原 Candidate 不存在")
        db.execute("UPDATE candidate_findings SET status='candidate',updated_at=? WHERE id=?", (utcnow(), row["id"]))
        db.execute("DELETE FROM graveyard WHERE id=?", (grave_id,))
    return {"candidate_id": row["id"], "status": "candidate", "resurrected": True}
