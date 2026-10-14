"""V5 Research Graph repository, API, and explicit legacy provenance bridge."""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field


router = APIRouter(prefix="/api/v1/research", tags=["V5 Research Graph"])

NODE_TYPES = {
    "question", "hypothesis", "claim", "observation", "evidence", "counterevidence",
    "failure", "open_question", "experiment", "verification", "canonical_result",
    "artifact", "entity",
}
EDGE_TYPES = {
    "decomposes_to", "supports", "contradicts", "derived_from", "tested_by", "failed_by",
    "depends_on", "supersedes", "verified_by", "invalidated_by", "related_to", "produced_by",
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


def _stable_node_id(campaign_id: str, source_type: str, source_ref: str) -> str:
    digest = hashlib.sha256(f"{campaign_id}\0{source_type}\0{source_ref}".encode()).hexdigest()[:24]
    return f"rnode-{digest}"


def _stable_edge_id(campaign_id: str, source_id: str, target_id: str, relation: str) -> str:
    digest = hashlib.sha256(f"{campaign_id}\0{source_id}\0{target_id}\0{relation}".encode()).hexdigest()[:24]
    return f"redge-{digest}"


def _campaign(db: sqlite3.Connection, campaign_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM research_campaigns WHERE id=?", (campaign_id,)).fetchone()
    if not row:
        raise HTTPException(404, "research campaign not found")
    return row


def _node(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["attributes"] = _load(value.pop("attributes_json"), {})
    return value


def _node_with_trust(db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    value = _node(row)
    if row["node_type"] == "canonical_result":
        from v5_verification import validate_receipt_for_promotion
        attributes = value["attributes"]
        try:
            validate_receipt_for_promotion(
                db, attributes.get("verification_receipt_id", ""), row["campaign_id"],
                row["source_ref"], attributes.get("verification_outcome"),
            )
            current = True
        except HTTPException:
            current = False
        value["canonical_trust"] = {"current": current, "basis": "independent_verification_receipt"}
    return value


def _edge(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["attributes"] = _load(value.pop("attributes_json"), {})
    return value


def _emit(db: sqlite3.Connection, campaign_id: str, entity_id: str, event_type: str, payload: dict[str, Any]) -> None:
    db.execute(
        "INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)",
        ("research", campaign_id, entity_id, event_type, _dump(payload), _now()),
    )


def _canonical_guard(db: sqlite3.Connection, campaign_id: str, source_ref: str | None,
                     attributes: dict[str, Any]) -> None:
    receipt_id = attributes.get("verification_receipt_id")
    if not receipt_id or not source_ref:
        raise HTTPException(409, "canonical_result requires a source claim and verification_receipt_id")
    claim = db.execute(
        "SELECT node_type,campaign_id FROM research_nodes WHERE id=?", (source_ref,),
    ).fetchone()
    if not claim or claim["campaign_id"] != campaign_id or claim["node_type"] != "claim":
        raise HTTPException(409, "canonical_result source_ref must identify a claim in the same campaign")
    from v5_verification import validate_receipt_for_promotion
    try:
        validate_receipt_for_promotion(
            db, receipt_id, campaign_id, source_ref, attributes.get("verification_outcome"),
        )
    except HTTPException as error:
        if error.status_code == 404:
            raise HTTPException(409, "canonical_result requires a valid immutable verification receipt") from error
        raise


class NodeCreate(BaseModel):
    campaign_id: str = Field(min_length=1, max_length=200)
    run_id: str | None = Field(default=None, max_length=200)
    node_type: Literal[
        "question", "hypothesis", "claim", "observation", "evidence", "counterevidence",
        "failure", "open_question", "experiment", "verification", "canonical_result",
        "artifact", "entity",
    ]
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(default="", max_length=100_000)
    status: str = Field(default="active", min_length=1, max_length=64)
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_type: str = Field(default="user", min_length=1, max_length=64)
    source_ref: str | None = Field(default=None, max_length=500)
    attributes: dict[str, Any] = Field(default_factory=dict)


class NodeUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = Field(default=None, max_length=100_000)
    status: str | None = Field(default=None, min_length=1, max_length=64)
    confidence: float | None = Field(default=None, ge=0, le=1)
    attributes: dict[str, Any] | None = None


class EdgeCreate(BaseModel):
    campaign_id: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1, max_length=200)
    target_id: str = Field(min_length=1, max_length=200)
    relation_type: Literal[
        "decomposes_to", "supports", "contradicts", "derived_from", "tested_by", "failed_by",
        "depends_on", "supersedes", "verified_by", "invalidated_by", "related_to", "produced_by",
    ]
    attributes: dict[str, Any] = Field(default_factory=dict)


class EdgeUpdate(BaseModel):
    attributes: dict[str, Any]


@router.post("/nodes", status_code=201)
def create_node(body: NodeCreate):
    f = _core()
    now, node_id = _now(), _uid("rnode")
    with f.connect() as db:
        _campaign(db, body.campaign_id)
        if body.run_id:
            run = db.execute("SELECT engagement_id FROM analysis_runs WHERE id=?", (body.run_id,)).fetchone()
            campaign = _campaign(db, body.campaign_id)
            if not run or run["engagement_id"] != campaign["engagement_id"]:
                raise HTTPException(409, "run does not belong to the campaign engagement")
        if body.node_type == "canonical_result":
            _canonical_guard(db, body.campaign_id, body.source_ref, body.attributes)
        db.execute(
            "INSERT INTO research_nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (node_id, body.campaign_id, body.run_id, body.node_type, body.title, body.body,
             body.status, body.confidence, body.source_type, body.source_ref,
             _dump(body.attributes), now, now),
        )
        _emit(db, body.campaign_id, node_id, "node.created", {"node_type": body.node_type})
    return get_node(node_id)


@router.get("/nodes/{node_id}")
def get_node(node_id: str):
    f = _core()
    with f.connect() as db:
        row = db.execute("SELECT * FROM research_nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            raise HTTPException(404, "research node not found")
        return _node_with_trust(db, row)


@router.patch("/nodes/{node_id}")
def update_node(node_id: str, body: NodeUpdate):
    f = _core()
    values = body.model_dump(exclude_unset=True)
    with f.connect() as db:
        row = db.execute("SELECT * FROM research_nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            raise HTTPException(404, "research node not found")
        from v5_verification import node_is_receipt_locked
        if row["node_type"] == "canonical_result" or node_is_receipt_locked(db, node_id):
            raise HTTPException(409, "receipt-bound graph records are immutable")
        attributes = values.get("attributes", _load(row["attributes_json"], {}))
        if row["node_type"] == "canonical_result":
            _canonical_guard(db, row["campaign_id"], row["source_ref"], attributes)
        assignments, parameters = [], []
        for key, value in values.items():
            column = "attributes_json" if key == "attributes" else key
            assignments.append(f"{column}=?")
            parameters.append(_dump(value) if key == "attributes" else value)
        if assignments:
            assignments.append("updated_at=?")
            parameters.extend([_now(), node_id])
            db.execute(f"UPDATE research_nodes SET {','.join(assignments)} WHERE id=?", parameters)
            _emit(db, row["campaign_id"], node_id, "node.updated", {"fields": sorted(values)})
    return get_node(node_id)


@router.delete("/nodes/{node_id}", status_code=204)
def delete_node(node_id: str):
    f = _core()
    with f.connect() as db:
        row = db.execute("SELECT * FROM research_nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            raise HTTPException(404, "research node not found")
        if db.execute("SELECT 1 FROM research_edges WHERE source_id=? OR target_id=? LIMIT 1", (node_id, node_id)).fetchone():
            raise HTTPException(409, "delete graph edges before deleting this node")
        from v5_verification import node_is_receipt_locked
        if row["node_type"] == "canonical_result" or node_is_receipt_locked(db, node_id):
            raise HTTPException(409, "verified graph records are immutable")
        db.execute("DELETE FROM research_nodes WHERE id=?", (node_id,))
        _emit(db, row["campaign_id"], node_id, "node.deleted", {"node_type": row["node_type"]})


@router.post("/edges", status_code=201)
def create_edge(body: EdgeCreate):
    if body.source_id == body.target_id:
        raise HTTPException(422, "self edges are not supported")
    f = _core()
    with f.connect() as db:
        _campaign(db, body.campaign_id)
        rows = db.execute(
            "SELECT id,campaign_id FROM research_nodes WHERE id IN (?,?)", (body.source_id, body.target_id),
        ).fetchall()
        if len(rows) != 2:
            raise HTTPException(404, "source or target node not found")
        if any(row["campaign_id"] != body.campaign_id for row in rows):
            raise HTTPException(409, "cross-campaign edges are not allowed")
        edge_id = _uid("redge")
        try:
            db.execute(
                "INSERT INTO research_edges VALUES(?,?,?,?,?,?,?)",
                (edge_id, body.campaign_id, body.source_id, body.target_id,
                 body.relation_type, _dump(body.attributes), _now()),
            )
            _emit(db, body.campaign_id, edge_id, "edge.created", {"relation_type": body.relation_type})
        except sqlite3.IntegrityError:
            row = db.execute(
                "SELECT * FROM research_edges WHERE campaign_id=? AND source_id=? AND target_id=? AND relation_type=?",
                (body.campaign_id, body.source_id, body.target_id, body.relation_type),
            ).fetchone()
            if row:
                return _edge(row)
            raise
    return get_edge(edge_id)


@router.get("/edges/{edge_id}")
def get_edge(edge_id: str):
    f = _core()
    with f.connect() as db:
        row = db.execute("SELECT * FROM research_edges WHERE id=?", (edge_id,)).fetchone()
    if not row:
        raise HTTPException(404, "research edge not found")
    return _edge(row)


@router.patch("/edges/{edge_id}")
def update_edge(edge_id: str, body: EdgeUpdate):
    f = _core()
    with f.connect() as db:
        row = db.execute("SELECT campaign_id FROM research_edges WHERE id=?", (edge_id,)).fetchone()
        if not row:
            raise HTTPException(404, "research edge not found")
        db.execute("UPDATE research_edges SET attributes_json=? WHERE id=?", (_dump(body.attributes), edge_id))
        _emit(db, row["campaign_id"], edge_id, "edge.updated", {"fields": ["attributes"]})
    return get_edge(edge_id)


@router.delete("/edges/{edge_id}", status_code=204)
def delete_edge(edge_id: str):
    f = _core()
    with f.connect() as db:
        row = db.execute("SELECT campaign_id,relation_type FROM research_edges WHERE id=?", (edge_id,)).fetchone()
        if not row:
            raise HTTPException(404, "research edge not found")
        db.execute("DELETE FROM research_edges WHERE id=?", (edge_id,))
        _emit(db, row["campaign_id"], edge_id, "edge.deleted", {"relation_type": row["relation_type"]})


@router.get("/campaigns/{campaign_id}/graph/page")
def get_graph_page(campaign_id: str, limit: int = Query(default=200, ge=1, le=1000),
                   cursor: str | None = Query(default=None, max_length=4096)):
    """Page nodes and edges independently so cross-page relations are retained.

    Each request is a read snapshot; the traversal is a live view, not a
    cross-request snapshot. Refresh to include new records before a cursor.
    """
    markers, done = [None, None], [False, False]
    if cursor:
        try:
            value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if (not isinstance(value, list) or len(value) != 5 or value[0] != campaign_id
                    or any(type(flag) is not bool for flag in value[3:])
                    or any(marker is not None and (not isinstance(marker, list) or len(marker) != 2
                           or any(not isinstance(part, str) for part in marker)) for marker in value[1:3])):
                raise ValueError("invalid graph cursor")
            markers, done = value[1:3], value[3:]
        except (ValueError, TypeError, RecursionError):
            raise HTTPException(422, "invalid cursor for this campaign") from None
    rows_by_kind = []
    with _core().connect() as db:
        db.execute("BEGIN")
        _campaign(db, campaign_id)
        for index, table in enumerate(("research_nodes", "research_edges")):
            rows = []
            if not done[index]:
                clause, params = "", [campaign_id]
                if markers[index] is not None:
                    clause = " AND (created_at,id)>(?,?)"
                    params.extend(markers[index])
                rows = db.execute(
                    f"SELECT * FROM {table} WHERE campaign_id=?{clause} ORDER BY created_at,id LIMIT ?",
                    (*params, limit + 1),
                ).fetchall()
                done[index] = len(rows) <= limit
                rows = rows[:limit]
                if rows:
                    markers[index] = [rows[-1]["created_at"], rows[-1]["id"]]
            rows_by_kind.append(rows)
        nodes = [_node_with_trust(db, row) for row in rows_by_kind[0]]
    has_more = not all(done)
    next_cursor = base64.urlsafe_b64encode(_dump([campaign_id, *markers, *done]).encode()).decode() if has_more else None
    return {"campaign_id": campaign_id, "nodes": nodes,
            "edges": [_edge(row) for row in rows_by_kind[1]],
            "page": {"limit": limit, "has_more": has_more, "next_cursor": next_cursor,
                     "nodes_complete": done[0], "edges_complete": done[1]}}


@router.get("/campaigns/{campaign_id}/graph")
def get_graph(campaign_id: str, limit: int = Query(default=200, ge=1, le=1000),
              offset: int = Query(default=0, ge=0)):
    f = _core()
    with f.connect() as db:
        _campaign(db, campaign_id)
        total_nodes = db.execute("SELECT COUNT(*) FROM research_nodes WHERE campaign_id=?", (campaign_id,)).fetchone()[0]
        total_edges = db.execute("SELECT COUNT(*) FROM research_edges WHERE campaign_id=?", (campaign_id,)).fetchone()[0]
        node_rows = db.execute(
            "SELECT * FROM research_nodes WHERE campaign_id=? ORDER BY created_at,id LIMIT ? OFFSET ?",
            (campaign_id, limit, offset),
        ).fetchall()
        ids = [row["id"] for row in node_rows]
        edge_rows = []
        if ids:
            placeholders = ",".join("?" for _ in ids)
            edge_rows = db.execute(
                f"SELECT * FROM research_edges WHERE campaign_id=? AND source_id IN ({placeholders}) "
                f"AND target_id IN ({placeholders}) ORDER BY created_at,id",
                (campaign_id, *ids, *ids),
            ).fetchall()
        nodes = [_node_with_trust(db, row) for row in node_rows]
    return {
        "campaign_id": campaign_id,
        "nodes": nodes,
        "edges": [_edge(row) for row in edge_rows],
        "page": {"limit": limit, "offset": offset, "total_nodes": total_nodes, "total_edges": total_edges,
                 "has_more": offset + len(node_rows) < total_nodes},
    }


def _bridge_node(db: sqlite3.Connection, campaign_id: str, *, source_type: str, source_ref: str,
                 node_type: str, title: str, body: str, status: str, run_id: str | None = None,
                 confidence: float | None = None, attributes: dict[str, Any] | None = None) -> tuple[str, bool]:
    node_id = _stable_node_id(campaign_id, source_type, source_ref)
    now = _now()
    cursor = db.execute(
        "INSERT OR IGNORE INTO research_nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (node_id, campaign_id, run_id, node_type, title[:500], body[:100_000], status[:64], confidence,
         source_type, source_ref, _dump(attributes or {}), now, now),
    )
    return node_id, bool(cursor.rowcount)


def _bridge_edge(db: sqlite3.Connection, campaign_id: str, source_id: str, target_id: str,
                 relation_type: str, attributes: dict[str, Any] | None = None) -> bool:
    if source_id == target_id:
        return False
    edge_id = _stable_edge_id(campaign_id, source_id, target_id, relation_type)
    cursor = db.execute(
        "INSERT OR IGNORE INTO research_edges VALUES(?,?,?,?,?,?,?)",
        (edge_id, campaign_id, source_id, target_id, relation_type, _dump(attributes or {}), _now()),
    )
    return bool(cursor.rowcount)


def project_traditional_tool_result(db: sqlite3.Connection, engagement_id: str, run_id: str,
                                    artifact_id: str, observation_ids: list[str]) -> dict[str, int]:
    """Project persisted Traditional facts into active campaigns in the same transaction."""
    campaigns = db.execute(
        "SELECT id FROM research_campaigns WHERE engagement_id=? AND status='active' ORDER BY id",
        (engagement_id,),
    ).fetchall()
    artifact = db.execute(
        "SELECT * FROM artifacts WHERE id=? AND run_id=?", (artifact_id, run_id),
    ).fetchone()
    if not artifact:
        raise ValueError("traditional tool artifact is missing from the current run")
    observations = []
    for observation_id in observation_ids:
        row = db.execute(
            "SELECT * FROM observations WHERE id=? AND run_id=? AND engagement_id=? AND mode='traditional'",
            (observation_id, run_id, engagement_id),
        ).fetchone()
        if not row:
            raise ValueError("traditional observation is missing from the current run")
        observations.append(row)
    created_nodes = created_edges = 0
    for campaign in campaigns:
        campaign_id = campaign["id"]
        campaign_nodes = campaign_edges = 0
        artifact_node, created = _bridge_node(
            db, campaign_id, source_type="artifact", source_ref=artifact_id,
            node_type="artifact", title=f"Artifact · {artifact['kind']}",
            body="Binary content is not copied into Research Graph.", status="recorded", run_id=run_id,
            attributes={"sha256": artifact["sha256"], "media_type": artifact["media_type"],
                        "redacted": bool(artifact["redacted"]), "content_copied": False},
        )
        created_nodes += int(created)
        campaign_nodes += int(created)
        for row in observations:
            observation_node, created = _bridge_node(
                db, campaign_id, source_type="observation", source_ref=row["id"],
                node_type="observation", title=row["summary"], body=row["subject"],
                status="observed", run_id=run_id, confidence=row["confidence"],
                attributes={"mode": row["mode"], "observation_type": row["observation_type"],
                            "source_capability": row["source_capability"], "artifact_ref": artifact_id},
            )
            created_nodes += int(created)
            campaign_nodes += int(created)
            edge_created = _bridge_edge(
                db, campaign_id, observation_node, artifact_node, "derived_from",
                {"source": "traditional_tool_result"},
            )
            created_edges += int(edge_created)
            campaign_edges += int(edge_created)
        if campaign_nodes or campaign_edges:
            _emit(db, campaign_id, run_id, "traditional.projected",
                  {"artifact_id": artifact_id, "observation_count": len(observations)})
    return {"nodes": created_nodes, "edges": created_edges}


def project_web3_run_facts(db: sqlite3.Connection, engagement_id: str, run_id: str,
                           artifact_id: str, observation_ids: list[str],
                           deployment_context: dict[str, Any] | None = None) -> dict[str, int]:
    """Map persisted Web3 AST/Forge observations and evidence into active Graphs."""
    campaigns = db.execute(
        "SELECT id FROM research_campaigns WHERE engagement_id=? AND status='active' ORDER BY id",
        (engagement_id,),
    ).fetchall()
    artifact = db.execute("SELECT * FROM artifacts WHERE id=? AND run_id=?", (artifact_id, run_id)).fetchone()
    if not artifact:
        raise ValueError("Web3 artifact is missing from the current run")
    observations = []
    for observation_id in dict.fromkeys(observation_ids):
        row = db.execute(
            "SELECT * FROM observations WHERE id=? AND engagement_id=? AND run_id=? AND mode='web3'",
            (observation_id, engagement_id, run_id),
        ).fetchone()
        if not row or row["raw_ref"] != artifact_id:
            raise ValueError("Web3 observation is not bound to the current artifact and run")
        observations.append(row)
    snapshot_context = {}
    for snapshot in db.execute(
        "SELECT rules FROM program_snapshots WHERE engagement_id=? ORDER BY version DESC",
        (engagement_id,),
    ):
        rules = _load(snapshot["rules"], {})
        if rules.get("kind") == "deployment_alignment":
            snapshot_context = rules
            break
    context_input = {**snapshot_context,
                     **{key: value for key, value in (deployment_context or {}).items() if value is not None}}
    context = {key: value for key, value in context_input.items() if key in {
        "chain_id", "block_number", "contract_address", "implementation_address",
        "comparison_address", "source_commit", "deployed_address", "deployed_bytecode_hash",
        "local_runtime_sha256", "chain_runtime_sha256", "runtime_bytecode_match", "status",
    } and value is not None}
    created_nodes = created_edges = 0
    for campaign in campaigns:
        cid = campaign["id"]
        artifact_node, made = _bridge_node(
            db, cid, source_type="artifact", source_ref=artifact_id, node_type="artifact",
            title=f"Artifact · {artifact['kind']}", body="Binary content is not copied into Research Graph.",
            status="recorded", run_id=run_id,
            attributes={"sha256": artifact["sha256"], "media_type": artifact["media_type"],
                        "redacted": bool(artifact["redacted"]), "content_copied": False,
                        "deployment_context": context},
        )
        created_nodes += int(made)
        for row in observations:
            observation_node, made = _bridge_node(
                db, cid, source_type="observation", source_ref=row["id"], node_type="observation",
                title=row["summary"], body=row["subject"], status="observed", run_id=run_id,
                confidence=row["confidence"],
                attributes={"mode": "web3", "observation_type": row["observation_type"],
                            "source_capability": row["source_capability"], "deployment_context": context},
            )
            created_nodes += int(made)
            created_edges += int(_bridge_edge(db, cid, observation_node, artifact_node, "derived_from"))
            evidence_rows = db.execute(
                "SELECT * FROM evidence_v2 WHERE observation_id=? AND run_id=? ORDER BY id",
                (row["id"], run_id),
            ).fetchall()
            for evidence in evidence_rows:
                polarity = str(evidence["polarity"]).lower()
                kind = "counterevidence" if polarity in {"negative", "contradicts", "counterevidence", "counter"} else "evidence"
                evidence_node, made = _bridge_node(
                    db, cid, source_type="evidence_v2", source_ref=evidence["id"], node_type=kind,
                    title=evidence["summary"], body="", status="recorded", run_id=run_id,
                    attributes={"evidence_type": evidence["evidence_type"], "polarity": evidence["polarity"],
                                "observation_id": row["id"], "artifact_id": artifact_id,
                                "deployment_context": context},
                )
                created_nodes += int(made)
                created_edges += int(_bridge_edge(db, cid, evidence_node, observation_node, "derived_from"))
                created_edges += int(_bridge_edge(db, cid, evidence_node, artifact_node, "derived_from"))
    return {"nodes": created_nodes, "edges": created_edges}


def project_agent_audit_facts(db: sqlite3.Connection, audit_id: str, run_id: str,
                              reconciliation: dict[str, Any] | None = None) -> dict[str, int]:
    """Project audit provenance and deterministic claim contradictions without raw telemetry."""
    audit = db.execute("SELECT id FROM agent_audits WHERE id=? AND run_id=?", (audit_id, run_id)).fetchone()
    if not audit:
        raise ValueError("Agent audit does not match the run")
    campaigns = db.execute(
        "SELECT id FROM research_campaigns WHERE engagement_id=? AND status='active' ORDER BY id", (audit_id,),
    ).fetchall()
    events = db.execute("SELECT * FROM agent_events WHERE audit_id=? ORDER BY id", (audit_id,)).fetchall()
    claims = db.execute("SELECT * FROM agent_claims WHERE audit_id=? ORDER BY id", (audit_id,)).fetchall()
    claim_observations = {
        row["raw_ref"]: row for row in db.execute(
            "SELECT * FROM observations WHERE engagement_id=? AND run_id=? AND mode='agent_audit' AND observation_type='self_report'",
            (audit_id, run_id),
        )
    }
    created_nodes = created_edges = 0
    for campaign in campaigns:
        cid = campaign["id"]
        event_nodes = {}
        for event in events:
            observation = db.execute(
                "SELECT * FROM observations WHERE id=? AND engagement_id=? AND run_id=? AND mode='agent_audit' AND raw_ref=?",
                (event["observation_id"], audit_id, run_id, event["artifact_id"]),
            ).fetchone()
            evidence = db.execute(
                "SELECT * FROM evidence_v2 WHERE id=? AND observation_id=? AND run_id=? AND artifact_id=?",
                (event["evidence_id"], event["observation_id"], run_id, event["artifact_id"]),
            ).fetchone()
            artifact = db.execute("SELECT * FROM artifacts WHERE id=? AND run_id=?", (event["artifact_id"], run_id)).fetchone()
            if not observation or not evidence or not artifact:
                raise ValueError("Agent audit telemetry provenance is incomplete")
            details = _load(event["event_json"], {})
            attrs = {"source_type": observation["observation_type"], "action_type": details.get("action_type"),
                     "trust_level": details.get("trust_level"), "independent": bool(details.get("independent")),
                     "authenticity_verified": bool(details.get("authenticity_verified")),
                     "raw_content_copied": False}
            artifact_node, made = _bridge_node(
                db, cid, source_type="artifact", source_ref=artifact["id"], node_type="artifact",
                title="Artifact · agent_event", body="Raw telemetry is not copied into Research Graph.",
                status="recorded", run_id=run_id,
                attributes={"sha256": artifact["sha256"], "redacted": bool(artifact["redacted"]), "content_copied": False},
            )
            created_nodes += int(made)
            observation_node, made = _bridge_node(
                db, cid, source_type="observation", source_ref=observation["id"], node_type="observation",
                title=f"Agent telemetry · {observation['observation_type']}", body="", status="observed",
                run_id=run_id, confidence=observation["confidence"], attributes=attrs,
            )
            created_nodes += int(made)
            created_edges += int(_bridge_edge(db, cid, observation_node, artifact_node, "derived_from"))
            evidence_node, made = _bridge_node(
                db, cid, source_type="evidence_v2", source_ref=evidence["id"], node_type="evidence",
                title="Agent telemetry evidence", body="", status="recorded", run_id=run_id,
                attributes={"polarity": evidence["polarity"], "trust_level": attrs["trust_level"],
                            "authenticity_verified": attrs["authenticity_verified"], "raw_content_copied": False},
            )
            created_nodes += int(made)
            created_edges += int(_bridge_edge(db, cid, evidence_node, observation_node, "derived_from"))
            created_edges += int(_bridge_edge(db, cid, evidence_node, artifact_node, "derived_from"))
            event_nodes[event["id"]] = evidence_node
        claim_nodes = {}
        for claim in claims:
            observation = claim_observations.get(claim["artifact_id"])
            artifact = db.execute("SELECT * FROM artifacts WHERE id=? AND run_id=?", (claim["artifact_id"], run_id)).fetchone()
            if not observation or not artifact:
                raise ValueError("Agent audit self-report provenance is incomplete")
            artifact_node, made = _bridge_node(
                db, cid, source_type="artifact", source_ref=artifact["id"], node_type="artifact",
                title="Artifact · agent_self_report", body="Self-report text is not copied into Research Graph.",
                status="recorded", run_id=run_id,
                attributes={"sha256": artifact["sha256"], "redacted": bool(artifact["redacted"]), "content_copied": False},
            )
            created_nodes += int(made)
            claim_node, made = _bridge_node(
                db, cid, source_type="agent_claim", source_ref=claim["id"], node_type="claim",
                title="Agent self-report claim", body="", status="unverified", run_id=run_id,
                attributes={"origin": "self_report", "trusted_as_evidence": False, "raw_content_copied": False},
            )
            created_nodes += int(made)
            created_edges += int(_bridge_edge(db, cid, claim_node, artifact_node, "derived_from"))
            claim_nodes[claim["id"]] = claim_node
        for row in (reconciliation or {}).get("rows", []):
            if row.get("status") != "CONTRADICTED" or row.get("claim_id") not in claim_nodes:
                continue
            for event_id in row.get("event_ids", []):
                if source := event_nodes.get(event_id):
                    created_edges += int(_bridge_edge(db, cid, source, claim_nodes[row["claim_id"]], "contradicts",
                                                      {"basis": "deterministic_reconciliation"}))
    return {"nodes": created_nodes, "edges": created_edges}


@router.post("/campaigns/{campaign_id}/bridge")
def bridge_campaign(campaign_id: str):
    """Explicit, idempotent projection. It never promotes or executes legacy records."""
    f = _core()
    created_nodes = created_edges = 0
    refs: dict[tuple[str, str], str] = {}
    with f.connect() as db:
        campaign = _campaign(db, campaign_id)
        engagement_id = campaign["engagement_id"]

        def add(**kwargs) -> str:
            nonlocal created_nodes
            node_id, created = _bridge_node(db, campaign_id, **kwargs)
            refs[(kwargs["source_type"], kwargs["source_ref"])] = node_id
            created_nodes += int(created)
            return node_id

        for row in db.execute("SELECT * FROM entities WHERE engagement_id=? ORDER BY created_at,id", (engagement_id,)):
            add(source_type="entity", source_ref=row["id"], node_type="entity", title=row["label"], body="",
                status="observed", attributes={"entity_type": row["entity_type"], "canonical_key": row["canonical_key"]})
        run_ids = [row[0] for row in db.execute("SELECT id FROM analysis_runs WHERE engagement_id=?", (engagement_id,))]
        for row in db.execute("SELECT * FROM observations WHERE engagement_id=? ORDER BY created_at,id", (engagement_id,)):
            add(source_type="observation", source_ref=row["id"], node_type="observation", title=row["summary"],
                body=row["subject"], status="observed", run_id=row["run_id"], confidence=row["confidence"],
                attributes={"mode": row["mode"], "observation_type": row["observation_type"],
                            "source_capability": row["source_capability"]})
        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            for row in db.execute(f"SELECT * FROM artifacts WHERE run_id IN ({placeholders}) ORDER BY created_at,id", run_ids):
                add(source_type="artifact", source_ref=row["id"], node_type="artifact", title=f"Artifact · {row['kind']}",
                    body="Binary content is not copied into Research Graph.", status="recorded", run_id=row["run_id"],
                    attributes={"sha256": row["sha256"], "media_type": row["media_type"],
                                "redacted": bool(row["redacted"]), "content_copied": False})
            for row in db.execute(f"SELECT * FROM evidence_v2 WHERE run_id IN ({placeholders}) ORDER BY created_at,id", run_ids):
                polarity = str(row["polarity"]).lower()
                node_type = "counterevidence" if polarity in {"negative", "contradicts", "counterevidence"} else "evidence"
                evidence_id = add(source_type="evidence_v2", source_ref=row["id"], node_type=node_type,
                    title=row["summary"], body="", status="recorded", run_id=row["run_id"],
                    attributes={"evidence_type": row["evidence_type"], "polarity": row["polarity"],
                                "observation_id": row["observation_id"], "artifact_id": row["artifact_id"]})
                if row["observation_id"] and (target := refs.get(("observation", row["observation_id"]))):
                    created_edges += int(_bridge_edge(db, campaign_id, evidence_id, target, "derived_from"))
                if row["artifact_id"] and (target := refs.get(("artifact", row["artifact_id"]))):
                    created_edges += int(_bridge_edge(db, campaign_id, evidence_id, target, "derived_from"))
        for row in db.execute("SELECT * FROM research_hypotheses WHERE campaign_id=? ORDER BY created_at,id", (campaign_id,)):
            node_id = add(source_type="research_hypothesis", source_ref=row["id"], node_type="hypothesis",
                title=row["statement"], body=row["next_action"], status=row["status"],
                attributes={"category": row["category"], "priority": row["priority"], "attempts": row["attempts"]})
            for evidence_id in _load(row["evidence_ids"], []):
                if source := refs.get(("evidence_v2", evidence_id)):
                    created_edges += int(_bridge_edge(db, campaign_id, source, node_id, "supports"))
            for evidence_id in _load(row["counterevidence_ids"], []):
                if source := refs.get(("evidence_v2", evidence_id)):
                    created_edges += int(_bridge_edge(db, campaign_id, source, node_id, "contradicts"))
        linked_candidates = [row[0] for row in db.execute(
            "SELECT candidate_id FROM campaign_candidate_links WHERE campaign_id=?", (campaign_id,),
        )]
        if linked_candidates:
            placeholders = ",".join("?" for _ in linked_candidates)
            candidates = db.execute(
                f"SELECT * FROM candidate_findings WHERE id IN ({placeholders}) ORDER BY created_at,id", linked_candidates,
            ).fetchall()
        else:
            candidates = []
        for row in candidates:
            claim_id = add(source_type="candidate_finding", source_ref=row["id"], node_type="claim", title=row["title"],
                body=row["hypothesis"], status=row["status"], run_id=row["run_id"],
                attributes={"category": row["category"], "target": row["target"], "legacy_candidate": True})
            for evidence_id in _load(row["evidence_ids"], []):
                if source := refs.get(("evidence_v2", evidence_id)):
                    created_edges += int(_bridge_edge(db, campaign_id, source, claim_id, "supports"))
        for candidate_id in linked_candidates:
            finding = db.execute("SELECT * FROM canonical_findings WHERE candidate_id=?", (candidate_id,)).fetchone()
            if not finding:
                continue
            # Preserve provenance without creating a canonical_result. Historical
            # verification is not a V5 receipt and cannot cross the new trust gate.
            legacy_id = add(source_type="canonical_finding", source_ref=finding["id"], node_type="claim",
                title=finding["title"], body=finding["impact"], status="legacy_verified_claim",
                attributes={"severity": finding["severity"], "legacy_status": finding["status"],
                            "canonical_result_eligible": False})
            if source := refs.get(("candidate_finding", candidate_id)):
                created_edges += int(_bridge_edge(db, campaign_id, legacy_id, source, "derived_from"))
        for row in db.execute("SELECT * FROM relationships WHERE engagement_id=? ORDER BY created_at,id", (engagement_id,)):
            source = refs.get(("entity", row["source_id"]))
            target = refs.get(("entity", row["target_id"]))
            if source and target:
                relation = row["relation_type"] if row["relation_type"] in EDGE_TYPES else "related_to"
                created_edges += int(_bridge_edge(db, campaign_id, source, target, relation,
                                                  {"legacy_relation_type": row["relation_type"]}))
        _emit(db, campaign_id, campaign_id, "bridge.completed",
              {"created_nodes": created_nodes, "created_edges": created_edges})
        totals = {
            "nodes": db.execute("SELECT COUNT(*) FROM research_nodes WHERE campaign_id=?", (campaign_id,)).fetchone()[0],
            "edges": db.execute("SELECT COUNT(*) FROM research_edges WHERE campaign_id=?", (campaign_id,)).fetchone()[0],
        }
    return {"campaign_id": campaign_id, "created": {"nodes": created_nodes, "edges": created_edges},
            "totals": totals, "automatic_promotion": False, "artifacts_copied": False}
