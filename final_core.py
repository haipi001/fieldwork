from __future__ import annotations

import asyncio
import base64
import binascii
import concurrent.futures
import hashlib
import io
import ipaddress
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import yaml
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlparse, urlunparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, StreamingResponse
from reporting import export_bundle, redact, redact_structure, render, universal_model
from capability_registry import inventory as capability_inventory
from lifecycle import database_integrity, list_backups
from version import APP_VERSION, BUILD_NUMBER, SCHEMA_VERSION


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "src_control.db"
LOCAL_DATA_ROOT = ROOT / "data"
router = APIRouter(prefix="/api/v1", tags=["FINAL v1"])
CAMPAIGN_SCHEDULER_STATE: dict[str, Any] = {
    "running": False, "last_tick_at": None, "last_processed": 0, "last_error": None,
}


def mark_campaign_scheduler(**values: Any) -> None:
    CAMPAIGN_SCHEDULER_STATE.update(values)


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


def update_verification_job(job_id: str, *, status: str | None = None, phase: str | None = None,
                            completed_requests: int | None = None, result: dict | None = None,
                            error: str | None = None, completed: bool = False) -> None:
    assignments, values = [], []
    for column, value in (("status", status), ("phase", phase),
                          ("completed_requests", completed_requests),
                          ("result", dump(result) if result is not None else None),
                          ("error", redact(error) if error is not None else None)):
        if value is not None:
            assignments.append(f"{column}=?")
            values.append(value)
    if completed:
        assignments.append("completed_at=?")
        values.append(utcnow())
    if assignments:
        values.append(job_id)
        with connect() as db:
            db.execute(f"UPDATE verification_jobs SET {','.join(assignments)} WHERE id=?", values)


def verification_job_cancel_requested(job_id: str) -> bool:
    with connect() as db:
        row = db.execute("SELECT cancel_requested FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
    return bool(row and row["cancel_requested"])


def hydrate_verification_job(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["cancel_requested"] = bool(value["cancel_requested"])
    value["result"] = load(value["result"], None)
    value["progress"] = round(value["completed_requests"] / value["total_requests"], 3) if value["total_requests"] else 0
    return redact_structure(value)


@router.get("/verification-jobs/{job_id}")
def get_verification_job(job_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "复验任务不存在")
    return hydrate_verification_job(row)


@router.post("/verification-jobs/{job_id}/cancel")
def cancel_verification_job(job_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "复验任务不存在")
        if row["status"] not in {"queued", "running", "cancelling"}:
            raise HTTPException(409, "复验任务已经结束")
        db.execute("UPDATE verification_jobs SET cancel_requested=1,status='cancelling',phase='等待当前步骤安全停止' WHERE id=?", (job_id,))
    return get_verification_job(job_id)


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
            CREATE TABLE IF NOT EXISTS verification_jobs (
              id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, run_id TEXT NOT NULL,
              oracle TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL,
              completed_requests INTEGER NOT NULL DEFAULT 0,
              total_requests INTEGER NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
              result TEXT, error TEXT, created_at TEXT NOT NULL,
              started_at TEXT, completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS verification_jobs_candidate_idx
              ON verification_jobs(candidate_id,created_at);
            CREATE TABLE IF NOT EXISTS canonical_findings (
              id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL UNIQUE,
              engagement_id TEXT NOT NULL, mode TEXT NOT NULL, title TEXT NOT NULL,
              category TEXT NOT NULL, severity TEXT NOT NULL, target TEXT NOT NULL,
              impact TEXT NOT NULL, eligibility TEXT NOT NULL, verification TEXT NOT NULL,
              evidence_ids TEXT NOT NULL, status TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS finding_lifecycle (
              finding_id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL,
              fingerprint TEXT NOT NULL, status TEXT NOT NULL,
              first_seen_run_id TEXT NOT NULL, last_seen_run_id TEXT NOT NULL,
              occurrence_count INTEGER NOT NULL DEFAULT 1,
              remediation TEXT NOT NULL, history TEXT NOT NULL, updated_at TEXT NOT NULL,
              UNIQUE(engagement_id,fingerprint)
            );
            CREATE TABLE IF NOT EXISTS finding_occurrences (
              id TEXT PRIMARY KEY, finding_id TEXT NOT NULL, candidate_id TEXT NOT NULL UNIQUE,
              run_id TEXT NOT NULL, occurrence_kind TEXT NOT NULL, receipt_id TEXT,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS finding_occurrences_run_idx ON finding_occurrences(run_id,created_at);
            CREATE TABLE IF NOT EXISTS finding_retests (
              id TEXT PRIMARY KEY, finding_id TEXT NOT NULL, run_id TEXT NOT NULL,
              status TEXT NOT NULL, note TEXT NOT NULL, result TEXT,
              created_at TEXT NOT NULL, completed_at TEXT
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
            CREATE TABLE IF NOT EXISTS program_rule_authorizations (
              snapshot_id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL,
              status TEXT NOT NULL, previous_snapshot_id TEXT,
              diff TEXT NOT NULL, confirmed_at TEXT, note TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
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
            CREATE TABLE IF NOT EXISTS research_campaigns (
              id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, name TEXT NOT NULL,
              objective TEXT NOT NULL, status TEXT NOT NULL, strategy TEXT NOT NULL,
              max_iterations INTEGER NOT NULL, iterations_completed INTEGER NOT NULL DEFAULT 0,
              horizon_days INTEGER NOT NULL, coverage_target REAL NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS business_workflows (
              id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, engagement_id TEXT NOT NULL,
              name TEXT NOT NULL, objective TEXT NOT NULL, preconditions TEXT NOT NULL,
              steps TEXT NOT NULL, invariants TEXT NOT NULL, risk_class TEXT NOT NULL,
              status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              executable_invariants TEXT NOT NULL DEFAULT '[]',
              depends_on_workflow_ids TEXT NOT NULL DEFAULT '[]',
              import_variables TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS campaign_candidate_links (
              id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, iteration_id TEXT NOT NULL,
              workflow_id TEXT NOT NULL, hypothesis_id TEXT NOT NULL,
              candidate_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
              UNIQUE(iteration_id,hypothesis_id)
            );
            CREATE TABLE IF NOT EXISTS research_hypotheses (
              id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, workflow_id TEXT,
              fingerprint TEXT NOT NULL, category TEXT NOT NULL, statement TEXT NOT NULL,
              status TEXT NOT NULL, priority INTEGER NOT NULL, evidence_ids TEXT NOT NULL,
              counterevidence_ids TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              last_tested_at TEXT, next_action TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              UNIQUE(campaign_id,fingerprint)
            );
            CREATE TABLE IF NOT EXISTS campaign_iterations (
              id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, run_id TEXT,
              sequence INTEGER NOT NULL, status TEXT NOT NULL, plan TEXT NOT NULL,
              results TEXT NOT NULL, started_at TEXT, completed_at TEXT,
              created_at TEXT NOT NULL, UNIQUE(campaign_id,sequence)
            );
            CREATE TABLE IF NOT EXISTS campaign_schedules (
              campaign_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
              cadence_minutes INTEGER NOT NULL, execution_mode TEXT NOT NULL,
              preferred_run_id TEXT, max_tests INTEGER NOT NULL,
              next_run_at TEXT NOT NULL, last_planned_at TEXT, paused_at TEXT,
              lease_token TEXT, lease_expires_at TEXT, failure_count INTEGER NOT NULL DEFAULT 0,
              last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS campaign_iteration_metrics (
              iteration_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, sequence INTEGER NOT NULL,
              planned_tests INTEGER NOT NULL, tested_tests INTEGER NOT NULL, blocked_tests INTEGER NOT NULL,
              test_signature_count INTEGER NOT NULL, new_test_signature_count INTEGER NOT NULL,
              cumulative_test_signature_count INTEGER NOT NULL, signature_hashes TEXT NOT NULL,
              hypotheses_before INTEGER NOT NULL, hypotheses_after INTEGER NOT NULL,
              new_hypothesis_count INTEGER NOT NULL, retested_hypothesis_id TEXT,
              open_hypotheses_before INTEGER NOT NULL, open_hypotheses_after INTEGER NOT NULL,
              resolved_hypothesis_count INTEGER NOT NULL,
              invariant_passes INTEGER NOT NULL, invariant_failures INTEGER NOT NULL,
              execution_rate REAL NOT NULL, novelty_rate REAL NOT NULL,
              observation_count INTEGER NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS state_change_journal (
              id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, campaign_id TEXT NOT NULL,
              iteration_id TEXT NOT NULL, run_id TEXT NOT NULL, workflow_id TEXT NOT NULL,
              step_number INTEGER NOT NULL, identity_id TEXT NOT NULL, state TEXT NOT NULL,
              snapshot_exchange_id TEXT, mutation_exchange_id TEXT, after_exchange_id TEXT,
              compensation_exchange_id TEXT, rollback_exchange_id TEXT, error TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              rollback_probe_baselines TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS oast_probes (
              id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, run_id TEXT NOT NULL,
              campaign_id TEXT NOT NULL, hypothesis_id TEXT,
              token_sha256 TEXT NOT NULL UNIQUE, callback_base TEXT NOT NULL,
              status TEXT NOT NULL, expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oast_events (
              id TEXT PRIMARY KEY, probe_id TEXT NOT NULL, transport TEXT NOT NULL,
              method TEXT NOT NULL, path TEXT NOT NULL, query_preview TEXT NOT NULL,
              headers TEXT NOT NULL, body_preview TEXT NOT NULL,
              body_sha256 TEXT NOT NULL, source_address_sha256 TEXT,
              event_sha256 TEXT NOT NULL, received_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS campaign_engagement_idx ON research_campaigns(engagement_id,updated_at);
            CREATE INDEX IF NOT EXISTS workflow_campaign_idx ON business_workflows(campaign_id,updated_at);
            CREATE INDEX IF NOT EXISTS hypothesis_campaign_idx ON research_hypotheses(campaign_id,priority,updated_at);
            CREATE INDEX IF NOT EXISTS oast_campaign_idx ON oast_probes(campaign_id,created_at);
            CREATE INDEX IF NOT EXISTS oast_probe_event_idx ON oast_events(probe_id,received_at);
            CREATE INDEX IF NOT EXISTS state_change_engagement_idx ON state_change_journal(engagement_id,state,updated_at);
            CREATE INDEX IF NOT EXISTS campaign_schedule_due_idx ON campaign_schedules(enabled,next_run_at,lease_expires_at);
            CREATE INDEX IF NOT EXISTS campaign_metrics_sequence_idx ON campaign_iteration_metrics(campaign_id,sequence);
            """
        )
        # Request credentials are intentionally kept only in the worker process.
        # A restart therefore closes unfinished jobs instead of pretending they can resume.
        db.execute("""UPDATE verification_jobs
            SET status='interrupted',phase='进程重启，需重新配置身份后执行',completed_at=?
            WHERE status IN ('queued','running','cancelling')""", (utcnow(),))
        workflow_columns = {row["name"] for row in db.execute("PRAGMA table_info(business_workflows)")}
        if "executable_invariants" not in workflow_columns:
            db.execute("ALTER TABLE business_workflows ADD COLUMN executable_invariants TEXT NOT NULL DEFAULT '[]'")
        if "depends_on_workflow_ids" not in workflow_columns:
            db.execute("ALTER TABLE business_workflows ADD COLUMN depends_on_workflow_ids TEXT NOT NULL DEFAULT '[]'")
        if "import_variables" not in workflow_columns:
            db.execute("ALTER TABLE business_workflows ADD COLUMN import_variables TEXT NOT NULL DEFAULT '{}'")
        journal_columns = {row["name"] for row in db.execute("PRAGMA table_info(state_change_journal)")}
        if "rollback_probe_baselines" not in journal_columns:
            db.execute("ALTER TABLE state_change_journal ADD COLUMN rollback_probe_baselines TEXT NOT NULL DEFAULT '[]'")
        # Existing reviewed rule snapshots predate explicit change authorization.
        # Preserve their prior validity while requiring every new changed version
        # to pass through the authorization workflow below.
        for snapshot in db.execute("SELECT * FROM program_snapshots ORDER BY engagement_id,version").fetchall():
            rules = load(snapshot["rules"], {})
            if rules.get("kind") != "program_rules":
                continue
            db.execute("""INSERT OR IGNORE INTO program_rule_authorizations
                VALUES(?,?,?,?,?,?,?,?,?)""", (
                snapshot["id"], snapshot["engagement_id"], "confirmed", None,
                dump({"has_changes": False, "legacy_backfill": True}),
                snapshot["created_at"], "迁移：保留原有已审阅状态",
                snapshot["created_at"], snapshot["created_at"],
            ))
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


class TargetPackageInput(BaseModel):
    kind: Literal["auto", "openapi", "postman", "har", "source_zip", "cidr"] = "auto"
    content: str = Field(min_length=2, max_length=30_000_000)
    encoding: Literal["text", "base64"] = "text"
    mode: Literal["traditional", "web3"] = "traditional"
    filename: str | None = Field(default=None, max_length=255)


class TargetPackageApplyInput(TargetPackageInput):
    name: str = Field(min_length=2, max_length=160)
    root_target: str | None = Field(default=None, max_length=2048)


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


class FindingReportFieldsInput(BaseModel):
    summary: str = Field(min_length=10, max_length=4000)
    prerequisites: list[str] = Field(min_length=1, max_length=20)
    impact_description: str = Field(min_length=10, max_length=4000)
    affected_users: str = Field(min_length=3, max_length=2000)
    impact_conditions: str = Field(min_length=3, max_length=2000)
    impact_evidence_ids: list[str] = Field(min_length=1, max_length=100)
    counterevidence_summary: str = Field(min_length=3, max_length=4000)
    counterevidence_ids: list[str] = Field(min_length=1, max_length=100)
    remediation: str = Field(min_length=3, max_length=4000)
    platform_custom: dict[str, str] = Field(default_factory=dict, max_length=30)


class CandidateTriageInput(BaseModel):
    disposition: Literal["security_hypothesis", "needs_evidence", "not_security", "duplicate"]
    reason: str = Field(min_length=3, max_length=2000)
    duplicate_of: str | None = None
    resurrect_when: str | None = Field(default=None, max_length=1000)


class FindingLifecycleInput(BaseModel):
    status: Literal["open", "fix_claimed"]
    remediation: str = Field(default="", max_length=4000)
    note: str = Field(default="", max_length=2000)


class FindingRetestPlanInput(BaseModel):
    run_id: str
    note: str = Field(default="验证修复版本是否仍可复现同一根因", min_length=3, max_length=2000)


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


class ProgramRulesImportInput(BaseModel):
    engagement_id: str
    platform: str = Field(min_length=2, max_length=80)
    source_uri: str = Field(min_length=3, max_length=2048)
    rules_text: str = Field(min_length=20, max_length=100_000)
    valid_until: str
    scope_assets: list[str] = Field(min_length=1, max_length=500)
    impact_categories: dict[str, Literal["low", "medium", "high", "critical"]] = Field(min_length=1, max_length=100)
    known_issue_sources: list[str] = Field(min_length=1, max_length=100)
    previous_audit_sources: list[str] = Field(min_length=1, max_length=100)
    poc_policy: Literal["allowed", "restricted", "forbidden"]


class ProgramRuleAuthorizationInput(BaseModel):
    confirmation: Literal["CONFIRM_RULE_CHANGE"]
    note: str = Field(min_length=3, max_length=2000)


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
    receipt_id: str | None = None
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


class ResearchCampaignInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    objective: str = Field(min_length=8, max_length=4000)
    strategy: Literal["breadth_first", "depth_first", "risk_weighted", "business_logic"] = "business_logic"
    max_iterations: int = Field(default=20, ge=1, le=500)
    horizon_days: int = Field(default=30, ge=1, le=3650)
    coverage_target: float = Field(default=.85, ge=.1, le=1)


class CampaignScheduleInput(BaseModel):
    cadence_minutes: int = Field(default=1440, ge=15, le=10080)
    execution_mode: Literal["plan_only", "read_only_execute"] = "plan_only"
    preferred_run_id: str | None = Field(default=None, max_length=80)
    max_tests: int = Field(default=20, ge=1, le=50)
    start_immediately: bool = False


class CampaignScheduleUpdateInput(BaseModel):
    cadence_minutes: int | None = Field(default=None, ge=15, le=10080)
    execution_mode: Literal["plan_only", "read_only_execute"] | None = None
    preferred_run_id: str | None = Field(default=None, max_length=80)
    max_tests: int | None = Field(default=None, ge=1, le=50)


class WorkflowStepInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    method: Literal["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
    url: str = Field(min_length=4, max_length=2048)
    actor_role: str | None = Field(default=None, max_length=80)
    state_before: str = Field(default="", max_length=1000)
    expected_transition: str = Field(default="", max_length=2000)
    replay_safe: bool = True
    body: str | None = Field(default=None, max_length=65536)
    snapshot_url: str | None = Field(default=None, max_length=2048)
    compensation_method: Literal["POST", "PUT", "PATCH", "DELETE"] | None = None
    compensation_url: str | None = Field(default=None, max_length=2048)
    compensation_body: str | None = Field(default=None, max_length=65536)
    rollback_probe_urls: list[str] = Field(default_factory=list, max_length=10)
    extract: dict[str, str] = Field(default_factory=dict, max_length=20)
    requires_steps: list[int] = Field(default_factory=list, max_length=20)
    concurrency_safe: bool = False
    concurrency_replays: int = Field(default=2, ge=2, le=10)
    when_variable: str | None = Field(default=None, max_length=64)
    when_operator: Literal["exists", "not_exists", "equals", "not_equals"] | None = None
    when_value: str | int | float | bool | None = None
    max_repeats: int = Field(default=1, ge=1, le=5)
    repeat_until_pointer: str | None = Field(default=None, max_length=1000)
    repeat_until_value: str | int | float | bool | None = None


class ExecutableInvariantInput(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    kind: Literal[
        "status_in", "json_exists", "json_equals", "json_not_equals",
        "body_equals_step", "body_differs_step", "json_number_compare",
        "json_collection_contains", "json_collection_not_contains", "json_collection_unique",
        "json_collection_size_compare", "json_numeric_delta_equals", "json_sum_equals",
        "json_project_unique", "json_project_contains", "json_project_not_contains",
        "json_all_items_equal", "json_any_item_equals", "json_filtered_sum_equals",
    ]
    step: int = Field(ge=1, le=100)
    pointer: str | None = Field(default=None, max_length=1000)
    expected: str | int | float | bool | None = None
    expected_template: str | None = Field(default=None, max_length=2000)
    expected_statuses: list[int] = Field(default_factory=list, max_length=20)
    other_step: int | None = Field(default=None, ge=1, le=100)
    other_pointer: str | None = Field(default=None, max_length=1000)
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"] | None = None
    tolerance: float = Field(default=0, ge=0, le=1000000000)
    item_pointer: str | None = Field(default=None, max_length=1000)
    filter_pointer: str | None = Field(default=None, max_length=1000)
    filter_expected: str | int | float | bool | None = None


class BusinessWorkflowInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    objective: str = Field(min_length=8, max_length=2000)
    preconditions: list[str] = Field(default_factory=list, max_length=50)
    steps: list[WorkflowStepInput] = Field(min_length=1, max_length=100)
    invariants: list[str] = Field(min_length=1, max_length=100)
    executable_invariants: list[ExecutableInvariantInput] = Field(default_factory=list, max_length=100)
    depends_on_workflow_ids: list[str] = Field(default_factory=list, max_length=20)
    import_variables: dict[str, str] = Field(default_factory=dict, max_length=20)
    risk_class: Literal["read_only", "reversible", "state_changing"] = "read_only"


class BusinessWorkflowTemplateApplyInput(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict, max_length=20)


class ResearchHypothesisInput(BaseModel):
    workflow_id: str | None = None
    category: str = Field(min_length=2, max_length=120)
    statement: str = Field(min_length=8, max_length=4000)
    priority: int = Field(default=50, ge=1, le=100)
    next_action: str = Field(default="等待测试计划", max_length=2000)


class ResearchHypothesisUpdateInput(BaseModel):
    status: Literal["new", "planned", "testing", "open_proof_gap", "rejected", "archived"] | None = None
    priority: int | None = Field(default=None, ge=1, le=100)
    next_action: str | None = Field(default=None, min_length=2, max_length=2000)
    counterevidence_ids: list[str] | None = Field(default=None, max_length=100)


class CampaignExecuteInput(BaseModel):
    run_id: str
    max_tests: int = Field(default=10, ge=1, le=50)
    confirm_reversible_state_change: bool = False


class StateChangeRecoveryInput(BaseModel):
    confirm_compensation: bool = False


class OastProbeInput(BaseModel):
    run_id: str
    hypothesis_id: str | None = None
    callback_base: str = "http://127.0.0.1:8000/api/v1/oast/callback"
    expires_minutes: int = Field(default=30, ge=5, le=1440)


class SessionCaptureInput(BaseModel):
    login_url: str = Field(min_length=8, max_length=2048)
    max_requests: int = Field(default=300, ge=20, le=1000)


def _planned_capabilities(engagement: dict[str, Any], body: StartAnalysisInput) -> list[str]:
    repository = engagement.get("target_type") == "repository"
    if engagement["mode"] == "web3":
        planned = ["forge", "slither", "aderyn", "echidna", "medusa", "halmos", "gitleaks", "trivy"]
        if not repository:
            planned.extend(["anvil", "cast"])
        return planned
    cidr = engagement.get("target_type") == "cidr"
    planned = (["httpx"] if cidr and body.include_recon else
               [] if repository or not body.include_recon else ["subfinder", "httpx", "katana", "nuclei"])
    if repository or body.include_code or body.source_path:
        planned.extend(["semgrep", "gitleaks", "trivy"])
    if body.include_native_agent and not cidr:
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
        try:
            network = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            network = None
        if network is not None and "/" in raw:
            normalized = str(network)
            return {
                "mode": body.mode, "raw_target": raw, "target_type": "cidr",
                "normalized_target": normalized, "chain_id": None,
                "metadata": {"ip_version": network.version, "address_count": network.num_addresses},
            }
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


SOURCE_IMPORT_EXTENSIONS = {
    ".sol", ".vy", ".rs", ".go", ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".php", ".rb", ".move", ".toml", ".json",
    ".yaml", ".yml", ".lock", ".md",
}


def decode_source_archive(body: TargetPackageInput) -> bytes:
    if body.encoding != "base64":
        raise HTTPException(422, "源码压缩包必须使用 base64 编码上传")
    try:
        archive = base64.b64decode(body.content, validate=True)
    except (ValueError, binascii.Error) as error:
        raise HTTPException(422, "源码压缩包 base64 无效") from error
    if len(archive) > 20 * 1024 * 1024:
        raise HTTPException(413, "源码压缩包不能超过 20 MB")
    return archive


def inspect_source_archive(body: TargetPackageInput) -> tuple[dict[str, Any], bytes]:
    archive = decode_source_archive(body)
    try:
        package = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as error:
        raise HTTPException(422, "文件不是有效 ZIP 压缩包") from error
    accepted, rejected, total_size = [], [], 0
    with package:
        entries = package.infolist()
        if len(entries) > 2000:
            raise HTTPException(413, "源码压缩包文件数超过 2000")
        for info in entries:
            raw_name = info.filename.replace("\\", "/")
            path = Path(raw_name)
            unsafe = (not raw_name or raw_name.startswith("/") or ".." in path.parts
                      or any(part in {".git", "node_modules", "vendor", "dist", "build"} for part in path.parts))
            is_symlink = ((info.external_attr >> 16) & 0o170000) == 0o120000
            if info.is_dir():
                continue
            if unsafe or is_symlink:
                raise HTTPException(422, f"压缩包包含不安全路径或符号链接：{raw_name[:160]}")
            suffix = path.suffix.lower()
            if suffix not in SOURCE_IMPORT_EXTENSIONS or path.name.startswith("."):
                rejected.append(raw_name)
                continue
            if info.file_size > 5 * 1024 * 1024:
                raise HTTPException(413, f"单个源码文件超过 5 MB：{raw_name[:160]}")
            total_size += info.file_size
            if total_size > 100 * 1024 * 1024:
                raise HTTPException(413, "源码解压后不能超过 100 MB")
            if info.compress_size and info.file_size / info.compress_size > 200:
                raise HTTPException(413, f"源码文件压缩比异常：{raw_name[:160]}")
            accepted.append({"path": raw_name, "bytes": info.file_size,
                             "sha256": hashlib.sha256(package.read(info)).hexdigest()})
    if not accepted:
        raise HTTPException(422, "压缩包中没有支持的源码或构建配置文件")
    language_counts: dict[str, int] = {}
    for item in accepted:
        suffix = Path(item["path"]).suffix.lower() or "other"
        language_counts[suffix] = language_counts.get(suffix, 0) + 1
    preview = {
        "kind": "source_zip", "mode": body.mode,
        "source_sha256": hashlib.sha256(archive).hexdigest(),
        "filename": Path(body.filename or "source.zip").name,
        "files": accepted, "ignored_files": len(rejected),
        "counts": {"files": len(accepted), "bytes": total_size, "ignored": len(rejected)},
        "languages": language_counts,
        "boundary": "仅解压受支持的源码与构建配置；拒绝路径穿越、符号链接、隐藏文件、依赖/构建目录和异常压缩比。导入后仍需确认 Scope。",
    }
    return preview, archive


def parse_cidr_package(body: TargetPackageInput) -> dict[str, Any]:
    if body.encoding != "text" or body.mode != "traditional":
        raise HTTPException(422, "CIDR 导入仅支持 Traditional 文本")
    tokens = []
    for line in body.content.splitlines():
        value = line.split("#", 1)[0].strip()
        tokens.extend(item.strip() for item in re.split(r"[,\s]+", value) if item.strip())
    if not tokens or len(tokens) > 256:
        raise HTTPException(422, "CIDR 列表必须包含 1–256 个网段")
    networks = []
    for token in tokens:
        if "/" not in token:
            raise HTTPException(422, f"必须使用显式 CIDR 前缀：{token[:80]}")
        try:
            networks.append(ipaddress.ip_network(token, strict=False))
        except ValueError as error:
            raise HTTPException(422, f"CIDR 无效：{token[:80]}") from error
    collapsed = []
    for version in (4, 6):
        collapsed.extend(ipaddress.collapse_addresses(item for item in networks if item.version == version))
    if any(item.num_addresses > 65536 for item in collapsed):
        raise HTTPException(422, "单个 CIDR 最多包含 65,536 个地址；请缩小授权范围")
    ranges = [{"cidr": str(item), "ip_version": item.version, "addresses": item.num_addresses} for item in collapsed]
    return {
        "kind": "cidr", "mode": "traditional",
        "source_sha256": hashlib.sha256(body.content.encode()).hexdigest(),
        "root_target": ranges[0]["cidr"], "ranges": ranges,
        "counts": {"ranges": len(ranges), "addresses": sum(item["addresses"] for item in ranges)},
        "boundary": "CIDR 仅冻结为授权网络资产；HTTP 请求仍逐个经过 IP 归属、速率和预算门禁。不会自动扩大到相邻网段。",
    }


def parse_target_package(body: TargetPackageInput) -> dict[str, Any]:
    detected = body.kind
    if detected == "auto" and (body.encoding == "base64" or (body.filename or "").lower().endswith(".zip")):
        detected = "source_zip"
    if detected == "source_zip":
        return redact_structure(inspect_source_archive(body)[0])
    if detected == "cidr":
        return parse_cidr_package(body)
    try:
        document = json.loads(body.content)
    except ValueError:
        try:
            document = yaml.safe_load(body.content)
        except yaml.YAMLError as error:
            raise HTTPException(422, "目标包不是有效的 JSON 或 YAML") from error
    if not isinstance(document, dict):
        raise HTTPException(422, "目标包根节点必须是对象")
    if detected == "auto":
        if "openapi" in document or "swagger" in document:
            detected = "openapi"
        elif isinstance(document.get("log"), dict) and isinstance(document["log"].get("entries"), list):
            detected = "har"
        elif isinstance(document.get("item"), list) and "info" in document:
            detected = "postman"
        else:
            raise HTTPException(422, "无法识别目标包；请选择 OpenAPI、Postman、HAR、源码 ZIP 或 CIDR")
    records: list[tuple[str, str]] = []
    if detected == "openapi":
        servers = [item.get("url") for item in document.get("servers", []) if isinstance(item, dict)]
        if not servers and document.get("swagger"):
            schemes = document.get("schemes") or ["https"]
            servers = [f"{schemes[0]}://{document.get('host', '')}{document.get('basePath', '')}"]
        for server in servers:
            if not isinstance(server, str) or "{" in server:
                continue
            for path, operations in (document.get("paths") or {}).items():
                if not isinstance(operations, dict):
                    continue
                for method in operations:
                    if method.lower() in {"get", "head", "options", "post", "put", "patch", "delete"}:
                        records.append((method.upper(), server.rstrip("/") + "/" + str(path).lstrip("/")))
    elif detected == "har":
        for entry in document.get("log", {}).get("entries", []):
            request = entry.get("request", {}) if isinstance(entry, dict) else {}
            records.append((str(request.get("method") or "GET").upper(), str(request.get("url") or "")))
    else:
        def visit(items):
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("item"), list):
                    visit(item["item"])
                request = item.get("request")
                if isinstance(request, dict):
                    url = request.get("url")
                    raw = url.get("raw") if isinstance(url, dict) else url
                    records.append((str(request.get("method") or "GET").upper(), str(raw or "")))
        visit(document.get("item", []))
    safe, origins, seen = [], {}, set()
    for method, raw_url in records[:5000]:
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        port = f":{parsed.port}" if parsed.port else ""
        origin = f"{parsed.scheme}://{parsed.hostname.lower()}{port}"
        sanitized = urlunparse((parsed.scheme, f"{parsed.hostname.lower()}{port}", parsed.path or "/", "", "", ""))
        key = (method, sanitized)
        if key in seen:
            continue
        seen.add(key)
        origins[origin] = origins.get(origin, 0) + 1
        safe.append({"method": method, "url": sanitized, "origin": origin})
        if len(safe) >= 1000:
            break
    if not safe:
        raise HTTPException(422, "目标包没有可用的 HTTP(S) 请求；变量 URL、认证内容与非网络项不会导入")
    ordered_origins = sorted(origins, key=lambda value: (-origins[value], value))
    root = ordered_origins[0]
    return redact_structure({
        "kind": detected, "source_sha256": hashlib.sha256(body.content.encode()).hexdigest(),
        "root_target": root, "origins": [{"origin": value, "requests": origins[value],
                                             "default_in_scope": value == root} for value in ordered_origins],
        "endpoints": safe, "counts": {"endpoints": len(safe), "origins": len(ordered_origins)},
        "boundary": "只导入方法、去查询参数 URL 与来源哈希；Header、Cookie、Body 和变量值不会持久化。仅主来源默认进入 Scope。",
    })


@router.post("/target-packages/preview")
def preview_target_package(body: TargetPackageInput):
    return parse_target_package(body)


@router.post("/target-packages/apply", status_code=201)
def apply_target_package(body: TargetPackageApplyInput):
    parsed = parse_target_package(body)
    if parsed["kind"] == "source_zip":
        preview, archive = inspect_source_archive(body)
        destination = LOCAL_DATA_ROOT / "repositories" / uid("import")
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        accepted = {item["path"] for item in preview["files"]}
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as package:
                for info in package.infolist():
                    name = info.filename.replace("\\", "/")
                    if name not in accepted:
                        continue
                    output = destination.joinpath(*Path(name).parts)
                    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    output.write_bytes(package.read(info))
                    output.chmod(0o600)
            engagement = create_engagement(EngagementInput(
                name=body.name, target=str(destination.resolve()), mode=body.mode,
                scope_source="import:source_zip",
                scope={"import_source_sha256": preview["source_sha256"], "import_kind": "source_zip",
                       "source_manifest": preview["files"], "source_filename": preview["filename"]},
            ))
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        return {"engagement": engagement, "import": {**preview["counts"], "kind": "source_zip",
                                                         "mode": body.mode, "repository_path": str(destination.resolve())}}
    if parsed["kind"] == "cidr":
        ranges = [item["cidr"] for item in parsed["ranges"]]
        engagement = create_engagement(EngagementInput(
            name=body.name, target=parsed["root_target"], mode="traditional", scope_source="import:cidr",
            scope={"allowed_targets": ranges, "cidr_ranges": ranges,
                   "import_source_sha256": parsed["source_sha256"], "import_kind": "cidr"},
        ))
        return {"engagement": engagement, "import": {**parsed["counts"], "kind": "cidr"}}
    root = body.root_target or parsed["root_target"]
    known = {item["origin"] for item in parsed["origins"]}
    if root not in known:
        raise HTTPException(422, "所选根目标不在导入包来源列表中")
    endpoints = [item for item in parsed["endpoints"] if item["origin"] == root]
    engagement = create_engagement(EngagementInput(
        name=body.name, target=root, mode="traditional", scope_source=f"import:{parsed['kind']}",
        scope={"imported_surface": endpoints, "import_source_sha256": parsed["source_sha256"],
               "import_kind": parsed["kind"], "external_origins": sorted(known - {root})},
    ))
    return {"engagement": engagement, "import": {**parsed["counts"], "kind": parsed["kind"],
                                                    "in_scope_endpoints": len(endpoints),
                                                    "external_origins": len(known - {root})}}


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
        "allow_oast": False,
        "oast_allowed_hosts": [],
        "allow_authentication": False,
        "auth_allowed_hosts": [],
        "allow_reversible_state_change": False,
        "allow_concurrency_testing": False,
        "environment_class": "production",
        "requires_confirmation": True,
        **body.scope,
    }
    policy = {
        "max_requests": 100,
        "max_requests_per_second": 1,
        "max_runtime_minutes": 30,
        "max_concurrency": 2,
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
        db.execute("INSERT INTO entities VALUES(?,?,?,?,?,?,?)", (
            uid("entity"), engagement_id, "target", f"target:{resolved['normalized_target']}",
            resolved["normalized_target"], dump({
                "target_type": resolved["target_type"], "mode": body.mode,
                "environment_class": scope["environment_class"], "root": True,
            }), timestamp,
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
    with connect() as db:
        hidden_row = db.execute("SELECT value FROM app_metadata WHERE key=?", (f"task_center_hidden:{mode or 'all'}",)).fetchone()
    hidden = set(load(hidden_row["value"], []) if hidden_row else [])
    runs = [run for run in runs if run["id"] not in hidden]
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


@router.delete("/task-center")
def clear_task_center(mode: Literal["traditional", "web3"], body: MaintenanceConfirmInput):
    """Hide terminal task rows without deleting runs, evidence, findings, or reports."""
    if body.confirmation != "CLEAR_TERMINAL_TASKS":
        raise HTTPException(422, "任务记录清理确认值无效")
    runs = list_runs(mode)
    terminal = [run["id"] for run in runs if run["status"] not in {"queued", "running", "paused"}]
    key, timestamp = f"task_center_hidden:{mode}", utcnow()
    with connect() as db:
        previous = db.execute("SELECT value FROM app_metadata WHERE key=?", (key,)).fetchone()
        hidden = list(dict.fromkeys((load(previous["value"], []) if previous else []) + terminal))
        db.execute("INSERT OR REPLACE INTO app_metadata VALUES(?,?,?)", (key, dump(hidden), timestamp))
    return {"mode": mode, "hidden": len(terminal), "active_preserved": True, "evidence_preserved": True}


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
        candidate_count = db.execute("SELECT COUNT(*) FROM candidate_findings WHERE run_id=? AND status NOT IN ('archived','verified','graveyard')", (run_id,)).fetchone()[0]
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
            success_reasons = {"completed", "passed", "compiled", "read_only_completed"}
            failed = any(row["state"] == "failed" or (row["state"] == "tested" and row["reason"] not in success_reasons) for row in rows)
            status = "failed" if failed else ("completed" if all(row["state"] == "tested" and row["reason"] in success_reasons for row in rows) else "not_tested")
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
        {"id": "verification", "label": "候选与独立复验", "status": ("human_review" if candidate_count else "completed" if verified_count else "not_tested") if terminal else "queued", "summary": f"{candidate_count} 个 Candidate · {verified_count} 个 Verified"},
        {"id": "impact", "label": "影响与可提交性", "status": ("completed" if verified_count else "not_applicable") if terminal else "queued", "summary": "无 Verified Finding，未形成影响结论" if not verified_count else f"{verified_count} 个结果进入影响评估"},
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
        entity = db.execute(
            "SELECT id FROM entities WHERE engagement_id=? AND canonical_key=?",
            (run["engagement_id"], entity_key),
        ).fetchone()
        root = db.execute(
            "SELECT id FROM entities WHERE engagement_id=? AND entity_type='target' ORDER BY created_at LIMIT 1",
            (run["engagement_id"],),
        ).fetchone()
        if root and entity and root["id"] != entity["id"]:
            relation = db.execute(
                """SELECT id,evidence_ids FROM relationships
                   WHERE engagement_id=? AND source_id=? AND target_id=? AND relation_type='observed_surface'""",
                (run["engagement_id"], root["id"], entity["id"]),
            ).fetchone()
            if relation:
                refs = list(dict.fromkeys([*load(relation["evidence_ids"], []), observation_id]))
                db.execute("UPDATE relationships SET evidence_ids=? WHERE id=?", (dump(refs), relation["id"]))
            else:
                db.execute("INSERT INTO relationships VALUES(?,?,?,?,?,?,?)", (
                    uid("relation"), run["engagement_id"], root["id"], entity["id"],
                    "observed_surface", dump([observation_id]), utcnow(),
                ))
        coverage_state = "observed"  # Observation confidence is not a test execution receipt.
        db.execute("""INSERT INTO coverage_v2 VALUES(?,?,?,?,?,?,?)
          ON CONFLICT(run_id,surface_key) DO UPDATE SET state=excluded.state,reason=excluded.reason,
          observation_ids=excluded.observation_ids,updated_at=excluded.updated_at""", (
            uid("coverage"), run_id, body.subject, coverage_state,
            f"Observed by {body.source_capability}", dump([observation_id]), utcnow(),
        ))
    add_event(run_id, "processing", "observation.recorded", f"{body.source_capability} 输出已规范化为 Observation", {"observation_id": observation_id})
    return {"id": observation_id, **body.model_dump()}


@router.get("/engagements/{engagement_id}/asset-graph")
def engagement_asset_graph(engagement_id: str):
    engagement = get_engagement(engagement_id)
    with connect() as db:
        entity_rows = db.execute(
            "SELECT * FROM entities WHERE engagement_id=? ORDER BY created_at,id", (engagement_id,),
        ).fetchall()
        relation_rows = db.execute(
            "SELECT * FROM relationships WHERE engagement_id=? ORDER BY created_at,id", (engagement_id,),
        ).fetchall()
        observation_rows = db.execute(
            "SELECT * FROM observations WHERE engagement_id=? ORDER BY created_at,id", (engagement_id,),
        ).fetchall()
    target_host = urlparse(engagement["normalized_target"]).hostname
    allow_subdomains = bool(engagement["scope"].get("allow_subdomains", False))

    def scope_status(subject: str) -> str:
        subject_host = urlparse(subject).hostname if "://" in subject else None
        if not target_host or not subject_host:
            return "not_applicable"
        if subject_host == target_host or (allow_subdomains and subject_host.endswith(f".{target_host}")):
            return "in_scope"
        return "outside_root"

    nodes = []
    for row in entity_rows:
        item = dict(row)
        item["attributes"] = load(item["attributes"], {})
        if item["entity_type"] != "target":
            item["attributes"]["scope_status"] = scope_status(item["label"])
        nodes.append(item)
    known_keys = {item["canonical_key"] for item in nodes}
    for row in observation_rows:
        canonical_key = f"{row['observation_type']}:{row['subject']}".lower()
        if canonical_key in known_keys:
            continue
        nodes.append({
            "id": f"observed-{hashlib.sha256(canonical_key.encode()).hexdigest()[:16]}",
            "engagement_id": engagement_id, "entity_type": row["observation_type"],
            "canonical_key": canonical_key, "label": row["subject"], "attributes": {
                "source": row["source_capability"], "confidence": row["confidence"],
                "run_id": row["run_id"], "scope_status": scope_status(row["subject"]),
                "derived_from_observation": True,
            }, "created_at": row["created_at"],
        })
        known_keys.add(canonical_key)
    roots = [item for item in nodes if item["entity_type"] == "target"]
    if not roots:
        roots = [{
            "id": f"target-{engagement_id}", "engagement_id": engagement_id, "entity_type": "target",
            "canonical_key": f"target:{engagement['normalized_target']}",
            "label": engagement["normalized_target"], "attributes": {
                "target_type": engagement["target_type"], "mode": engagement["mode"],
                "environment_class": engagement["scope"].get("environment_class"), "root": True,
            }, "created_at": engagement["created_at"],
        }]
        nodes = [*roots, *nodes]
    edges = []
    for row in relation_rows:
        item = dict(row)
        item["evidence_ids"] = load(item["evidence_ids"], [])
        edges.append(item)
    linked = {item["target_id"] for item in edges}
    root_id = roots[0]["id"]
    for node in nodes:
        if node["id"] != root_id and node["id"] not in linked:
            edges.append({
                "id": f"inferred-{node['id']}", "engagement_id": engagement_id,
                "source_id": root_id, "target_id": node["id"], "relation_type": "observed_surface",
                "evidence_ids": [], "created_at": node["created_at"], "inferred": True,
            })
    by_type: dict[str, int] = {}
    for node in nodes:
        by_type[node["entity_type"]] = by_type.get(node["entity_type"], 0) + 1
    return redact_structure({
        "engagement_id": engagement_id, "mode": engagement["mode"], "root_id": root_id,
        "nodes": nodes, "edges": edges, "counts": {"nodes": len(nodes), "edges": len(edges), "by_type": by_type},
        "boundary": "关系图来自已持久化 Observation；未观察到的资产不会被推断为已测试。",
    })


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
    from candidate_quality import security_category
    with connect() as db:
        run = db.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "Run 不存在")
        observations = db.execute("SELECT * FROM observations WHERE run_id=? ORDER BY created_at", (run_id,)).fetchall()
    groups = {}
    for observation in observations:
        category = security_category(observation)
        if category:
            groups.setdefault((observation["subject"], category), []).append(observation)
    created = []
    for (subject, category), signals in groups.items():
        with connect() as db:
            existing = db.execute("SELECT id FROM candidate_findings WHERE run_id=? AND target=? AND category=?",
                                  (run_id, subject, category)).fetchone()
        if existing:
            continue
        sources = sorted({row["source_capability"] for row in signals})
        result = create_candidate(run_id, CandidateInput(
            title=f"待复核安全线索 · {category} · {subject}"[:300], category=category,
            target=subject, hypothesis="\n".join([
                f"来源：{', '.join(sources)}。工具线索尚未证明漏洞。",
                *[row["summary"] for row in signals[:5]],
                "下一步：确认具体安全边界、受影响对象和适用验证方法；普通公开信息应标记为观察。",
            ])[:4000], observation_ids=[row["id"] for row in signals],
        ))
        created.append(result)
    return {"run_id": run_id, "created": created, "count": len(created),
            "discovery_observations": sum(security_category(row) is None for row in observations)}


@router.get("/candidates/{candidate_id}")
def candidate_detail(candidate_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM candidate_findings WHERE id=?", (candidate_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Candidate 不存在")
        candidate = dict(row)
        ids = load(candidate["evidence_ids"], [])
        evidence = []
        for evidence_id in ids:
            item = db.execute("""SELECT e.id,e.summary,e.evidence_type,e.polarity,e.artifact_id,
                o.id AS observation_id,o.subject,o.source_capability,o.observation_type,o.summary AS observation_summary
                FROM evidence_v2 e LEFT JOIN observations o ON o.id=e.observation_id
                WHERE e.id=? AND e.run_id=?""", (evidence_id, candidate["run_id"])).fetchone()
            if item:
                evidence.append(dict(item))
        attempts = [dict(item) for item in db.execute("SELECT id,oracle,status,attempts,started_at,completed_at FROM verification_attempts WHERE candidate_id=? ORDER BY started_at DESC", (candidate_id,))]
        verification_jobs = [dict(item) for item in db.execute("""SELECT id,oracle,status,phase,
            completed_requests,total_requests,error,created_at,started_at,completed_at
            FROM verification_jobs WHERE candidate_id=? ORDER BY created_at DESC LIMIT 5""", (candidate_id,))]
        peer_candidates = [dict(item) for item in db.execute("""SELECT id,title,target,status
            FROM candidate_findings WHERE engagement_id=? AND id!=?
            AND status NOT IN ('archived','graveyard') ORDER BY updated_at DESC LIMIT 50""",
            (candidate["engagement_id"], candidate_id))]
        program_rows = db.execute("SELECT * FROM program_snapshots WHERE engagement_id=? ORDER BY version DESC",
                                  (candidate["engagement_id"],)).fetchall() if candidate["mode"] == "web3" else []
        program_authorizations = {item["snapshot_id"]: item for item in db.execute(
            "SELECT * FROM program_rule_authorizations WHERE engagement_id=?", (candidate["engagement_id"],),
        ).fetchall()} if candidate["mode"] == "web3" else {}
        engagement_row = db.execute("""SELECT t.normalized_target FROM engagements_v2 e
            JOIN target_specs t ON t.id=e.target_spec_id WHERE e.id=?""", (candidate["engagement_id"],)).fetchone()
    program_rules = []
    latest_rule_id = next((item["id"] for item in program_rows if load(item["rules"], {}).get("kind") == "program_rules"), None)
    for item in program_rows:
        rules = load(item["rules"], {})
        if rules.get("kind") == "program_rules":
            authorization = program_authorizations.get(item["id"])
            program_rules.append({
                "id": item["id"], "version": item["version"], "platform": item["platform"],
                "source_uri": item["source_uri"], "created_at": item["created_at"],
                "valid_until": rules.get("valid_until"), "scope_assets": rules.get("scope_assets", []),
                "impact_categories": rules.get("impact_categories", {}), "poc_policy": rules.get("poc_policy"),
                "authorization_status": authorization["status"] if authorization else "missing",
                "previous_snapshot_id": authorization["previous_snapshot_id"] if authorization else None,
                "diff": load(authorization["diff"], {}) if authorization else None,
                "confirmed_at": authorization["confirmed_at"] if authorization else None,
                "is_latest_program_rules": item["id"] == latest_rule_id,
            })
    candidate["evidence_ids"] = ids
    from candidate_quality import candidate_next_action
    action_plan = candidate_next_action(candidate)
    ordinary = action_plan["stage"] == "triage"
    property_candidate = candidate["mode"] == "web3" and candidate["category"] == "web3_property_violation"
    http_candidate = candidate["mode"] == "traditional" and candidate["category"].lower() in {
        "authorization", "authentication", "access_control", "cwe-639", "idor",
    }
    next_steps = (["先确认这是否涉及非公开资产或权限边界；公开目录、品牌与普通页面信息通常只是观察。"] if ordinary else [])
    next_steps.extend(["核对每条来源证据是否真正支持这项假设。", "明确合法行为、异常行为、所需身份与受影响对象。"])
    next_steps.append("执行两轮独立属性复测，再补部署对齐、反证与影响材料。" if property_candidate else "建立适用的验证计划，比较基线、测试行为及负对照；响应差异本身不等于漏洞。")
    next_steps.append("只有真实复验收据与必要证明材料齐备，才可升级为已验证漏洞。")
    return redact_structure({"candidate": candidate, "evidence": evidence, "attempts": attempts,
        "verification_jobs": verification_jobs,
        "peer_candidates": peer_candidates,
        "needs_triage": ordinary, "next_steps": next_steps, "next_action": action_plan,
        "verification_level": "V2 / 已复现" if candidate["status"] == "reproduced" else "V0 / 缺少安全语义" if ordinary or candidate["status"] == "needs_evidence" else "V1 / 待验证假设",
        "available_method": "web3_finalize" if property_candidate and candidate["status"] == "reproduced" else "web3_property" if property_candidate else "http_workbench" if http_candidate else "manual_review",
        "engagement_target": engagement_row["normalized_target"] if engagement_row else None,
        "program_rules": program_rules,
        "save_behavior": "保存只更新标题和判断说明，不执行复验，也不改变验证等级。"})


@router.post("/candidates/{candidate_id}/triage")
def triage_candidate(candidate_id: str, body: CandidateTriageInput):
    with connect() as db:
        candidate = db.execute("SELECT * FROM candidate_findings WHERE id=?", (candidate_id,)).fetchone()
        if not candidate:
            raise HTTPException(404, "Candidate 不存在")
        if candidate["status"] in {"verified", "archived"}:
            raise HTTPException(409, "已验证或已归档记录不能重新分诊")
        if body.disposition == "duplicate":
            if not body.duplicate_of or body.duplicate_of == candidate_id:
                raise HTTPException(422, "重复项必须指向同一项目中的另一条 Candidate")
            duplicate = db.execute("SELECT id FROM candidate_findings WHERE id=? AND engagement_id=?",
                                   (body.duplicate_of, candidate["engagement_id"])).fetchone()
            if not duplicate:
                raise HTTPException(422, "重复项引用不存在或不属于当前项目")
        if body.disposition in {"not_security", "duplicate"}:
            grave_id = uid("grave")
            reason = f"{body.disposition}: {body.reason.strip()}"
            if body.duplicate_of:
                reason += f" · duplicate_of={body.duplicate_of}"
            counter_ids = [row["id"] for row in db.execute(
                "SELECT id FROM evidence_v2 WHERE run_id=? AND polarity='counter'", (candidate["run_id"],))]
            db.execute("INSERT INTO graveyard VALUES(?,?,?,?,?,?,?)", (
                grave_id, candidate["engagement_id"], f"{candidate['category']}:{candidate['target']}",
                reason, dump(counter_ids), body.resurrect_when, utcnow(),
            ))
            status = "graveyard"
        else:
            grave_id = None
            status = "candidate" if body.disposition == "security_hypothesis" else "needs_evidence"
        db.execute("UPDATE candidate_findings SET status=?,updated_at=? WHERE id=?", (status, utcnow(), candidate_id))
    add_event(candidate["run_id"], "verification", "candidate.triaged", "候选分诊已记录", {
        "candidate_id": candidate_id, "disposition": body.disposition, "reason": body.reason,
        "duplicate_of": body.duplicate_of,
    })
    return {"candidate_id": candidate_id, "status": status, "disposition": body.disposition,
            "graveyard_id": grave_id, "history_preserved": True}


def finding_root_fingerprint(engagement_id: str, category: str, target: str, root_cause: str,
                             weakness: str, location: str) -> str:
    def normalized(value: str) -> str:
        return " ".join(value.strip().lower().split())
    source_location = normalized(location).split(":", 1)[0]
    material = "|".join((engagement_id, normalized(category), normalized(target),
                         normalized(root_cause), normalized(weakness), source_location))
    return hashlib.sha256(material.encode()).hexdigest()


def ensure_finding_lifecycle(db: sqlite3.Connection) -> None:
    rows = db.execute("""SELECT f.*,c.run_id FROM canonical_findings f
        JOIN candidate_findings c ON c.id=f.candidate_id""").fetchall()
    for row in rows:
        verification = load(row["verification"], {})
        fingerprint = finding_root_fingerprint(
            row["engagement_id"], row["category"], row["target"],
            verification.get("root_cause") or "unknown", verification.get("weakness") or "unknown",
            verification.get("location") or row["target"],
        )
        timestamp = row["updated_at"] or row["created_at"]
        db.execute("""INSERT OR IGNORE INTO finding_lifecycle
            VALUES(?,?,?,?,?,?,?,?,?,?)""", (
            row["id"], row["engagement_id"], fingerprint, "open", row["run_id"], row["run_id"],
            1, "", dump([{"at": row["created_at"], "kind": "first_seen", "run_id": row["run_id"]}]), timestamp,
        ))
        owner = db.execute("""SELECT * FROM finding_lifecycle
            WHERE engagement_id=? AND fingerprint=?""", (row["engagement_id"], fingerprint)).fetchone()
        occurrence = db.execute("SELECT id FROM finding_occurrences WHERE candidate_id=?", (row["candidate_id"],)).fetchone()
        if occurrence:
            continue
        occurrence_kind = "first_seen" if owner["finding_id"] == row["id"] else "migrated_repeat"
        if owner["finding_id"] != row["id"]:
            canonical_owner = db.execute("SELECT evidence_ids FROM canonical_findings WHERE id=?", (owner["finding_id"],)).fetchone()
            merged = list(dict.fromkeys([*load(canonical_owner["evidence_ids"], []), *load(row["evidence_ids"], [])]))
            history = load(owner["history"], [])
            history.append({"at": timestamp, "kind": occurrence_kind, "run_id": row["run_id"], "candidate_id": row["candidate_id"]})
            db.execute("UPDATE canonical_findings SET evidence_ids=?,updated_at=? WHERE id=?", (dump(merged), timestamp, owner["finding_id"]))
            db.execute("UPDATE canonical_findings SET status='deduplicated',updated_at=? WHERE id=?", (timestamp, row["id"]))
            db.execute("""UPDATE finding_lifecycle SET last_seen_run_id=?,occurrence_count=occurrence_count+1,
                history=?,updated_at=? WHERE finding_id=?""", (
                row["run_id"], dump(history), timestamp, owner["finding_id"],
            ))
        db.execute("INSERT INTO finding_occurrences VALUES(?,?,?,?,?,?,?)", (
            uid("occurrence"), owner["finding_id"], row["candidate_id"], row["run_id"], occurrence_kind,
            verification.get("receipt_id"), row["created_at"],
        ))


@router.post("/candidates/{candidate_id}/verify")
def verify_candidate(candidate_id: str, body: VerificationInput):
    deduplicated, occurrence_kind, lifecycle_status = False, "first_seen", "open"
    with connect() as db:
        candidate = db.execute("SELECT * FROM candidate_findings WHERE id=?", (candidate_id,)).fetchone()
        if not candidate:
            raise HTTPException(404, "Candidate 不存在")
        if candidate["status"] in {"verified", "archived"}:
            raise HTTPException(409, "Candidate 已验证或已归档")
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
        receipt_id = None
        if passed:
            from verification_receipts import validate_receipt
            receipt_id = validate_receipt(db, candidate, run, scope, body)
        attempt_id = uid("verify")
        result = {"receipt_id": receipt_id, "reproduced": body.reproduced, "counterevidence_checked": body.counterevidence_checked, "unsafe_oracle": unsafe_oracle}
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
        verification = {
            "oracle": body.oracle, "attempts": body.attempts, "attempt_id": attempt_id, "receipt_id": receipt_id,
            "steps": body.steps, "expected": body.expected, "actual": body.actual,
            "root_cause": body.root_cause, "weakness": body.weakness, "location": body.location,
            "poc_artifact_ids": body.poc_artifact_ids, "summary": candidate["hypothesis"],
        }
        impact = {"description": body.impact_description, "demonstrated": candidate["mode"] == "traditional", "feasibility": body.feasibility, "funds_at_risk": body.funds_at_risk}
        eligibility = {
            "in_scope": True if candidate["mode"] == "traditional" else body.impact_in_scope,
            "scope_snapshot_id": run["scope_snapshot_id"], "program_snapshot_id": body.program_snapshot_id,
            "known_issue_checked": body.known_issue_checked, "previous_audit_checked": body.previous_audit_checked,
            "poc_rule_checked": body.poc_rule_checked,
        }
        timestamp = utcnow()
        ensure_finding_lifecycle(db)
        fingerprint = finding_root_fingerprint(
            candidate["engagement_id"], candidate["category"], candidate["target"],
            body.root_cause, body.weakness, body.location,
        )
        existing = db.execute("""SELECT f.*,l.status AS lifecycle_status,l.history,l.occurrence_count
            FROM finding_lifecycle l JOIN canonical_findings f ON f.id=l.finding_id
            WHERE l.engagement_id=? AND l.fingerprint=?""",
            (candidate["engagement_id"], fingerprint),
        ).fetchone()
        if existing:
            deduplicated, finding_id = True, existing["id"]
            occurrence_kind = "reopened" if existing["lifecycle_status"] in {"fix_claimed", "retest_required", "verified_fixed"} else "repeat"
            lifecycle_status = "reopened" if occurrence_kind == "reopened" else "open"
            merged_evidence = list(dict.fromkeys([*load(existing["evidence_ids"], []), *evidence_ids]))
            history = load(existing["history"], [])
            history.append({"at": timestamp, "kind": occurrence_kind, "run_id": run["id"], "candidate_id": candidate_id})
            db.execute("""UPDATE canonical_findings SET candidate_id=?,title=?,severity=?,impact=?,eligibility=?,verification=?,
                evidence_ids=?,status='verified',updated_at=? WHERE id=?""", (
                candidate_id, candidate["title"], body.severity, dump(impact), dump(eligibility), dump(verification),
                dump(merged_evidence), timestamp, finding_id,
            ))
            db.execute("""UPDATE finding_lifecycle SET status=?,last_seen_run_id=?,occurrence_count=?,
                history=?,updated_at=? WHERE finding_id=?""", (
                lifecycle_status, run["id"], int(existing["occurrence_count"]) + 1,
                dump(history), timestamp, finding_id,
            ))
            planned_retest = db.execute("""SELECT * FROM finding_retests
                WHERE finding_id=? AND run_id=? AND status='planned' ORDER BY created_at DESC LIMIT 1""",
                (finding_id, run["id"]),
            ).fetchone()
            if planned_retest:
                planned_candidate_id = load(planned_retest["result"], {}).get("candidate_id")
                if planned_candidate_id and planned_candidate_id != candidate_id:
                    db.execute("UPDATE candidate_findings SET status='duplicate',updated_at=? WHERE id=?", (
                        timestamp, planned_candidate_id,
                    ))
            db.execute("""UPDATE finding_retests SET status='reproduced',
                result=?,completed_at=? WHERE finding_id=? AND run_id=? AND status='planned'""", (
                dump({"candidate_id": candidate_id, "receipt_id": receipt_id}), timestamp, finding_id, run["id"],
            ))
            evidence_ids = merged_evidence
        else:
            finding_id = uid("finding")
            db.execute("INSERT INTO canonical_findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                finding_id, candidate_id, candidate["engagement_id"], candidate["mode"], candidate["title"],
                candidate["category"], body.severity, candidate["target"], dump(impact), dump(eligibility),
                dump(verification), dump(evidence_ids), "verified", timestamp, timestamp,
            ))
            db.execute("INSERT INTO finding_lifecycle VALUES(?,?,?,?,?,?,?,?,?,?)", (
                finding_id, candidate["engagement_id"], fingerprint, "open", run["id"], run["id"], 1, "",
                dump([{"at": timestamp, "kind": "first_seen", "run_id": run["id"], "candidate_id": candidate_id}]), timestamp,
            ))
        db.execute("INSERT INTO finding_occurrences VALUES(?,?,?,?,?,?,?)", (
            uid("occurrence"), finding_id, candidate_id, run["id"], occurrence_kind, receipt_id, timestamp,
        ))
        db.execute("UPDATE candidate_findings SET status='verified',updated_at=? WHERE id=?", (timestamp, candidate_id))
    add_event(run["id"], "verification", "finding.reopened" if occurrence_kind == "reopened" else "finding.verified",
              "同根因在修复复测中再次出现" if occurrence_kind == "reopened" else "独立 Oracle 与反证门槛通过，已关联 CanonicalFinding",
              {"finding_id": finding_id, "deduplicated": deduplicated, "occurrence_kind": occurrence_kind})
    return {"id": finding_id, "candidate_id": candidate_id, "status": "verified", "evidence_ids": evidence_ids,
            "scope_snapshot_id": run["scope_snapshot_id"], "deduplicated": deduplicated,
            "occurrence_kind": occurrence_kind, "lifecycle_status": lifecycle_status}


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
        clauses, params = ["c.status NOT IN ('archived','graveyard','verified','verified_fixed','duplicate')"], []
        if mode:
            clauses.append("c.mode=?")
            params.append(mode)
        if run_id:
            clauses.append("c.run_id=?")
            params.append(run_id)
        where = " WHERE " + " AND ".join(clauses)
        candidates = [dict(row) for row in db.execute("SELECT c.* FROM candidate_findings c" + where + " ORDER BY c.created_at DESC", tuple(params))]
        ensure_finding_lifecycle(db)
        verified_clauses, verified_params = ["f.status='verified'"], []
        if mode:
            verified_clauses.append("f.mode=?")
            verified_params.append(mode)
        if run_id:
            verified_clauses.append("o.run_id=?")
            verified_params.append(run_id)
            verified_sql = """SELECT DISTINCT f.*,o.run_id AS run_id,l.status AS lifecycle_status,
                l.occurrence_count,l.first_seen_run_id,l.last_seen_run_id
                FROM canonical_findings f JOIN finding_occurrences o ON o.finding_id=f.id
                JOIN finding_lifecycle l ON l.finding_id=f.id"""
        else:
            verified_sql = """SELECT f.*,c.run_id AS run_id,l.status AS lifecycle_status,
                l.occurrence_count,l.first_seen_run_id,l.last_seen_run_id
                FROM canonical_findings f JOIN candidate_findings c ON c.id=f.candidate_id
                JOIN finding_lifecycle l ON l.finding_id=f.id"""
        verified_where = " WHERE " + " AND ".join(verified_clauses)
        verified = [dict(row) for row in db.execute(
            verified_sql + verified_where + " ORDER BY f.updated_at DESC", tuple(verified_params),
        )]
    for row in verified:
        for key in ("impact", "eligibility", "verification", "evidence_ids"):
            row[key] = load(row[key], {} if key != "evidence_ids" else [])
    from candidate_quality import candidate_next_action
    for row in candidates:
        row["evidence_ids"] = load(row["evidence_ids"], [])
        row["next_action"] = candidate_next_action(row)
    return {"verified": verified, "candidates": candidates}


@router.get("/findings/{finding_id}/lifecycle")
def get_finding_lifecycle(finding_id: str):
    with connect() as db:
        ensure_finding_lifecycle(db)
        row = db.execute("SELECT * FROM finding_lifecycle WHERE finding_id=?", (finding_id,)).fetchone()
        if not row:
            raise HTTPException(404, "CanonicalFinding 生命周期不存在")
        occurrences = [dict(item) for item in db.execute(
            "SELECT * FROM finding_occurrences WHERE finding_id=? ORDER BY created_at", (finding_id,),
        )]
        retests = [dict(item) for item in db.execute(
            "SELECT * FROM finding_retests WHERE finding_id=? ORDER BY created_at DESC", (finding_id,),
        )]
    value = dict(row)
    value["remediation"] = load(value["remediation"], {}) if value["remediation"] else {}
    value["history"] = load(value["history"], [])
    for item in retests:
        item["result"] = load(item["result"], None)
    return redact_structure({**value, "occurrences": occurrences, "retests": retests,
        "fixed_gate": "修复声明不会关闭 Finding；只有受支持的负向复测收据才能标记 verified_fixed。"})


@router.patch("/findings/{finding_id}/lifecycle")
def update_finding_lifecycle(finding_id: str, body: FindingLifecycleInput):
    with connect() as db:
        ensure_finding_lifecycle(db)
        row = db.execute("SELECT * FROM finding_lifecycle WHERE finding_id=?", (finding_id,)).fetchone()
        if not row:
            raise HTTPException(404, "CanonicalFinding 生命周期不存在")
        if body.status == "fix_claimed" and len(body.remediation.strip()) < 8:
            raise HTTPException(422, "声明修复时必须记录修复版本、提交或措施")
        timestamp = utcnow()
        history = load(row["history"], [])
        history.append({"at": timestamp, "kind": body.status, "note": body.note.strip()})
        remediation = load(row["remediation"], {}) if row["remediation"] else {}
        if body.remediation.strip():
            remediation = {"description": body.remediation.strip(), "claimed_at": timestamp}
        db.execute("""UPDATE finding_lifecycle SET status=?,remediation=?,history=?,updated_at=?
            WHERE finding_id=?""", (body.status, dump(remediation), dump(history), timestamp, finding_id))
    return get_finding_lifecycle(finding_id)


@router.post("/findings/{finding_id}/retest-plans", status_code=201)
def create_finding_retest_plan(finding_id: str, body: FindingRetestPlanInput):
    with connect() as db:
        ensure_finding_lifecycle(db)
        lifecycle = db.execute("SELECT * FROM finding_lifecycle WHERE finding_id=?", (finding_id,)).fetchone()
        if not lifecycle:
            raise HTTPException(404, "CanonicalFinding 生命周期不存在")
        run = db.execute("SELECT * FROM analysis_runs WHERE id=?", (body.run_id,)).fetchone()
        if not run or run["engagement_id"] != lifecycle["engagement_id"]:
            raise HTTPException(422, "复测 Run 必须属于同一项目")
        if run["id"] == lifecycle["last_seen_run_id"]:
            raise HTTPException(422, "复测必须选择新的 Run，不能复用最近一次证据所在 Run")
        existing = db.execute("""SELECT id FROM finding_retests
            WHERE finding_id=? AND run_id=? AND status='planned'""", (finding_id, body.run_id)).fetchone()
        if existing:
            raise HTTPException(409, "该 Run 已有待执行的根因复测计划")
        finding = db.execute("SELECT * FROM canonical_findings WHERE id=? AND status='verified'", (finding_id,)).fetchone()
        source_candidate = db.execute(
            "SELECT * FROM candidate_findings WHERE id=?", (finding["candidate_id"],),
        ).fetchone() if finding else None
        if not finding or not source_candidate:
            raise HTTPException(409, "Finding 缺少可复制的原始验证定义")
        timestamp, retest_id = utcnow(), uid("retest")
        observation_id, evidence_id, candidate_id = uid("obs"), uid("evidence"), uid("candidate")
        db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            observation_id, run["id"], run["engagement_id"], run["mode"], "retest.plan",
            finding["target"], f"Planned root-cause retest for {finding_id}", 1.0,
            "finding-lifecycle", finding_id, timestamp,
        ))
        db.execute("INSERT INTO evidence_v2 VALUES(?,?,?,?,?,?,?,?)", (
            evidence_id, observation_id, run["id"], "retest.plan",
            f"定向复测 {finding_id}：{body.note.strip()}", None, "context", timestamp,
        ))
        db.execute("INSERT INTO candidate_findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            candidate_id, run["id"], run["engagement_id"], run["mode"], finding["title"],
            finding["category"], finding["target"], source_candidate["hypothesis"],
            "candidate", dump([evidence_id]), timestamp, timestamp,
        ))
        db.execute("INSERT INTO finding_retests VALUES(?,?,?,?,?,?,?,?)", (
            retest_id, finding_id, body.run_id, "planned", body.note.strip(),
            dump({"candidate_id": candidate_id}), timestamp, None,
        ))
        history = load(lifecycle["history"], [])
        history.append({"at": timestamp, "kind": "retest_planned", "run_id": body.run_id, "retest_id": retest_id})
        db.execute("""UPDATE finding_lifecycle SET status='retest_required',history=?,updated_at=?
            WHERE finding_id=?""", (dump(history), timestamp, finding_id))
    add_event(body.run_id, "verification", "finding.retest_planned",
              "已从原始根因建立新 Run 定向复测候选", {"finding_id": finding_id, "candidate_id": candidate_id})
    return {"id": retest_id, "finding_id": finding_id, "run_id": body.run_id, "candidate_id": candidate_id,
            "status": "planned", "note": body.note.strip(), "created_at": timestamp}


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


@router.put("/findings/{finding_id}/report-fields")
def update_finding_report_fields(finding_id: str, body: FindingReportFieldsInput):
    """Add reviewed report context without mutating receipt-bound machine proof."""
    with connect() as db:
        row = db.execute(
            "SELECT * FROM canonical_findings WHERE id=? AND status='verified'", (finding_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Verified CanonicalFinding 不存在")
        finding_evidence = set(load(row["evidence_ids"], []))
        impact_ids = list(dict.fromkeys(body.impact_evidence_ids))
        counter_ids = list(dict.fromkeys(body.counterevidence_ids))
        requested = set(impact_ids + counter_ids)
        if not requested <= finding_evidence:
            raise HTTPException(409, "报告资料只能引用当前 Finding 已绑定的 Evidence")
        evidence_rows = {item["id"]: item for item in db.execute(
            f"SELECT * FROM evidence_v2 WHERE id IN ({','.join('?' for _ in requested)})",
            tuple(requested),
        ).fetchall()} if requested else {}
        if set(evidence_rows) != requested:
            raise HTTPException(409, "报告资料引用的 Evidence 不存在")
        invalid_counter = [evidence_id for evidence_id in counter_ids
                           if evidence_rows[evidence_id]["polarity"] != "counter"
                           and "counter" not in evidence_rows[evidence_id]["evidence_type"]]
        if invalid_counter:
            raise HTTPException(409, "反证引用必须指向 counter polarity 的 Evidence")
        invalid_impact = [evidence_id for evidence_id in impact_ids
                          if evidence_rows[evidence_id]["polarity"] == "counter"]
        if invalid_impact:
            raise HTTPException(409, "影响依据不能使用反证 Evidence")
        prerequisites = [value.strip() for value in body.prerequisites if value.strip()]
        if not prerequisites:
            raise HTTPException(422, "至少保留一个复现前置条件")
        verification = load(row["verification"], {})
        impact = load(row["impact"], {})
        verification["editorial"] = {
            "summary": body.summary.strip(), "prerequisites": prerequisites,
            "counterevidence_summary": body.counterevidence_summary.strip(),
            "counterevidence_ids": counter_ids, "remediation": body.remediation.strip(),
            "platform_custom": {str(key).strip(): str(value).strip()
                                for key, value in body.platform_custom.items()
                                if str(key).strip() and str(value).strip()},
            "reviewed_at": utcnow(),
        }
        impact["editorial"] = {
            "description": body.impact_description.strip(), "affected_users": body.affected_users.strip(),
            "conditions": body.impact_conditions.strip(), "evidence_ids": impact_ids,
        }
        db.execute("UPDATE canonical_findings SET verification=?,impact=?,updated_at=? WHERE id=?", (
            dump(verification), dump(impact), utcnow(), finding_id,
        ))
    finding, evidence, scope_id = finding_report_inputs(finding_id)
    return {"id": finding_id, "report_fields": universal_model(finding, evidence, scope_id),
            "receipt_preserved": True}


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
    attachments = build_proof_attachments(finding, evidence, scope_id)
    replay = attachments.get("proof/replay-contract.json", {})
    replay_kind = (replay.get("assertions") or {}).get("kind")
    supported = replay_kind in {"http_authorization_read_v2", "forge_property_replay_v1", "ptai_recorded_replay"}
    executable = replay.get("mode") == "isolated_local_execution"
    capsule = {
        "schema": "proof-capsule/1.0", "finding_id": finding_id,
        "scope_snapshot_id": scope_id, "verification": finding["verification"],
        "impact": finding["impact"], "eligibility": finding["eligibility"],
        "evidence": evidence, "portable": supported,
        "portability_status": "isolated_local_replay_ready" if executable else "recorded_assertion_ready" if supported else "unsupported_oracle",
        "replay": {"kind": replay_kind, "mode": replay.get("mode"), "active_execution_supported": executable},
    }
    capsule = redact_structure(capsule)
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


def build_proof_attachments(finding, evidence, scope_id):
    """Resolve actual proof bytes, validating ownership and integrity before exporting."""
    with connect() as db:
        candidate = db.execute("SELECT * FROM candidate_findings WHERE id=?", (finding["candidate_id"],)).fetchone()
        run = db.execute("SELECT * FROM analysis_runs WHERE id=?", (candidate["run_id"],)).fetchone()
        scope = db.execute("SELECT * FROM scope_snapshots WHERE id=?", (scope_id,)).fetchone()
        receipt_id = finding["verification"].get("receipt_id")
        row = db.execute("SELECT result FROM verification_attempts WHERE id=? AND candidate_id=? AND status='machine_receipt'", (receipt_id, candidate["id"])).fetchone()
        if not row:
            raise HTTPException(409, "历史结论没有机器复验收据，请重新验证后导出证明包")
        receipt = load(row["result"], {})
        # A historical proof may be exported after its promotion window expires, but its
        # source bytes, scope and original assertions must remain intact.
        artifact_ids = set(finding["verification"].get("poc_artifact_ids", []))
        if not artifact_ids or artifact_ids != set(receipt.get("artifacts", {})):
            raise HTTPException(409, "复现材料与验证收据不一致")
        oracle = finding["verification"].get("oracle") or ""
        files = {
            "proof/environment.json": {"run_id": run["id"], "mode": run["mode"],
                "scope_snapshot": {"id": scope["id"], "rules": load(scope["rules"], {}), "confirmed_at": scope["confirmed_at"]},
                "program_snapshot_id": finding["eligibility"].get("program_snapshot_id")},
            "proof/verification.json": receipt,
            "proof/expected.json": {"expected": finding["verification"].get("expected"), "actual": finding["verification"].get("actual")},
            "proof/steps.md": "# Reproduction steps\n\n" + "\n".join(f"{i}. {step}" for i, step in enumerate(finding["verification"].get("steps", []), 1)),
        }
        artifact_files = {}
        for artifact_id in sorted(artifact_ids):
            artifact = db.execute("SELECT * FROM artifacts WHERE id=? AND run_id=?", (artifact_id, run["id"])).fetchone()
            if not artifact:
                raise HTTPException(409, "复现 Artifact 不存在或不属于当前运行")
            path = Path(artifact["uri"])
            if not path.is_file() or path.stat().st_size > 10_000_000:
                raise HTTPException(409, "复现 Artifact 缺失或超过导出大小限制")
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != artifact["sha256"] or digest != receipt["artifacts"][artifact_id]:
                raise HTTPException(409, "复现 Artifact 哈希校验失败")
            try:
                value = json.loads(raw) if artifact["media_type"] == "application/json" else raw.decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                raise HTTPException(409, "复现 Artifact 无法安全解析和脱敏")
            extension = "json" if artifact["media_type"] == "application/json" else "txt"
            proof_name = f"proof/evidence/{artifact_id}.{extension}"
            files[proof_name] = value
            artifact_files[artifact_id] = proof_name
        replay_contract = {
            "schema": "fieldwork-replay-contract/1", "oracle": oracle,
            "artifact_files": artifact_files, "mode": "recorded_assertion",
            "expected": finding["verification"].get("expected"),
            "actual": finding["verification"].get("actual"),
        }
        if oracle == "http-authorization-read-v2":
            replay_contract["assertions"] = {
                "kind": "http_authorization_read_v2", "minimum_rounds": 2,
                "require_reproduced": True, "require_stable": True,
                "require_all_semantic_checks": True,
            }
        elif oracle == "forge-property-replay-v1":
            property_name = finding["target"]
            replay_contract["assertions"] = {
                "kind": "forge_property_replay_v1", "property": property_name,
                "minimum_rounds": 2, "require_distinct_seeds": True,
                "require_counterexamples": True, "require_stable_failure_reason": True,
            }
            source = db.execute("""SELECT subject FROM observations
                WHERE run_id=? AND observation_type='web3.compiler_result'
                ORDER BY created_at DESC LIMIT 1""", (run["id"],)).fetchone()
            root = Path(source["subject"]).expanduser().resolve() if source else None
            if root and root.is_dir():
                selected = []
                for relative in ("foundry.toml", "remappings.txt"):
                    path = root / relative
                    if path.is_file() and not path.is_symlink():
                        selected.append(path)
                for folder in ("src", "test", "script", "lib"):
                    base = root / folder
                    if base.is_dir():
                        selected.extend(path for path in base.rglob("*.sol") if path.is_file() and not path.is_symlink())
                total = 0
                for path in sorted(set(selected))[:300]:
                    relative = path.resolve().relative_to(root).as_posix()
                    raw = path.read_bytes()
                    total += len(raw)
                    if total > 5_000_000:
                        raise HTTPException(409, "可移植 Foundry 源码超过 5MB 限制")
                    try:
                        files[f"proof/source/{relative}"] = raw.decode("utf-8")
                    except UnicodeDecodeError as error:
                        raise HTTPException(409, "Foundry 源码包含不可导出的非 UTF-8 文件") from error
                if any(name.startswith("proof/source/src/") for name in files) and any(name.startswith("proof/source/test/") for name in files):
                    replay_contract.update({
                        "mode": "isolated_local_execution", "source_root": "proof/source",
                        "commands": [["forge", "test", "--offline", "--match-test", f"^{property_name.split('(', 1)[0]}",
                                      "--fuzz-seed", hex(seed), "--json"] for seed in (0xF13D01, 0xF13D02)],
                        "expected_exit": "nonzero_property_failure",
                    })
        elif oracle.startswith("ptai:"):
            replay_contract["assertions"] = {
                "kind": "ptai_recorded_replay", "require_integrity": True,
                "require_verified_verdict": True, "minimum_successful_rounds": 2,
            }
        else:
            replay_contract["assertions"] = {"kind": "unsupported", "reason": "No portable replay adapter registered"}
        files["proof/replay-contract.json"] = replay_contract
    return redact_structure(files)


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
    attachments = build_proof_attachments(finding, evidence, scope_id)
    path, manifest = export_bundle(package_id, model, platform, content, check, attachments)
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


@router.get("/verification-oracles")
def verification_oracles():
    """Publish the exact proof boundary instead of implying that every candidate is auto-verifiable."""
    return [
        {
            "id": "http-authorization-read-v2", "mode": "traditional", "level": "automatic_proof",
            "supports": ["CWE-639", "CWE-862", "对象级读取越权"],
            "requires": ["JSON GET 对象", "两个已认证主体", "主体与对象所有者字段", "未登录负对照", "两轮稳定结果"],
            "positive": "不同的已认证主体连续两轮读到同一所有者对象，同时未登录访问被拒绝。",
            "negative": "对象不同、身份归属缺失、未登录也可访问或服务正确拒绝越权时，均不确认漏洞。",
            "portable": "recorded_assertion", "promotes_finding": True,
        },
        {
            "id": "forge-property-replay-v1", "mode": "web3", "level": "automatic_proof",
            "supports": ["指定 Foundry 测试", "不变量或属性反例"],
            "requires": ["本地 Foundry 源码", "两个独立 fuzz seed", "每轮都有反例", "失败原因稳定", "已审阅项目规则快照"],
            "positive": "指定属性在两个独立 seed 下均失败，且都有反例和一致的失败原因。",
            "negative": "属性通过、缺少反例、原因波动或部署与规则不匹配时，均不能生成 Finding。",
            "portable": "isolated_local_execution", "promotes_finding": True,
        },
        {
            "id": "ptai:machine-oracle", "mode": "traditional", "level": "bounded_adapter",
            "supports": ["带已注册安全重放方法的 pentest-ai 证据胶囊"],
            "requires": ["胶囊完整性", "已确认 Scope", "安全执行强度", "所有重放轮次均通过"],
            "positive": "已注册的胶囊方法在每轮重放中都返回 verified。",
            "negative": "完整性失败、部分重放、目标不安全或仅返回 candidate 时，继续人工复核。",
            "portable": "recorded_assertion", "promotes_finding": True,
        },
        {
            "id": "unregistered", "mode": "traditional/web3", "level": "human_review_only",
            "supports": [], "requires": ["专用且版本化的 Oracle 适配器"],
            "positive": "当前没有可用的机器升级路径。",
            "negative": "人工文字、扫描器命中或修改 Oracle 名称都不会生成 Verified Finding。",
            "portable": "unsupported", "promotes_finding": False,
        },
    ]


@router.get("/runtime/readiness")
def runtime_readiness(lightweight: bool = False):
    """Summarize the default Docker-free production runtime."""
    if lightweight:
        # First-run diagnostics must never wait for every third-party CLI to
        # answer a version command. The detailed Tool Health refresh still
        # performs those probes; startup only needs executable presence.
        from capability_registry import SPECS, resolve_executable
        items = [{"id": name, "available": bool(resolve_executable(spec[1]))} for name, spec in SPECS.items()]
    else:
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
    try:
        readiness = runtime_readiness(lightweight=True)
    except TypeError:  # test adapters may expose the legacy no-argument contract
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


@router.post("/onboarding/repair")
def onboarding_repair():
    """Refresh installed-tool discovery and return an actionable proof result."""
    import capability_registry
    capability_registry.inventory(refresh=True)
    result = onboarding_checks(False)
    result["repair_attempted"] = True
    result["repair_actions"] = [
        "refreshed_standard_tool_paths",
        "refreshed_capability_inventory",
        "rechecked_runtime_model_browser_storage_and_database",
    ]
    return result


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
        owned_keychain_ids = [row[0] for row in db.execute(
            "SELECT id FROM identities WHERE credential_ref='keychain://fieldwork-session/' || id"
        )]
    if active:
        raise HTTPException(409, "存在运行中或暂停的任务，请先停止后再清空记录")
    if owned_keychain_ids:
        import session_capture
        for identity_id in owned_keychain_ids:
            try:
                session_capture.delete_keychain(identity_id)
            except RuntimeError as error:
                raise HTTPException(409, f"无法清理测试会话钥匙串：{error}") from error
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
        "oast_events", "oast_probes", "state_change_journal", "campaign_candidate_links", "campaign_iterations", "research_hypotheses", "business_workflows", "research_campaigns",
        "http_exchanges", "request_slots_v2", "run_configs_v2", "run_budgets_v2", "web3_forks", "invariant_registry",
        "graveyard", "coverage_v2", "identity_profiles", "identities", "program_rule_authorizations", "program_snapshots", "submission_packages_v2",
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
        row = db.execute("SELECT credential_ref FROM identities WHERE id=?", (identity_id,)).fetchone()
        owned_keychain = bool(row and row[0] == f"keychain://fieldwork-session/{identity_id}")
    if owned_keychain:
        import session_capture
        try:
            session_capture.delete_keychain(identity_id)
        except RuntimeError as error:
            raise HTTPException(409, f"无法清理测试会话钥匙串：{error}") from error
    with connect() as db:
        db.execute("DELETE FROM identity_profiles WHERE identity_id=?", (identity_id,))
        db.execute("DELETE FROM identities WHERE id=?", (identity_id,))
    return {"id": identity_id, "status": "deleted"}


def _session_capture_scope(identity_id: str, login_url: str) -> tuple[dict[str, Any], list[str]]:
    identity = get_identity(identity_id)
    engagement = get_engagement(identity["engagement_id"])
    if engagement["mode"] != "traditional" or engagement.get("target_type") == "repository":
        raise HTTPException(409, "可见登录态采集仅支持 Traditional Web/API 项目")
    if not engagement.get("confirmed_at") or not engagement["scope"].get("allow_authentication", False):
        raise HTTPException(409, "冻结 Scope 未显式允许登录态采集")
    parsed = urlparse(login_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(422, "login_url 必须是无凭据的 HTTP(S) URL")
    host = parsed.hostname.lower().rstrip(".")
    target_host = (urlparse(engagement["normalized_target"]).hostname or "").lower().rstrip(".")
    allowed = {target_host, *{
        str(item).lower().rstrip(".") for item in engagement["scope"].get("auth_allowed_hosts", [])
    }}
    local = host in {"localhost", "127.0.0.1", "::1"}
    if host not in allowed:
        raise HTTPException(409, "登录域名未列入冻结 Scope 的目标或 auth_allowed_hosts")
    if parsed.scheme != "https" and not local:
        raise HTTPException(409, "远程登录页必须使用 HTTPS")
    return identity, sorted(item for item in allowed if item)


@router.post("/identities/{identity_id}/session-captures", status_code=201)
def start_identity_session_capture(identity_id: str, body: SessionCaptureInput):
    identity, allowed_hosts = _session_capture_scope(identity_id, body.login_url)
    import session_capture
    try:
        result = session_capture.start(identity_id, body.login_url, allowed_hosts, body.max_requests)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    return {
        **result, "allowed_hosts": allowed_hosts, "max_requests": body.max_requests,
        "instructions": "在独立 Chrome 中完成授权测试账号登录，然后回到 Fieldwork 点击完成采集。",
        "privacy": "不会读取日常 Chrome 配置；Cookie 仅经内存管道写入 macOS Keychain。",
        "identity": {"id": identity["id"], "label": identity["label"], "role": identity["role"]},
    }


@router.get("/session-captures/{capture_id}")
def identity_session_capture_status(capture_id: str):
    import session_capture
    try:
        return session_capture.status(capture_id)
    except KeyError as error:
        raise HTTPException(404, "登录态采集不存在或服务已重启") from error


@router.post("/session-captures/{capture_id}/complete")
def complete_identity_session_capture(capture_id: str):
    import session_capture
    try:
        capture = session_capture.status(capture_id)
        result = session_capture.complete(capture_id)
        credential_ref = session_capture.store_keychain(capture["identity_id"], result["headers"])
    except KeyError as error:
        raise HTTPException(404, "登录态采集不存在或服务已重启") from error
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    timestamp = utcnow()
    with connect() as db:
        db.execute("UPDATE identities SET credential_ref=? WHERE id=?", (credential_ref, capture["identity_id"]))
        db.execute("""UPDATE identity_profiles SET auth_type='keychain_reference',session_status='ready',
          last_validated_at=?,updated_at=? WHERE identity_id=?""", (timestamp, timestamp, capture["identity_id"]))
    return {
        "id": capture_id, "identity_id": capture["identity_id"], "status": "stored",
        "cookie_count": result["cookie_count"], "domain_count": result["domain_count"],
        "requests_seen": result["requests_seen"], "requests_blocked": result["requests_blocked"],
        "credential_configured": True, "secret_persisted_outside_keychain": False,
    }


@router.delete("/session-captures/{capture_id}")
def cancel_identity_session_capture(capture_id: str):
    import session_capture
    if not session_capture.cancel(capture_id):
        raise HTTPException(404, "登录态采集不存在或服务已重启")
    return {"id": capture_id, "status": "cancelled"}


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


def hydrate_campaign(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    with connect() as db:
        value["workflow_count"] = db.execute("SELECT COUNT(*) FROM business_workflows WHERE campaign_id=? AND status='active'", (value["id"],)).fetchone()[0]
        value["hypothesis_count"] = db.execute("SELECT COUNT(*) FROM research_hypotheses WHERE campaign_id=? AND status!='archived'", (value["id"],)).fetchone()[0]
        value["open_hypotheses"] = db.execute("SELECT COUNT(*) FROM research_hypotheses WHERE campaign_id=? AND status IN ('new','planned','testing','open_proof_gap')", (value["id"],)).fetchone()[0]
        schedule = db.execute("SELECT * FROM campaign_schedules WHERE campaign_id=?", (value["id"],)).fetchone()
        value["schedule"] = dict(schedule) if schedule else None
        if value["schedule"]:
            value["schedule"]["enabled"] = bool(value["schedule"]["enabled"])
            value["schedule"].pop("lease_token", None)
    return value


BUSINESS_WORKFLOW_TEMPLATES = [
    {"id": "object_ownership", "name": "对象所有权隔离", "category": "access_control",
     "description": "比较所有者与其他已授权测试身份读取同一对象时的决策。",
     "variables": [{"name": "resource_url", "label": "对象 URL", "example": "https://authorized.example/api/orders/42"},
                   {"name": "owner_role", "label": "所有者角色", "example": "owner", "default": "owner"}]},
    {"id": "sequence_enforcement", "name": "流程顺序约束", "category": "business_logic",
     "description": "验证依赖步骤在跳过前置步骤时是否仍被错误接受。",
     "variables": [{"name": "prerequisite_url", "label": "前置步骤 URL", "example": "https://authorized.example/api/checkout/prepare"},
                   {"name": "dependent_url", "label": "依赖步骤 URL", "example": "https://authorized.example/api/checkout/confirm"},
                   {"name": "actor_role", "label": "执行角色", "example": "user", "default": "user"}]},
    {"id": "ledger_consistency", "name": "账本集合一致性", "category": "financial_logic",
     "description": "读取汇总和明细，检查账本项目标识是否唯一并保留跨身份差异矩阵。",
     "variables": [{"name": "summary_url", "label": "汇总 URL", "example": "https://authorized.example/api/wallet/summary"},
                   {"name": "ledger_url", "label": "明细 URL", "example": "https://authorized.example/api/wallet/entries"},
                   {"name": "item_pointer", "label": "明细 ID 指针", "example": "/id", "default": "/id"},
                   {"name": "actor_role", "label": "执行角色", "example": "account_owner", "default": "account_owner"}]},
    {"id": "entitlement_boundary", "name": "租户与权益边界", "category": "tenant_isolation",
     "description": "先读取当前权益，再访问受保护资源，并用其他测试身份重放整条序列。",
     "variables": [{"name": "entitlement_url", "label": "权益 URL", "example": "https://authorized.example/api/me/entitlements"},
                   {"name": "resource_url", "label": "受保护资源 URL", "example": "https://authorized.example/api/export"},
                   {"name": "actor_role", "label": "有权益角色", "example": "paid_user", "default": "paid_user"}]},
]


@router.get("/business-workflow-templates")
def list_business_workflow_templates():
    return BUSINESS_WORKFLOW_TEMPLATES


def workflow_from_template(template_id: str, values: dict[str, str]) -> BusinessWorkflowInput:
    template = next((item for item in BUSINESS_WORKFLOW_TEMPLATES if item["id"] == template_id), None)
    if not template:
        raise HTTPException(404, "业务流程模板不存在")
    resolved = {}
    for field in template["variables"]:
        value = str(values.get(field["name"], field.get("default", ""))).strip()
        if not value:
            raise HTTPException(422, f"模板缺少参数：{field['label']}")
        resolved[field["name"]] = value
    extra = set(values) - {item["name"] for item in template["variables"]}
    if extra:
        raise HTTPException(422, f"模板包含未知参数：{', '.join(sorted(extra))}")
    status_ok = lambda step: ExecutableInvariantInput(
        name=f"步骤 {step} 返回成功", kind="status_in", step=step, expected_statuses=[200],
    )
    if template_id == "object_ownership":
        steps = [WorkflowStepInput(name="读取目标对象", method="GET", url=resolved["resource_url"],
                                   actor_role=resolved["owner_role"], state_before="owner authenticated",
                                   expected_transition="read only", replay_safe=True)]
        invariants = ["对象所有者可读取；其他测试身份不得获得同一对象内容"]
        executable = [status_ok(1)]
    elif template_id == "sequence_enforcement":
        steps = [
            WorkflowStepInput(name="执行前置步骤", method="GET", url=resolved["prerequisite_url"],
                              actor_role=resolved["actor_role"], state_before="authenticated", expected_transition="prerequisite observed"),
            WorkflowStepInput(name="执行依赖步骤", method="GET", url=resolved["dependent_url"],
                              actor_role=resolved["actor_role"], state_before="prerequisite complete",
                              expected_transition="dependent action accepted", requires_steps=[1]),
        ]
        invariants = ["依赖步骤在前置步骤缺失或顺序颠倒时必须被服务端拒绝"]
        executable = [status_ok(1), status_ok(2)]
    elif template_id == "ledger_consistency":
        steps = [
            WorkflowStepInput(name="读取账户汇总", method="GET", url=resolved["summary_url"], actor_role=resolved["actor_role"], expected_transition="read only"),
            WorkflowStepInput(name="读取账本明细", method="GET", url=resolved["ledger_url"], actor_role=resolved["actor_role"], expected_transition="read only", requires_steps=[1]),
        ]
        invariants = ["账本明细标识必须唯一；汇总与明细应由同一授权账户读取"]
        executable = [status_ok(1), status_ok(2), ExecutableInvariantInput(
            name="账本项目标识唯一", kind="json_collection_unique", step=2, pointer="", item_pointer=resolved["item_pointer"],
        )]
    else:
        steps = [
            WorkflowStepInput(name="读取当前权益", method="GET", url=resolved["entitlement_url"], actor_role=resolved["actor_role"], expected_transition="entitlement observed"),
            WorkflowStepInput(name="读取受保护资源", method="GET", url=resolved["resource_url"], actor_role=resolved["actor_role"], expected_transition="authorized resource read", requires_steps=[1]),
        ]
        invariants = ["没有对应租户权益的身份不得读取受保护资源"]
        executable = [status_ok(1), status_ok(2)]
    return BusinessWorkflowInput(
        name=template["name"], objective=template["description"], preconditions=["至少一个就绪测试身份"],
        steps=steps, invariants=invariants, executable_invariants=executable, risk_class="read_only",
    )


@router.post("/campaigns/{campaign_id}/workflow-templates/{template_id}/apply", status_code=201)
def apply_business_workflow_template(campaign_id: str, template_id: str, body: BusinessWorkflowTemplateApplyInput):
    campaign = get_research_campaign(campaign_id)
    engagement = get_engagement(campaign["engagement_id"])
    if engagement["mode"] != "traditional" or engagement.get("target_type") in {"repository", "cidr"}:
        raise HTTPException(409, "业务流程模板仅适用于 Traditional Web/API 项目")
    workflow = create_business_workflow(campaign_id, workflow_from_template(template_id, body.variables))
    return {**workflow, "template_id": template_id}


@router.post("/engagements/{engagement_id}/campaigns", status_code=201)
def create_research_campaign(engagement_id: str, body: ResearchCampaignInput):
    engagement = get_engagement(engagement_id)
    if engagement["status"] == "archived":
        raise HTTPException(409, "归档项目不能创建长期研究 Campaign")
    campaign_id, timestamp = uid("campaign"), utcnow()
    with connect() as db:
        db.execute("INSERT INTO research_campaigns VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            campaign_id, engagement_id, body.name.strip(), body.objective.strip(), "active",
            body.strategy, body.max_iterations, 0, body.horizon_days, body.coverage_target,
            timestamp, timestamp,
        ))
    with connect() as db:
        row = db.execute("SELECT * FROM research_campaigns WHERE id=?", (campaign_id,)).fetchone()
    return hydrate_campaign(row)


@router.get("/engagements/{engagement_id}/campaigns")
def list_research_campaigns(engagement_id: str):
    get_engagement(engagement_id)
    with connect() as db:
        rows = db.execute("SELECT * FROM research_campaigns WHERE engagement_id=? ORDER BY updated_at DESC", (engagement_id,)).fetchall()
    return [hydrate_campaign(row) for row in rows]


@router.get("/campaigns/{campaign_id}")
def get_research_campaign(campaign_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM research_campaigns WHERE id=?", (campaign_id,)).fetchone()
        workflows = [dict(item) for item in db.execute("SELECT * FROM business_workflows WHERE campaign_id=? AND status='active' ORDER BY updated_at DESC", (campaign_id,))]
        hypotheses = [dict(item) for item in db.execute("SELECT * FROM research_hypotheses WHERE campaign_id=? AND status!='archived' ORDER BY priority DESC,updated_at DESC", (campaign_id,))]
        iterations = [dict(item) for item in db.execute("SELECT * FROM campaign_iterations WHERE campaign_id=? ORDER BY sequence DESC", (campaign_id,))]
        candidate_links = [dict(item) for item in db.execute("""SELECT l.*,c.title,c.status,c.run_id
            FROM campaign_candidate_links l JOIN candidate_findings c ON c.id=l.candidate_id
            WHERE l.campaign_id=? ORDER BY l.created_at DESC""", (campaign_id,))]
    if not row:
        raise HTTPException(404, "Research Campaign 不存在")
    for workflow in workflows:
        for key in ("preconditions", "steps", "invariants", "executable_invariants", "depends_on_workflow_ids"):
            workflow[key] = load(workflow[key], [])
        workflow["import_variables"] = load(workflow.get("import_variables"), {})
    for hypothesis in hypotheses:
        hypothesis["evidence_ids"] = load(hypothesis["evidence_ids"], [])
        hypothesis["counterevidence_ids"] = load(hypothesis["counterevidence_ids"], [])
    for iteration in iterations:
        iteration["plan"] = load(iteration["plan"], {})
        iteration["results"] = load(iteration["results"], {})
    campaign = hydrate_campaign(row)
    engagement = get_engagement(campaign["engagement_id"])
    return {
        **campaign, "workflows": workflows, "hypotheses": hypotheses, "iterations": iterations,
        "candidate_links": candidate_links,
        "oast_policy": {
            "allowed": bool(engagement.get("confirmed_at") and engagement["scope"].get("allow_oast", False)),
            "allowed_hosts": engagement["scope"].get("oast_allowed_hosts", []),
        },
    }


@router.get("/campaigns/{campaign_id}/trend")
def get_campaign_trend(campaign_id: str):
    get_research_campaign(campaign_id)
    with connect() as db:
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM campaign_iteration_metrics WHERE campaign_id=? ORDER BY sequence", (campaign_id,),
        )]
    for row in rows:
        row.pop("signature_hashes", None)
    plateau_streak = 0
    for row in reversed(rows):
        if row["new_test_signature_count"]:
            break
        plateau_streak += 1
    latest = rows[-1] if rows else None
    if not latest:
        recommendation = "execute_first_iteration"
    elif latest["execution_rate"] < .7:
        recommendation = "resolve_execution_blockers"
    elif latest["invariant_failures"]:
        recommendation = "prioritize_invariant_reproduction"
    elif plateau_streak >= 2 and latest["open_hypotheses_after"]:
        recommendation = "prioritize_directed_retests"
    elif plateau_streak >= 2:
        recommendation = "expand_workflows_and_identity_dimensions"
    else:
        recommendation = "continue_scheduled_coverage"
    return {
        "campaign_id": campaign_id, "iterations": rows, "latest": latest,
        "summary": {
            "measured_iterations": len(rows), "cumulative_test_signatures": latest["cumulative_test_signature_count"] if latest else 0,
            "plateau_streak": plateau_streak,
            "open_hypothesis_delta": (latest["open_hypotheses_after"] - rows[0]["open_hypotheses_before"]) if latest else 0,
            "total_new_hypotheses": sum(row["new_hypothesis_count"] for row in rows),
            "total_resolved_hypotheses": sum(row["resolved_hypothesis_count"] for row in rows),
            "recommendation": recommendation,
        },
    }


@router.post("/campaigns/{campaign_id}/workflows", status_code=201)
def create_business_workflow(campaign_id: str, body: BusinessWorkflowInput):
    campaign = get_research_campaign(campaign_id)
    engagement = get_engagement(campaign["engagement_id"])
    existing = {item["id"]: item for item in campaign["workflows"]}
    dependencies = list(dict.fromkeys(body.depends_on_workflow_ids))
    blocked, declared_variables = [], set(body.import_variables)
    if len(dependencies) != len(body.depends_on_workflow_ids):
        blocked.append({"step": 0, "reason": "duplicate_workflow_dependency"})
    read_methods = {"GET", "HEAD", "OPTIONS"}
    dependent_read_only = body.risk_class == "read_only" and len(body.steps) >= 2 and all(step.method in read_methods for step in body.steps)
    dependent_reversible = body.risk_class == "reversible" and bool(body.steps) and all(step.method not in read_methods for step in body.steps)
    if dependencies and not (dependent_read_only or dependent_reversible):
        blocked.append({"step": 0, "reason": "cross_workflow_dependency_requires_read_only_sequence_or_reversible_dag"})
    if dependencies and dependent_reversible and body.import_variables:
        blocked.append({"step": 0, "reason": "reversible_dependency_does_not_support_transient_imports"})
    for dependency_id in dependencies:
        source = existing.get(dependency_id)
        if not source:
            blocked.append({"step": 0, "reason": f"dependency_not_in_campaign:{dependency_id}"})
        elif dependent_read_only and (source["risk_class"] != "read_only" or len(source["steps"]) < 2 or any(step.get("method") not in read_methods for step in source["steps"])):
            blocked.append({"step": 0, "reason": f"dependency_not_read_only_sequence:{dependency_id}"})
        elif dependent_reversible and (
            source["risk_class"] != "reversible"
            or not source["steps"]
            or any(step.get("method") in read_methods for step in source["steps"])
            or source.get("import_variables")
        ):
            blocked.append({"step": 0, "reason": f"dependency_not_reversible_transaction:{dependency_id}"})
    for local_name, source_ref in body.import_variables.items():
        match = re.fullmatch(r"([^.]+)\.([A-Za-z_][A-Za-z0-9_]{0,63})", source_ref)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", local_name) or not match:
            blocked.append({"step": 0, "reason": "invalid_cross_workflow_import"})
            continue
        source_id, source_variable = match.groups()
        source = existing.get(source_id)
        exported = {name for step in source["steps"] for name in step.get("extract", {})} if source else set()
        if source_id not in dependencies:
            blocked.append({"step": 0, "reason": f"import_source_must_be_declared_dependency:{source_id}"})
        elif source_variable not in exported:
            blocked.append({"step": 0, "reason": f"imported_variable_not_exported:{source_id}.{source_variable}"})
    for index, step in enumerate(body.steps):
        if any(required < 1 or required >= index + 1 for required in step.requires_steps):
            blocked.append({"step": index + 1, "reason": "requires_steps_must_reference_earlier_steps"})
        if step.concurrency_safe and step.method not in {"GET", "HEAD", "OPTIONS"}:
            blocked.append({"step": index + 1, "reason": "concurrency_replay_only_allows_read_methods"})
        if bool(step.when_variable) != bool(step.when_operator):
            blocked.append({"step": index + 1, "reason": "branch_requires_variable_and_operator"})
        if step.when_variable and (
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", step.when_variable)
            or step.when_variable not in declared_variables | {"identity_role", "identity_tenant"}
        ):
            blocked.append({"step": index + 1, "reason": "branch_variable_not_available"})
        if step.when_operator in {"equals", "not_equals"} and step.when_value is None:
            blocked.append({"step": index + 1, "reason": "branch_comparison_requires_value"})
        if step.max_repeats > 1 and step.method not in {"GET", "HEAD", "OPTIONS"}:
            blocked.append({"step": index + 1, "reason": "bounded_loop_only_allows_read_methods"})
        if step.repeat_until_pointer is not None and (
            step.max_repeats == 1 or not (step.repeat_until_pointer == "" or step.repeat_until_pointer.startswith("/"))
        ):
            blocked.append({"step": index + 1, "reason": "repeat_until_requires_bounded_loop_and_json_pointer"})
        if step.concurrency_safe and step.max_repeats > 1:
            blocked.append({"step": index + 1, "reason": "loop_and_concurrency_must_be_separate_steps"})
        template_sources = [step.url, step.body or "", step.snapshot_url or "", step.compensation_url or "", step.compensation_body or "", *step.rollback_probe_urls]
        referenced = set().union(*(set(re.findall(r"\{\{([A-Za-z_][A-Za-z0-9_]{0,63})\}\}", value)) for value in template_sources))
        missing = referenced - declared_variables - {"identity_role", "identity_tenant"}
        if missing:
            blocked.append({"step": index + 1, "reason": f"template_variables_not_available:{','.join(sorted(missing))}"})
        for variable, pointer in step.extract.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", variable) or not (pointer == "" or pointer.startswith("/")):
                blocked.append({"step": index + 1, "reason": "invalid_extraction_declaration"})
            declared_variables.add(variable)
        safe_url = re.sub(r"\{\{[A-Za-z_][A-Za-z0-9_]{0,63}\}\}", "fieldwork-placeholder", step.url)
        policy = execution_policy_check(PolicyCheckInput(engagement_id=engagement["id"], target=safe_url, action="read"))
        if not policy["allowed"]:
            blocked.append({"step": index + 1, "reason": policy["reason"]})
        if step.method not in {"GET", "HEAD", "OPTIONS"} and body.risk_class == "read_only":
            blocked.append({"step": index + 1, "reason": "non_read_method_requires_reversible_or_state_changing_risk_class"})
        if step.method not in {"GET", "HEAD", "OPTIONS"} and body.risk_class == "reversible":
            if not all((step.snapshot_url, step.compensation_method, step.compensation_url)):
                blocked.append({"step": index + 1, "reason": "reversible_step_requires_snapshot_and_compensation"})
            for proof_url in (step.snapshot_url, step.compensation_url, *step.rollback_probe_urls):
                if proof_url:
                    safe_proof_url = re.sub(r"\{\{[A-Za-z_][A-Za-z0-9_]{0,63}\}\}", "fieldwork-placeholder", proof_url)
                    proof_policy = execution_policy_check(PolicyCheckInput(engagement_id=engagement["id"], target=safe_proof_url, action="read"))
                    if not proof_policy["allowed"]:
                        blocked.append({"step": index + 1, "reason": f"compensation_or_snapshot_{proof_policy['reason']}"})
        if step.rollback_probe_urls and (step.method in {"GET", "HEAD", "OPTIONS"} or body.risk_class != "reversible"):
            blocked.append({"step": index + 1, "reason": "rollback_probes_require_reversible_mutation"})
    for invariant in body.executable_invariants:
        if invariant.step > len(body.steps) or (invariant.other_step and invariant.other_step > len(body.steps)):
            blocked.append({"step": invariant.step, "reason": "invariant_references_unknown_step"})
        if invariant.kind.startswith("json_") and invariant.pointer is None:
            blocked.append({"step": invariant.step, "reason": "json_invariant_requires_pointer"})
        if invariant.kind == "status_in" and not invariant.expected_statuses:
            blocked.append({"step": invariant.step, "reason": "status_in_requires_expected_statuses"})
        if invariant.kind.startswith("body_") and invariant.other_step is None:
            blocked.append({"step": invariant.step, "reason": "body_comparison_requires_other_step"})
        if invariant.other_pointer is not None and not (invariant.other_pointer == "" or invariant.other_pointer.startswith("/")):
            blocked.append({"step": invariant.step, "reason": "invalid_other_json_pointer"})
        if invariant.kind in {"json_number_compare", "json_collection_size_compare"}:
            if invariant.operator is None:
                blocked.append({"step": invariant.step, "reason": "numeric_comparison_requires_operator"})
            if invariant.expected is None and invariant.expected_template is None:
                blocked.append({"step": invariant.step, "reason": "numeric_comparison_requires_expected_value"})
        if invariant.kind in {"json_collection_contains", "json_collection_not_contains"} and invariant.expected is None and invariant.expected_template is None:
            blocked.append({"step": invariant.step, "reason": "collection_membership_requires_expected_value"})
        if invariant.kind == "json_numeric_delta_equals" and (invariant.other_step is None or invariant.other_pointer is None):
            blocked.append({"step": invariant.step, "reason": "numeric_delta_requires_other_step_and_pointer"})
        if invariant.kind == "json_numeric_delta_equals" and invariant.expected is None and invariant.expected_template is None:
            blocked.append({"step": invariant.step, "reason": "numeric_delta_requires_expected_value"})
        if invariant.kind == "json_sum_equals" and invariant.other_step is not None and invariant.other_pointer is None:
            blocked.append({"step": invariant.step, "reason": "sum_step_comparison_requires_other_pointer"})
        if invariant.kind == "json_sum_equals" and invariant.other_step is None and invariant.expected is None and invariant.expected_template is None:
            blocked.append({"step": invariant.step, "reason": "sum_comparison_requires_expected_or_other_step"})
        projection_kinds = {"json_project_unique", "json_project_contains", "json_project_not_contains", "json_all_items_equal", "json_any_item_equals", "json_filtered_sum_equals"}
        if invariant.kind in projection_kinds and (invariant.item_pointer is None or not (invariant.item_pointer == "" or invariant.item_pointer.startswith("/"))):
            blocked.append({"step": invariant.step, "reason": "collection_projection_requires_item_pointer"})
        if invariant.kind in {"json_project_contains", "json_project_not_contains", "json_all_items_equal", "json_any_item_equals"} and invariant.expected is None and invariant.expected_template is None:
            blocked.append({"step": invariant.step, "reason": "collection_projection_requires_expected_value"})
        if invariant.kind == "json_filtered_sum_equals":
            if invariant.filter_pointer is None or not (invariant.filter_pointer == "" or invariant.filter_pointer.startswith("/")) or invariant.filter_expected is None:
                blocked.append({"step": invariant.step, "reason": "filtered_sum_requires_filter_pointer_and_value"})
            if invariant.other_step is not None and invariant.other_pointer is None:
                blocked.append({"step": invariant.step, "reason": "filtered_sum_step_comparison_requires_other_pointer"})
            if invariant.other_step is None and invariant.expected is None and invariant.expected_template is None:
                blocked.append({"step": invariant.step, "reason": "filtered_sum_requires_expected_or_other_step"})
    if blocked:
        raise HTTPException(409, {"message": "业务流程包含未授权或风险声明不一致的步骤", "blocked_steps": blocked})
    workflow_id, timestamp = uid("workflow"), utcnow()
    with connect() as db:
        db.execute("""INSERT INTO business_workflows
          (id,campaign_id,engagement_id,name,objective,preconditions,steps,invariants,risk_class,status,created_at,updated_at,executable_invariants,depends_on_workflow_ids,import_variables)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            workflow_id, campaign_id, engagement["id"], body.name.strip(), body.objective.strip(),
            dump(body.preconditions), dump([step.model_dump() for step in body.steps]), dump(body.invariants),
            body.risk_class, "active", timestamp, timestamp, dump([item.model_dump() for item in body.executable_invariants]),
            dump(dependencies), dump(body.import_variables),
        ))
        db.execute("UPDATE research_campaigns SET updated_at=? WHERE id=?", (timestamp, campaign_id))
    return {"id": workflow_id, "campaign_id": campaign_id, **body.model_dump(), "status": "active"}


@router.post("/campaigns/{campaign_id}/hypotheses", status_code=201)
def create_research_hypothesis(campaign_id: str, body: ResearchHypothesisInput):
    campaign = get_research_campaign(campaign_id)
    if body.workflow_id and not any(item["id"] == body.workflow_id for item in campaign["workflows"]):
        raise HTTPException(422, "workflow_id 不属于当前 Campaign")
    fingerprint = hashlib.sha256(f"{body.category.strip().lower()}:{' '.join(body.statement.lower().split())}".encode()).hexdigest()
    hypothesis_id, timestamp = uid("hypothesis"), utcnow()
    with connect() as db:
        existing = db.execute("SELECT id FROM research_hypotheses WHERE campaign_id=? AND fingerprint=?", (campaign_id, fingerprint)).fetchone()
        if existing:
            raise HTTPException(409, "相同研究假设已存在，将继续累积原记录")
        db.execute("INSERT INTO research_hypotheses VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            hypothesis_id, campaign_id, body.workflow_id, fingerprint, body.category.strip(), body.statement.strip(),
            "new", body.priority, dump([]), dump([]), 0, None, body.next_action.strip(), timestamp, timestamp,
        ))
        db.execute("UPDATE research_campaigns SET updated_at=? WHERE id=?", (timestamp, campaign_id))
    return {"id": hypothesis_id, "campaign_id": campaign_id, "fingerprint": fingerprint, "status": "new", **body.model_dump()}


@router.post("/campaigns/{campaign_id}/sync-memory")
def sync_campaign_memory(campaign_id: str):
    campaign = get_research_campaign(campaign_id)
    created = linked = 0
    with connect() as db:
        candidates = db.execute("SELECT * FROM candidate_findings WHERE engagement_id=? AND status!='archived' ORDER BY updated_at", (campaign["engagement_id"],)).fetchall()
        for candidate in candidates:
            fingerprint = hashlib.sha256(f"{candidate['category'].lower()}:{candidate['target'].lower()}:{' '.join(candidate['hypothesis'].lower().split())}".encode()).hexdigest()
            existing = db.execute("SELECT id,evidence_ids FROM research_hypotheses WHERE campaign_id=? AND fingerprint=?", (campaign_id, fingerprint)).fetchone()
            evidence_ids = load(candidate["evidence_ids"], [])
            if existing:
                merged = list(dict.fromkeys(load(existing["evidence_ids"], []) + evidence_ids))
                db.execute("UPDATE research_hypotheses SET evidence_ids=?,updated_at=? WHERE id=?", (dump(merged), utcnow(), existing["id"]))
                linked += 1
                continue
            db.execute("INSERT INTO research_hypotheses VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                uid("hypothesis"), campaign_id, None, fingerprint, candidate["category"], candidate["hypothesis"],
                "verified" if candidate["status"] == "verified" else "open_proof_gap", 60,
                dump(evidence_ids), dump([]), 0, None, "基于历史 Candidate 规划独立重放与负对照", utcnow(), utcnow(),
            ))
            created += 1
        db.execute("UPDATE research_campaigns SET updated_at=? WHERE id=?", (utcnow(), campaign_id))
    return {"campaign_id": campaign_id, "candidates_seen": len(candidates), "hypotheses_created": created, "hypotheses_linked": linked}


@router.patch("/campaigns/{campaign_id}/hypotheses/{hypothesis_id}")
def update_research_hypothesis(campaign_id: str, hypothesis_id: str, body: ResearchHypothesisUpdateInput):
    campaign = get_research_campaign(campaign_id)
    values = body.model_dump(exclude_none=True)
    if not values:
        raise HTTPException(422, "至少提供一个研究假设更新字段")
    with connect() as db:
        row = db.execute("SELECT * FROM research_hypotheses WHERE id=? AND campaign_id=?", (hypothesis_id, campaign_id)).fetchone()
        if not row:
            raise HTTPException(404, "长期研究假设不存在")
        transitions = {
            "new": {"planned", "archived"}, "planned": {"new", "testing", "archived"},
            "testing": {"planned", "open_proof_gap", "rejected"},
            "open_proof_gap": {"planned", "testing", "rejected", "archived"},
            "rejected": {"planned", "archived"}, "verified": {"planned", "archived"},
        }
        target_status = values.get("status")
        if target_status and target_status != row["status"] and target_status not in transitions.get(row["status"], set()):
            raise HTTPException(409, f"不允许从 {row['status']} 直接变为 {target_status}")
        if target_status == "rejected" and not values.get("counterevidence_ids") and not load(row["counterevidence_ids"], []):
            raise HTTPException(409, "拒绝研究假设前必须关联反证 Evidence")
        if "counterevidence_ids" in values:
            ids = list(dict.fromkeys(values["counterevidence_ids"]))
            if ids:
                marks = ",".join("?" for _ in ids)
                valid = db.execute(f"""SELECT COUNT(*) FROM evidence_v2 e JOIN analysis_runs r ON r.id=e.run_id
                  WHERE e.id IN ({marks}) AND r.engagement_id=? AND e.polarity='counter'""", (*ids, campaign["engagement_id"])).fetchone()[0]
                if valid != len(ids):
                    raise HTTPException(422, "反证必须属于当前项目且 polarity=counter")
            values["counterevidence_ids"] = dump(ids)
        values["updated_at"] = utcnow()
        db.execute("UPDATE research_hypotheses SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?", (*values.values(), hypothesis_id))
        db.execute("UPDATE research_campaigns SET updated_at=? WHERE id=?", (utcnow(), campaign_id))
    return next(item for item in get_research_campaign(campaign_id)["hypotheses"] if item["id"] == hypothesis_id)


def _expire_oast_probes(db: sqlite3.Connection) -> None:
    timestamp = utcnow()
    expired = db.execute(
        "SELECT id,run_id,status FROM oast_probes WHERE status IN ('pending','observed') AND expires_at<=?", (timestamp,),
    ).fetchall()
    for row in expired:
        db.execute("UPDATE oast_probes SET status='expired',updated_at=? WHERE id=?", (timestamp, row["id"]))
        if row["status"] == "pending":
            db.execute("""UPDATE coverage_v2 SET state='not_tested',reason=?,updated_at=?
              WHERE run_id=? AND surface_key=?""", (
                "探针已过期且未观测到回调；结果保持 NOT TESTED，不能作为安全反证",
                timestamp, row["run_id"], f"oast:{row['id']}",
            ))


def _hydrate_oast_probe(row: sqlite3.Row, include_events: bool = True) -> dict[str, Any]:
    value = dict(row)
    value.pop("token_sha256", None)
    with connect() as db:
        events = [dict(item) for item in db.execute(
            "SELECT * FROM oast_events WHERE probe_id=? ORDER BY received_at DESC", (value["id"],),
        )] if include_events else []
    for event in events:
        event["headers"] = load(event["headers"], {})
    value["events"] = events
    value["event_count"] = len(events)
    value["callback_url"] = None
    return value


def _validate_oast_callback_base(engagement: dict[str, Any], callback_base: str) -> tuple[str, bool]:
    parsed = urlparse(callback_base.strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(422, "OAST callback_base 必须是无凭据、无查询参数的 HTTP(S) URL")
    host = parsed.hostname.lower()
    local = host in {"localhost", "127.0.0.1", "::1"}
    allowed_hosts = {str(item).lower().rstrip(".") for item in engagement["scope"].get("oast_allowed_hosts", [])}
    if not local and parsed.scheme != "https":
        raise HTTPException(409, "远程 OAST 回调必须使用 HTTPS")
    if not local and host.rstrip(".") not in allowed_hosts:
        raise HTTPException(409, "OAST 回调域名未列入冻结 Scope 的 oast_allowed_hosts")
    return callback_base.strip().rstrip("/"), local


@router.post("/campaigns/{campaign_id}/oast-probes", status_code=201)
def create_oast_probe(campaign_id: str, body: OastProbeInput):
    campaign = get_research_campaign(campaign_id)
    engagement = get_engagement(campaign["engagement_id"])
    if not engagement.get("confirmed_at") or not engagement["scope"].get("allow_oast", False):
        raise HTTPException(409, "冻结 Scope 未显式允许 OAST")
    run = get_run(body.run_id)
    if run["engagement_id"] != engagement["id"] or run["mode"] != "traditional":
        raise HTTPException(409, "OAST 探针必须绑定当前项目的 Traditional Run")
    if run["status"] not in {"running", "paused", "completed"}:
        raise HTTPException(409, "当前 Run 状态不允许创建 OAST 探针")
    if body.hypothesis_id and not any(item["id"] == body.hypothesis_id for item in campaign["hypotheses"]):
        raise HTTPException(422, "hypothesis_id 不属于当前 Campaign")
    callback_base, local_only = _validate_oast_callback_base(engagement, body.callback_base)
    token = secrets.token_urlsafe(24)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    timestamp = utcnow()
    expires_at = datetime.fromtimestamp(time.time() + body.expires_minutes * 60, timezone.utc).isoformat()
    probe_id = uid("oast")
    with connect() as db:
        db.execute("INSERT INTO oast_probes VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            probe_id, engagement["id"], body.run_id, campaign_id, body.hypothesis_id,
            token_hash, callback_base, "pending", expires_at, timestamp, timestamp,
        ))
        db.execute("""INSERT INTO coverage_v2 VALUES(?,?,?,?,?,?,?)
          ON CONFLICT(run_id,surface_key) DO UPDATE SET state=excluded.state,reason=excluded.reason,updated_at=excluded.updated_at""", (
            uid("coverage"), body.run_id, f"oast:{probe_id}", "not_tested",
            "等待短期异步回调；过期无回调仍保持 NOT TESTED", dump([]), timestamp,
        ))
    add_event(body.run_id, "verification", "oast.probe_created", "已创建短期 OAST 关联探针，等待异步回调", {
        "probe_id": probe_id, "expires_at": expires_at, "local_only": local_only,
    })
    return {
        "id": probe_id, "campaign_id": campaign_id, "run_id": body.run_id,
        "hypothesis_id": body.hypothesis_id, "status": "pending", "expires_at": expires_at,
        "callback_url": f"{callback_base}/{token}", "local_only": local_only,
        "warning": "回调 URL 仅本次返回；请注入授权测试输入。无回调不代表无漏洞。",
    }


@router.get("/campaigns/{campaign_id}/oast-probes")
def list_oast_probes(campaign_id: str):
    get_research_campaign(campaign_id)
    with connect() as db:
        _expire_oast_probes(db)
        rows = db.execute("SELECT * FROM oast_probes WHERE campaign_id=? ORDER BY created_at DESC", (campaign_id,)).fetchall()
    return [_hydrate_oast_probe(row) for row in rows]


@router.post("/oast-probes/{probe_id}/revoke")
def revoke_oast_probe(probe_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM oast_probes WHERE id=?", (probe_id,)).fetchone()
        if not row:
            raise HTTPException(404, "OAST 探针不存在")
        if row["status"] in {"pending", "observed"}:
            db.execute("UPDATE oast_probes SET status='revoked',updated_at=? WHERE id=?", (utcnow(), probe_id))
            if row["status"] == "pending":
                db.execute("UPDATE coverage_v2 SET reason=?,updated_at=? WHERE run_id=? AND surface_key=?", (
                    "探针已人工撤销且未观测到回调；结果保持 NOT TESTED", utcnow(), row["run_id"], f"oast:{probe_id}",
                ))
    return {"id": probe_id, "status": "revoked" if row["status"] in {"pending", "observed"} else row["status"]}


def _safe_oast_headers(headers: Any) -> dict[str, str]:
    sensitive = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key"}
    return {
        str(key): "[REDACTED]" if str(key).lower() in sensitive else redact(str(value))
        for key, value in headers.items()
    }


@router.api_route("/oast/callback/{token}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "OPTIONS"])
async def receive_oast_callback(token: str, request: Request):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with connect() as db:
        _expire_oast_probes(db)
        probe = db.execute("SELECT * FROM oast_probes WHERE token_sha256=?", (token_hash,)).fetchone()
    if not probe:
        raise HTTPException(404, "OAST 探针不存在")
    if probe["status"] in {"expired", "revoked"}:
        raise HTTPException(410, f"OAST 探针已{ '过期' if probe['status']=='expired' else '撤销' }")
    body = await request.body()
    if len(body) > 65536:
        raise HTTPException(413, "OAST 回调体超过 64 KiB")
    timestamp = utcnow()
    body_hash = hashlib.sha256(body).hexdigest()
    headers = _safe_oast_headers(request.headers)
    query_preview = redact(request.url.query[:2000])
    body_preview = redact(body.decode(errors="replace")[:4000])
    source = request.client.host if request.client else ""
    source_hash = hashlib.sha256(source.encode()).hexdigest() if source else None
    canonical = dump({
        "probe_id": probe["id"], "method": request.method, "path": request.url.path,
        "query": query_preview, "headers": headers, "body_sha256": body_hash, "received_at": timestamp,
    })
    event_hash = hashlib.sha256(canonical.encode()).hexdigest()
    event_id, observation_id, evidence_id = uid("oast-event"), uid("obs"), uid("evidence")
    summary = f"收到 {request.method} 异步 HTTP 回调；body_sha256={body_hash[:12]}"
    with connect() as db:
        db.execute("INSERT INTO oast_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            event_id, probe["id"], "http", request.method, request.url.path, query_preview,
            dump(headers), body_preview, body_hash, source_hash, event_hash, timestamp,
        ))
        db.execute("UPDATE oast_probes SET status='observed',updated_at=? WHERE id=?", (timestamp, probe["id"]))
        db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            observation_id, probe["run_id"], probe["engagement_id"], "traditional", "oast_callback",
            f"oast:{probe['id']}", summary, .98, "oast-callback", event_id, timestamp,
        ))
        db.execute("INSERT INTO evidence_v2 VALUES(?,?,?,?,?,?,?,?)", (
            evidence_id, observation_id, probe["run_id"], "oast_http_callback", summary,
            None, "supporting", timestamp,
        ))
        db.execute("""INSERT INTO coverage_v2 VALUES(?,?,?,?,?,?,?)
          ON CONFLICT(run_id,surface_key) DO UPDATE SET state='tested',reason=excluded.reason,
          observation_ids=excluded.observation_ids,updated_at=excluded.updated_at""", (
            uid("coverage"), probe["run_id"], f"oast:{probe['id']}", "tested",
            "已收到关联 HTTP 回调；仍需独立确认触发路径与影响", dump([observation_id]), timestamp,
        ))
        if probe["hypothesis_id"]:
            hypothesis = db.execute("SELECT evidence_ids FROM research_hypotheses WHERE id=?", (probe["hypothesis_id"],)).fetchone()
            if hypothesis:
                merged = list(dict.fromkeys(load(hypothesis["evidence_ids"], []) + [evidence_id]))
                db.execute("""UPDATE research_hypotheses SET status='open_proof_gap',evidence_ids=?,
                  last_tested_at=?,next_action=?,updated_at=? WHERE id=?""", (
                    dump(merged), timestamp, "独立重放触发路径，并使用负对照排除背景流量", timestamp, probe["hypothesis_id"],
                ))
        db.execute("UPDATE research_campaigns SET updated_at=? WHERE id=?", (timestamp, probe["campaign_id"]))
    add_event(probe["run_id"], "verification", "oast.callback_observed", "收到与探针关联的异步 HTTP 回调", {
        "probe_id": probe["id"], "event_id": event_id, "evidence_id": evidence_id,
    })
    return {"received": True, "event_id": event_id}


def _build_campaign_matrix(campaign: dict[str, Any], workflow_ids: set[str] | None = None) -> dict[str, Any]:
    engagement = get_engagement(campaign["engagement_id"])
    ready = [item for item in list_identities(engagement["id"]) if item["session_status"] == "ready"]
    matrix, blocked = [], []
    requested = set(workflow_ids or [])
    if requested:
        all_by_id = {item["id"]: item for item in campaign["workflows"]}
        frontier = list(requested)
        while frontier:
            current_id = frontier.pop()
            for dependency_id in all_by_id.get(current_id, {}).get("depends_on_workflow_ids", []):
                if dependency_id not in requested:
                    requested.add(dependency_id)
                    frontier.append(dependency_id)
    selected = [item for item in campaign["workflows"] if not requested or item["id"] in requested]
    selected_by_id = {item["id"]: item for item in selected}
    workflows, remaining = [], set(selected_by_id)
    while remaining:
        ready_ids = sorted(workflow_id for workflow_id in remaining if not (set(selected_by_id[workflow_id].get("depends_on_workflow_ids", [])) & remaining))
        if not ready_ids:
            blocked.append({"workflow_id": "campaign", "step": 0, "reason": "workflow_dependency_cycle"})
            break
        for workflow_id in ready_ids:
            workflows.append(selected_by_id[workflow_id])
            remaining.remove(workflow_id)
    # Reversible workflows linked by dependency edges form one frozen business
    # transaction.  They must not execute (and compensate) independently,
    # otherwise a downstream action would observe an already-rolled-back parent.
    read_methods = {"GET", "HEAD", "OPTIONS"}
    reversible_ids = {
        item["id"] for item in workflows
        if item["risk_class"] == "reversible" and item["steps"]
        and all(step.get("method") not in read_methods for step in item["steps"])
        and not item.get("import_variables")
    }
    adjacency = {workflow_id: set() for workflow_id in reversible_ids}
    for workflow_id in reversible_ids:
        for dependency_id in selected_by_id[workflow_id].get("depends_on_workflow_ids", []):
            if dependency_id in reversible_ids:
                adjacency[workflow_id].add(dependency_id)
                adjacency[dependency_id].add(workflow_id)
    cross_group_by_workflow: dict[str, tuple[str, ...]] = {}
    seen_groups: set[str] = set()
    workflow_order = {item["id"]: index for index, item in enumerate(workflows)}
    for workflow_id in reversible_ids:
        if workflow_id in seen_groups or not adjacency[workflow_id]:
            continue
        component, frontier = set(), [workflow_id]
        while frontier:
            current_id = frontier.pop()
            if current_id in component:
                continue
            component.add(current_id)
            frontier.extend(adjacency[current_id] - component)
        seen_groups.update(component)
        ordered_component = tuple(sorted(component, key=workflow_order.get))
        for member_id in ordered_component:
            cross_group_by_workflow[member_id] = ordered_component
    for workflow in workflows:
        cross_group = cross_group_by_workflow.get(workflow["id"])
        if cross_group:
            if workflow["id"] != cross_group[-1]:
                continue
            transaction_steps, transaction_blocked, assertions = [], [], []
            global_step = 0
            for source_workflow_id in cross_group:
                source_workflow = selected_by_id[source_workflow_id]
                assertions.extend(f"{source_workflow['name']}: {value}" for value in source_workflow["invariants"])
                for workflow_step, step in enumerate(source_workflow["steps"], start=1):
                    global_step += 1
                    eligible = [item for item in ready if not step.get("actor_role") or item["role"] == step["actor_role"]]
                    if not eligible:
                        transaction_blocked.append({"workflow_id": source_workflow_id, "step": workflow_step, "reason": "required_identity_not_ready"})
                    elif not engagement["scope"].get("allow_reversible_state_change") or not engagement["policy"].get("allow_state_change"):
                        transaction_blocked.append({"workflow_id": source_workflow_id, "step": workflow_step, "reason": "reversible_state_change_not_in_frozen_scope"})
                    elif engagement["scope"].get("environment_class") not in {"local_fixture", "ephemeral_test", "staging_clone"}:
                        transaction_blocked.append({"workflow_id": source_workflow_id, "step": workflow_step, "reason": "state_change_requires_isolated_environment"})
                    else:
                        transaction_steps.append({
                            "step": global_step, "workflow_step": workflow_step,
                            "source_workflow_id": source_workflow_id,
                            "identity_id": eligible[0]["id"], **step,
                        })
            blocked.extend(transaction_blocked)
            if not transaction_blocked:
                matrix.append({
                    "kind": "cross_workflow_reversible_transaction",
                    "workflow_id": cross_group[-1], "workflow_ids": list(cross_group),
                    "internalized_dependency_ids": list(cross_group[:-1]),
                    "steps": transaction_steps, "assertions": assertions,
                })
            continue
        mutating_steps = [(index, step) for index, step in enumerate(workflow["steps"], start=1) if step.get("method") not in {"GET", "HEAD", "OPTIONS"}]
        if len(mutating_steps) > 1:
            transaction_steps, transaction_blocked = [], []
            for step_index, step in mutating_steps:
                eligible = [item for item in ready if not step.get("actor_role") or item["role"] == step["actor_role"]]
                if not eligible:
                    transaction_blocked.append({"workflow_id": workflow["id"], "step": step_index, "reason": "required_identity_not_ready"})
                elif workflow["risk_class"] != "reversible":
                    transaction_blocked.append({"workflow_id": workflow["id"], "step": step_index, "reason": "unbounded_state_change_is_never_executable"})
                elif not engagement["scope"].get("allow_reversible_state_change") or not engagement["policy"].get("allow_state_change"):
                    transaction_blocked.append({"workflow_id": workflow["id"], "step": step_index, "reason": "reversible_state_change_not_in_frozen_scope"})
                elif engagement["scope"].get("environment_class") not in {"local_fixture", "ephemeral_test", "staging_clone"}:
                    transaction_blocked.append({"workflow_id": workflow["id"], "step": step_index, "reason": "state_change_requires_isolated_environment"})
                else:
                    transaction_steps.append({"step": step_index, "identity_id": eligible[0]["id"], **step})
            blocked.extend(transaction_blocked)
            if not transaction_blocked:
                matrix.append({
                    "kind": "reversible_transaction", "workflow_id": workflow["id"],
                    "steps": transaction_steps, "assertions": workflow["invariants"],
                })
            continue
        if len(workflow["steps"]) > 1 and all(step.get("method") in {"GET", "HEAD", "OPTIONS"} for step in workflow["steps"]):
            actor_map, missing_roles = {}, []
            for role in sorted({step.get("actor_role") for step in workflow["steps"] if step.get("actor_role")}):
                match = next((item for item in ready if item["role"] == role), None)
                if match:
                    actor_map[role] = match["id"]
                else:
                    missing_roles.append(role)
            default_identity = ready[0]["id"] if ready else None
            if missing_roles or not default_identity:
                blocked.append({"workflow_id": workflow["id"], "step": 0, "reason": f"required_sequence_identities_not_ready:{','.join(missing_roles)}"})
            else:
                matrix.append({
                    "kind": "workflow_sequence", "workflow_id": workflow["id"], "steps": workflow["steps"],
                    "actor_identity_ids": actor_map, "default_identity_id": default_identity,
                    "assertions": workflow["invariants"], "executable_invariants": workflow.get("executable_invariants", []),
                })
                replay_variants = []
                if actor_map:
                    for role, source_id in actor_map.items():
                        for alternative in ready:
                            if alternative["role"] == role and alternative["id"] != source_id:
                                replay_variants.append(({**actor_map, role: alternative["id"]}, default_identity))
                else:
                    replay_variants.extend(({}, item["id"]) for item in ready if item["id"] != default_identity)
                for replay_map, replay_default in replay_variants[:10]:
                    matrix.append({
                        "kind": "cross_identity_sequence", "workflow_id": workflow["id"], "steps": workflow["steps"],
                        "source_actor_identity_ids": actor_map, "source_default_identity_id": default_identity,
                        "replay_actor_identity_ids": replay_map, "replay_default_identity_id": replay_default,
                        "assertions": workflow["invariants"], "executable_invariants": workflow.get("executable_invariants", []),
                    })
                if any(step.get("requires_steps") for step in workflow["steps"]):
                    matrix.append({
                        "kind": "sequence_violation", "workflow_id": workflow["id"], "steps": workflow["steps"],
                        "sequence": list(reversed(range(1, len(workflow["steps"]) + 1))),
                        "actor_identity_ids": actor_map, "default_identity_id": default_identity,
                        "assertions": workflow["invariants"],
                    })
                for step_index, step in enumerate(workflow["steps"], start=1):
                    if step.get("concurrency_safe"):
                        if not engagement["scope"].get("allow_concurrency_testing") or engagement["scope"].get("environment_class") not in {"local_fixture", "ephemeral_test", "staging_clone"}:
                            blocked.append({"workflow_id": workflow["id"], "step": step_index, "reason": "concurrency_test_requires_isolated_scope"})
                        else:
                            matrix.append({
                                "kind": "concurrent_step", "workflow_id": workflow["id"], "steps": workflow["steps"],
                                "target_step": step_index, "replays": step.get("concurrency_replays", 2),
                                "actor_identity_ids": actor_map, "default_identity_id": default_identity,
                                "assertions": workflow["invariants"],
                            })
            continue
        for step_index, step in enumerate(workflow["steps"]):
            eligible = [item for item in ready if not step.get("actor_role") or item["role"] == step["actor_role"]]
            if not eligible:
                blocked.append({"workflow_id": workflow["id"], "step": step_index + 1, "reason": "required_identity_not_ready"})
                continue
            mutating = step.get("method") not in {"GET", "HEAD", "OPTIONS"}
            if mutating:
                if workflow["risk_class"] != "reversible":
                    blocked.append({"workflow_id": workflow["id"], "step": step_index + 1, "reason": "unbounded_state_change_is_never_executable"})
                elif not engagement["scope"].get("allow_reversible_state_change") or not engagement["policy"].get("allow_state_change"):
                    blocked.append({"workflow_id": workflow["id"], "step": step_index + 1, "reason": "reversible_state_change_not_in_frozen_scope"})
                elif engagement["scope"].get("environment_class") not in {"local_fixture", "ephemeral_test", "staging_clone"}:
                    blocked.append({"workflow_id": workflow["id"], "step": step_index + 1, "reason": "state_change_requires_isolated_environment"})
                else:
                    matrix.append({
                        "kind": "reversible_transition", "workflow_id": workflow["id"], "step": step_index + 1,
                        "identity_id": eligible[0]["id"], **step, "assertions": workflow["invariants"],
                    })
                continue
            for identity in eligible:
                matrix.append({"kind": "baseline", "workflow_id": workflow["id"], "step": step_index + 1, "identity_id": identity["id"], "method": step["method"], "url": step["url"], "expected": step["expected_transition"]})
            for left in eligible:
                for right in ready:
                    if left["id"] != right["id"] and (left["role"] != right["role"] or left.get("tenant") != right.get("tenant")):
                        matrix.append({"kind": "cross_identity_replay", "workflow_id": workflow["id"], "step": step_index + 1, "source_identity_id": left["id"], "replay_identity_id": right["id"], "method": step["method"], "url": step["url"], "assertions": workflow["invariants"]})
            if step.get("replay_safe"):
                matrix.append({"kind": "duplicate_replay", "workflow_id": workflow["id"], "step": step_index + 1, "identity_id": eligible[0]["id"], "method": step["method"], "url": step["url"], "assertions": workflow["invariants"]})
        if len(workflow["steps"]) > 1:
            matrix.append({"kind": "sequence_violation", "workflow_id": workflow["id"], "sequence": list(reversed(range(1, len(workflow["steps"]) + 1))), "assertions": workflow["invariants"]})
    for test in matrix:
        workflow = selected_by_id.get(test.get("workflow_id"))
        if workflow:
            internalized = set(test.get("internalized_dependency_ids", []))
            test["depends_on_workflow_ids"] = [item for item in workflow.get("depends_on_workflow_ids", []) if item not in internalized]
            test["import_variables"] = workflow.get("import_variables", {})
    return {"tests": matrix, "blocked": blocked, "identity_count": len(ready), "workflow_count": len(workflows)}


def _insert_campaign_iteration(campaign: dict[str, Any], matrix: dict[str, Any], strategy: str,
                               hypothesis_id: str | None = None) -> dict[str, Any]:
    campaign_id, iteration_id, timestamp = campaign["id"], uid("iteration"), utcnow()
    plan = {"strategy": strategy, **({"hypothesis_id": hypothesis_id} if hypothesis_id else {}), **matrix}
    with connect() as db:
        sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM campaign_iterations WHERE campaign_id=?", (campaign_id,)).fetchone()[0]
        db.execute("INSERT INTO campaign_iterations VALUES(?,?,?,?,?,?,?,?,?,?)", (
            iteration_id, campaign_id, None, sequence, "planned", dump(plan), dump({}), None, None, timestamp,
        ))
        if hypothesis_id:
            db.execute("UPDATE research_hypotheses SET status='planned',next_action=?,updated_at=? WHERE id=? AND campaign_id=?", (
                f"执行第 {sequence} 轮定向复测", timestamp, hypothesis_id, campaign_id,
            ))
        db.execute("UPDATE research_campaigns SET updated_at=? WHERE id=?", (timestamp, campaign_id))
    return {"id": iteration_id, "campaign_id": campaign_id, "sequence": sequence, "status": "planned", **plan}


@router.post("/campaigns/{campaign_id}/iterations/plan", status_code=201)
def plan_campaign_iteration(campaign_id: str):
    campaign = get_research_campaign(campaign_id)
    if campaign["status"] != "active":
        raise HTTPException(409, "Campaign 已暂停或归档，不能生成新轮次")
    if campaign["iterations_completed"] >= campaign["max_iterations"]:
        raise HTTPException(409, "Campaign 已达到最大研究轮次")
    matrix = _build_campaign_matrix(campaign)
    return _insert_campaign_iteration(campaign, matrix, campaign["strategy"])


@router.post("/campaigns/{campaign_id}/hypotheses/{hypothesis_id}/retest-plan", status_code=201)
def plan_hypothesis_retest(campaign_id: str, hypothesis_id: str):
    campaign = get_research_campaign(campaign_id)
    if campaign["status"] != "active":
        raise HTTPException(409, "Campaign 已暂停或归档，不能生成定向复测")
    hypothesis = next((item for item in campaign["hypotheses"] if item["id"] == hypothesis_id), None)
    if not hypothesis:
        raise HTTPException(404, "长期研究假设不存在")
    if not hypothesis.get("workflow_id"):
        raise HTTPException(409, "该历史假设尚未绑定业务流程，请先关联流程后再定向复测")
    if hypothesis["status"] in {"archived", "verified"}:
        raise HTTPException(409, "当前假设状态不能直接定向复测")
    matrix = _build_campaign_matrix(campaign, {hypothesis["workflow_id"]})
    return _insert_campaign_iteration(campaign, matrix, "directed_retest", hypothesis_id)


def _validate_campaign_schedule(campaign: dict[str, Any], execution_mode: str, preferred_run_id: str | None) -> None:
    if campaign["status"] != "active":
        raise HTTPException(409, "只有 active Campaign 可以启用长期调度")
    if execution_mode == "read_only_execute":
        if not preferred_run_id:
            raise HTTPException(422, "自动执行只读轮次必须绑定同项目 Run")
        with connect() as db:
            run = db.execute("SELECT engagement_id,mode,status FROM analysis_runs WHERE id=?", (preferred_run_id,)).fetchone()
        if not run or run["engagement_id"] != campaign["engagement_id"] or run["mode"] != "traditional":
            raise HTTPException(422, "preferred_run_id 必须属于同一 Traditional 项目")
        if run["status"] not in {"running", "paused", "completed"}:
            raise HTTPException(409, "绑定 Run 当前不能用于长期只读研究")


@router.put("/campaigns/{campaign_id}/schedule")
def configure_campaign_schedule(campaign_id: str, body: CampaignScheduleInput):
    campaign = get_research_campaign(campaign_id)
    _validate_campaign_schedule(campaign, body.execution_mode, body.preferred_run_id)
    timestamp = utcnow()
    next_run = timestamp if body.start_immediately else (datetime.now(timezone.utc) + timedelta(minutes=body.cadence_minutes)).isoformat()
    with connect() as db:
        db.execute("""INSERT INTO campaign_schedules
          (campaign_id,enabled,cadence_minutes,execution_mode,preferred_run_id,max_tests,next_run_at,last_planned_at,
           paused_at,lease_token,lease_expires_at,failure_count,last_error,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(campaign_id) DO UPDATE SET enabled=1,cadence_minutes=excluded.cadence_minutes,
          execution_mode=excluded.execution_mode,preferred_run_id=excluded.preferred_run_id,max_tests=excluded.max_tests,
          next_run_at=excluded.next_run_at,paused_at=NULL,lease_token=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=excluded.updated_at""", (
            campaign_id, 1, body.cadence_minutes, body.execution_mode, body.preferred_run_id, body.max_tests,
            next_run, None, None, None, None, 0, None, timestamp, timestamp,
        ))
    return get_research_campaign(campaign_id)["schedule"]


@router.patch("/campaigns/{campaign_id}/schedule")
def update_campaign_schedule(campaign_id: str, body: CampaignScheduleUpdateInput):
    campaign = get_research_campaign(campaign_id)
    schedule = campaign.get("schedule")
    if not schedule:
        raise HTTPException(404, "Campaign 调度尚未配置")
    values = body.model_dump(exclude_none=True)
    if not values:
        raise HTTPException(422, "至少提供一个调度更新字段")
    mode = values.get("execution_mode", schedule["execution_mode"])
    run_id = values.get("preferred_run_id", schedule.get("preferred_run_id"))
    _validate_campaign_schedule(campaign, mode, run_id)
    values["updated_at"] = utcnow()
    with connect() as db:
        db.execute("UPDATE campaign_schedules SET " + ",".join(f"{key}=?" for key in values) + " WHERE campaign_id=?", (*values.values(), campaign_id))
    return get_research_campaign(campaign_id)["schedule"]


@router.post("/campaigns/{campaign_id}/schedule/pause")
def pause_campaign_schedule(campaign_id: str):
    get_research_campaign(campaign_id)
    timestamp = utcnow()
    with connect() as db:
        changed = db.execute("""UPDATE campaign_schedules SET enabled=0,paused_at=?,lease_token=NULL,
          lease_expires_at=NULL,updated_at=? WHERE campaign_id=? AND enabled=1""", (timestamp, timestamp, campaign_id)).rowcount
    if not changed:
        raise HTTPException(409, "Campaign 调度未启用")
    return get_research_campaign(campaign_id)["schedule"]


@router.post("/campaigns/{campaign_id}/schedule/resume")
def resume_campaign_schedule(campaign_id: str):
    campaign = get_research_campaign(campaign_id)
    schedule = campaign.get("schedule")
    if not schedule:
        raise HTTPException(404, "Campaign 调度尚未配置")
    _validate_campaign_schedule(campaign, schedule["execution_mode"], schedule.get("preferred_run_id"))
    timestamp = utcnow()
    with connect() as db:
        db.execute("""UPDATE campaign_schedules SET enabled=1,paused_at=NULL,next_run_at=?,lease_token=NULL,
          lease_expires_at=NULL,last_error=NULL,updated_at=? WHERE campaign_id=?""", (timestamp, timestamp, campaign_id))
    return get_research_campaign(campaign_id)["schedule"]


def _claim_due_campaign_schedule(campaign_id: str, now: str) -> tuple[dict[str, Any], str] | None:
    token = secrets.token_urlsafe(18)
    lease_expires = (datetime.fromisoformat(now) + timedelta(minutes=5)).isoformat()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        changed = db.execute("""UPDATE campaign_schedules SET lease_token=?,lease_expires_at=?,updated_at=?
          WHERE campaign_id=? AND enabled=1 AND next_run_at<=? AND (lease_expires_at IS NULL OR lease_expires_at<=?)""",
          (token, lease_expires, now, campaign_id, now, now)).rowcount
        if not changed:
            return None
        row = db.execute("SELECT * FROM campaign_schedules WHERE campaign_id=?", (campaign_id,)).fetchone()
    return dict(row), token


def run_due_campaign_schedules(now: str | None = None, campaign_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    now = now or utcnow()
    with connect() as db:
        query = """SELECT s.campaign_id FROM campaign_schedules s JOIN research_campaigns c ON c.id=s.campaign_id
          WHERE s.enabled=1 AND c.status='active' AND s.next_run_at<=? AND (s.lease_expires_at IS NULL OR s.lease_expires_at<=?)"""
        params: list[Any] = [now, now]
        if campaign_id:
            query += " AND s.campaign_id=?"
            params.append(campaign_id)
        due = [row["campaign_id"] for row in db.execute(query + " ORDER BY s.next_run_at LIMIT ?", (*params, limit))]
    outcomes = []
    for due_id in due:
        claimed = _claim_due_campaign_schedule(due_id, now)
        if not claimed:
            continue
        schedule, token = claimed
        outcome: dict[str, Any] = {"campaign_id": due_id, "status": "claimed"}
        try:
            campaign = get_research_campaign(due_id)
            if campaign["iterations_completed"] >= campaign["max_iterations"]:
                raise HTTPException(409, "Campaign 已达到最大研究轮次")
            pending = next((item for item in campaign["iterations"] if item["status"] in {"planned", "executing"}), None)
            if pending:
                outcome.update(status="waiting", iteration_id=pending["id"], reason="previous_iteration_pending")
            else:
                hypothesis = next((item for item in campaign["hypotheses"] if item.get("workflow_id") and item["status"] in {"new", "open_proof_gap", "rejected"}), None)
                if hypothesis:
                    matrix = _build_campaign_matrix(campaign, {hypothesis["workflow_id"]})
                    iteration = _insert_campaign_iteration(campaign, matrix, "scheduled_directed_retest", hypothesis["id"])
                else:
                    iteration = _insert_campaign_iteration(campaign, _build_campaign_matrix(campaign), "scheduled_coverage")
                outcome.update(status="planned", iteration_id=iteration["id"], sequence=iteration["sequence"],
                               hypothesis_id=iteration.get("hypothesis_id"), test_count=len(iteration["tests"]))
                if schedule["execution_mode"] == "read_only_execute":
                    has_mutation = any(item.get("kind") in {"reversible_transition", "reversible_transaction", "cross_workflow_reversible_transaction"} for item in iteration["tests"])
                    if has_mutation:
                        outcome["execution"] = "blocked_reversible_requires_human_confirmation"
                    else:
                        executed = execute_campaign_iteration(due_id, iteration["id"], CampaignExecuteInput(
                            run_id=schedule["preferred_run_id"], max_tests=schedule["max_tests"], confirm_reversible_state_change=False,
                        ))
                        outcome.update(status="completed", execution="read_only", tested=executed["tested"], blocked=executed["blocked"])
            next_run = (datetime.fromisoformat(now) + timedelta(minutes=schedule["cadence_minutes"])).isoformat()
            with connect() as db:
                db.execute("""UPDATE campaign_schedules SET next_run_at=?,last_planned_at=?,lease_token=NULL,
                  lease_expires_at=NULL,last_error=NULL,updated_at=? WHERE campaign_id=? AND lease_token=?""",
                  (next_run, now, utcnow(), due_id, token))
        except Exception as error:
            message = redact(str(error.detail) if isinstance(error, HTTPException) else str(error))
            with connect() as db:
                if outcome.get("iteration_id"):
                    db.execute("UPDATE campaign_iterations SET status='failed',results=?,completed_at=? WHERE id=? AND status IN ('planned','executing')", (
                        dump({"scheduler_error": message[:1000]}), utcnow(), outcome["iteration_id"],
                    ))
                if outcome.get("hypothesis_id"):
                    db.execute("UPDATE research_hypotheses SET status='planned',next_action=?,updated_at=? WHERE id=? AND status='testing'", (
                        "调度执行失败；修复条件后重试", utcnow(), outcome["hypothesis_id"],
                    ))
                db.execute("""UPDATE campaign_schedules SET failure_count=failure_count+1,last_error=?,lease_token=NULL,
                  lease_expires_at=NULL,next_run_at=?,updated_at=? WHERE campaign_id=? AND lease_token=?""", (
                    message[:1000], (datetime.fromisoformat(now) + timedelta(minutes=schedule["cadence_minutes"])).isoformat(), utcnow(), due_id, token,
                ))
            outcome.update(status="failed", error=message)
        outcomes.append(outcome)
    return outcomes


@router.get("/campaign-scheduler/due")
def list_due_campaign_schedules():
    now = utcnow()
    with connect() as db:
        rows = [dict(row) for row in db.execute("""SELECT s.campaign_id,s.next_run_at,s.execution_mode,s.failure_count,s.last_error
          FROM campaign_schedules s JOIN research_campaigns c ON c.id=s.campaign_id
          WHERE s.enabled=1 AND c.status='active' AND s.next_run_at<=? ORDER BY s.next_run_at""", (now,))]
    return {"now": now, "due": rows}


@router.get("/campaign-scheduler/status")
def campaign_scheduler_status():
    with connect() as db:
        enabled = db.execute("SELECT COUNT(*) FROM campaign_schedules WHERE enabled=1").fetchone()[0]
        paused = db.execute("SELECT COUNT(*) FROM campaign_schedules WHERE enabled=0").fetchone()[0]
    return {**CAMPAIGN_SCHEDULER_STATE, "enabled_schedules": enabled, "paused_schedules": paused, "poll_seconds": 30}


@router.post("/campaign-scheduler/tick")
def tick_campaign_scheduler():
    return {"processed": run_due_campaign_schedules()}


@router.post("/campaigns/{campaign_id}/schedule/run-now")
def run_campaign_schedule_now(campaign_id: str):
    campaign = get_research_campaign(campaign_id)
    if not campaign.get("schedule") or not campaign["schedule"]["enabled"]:
        raise HTTPException(409, "Campaign 调度未启用")
    timestamp = utcnow()
    with connect() as db:
        db.execute("UPDATE campaign_schedules SET next_run_at=?,lease_token=NULL,lease_expires_at=NULL,updated_at=? WHERE campaign_id=?", (timestamp, timestamp, campaign_id))
    outcomes = run_due_campaign_schedules(timestamp, campaign_id, 1)
    return outcomes[0] if outcomes else {"campaign_id": campaign_id, "status": "not_claimed"}


def _campaign_hypothesis(campaign_id: str, workflow_id: str, category: str, statement: str, evidence_ids: list[str], next_action: str) -> str:
    fingerprint = hashlib.sha256(f"{workflow_id}:{category}:{' '.join(statement.lower().split())}".encode()).hexdigest()
    timestamp = utcnow()
    with connect() as db:
        row = db.execute("SELECT id,evidence_ids,attempts FROM research_hypotheses WHERE campaign_id=? AND fingerprint=?", (campaign_id, fingerprint)).fetchone()
        if row:
            merged = list(dict.fromkeys(load(row["evidence_ids"], []) + evidence_ids))
            db.execute("UPDATE research_hypotheses SET evidence_ids=?,attempts=?,last_tested_at=?,next_action=?,updated_at=? WHERE id=?", (dump(merged), row["attempts"] + 1, timestamp, next_action, timestamp, row["id"]))
            return row["id"]
        hypothesis_id = uid("hypothesis")
        db.execute("INSERT INTO research_hypotheses VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            hypothesis_id, campaign_id, workflow_id, fingerprint, category, statement, "open_proof_gap", 70,
            dump(evidence_ids), dump([]), 1, timestamp, next_action, timestamp, timestamp,
        ))
    return hypothesis_id


def _campaign_candidates(campaign_id: str, iteration_id: str, run_id: str,
                         results: list[dict[str, Any]]) -> list[str]:
    promotable = {
        "executable_invariant_violation", "workflow_sequence_bypass",
        "concurrency_nondeterminism", "cross_identity_sequence_access",
    }
    created = []
    for result in results:
        hypothesis_ids = result.get("hypothesis_ids") or ([result["hypothesis_id"]] if result.get("hypothesis_id") else [])
        for hypothesis_id in hypothesis_ids:
            with connect() as db:
                hypothesis = db.execute(
                    "SELECT * FROM research_hypotheses WHERE id=? AND campaign_id=?", (hypothesis_id, campaign_id),
                ).fetchone()
                workflow = db.execute(
                    "SELECT * FROM business_workflows WHERE id=? AND campaign_id=?", (result["workflow_id"], campaign_id),
                ).fetchone()
                existing = db.execute(
                    "SELECT candidate_id FROM campaign_candidate_links WHERE iteration_id=? AND hypothesis_id=?",
                    (iteration_id, hypothesis_id),
                ).fetchone()
            if not hypothesis or not workflow or existing or hypothesis["category"] not in promotable or not result.get("observation_id"):
                continue
            steps = load(workflow["steps"], [])
            target = next((step.get("url") for step in steps if step.get("url")), f"workflow:{workflow['id']}")
            candidate = create_candidate(run_id, CandidateInput(
                title=f"业务流程待复验 · {workflow['name']}"[:300],
                category=f"business_logic.{hypothesis['category']}", target=target,
                hypothesis=f"{hypothesis['statement']}。该信号来自 Campaign 受控执行，仍需独立重放、负对照与影响确认。",
                observation_ids=[result["observation_id"]],
            ))
            with connect() as db:
                db.execute("INSERT INTO campaign_candidate_links VALUES(?,?,?,?,?,?,?)", (
                    uid("campaign-link"), campaign_id, iteration_id, workflow["id"],
                    hypothesis_id, candidate["id"], utcnow(),
                ))
            result.setdefault("candidate_ids", []).append(candidate["id"])
            result.setdefault("candidate_id", candidate["id"])
            created.append(candidate["id"])
    return created


def _json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError("invalid_json_pointer")
    value = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            value = value[int(token)]
        elif isinstance(value, dict):
            value = value[token]
        else:
            raise KeyError(token)
    return value


def _render_workflow_template(template: str | None, variables: dict[str, Any], url_mode: bool = False) -> str | None:
    if template is None:
        return None
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise KeyError(name)
        value = str(variables[name])
        return quote(value, safe="") if url_mode else json.dumps(value, ensure_ascii=False)[1:-1]
    return re.sub(r"\{\{([A-Za-z_][A-Za-z0-9_]{0,63})\}\}", replace, template)


def _workflow_step_enabled(step: dict[str, Any], variables: dict[str, Any]) -> tuple[bool, str]:
    variable, operator = step.get("when_variable"), step.get("when_operator")
    if not variable or not operator:
        return True, "unconditional"
    exists = variable in variables
    if operator == "exists":
        return exists, f"{variable}_exists" if exists else f"{variable}_missing"
    if operator == "not_exists":
        return not exists, f"{variable}_missing" if not exists else f"{variable}_exists"
    if not exists:
        return False, f"{variable}_missing"
    equal = variables[variable] == step.get("when_value")
    enabled = equal if operator == "equals" else not equal
    return enabled, f"{variable}_{'matched' if equal else 'different'}"


def _invariant_expected(item: dict[str, Any], variables: dict[str, Any]) -> Any:
    expected = item.get("expected")
    template = item.get("expected_template")
    if template is not None:
        exact = re.fullmatch(r"\{\{([A-Za-z_][A-Za-z0-9_]{0,63})\}\}", template)
        return variables[exact.group(1)] if exact else _render_workflow_template(template, variables)
    return expected


def _invariant_decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or isinstance(value, (dict, list)) or value is None:
        raise ValueError("not_numeric")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("not_finite")
    return number


def _decimal_compare(actual: Decimal, expected: Decimal, operator: str, tolerance: Decimal) -> bool:
    if operator == "eq":
        return abs(actual - expected) <= tolerance
    if operator == "ne":
        return abs(actual - expected) > tolerance
    if operator == "gt":
        return actual > expected + tolerance
    if operator == "gte":
        return actual >= expected - tolerance
    if operator == "lt":
        return actual < expected - tolerance
    if operator == "lte":
        return actual <= expected + tolerance
    raise ValueError("unknown_numeric_operator")


def _project_collection(document: Any, collection_pointer: str, item_pointer: str, filter_pointer: str | None = None, filter_expected: Any = None) -> list[Any]:
    collection = _json_pointer(document, collection_pointer)
    if not isinstance(collection, list) or len(collection) > 1000:
        raise ValueError("collection_projection_requires_bounded_list")
    selected = []
    for item in collection:
        if filter_pointer is not None and _json_pointer(item, filter_pointer) != filter_expected:
            continue
        selected.append(_json_pointer(item, item_pointer))
    return selected


def _evaluate_workflow_invariants(invariants: list[dict[str, Any]], steps: list[dict[str, Any]], variables: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for item in invariants:
        name, kind, step_number = item["name"], item["kind"], int(item["step"])
        result = {"name": name, "kind": kind, "step": step_number, "passed": False, "reason": "not_evaluated"}
        try:
            step = steps[step_number - 1]
            if step.get("skipped"):
                result.update({"passed": True, "reason": "step_skipped_not_applicable"})
                results.append(result)
                continue
            if kind == "status_in":
                result["passed"] = step["status"] in item.get("expected_statuses", [])
            elif kind == "json_exists":
                _json_pointer(step["json"], item["pointer"])
                result["passed"] = True
            elif kind in {"json_equals", "json_not_equals"}:
                actual = _json_pointer(step["json"], item["pointer"])
                expected = _invariant_expected(item, variables)
                result["passed"] = (actual == expected) if kind == "json_equals" else (actual != expected)
            elif kind in {"body_equals_step", "body_differs_step"}:
                other = steps[int(item["other_step"]) - 1]
                equal = step["body_sha256"] == other["body_sha256"]
                result["passed"] = equal if kind == "body_equals_step" else not equal
            elif kind == "json_number_compare":
                actual = _invariant_decimal(_json_pointer(step["json"], item["pointer"]))
                expected = _invariant_decimal(_invariant_expected(item, variables))
                result["passed"] = _decimal_compare(actual, expected, item["operator"], _invariant_decimal(item.get("tolerance", 0)))
            elif kind in {"json_collection_contains", "json_collection_not_contains"}:
                collection = _json_pointer(step["json"], item["pointer"])
                if not isinstance(collection, list):
                    raise ValueError("not_collection")
                contains = _invariant_expected(item, variables) in collection
                result["passed"] = contains if kind == "json_collection_contains" else not contains
            elif kind == "json_collection_unique":
                collection = _json_pointer(step["json"], item["pointer"])
                if not isinstance(collection, list):
                    raise ValueError("not_collection")
                canonical = [json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for value in collection]
                result["passed"] = len(canonical) == len(set(canonical))
            elif kind == "json_collection_size_compare":
                collection = _json_pointer(step["json"], item["pointer"])
                if not isinstance(collection, (list, dict)):
                    raise ValueError("not_collection")
                expected = _invariant_decimal(_invariant_expected(item, variables))
                result["passed"] = _decimal_compare(Decimal(len(collection)), expected, item["operator"], _invariant_decimal(item.get("tolerance", 0)))
            elif kind == "json_numeric_delta_equals":
                other = steps[int(item["other_step"]) - 1]
                actual = _invariant_decimal(_json_pointer(step["json"], item["pointer"]))
                baseline = _invariant_decimal(_json_pointer(other["json"], item["other_pointer"]))
                expected = _invariant_decimal(_invariant_expected(item, variables))
                result["passed"] = abs((actual - baseline) - expected) <= _invariant_decimal(item.get("tolerance", 0))
            elif kind == "json_sum_equals":
                values = _json_pointer(step["json"], item["pointer"])
                if not isinstance(values, list):
                    raise ValueError("not_collection")
                actual = sum((_invariant_decimal(value) for value in values), Decimal(0))
                if item.get("other_step") is not None:
                    other = steps[int(item["other_step"]) - 1]
                    other_value = _json_pointer(other["json"], item["other_pointer"])
                    expected = sum((_invariant_decimal(value) for value in other_value), Decimal(0)) if isinstance(other_value, list) else _invariant_decimal(other_value)
                else:
                    expected = _invariant_decimal(_invariant_expected(item, variables))
                result["passed"] = abs(actual - expected) <= _invariant_decimal(item.get("tolerance", 0))
            elif kind in {"json_project_unique", "json_project_contains", "json_project_not_contains", "json_all_items_equal", "json_any_item_equals"}:
                projected = _project_collection(step["json"], item["pointer"], item["item_pointer"])
                if kind == "json_project_unique":
                    canonical = [json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for value in projected]
                    result["passed"] = len(canonical) == len(set(canonical))
                else:
                    expected = _invariant_expected(item, variables)
                    if kind == "json_project_contains":
                        result["passed"] = expected in projected
                    elif kind == "json_project_not_contains":
                        result["passed"] = expected not in projected
                    elif kind == "json_all_items_equal":
                        result["passed"] = bool(projected) and all(value == expected for value in projected)
                    else:
                        result["passed"] = any(value == expected for value in projected)
            elif kind == "json_filtered_sum_equals":
                projected = _project_collection(step["json"], item["pointer"], item["item_pointer"], item["filter_pointer"], item.get("filter_expected"))
                actual = sum((_invariant_decimal(value) for value in projected), Decimal(0))
                if item.get("other_step") is not None:
                    other = steps[int(item["other_step"]) - 1]
                    expected = _invariant_decimal(_json_pointer(other["json"], item["other_pointer"]))
                else:
                    expected = _invariant_decimal(_invariant_expected(item, variables))
                result["passed"] = abs(actual - expected) <= _invariant_decimal(item.get("tolerance", 0))
            result["reason"] = "assertion_satisfied" if result["passed"] else "assertion_failed"
        except (IndexError, KeyError, ValueError, TypeError, InvalidOperation, json.JSONDecodeError):
            result["reason"] = "assertion_input_unavailable"
        results.append(result)
    return results


def _campaign_test_signature(test: dict[str, Any]) -> str:
    identity_fields = {key: test[key] for key in sorted(test) if "identity_id" in key}
    signature = {
        "kind": test.get("kind"), "workflow_id": test.get("workflow_id"), "step": test.get("step"),
        "target_step": test.get("target_step"), "sequence": test.get("sequence"),
        "url_sha256": hashlib.sha256(str(test.get("url", "")).encode()).hexdigest() if test.get("url") else None,
        "invariant_kinds": sorted(item.get("kind", "") for item in test.get("executable_invariants", [])),
        "identity_dimensions": identity_fields,
    }
    return hashlib.sha256(json.dumps(signature, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _build_campaign_iteration_metrics(db: sqlite3.Connection, campaign_id: str, iteration_id: str, sequence: int,
                                      tests: list[dict[str, Any]], results: list[dict[str, Any]],
                                      hypotheses_before: dict[str, str], observation_count: int,
                                      retested_hypothesis_id: str | None) -> dict[str, Any]:
    signatures = sorted({_campaign_test_signature(test) for test in tests})
    historical: set[str] = set()
    for row in db.execute("SELECT signature_hashes FROM campaign_iteration_metrics WHERE campaign_id=?", (campaign_id,)):
        historical.update(load(row["signature_hashes"], []))
    new_signatures = set(signatures) - historical
    hypotheses_after = {row["id"]: row["status"] for row in db.execute(
        "SELECT id,status FROM research_hypotheses WHERE campaign_id=? AND status!='archived'", (campaign_id,),
    )}
    open_states = {"new", "planned", "testing", "open_proof_gap"}
    open_before = {item_id for item_id, status in hypotheses_before.items() if status in open_states}
    open_after = {item_id for item_id, status in hypotheses_after.items() if status in open_states}
    invariant_rows = [assertion for result in results for assertion in result.get("invariants", [])]
    tested = sum(result.get("status") == "observed" for result in results)
    blocked = len(results) - tested
    metrics = {
        "iteration_id": iteration_id, "campaign_id": campaign_id, "sequence": sequence,
        "planned_tests": len(tests), "tested_tests": tested, "blocked_tests": blocked,
        "test_signature_count": len(signatures), "new_test_signature_count": len(new_signatures),
        "cumulative_test_signature_count": len(historical | set(signatures)), "signature_hashes": signatures,
        "hypotheses_before": len(hypotheses_before), "hypotheses_after": len(hypotheses_after),
        "new_hypothesis_count": len(set(hypotheses_after) - set(hypotheses_before)),
        "retested_hypothesis_id": retested_hypothesis_id,
        "open_hypotheses_before": len(open_before), "open_hypotheses_after": len(open_after),
        "resolved_hypothesis_count": len(open_before - open_after),
        "invariant_passes": sum(bool(item.get("passed")) for item in invariant_rows),
        "invariant_failures": sum(not bool(item.get("passed")) for item in invariant_rows),
        "execution_rate": round(tested / len(results), 4) if results else 0.0,
        "novelty_rate": round(len(new_signatures) / len(signatures), 4) if signatures else 0.0,
        "observation_count": observation_count, "created_at": utcnow(),
    }
    db.execute("""INSERT OR REPLACE INTO campaign_iteration_metrics
      (iteration_id,campaign_id,sequence,planned_tests,tested_tests,blocked_tests,test_signature_count,
       new_test_signature_count,cumulative_test_signature_count,signature_hashes,hypotheses_before,hypotheses_after,
       new_hypothesis_count,retested_hypothesis_id,open_hypotheses_before,open_hypotheses_after,
       resolved_hypothesis_count,invariant_passes,invariant_failures,execution_rate,novelty_rate,
       observation_count,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        metrics["iteration_id"], metrics["campaign_id"], metrics["sequence"], metrics["planned_tests"],
        metrics["tested_tests"], metrics["blocked_tests"], metrics["test_signature_count"],
        metrics["new_test_signature_count"], metrics["cumulative_test_signature_count"], dump(metrics["signature_hashes"]),
        metrics["hypotheses_before"], metrics["hypotheses_after"], metrics["new_hypothesis_count"],
        metrics["retested_hypothesis_id"], metrics["open_hypotheses_before"], metrics["open_hypotheses_after"],
        metrics["resolved_hypothesis_count"], metrics["invariant_passes"], metrics["invariant_failures"],
        metrics["execution_rate"], metrics["novelty_rate"], metrics["observation_count"], metrics["created_at"],
    ))
    return metrics


@router.post("/campaigns/{campaign_id}/iterations/{iteration_id}/execute")
def execute_campaign_iteration(campaign_id: str, iteration_id: str, body: CampaignExecuteInput):
    """Execute bounded probes; mutations require isolated scope and proven compensation."""
    import traditional_runtime
    campaign = get_research_campaign(campaign_id)
    with connect() as db:
        iteration = db.execute("SELECT * FROM campaign_iterations WHERE id=? AND campaign_id=?", (iteration_id, campaign_id)).fetchone()
        run = db.execute("SELECT * FROM analysis_runs WHERE id=?", (body.run_id,)).fetchone()
    if not iteration:
        raise HTTPException(404, "Campaign iteration 不存在")
    if iteration["status"] not in {"planned", "failed"}:
        raise HTTPException(409, "该研究轮次不能重复执行")
    if not run or run["engagement_id"] != campaign["engagement_id"] or run["mode"] != "traditional":
        raise HTTPException(409, "执行 Run 必须属于同一 Traditional 项目")
    if run["status"] not in {"running", "paused", "completed"}:
        raise HTTPException(409, "当前 Run 状态不允许深度 HTTP 研究")
    plan = load(iteration["plan"], {})
    tests = plan.get("tests", [])[:body.max_tests]
    timestamp = utcnow()
    with connect() as db:
        db.execute("UPDATE campaign_iterations SET status='executing',run_id=?,started_at=? WHERE id=?", (body.run_id, timestamp, iteration_id))
        if plan.get("hypothesis_id"):
            db.execute("UPDATE research_hypotheses SET status='testing',updated_at=? WHERE id=? AND campaign_id=?", (timestamp, plan["hypothesis_id"], campaign_id))
    results, observation_ids = [], []
    with connect() as db:
        hypothesis_ids_before = {row["id"]: row["status"] for row in db.execute(
            "SELECT id,status FROM research_hypotheses WHERE campaign_id=? AND status!='archived'", (campaign_id,),
        )}
    workflow_outcomes: dict[str, dict[str, Any]] = {}
    workflow_exports: dict[str, dict[str, Any]] = {}
    engagement = get_engagement(campaign["engagement_id"])
    delay = 1 / max(.001, float(engagement["policy"].get("max_requests_per_second", 1)))
    with connect() as db:
        unresolved_state_change = db.execute("""SELECT id,state FROM state_change_journal
          WHERE engagement_id=? AND state NOT IN ('restored','cancelled') ORDER BY created_at LIMIT 1""", (engagement["id"],)).fetchone()

    def request(test: dict, identity_key: str = "identity_id", include_transient: bool = False) -> dict:
        return traditional_runtime._execute_exchange(body.run_id, traditional_runtime.ExchangeRequestInput(
            url=test["url"], method=test.get("method", "GET"), headers={}, body=test.get("body"),
            identity_id=test.get(identity_key),
        ), f"campaign:{campaign_id}:{test['kind']}", include_transient=include_transient)

    def capture_rollback_probes(step: dict) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        baselines, exchanges = [], []
        for probe_url in step.get("rollback_probe_urls", []):
            exchange = request({"kind": step.get("kind", "rollback_probe"), "url": probe_url, "method": "GET", "identity_id": step["identity_id"]})
            exchanges.append(exchange)
            baselines.append({"url": probe_url, "exchange_id": exchange["id"]})
        return baselines, exchanges

    def verify_rollback_probes(step: dict, baselines: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        checks, exchanges, all_matched = [], [], True
        for baseline_ref in baselines:
            baseline = traditional_runtime._get_exchange(baseline_ref["exchange_id"])
            try:
                restored = request({"kind": step.get("kind", "rollback_probe"), "url": baseline_ref["url"], "method": "GET", "identity_id": step["identity_id"]})
                exchanges.append(restored)
                matched = baseline["response_status"] == restored["response_status"] and baseline["response_sha256"] == restored["response_sha256"]
                restored_id = restored["id"]
            except HTTPException:
                matched, restored_id = False, None
            all_matched = all_matched and matched
            checks.append({
                "url_sha256": hashlib.sha256(baseline_ref["url"].encode()).hexdigest(),
                "baseline_exchange_id": baseline["id"], "restored_exchange_id": restored_id, "matched": matched,
            })
        return checks, exchanges, all_matched

    def imported_variables(test: dict) -> dict[str, Any]:
        imported: dict[str, Any] = {}
        for local_name, source_ref in test.get("import_variables", {}).items():
            source_id, source_name = source_ref.rsplit(".", 1)
            if source_name not in workflow_exports.get(source_id, {}):
                raise HTTPException(409, f"cross_workflow_variable_unavailable:{source_ref}")
            imported[local_name] = workflow_exports[source_id][source_name]
        return imported

    def run_sequence(test: dict, actor_identity_ids: dict[str, str], default_identity_id: str, initial_variables: dict[str, Any] | None = None, freeze_extractions: bool = False, step_order: list[int] | None = None) -> tuple[list[dict[str, Any]], list[str], dict[str, str], dict[str, Any]]:
        variables, sequence_steps, exchange_ids, extraction_hashes = dict(initial_variables or {}), [], [], {}
        ordered = [(position, test["steps"][position - 1]) for position in (step_order or list(range(1, len(test["steps"]) + 1)))]
        for position, step in ordered:
            identity_id = actor_identity_ids.get(step.get("actor_role")) or default_identity_id
            identity = get_identity(identity_id)
            variables.update({"identity_role": identity["role"], "identity_tenant": identity.get("tenant") or ""})
            enabled, condition_reason = _workflow_step_enabled(step, variables)
            if not enabled:
                sequence_steps.append({
                    "step": position, "status": None, "body_sha256": None, "json": None,
                    "skipped": True, "condition": condition_reason, "attempts": 0,
                })
                continue
            attempt_ids, final_exchange, final_json, until_satisfied = [], None, None, None
            for _attempt in range(int(step.get("max_repeats", 1))):
                resolved = {
                    **step, "kind": test["kind"], "identity_id": identity_id,
                    "url": _render_workflow_template(step["url"], variables, True),
                    "body": _render_workflow_template(step.get("body"), variables),
                }
                exchange = request(resolved, include_transient=True)
                transient = exchange.pop("_transient_body", "")
                try:
                    parsed = json.loads(transient)
                except json.JSONDecodeError:
                    parsed = None
                exchange_ids.append(exchange["id"])
                attempt_ids.append(exchange["id"])
                final_exchange, final_json = exchange, parsed
                if step.get("extract"):
                    if parsed is None:
                        raise HTTPException(409, f"step_{position}_response_is_not_json")
                    for variable, pointer in step["extract"].items():
                        try:
                            value = _json_pointer(parsed, pointer)
                        except (KeyError, IndexError, ValueError, TypeError) as error:
                            raise HTTPException(409, f"step_{position}_extraction_failed:{variable}") from error
                        if isinstance(value, (dict, list)) or value is None or len(str(value)) > 1024:
                            raise HTTPException(409, f"step_{position}_extraction_not_scalar:{variable}")
                        if not freeze_extractions or variable not in variables:
                            variables[variable] = value
                        extraction_hashes[variable] = hashlib.sha256(str(variables[variable]).encode()).hexdigest()
                pointer = step.get("repeat_until_pointer")
                if pointer is not None:
                    try:
                        until_satisfied = parsed is not None and _json_pointer(parsed, pointer) == step.get("repeat_until_value")
                    except (KeyError, IndexError, ValueError, TypeError):
                        until_satisfied = False
                    if until_satisfied:
                        break
            sequence_steps.append({
                "step": position, "status": final_exchange["response_status"],
                "body_sha256": final_exchange["response_sha256"], "json": final_json,
                "skipped": False, "condition": condition_reason, "attempts": len(attempt_ids),
                "attempt_exchange_ids": attempt_ids, "repeat_until_pointer": step.get("repeat_until_pointer"),
                "until_satisfied": until_satisfied,
            })
        return sequence_steps, exchange_ids, extraction_hashes, variables

    for test in tests:
        kind, workflow_id = test.get("kind"), test.get("workflow_id")
        dependencies = test.get("depends_on_workflow_ids", [])
        missing_dependencies = [item for item in dependencies if item not in workflow_outcomes]
        failed_dependencies = [item for item in dependencies if workflow_outcomes.get(item, {}).get("status") != "passed"]
        if missing_dependencies or failed_dependencies:
            reason = "workflow_dependency_not_executed" if missing_dependencies else "workflow_dependency_invariants_failed"
            results.append({"kind": kind, "workflow_id": workflow_id, "status": "blocked", "reason": reason,
                            "dependency_workflow_ids": dependencies, "blocked_dependency_ids": missing_dependencies or failed_dependencies})
            continue
        reversible_kinds = {"reversible_transition", "reversible_transaction", "cross_workflow_reversible_transaction"}
        if kind in reversible_kinds and not body.confirm_reversible_state_change:
            results.append({"kind": kind, "workflow_id": workflow_id, "status": "blocked", "reason": "reversible_state_change_requires_per_execution_confirmation"})
            continue
        if kind in reversible_kinds and unresolved_state_change:
            results.append({"kind": kind, "workflow_id": workflow_id, "status": "blocked", "reason": f"pending_state_recovery:{unresolved_state_change['id']}:{unresolved_state_change['state']}"})
            continue
        if test.get("method") and test.get("method") not in {"GET", "HEAD", "OPTIONS"} and kind not in reversible_kinds:
            results.append({"kind": kind, "workflow_id": workflow_id, "status": "blocked", "reason": "state_change_not_enabled_for_campaign_executor"})
            continue
        try:
            if kind == "workflow_sequence":
                imported = imported_variables(test)
                import_hashes = {key: hashlib.sha256(str(value).encode()).hexdigest() for key, value in imported.items()}
                sequence_steps, exchange_ids, extraction_hashes, variables = run_sequence(test, test.get("actor_identity_ids", {}), test["default_identity_id"], imported)
                invariant_results = _evaluate_workflow_invariants(test.get("executable_invariants", []), sequence_steps, variables)
                failed = [item for item in invariant_results if not item["passed"]]
                skipped = [item for item in sequence_steps if item.get("skipped")]
                repeated = [item for item in sequence_steps if item.get("attempts", 0) > 1]
                exhausted = [item for item in repeated if item.get("repeat_until_pointer") is not None and not item.get("until_satisfied")]
                summary = f"多步业务流程完成 {len(sequence_steps)} 步；{len(skipped)} 个分支跳过，{sum(item['attempts'] for item in repeated)} 次循环尝试；机器不变量 {len(invariant_results)-len(failed)}/{len(invariant_results)} 通过"
                observation = record_observation(body.run_id, ObservationInput(
                    observation_type="business_logic.workflow_sequence", subject=f"workflow:{workflow_id}",
                    summary=summary, source_capability="campaign-logic-runner", confidence=.92 if failed else .82,
                    raw_ref=":".join(exchange_ids),
                ))
                observation_ids.append(observation["id"])
                with connect() as db:
                    db.execute("INSERT INTO evidence_v2 VALUES(?,?,?,?,?,?,?,?)", (
                        uid("evidence"), observation["id"], body.run_id, "workflow_invariant_result",
                        dump({"workflow_id": workflow_id, "steps": len(sequence_steps), "invariants": invariant_results,
                              "control_flow": [{"step": item["step"], "skipped": item.get("skipped", False), "attempts": item.get("attempts", 0), "until_satisfied": item.get("until_satisfied")} for item in sequence_steps],
                              "extracted_variable_hashes": extraction_hashes, "imported_variable_hashes": import_hashes,
                              "dependency_workflow_ids": dependencies}),
                        None, "supporting" if failed or exhausted else "neutral", utcnow(),
                    ))
                result = {"kind": kind, "workflow_id": workflow_id, "status": "observed", "exchange_ids": exchange_ids,
                          "observation_id": observation["id"], "invariants": invariant_results,
                          "invariant_failures": len(failed), "failed_invariant_kinds": sorted({item["kind"] for item in failed}),
                          "extracted_variables": sorted(extraction_hashes), "extracted_value_hashes": extraction_hashes,
                          "imported_variables": sorted(import_hashes), "imported_value_hashes": import_hashes,
                          "control_flow": [{"step": item["step"], "skipped": item.get("skipped", False), "condition": item.get("condition"), "attempts": item.get("attempts", 0), "until_satisfied": item.get("until_satisfied")} for item in sequence_steps]}
                hypothesis_ids = []
                if failed:
                    hypothesis_ids.append(_campaign_hypothesis(campaign_id, workflow_id, "executable_invariant_violation", f"{len(failed)} 个业务不变量在多步流程中失败：{', '.join(item['name'] for item in failed)}", [observation["id"]], "使用独立身份与负对照重放失败的不变量"))
                if exhausted:
                    hypothesis_ids.append(_campaign_hypothesis(campaign_id, workflow_id, "bounded_loop_nonconvergence", f"{len(exhausted)} 个只读轮询步骤在声明的有界次数内未收敛", [observation["id"]], "使用新 Run 重复轮询并核对服务端状态机和延迟预算"))
                if hypothesis_ids:
                    result["hypothesis_id"], result["hypothesis_ids"] = hypothesis_ids[0], hypothesis_ids
                workflow_exports[workflow_id] = {key: value for key, value in variables.items() if key not in {"identity_role", "identity_tenant"}}
                workflow_outcomes[workflow_id] = {"status": "passed" if not failed and not exhausted else "failed", "observation_id": observation["id"]}
                result["dependency_workflow_ids"] = dependencies
                results.append(result)
            elif kind == "sequence_violation":
                baseline_steps, baseline_ids, baseline_hashes, baseline_variables = run_sequence(
                    test, test.get("actor_identity_ids", {}), test["default_identity_id"], imported_variables(test),
                )
                carried = {key: value for key, value in baseline_variables.items() if key not in {"identity_role", "identity_tenant"}}
                perturbed_steps, perturbed_ids, _, _ = run_sequence(
                    test, test.get("actor_identity_ids", {}), test["default_identity_id"], carried, True, test["sequence"],
                )
                perturbed_by_step = {item["step"]: item for item in perturbed_steps}
                dependency_checks = []
                for step_number, step in enumerate(test["steps"], start=1):
                    if not step.get("requires_steps"):
                        continue
                    perturbed = perturbed_by_step[step_number]
                    status = perturbed["status"]
                    dependency_checks.append({
                        "step": step_number, "requires_steps": step["requires_steps"], "status": status,
                        "skipped": perturbed.get("skipped", False),
                        "passed": perturbed.get("skipped", False) or status in {401, 403, 404, 409, 422},
                    })
                suspicious = any(not item["passed"] and item["status"] is not None and 200 <= item["status"] < 300 for item in dependency_checks)
                summary = f"顺序扰动执行 {len(perturbed_steps)} 步；{sum(item['passed'] for item in dependency_checks)}/{len(dependency_checks)} 个前置条件被服务端拒绝"
                observation = record_observation(body.run_id, ObservationInput(
                    observation_type="business_logic.sequence_violation", subject=f"workflow:{workflow_id}",
                    summary=summary, source_capability="campaign-logic-runner", confidence=.9 if suspicious else .78,
                    raw_ref=":".join(baseline_ids + perturbed_ids),
                ))
                observation_ids.append(observation["id"])
                result = {"kind": kind, "workflow_id": workflow_id, "status": "observed",
                          "decision": "suspicious_success" if suspicious else "dependency_enforced",
                          "dependency_checks": dependency_checks, "baseline_exchange_ids": baseline_ids,
                          "perturbed_exchange_ids": perturbed_ids, "observation_id": observation["id"],
                          "carried_variables": sorted(carried), "carried_value_hashes": {key: baseline_hashes[key] for key in carried if key in baseline_hashes}}
                if suspicious:
                    result["hypothesis_id"] = _campaign_hypothesis(campaign_id, workflow_id, "workflow_sequence_bypass", "在跳过或颠倒声明的前置步骤后，受依赖步骤仍返回成功响应", [observation["id"]], "加入状态负对照，独立重放受依赖步骤并确认可利用影响")
                results.append(result)
            elif kind == "concurrent_step":
                baseline_steps, baseline_ids, baseline_hashes, baseline_variables = run_sequence(
                    test, test.get("actor_identity_ids", {}), test["default_identity_id"], imported_variables(test),
                )
                step = test["steps"][int(test["target_step"]) - 1]
                identity_id = test.get("actor_identity_ids", {}).get(step.get("actor_role")) or test["default_identity_id"]
                identity = get_identity(identity_id)
                variables = {**baseline_variables, "identity_role": identity["role"], "identity_tenant": identity.get("tenant") or ""}
                resolved = {**step, "kind": kind, "identity_id": identity_id,
                            "url": _render_workflow_template(step["url"], variables, True),
                            "body": _render_workflow_template(step.get("body"), variables)}
                workers = min(int(test.get("replays", 2)), int(engagement["policy"].get("max_concurrency", 2)), 10)
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                    concurrent_results = list(pool.map(lambda _: request(resolved), range(workers)))
                signatures = {(item["response_status"], item["response_sha256"]) for item in concurrent_results}
                stable = len(signatures) == 1
                summary = f"有界并发重放 {workers} 次；响应{'稳定' if stable else '出现分歧'}"
                observation = record_observation(body.run_id, ObservationInput(
                    observation_type="business_logic.concurrent_replay", subject=f"{step['method']} {resolved['url']}",
                    summary=summary, source_capability="campaign-logic-runner", confidence=.88 if not stable else .72,
                    raw_ref=":".join(item["id"] for item in concurrent_results),
                ))
                observation_ids.append(observation["id"])
                result = {"kind": kind, "workflow_id": workflow_id, "status": "observed", "stable": stable,
                          "workers": workers, "response_signature_count": len(signatures),
                          "baseline_exchange_ids": baseline_ids, "exchange_ids": [item["id"] for item in concurrent_results],
                          "observation_id": observation["id"], "carried_variables": sorted(baseline_hashes)}
                if not stable:
                    result["hypothesis_id"] = _campaign_hypothesis(campaign_id, workflow_id, "concurrency_nondeterminism", "同一只读业务步骤的有界并发重放产生不同响应签名，可能存在竞态或不一致读", [observation["id"]], "在隔离环境重复并发批次并加入串行负对照")
                results.append(result)
            elif kind == "cross_identity_sequence":
                source_steps, source_ids, source_hashes, source_variables = run_sequence(
                    test, test.get("source_actor_identity_ids", {}), test["source_default_identity_id"], imported_variables(test),
                )
                carried = {key: value for key, value in source_variables.items() if key not in {"identity_role", "identity_tenant"}}
                replay_steps, replay_ids, replay_hashes, replay_variables = run_sequence(
                    test, test.get("replay_actor_identity_ids", {}), test["replay_default_identity_id"], carried, True,
                )
                source_invariants = _evaluate_workflow_invariants(test.get("executable_invariants", []), source_steps, source_variables)
                replay_invariants = _evaluate_workflow_invariants(test.get("executable_invariants", []), replay_steps, replay_variables)
                dependent_steps = []
                for position, step in enumerate(test["steps"], start=1):
                    sources = [step.get("url", ""), step.get("body") or ""]
                    if any(re.search(r"\{\{(?:" + "|".join(re.escape(key) for key in carried) + r")\}\}", value) for value in sources) if carried else False:
                        dependent_steps.append(position)
                dependent_statuses = [replay_steps[position - 1]["status"] for position in dependent_steps]
                denied = bool(dependent_statuses) and all(status in {401, 403, 404} for status in dependent_statuses)
                suspicious = bool(dependent_statuses) and any(200 <= status < 300 for status in dependent_statuses)
                decision = "denied" if denied else ("suspicious_success" if suspicious else "indeterminate")
                summary = f"跨身份多步重放：携带 {len(carried)} 个源身份变量，依赖步骤决策为 {decision}"
                observation = record_observation(body.run_id, ObservationInput(
                    observation_type="business_logic.cross_identity_sequence", subject=f"workflow:{workflow_id}",
                    summary=summary, source_capability="campaign-logic-runner", confidence=.88 if suspicious else .75,
                    raw_ref=":".join(source_ids + replay_ids),
                ))
                observation_ids.append(observation["id"])
                result = {"kind": kind, "workflow_id": workflow_id, "status": "observed", "decision": decision,
                          "source_exchange_ids": source_ids, "replay_exchange_ids": replay_ids,
                          "source_invariants": source_invariants, "replay_invariants": replay_invariants,
                          "carried_variables": sorted(carried), "carried_value_hashes": {key: source_hashes[key] for key in carried if key in source_hashes},
                          "observation_id": observation["id"]}
                if suspicious:
                    result["hypothesis_id"] = _campaign_hypothesis(campaign_id, workflow_id, "cross_identity_sequence_access", "跨身份重放在携带源身份对象变量时仍获得成功响应，需要独立确认是否越权", [observation["id"]], "对源对象执行两轮非所有者重放并加入拒绝型负对照")
                results.append(result)
            elif kind in {"reversible_transaction", "cross_workflow_reversible_transaction"}:
                transaction_entries, exchanges, primary_error = [], [], None
                # Capture every baseline before the first mutation. This makes the
                # rollback target explicit and prevents a half-observed transaction.
                for step in test["steps"]:
                    snapshot = request({"kind": kind, "url": step["snapshot_url"], "method": "GET", "identity_id": step["identity_id"]})
                    exchanges.append(snapshot)
                    probe_baselines, probe_exchanges = capture_rollback_probes({**step, "kind": kind})
                    exchanges.extend(probe_exchanges)
                    journal_id, journal_time = uid("state-change"), utcnow()
                    with connect() as db:
                        db.execute("""INSERT INTO state_change_journal
                          (id,engagement_id,campaign_id,iteration_id,run_id,workflow_id,step_number,identity_id,state,
                           snapshot_exchange_id,mutation_exchange_id,after_exchange_id,compensation_exchange_id,
                           rollback_exchange_id,error,created_at,updated_at,rollback_probe_baselines)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                            journal_id, engagement["id"], campaign_id, iteration_id, body.run_id,
                            step.get("source_workflow_id", workflow_id),
                            int(step["step"]), step["identity_id"], "snapshot_captured", snapshot["id"], None, None,
                            None, None, None, journal_time, journal_time, dump(probe_baselines),
                        ))
                    transaction_entries.append({"step": step, "journal_id": journal_id, "before": snapshot, "probe_baselines": probe_baselines, "attempted": False})
                for entry in transaction_entries:
                    step, journal_id = entry["step"], entry["journal_id"]
                    try:
                        with connect() as db:
                            db.execute("UPDATE state_change_journal SET state='mutation_attempted',updated_at=? WHERE id=?", (utcnow(), journal_id))
                        entry["attempted"] = True
                        mutation = request({**step, "kind": kind})
                        exchanges.append(mutation)
                        with connect() as db:
                            db.execute("UPDATE state_change_journal SET mutation_exchange_id=?,updated_at=? WHERE id=?", (mutation["id"], utcnow(), journal_id))
                        if mutation["response_status"] >= 400:
                            raise HTTPException(409, f"mutation_http_status:{mutation['response_status']}")
                        after = request({"kind": kind, "url": step["snapshot_url"], "method": "GET", "identity_id": step["identity_id"]})
                        exchanges.append(after)
                        with connect() as db:
                            db.execute("UPDATE state_change_journal SET after_exchange_id=?,updated_at=? WHERE id=?", (after["id"], utcnow(), journal_id))
                    except HTTPException as error:
                        primary_error = f"step_{step['step']}:{error.detail}"
                        break
                rollback_details, rollback_proven = [], True
                # Compensations are a stack: the last attempted business action is
                # always unwound first. Unattempted rows are safely cancelled.
                for entry in reversed(transaction_entries):
                    step, journal_id, before = entry["step"], entry["journal_id"], entry["before"]
                    if not entry["attempted"]:
                        with connect() as db:
                            db.execute("UPDATE state_change_journal SET state='cancelled',updated_at=? WHERE id=?", (utcnow(), journal_id))
                        rollback_details.append({
                            "step": step["step"], "workflow_step": step.get("workflow_step", step["step"]),
                            "source_workflow_id": step.get("source_workflow_id", workflow_id),
                            "state": "cancelled", "rollback_proven": True,
                        })
                        continue
                    compensation_error, restored = None, None
                    try:
                        with connect() as db:
                            db.execute("UPDATE state_change_journal SET state='compensation_attempted',updated_at=? WHERE id=?", (utcnow(), journal_id))
                        compensation = request({**step, "kind": kind, "url": step["compensation_url"], "method": step["compensation_method"], "body": step.get("compensation_body")})
                        exchanges.append(compensation)
                        with connect() as db:
                            db.execute("UPDATE state_change_journal SET compensation_exchange_id=?,updated_at=? WHERE id=?", (compensation["id"], utcnow(), journal_id))
                        if compensation["response_status"] >= 400:
                            raise HTTPException(409, f"compensation_http_status:{compensation['response_status']}")
                        restored = request({"kind": kind, "url": step["snapshot_url"], "method": "GET", "identity_id": step["identity_id"]})
                        exchanges.append(restored)
                        with connect() as db:
                            db.execute("UPDATE state_change_journal SET rollback_exchange_id=?,updated_at=? WHERE id=?", (restored["id"], utcnow(), journal_id))
                    except HTTPException as error:
                        compensation_error = str(error.detail)
                    probe_results, probe_exchanges, probes_restored = verify_rollback_probes({**step, "kind": kind}, entry["probe_baselines"])
                    exchanges.extend(probe_exchanges)
                    snapshot_restored = bool(restored and before["response_status"] == restored["response_status"] and before["response_sha256"] == restored["response_sha256"])
                    step_restored = snapshot_restored and probes_restored
                    rollback_proven = rollback_proven and step_restored
                    with connect() as db:
                        db.execute("UPDATE state_change_journal SET state=?,error=?,updated_at=? WHERE id=?", (
                            "restored" if step_restored else "rollback_failed", compensation_error or (None if step_restored else "rollback_probe_or_snapshot_mismatch"), utcnow(), journal_id,
                        ))
                    rollback_details.append({"step": step["step"], "workflow_step": step.get("workflow_step", step["step"]),
                                             "source_workflow_id": step.get("source_workflow_id", workflow_id),
                                             "state": "restored" if step_restored else "rollback_failed", "rollback_proven": step_restored,
                                             "snapshot_restored": snapshot_restored, "rollback_probe_results": probe_results})
                summary = f"多步骤可逆事务执行 {sum(item['attempted'] for item in transaction_entries)} 个动作；按逆序补偿，{sum(item['rollback_proven'] for item in rollback_details)}/{len(rollback_details)} 个基线闭合"
                if primary_error:
                    summary += f"；主流程异常：{primary_error}"
                observation = record_observation(body.run_id, ObservationInput(
                    observation_type=f"business_logic.{kind}", subject=f"workflow:{workflow_id}",
                    summary=summary, source_capability="campaign-logic-runner", confidence=.92 if rollback_proven else .99,
                    raw_ref=":".join(item["id"] for item in exchanges),
                ))
                observation_ids.append(observation["id"])
                result = {"kind": kind, "workflow_id": workflow_id, "status": "observed" if rollback_proven and not primary_error else ("degraded" if rollback_proven else "rollback_failed"),
                          "workflow_ids": test.get("workflow_ids", [workflow_id]),
                          "rollback_proven": rollback_proven, "rollback_order": [item["step"] for item in rollback_details],
                          "step_results": rollback_details, "journal_ids": [item["journal_id"] for item in transaction_entries],
                          "exchange_ids": [item["id"] for item in exchanges], "observation_id": observation["id"], "primary_error": primary_error}
                if not rollback_proven:
                    result["hypothesis_id"] = _campaign_hypothesis(campaign_id, workflow_id, "transaction_rollback_integrity", "多步骤补偿栈未能完整恢复全部前置状态，隔离环境需要人工恢复", [observation["id"]], "停止状态变更并按回滚账本逆序完成人工恢复")
                results.append(result)
                if not rollback_proven:
                    break
            elif kind == "reversible_transition":
                snapshot = {"kind": kind, "url": test["snapshot_url"], "method": "GET", "identity_id": test["identity_id"]}
                before = request(snapshot)
                probe_baselines, probe_baseline_exchanges = capture_rollback_probes(test)
                journal_id, journal_time = uid("state-change"), utcnow()
                with connect() as db:
                    db.execute("""INSERT INTO state_change_journal
                      (id,engagement_id,campaign_id,iteration_id,run_id,workflow_id,step_number,identity_id,state,
                       snapshot_exchange_id,mutation_exchange_id,after_exchange_id,compensation_exchange_id,
                       rollback_exchange_id,error,created_at,updated_at,rollback_probe_baselines)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                        journal_id, engagement["id"], campaign_id, iteration_id, body.run_id, workflow_id,
                        int(test["step"]), test["identity_id"], "snapshot_captured", before["id"], None, None,
                        None, None, None, journal_time, journal_time, dump(probe_baselines),
                    ))
                exchanges, primary_error, compensation_error, restored_exchange = [before, *probe_baseline_exchanges], None, None, None
                try:
                    with connect() as db:
                        db.execute("UPDATE state_change_journal SET state='mutation_attempted',updated_at=? WHERE id=?", (utcnow(), journal_id))
                    mutation = request(test)
                    exchanges.append(mutation)
                    with connect() as db:
                        db.execute("UPDATE state_change_journal SET mutation_exchange_id=?,updated_at=? WHERE id=?", (mutation["id"], utcnow(), journal_id))
                    after = request(snapshot)
                    exchanges.append(after)
                    with connect() as db:
                        db.execute("UPDATE state_change_journal SET after_exchange_id=?,updated_at=? WHERE id=?", (after["id"], utcnow(), journal_id))
                except HTTPException as error:
                    # A timeout can happen after the server committed the change,
                    # therefore compensation remains mandatory once attempted.
                    primary_error = str(error.detail)
                finally:
                    try:
                        with connect() as db:
                            db.execute("UPDATE state_change_journal SET state='compensation_attempted',updated_at=? WHERE id=?", (utcnow(), journal_id))
                        compensation = request({
                            **test, "url": test["compensation_url"], "method": test["compensation_method"],
                            "body": test.get("compensation_body"),
                        })
                        exchanges.append(compensation)
                        restored_exchange = request(snapshot)
                        exchanges.append(restored_exchange)
                        with connect() as db:
                            db.execute("UPDATE state_change_journal SET compensation_exchange_id=?,rollback_exchange_id=?,updated_at=? WHERE id=?", (compensation["id"], restored_exchange["id"], utcnow(), journal_id))
                    except HTTPException as error:
                        compensation_error = str(error.detail)
                restored = restored_exchange if not compensation_error else None
                probe_results, probe_restored_exchanges, probes_restored = verify_rollback_probes(test, probe_baselines)
                exchanges.extend(probe_restored_exchanges)
                snapshot_restored = bool(restored and before["response_status"] == restored["response_status"] and before["response_sha256"] == restored["response_sha256"])
                rollback_proven = snapshot_restored and probes_restored
                with connect() as db:
                    db.execute("UPDATE state_change_journal SET state=?,error=?,updated_at=? WHERE id=?", (
                        "restored" if rollback_proven else "rollback_failed", primary_error or compensation_error or (None if rollback_proven else "rollback_probe_or_snapshot_mismatch"), utcnow(), journal_id,
                    ))
                summary = f"可逆状态测试已执行；补偿后状态{'恢复' if rollback_proven else '未恢复'}"
                if primary_error:
                    summary += f"；主动作异常：{primary_error}"
                if compensation_error:
                    summary += f"；补偿异常：{compensation_error}"
                observation = record_observation(body.run_id, ObservationInput(
                    observation_type="business_logic.reversible_transition", subject=f"{test['method']} {test['url']}",
                    summary=summary, source_capability="campaign-logic-runner", confidence=.9 if rollback_proven else .98,
                    raw_ref=":".join(item["id"] for item in exchanges),
                ))
                observation_ids.append(observation["id"])
                result_status = "rollback_failed" if not rollback_proven else ("degraded" if primary_error else "observed")
                result = {"kind": kind, "workflow_id": workflow_id, "status": result_status, "rollback_proven": rollback_proven,
                          "exchange_ids": [item["id"] for item in exchanges], "observation_id": observation["id"],
                          "primary_error": primary_error, "compensation_error": compensation_error, "journal_id": journal_id,
                          "snapshot_restored": snapshot_restored, "rollback_probe_results": probe_results}
                if not rollback_proven:
                    result["hypothesis_id"] = _campaign_hypothesis(campaign_id, workflow_id, "rollback_integrity", "补偿动作未能恢复前置状态，隔离环境需要人工清理并审阅业务副作用", [observation["id"]], "停止状态变更测试并人工恢复隔离环境")
                results.append(result)
                if not rollback_proven:
                    break
            elif kind == "cross_identity_replay":
                left = request({**test, "identity_id": test["source_identity_id"]})
                time.sleep(min(delay, 2))
                right = request({**test, "identity_id": test["replay_identity_id"]})
                same = left["response_status"] == right["response_status"] and left["response_sha256"] == right["response_sha256"]
                summary = f"跨身份响应 {'完全相同' if same else '存在差异'}：HTTP {left['response_status']} / {right['response_status']}"
                observation = record_observation(body.run_id, ObservationInput(
                    observation_type="business_logic.cross_identity", subject=f"{test['method']} {test['url']}",
                    summary=summary, source_capability="campaign-logic-runner", confidence=.55,
                    raw_ref=f"{left['id']}:{right['id']}",
                ))
                observation_ids.append(observation["id"])
                hypothesis_id = _campaign_hypothesis(campaign_id, workflow_id, "access_control_differential", f"{summary}；需要依据业务不变量进行独立重放和负对照", [observation["id"]], "使用对象所有者与非所有者执行两轮确定性重放")
                results.append({"kind": kind, "workflow_id": workflow_id, "status": "observed", "same_response": same, "exchange_ids": [left["id"], right["id"]], "observation_id": observation["id"], "hypothesis_id": hypothesis_id})
            elif kind == "duplicate_replay":
                first = request(test)
                time.sleep(min(delay, 2))
                second = request(test)
                stable = first["response_status"] == second["response_status"] and first["response_sha256"] == second["response_sha256"]
                observation = record_observation(body.run_id, ObservationInput(
                    observation_type="business_logic.duplicate_replay", subject=f"{test['method']} {test['url']}",
                    summary=f"重复执行响应 {'稳定' if stable else '发生变化'}", source_capability="campaign-logic-runner", confidence=.65,
                    raw_ref=f"{first['id']}:{second['id']}",
                ))
                observation_ids.append(observation["id"])
                results.append({"kind": kind, "workflow_id": workflow_id, "status": "observed", "stable": stable, "exchange_ids": [first["id"], second["id"]], "observation_id": observation["id"]})
            else:
                exchange = request(test)
                observation = record_observation(body.run_id, ObservationInput(
                    observation_type="business_logic.baseline", subject=f"{test['method']} {test['url']}",
                    summary=f"基线请求返回 HTTP {exchange['response_status']}", source_capability="campaign-logic-runner", confidence=.7,
                    raw_ref=exchange["id"],
                ))
                observation_ids.append(observation["id"])
                results.append({"kind": kind, "workflow_id": workflow_id, "status": "observed", "exchange_ids": [exchange["id"]], "observation_id": observation["id"]})
        except HTTPException as error:
            results.append({"kind": kind, "workflow_id": workflow_id, "status": "blocked", "reason": str(error.detail)})
        if delay:
            time.sleep(min(delay, 2))
    candidate_ids = _campaign_candidates(campaign_id, iteration_id, body.run_id, results)
    tested = sum(item["status"] == "observed" for item in results)
    blocked = len(results) - tested
    coverage_key = f"campaign:{campaign_id}:iteration:{iteration['sequence']}"
    with connect() as db:
        metrics = _build_campaign_iteration_metrics(
            db, campaign_id, iteration_id, int(iteration["sequence"]), tests, results,
            hypothesis_ids_before, len(observation_ids), plan.get("hypothesis_id"),
        )
        db.execute("""INSERT INTO coverage_v2 VALUES(?,?,?,?,?,?,?)
          ON CONFLICT(run_id,surface_key) DO UPDATE SET state=excluded.state,reason=excluded.reason,observation_ids=excluded.observation_ids,updated_at=excluded.updated_at""", (
            uid("coverage"), body.run_id, coverage_key, "tested" if tested else "blocked",
            f"长期业务逻辑轮次：{tested} 已执行，{blocked} 阻塞", dump(observation_ids), utcnow(),
        ))
        db.execute("UPDATE campaign_iterations SET status='completed',results=?,completed_at=? WHERE id=?", (
            dump({"tests": results, "tested": tested, "blocked": blocked, "metrics": {key: value for key, value in metrics.items() if key != "signature_hashes"}}), utcnow(), iteration_id,
        ))
        db.execute("UPDATE research_campaigns SET iterations_completed=iterations_completed+1,updated_at=? WHERE id=?", (utcnow(), campaign_id))
        if plan.get("hypothesis_id"):
            db.execute("""UPDATE research_hypotheses SET status=?,attempts=attempts+1,last_tested_at=?,
              next_action=?,updated_at=? WHERE id=? AND campaign_id=?""", (
                "open_proof_gap" if tested else "planned", utcnow(),
                "审阅本轮差异并执行独立负对照" if tested else "补齐阻塞条件后重新执行",
                utcnow(), plan["hypothesis_id"], campaign_id,
            ))
    add_event(body.run_id, "verification", "campaign.iteration_completed", f"长期研究第 {iteration['sequence']} 轮完成：{tested} 已执行，{blocked} 阻塞", {"campaign_id": campaign_id, "iteration_id": iteration_id})
    return {"campaign_id": campaign_id, "iteration_id": iteration_id, "run_id": body.run_id, "status": "completed", "tested": tested, "blocked": blocked, "results": results, "observation_ids": observation_ids,
            "candidate_ids": candidate_ids,
            "metrics": {key: value for key, value in metrics.items() if key != "signature_hashes"}}


@router.get("/campaigns/{campaign_id}/state-change-journals")
def list_state_change_journals(campaign_id: str):
    get_research_campaign(campaign_id)
    with connect() as db:
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM state_change_journal WHERE campaign_id=? ORDER BY created_at DESC", (campaign_id,),
        )]
    for row in rows:
        baselines = load(row.pop("rollback_probe_baselines", "[]"), [])
        row["rollback_probe_count"] = len(baselines)
    return rows


@router.post("/state-change-journals/{journal_id}/recover")
def recover_state_change(journal_id: str, body: StateChangeRecoveryInput):
    """Run only the declared compensation and prove the baseline was restored."""
    import traditional_runtime
    with connect() as db:
        journal = db.execute("SELECT * FROM state_change_journal WHERE id=?", (journal_id,)).fetchone()
        if not journal:
            raise HTTPException(404, "状态变更日志不存在")
        iteration = db.execute("SELECT plan FROM campaign_iterations WHERE id=?", (journal["iteration_id"],)).fetchone()
    if journal["state"] in {"restored", "cancelled"}:
        return {"id": journal_id, "state": journal["state"], "rollback_proven": journal["state"] == "restored"}
    if not body.confirm_compensation:
        raise HTTPException(409, "恢复操作必须显式确认补偿动作")
    engagement = get_engagement(journal["engagement_id"])
    if not engagement["scope"].get("allow_reversible_state_change") or not engagement["policy"].get("allow_state_change"):
        raise HTTPException(409, "当前冻结 Scope 不允许可逆状态恢复")
    if engagement["scope"].get("environment_class") not in {"local_fixture", "ephemeral_test", "staging_clone"}:
        raise HTTPException(409, "状态恢复只允许隔离测试环境")
    if journal["state"] == "snapshot_captured":
        with connect() as db:
            db.execute("UPDATE state_change_journal SET state='cancelled',updated_at=? WHERE id=?", (utcnow(), journal_id))
        return {"id": journal_id, "state": "cancelled", "rollback_proven": True, "reason": "mutation_was_never_attempted"}
    plan = load(iteration["plan"], {}) if iteration else {}
    test = next((item for item in plan.get("tests", []) if item.get("kind") == "reversible_transition" and item.get("workflow_id") == journal["workflow_id"] and int(item.get("step", 0)) == journal["step_number"]), None)
    if not test:
        transaction = next((item for item in plan.get("tests", []) if item.get("kind") == "reversible_transaction" and item.get("workflow_id") == journal["workflow_id"]), None)
        if transaction:
            test = next((step for step in transaction.get("steps", []) if int(step.get("step", 0)) == journal["step_number"]), None)
    if not test:
        transaction = next((
            item for item in plan.get("tests", [])
            if item.get("kind") == "cross_workflow_reversible_transaction"
            and journal["workflow_id"] in item.get("workflow_ids", [])
        ), None)
        if transaction:
            test = next((
                step for step in transaction.get("steps", [])
                if step.get("source_workflow_id") == journal["workflow_id"]
                and int(step.get("step", 0)) == journal["step_number"]
            ), None)
    if not test:
        raise HTTPException(409, "无法从冻结研究计划恢复补偿定义")
    baseline = traditional_runtime._get_exchange(journal["snapshot_exchange_id"])
    probe_baselines = load(journal["rollback_probe_baselines"], [])
    try:
        with connect() as db:
            db.execute("UPDATE state_change_journal SET state='compensation_attempted',updated_at=? WHERE id=?", (utcnow(), journal_id))
        compensation = traditional_runtime._execute_exchange(journal["run_id"], traditional_runtime.ExchangeRequestInput(
            url=test["compensation_url"], method=test["compensation_method"], headers={},
            body=test.get("compensation_body"), identity_id=journal["identity_id"],
        ), f"campaign-recovery:{journal_id}")
        restored = traditional_runtime._execute_exchange(journal["run_id"], traditional_runtime.ExchangeRequestInput(
            url=test["snapshot_url"], method="GET", headers={}, body=None, identity_id=journal["identity_id"],
        ), f"campaign-recovery:{journal_id}")
        probe_results, probe_exchange_ids, probes_restored = [], [], True
        for probe in probe_baselines:
            probe_baseline = traditional_runtime._get_exchange(probe["exchange_id"])
            probe_restored = traditional_runtime._execute_exchange(journal["run_id"], traditional_runtime.ExchangeRequestInput(
                url=probe["url"], method="GET", headers={}, body=None, identity_id=journal["identity_id"],
            ), f"campaign-recovery:{journal_id}")
            matched = probe_baseline["response_status"] == probe_restored["response_status"] and probe_baseline["response_sha256"] == probe_restored["response_sha256"]
            probes_restored = probes_restored and matched
            probe_exchange_ids.append(probe_restored["id"])
            probe_results.append({"url_sha256": hashlib.sha256(probe["url"].encode()).hexdigest(),
                                  "baseline_exchange_id": probe_baseline["id"], "restored_exchange_id": probe_restored["id"], "matched": matched})
    except HTTPException as error:
        with connect() as db:
            db.execute("UPDATE state_change_journal SET state='rollback_failed',error=?,updated_at=? WHERE id=?", (str(error.detail), utcnow(), journal_id))
        raise HTTPException(409, f"补偿恢复失败：{error.detail}") from error
    snapshot_restored = baseline["response_status"] == restored["response_status"] and baseline["response_sha256"] == restored["response_sha256"]
    rollback_proven = snapshot_restored and probes_restored
    state_value = "restored" if rollback_proven else "rollback_failed"
    timestamp = utcnow()
    with connect() as db:
        db.execute("""UPDATE state_change_journal SET state=?,compensation_exchange_id=?,rollback_exchange_id=?,
          error=?,updated_at=? WHERE id=?""", (state_value, compensation["id"], restored["id"], None if rollback_proven else "rollback_probe_or_snapshot_mismatch", timestamp, journal_id))
    observation = record_observation(journal["run_id"], ObservationInput(
        observation_type="business_logic.state_recovery", subject=f"journal:{journal_id}",
        summary=f"中断状态变更补偿已执行；基线{'已恢复' if rollback_proven else '仍不一致'}",
        source_capability="campaign-recovery", confidence=.98,
        raw_ref=f"{baseline['id']}:{compensation['id']}:{restored['id']}",
    ))
    add_event(journal["run_id"], "verification", "campaign.state_recovery", observation["summary"], {"journal_id": journal_id, "rollback_proven": rollback_proven})
    return {"id": journal_id, "state": state_value, "rollback_proven": rollback_proven, "snapshot_restored": snapshot_restored,
            "rollback_probe_results": probe_results, "observation_id": observation["id"],
            "exchange_ids": [compensation["id"], restored["id"], *probe_exchange_ids]}


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


def validated_program_rules(snapshot: sqlite3.Row | dict[str, Any], engagement: dict[str, Any], impact_category: str) -> dict[str, Any]:
    value = dict(snapshot)
    rules = load(value.get("rules"), {}) if isinstance(value.get("rules"), str) else value.get("rules", {})
    if rules.get("kind") != "program_rules" or rules.get("reviewed") is not True:
        raise HTTPException(409, "需要已审阅的项目规则快照")
    with connect() as db:
        authorization = db.execute(
            "SELECT * FROM program_rule_authorizations WHERE snapshot_id=?", (value["id"],),
        ).fetchone()
        rows = db.execute(
            "SELECT id,rules FROM program_snapshots WHERE engagement_id=? ORDER BY version DESC",
            (value["engagement_id"],),
        ).fetchall()
    latest_rule_id = next((row["id"] for row in rows if load(row["rules"], {}).get("kind") == "program_rules"), None)
    if latest_rule_id != value["id"]:
        raise HTTPException(409, "已有更新的项目规则版本，请审阅并使用最新版本")
    if not authorization or authorization["status"] != "confirmed":
        raise HTTPException(409, "项目规则发生变化，必须完成差异审阅与重新授权")
    try:
        valid_until = datetime.fromisoformat(str(rules["valid_until"]).replace("Z", "+00:00"))
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(409, "项目规则有效期无效") from error
    if valid_until <= datetime.now(timezone.utc):
        raise HTTPException(409, "项目规则已过期，请导入新版本")
    if engagement["normalized_target"] not in rules.get("scope_assets", []):
        raise HTTPException(409, "当前目标不在项目规则快照的资产范围内")
    severity = rules.get("impact_categories", {}).get(impact_category)
    if severity not in {"low", "medium", "high", "critical"}:
        raise HTTPException(409, "所选影响类别不在项目规则范围内")
    if rules.get("poc_policy") != "allowed":
        raise HTTPException(409, "项目规则未明确允许此类 PoC")
    if not rules.get("known_issue_sources") or not rules.get("previous_audit_sources"):
        raise HTTPException(409, "缺少已知问题或历史审计核查来源")
    if rules.get("rules_sha256") != hashlib.sha256(rules.get("rules_text", "").encode()).hexdigest():
        raise HTTPException(409, "项目规则正文与哈希不一致")
    return {**rules, "severity": severity}


def program_rule_diff(previous: dict[str, Any] | None, current: dict[str, Any],
                      previous_source: str | None, current_source: str | None) -> dict[str, Any]:
    if previous is None:
        return {"has_changes": False, "initial_version": True}

    def set_delta(key: str) -> dict[str, list[str]]:
        before, after = set(previous.get(key, [])), set(current.get(key, []))
        return {"added": sorted(after - before), "removed": sorted(before - after)}

    old_impacts, new_impacts = previous.get("impact_categories", {}), current.get("impact_categories", {})
    impact_delta = {
        "added": {key: new_impacts[key] for key in sorted(new_impacts.keys() - old_impacts.keys())},
        "removed": {key: old_impacts[key] for key in sorted(old_impacts.keys() - new_impacts.keys())},
        "changed": [
            {"category": key, "from": old_impacts[key], "to": new_impacts[key]}
            for key in sorted(old_impacts.keys() & new_impacts.keys()) if old_impacts[key] != new_impacts[key]
        ],
    }
    diff = {
        "rules_text_changed": previous.get("rules_sha256") != current.get("rules_sha256"),
        "scope_assets": set_delta("scope_assets"),
        "impact_categories": impact_delta,
        "known_issue_sources": set_delta("known_issue_sources"),
        "previous_audit_sources": set_delta("previous_audit_sources"),
        "poc_policy": None if previous.get("poc_policy") == current.get("poc_policy") else {
            "from": previous.get("poc_policy"), "to": current.get("poc_policy")},
        "valid_until": None if previous.get("valid_until") == current.get("valid_until") else {
            "from": previous.get("valid_until"), "to": current.get("valid_until")},
        "source_uri": None if previous_source == current_source else {"from": previous_source, "to": current_source},
    }
    diff["has_changes"] = any((
        diff["rules_text_changed"], diff["poc_policy"], diff["valid_until"], diff["source_uri"],
        *(delta[direction] for delta in (diff["scope_assets"], diff["known_issue_sources"], diff["previous_audit_sources"])
          for direction in ("added", "removed")),
        impact_delta["added"], impact_delta["removed"], impact_delta["changed"],
    ))
    return diff


def hydrate_program_snapshot(row: sqlite3.Row | dict[str, Any], authorization: sqlite3.Row | dict[str, Any] | None,
                             latest_rule_id: str | None) -> dict[str, Any]:
    value = dict(row)
    value["rules"] = load(value["rules"], {}) if isinstance(value["rules"], str) else value["rules"]
    auth = dict(authorization) if authorization else None
    value.update({
        "authorization_status": auth["status"] if auth else "not_applicable",
        "previous_snapshot_id": auth["previous_snapshot_id"] if auth else None,
        "diff": load(auth["diff"], {}) if auth else None,
        "confirmed_at": auth["confirmed_at"] if auth else None,
        "authorization_note": auth["note"] if auth else None,
        "is_latest_program_rules": value["id"] == latest_rule_id,
    })
    return value


@router.post("/program-rules/import", status_code=201)
def import_program_rules(body: ProgramRulesImportInput):
    engagement = get_engagement(body.engagement_id)
    if engagement["mode"] != "web3":
        raise HTTPException(409, "项目规则导入仅用于 Web3 Engagement")
    try:
        valid_until = datetime.fromisoformat(body.valid_until.replace("Z", "+00:00"))
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise HTTPException(422, "valid_until 必须是 ISO 8601 时间") from error
    if valid_until <= datetime.now(timezone.utc):
        raise HTTPException(422, "不能导入已过期的项目规则")
    assets = list(dict.fromkeys(item.strip() for item in body.scope_assets if item.strip()))
    if engagement["normalized_target"] not in assets:
        raise HTTPException(422, "规则资产列表必须包含当前项目目标")
    timestamp = utcnow()
    rules = {
        "kind": "program_rules", "reviewed": True, "reviewed_at": timestamp,
        "valid_until": valid_until.isoformat(), "scope_assets": assets,
        "impact_categories": body.impact_categories,
        "known_issue_sources": [item.strip() for item in body.known_issue_sources if item.strip()],
        "previous_audit_sources": [item.strip() for item in body.previous_audit_sources if item.strip()],
        "poc_policy": body.poc_policy, "rules_text": body.rules_text,
        "rules_sha256": hashlib.sha256(body.rules_text.encode()).hexdigest(),
    }
    if not rules["known_issue_sources"] or not rules["previous_audit_sources"]:
        raise HTTPException(422, "必须记录已知问题与历史审计的核查来源")
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM program_snapshots WHERE engagement_id=? ORDER BY version DESC", (body.engagement_id,),
        ).fetchall()
        previous_row = next((row for row in rows if load(row["rules"], {}).get("kind") == "program_rules"), None)
        previous_rules = load(previous_row["rules"], {}) if previous_row else None
        diff = program_rule_diff(previous_rules, rules, previous_row["source_uri"] if previous_row else None, body.source_uri)
        status = "pending_reauthorization" if diff["has_changes"] else "confirmed"
        version = (rows[0]["version"] + 1) if rows else 1
        snapshot_id = uid("program")
        db.execute("INSERT INTO program_snapshots VALUES(?,?,?,?,?,?,?)", (
            snapshot_id, body.engagement_id, version, body.platform, dump(rules), body.source_uri, timestamp,
        ))
        db.execute("INSERT INTO program_rule_authorizations VALUES(?,?,?,?,?,?,?,?,?)", (
            snapshot_id, body.engagement_id, status, previous_row["id"] if previous_row else None,
            dump(diff), timestamp if status == "confirmed" else None,
            "首次规则导入已确认" if previous_row is None else "规则内容未变化" if status == "confirmed" else "等待人工审阅差异",
            timestamp, timestamp,
        ))
    return {"id": snapshot_id, "version": version, **body.model_dump(),
            "authorization_status": status, "previous_snapshot_id": previous_row["id"] if previous_row else None,
            "diff": diff}


@router.post("/program-snapshots/{snapshot_id}/confirm-authorization")
def confirm_program_rule_authorization(snapshot_id: str, body: ProgramRuleAuthorizationInput):
    timestamp = utcnow()
    with connect() as db:
        snapshot = db.execute("SELECT * FROM program_snapshots WHERE id=?", (snapshot_id,)).fetchone()
        if not snapshot or load(snapshot["rules"], {}).get("kind") != "program_rules":
            raise HTTPException(404, "项目规则快照不存在")
        authorization = db.execute(
            "SELECT * FROM program_rule_authorizations WHERE snapshot_id=?", (snapshot_id,),
        ).fetchone()
        if not authorization:
            raise HTTPException(409, "规则快照缺少授权记录")
        rows = db.execute(
            "SELECT id,rules FROM program_snapshots WHERE engagement_id=? ORDER BY version DESC",
            (snapshot["engagement_id"],),
        ).fetchall()
        latest_rule_id = next((row["id"] for row in rows if load(row["rules"], {}).get("kind") == "program_rules"), None)
        if snapshot_id != latest_rule_id:
            raise HTTPException(409, "只能授权最新的项目规则版本")
        rules = load(snapshot["rules"], {})
        try:
            valid_until = datetime.fromisoformat(str(rules["valid_until"]).replace("Z", "+00:00"))
            if valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(409, "项目规则有效期无效") from error
        if valid_until <= datetime.now(timezone.utc):
            raise HTTPException(409, "项目规则已过期，请导入新版本")
        engagement = get_engagement(snapshot["engagement_id"])
        if engagement["normalized_target"] not in rules.get("scope_assets", []):
            raise HTTPException(409, "当前目标已不在规则资产范围内")
        db.execute("""UPDATE program_rule_authorizations
            SET status='confirmed',confirmed_at=?,note=?,updated_at=? WHERE snapshot_id=?""",
            (timestamp, body.note.strip(), timestamp, snapshot_id))
    return {"snapshot_id": snapshot_id, "authorization_status": "confirmed",
            "confirmed_at": timestamp, "note": body.note.strip()}


@router.get("/engagements/{engagement_id}/program-snapshots")
def list_program_snapshots(engagement_id: str):
    engagement = get_engagement(engagement_id)
    if engagement["mode"] != "web3":
        raise HTTPException(409, "ProgramSnapshot 仅用于 Web3 Engagement")
    with connect() as db:
        rows = db.execute("SELECT * FROM program_snapshots WHERE engagement_id=? ORDER BY version DESC", (engagement_id,)).fetchall()
        authorizations = {row["snapshot_id"]: row for row in db.execute(
            "SELECT * FROM program_rule_authorizations WHERE engagement_id=?", (engagement_id,),
        ).fetchall()}
    latest_rule_id = next((row["id"] for row in rows if load(row["rules"], {}).get("kind") == "program_rules"), None)
    result = [hydrate_program_snapshot(row, authorizations.get(row["id"]), latest_rule_id) for row in rows]
    return redact_structure(result)


@router.get("/program-snapshots/{snapshot_id}")
def get_program_snapshot(snapshot_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM program_snapshots WHERE id=?", (snapshot_id,)).fetchone()
        authorization = db.execute(
            "SELECT * FROM program_rule_authorizations WHERE snapshot_id=?", (snapshot_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "ProgramSnapshot 不存在")
    with connect() as db:
        rows = db.execute(
            "SELECT id,rules FROM program_snapshots WHERE engagement_id=? ORDER BY version DESC", (row["engagement_id"],),
        ).fetchall()
    latest_rule_id = next((item["id"] for item in rows if load(item["rules"], {}).get("kind") == "program_rules"), None)
    return hydrate_program_snapshot(row, authorization, latest_rule_id)


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
    if not target_allowed and engagement["mode"] == "traditional" and engagement.get("target_type") == "cidr":
        candidate_host = urlparse(body.target if "://" in body.target else f"https://{body.target}").hostname
        try:
            candidate_ip = ipaddress.ip_address(candidate_host or "")
            target_allowed = any(candidate_ip in ipaddress.ip_network(item, strict=False)
                                 for item in engagement["scope"].get("cidr_ranges", []))
        except ValueError:
            target_allowed = False
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
        cleared = db.execute("SELECT value FROM app_metadata WHERE key=?", (f"coverage_hidden_before:{run_id}",)).fetchone()
        cleared_at = cleared["value"] if cleared else None
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM coverage_v2 WHERE run_id=? AND (? IS NULL OR updated_at>?) ORDER BY updated_at DESC",
            (run_id, cleared_at, cleared_at),
        )]
        grave = [dict(row) for row in db.execute("""SELECT g.* FROM graveyard g JOIN analysis_runs r ON r.engagement_id=g.engagement_id WHERE r.id=?""", (run_id,))]
    for row in rows:
        row["observation_ids"] = load(row["observation_ids"], [])
    for row in grave:
        row["counterevidence_ids"] = load(row["counterevidence_ids"], [])
    return {"coverage": rows, "graveyard": grave}


@router.delete("/runs/{run_id}/coverage-display")
def clear_run_coverage_display(run_id: str, body: MaintenanceConfirmInput):
    """Clear the visible ledger while preserving its source records and compiled report."""
    if body.confirmation != "CLEAR_COVERAGE_DISPLAY":
        raise HTTPException(422, "覆盖记录清理确认值无效")
    get_run(run_id)
    timestamp = utcnow()
    with connect() as db:
        count = db.execute("SELECT COUNT(*) FROM coverage_v2 WHERE run_id=?", (run_id,)).fetchone()[0]
        db.execute("INSERT OR REPLACE INTO app_metadata VALUES(?,?,?)", (f"coverage_hidden_before:{run_id}", timestamp, timestamp))
    return {"run_id": run_id, "hidden": count, "evidence_preserved": True, "report_preserved": True}


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
