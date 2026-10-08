"""Durable V5 research-group and agent-task orchestration.

This module schedules declarative work only.  It never invokes a target tool,
shell command, or network client; execution belongs to separately registered
runners with explicit capabilities.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field


router = APIRouter(prefix="/api/v1/orchestration", tags=["V5 Orchestration"])
runner_router = APIRouter(prefix="/api/v1/runners", tags=["V5 Runners"])


def _core():
    import final_core
    return final_core


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _emit(db: sqlite3.Connection, campaign_id: str | None, entity_id: str,
          event_type: str, payload: dict[str, Any] | None = None) -> None:
    db.execute(
        "INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) "
        "VALUES(?,?,?,?,?,?)",
        ("orchestration", campaign_id, entity_id, event_type, _dump(payload or {}), _now()),
    )


def _campaign(db: sqlite3.Connection, campaign_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM research_campaigns WHERE id=?", (campaign_id,)).fetchone()
    if not row:
        raise HTTPException(404, "research campaign not found")
    return row


def _validate_run(db: sqlite3.Connection, campaign: sqlite3.Row, run_id: str | None) -> None:
    if not run_id:
        return
    run = db.execute("SELECT engagement_id FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
    if not run or run["engagement_id"] != campaign["engagement_id"]:
        raise HTTPException(409, "run does not belong to the campaign engagement")


def _group_value(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["budget"] = _load(value.pop("budget_json"), {})
    return value


def _task_value(row: sqlite3.Row, usage: sqlite3.Row | None = None, model_time=None) -> dict[str, Any]:
    value = dict(row)
    for name in ("context_capsule", "tool_grants", "route_requirement", "budget"):
        value[name] = _load(value.pop(f"{name}_json"), {} if name != "tool_grants" else [])
    value["result"] = _load(value.pop("result_json"), None)
    value["error"] = _load(value.pop("error_json"), None)
    value["usage"] = dict(usage) if usage else {
        "input_tokens": 0, "output_tokens": 0, "cost_micros": 0, "runtime_ms": 0,
    }
    if value["usage"]:
        value["usage"].pop("task_id", None)
        value["usage"].pop("updated_at", None)
    if model_time is not None:
        value['usage']['runtime_ms'] = max(value['usage']['runtime_ms'], model_time['known_ms'])
        for field in ('input_tokens', 'output_tokens', 'cost_micros'):
            value['usage'][field] = max(value['usage'][field], model_time[field])
        value['model_runtime'] = {**model_time, 'charged_ms': max(
            value['usage']['runtime_ms'], model_time['known_ms'] + model_time['held_ms'])}
    return value


def _model_times(db, task_ids):
    if not task_ids:
        return {}
    return {row['task_id']: {field: row[field] for field in
            ('known_ms', 'held_ms', 'input_tokens', 'output_tokens', 'cost_micros')} for row in db.execute(
        "SELECT c.task_id,COALESCE(SUM(t.runtime_ms),0)+COALESCE(MAX(t.legacy_runtime_ms),0) known_ms,"
        "COALESCE(SUM(CASE WHEN t.call_id IS NULL THEN c.max_runtime_ms ELSE 0 END),0) held_ms,"
        "COALESCE(SUM(u.input_tokens),0) input_tokens,COALESCE(SUM(u.output_tokens),0) output_tokens,"
        "COALESCE(SUM(u.cost_micros),0) cost_micros "
        "FROM runtime_calls c LEFT JOIN runtime_call_timings t ON t.call_id=c.id "
        "LEFT JOIN runtime_usage u ON u.id=c.usage_id "
        f"WHERE c.task_id IN ({','.join('?' for _ in task_ids)}) AND c.state!='released' GROUP BY c.task_id", task_ids)}


def _runner_value(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["capabilities"] = _load(value.pop("capabilities_json"), [])
    value["labels"] = _load(value.pop("labels_json"), {})
    value["metadata"] = _load(value.pop("metadata_json"), {})
    return value


def _sync_runner_jobs(db: sqlite3.Connection, runner_id: str | None = None) -> None:
    clause, params = (" WHERE id=?", (runner_id,)) if runner_id else ("", ())
    db.execute(
        "UPDATE runner_registry_v5 SET active_jobs=(SELECT COUNT(*) FROM agent_tasks "
        "WHERE lease_owner=runner_registry_v5.id AND status IN ('leased','running'))" + clause,
        params,
    )


def recover_expired_leases(database: Any | None = None) -> dict[str, int]:
    """Requeue expired work transactionally, or fail it after the last attempt."""
    f = _core()
    connection = sqlite3.connect(database) if database is not None else f.connect()
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        expired = connection.execute(
            "SELECT * FROM agent_tasks WHERE status IN ('leased','running') "
            "AND lease_expires_at IS NOT NULL AND lease_expires_at<=?", (_now(),),
        ).fetchall()
        counts = {"requeued": 0, "failed": 0}
        for row in expired:
            status = "queued" if row["attempt"] < row["max_attempts"] else "failed"
            error = None if status == "queued" else _dump({"code": "lease_exhausted", "message": "lease expired after final attempt"})
            connection.execute(
                "UPDATE agent_tasks SET status=?,lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,"
                "error_json=?,updated_at=? WHERE id=? AND status IN ('leased','running')",
                (status, error, _now(), row["id"]),
            )
            counts["requeued" if status == "queued" else "failed"] += 1
            _emit(connection, row["campaign_id"], row["id"], f"task.{status}", {"reason": "lease_expired"})
        _sync_runner_jobs(connection)
        connection.commit()
        return counts
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


class GroupCreate(BaseModel):
    campaign_id: str = Field(min_length=1, max_length=200)
    run_id: str | None = Field(default=None, max_length=200)
    parent_group_id: str | None = Field(default=None, max_length=200)
    role: str = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=10_000)
    strategy: str = Field(default="coordinator", min_length=1, max_length=100)
    runtime_profile_id: str | None = Field(default=None, max_length=200)
    budget: dict[str, int] = Field(default_factory=dict)


class TeamRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["researcher", "explorer", "specialist"]
    count: int = Field(ge=1, le=100, strict=True)


class TeamPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    campaign_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=8, max_length=2000)
    roles: list[TeamRole] = Field(min_length=1, max_length=3)
    max_concurrency: int = Field(ge=1, le=32, strict=True)
    max_cost_micros: int = Field(ge=1, le=1_000_000_000, strict=True)
    allow_cloud_context: bool = Field(default=False, strict=True)
    max_tokens_per_task: int = Field(default=8000, strict=True, ge=1, le=100000)
    max_runtime_ms_per_task: int = Field(default=45000, strict=True, ge=1, le=45000)
    runtime_profile_id: str | None = Field(default=None, max_length=200)


class TeamCommit(TeamPlan):
    preview_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=200)


def _team_plan(db: sqlite3.Connection, body: TeamPlan) -> dict[str, Any]:
    campaign = _campaign(db, body.campaign_id)
    if campaign["status"] != "active":
        raise HTTPException(409, "research campaign is not active")
    authority = db.execute(
        "SELECT e.status,e.current_scope_snapshot_id,e.current_policy_id,s.confirmed_at "
        "FROM engagements_v2 e JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id "
        "WHERE e.id=?", (campaign["engagement_id"],),
    ).fetchone()
    if not authority or authority["status"] != "ready" or not authority["confirmed_at"]:
        raise HTTPException(409, "research project scope is not confirmed")
    run = db.execute(
        "SELECT engagement_id,scope_snapshot_id,policy_id,status,synthetic "
        "FROM analysis_runs WHERE id=?", (body.run_id,),
    ).fetchone()
    if (not run or run["engagement_id"] != campaign["engagement_id"]
            or run["scope_snapshot_id"] != authority["current_scope_snapshot_id"]
            or run["policy_id"] != authority["current_policy_id"]
            or run["status"] not in {"queued", "running", "paused", "completed"} or run["synthetic"]):
        raise HTTPException(409, "run is not usable under the current confirmed scope and policy")
    if body.runtime_profile_id and not db.execute(
        "SELECT 1 FROM runtime_profiles WHERE id=?", (body.runtime_profile_id,),
    ).fetchone():
        raise HTTPException(404, "runtime profile not found")
    roles = [item.model_dump() for item in body.roles]
    if len({item["role"] for item in roles}) != len(roles):
        raise HTTPException(422, "team roles must be unique")
    count = sum(item["count"] for item in roles)
    if count > 100 or body.max_concurrency > count or body.max_cost_micros < count:
        raise HTTPException(422, "team size, concurrency or cost budget is invalid")
    from v5_workers import _node_digest
    input_rows = db.execute("SELECT * FROM research_nodes WHERE campaign_id=? "
                            "AND node_type IN ('observation','evidence','counterevidence','claim') "
                            "AND status NOT IN ('archived','retired','invalidated') "
                            "AND source_type!='team_research' "
                            "ORDER BY (node_type='counterevidence') DESC,created_at DESC,id LIMIT 20",
                            (body.campaign_id,)).fetchall()
    from v5_runtime import _contains_sensitive_key
    cloud_eligible = all(_load(row['attributes_json'], {}).get('sensitivity') == 'public'
                         and not _contains_sensitive_key(_load(row['attributes_json'], {})) for row in input_rows)
    if body.allow_cloud_context and (not body.runtime_profile_id or not cloud_eligible):
        raise HTTPException(409, "cloud context requires a selected profile and explicitly public graph inputs")
    plan = {
        "cloud_context_approved": body.allow_cloud_context,
        "research_input_hashes": {row["id"]: _node_digest(row) for row in input_rows},
        "campaign_id": body.campaign_id, "run_id": body.run_id,
        "engagement_id": campaign["engagement_id"],
        "scope_snapshot_id": authority["current_scope_snapshot_id"],
        "policy_id": authority["current_policy_id"],
        "objective": body.objective.strip(), "roles": roles, "task_count": count,
        "max_concurrency": body.max_concurrency,
        "max_cost_micros": body.max_cost_micros,
        "max_tokens_per_task": body.max_tokens_per_task,
        "max_runtime_ms_per_task": body.max_runtime_ms_per_task,
        "runtime_profile_id": body.runtime_profile_id,
        "execution": "queued_until_explicit_local_research_start",
    }
    if len(plan["objective"]) < 8:
        raise HTTPException(422, "team objective is too short")
    plan["preview_hash"] = hashlib.sha256(_dump(plan).encode()).hexdigest()
    return plan


@router.post("/groups/team/preview")
def preview_team(body: TeamPlan):
    with _core().connect() as db:
        return _team_plan(db, body)


@router.post("/groups/team", status_code=201)
def create_team(body: TeamCommit):
    f, now = _core(), _now()
    with f.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        plan = _team_plan(db, body)
        if body.preview_hash != plan["preview_hash"]:
            raise HTTPException(409, "team preview is stale; review the current plan")
        marker = f"v5-team:{body.idempotency_key}"
        existing = db.execute("SELECT value FROM app_metadata WHERE key=?", (marker,)).fetchone()
        if existing:
            saved = _load(existing["value"], {})
            if saved.get("preview_hash") != plan["preview_hash"]:
                raise HTTPException(409, "idempotency key was used for another team plan")
            group_id = saved["group_id"]
            group = db.execute("SELECT * FROM research_groups WHERE id=?", (group_id,)).fetchone()
            tasks = db.execute("SELECT * FROM agent_tasks WHERE group_id=? ORDER BY created_at,id",
                               (group_id,)).fetchall()
            return {"group": _group_value(group), "tasks": [_task_value(row) for row in tasks],
                    "deduplicated": True, "execution": plan["execution"]}
        group_id = _uid("rgroup")
        budget = {"max_tasks": plan["task_count"], "max_concurrency": plan["max_concurrency"],
                  "max_cost_micros": plan["max_cost_micros"]}
        db.execute(
            "INSERT INTO research_groups VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (group_id, body.campaign_id, body.run_id, None, "coordinator",
             plan["objective"], "bounded_research", body.runtime_profile_id,
             _dump(budget), "active", now, now),
        )
        remaining = body.max_cost_micros
        task_rows = []
        for role in body.roles:
            for index in range(role.count):
                task_id = _uid("atask")
                remaining_tasks = plan["task_count"] - len(task_rows)
                task_cost = remaining // remaining_tasks
                remaining -= task_cost
                capsule = {"team_plan": True, "scope_snapshot_id": plan["scope_snapshot_id"],
                           "policy_id": plan["policy_id"], "preview_hash": plan["preview_hash"],
                           "role_index": index + 1,
                           "research_input_hashes": plan["research_input_hashes"],
                           "cloud_context_approved": plan["cloud_context_approved"],
                           "sensitivity": "public" if plan["cloud_context_approved"] else "secret"}
                db.execute(
                    "INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (task_id, body.campaign_id, body.run_id, group_id, role.role,
                     f"{plan['objective']} · {role.role} {index + 1}",
                     _dump(capsule), _dump([]),
                     _dump({"kind": "research-worker"}), _dump({"max_cost_micros": task_cost, "max_tokens": body.max_tokens_per_task, "max_runtime_ms": body.max_runtime_ms_per_task}),
                     0, "queued", 0, 2, f"team:{body.idempotency_key}:{role.role}:{index + 1}",
                     None, None, None, None, None, now, now),
                )
                _emit(db, body.campaign_id, task_id, "task.created", {"status": "queued"})
                task_rows.append(task_id)
        _emit(db, body.campaign_id, group_id, "team.created",
              {"task_count": plan["task_count"], "preview_hash": plan["preview_hash"]})
        db.execute("INSERT INTO app_metadata VALUES(?,?,?)",
                   (marker, _dump({"preview_hash": plan["preview_hash"], "group_id": group_id}), now))
        group = db.execute("SELECT * FROM research_groups WHERE id=?", (group_id,)).fetchone()
        tasks = db.execute("SELECT * FROM agent_tasks WHERE group_id=? ORDER BY created_at,id",
                           (group_id,)).fetchall()
    return {"group": _group_value(group), "tasks": [_task_value(row) for row in tasks],
            "deduplicated": False, "execution": plan["execution"]}


class TaskCreate(BaseModel):
    campaign_id: str = Field(min_length=1, max_length=200)
    run_id: str | None = Field(default=None, max_length=200)
    group_id: str | None = Field(default=None, max_length=200)
    role: str = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=20_000)
    context_capsule: dict[str, Any] = Field(default_factory=dict)
    tool_grants: list[str] = Field(default_factory=list, max_length=100)
    route_requirement: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, int] = Field(default_factory=dict)
    priority: float = Field(default=0, ge=-1_000_000, le=1_000_000)
    max_attempts: int = Field(default=2, ge=1, le=20)
    idempotency_key: str = Field(min_length=1, max_length=200)


class RunnerRegistration(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="worker", min_length=1, max_length=100)
    capabilities: list[str] = Field(default_factory=list, max_length=200)
    labels: dict[str, str] = Field(default_factory=dict)
    max_concurrency: int = Field(default=1, ge=1, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LeaseRequest(BaseModel):
    runner_id: str = Field(min_length=1, max_length=200)
    lease_seconds: int = Field(default=60, ge=5, le=3600)


class HeartbeatRequest(BaseModel):
    runner_id: str = Field(min_length=1, max_length=200)
    lease_attempt: int | None = Field(default=None, ge=1)
    lease_seconds: int = Field(default=60, ge=5, le=3600)


class CheckpointRequest(BaseModel):
    runner_id: str = Field(min_length=1, max_length=200)
    lease_attempt: int | None = Field(default=None, ge=1)
    checkpoint: dict[str, Any]


class Usage(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_micros: int = Field(default=0, ge=0)
    runtime_ms: int = Field(default=0, ge=0)


class CompletionRequest(BaseModel):
    runner_id: str = Field(min_length=1, max_length=200)
    lease_attempt: int | None = Field(default=None, ge=1)
    outcome: Literal["succeeded", "failed"]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    usage: Usage = Field(default_factory=Usage)


def _validate_budget(budget: dict[str, int], allowed: set[str]) -> None:
    unknown = set(budget) - allowed
    if unknown or any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in budget.values()):
        raise HTTPException(422, f"invalid budget keys or values: {sorted(unknown)}")


def _validate_task_constraints(body: TaskCreate) -> None:
    if len(set(body.tool_grants)) != len(body.tool_grants) or any(
        not isinstance(item, str) or not item or len(item) > 100 for item in body.tool_grants
    ):
        raise HTTPException(422, "tool_grants must contain unique, non-empty capability ids")
    requirement = body.route_requirement
    if set(requirement) - {"kind", "labels"}:
        raise HTTPException(422, "route_requirement supports only kind and labels")
    if "kind" in requirement and (not isinstance(requirement["kind"], str) or not requirement["kind"]):
        raise HTTPException(422, "route kind must be a non-empty string")
    labels = requirement.get("labels", {})
    if not isinstance(labels, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in labels.items()
    ):
        raise HTTPException(422, "route labels must be string pairs")


@router.post("/groups", status_code=201)
def create_group(body: GroupCreate):
    _validate_budget(body.budget, {"max_tasks", "max_cost_micros", "max_concurrency"})
    f, group_id, now = _core(), _uid("rgroup"), _now()
    with f.connect() as db:
        current_campaign = _campaign(db, body.campaign_id)
        _validate_run(db, current_campaign, body.run_id)
        if body.runtime_profile_id and not db.execute(
            "SELECT 1 FROM runtime_profiles WHERE id=?", (body.runtime_profile_id,),
        ).fetchone():
            raise HTTPException(404, "runtime profile not found")
        if body.parent_group_id:
            parent = db.execute("SELECT campaign_id FROM research_groups WHERE id=?", (body.parent_group_id,)).fetchone()
            if not parent or parent["campaign_id"] != body.campaign_id:
                raise HTTPException(409, "parent group must belong to the same campaign")
        db.execute(
            "INSERT INTO research_groups VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (group_id, body.campaign_id, body.run_id, body.parent_group_id, body.role,
             body.objective, body.strategy, body.runtime_profile_id, _dump(body.budget),
             "active", now, now),
        )
        _emit(db, body.campaign_id, group_id, "group.created", {"role": body.role})
    return get_group(group_id)


@router.get("/groups/{group_id}")
def get_group(group_id: str):
    f = _core()
    with f.connect() as db:
        row = db.execute("SELECT * FROM research_groups WHERE id=?", (group_id,)).fetchone()
        if row:
            totals = db.execute(
                "SELECT COUNT(t.id) task_count,COALESCE(SUM(u.cost_micros),0) cost_micros "
                "FROM agent_tasks t LEFT JOIN agent_task_usage u ON u.task_id=t.id WHERE t.group_id=?",
                (group_id,),
            ).fetchone()
    if not row:
        raise HTTPException(404, "research group not found")
    value = _group_value(row)
    value["usage"] = dict(totals)
    return value


@router.get("/groups")
def list_groups(campaign_id: str = Query(min_length=1), limit: int = Query(100, ge=1, le=500)):
    f = _core()
    with f.connect() as db:
        rows = db.execute(
            "SELECT g.*,COALESCE(u.task_count,0) task_count,COALESCE(u.cost_micros,0) cost_micros "
            "FROM research_groups g LEFT JOIN ("
            "SELECT t.group_id,COUNT(t.id) task_count,COALESCE(SUM(v.cost_micros),0) cost_micros "
            "FROM agent_tasks t LEFT JOIN agent_task_usage v ON v.task_id=t.id "
            "GROUP BY t.group_id) u ON u.group_id=g.id "
            "WHERE g.campaign_id=? ORDER BY g.created_at LIMIT ?", (campaign_id, limit),
        ).fetchall()
    items = []
    for row in rows:
        value = _group_value(row)
        value["usage"] = {"task_count": value.pop("task_count"),
                          "cost_micros": value.pop("cost_micros")}
        items.append(value)
    return {"items": items}


def _group_transition(group_id: str, target: str) -> dict[str, Any]:
    f = _core()
    with f.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM research_groups WHERE id=?", (group_id,)).fetchone()
        if not row:
            raise HTTPException(404, "research group not found")
        allowed = {"paused": {"active"}, "active": {"paused"}, "cancelled": {"active", "paused"}}
        if row["status"] not in allowed[target]:
            raise HTTPException(409, f"cannot transition group from {row['status']} to {target}")
        db.execute("UPDATE research_groups SET status=?,updated_at=? WHERE id=?", (target, _now(), group_id))
        if target == "paused":
            db.execute("UPDATE agent_tasks SET status='paused',lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,updated_at=? "
                       "WHERE group_id=? AND status IN ('queued','leased','running')", (_now(), group_id))
            _sync_runner_jobs(db)
        elif target == "active":
            db.execute("UPDATE agent_tasks SET status='queued',updated_at=? WHERE group_id=? AND status='paused'", (_now(), group_id))
        else:
            db.execute(
                "UPDATE agent_tasks SET status='cancelled',lease_owner=NULL,lease_expires_at=NULL,updated_at=? "
                "WHERE group_id=? AND status IN ('queued','paused','leased','running')", (_now(), group_id),
            )
            _sync_runner_jobs(db)
        _emit(db, row["campaign_id"], group_id, f"group.{target}")
    return get_group(group_id)


@router.post("/groups/{group_id}/pause")
def pause_group(group_id: str): return _group_transition(group_id, "paused")


@router.post("/groups/{group_id}/resume")
def resume_group(group_id: str): return _group_transition(group_id, "active")


@router.post("/groups/{group_id}/cancel")
def cancel_group(group_id: str): return _group_transition(group_id, "cancelled")


def _task_identity(body: TaskCreate) -> dict[str, Any]:
    return body.model_dump(mode="json")


@router.post("/tasks", status_code=201)
def create_task(body: TaskCreate):
    _validate_budget(body.budget, {"max_tokens", "max_cost_micros", "max_runtime_ms"})
    _validate_task_constraints(body)
    if body.role in {"critic", "synthesizer", "verifier", "evolver"}:
        raise HTTPException(422, "reserved worker roles require their structured task endpoint")
    f, now, task_id = _core(), _now(), _uid("atask")
    with f.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current_campaign = _campaign(db, body.campaign_id)
        _validate_run(db, current_campaign, body.run_id)
        existing = db.execute("SELECT * FROM agent_tasks WHERE idempotency_key=?", (body.idempotency_key,)).fetchone()
        if existing:
            comparable = {
                "campaign_id": existing["campaign_id"], "run_id": existing["run_id"],
                "group_id": existing["group_id"], "role": existing["role"], "objective": existing["objective"],
                "context_capsule": _load(existing["context_capsule_json"], {}),
                "tool_grants": _load(existing["tool_grants_json"], []),
                "route_requirement": _load(existing["route_requirement_json"], {}),
                "budget": _load(existing["budget_json"], {}), "priority": existing["priority"],
                "max_attempts": existing["max_attempts"], "idempotency_key": existing["idempotency_key"],
            }
            if _dump(comparable) != _dump(_task_identity(body)):
                raise HTTPException(409, "idempotency key was already used with a different task")
            task_id = existing["id"]
        else:
            status = "queued"
            if body.group_id:
                group = db.execute("SELECT * FROM research_groups WHERE id=?", (body.group_id,)).fetchone()
                if not group or group["campaign_id"] != body.campaign_id:
                    raise HTTPException(409, "group must belong to the same campaign")
                if group["status"] == "cancelled":
                    raise HTTPException(409, "cannot add a task to a cancelled group")
                status = "paused" if group["status"] == "paused" else "queued"
                group_budget = _load(group["budget_json"], {})
                count = db.execute("SELECT COUNT(*) FROM agent_tasks WHERE group_id=?", (body.group_id,)).fetchone()[0]
                if group_budget.get("max_tasks") is not None and count >= group_budget["max_tasks"]:
                    raise HTTPException(409, "group task budget exhausted")
            db.execute(
                "INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, body.campaign_id, body.run_id, body.group_id, body.role, body.objective,
                 _dump(body.context_capsule), _dump(body.tool_grants), _dump(body.route_requirement),
                 _dump(body.budget), body.priority, status, 0, body.max_attempts, body.idempotency_key,
                 None, None, None, None, None, now, now),
            )
            _emit(db, body.campaign_id, task_id, "task.created", {"status": status})
    return get_task(task_id)


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    f = _core()
    with f.connect() as db:
        row = db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
        usage = db.execute("SELECT * FROM agent_task_usage WHERE task_id=?", (task_id,)).fetchone() if row else None
        model_time = _model_times(db, [task_id]).get(task_id)
    if not row:
        raise HTTPException(404, "agent task not found")
    return _task_value(row, usage, model_time)


@router.get("/tasks")
def list_tasks(campaign_id: str = Query(min_length=1), status: str | None = None,
               limit: int = Query(100, ge=1, le=500), cursor: str | None = Query(None, max_length=4096)):
    f = _core()
    clause, params = (" AND status=?", [campaign_id, status]) if status else ("", [campaign_id])
    if cursor:
        try:
            value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if (not isinstance(value, list) or len(value) != 5 or value[:2] != [campaign_id, status]
                    or type(value[2]) not in (int, float) or not math.isfinite(value[2])
                    or not isinstance(value[3], str) or not isinstance(value[4], str)):
                raise ValueError("invalid task cursor")
            priority, created_at, task_id = value[2:]
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise HTTPException(422, "invalid cursor for this campaign/status") from None
        clause += " AND (priority<? OR (priority=? AND (created_at>? OR (created_at=? AND id>?))))"
        params.extend([priority, priority, created_at, created_at, task_id])
    params.append(limit + 1)
    with f.connect() as db:
        rows = db.execute(
            f"SELECT * FROM agent_tasks WHERE campaign_id=?{clause} ORDER BY priority DESC,created_at,id LIMIT ?", params,
        ).fetchall()
        visible_ids = [row["id"] for row in rows[:limit]]
        usage_rows = {row["task_id"]: row for row in db.execute(
            f"SELECT * FROM agent_task_usage WHERE task_id IN ({','.join('?' for _ in visible_ids)})",
            visible_ids,
        ).fetchall()} if visible_ids else {}
        model_times = _model_times(db, visible_ids)
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more:
        last = rows[-1]
        next_cursor = base64.urlsafe_b64encode(json.dumps(
            [campaign_id, status, last["priority"], last["created_at"], last["id"]],
            separators=(",", ":"), allow_nan=False).encode()).decode()
    return {"items": [_task_value(row, usage_rows.get(row["id"]), model_times.get(row['id'])) for row in rows],
            "page": {"has_more": has_more, "next_cursor": next_cursor, "limit": limit}}


@router.get("/status")
def orchestration_status():
    """Return a read-only control-plane snapshot for operator surfaces."""
    f = _core()
    with f.connect() as db:
        task_rows = db.execute(
            "SELECT * FROM agent_tasks ORDER BY updated_at DESC,id LIMIT 500"
        ).fetchall()
        usage_rows = {
            row["task_id"]: row for row in db.execute("SELECT * FROM agent_task_usage").fetchall()
        }
        group_rows = db.execute(
            "SELECT * FROM research_groups ORDER BY updated_at DESC,id LIMIT 500"
        ).fetchall()
        runner_rows = db.execute("SELECT * FROM runner_registry_v5 ORDER BY name,id").fetchall()
    tasks = [_task_value(row, usage_rows.get(row["id"])) for row in task_rows]
    return {
        "agents": tasks,
        "groups": [_group_value(row) for row in group_rows],
        "runners": [_runner_value(row) for row in runner_rows],
        "counts": {
            "tasks": len(tasks),
            "active_tasks": sum(task["status"] in {"queued", "leased", "running", "paused"} for task in tasks),
            "groups": len(group_rows),
            "runners": len(runner_rows),
            "online_runners": sum(row["status"] == "online" for row in runner_rows),
        },
    }


def _task_transition(task_id: str, action: str) -> dict[str, Any]:
    f = _core()
    with f.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "agent task not found")
        if action == "pause" and row["status"] == "queued": target = "paused"
        elif action == "resume" and row["status"] == "paused": target = "queued"
        elif action == "cancel" and row["status"] in {"queued", "paused", "leased", "running"}: target = "cancelled"
        elif action == "retry" and row["status"] == "failed" and row["attempt"] < row["max_attempts"]: target = "queued"
        else: raise HTTPException(409, f"cannot {action} task in {row['status']} state")
        db.execute(
            "UPDATE agent_tasks SET status=?,lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,"
            "error_json=CASE WHEN ?='queued' THEN NULL ELSE error_json END,updated_at=? WHERE id=?",
            (target, target, _now(), task_id),
        )
        _sync_runner_jobs(db, row["lease_owner"])
        _emit(db, row["campaign_id"], task_id, f"task.{target}", {"action": action})
    return get_task(task_id)


@router.post("/tasks/{task_id}/pause")
def pause_task(task_id: str): return _task_transition(task_id, "pause")


@router.post("/tasks/{task_id}/resume")
def resume_task(task_id: str): return _task_transition(task_id, "resume")


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str): return _task_transition(task_id, "cancel")


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: str): return _task_transition(task_id, "retry")


@runner_router.put("/{runner_id}")
def register_runner(runner_id: str, body: RunnerRegistration):
    if runner_id.startswith(("builtin-verifier-", "builtin-structured-", "builtin-research-")):
        raise HTTPException(409, "built-in runner identities are reserved")
    if (runner_id != body.id or len(set(body.capabilities)) != len(body.capabilities)
            or any(not item or len(item) > 100 for item in body.capabilities)):
        raise HTTPException(422, "runner id must match and capabilities must be unique")
    f, now = _core(), _now()
    with f.connect() as db:
        db.execute(
            "INSERT INTO runner_registry_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,status='online',"
            "capabilities_json=excluded.capabilities_json,labels_json=excluded.labels_json,"
            "max_concurrency=excluded.max_concurrency,heartbeat_at=excluded.heartbeat_at,"
            "metadata_json=excluded.metadata_json,updated_at=excluded.updated_at",
            (body.id, body.name, body.kind, "online", _dump(body.capabilities), _dump(body.labels),
             body.max_concurrency, 0, now, _dump(body.metadata), now, now),
        )
        _sync_runner_jobs(db, body.id)
    return get_runner(body.id)


@runner_router.get("/{runner_id}")
def get_runner(runner_id: str):
    f = _core()
    with f.connect() as db:
        row = db.execute("SELECT * FROM runner_registry_v5 WHERE id=?", (runner_id,)).fetchone()
    if not row:
        raise HTTPException(404, "runner not found")
    return _runner_value(row)


@runner_router.get("")
def list_runners():
    f = _core()
    with f.connect() as db:
        rows = db.execute("SELECT * FROM runner_registry_v5 ORDER BY name,id").fetchall()
    return {"items": [_runner_value(row) for row in rows]}


@runner_router.post("/{runner_id}/heartbeat")
def runner_heartbeat(runner_id: str):
    f, now = _core(), _now()
    with f.connect() as db:
        changed = db.execute(
            "UPDATE runner_registry_v5 SET status='online',heartbeat_at=?,updated_at=? WHERE id=?",
            (now, now, runner_id),
        ).rowcount
        if not changed:
            raise HTTPException(404, "runner not found")
        _sync_runner_jobs(db, runner_id)
    return get_runner(runner_id)


def _route_matches(requirement: dict[str, Any], runner: sqlite3.Row) -> bool:
    if requirement.get("kind") and requirement["kind"] != runner["kind"]:
        return False
    labels = _load(runner["labels_json"], {})
    return all(labels.get(key) == value for key, value in requirement.get("labels", {}).items())


def _group_can_lease(db: sqlite3.Connection, task: sqlite3.Row) -> bool:
    if not task["group_id"]:
        return True
    group = db.execute("SELECT * FROM research_groups WHERE id=?", (task["group_id"],)).fetchone()
    if not group or group["status"] != "active":
        return False
    budget = _load(group["budget_json"], {})
    running = db.execute(
        "SELECT COUNT(*) FROM agent_tasks WHERE group_id=? AND status IN ('leased','running')", (group["id"],),
    ).fetchone()[0]
    if budget.get("max_concurrency") is not None and running >= budget["max_concurrency"]:
        return False
    cost = db.execute(
        "SELECT COALESCE(SUM(u.cost_micros),0) FROM agent_tasks t "
        "LEFT JOIN agent_task_usage u ON u.task_id=t.id WHERE t.group_id=?", (group["id"],),
    ).fetchone()[0]
    cap = budget.get("max_cost_micros")
    if cap is None:
        return True
    if cost >= cap:
        return False
    # Running attempts reserve the remaining declared task allowance. This is
    # evaluated inside the same write transaction as the lease acquisition.
    rows = db.execute(
        "SELECT t.id,t.budget_json,COALESCE(u.cost_micros,0) spent FROM agent_tasks t "
        "LEFT JOIN agent_task_usage u ON u.task_id=t.id "
        "WHERE t.group_id=? AND (t.status IN ('leased','running') OR t.id=?)",
        (group["id"], task["id"]),
    ).fetchall()
    reserved = 0
    for item in rows:
        task_cap = _load(item["budget_json"], {}).get("max_cost_micros")
        if task_cap is None:
            return False  # An unbounded task cannot fit a finite group budget.
        reserved += max(0, task_cap - item["spent"])
    return cost + reserved <= cap


def _continuous_scope_current(db: sqlite3.Connection, task: sqlite3.Row) -> bool:
    capsule = _load(task["context_capsule_json"], {})
    if (not capsule.get("continuous_research") and not capsule.get("team_plan")
            and not capsule.get("structured_worker") and not capsule.get('native_discovery')
            and not capsule.get("cross_pollination")
            and not capsule.get("verification_request_id")):
        return True
    row = db.execute(
        "SELECT c.status AS campaign_status,e.status AS engagement_status,"
        "e.current_scope_snapshot_id,e.current_policy_id,s.confirmed_at,cr.enabled,cr.policy_json continuous_policy_json "
        "FROM research_campaigns c JOIN engagements_v2 e ON e.id=c.engagement_id "
        "LEFT JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id "
        "LEFT JOIN continuous_research_state cr ON cr.campaign_id=c.id WHERE c.id=?",
        (task["campaign_id"],),
    ).fetchone()
    current = bool(row and row["campaign_status"] == "active" and row["engagement_status"] != "archived"
                and row["confirmed_at"] and (not capsule.get("continuous_research") or row["enabled"])
                and row["current_scope_snapshot_id"] == capsule.get("scope_snapshot_id")
                and row["current_policy_id"] == capsule.get("policy_id"))
    if current and (capsule.get("team_plan") or capsule.get('native_discovery')
                    or (capsule.get("structured_worker") and task["run_id"])):
        run = db.execute(
            "SELECT r.status,r.synthetic,r.scope_snapshot_id,r.policy_id "
            "FROM analysis_runs r JOIN research_campaigns c ON c.engagement_id=r.engagement_id "
            "WHERE r.id=? AND c.id=?", (task["run_id"], task["campaign_id"]),
        ).fetchone()
        current = bool(row["engagement_status"] == "ready" and run and not run["synthetic"]
                       and run["status"] in {"queued", "running", "paused", "completed"}
                       and run["scope_snapshot_id"] == capsule.get("scope_snapshot_id")
                       and run["policy_id"] == capsule.get("policy_id"))
        if capsule.get('native_discovery') or capsule.get('structured_worker'):
            current = current and run['status'] == 'running'
    if current and capsule.get("continuous_policy_hash"):
        current = hashlib.sha256(_dump(_load(row["continuous_policy_json"], {})).encode()).hexdigest() == capsule["continuous_policy_hash"]
    if current and capsule.get("cross_pollination"):
        from v5_evolution import _inputs_current
        transfer = capsule.get("transfer", {})
        current = _inputs_current(db, task["campaign_id"], transfer.get("claim_id", ""),
                                  capsule.get("input_hashes", {}))
    if current and capsule.get("evolution_population_id"):
        from v5_evolution import evolution_task_current
        current = evolution_task_current(db, task)
    return current


@router.post("/lease")
def lease_task(body: LeaseRequest):
    if body.runner_id.startswith(("builtin-verifier-", "builtin-structured-", "builtin-research-",
                                   "builtin-native-browser-", "builtin-http-replay-", "builtin-run-http-")):
        raise HTTPException(409, "built-in runners use their dedicated local executor")
    recover_expired_leases()
    f = _core()
    with f.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        runner = db.execute("SELECT * FROM runner_registry_v5 WHERE id=?", (body.runner_id,)).fetchone()
        if not runner or runner["status"] != "online":
            raise HTTPException(409, "runner is not online")
        _sync_runner_jobs(db, body.runner_id)
        runner = db.execute("SELECT * FROM runner_registry_v5 WHERE id=?", (body.runner_id,)).fetchone()
        if runner["active_jobs"] >= runner["max_concurrency"]:
            return {"task": None, "reason": "runner_at_capacity"}
        capabilities = set(_load(runner["capabilities_json"], []))
        candidates = db.execute(
            "SELECT * FROM agent_tasks WHERE status='queued' AND attempt<max_attempts "
            "ORDER BY priority DESC,created_at,id",
        )
        # Stream in scheduling order and stop at the first eligible task. A
        # fixed prefix can permanently hide live work behind stale capsules.
        chosen = next((task for task in candidates
                       if set(_load(task["tool_grants_json"], [])).issubset(capabilities)
                       and _route_matches(_load(task["route_requirement_json"], {}), runner)
                       and _group_can_lease(db, task)
                       and _continuous_scope_current(db, task)), None)
        candidates.close()
        if not chosen:
            return {"task": None, "reason": "no_eligible_task"}
        expires = (datetime.now(timezone.utc) + timedelta(seconds=body.lease_seconds)).isoformat()
        changed = db.execute(
            "UPDATE agent_tasks SET status='running',attempt=attempt+1,lease_owner=?,lease_expires_at=?,"
            "heartbeat_at=?,updated_at=? WHERE id=? AND status='queued'",
            (body.runner_id, expires, _now(), _now(), chosen["id"]),
        ).rowcount
        if changed != 1:
            raise HTTPException(409, "task lease race; retry")
        _sync_runner_jobs(db, body.runner_id)
        _emit(db, chosen["campaign_id"], chosen["id"], "task.leased", {"runner_id": body.runner_id})
        leased = db.execute("SELECT * FROM agent_tasks WHERE id=?", (chosen["id"],)).fetchone()
    return {"task": _task_value(leased)}


def _owned_running(db: sqlite3.Connection, task_id: str, runner_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(404, "agent task not found")
    if row["status"] != "running" or row["lease_owner"] != runner_id:
        raise HTTPException(409, "task is not actively leased by this runner")
    if row["lease_expires_at"] and row["lease_expires_at"] <= _now():
        raise HTTPException(409, "task lease has expired")
    if not _continuous_scope_current(db, row):
        raise HTTPException(409, "worker scope or policy changed")
    return row


def _check_lease_attempt(task: sqlite3.Row, lease_attempt: int | None) -> None:
    """Fence late writes when a runner reclaims the same task.

    Initial leases accept the original result contract. Retried leases must echo
    the attempt returned by the scheduler; runner identity alone is ambiguous.
    Call while holding the result transaction, before graph or usage writes.
    """
    if lease_attempt is None:
        if task["attempt"] > 1:
            raise HTTPException(409, "retried task result requires lease_attempt")
    elif lease_attempt != task["attempt"]:
        raise HTTPException(409, "result belongs to a superseded lease attempt")


@router.post("/tasks/{task_id}/heartbeat")
def task_heartbeat(task_id: str, body: HeartbeatRequest):
    f = _core()
    with f.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _owned_running(db, task_id, body.runner_id)
        _check_lease_attempt(row, body.lease_attempt)
        expires = (datetime.now(timezone.utc) + timedelta(seconds=body.lease_seconds)).isoformat()
        db.execute("UPDATE agent_tasks SET heartbeat_at=?,lease_expires_at=?,updated_at=? WHERE id=?",
                   (_now(), expires, _now(), task_id))
        db.execute("UPDATE runner_registry_v5 SET heartbeat_at=?,updated_at=? WHERE id=?",
                   (_now(), _now(), body.runner_id))
        _emit(db, row["campaign_id"], task_id, "task.heartbeat", {"runner_id": body.runner_id})
    return get_task(task_id)


@router.post("/tasks/{task_id}/checkpoints", status_code=201)
def checkpoint_task(task_id: str, body: CheckpointRequest):
    encoded = _dump(body.checkpoint)
    if len(encoded.encode()) > 256_000:
        raise HTTPException(413, "checkpoint is too large")
    f, checkpoint_id = _core(), _uid("checkpoint")
    with f.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _owned_running(db, task_id, body.runner_id)
        _check_lease_attempt(row, body.lease_attempt)
        db.execute("INSERT INTO agent_task_checkpoints VALUES(?,?,?,?,?)",
                   (checkpoint_id, task_id, body.runner_id, encoded, _now()))
        _emit(db, row["campaign_id"], task_id, "task.checkpoint", {"checkpoint_id": checkpoint_id})
    return {"id": checkpoint_id, "task_id": task_id, "checkpoint": body.checkpoint}


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: str, body: CompletionRequest):
    if len(_dump(body.result).encode()) > 1_000_000 or len(_dump(body.error).encode()) > 256_000:
        raise HTTPException(413, "completion payload is too large")
    f = _core()
    with f.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _owned_running(db, task_id, body.runner_id)
        _check_lease_attempt(row, body.lease_attempt)
        context = _load(row["context_capsule_json"], {})
        if row["role"] == "verifier" and context.get("verification_request_id"):
            raise HTTPException(409, "verification tasks must finish through the immutable receipt endpoint")
        if context.get("structured_worker") in {"critic", "synthesizer", "evolver"}:
            raise HTTPException(409, "structured worker tasks must use the role-specific result endpoint")
        if context.get("team_plan"):
            raise HTTPException(409, "research team results require the dedicated research worker")
        if context.get('native_discovery'):
            raise HTTPException(409, 'native discovery results require their dedicated model runner')
        usage = body.usage.model_dump()
        previous = db.execute("SELECT * FROM agent_task_usage WHERE task_id=?", (task_id,)).fetchone()
        if previous:
            usage = {key: value + previous[key] for key, value in usage.items()}
        budget = _load(row["budget_json"], {})
        over = (
            (budget.get("max_tokens") is not None and usage["input_tokens"] + usage["output_tokens"] > budget["max_tokens"])
            or (budget.get("max_cost_micros") is not None and usage["cost_micros"] > budget["max_cost_micros"])
            or (budget.get("max_runtime_ms") is not None and usage["runtime_ms"] > budget["max_runtime_ms"])
        )
        status = "budget_exhausted" if over else body.outcome
        error = {"code": "task_budget_exhausted", "reported_outcome": body.outcome} if over else body.error
        db.execute(
            "INSERT INTO agent_task_usage VALUES(?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
            "input_tokens=excluded.input_tokens,output_tokens=excluded.output_tokens,"
            "cost_micros=excluded.cost_micros,runtime_ms=excluded.runtime_ms,updated_at=excluded.updated_at",
            (task_id, usage["input_tokens"], usage["output_tokens"], usage["cost_micros"], usage["runtime_ms"], _now()),
        )
        db.execute(
            "UPDATE agent_tasks SET status=?,result_json=?,error_json=?,lease_owner=NULL,"
            "lease_expires_at=NULL,heartbeat_at=NULL,updated_at=? WHERE id=?",
            (status, _dump(body.result) if body.result is not None else None,
             _dump(error) if error is not None else None, _now(), task_id),
        )
        _sync_runner_jobs(db, body.runner_id)
        _emit(db, row["campaign_id"], task_id, f"task.{status}", {"usage": usage})
    return get_task(task_id)


@router.post("/recover")
def recover_tasks():
    return recover_expired_leases()
