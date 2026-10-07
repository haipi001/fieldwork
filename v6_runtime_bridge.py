"""Read-only V6 views over the existing V5 runtime tables.

The bridge does not authorize or dispatch work. Unknown correlation and trust
fields stay null; arbitrary legacy payloads never cross this boundary.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any


def _object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return sorted({item for item in parsed if isinstance(item, str)}) if isinstance(parsed, list) else []


def _task(db: sqlite3.Connection, task_id: str | None) -> sqlite3.Row | None:
    if not task_id:
        return None
    return db.execute(
        "SELECT id,campaign_id,run_id,group_id,role,lease_owner FROM agent_tasks WHERE id=?", (task_id,)
    ).fetchone()


def normalize_event(db: sqlite3.Connection, event: sqlite3.Row) -> dict[str, Any]:
    """Normalize one persisted event without exposing its free-form payload."""
    kind = str(event["event_type"])
    entity_id = event["entity_id"]
    payload = _object(event["payload_json"])
    task = _task(db, entity_id if kind.startswith("task.") else None)
    route = None
    if kind == "route.decided" and entity_id:
        route = db.execute(
            "SELECT task_id,campaign_id FROM runtime_route_decisions WHERE id=?", (entity_id,)
        ).fetchone()
        task = _task(db, route["task_id"]) if route else None
    call = None
    if kind.startswith("call.") and entity_id:
        call = db.execute(
            "SELECT task_id,campaign_id,group_id,runner_id FROM runtime_calls WHERE id=?", (entity_id,)
        ).fetchone()
        task = _task(db, call["task_id"]) if call else None
    if kind == "policy.decided" and entity_id:
        policy = db.execute(
            "SELECT a.task_id,a.campaign_id FROM policy_decisions_v6 d "
            "JOIN action_intents_v6 a ON a.id=d.intent_id WHERE d.id=?", (entity_id,)
        ).fetchone()
        if policy:
            task = _task(db, policy["task_id"])
            if event["campaign_id"] != policy["campaign_id"]:
                raise ValueError("policy event campaign conflicts with intent")
    campaign_id = event["campaign_id"]
    if task and campaign_id and campaign_id != task["campaign_id"]:
        raise ValueError("event campaign conflicts with task")
    if route and campaign_id and campaign_id != route["campaign_id"]:
        raise ValueError("event campaign conflicts with route")
    if call and (campaign_id and campaign_id != call["campaign_id"] or
                 task and (task["campaign_id"] != call["campaign_id"] or task["group_id"] != call["group_id"])):
        raise ValueError("event campaign or group conflicts with call")
    task_id = task["id"] if task else None
    group_id = task["group_id"] if task else (entity_id if kind.startswith(("group.", "team.")) else None)
    runner_id = payload.get("runner_id") if kind in {"task.leased", "task.heartbeat"} else None
    if not isinstance(runner_id, str):
        runner_id = None
    if call:
        runner_id = call["runner_id"]
    return {
        "schema": "fieldwork.runtime-event/v6",
        "event_id": f"v5:{event['id']}",
        "event_type": f"agent.{kind}" if kind.startswith("task.") else kind,
        "timestamp": event["created_at"],
        "source": "v5_events",
        "topic": event["topic"],
        "trace_id": task["run_id"] if task and task["run_id"] else None,
        "run_id": task["run_id"] if task else None,
        "campaign_id": campaign_id or (task["campaign_id"] if task else None),
        "group_id": group_id,
        "task_id": task_id,
        "runner_id": runner_id,
        "principal_id": None,
        "agent_id": None,
        "operation": kind,
        "resource": None,
        "policy_decision": None,
        "artifact_ids": [],
        "node_id": None,
        "sequence": None,
        "causal_parent_event_id": None,
        "checkpoint_id": payload.get("checkpoint_id") if kind == "task.checkpoint" and isinstance(payload.get("checkpoint_id"), str) else None,
        "agent_build_hash": None,
        "signature_ref": None,
        "metadata": {"legacy_entity_id": entity_id},
    }


def list_events(db: sqlite3.Connection, *, after_id: int = 0, limit: int = 100,
                campaign_id: str | None = None) -> list[dict[str, Any]]:
    if after_id < 0 or not 1 <= limit <= 500:
        raise ValueError("invalid event page")
    if campaign_id is None:
        rows = db.execute("SELECT * FROM v5_events WHERE id>? ORDER BY id LIMIT ?", (after_id, limit)).fetchall()
    else:
        rows = db.execute("SELECT * FROM v5_events WHERE id>? AND campaign_id=? ORDER BY id LIMIT ?",
                          (after_id, campaign_id, limit)).fetchall()
    return [normalize_event(db, row) for row in rows]


def normalize_runner(row: sqlite3.Row) -> dict[str, Any]:
    legacy_kind = row["kind"]
    if legacy_kind in {"worker", "local", "research-worker", "native-discovery", "isolated-verifier"} or legacy_kind.startswith("local-"):
        kind = "local"
    elif legacy_kind in {"container", "remote", "cloud"}:
        kind = legacy_kind
    else:
        raise ValueError("runner placement kind is unknown")
    return {
        "runner_id": row["id"], "node_id": None, "kind": kind,
        "capabilities": _list(row["capabilities_json"]),
        "labels": {key: value for key, value in _object(row["labels_json"]).items()
                   if key in {"location", "os", "architecture"} and isinstance(value, str)},
        "max_concurrency": row["max_concurrency"], "status": row["status"],
        "heartbeat_at": row["heartbeat_at"], "attestation": None, "metadata": {},
    }


def list_runners(db: sqlite3.Connection) -> list[dict[str, Any]]:
    return [normalize_runner(row) for row in db.execute("SELECT * FROM runner_registry_v5 ORDER BY id")]


def list_agent_identities(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """Expose observed agent registry identities without linking them to tasks."""
    rows = db.execute("SELECT id,model,runtime FROM agent_registry_v5 ORDER BY id")
    return [{
        "agent_id": row["id"], "model": row["model"], "model_version": None,
        "runtime": row["runtime"], "node_id": None, "prompt_hash": None,
        "skill_set_hash": None, "tool_set_hash": None, "policy_hash": None,
        "environment_hash": None, "build_hash": None, "attestation": None,
        "metadata": {"source": "agent_registry_v5"},
    } for row in rows]


def list_model_call_snapshots(db: sqlite3.Connection, *, task_id: str) -> list[dict[str, Any]]:
    """Current ledger state, not an immutable event history."""
    task = _task(db, task_id)
    if not task:
        return []
    rows = db.execute("SELECT id,decision_id,task_id,attempt,runner_id,campaign_id,group_id,"
                      "profile_id,provider_id,reserved_tokens,reserved_cost_micros,max_runtime_ms,"
                      "state,created_at,updated_at FROM runtime_calls WHERE task_id=? ORDER BY attempt", (task_id,))
    result = []
    for row in rows:
        if row["campaign_id"] != task["campaign_id"] or row["group_id"] != task["group_id"]:
            raise ValueError("model call ownership conflicts with task")
        result.append({
            "schema": "fieldwork.runtime-call-snapshot/v6", "source": "runtime_calls",
            "call_id": row["id"], "decision_id": row["decision_id"], "task_id": task_id,
            "attempt": row["attempt"], "campaign_id": row["campaign_id"],
            "run_id": task["run_id"], "group_id": row["group_id"],
            "runner_id": row["runner_id"], "agent_id": None, "principal_id": None,
            "profile_id": row["profile_id"], "provider_id": row["provider_id"],
            "state": row["state"], "reserved_tokens": row["reserved_tokens"],
            "reserved_cost_micros": row["reserved_cost_micros"],
            "max_runtime_ms": row["max_runtime_ms"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        })
    return result
