"""Immutable, task-bound V5 verification receipts and replay requests."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from v5_orchestration import _continuous_scope_current, _sync_runner_jobs


router = APIRouter(prefix="/api/v1/verification", tags=["V5 Verification"])
LOCAL_VERIFIER_ID = f"builtin-verifier-{uuid.uuid4().hex[:12]}"
CHILD_SCRIPT = Path(__file__).with_name("v5_verifier_child.py")

EVIDENCE_TYPES = {"observation", "evidence", "counterevidence", "verification", "artifact"}
SENSITIVE_KEYS = {"authorization", "cookie", "password", "passwd", "secret", "token", "api_key", "apikey"}
RESULT_RULES = {
    "positive": "verified",
    "repaired_negative": "refuted",
    "healthy_negative": "refuted",
    "invalid_precondition": "inconclusive",
    "interrupted": "inconclusive",
}


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


def _sha(value: Any) -> str:
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def _contains_sensitive(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            sensitive = (normalized in SENSITIVE_KEYS or normalized.endswith(("_token", "_secret", "_password", "_cookie"))
                         or normalized.startswith(("token_", "secret_", "password_", "authorization_")))
            if sensitive or _contains_sensitive(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    return False


def _emit(db: sqlite3.Connection, campaign_id: str, entity_id: str,
          event_type: str, payload: dict[str, Any] | None = None) -> None:
    db.execute(
        "INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) "
        "VALUES(?,?,?,?,?,?)",
        ("verification", campaign_id, entity_id, event_type, _dump(payload or {}), _now()),
    )


def _node_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "campaign_id": row["campaign_id"], "run_id": row["run_id"],
        "node_type": row["node_type"], "title": row["title"], "body": row["body"],
        "status": row["status"], "confidence": row["confidence"],
        "source_type": row["source_type"], "source_ref": row["source_ref"],
        "attributes": _load(row["attributes_json"], {}),
    }


def _node_digest(row: sqlite3.Row) -> str:
    return _sha(_node_payload(row))


def _claim(db: sqlite3.Connection, claim_id: str, campaign_id: str | None = None) -> sqlite3.Row:
    row = db.execute("SELECT * FROM research_nodes WHERE id=?", (claim_id,)).fetchone()
    if not row or row["node_type"] != "claim" or (campaign_id and row["campaign_id"] != campaign_id):
        raise HTTPException(404, "claim not found")
    return row


def _input_snapshot(db: sqlite3.Connection, campaign_id: str, node_ids: list[str]) -> list[str]:
    if len(set(node_ids)) != len(node_ids):
        raise HTTPException(422, "evidence ids must be unique")
    snapshots = []
    for node_id in node_ids:
        row = db.execute("SELECT * FROM research_nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"evidence node not found: {node_id}")
        if row["campaign_id"] != campaign_id or row["node_type"] not in EVIDENCE_TYPES:
            raise HTTPException(409, "verification evidence must be an evidence-like node in the same campaign")
        snapshots.append(f"{node_id}:{_node_digest(row)}")
    return sorted(snapshots)


def _validate_replay_contract(value: dict[str, Any]) -> None:
    if not value or not isinstance(value.get("type"), str) or not value["type"]:
        raise HTTPException(422, "replay contract requires a type")
    if _contains_sensitive(value):
        raise HTTPException(422, "replay contract must not contain credentials or secrets")
    if len(_dump(value).encode()) > 128_000:
        raise HTTPException(413, "replay contract is too large")


class VerifyRequest(BaseModel):
    campaign_id: str = Field(min_length=1, max_length=200)
    verifier_group_id: str | None = Field(default=None, max_length=200)
    evidence_ids: list[str] = Field(min_length=1, max_length=200)
    replay_contract: dict[str, Any]
    priority: float = Field(default=100, ge=-1_000_000, le=1_000_000)


class VerificationResult(BaseModel):
    status: Literal["verified", "refuted", "inconclusive"]
    classification: Literal["positive", "repaired_negative", "healthy_negative", "invalid_precondition", "interrupted"]
    summary: str = Field(min_length=1, max_length=20_000)
    preconditions_valid: bool
    counterevidence_checked: bool
    interrupted: bool = False
    attempts: int = Field(default=1, ge=1, le=1000)
    oracle: dict[str, Any] = Field(default_factory=dict)


class ReceiptIssue(BaseModel):
    runner_id: str = Field(min_length=1, max_length=200)
    environment: dict[str, Any]
    result: VerificationResult
    evidence_ids: list[str] = Field(min_length=1, max_length=200)
    limitations: list[str] = Field(default_factory=list, max_length=200)


def _request_value(row: sqlite3.Row, task: sqlite3.Row | None = None) -> dict[str, Any]:
    value = dict(row)
    value["input_node_ids"] = _load(value.pop("input_node_ids_json"), [])
    value["input_hashes"] = _load(value.pop("input_hashes_json"), [])
    value["replay_contract"] = _load(value.pop("replay_contract_json"), {})
    if task:
        value["task_status"] = task["status"]
        value["task_attempt"] = task["attempt"]
    return value


def _receipt_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "campaign_id": row["campaign_id"], "claim_node_id": row["claim_node_id"],
        "verifier_id": row["verifier_id"], "runner_ref": row["runner_ref"],
        "environment": _load(row["environment_json"], {}),
        "input_hashes": _load(row["input_hashes_json"], []),
        "replay_contract": _load(row["replay_contract_json"], {}),
        "result": _load(row["result_json"], {}),
        "evidence_ids": _load(row["evidence_ids_json"], []),
        "limitations": _load(row["limitations_json"], []), "created_at": row["created_at"],
    }


def _validated_receipt(db: sqlite3.Connection, receipt_id: str) -> tuple[sqlite3.Row, dict[str, Any], sqlite3.Row]:
    row = db.execute("SELECT * FROM verification_receipts_v5 WHERE id=?", (receipt_id,)).fetchone()
    binding = db.execute("SELECT * FROM verification_receipt_bindings_v5 WHERE receipt_id=?", (receipt_id,)).fetchone()
    if not row or not binding:
        raise HTTPException(404, "verification receipt not found")
    payload = _receipt_payload(row)
    encoded = _dump(payload)
    if binding["payload_json"] != encoded:
        raise HTTPException(409, "verification receipt binding failed integrity validation")
    if row["receipt_sha256"] != hashlib.sha256(encoded.encode()).hexdigest():
        raise HTTPException(409, "verification receipt hash mismatch")
    request = db.execute("SELECT * FROM verification_requests_v5 WHERE id=?", (binding["request_id"],)).fetchone()
    if (not request or request["verifier_task_id"] != binding["verifier_task_id"]
            or request["claim_node_id"] != row["claim_node_id"]
            or request["claim_sha256"] != binding["claim_sha256"]):
        raise HTTPException(409, "verification receipt request binding is invalid")
    return row, payload, binding


def _inputs_current(db: sqlite3.Connection, binding: sqlite3.Row) -> bool:
    request = db.execute("SELECT * FROM verification_requests_v5 WHERE id=?", (binding["request_id"],)).fetchone()
    task = db.execute("SELECT * FROM agent_tasks WHERE id=?", (binding["verifier_task_id"],)).fetchone()
    claim = db.execute("SELECT * FROM research_nodes WHERE id=?", (request["claim_node_id"],)).fetchone() if request else None
    if (not request or not task or not _continuous_scope_current(db, task)
            or not claim or _node_digest(claim) != request["claim_sha256"]):
        return False
    try:
        current = _input_snapshot(db, request["campaign_id"], _load(request["input_node_ids_json"], []))
    except HTTPException:
        return False
    if current != _load(request["input_hashes_json"], []):
        return False
    if _load(request["replay_contract_json"], {}).get("type") == "package_applicability_v1":
        return _package_replay_input(db, request) is not None
    if _load(request['replay_contract_json'], {}).get('type') == 'http_object_read_v1':
        from v5_http_receipts import replay_input
        return replay_input(db, request) is not None
    return True


def _process_observed(payload: dict[str, Any]) -> bool:
    environment = payload.get("environment", {})
    process_proof = environment.get("process_isolation", {})
    package = environment.get('attested', {}).get('oracle', environment.get('oracle')) in {'package_applicability_v1', 'http_object_read_v1'}
    return bool(
        environment.get("provenance") == "supervisor_observed_process"
        and process_proof.get("observed_by") == "fieldwork_local_supervisor"
        and process_proof.get("sandbox") == "macos-seatbelt"
        and isinstance(process_proof.get("sandbox_profile_sha256"), str)
        and len(process_proof["sandbox_profile_sha256"]) == 64
        and process_proof.get("file_read_denied") is True
        and (not package or process_proof.get("network_denied") is True)
        and process_proof.get("exit_code") == 0
        and isinstance(process_proof.get("child_pid"), int)
        and isinstance(process_proof.get("parent_pid"), int)
        and process_proof["child_pid"] != process_proof["parent_pid"]
    )


def _get_receipt_value(db: sqlite3.Connection, receipt_id: str) -> dict[str, Any]:
    row, payload, binding = _validated_receipt(db, receipt_id)
    current = _inputs_current(db, binding)
    observed = _process_observed(payload)
    result = payload.get("result", {})
    claim_oracle_current = True
    if payload.get("replay_contract", {}).get("type") == "loopback_http_status_v1":
        claim = db.execute("SELECT attributes_json FROM research_nodes WHERE id=?", (row["claim_node_id"],)).fetchone()
        attributes = _load(claim["attributes_json"], {}) if claim else {}
        claim_oracle_current = bool(
            attributes.get("claim_kind") == "http_status_relationship"
            and attributes.get("verification_contract_sha256") == _sha(payload["replay_contract"])
        )
    elif payload.get("replay_contract", {}).get("type") == "package_applicability_v1":
        request = db.execute("SELECT * FROM verification_requests_v5 WHERE id=?", (binding["request_id"],)).fetchone()
        current_input = _package_replay_input(db, request) if request else None
        oracle = result.get("oracle", {})
        fingerprint = current_input.get("fingerprint", {}) if current_input else {}
        claim_oracle_current = bool(
            current_input and oracle.get("kind") == "package_applicability_v1"
            and oracle.get("source_artifact_sha256") == current_input["source_artifact_sha256"]
            and oracle.get("advisory_record_sha256") == current_input["advisory_record_sha256"]
            and oracle.get("exact_affected_version_listed") is True
            and oracle.get("observed_versions") == [fingerprint.get("version")]
            and all(oracle.get(key) == fingerprint.get(key) for key in ("ecosystem", "name", "version"))
        )
    elif payload.get('replay_contract', {}).get('type') == 'http_object_read_v1':
        request = db.execute('SELECT * FROM verification_requests_v5 WHERE id=?', (binding['request_id'],)).fetchone()
        claim_oracle_current = _http_receipt_matches(db, request, result)
    eligible = bool(current and observed and claim_oracle_current
                    and result.get("status") in {"verified", "refuted"} and not result.get("interrupted"))
    domain = ("applicability_only" if payload.get("replay_contract", {}).get("type") == "package_applicability_v1"
              else "canonical_result")
    return {"id": row["id"], **payload, "receipt_sha256": row["receipt_sha256"],
            "request_id": binding["request_id"], "verifier_task_id": binding["verifier_task_id"],
            "integrity": {"valid": True, "algorithm": "sha256", "current_inputs_match": current,
                          "process_isolation_observed": observed, "promotion_eligible": eligible,
                          "promotion_domain": domain}}


def _request_fingerprint(claim_id: str, input_hashes: list[str], replay_contract: dict[str, Any],
                         source_receipt_id: str | None, scope_id: str, policy_id: str) -> str:
    return _sha({"claim_id": claim_id, "input_hashes": input_hashes,
                 "replay_contract": replay_contract, "source_receipt_id": source_receipt_id,
                 "scope_snapshot_id": scope_id, "policy_id": policy_id})


def _create_request_transaction(db: sqlite3.Connection, claim: sqlite3.Row, body: VerifyRequest,
                                source_receipt_id: str | None = None) -> tuple[sqlite3.Row, sqlite3.Row, bool]:
    campaign = db.execute(
        "SELECT c.status,e.status AS engagement_status,e.current_scope_snapshot_id,"
        "e.current_policy_id,s.confirmed_at FROM research_campaigns c "
        "JOIN engagements_v2 e ON e.id=c.engagement_id "
        "LEFT JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id WHERE c.id=?",
        (body.campaign_id,),
    ).fetchone()
    if (not campaign or campaign["status"] != "active" or campaign["engagement_status"] == "archived"
            or not campaign["confirmed_at"] or not campaign["current_policy_id"]):
        raise HTTPException(409, "active campaign and confirmed scope/policy required")
    input_hashes = _input_snapshot(db, body.campaign_id, body.evidence_ids)
    fingerprint = _request_fingerprint(claim["id"], input_hashes, body.replay_contract, source_receipt_id,
                                       campaign["current_scope_snapshot_id"], campaign["current_policy_id"])
    request_id, task_id = f"vrequest-{fingerprint[:24]}", f"vtask-{fingerprint[:24]}"
    existing = db.execute("SELECT * FROM verification_requests_v5 WHERE id=?", (request_id,)).fetchone()
    if existing:
        task = db.execute("SELECT * FROM agent_tasks WHERE id=?", (existing["verifier_task_id"],)).fetchone()
        return existing, task, False
    if body.verifier_group_id:
        group = db.execute("SELECT * FROM research_groups WHERE id=?", (body.verifier_group_id,)).fetchone()
        if not group or group["campaign_id"] != body.campaign_id or group["status"] != "active":
            raise HTTPException(409, "verifier group must be active and belong to the campaign")
    attributes = _load(claim["attributes_json"], {})
    producer_task_id = attributes.get("producer_task_id") if isinstance(attributes.get("producer_task_id"), str) else None
    producer_runner = attributes.get("producer_runner_ref") if isinstance(attributes.get("producer_runner_ref"), str) else None
    if producer_task_id and body.verifier_group_id:
        producer = db.execute("SELECT group_id FROM agent_tasks WHERE id=?", (producer_task_id,)).fetchone()
        if producer and producer["group_id"] and producer["group_id"] == body.verifier_group_id:
            raise HTTPException(409, "verifier must not share the producer task group")
    now = _now()
    context = {
        "verification_request_id": request_id, "claim_id": claim["id"],
        "claim_sha256": _node_digest(claim), "input_node_ids": sorted(body.evidence_ids),
        "input_hashes": input_hashes, "replay_contract": body.replay_contract,
        "scope_snapshot_id": campaign["current_scope_snapshot_id"],
        "policy_id": campaign["current_policy_id"],
        "constraints": ["independent context", "counterevidence required", "no self-certification"],
    }
    db.execute(
        "INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (task_id, body.campaign_id, None, body.verifier_group_id, "verifier",
         f"Independently verify claim {claim['id']}: {claim['title']}", _dump(context),
         _dump(["independent_verification"]), _dump({"kind": "isolated-verifier"}),
         _dump({"max_tokens": 12000}), body.priority, "queued", 0, 3,
         f"verification:{fingerprint}", None, None, None, None, None, now, now),
    )
    db.execute(
        "INSERT INTO verification_requests_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (request_id, body.campaign_id, claim["id"], _node_digest(claim), producer_task_id,
         producer_runner, task_id, _dump(sorted(body.evidence_ids)), _dump(input_hashes),
         _dump(body.replay_contract), "queued", source_receipt_id, now, now),
    )
    _emit(db, body.campaign_id, request_id, "verification.requested", {"task_id": task_id, "claim_id": claim["id"]})
    return (db.execute("SELECT * FROM verification_requests_v5 WHERE id=?", (request_id,)).fetchone(),
            db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone(), True)


@router.post("/claims/{claim_id}", status_code=202)
def request_verification(claim_id: str, body: VerifyRequest):
    _validate_replay_contract(body.replay_contract)
    f = _core()
    with f.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        claim = _claim(db, claim_id, body.campaign_id)
        request, task, created = _create_request_transaction(db, claim, body)
    return {"status": request["status"], "created": created, "request": _request_value(request, task)}


@router.get("/requests/{request_id}")
def get_request(request_id: str):
    f = _core()
    with f.connect() as db:
        row = db.execute("SELECT * FROM verification_requests_v5 WHERE id=?", (request_id,)).fetchone()
        task = db.execute("SELECT * FROM agent_tasks WHERE id=?", (row["verifier_task_id"],)).fetchone() if row else None
    if not row:
        raise HTTPException(404, "verification request not found")
    return _request_value(row, task)


def _validate_result(result: VerificationResult) -> None:
    if RESULT_RULES[result.classification] != result.status:
        raise HTTPException(422, "verification classification does not match status")
    if result.interrupted and result.classification != "interrupted":
        raise HTTPException(422, "interrupted verification must be inconclusive")
    if not result.preconditions_valid and result.status != "inconclusive":
        raise HTTPException(422, "invalid preconditions cannot produce a conclusive receipt")
    if result.status in {"verified", "refuted"} and not result.counterevidence_checked:
        raise HTTPException(422, "conclusive verification requires counterevidence evaluation")
    if result.status in {"verified", "refuted"} and not isinstance(result.oracle.get("kind"), str):
        raise HTTPException(422, "conclusive verification requires a machine-readable oracle kind")
    if _contains_sensitive(result.model_dump(mode="json")):
        raise HTTPException(422, "verification result must not contain credentials or secrets")


def _same_retry(payload: dict[str, Any], body: ReceiptIssue) -> bool:
    environment = payload.get("environment", {}).get("attested", {})
    return (environment == body.environment and payload.get("result") == body.result.model_dump(mode="json")
            and payload.get("evidence_ids") == sorted(body.evidence_ids)
            and payload.get("limitations") == body.limitations)


@router.post("/tasks/{task_id}/receipt", status_code=201)
def issue_receipt(task_id: str, body: ReceiptIssue):
    return _issue_receipt(task_id, body)


def _issue_receipt(task_id: str, body: ReceiptIssue,
                   process_attestation: dict[str, Any] | None = None):
    if body.runner_id.startswith("builtin-verifier-") and not process_attestation:
        raise HTTPException(409, "built-in verifier receipts require supervisor-observed execution")
    _validate_result(body.result)
    if _contains_sensitive(body.environment):
        raise HTTPException(422, "environment snapshot must not contain credentials or secrets")
    if len(_dump(body.environment).encode()) > 128_000:
        raise HTTPException(413, "environment snapshot is too large")
    if len(set(body.evidence_ids)) != len(body.evidence_ids) or any(not item for item in body.limitations):
        raise HTTPException(422, "evidence ids must be unique and limitations must be non-empty")
    f = _core()
    with f.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        request = db.execute("SELECT * FROM verification_requests_v5 WHERE verifier_task_id=?", (task_id,)).fetchone()
        if not request:
            raise HTTPException(404, "verification request not found for task")
        existing = db.execute(
            "SELECT r.* FROM verification_receipts_v5 r JOIN verification_receipt_bindings_v5 b "
            "ON b.receipt_id=r.id WHERE b.request_id=?", (request["id"],),
        ).fetchone()
        if existing:
            payload = _receipt_payload(existing)
            if existing["runner_ref"] != body.runner_id or not _same_retry(payload, body):
                raise HTTPException(409, "verification request already has a different immutable receipt")
            return _get_receipt_value(db, existing["id"])
        task = db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
        if (not task or task["status"] != "running" or task["lease_owner"] != body.runner_id
                or not task["lease_expires_at"] or task["lease_expires_at"] <= _now()):
            raise HTTPException(409, "receipt requires a current verifier task lease owned by this runner")
        if not _continuous_scope_current(db, task):
            raise HTTPException(409, "verifier scope or policy changed")
        if process_attestation:
            current_input = _local_replay_input(db, request)
            if (current_input is None or process_attestation.get("input_sha256")
                    != hashlib.sha256(_dump(current_input).encode()).hexdigest()):
                raise HTTPException(409, "local verifier input changed after independent replay")
        runner = db.execute("SELECT * FROM runner_registry_v5 WHERE id=?", (body.runner_id,)).fetchone()
        capabilities = set(_load(runner["capabilities_json"], [])) if runner else set()
        if (not runner or runner["status"] != "online" or runner["kind"] != "isolated-verifier"
                or "independent_verification" not in capabilities):
            raise HTTPException(409, "runner is not registered for independent verification")
        if request["producer_runner_ref"] and request["producer_runner_ref"] == body.runner_id:
            raise HTTPException(409, "claim producer cannot self-verify")
        claim = _claim(db, request["claim_node_id"], request["campaign_id"])
        if _node_digest(claim) != request["claim_sha256"]:
            raise HTTPException(409, "claim changed after verification was requested")
        input_ids = _load(request["input_node_ids_json"], [])
        if _input_snapshot(db, request["campaign_id"], input_ids) != _load(request["input_hashes_json"], []):
            raise HTTPException(409, "verification input evidence changed after request")
        _input_snapshot(db, request["campaign_id"], body.evidence_ids)
        if body.result.status in {"verified", "refuted"} and not body.evidence_ids:
            raise HTTPException(422, "conclusive verification requires produced evidence")
        metadata = _load(runner["metadata_json"], {})
        verifier_id = metadata.get("verifier_id") if isinstance(metadata.get("verifier_id"), str) else body.runner_id
        created = _now()
        environment = {
            "runner": {"id": runner["id"], "kind": runner["kind"], "capabilities": sorted(capabilities),
                       "labels": _load(runner["labels_json"], {})},
            "attested": body.environment,
            "provenance": "supervisor_observed_process" if process_attestation else "runner_attested",
            "context_isolation": {"request_id": request["id"], "task_id": task_id,
                                  "producer_runner_ref": request["producer_runner_ref"]},
        }
        if process_attestation:
            environment["process_isolation"] = process_attestation
        result = body.result.model_dump(mode="json")
        payload = {
            "campaign_id": request["campaign_id"], "claim_node_id": request["claim_node_id"],
            "verifier_id": verifier_id, "runner_ref": body.runner_id, "environment": environment,
            "input_hashes": _load(request["input_hashes_json"], []),
            "replay_contract": _load(request["replay_contract_json"], {}), "result": result,
            "evidence_ids": sorted(body.evidence_ids), "limitations": body.limitations, "created_at": created,
        }
        encoded, receipt_id = _dump(payload), _uid("vreceipt")
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        db.execute(
            "INSERT INTO verification_receipts_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (receipt_id, request["campaign_id"], request["claim_node_id"], verifier_id, body.runner_id,
             _dump(environment), _dump(payload["input_hashes"]), _dump(payload["replay_contract"]),
             _dump(result), _dump(payload["evidence_ids"]), _dump(body.limitations), digest, created),
        )
        db.execute("INSERT INTO verification_receipt_bindings_v5 VALUES(?,?,?,?,?,?)",
                   (receipt_id, request["id"], task_id, request["claim_sha256"], encoded, created))
        db.execute("UPDATE verification_requests_v5 SET status='issued',updated_at=? WHERE id=?", (created, request["id"]))
        db.execute(
            "UPDATE agent_tasks SET status='succeeded',result_json=?,error_json=NULL,lease_owner=NULL,"
            "lease_expires_at=NULL,heartbeat_at=NULL,updated_at=? WHERE id=?",
            (_dump({"verification_receipt_id": receipt_id, "status": result["status"]}), created, task_id),
        )
        db.execute(
            "UPDATE runner_registry_v5 SET active_jobs=(SELECT COUNT(*) FROM agent_tasks "
            "WHERE lease_owner=? AND status IN ('leased','running')),updated_at=? WHERE id=?",
            (body.runner_id, created, body.runner_id),
        )
        _emit(db, request["campaign_id"], receipt_id, "receipt.issued",
              {"claim_id": request["claim_node_id"], "status": result["status"], "task_id": task_id})
        value = _get_receipt_value(db, receipt_id)
    return value


def _loopback_contract_allowed(db: sqlite3.Connection, request: sqlite3.Row) -> bool:
    contract = _load(request["replay_contract_json"], {})
    if contract.get("type") != "loopback_http_status_v1":
        return False
    claim = db.execute("SELECT attributes_json FROM research_nodes WHERE id=? AND campaign_id=?",
                       (request["claim_node_id"], request["campaign_id"])).fetchone()
    attributes = _load(claim["attributes_json"], {}) if claim else {}
    if (attributes.get("claim_kind") != "http_status_relationship"
            or attributes.get("verification_contract_sha256") != _sha(contract)):
        return False
    urls = [contract.get("url"), contract.get("negative_control_url")]
    if any(not isinstance(url, str) or len(url) > 2000 for url in urls):
        return False
    parsed = [urlsplit(url) for url in urls]
    try:
        valid = all(value.scheme == "http" and value.port and not value.username
                    and not value.password and not value.fragment
                    and ipaddress.ip_address(value.hostname or "").is_loopback for value in parsed)
    except ValueError:
        return False
    if not valid or (parsed[0].scheme, parsed[0].hostname, parsed[0].port) != (
            parsed[1].scheme, parsed[1].hostname, parsed[1].port):
        return False
    campaign = db.execute(
        "SELECT c.status,e.status AS engagement_status,e.current_scope_snapshot_id,"
        "e.current_policy_id,s.rules,s.confirmed_at,p.policy FROM research_campaigns c "
        "JOIN engagements_v2 e ON e.id=c.engagement_id "
        "JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id "
        "JOIN execution_policies p ON p.id=e.current_policy_id WHERE c.id=?", (request["campaign_id"],),
    ).fetchone()
    if not campaign or campaign["status"] != "active" or campaign["engagement_status"] == "archived" or not campaign["confirmed_at"]:
        return False
    rules = _load(campaign["rules"], {})
    policy = _load(campaign["policy"], {})
    if "read" not in rules.get("allowed_actions", []) or policy.get("max_requests", 0) < 4:
        return False
    return any(
        all(value.scheme == target.scheme and value.hostname == target.hostname
            and value.port == target.port and value.path.startswith(target.path.rstrip("/") + "/")
            for value in parsed)
        for target in (urlsplit(url) for url in rules.get("allowed_targets", []))
    ) or any(
        all(value.scheme == target.scheme and value.hostname == target.hostname
            and value.port == target.port and target.path in ("", "/") for value in parsed)
        for target in (urlsplit(url) for url in rules.get("allowed_targets", []))
    )


def _read_trivy_artifact(path_text: str, expected_sha256: str) -> dict[str, Any] | None:
    import traditional_tools

    path = Path(path_text)
    root = traditional_tools.ARTIFACT_ROOT.resolve()
    if (not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(root)
            or not isinstance(expected_sha256, str) or len(expected_sha256) != 64):
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 256_000:
                return None
            raw = stream.read(256_001)
    except (OSError, ValueError):
        return None
    if len(raw) > 256_000 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _package_replay_input(db: sqlite3.Connection, request: sqlite3.Row) -> dict[str, Any] | None:
    from v5_intelligence import _assert_current_match

    contract = _load(request["replay_contract_json"], {})
    if (set(contract) != {"type", "match_id", "artifact_id"}
            or contract.get("type") != "package_applicability_v1"):
        return None
    match = db.execute("SELECT * FROM intel_matches WHERE id=? AND campaign_id=?",
                       (contract["match_id"], request["campaign_id"])).fetchone()
    if not match:
        return None
    try:
        _assert_current_match(db, match)
    except HTTPException:
        return None
    fingerprint = _load(match["fingerprint_json"], {})
    if (fingerprint.get("source_kind") != "artifact"
            or fingerprint.get("source_ref") != contract["artifact_id"]
            or not fingerprint.get("version")):
        return None
    claim = db.execute("SELECT attributes_json FROM research_nodes WHERE id=? AND campaign_id=?",
                       (request["claim_node_id"], request["campaign_id"])).fetchone()
    attributes = _load(claim["attributes_json"], {}) if claim else {}
    if (attributes.get("claim_kind") != "package_applicability"
            or attributes.get("verification_contract_sha256") != _sha(contract)
            or not db.execute("SELECT 1 FROM research_edges WHERE campaign_id=? AND source_id=? "
                              "AND target_id=? AND relation_type='derived_from'",
                              (request["campaign_id"], request["claim_node_id"],
                               match["research_node_id"])).fetchone()):
        return None
    record = db.execute("SELECT * FROM intel_records WHERE id=?", (match["intel_record_id"],)).fetchone()
    advisory = _load(record["record_json"], {}) if record else {}
    if not record or _sha(advisory) != record["record_sha256"]:
        return None
    artifact = db.execute(
        "SELECT a.*,r.engagement_id,r.synthetic,r.scope_snapshot_id,r.policy_id FROM artifacts a "
        "JOIN analysis_runs r ON r.id=a.run_id WHERE a.id=?",
        (contract["artifact_id"],),
    ).fetchone()
    campaign = db.execute("SELECT engagement_id FROM research_campaigns WHERE id=?",
                          (request["campaign_id"],)).fetchone()
    if (not artifact or not campaign or artifact["engagement_id"] != campaign["engagement_id"]
            or artifact["synthetic"] or artifact["kind"] != "traditional.tool.trivy"
            or artifact["media_type"] != "application/json" or artifact["redacted"] != 1
            or artifact["scope_snapshot_id"] != fingerprint.get("scope_snapshot_id")
            or artifact["policy_id"] != fingerprint.get("policy_id")):
        return None
    source = _read_trivy_artifact(artifact["uri"], artifact["sha256"])
    if not source:
        return None
    tool_result = source.get("tool_result", {})
    if (not isinstance(tool_result, dict) or tool_result.get("capability") != "trivy"
            or tool_result.get("status") != "completed" or tool_result.get("exit_code") != 0
            or tool_result.get("synthetic") is not False):
        return None
    observed = []
    items = source.get("observations", [])
    if not isinstance(items, list) or len(items) > 200:
        return None
    for item in items:
        if not isinstance(item, dict) or item.get("observation_type") != "code.dependency_vulnerability":
            continue
        data = item.get("structured", {})
        identifier = data.get("PkgIdentifier", {}) if isinstance(data, dict) else {}
        if not isinstance(identifier, dict):
            return None
        values = {"purl": identifier.get("PURL"), "pkg_name": data.get("PkgName"),
                  "installed_version": data.get("InstalledVersion")}
        if any(not isinstance(value, str) or not value or len(value) > 500 for value in values.values()):
            return None
        observed.append(values)
    rationale = _load(match["rationale_json"], {})
    advisory_node = db.execute("SELECT * FROM research_nodes WHERE id=? AND campaign_id=?",
                               (rationale.get("advisory_node_id"), request["campaign_id"])).fetchone()
    artifact_node = db.execute("SELECT id,attributes_json FROM research_nodes WHERE campaign_id=? AND node_type='artifact' "
                               "AND source_type='artifact' AND source_ref=?",
                               (request["campaign_id"], artifact["id"])).fetchone()
    input_ids = set(_load(request["input_node_ids_json"], []))
    if (not advisory_node or advisory_node["source_ref"] != record["id"]
            or _load(advisory_node["attributes_json"], {}).get("record_sha256") != record["record_sha256"]
            or not artifact_node
            or _load(artifact_node["attributes_json"], {}).get("sha256") != artifact["sha256"]
            or not {advisory_node["id"], artifact_node["id"]}.issubset(input_ids)):
        return None
    return {"type": "package_applicability_v1",
            "fingerprint": {key: fingerprint[key] for key in ("ecosystem", "name", "version")},
            "affected": [item for item in advisory.get("affected", [])
                         if item.get("ecosystem", "").casefold() == fingerprint["ecosystem"].casefold()
                         and item.get("name", "").casefold() == fingerprint["name"].casefold()],
            "observed_packages": observed,
            "source_artifact_sha256": artifact["sha256"],
            "advisory_record_sha256": record["record_sha256"]}


def _local_replay_input(db: sqlite3.Connection, request: sqlite3.Row) -> dict[str, Any] | None:
    contract = _load(request["replay_contract_json"], {})
    if contract.get("type") == "loopback_http_status_v1":
        return contract if _loopback_contract_allowed(db, request) else None
    if contract.get("type") == "package_applicability_v1":
        return _package_replay_input(db, request)
    if contract.get('type') == 'http_object_read_v1':
        from v5_http_receipts import replay_input
        return replay_input(db, request)
    return None


def _claim_local_verifier(db: sqlite3.Connection, request_id: str | None = None) -> tuple[sqlite3.Row, sqlite3.Row, dict[str, Any]] | None:
    now = _now()
    runner = db.execute("SELECT * FROM runner_registry_v5 WHERE id=?", (LOCAL_VERIFIER_ID,)).fetchone()
    if not runner:
        db.execute(
            "INSERT INTO runner_registry_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (LOCAL_VERIFIER_ID, "Built-in isolated local verifier", "isolated-verifier", "online",
             _dump(["independent_verification"]), _dump({"location": "local"}), 1, 0,
             now, _dump({"builtin": True}), now, now),
        )
    elif _load(runner["metadata_json"], {}).get("builtin") is not True:
        raise HTTPException(409, "built-in verifier identity is occupied")
    else:
        db.execute(
            "UPDATE runner_registry_v5 SET status='online',kind='isolated-verifier',"
            "capabilities_json=?,max_concurrency=1,heartbeat_at=?,updated_at=? WHERE id=?",
            (_dump(["independent_verification"]), now, now, LOCAL_VERIFIER_ID),
        )
    _sync_runner_jobs(db, LOCAL_VERIFIER_ID)
    if db.execute("SELECT active_jobs FROM runner_registry_v5 WHERE id=?", (LOCAL_VERIFIER_ID,)).fetchone()[0]:
        return None
    rows = db.execute(
        "SELECT t.*,v.id AS request_id FROM agent_tasks t JOIN verification_requests_v5 v "
        "ON v.verifier_task_id=t.id WHERE t.status='queued' AND t.attempt<t.max_attempts "
        "AND (? IS NULL OR v.id=?) ORDER BY t.priority DESC,t.created_at,t.id LIMIT 100", (request_id, request_id),
    ).fetchall()
    for task in rows:
        request = db.execute("SELECT * FROM verification_requests_v5 WHERE id=?", (task["request_id"],)).fetchone()
        if request["producer_runner_ref"] == LOCAL_VERIFIER_ID:
            continue
        execution_input = _local_replay_input(db, request)
        if execution_input is None:
            continue
        expires = (datetime.now(timezone.utc) + timedelta(seconds=45)).isoformat()
        db.execute(
            "UPDATE agent_tasks SET status='running',attempt=attempt+1,lease_owner=?,"
            "lease_expires_at=?,heartbeat_at=?,updated_at=? WHERE id=? AND status='queued'",
            (LOCAL_VERIFIER_ID, expires, now, now, task["id"]),
        )
        _sync_runner_jobs(db, LOCAL_VERIFIER_ID)
        return db.execute("SELECT * FROM agent_tasks WHERE id=?", (task["id"],)).fetchone(), request, execution_input
    return None


class LocalVerificationStopped(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _check_local_execution(task_id, request_id, execution_input):
    with _core().connect() as db:
        task = db.execute('SELECT * FROM agent_tasks WHERE id=?', (task_id,)).fetchone()
        request = db.execute('SELECT * FROM verification_requests_v5 WHERE id=?', (request_id,)).fetchone()
        if task and task['status'] == 'cancelled':
            raise LocalVerificationStopped('cancelled')
        if (not task or not request or task['status'] != 'running' or task['lease_owner'] != LOCAL_VERIFIER_ID
                or not task['lease_expires_at'] or task['lease_expires_at'] <= _now()):
            raise LocalVerificationStopped('lease_lost')
        contract = _load(request['replay_contract_json'], {})
        if contract.get('type') == 'http_object_read_v1':
            traditional = 'verification_job_id' in contract
            table, key = ('verification_jobs', 'verification_job_id') if traditional else ('guided_research_jobs', 'guided_job_id')
            source = db.execute(f'SELECT cancel_requested FROM {table} WHERE id=?', (contract.get(key),)).fetchone()
            if source and source['cancel_requested']:
                raise LocalVerificationStopped('cancelled')
        binding = {'request_id': request_id, 'verifier_task_id': task_id}
        if (not _inputs_current(db, binding)
                or _sha(_local_replay_input(db, request)) != _sha(execution_input)):
            raise LocalVerificationStopped('inputs_changed')


def _run_local_verifier(execution_input: dict[str, Any], check_current=None) -> tuple[dict[str, Any], dict[str, Any]]:
    encoded = _dump(execution_input).encode()
    if len(encoded) > 65_536:
        raise ValueError("local verifier input exceeds 64 KB")
    sandbox = Path("/usr/bin/sandbox-exec")
    if sys.platform != "darwin" or not sandbox.is_file():
        raise RuntimeError("local verifier requires the macOS sandbox")
    with (tempfile.TemporaryDirectory(prefix="fieldwork-verifier-") as directory,
          tempfile.NamedTemporaryFile(prefix="fieldwork-verifier-canary-", dir=Path.home()) as canary):
        canary.write(b"denied-read-probe")
        canary.flush()
        stage = Path(directory).resolve()
        staged_script = stage / "verifier.py"
        shutil.copyfile(CHILD_SCRIPT, staged_script)
        script_digest = hashlib.sha256(staged_script.read_bytes()).hexdigest()
        def quote(value: Path) -> str:
            return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

        executable = Path(sys.executable).resolve()
        runtime = Path(sys.prefix).resolve()
        if runtime == Path.home() or runtime == Path("/"):
            raise RuntimeError("local verifier Python runtime is too broad for sandboxing")
        package = execution_input.get('type') in {'package_applicability_v1', 'http_object_read_v1'}
        profile = "\n".join([
            "(version 1)", "(allow default)", "(deny process-fork)",
            "(deny network*)",
            *([] if package else ['(allow network-outbound (remote ip "localhost:*"))']),
            "(deny file-read* " + " ".join(
                f"(subpath {quote(path)})" for path in
                (Path.home().resolve(), Path("/Volumes"), Path("/private/tmp"), Path("/tmp"))) + ")",
            "(allow file-read* " + " ".join(
                f"(subpath {quote(path)})" for path in (runtime, executable.parent, stage)) + ")",
            "(deny file-write*)",
            f"(allow file-write* (subpath {quote(stage)}))",
        ])
        profile_digest = hashlib.sha256(profile.encode()).hexdigest()
        if check_current:
            check_current()
        process = subprocess.Popen(
            [str(sandbox), "-p", profile, str(executable), "-I", str(staged_script)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=directory,
            env={"PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8",
                 "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(stage), "TMPDIR": str(stage),
                 "FIELDWORK_DENIED_CANARY": canary.name},
        )
        try:
            deadline = time.monotonic() + 25
            first = True
            while True:
                if check_current:
                    check_current()
                if time.monotonic() >= deadline:
                    raise TimeoutError('isolated verifier timed out')
                try:
                    stdout, _ = process.communicate(encoded if first else None, timeout=.2)
                    if check_current:
                        check_current()
                    break
                except subprocess.TimeoutExpired:
                    first = False
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        if hashlib.sha256(staged_script.read_bytes()).hexdigest() != script_digest:
            raise ValueError("isolated verifier script changed during replay")
    if process.returncode != 0 or len(stdout) > 8192:
        raise ValueError("isolated verifier rejected replay contract")
    output = json.loads(stdout)
    if output.get("pid") != process.pid or output.get("ppid") != os.getpid() or process.pid == os.getpid():
        raise ValueError("isolated verifier process identity mismatch")
    isolation_probe = output.get("isolation_probe", {})
    if (isolation_probe.get("file_read_denied") is not True
            or (package and isolation_probe.get("network_denied") is not True)):
        raise ValueError("isolated verifier capability probe failed")
    result = VerificationResult.model_validate(output["result"])
    _validate_result(result)
    proof = {"parent_pid": os.getpid(), "child_pid": process.pid, "exit_code": process.returncode,
             "script_sha256": script_digest,
             "input_sha256": hashlib.sha256(encoded).hexdigest(),
             "output_sha256": hashlib.sha256(stdout).hexdigest(),
             "sandbox": "macos-seatbelt", "sandbox_profile_sha256": profile_digest,
             "file_read_denied": True, "network_denied": isolation_probe.get("network_denied"),
             "observed_by": "fieldwork_local_supervisor"}
    return result.model_dump(mode="json"), proof


@router.post("/local/tick")
def local_verifier_tick(limit: int = 1, request_id: str | None = None):
    if limit < 1 or limit > 4:
        raise HTTPException(422, "local verifier tick limit must be 1-4")
    completed = []
    for _ in range(limit):
        with _core().connect() as db:
            db.execute("BEGIN IMMEDIATE")
            selected = _claim_local_verifier(db, request_id)
            if not selected:
                return {"status": "idle", "completed": completed}
            task, request, execution_input = selected
            contract = _load(request["replay_contract_json"], {})
            evidence_ids = _load(request["input_node_ids_json"], [])
        try:
            check_current = lambda: _check_local_execution(task['id'], request['id'], execution_input)
            result, proof = _run_local_verifier(execution_input, check_current=check_current)
            check_current()
            package = contract.get('type') in {'package_applicability_v1', 'http_object_read_v1'}
            http_object = contract.get('type') == 'http_object_read_v1'
            receipt = _issue_receipt(task["id"], ReceiptIssue(
                runner_id=LOCAL_VERIFIER_ID,
                environment={"network": "none" if package else "loopback_http_only",
                             "oracle": contract.get("type")},
                result=VerificationResult.model_validate(result),
                evidence_ids=evidence_ids,
                limitations=(["Only the selected identity-bound object read and frozen business rule were checked; code root cause, severity and broader impact remain unproven."] if http_object else ["Only a hashed source-scan package/version and an explicit advisory version "
                              "were compared; deployment, runtime reachability, and exploitability are unproven."]
                             if package else
                             ["Only the declared loopback HTTP status relationship was replayed; "
                              "this does not establish a broader authorization vulnerability."]),
            ), process_attestation=proof)
            completed.append({"task_id": task["id"], "status": "succeeded", "receipt_id": receipt["id"]})
        except LocalVerificationStopped as error:
            with _core().connect() as db:
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT * FROM agent_tasks WHERE id=?', (task['id'],)).fetchone()
                if current and current['status'] == 'running' and current['lease_owner'] == LOCAL_VERIFIER_ID:
                    db.execute("UPDATE agent_tasks SET status=?,lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,error_json=?,updated_at=? WHERE id=?",
                               ('cancelled' if error.reason == 'cancelled' else 'failed',
                                _dump({'code': 'local_verifier_stopped', 'reason': error.reason}), _now(), task['id']))
                _sync_runner_jobs(db, LOCAL_VERIFIER_ID)
            completed.append({'task_id': task['id'], 'status': 'stopped', 'reason': error.reason})
        except Exception as error:
            with _core().connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute("SELECT * FROM agent_tasks WHERE id=?", (task["id"],)).fetchone()
                if current and current["status"] == "running" and current["lease_owner"] == LOCAL_VERIFIER_ID:
                    status = "queued" if current["attempt"] < current["max_attempts"] else "failed"
                    db.execute(
                        "UPDATE agent_tasks SET status=?,lease_owner=NULL,lease_expires_at=NULL,"
                        "heartbeat_at=NULL,error_json=?,updated_at=? WHERE id=?",
                        (status, _dump({"code": "local_verifier_failed", "error_type": type(error).__name__}),
                         _now(), task["id"]),
                    )
                    _sync_runner_jobs(db, LOCAL_VERIFIER_ID)
            completed.append({"task_id": task["id"], "status": "failed_attempt", "error_type": type(error).__name__})
    return {"status": "limit_reached", "completed": completed}


@router.get("/receipts/{receipt_id}")
def get_receipt(receipt_id: str):
    f = _core()
    with f.connect() as db:
        return _get_receipt_value(db, receipt_id)


@router.get("/receipts")
def list_receipts(campaign_id: str | None = None, limit: int = Query(100, ge=1, le=500),
                  offset: int = Query(0, ge=0)):
    f = _core()
    where, params = (" WHERE campaign_id=?", [campaign_id]) if campaign_id else ("", [])
    with f.connect() as db:
        total = db.execute("SELECT COUNT(*) FROM verification_receipts_v5" + where, params).fetchone()[0]
        rows = db.execute(
            "SELECT id FROM verification_receipts_v5" + where + " ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        items = [_get_receipt_value(db, row["id"]) for row in rows]
    return {"items": items, "page": {"limit": limit, "offset": offset, "total": total,
                                      "has_more": offset + len(items) < total}}


@router.post("/receipts/{receipt_id}/replay", status_code=202)
def replay_receipt(receipt_id: str):
    f = _core()
    with f.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row, payload, _ = _validated_receipt(db, receipt_id)
        claim = _claim(db, row["claim_node_id"], row["campaign_id"])
        request = db.execute(
            "SELECT * FROM verification_requests_v5 WHERE id=(SELECT request_id FROM verification_receipt_bindings_v5 WHERE receipt_id=?)",
            (receipt_id,),
        ).fetchone()
        body = VerifyRequest(campaign_id=row["campaign_id"], evidence_ids=_load(request["input_node_ids_json"], []),
                             replay_contract=payload["replay_contract"], priority=100)
        replay_request, task, created = _create_request_transaction(db, claim, body, receipt_id)
    return {"status": replay_request["status"], "created": created, "request": _request_value(replay_request, task)}


def validate_receipt_for_promotion(db: sqlite3.Connection, receipt_id: str, campaign_id: str,
                                   claim_node_id: str, expected_outcome: str | None,
                                   required_oracle_kind: str | None = None) -> dict[str, Any]:
    row, payload, binding = _validated_receipt(db, receipt_id)
    result = payload["result"]
    if row["campaign_id"] != campaign_id or row["claim_node_id"] != claim_node_id:
        raise HTTPException(409, "verification receipt is not bound to this campaign and claim")
    if result.get("status") not in {"verified", "refuted"} or result.get("interrupted"):
        raise HTTPException(409, "inconclusive or interrupted verification cannot become canonical")
    if not _process_observed(payload):
        raise HTTPException(409, "canonical promotion requires supervisor-observed independent verification")
    if not _inputs_current(db, binding):
        raise HTTPException(409, "verification inputs changed after receipt issuance")
    if payload["replay_contract"].get("type") == "loopback_http_status_v1":
        claim = _claim(db, claim_node_id, campaign_id)
        attributes = _load(claim["attributes_json"], {})
        if (attributes.get("claim_kind") != "http_status_relationship"
                or attributes.get("verification_contract_sha256") != _sha(payload["replay_contract"])):
            raise HTTPException(409, "loopback status receipt is not bound to the claim oracle")
    if payload["replay_contract"].get("type") == "package_applicability_v1":
        if required_oracle_kind != "package_applicability_v1":
            raise HTTPException(409, "package applicability alone cannot become a canonical finding")
        request = db.execute("SELECT * FROM verification_requests_v5 WHERE id=?", (binding["request_id"],)).fetchone()
        current_input = _package_replay_input(db, request) if request else None
        oracle = result.get("oracle", {})
        fingerprint = current_input.get("fingerprint", {}) if current_input else {}
        if (not current_input or oracle.get("kind") != "package_applicability_v1"
                or oracle.get("source_artifact_sha256") != current_input["source_artifact_sha256"]
                or oracle.get("advisory_record_sha256") != current_input["advisory_record_sha256"]
                or oracle.get("exact_affected_version_listed") is not True
                or oracle.get("observed_versions") != [fingerprint.get("version")]
                or any(oracle.get(key) != fingerprint.get(key) for key in ("ecosystem", "name", "version"))):
            raise HTTPException(409, "package applicability receipt does not match current source evidence")
    if payload['replay_contract'].get('type') == 'http_object_read_v1':
        request = db.execute('SELECT * FROM verification_requests_v5 WHERE id=?', (binding['request_id'],)).fetchone()
        if not _http_receipt_matches(db, request, result):
            raise HTTPException(409, 'HTTP receipt does not match current supervised evidence')
    if required_oracle_kind and (result.get("oracle", {}).get("kind") != required_oracle_kind
                                 or payload["replay_contract"].get("type") != required_oracle_kind):
        raise HTTPException(409, "verification receipt oracle does not match the required domain")
    if expected_outcome != result.get("status"):
        raise HTTPException(409, "canonical result must declare the receipt verification_outcome")
    return payload


def _http_receipt_matches(db, request, result):
    from v5_http_receipts import replay_input
    current = replay_input(db, request) if request else None
    oracle = result.get('oracle', {})
    return bool(current and oracle.get('kind') == 'http_object_read_v1'
                and all(oracle.get(key) == current[key] for key in ('source_artifact_sha256', 'scope_sha256', 'rule_sha256')))


class HttpFindingPromotion(BaseModel):
    authorized: Literal[True]
    source_fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')


@router.get('/receipts/{receipt_id}/http-finding-plan')
def http_finding_plan(receipt_id: str):
    from v5_http_receipts import finding_plan
    return finding_plan(receipt_id)


@router.post('/receipts/{receipt_id}/promote-http')
def promote_http_finding(receipt_id: str, body: HttpFindingPromotion):
    from v5_http_receipts import promote_finding
    return promote_finding(receipt_id, body.source_fingerprint)


@router.get('/receipts/{receipt_id}/http-fixed-plan')
def http_fixed_plan(receipt_id: str):
    from v5_http_receipts import fixed_plan
    return fixed_plan(receipt_id)


@router.post('/receipts/{receipt_id}/confirm-http-fixed')
def confirm_http_fixed(receipt_id: str, body: HttpFindingPromotion):
    from v5_http_receipts import confirm_fixed
    return confirm_fixed(receipt_id, body.source_fingerprint)


def node_is_receipt_locked(db: sqlite3.Connection, node_id: str) -> bool:
    if db.execute("SELECT 1 FROM verification_receipts_v5 WHERE claim_node_id=? LIMIT 1", (node_id,)).fetchone():
        return True
    requests = db.execute(
        "SELECT input_node_ids_json FROM verification_requests_v5 WHERE status='issued'",
    ).fetchall()
    if any(node_id in _load(row["input_node_ids_json"], []) for row in requests):
        return True
    receipts = db.execute("SELECT evidence_ids_json FROM verification_receipts_v5").fetchall()
    return any(node_id in _load(row["evidence_ids_json"], []) for row in receipts)
