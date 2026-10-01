"""Bounded cross-group knowledge transfer and deterministic research selection.

These records are research scheduling inputs, never verification or findings.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from v5_graph import _bridge_edge, _bridge_node


router = APIRouter(prefix="/api/v1/evolution", tags=["V5 Research Evolution"])
MAX_POPULATION_VARIANTS = 32


def _core():
    import final_core
    return final_core


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _load(value: str) -> Any:
    return json.loads(value)


def _sha(value: Any) -> str:
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def _id(prefix: str, *parts: str) -> str:
    return f"{prefix}-{hashlib.sha256(chr(0).join(parts).encode()).hexdigest()[:24]}"


def _campaign(db: sqlite3.Connection, campaign_id: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT c.status AS campaign_status,e.status AS engagement_status,"
        "e.current_scope_snapshot_id,e.current_policy_id,s.confirmed_at "
        "FROM research_campaigns c JOIN engagements_v2 e ON e.id=c.engagement_id "
        "LEFT JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id WHERE c.id=?",
        (campaign_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "research campaign not found")
    if (row["campaign_status"] != "active" or row["engagement_status"] == "archived"
            or not row["confirmed_at"] or not row["current_policy_id"]):
        raise HTTPException(409, "active campaign and confirmed scope/policy required")
    return row


def _group(db: sqlite3.Connection, campaign_id: str, group_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM research_groups WHERE id=? AND campaign_id=?", (group_id, campaign_id)).fetchone()
    if not row or row["status"] != "active":
        raise HTTPException(409, "active group in this campaign required")
    return row


def _claim(db: sqlite3.Connection, campaign_id: str, claim_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM research_nodes WHERE id=? AND campaign_id=? AND node_type='claim'",
                     (claim_id, campaign_id)).fetchone()
    if not row or row["status"] in {"retired", "archived", "invalidated"}:
        raise HTTPException(409, "current claim in this campaign required")
    return row


def _node_hash(row: sqlite3.Row) -> str:
    return _sha(dict(row))


def _claim_inputs(db: sqlite3.Connection, campaign_id: str, claim_id: str) -> tuple[dict, dict]:
    claim = _claim(db, campaign_id, claim_id)
    edges = db.execute(
        "SELECT e.* FROM research_edges e WHERE e.campaign_id=? AND "
        "((e.target_id=? AND e.relation_type IN ('supports','contradicts','related_to')) "
        "OR (e.source_id=? AND e.relation_type IN ('tested_by','derived_from'))) "
        "ORDER BY e.id LIMIT 101",
        (campaign_id, claim_id, claim_id),
    ).fetchall()
    if len(edges) > 100:
        raise HTTPException(413, "claim has too many related edges for a bounded capsule")
    node_ids = {claim_id}
    for edge in edges:
        node_ids.add(edge["source_id"] if edge["target_id"] == claim_id else edge["target_id"])
    nodes = {}
    for node_id in sorted(node_ids):
        row = db.execute("SELECT * FROM research_nodes WHERE id=? AND campaign_id=?",
                         (node_id, campaign_id)).fetchone()
        if not row:
            raise HTTPException(409, "claim relation references a missing node")
        nodes[node_id] = row
    hashes = {"nodes": {key: _node_hash(row) for key, row in nodes.items()},
              "edges_sha256": _sha([dict(edge) for edge in edges])}
    return {"claim": claim, "edges": edges, "nodes": nodes}, hashes


def _inputs_current(db: sqlite3.Connection, campaign_id: str, claim_id: str, saved: dict) -> bool:
    try:
        _, current = _claim_inputs(db, campaign_id, claim_id)
        return current == saved
    except HTTPException:
        return False


def _event(db: sqlite3.Connection, campaign_id: str, entity_id: str, name: str, payload: dict) -> None:
    db.execute("INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) "
               "VALUES(?,?,?,?,?,?)", ("evolution", campaign_id, entity_id, name, _dump(payload), _now()))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TransferCreate(StrictModel):
    campaign_id: str = Field(min_length=1, max_length=200)
    source_group_id: str = Field(min_length=1, max_length=200)
    target_group_id: str = Field(min_length=1, max_length=200)
    source_claim_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)


def _transfer_value(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    try:
        campaign = _campaign(db, row["campaign_id"])
    except HTTPException:
        campaign = None
    groups = db.execute("SELECT id,status FROM research_groups WHERE id IN (?,?)",
                        (row["source_group_id"], row["target_group_id"])).fetchall()
    groups_current = len(groups) == 2 and all(group["status"] != "cancelled" for group in groups)
    return {**dict(row), "capsule": _load(row["capsule_json"]),
            "current": bool(campaign and groups_current
                            and campaign["current_scope_snapshot_id"] == row["scope_snapshot_id"]
                            and campaign["current_policy_id"] == row["policy_id"]
                            and _inputs_current(db, row["campaign_id"], row["source_claim_id"],
                                                _load(row["input_hashes_json"])))}


@router.post("/transfers", status_code=201)
def create_transfer(body: TransferCreate):
    if body.source_group_id == body.target_group_id:
        raise HTTPException(422, "cross-pollination requires distinct groups")
    transfer_id = _id("xfer", body.campaign_id, body.idempotency_key)
    with _core().connect() as db:
        db.execute("BEGIN IMMEDIATE")
        campaign = _campaign(db, body.campaign_id)
        source = _group(db, body.campaign_id, body.source_group_id)
        target = _group(db, body.campaign_id, body.target_group_id)
        if source["run_id"] != target["run_id"]:
            raise HTTPException(409, "cross-pollination groups must belong to the same run")
        graph, hashes = _claim_inputs(db, body.campaign_id, body.source_claim_id)
        claim = graph["claim"]
        if source["run_id"] is not None and claim["run_id"] != source["run_id"]:
            raise HTTPException(409, "source claim is outside the source group run")
        attributes = _load(claim["attributes_json"])
        producer_task_id = attributes.get("producer_task_id")
        producer = db.execute("SELECT group_id FROM agent_tasks WHERE id=? AND campaign_id=?",
                              (producer_task_id, body.campaign_id)).fetchone() if producer_task_id else None
        if attributes.get("group_id") != source["id"] and (not producer or producer["group_id"] != source["id"]):
            raise HTTPException(409, "source claim is not attributable to source group")
        supports, counters, questions = [], [], []
        for edge in graph["edges"]:
            node = graph["nodes"][edge["source_id"] if edge["target_id"] == claim["id"] else edge["target_id"]]
            if edge["target_id"] == claim["id"] and edge["relation_type"] == "supports" and node["node_type"] in {"evidence", "observation"}:
                supports.append(node["id"])
            elif edge["target_id"] == claim["id"] and edge["relation_type"] == "contradicts" and node["node_type"] == "counterevidence":
                counters.append(node["id"])
            elif edge["target_id"] == claim["id"] and edge["relation_type"] == "related_to" and node["node_type"] == "open_question":
                questions.append({"id": node["id"], "question": node["title"][:500]})
        if max(len(supports), len(counters), len(questions)) > 20:
            raise HTTPException(413, "capsule references exceed per-kind limit")
        import reporting
        capsule = reporting.redact_structure({
            "claim_id": claim["id"], "key_idea": claim["title"][:500],
            "evidence_refs": sorted(supports), "counterevidence_refs": sorted(counters),
            "weaknesses": questions, "unresolved_question": questions[0]["question"] if questions else None,
            "source_group_id": source["id"], "target_group_id": target["id"],
            "constraints": ["untrusted research context", "no automatic finding or verification"],
        })
        if len(_dump(capsule).encode()) > 16_000:
            raise HTTPException(413, "context capsule exceeds 16 KB")
        snapshot_sha = _sha({"capsule": capsule, "input_hashes": hashes})
        existing = db.execute("SELECT * FROM research_transfers_v5 WHERE id=?", (transfer_id,)).fetchone()
        if existing:
            if (existing["source_group_id"] != body.source_group_id
                    or existing["target_group_id"] != body.target_group_id
                    or existing["source_claim_id"] != body.source_claim_id
                    or existing["snapshot_sha256"] != snapshot_sha):
                raise HTTPException(409, "idempotency key reused with different transfer")
            return _transfer_value(db, existing)
        duplicate = db.execute(
            "SELECT * FROM research_transfers_v5 WHERE campaign_id=? AND source_group_id=? "
            "AND target_group_id=? AND source_claim_id=? AND snapshot_sha256=?",
            (body.campaign_id, source["id"], target["id"], claim["id"], snapshot_sha),
        ).fetchone()
        if duplicate:
            return _transfer_value(db, duplicate)
        group_budget = _load(target["budget_json"])
        count = db.execute("SELECT COUNT(*) FROM agent_tasks WHERE group_id=?", (target["id"],)).fetchone()[0]
        if group_budget.get("max_tasks") is not None and count >= group_budget["max_tasks"]:
            raise HTTPException(409, "target group task budget exhausted")
        task_id, now = _id("atask", transfer_id), _now()
        task_capsule = {"cross_pollination": True, "transfer_id": transfer_id,
                        "transfer": capsule, "scope_snapshot_id": campaign["current_scope_snapshot_id"],
                        "policy_id": campaign["current_policy_id"], "input_hashes": hashes}
        db.execute("INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            task_id, body.campaign_id, target["run_id"], target["id"], "specialist",
            "Assess bounded transferred claim and seek independent counterevidence",
            _dump(task_capsule), _dump([]), _dump({"labels": {"location": "local"}}),
            _dump({"max_tokens": 2000, "max_cost_micros": 0}), 0, "queued", 0, 2,
            f"transfer:{transfer_id}", None, None, None, None, None, now, now))
        db.execute("INSERT INTO research_transfers_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            transfer_id, body.campaign_id, source["id"], target["id"], claim["id"], task_id,
            _dump(capsule), _dump(hashes), campaign["current_scope_snapshot_id"],
            campaign["current_policy_id"], _sha(capsule), snapshot_sha, now))
        _event(db, body.campaign_id, transfer_id, "capsule.transferred", {"target_task_id": task_id})
        return _transfer_value(db, db.execute("SELECT * FROM research_transfers_v5 WHERE id=?", (transfer_id,)).fetchone())


@router.get("/transfers/{transfer_id}")
def get_transfer(transfer_id: str):
    with _core().connect() as db:
        row = db.execute("SELECT * FROM research_transfers_v5 WHERE id=?", (transfer_id,)).fetchone()
        if not row:
            raise HTTPException(404, "transfer not found")
        return _transfer_value(db, row)


@router.get("/transfers")
def list_transfers(campaign_id: str = Query(min_length=1), limit: int = Query(100, ge=1, le=500)):
    with _core().connect() as db:
        rows = db.execute("SELECT * FROM research_transfers_v5 WHERE campaign_id=? "
                          "ORDER BY created_at DESC,id DESC LIMIT ?", (campaign_id, limit)).fetchall()
        return {"items": [_transfer_value(db, row) for row in rows]}


class VariantInput(StrictModel):
    claim_node_id: str = Field(min_length=1, max_length=200)
    parent_variant_ids: list[str] = Field(default_factory=list, max_length=2)


class PopulationCreate(StrictModel):
    campaign_id: str = Field(min_length=1, max_length=200)
    group_id: str = Field(min_length=1, max_length=200)
    parent_population_id: str | None = Field(default=None, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)
    variants: list[VariantInput] = Field(min_length=2, max_length=MAX_POPULATION_VARIANTS)
    selection_count: int = Field(default=2, ge=1, le=16)


def _signals(db: sqlite3.Connection, graph: dict, previous_titles: set[str], titles: list[str]) -> dict:
    claim, edges, nodes = graph["claim"], graph["edges"], graph["nodes"]
    support = sum(edge["target_id"] == claim["id"] and edge["relation_type"] == "supports"
                  and nodes[edge["source_id"]]["node_type"] in {"observation", "evidence"} for edge in edges)
    contradiction = sum(edge["target_id"] == claim["id"] and edge["relation_type"] == "contradicts"
                        and nodes[edge["source_id"]]["node_type"] == "counterevidence" for edge in edges)
    tested = any(edge["source_id"] == claim["id"] and edge["relation_type"] == "tested_by" for edge in edges)
    attrs = _load(claim["attributes_json"])
    title = claim["title"].strip().casefold()
    impact = attrs.get("declared_impact_signal")
    impact = float(impact) if type(impact) in {int, float} and 0 <= impact <= 1 else 0.0
    task_id = attrs.get("producer_task_id")
    usage = db.execute("SELECT input_tokens,output_tokens,cost_micros FROM agent_task_usage WHERE task_id=?",
                       (task_id,)).fetchone() if isinstance(task_id, str) else None
    tokens = usage["input_tokens"] + usage["output_tokens"] if usage else None
    cost = (1 / (1 + tokens / 1000 + usage["cost_micros"] / 1_000_000)) if usage else 0.5
    novelty = 1.0 if title not in previous_titles and titles.count(title) == 1 else 0.0
    contradiction_rate = contradiction / (support + contradiction) if support + contradiction else 0.0
    contract_sha = attrs.get("verification_contract_sha256")
    has_contract = isinstance(contract_sha, str) and len(contract_sha) == 64 and all(
        character in "0123456789abcdef" for character in contract_sha.casefold())
    return {"novelty": novelty, "evidence_support": min(1.0, support / 3),
            "contradiction_rate": contradiction_rate,
            "testability": 1.0 if tested or has_contract else 0.0,
            "declared_impact": impact, "cost_efficiency": cost,
            "support_count": support, "counterevidence_count": contradiction,
            "cost_tokens_observed": tokens,
            "limits": "heuristic research priority only; not verification, impact proof, or finding confidence"}


def _score(signals: dict) -> float:
    return round(0.2 * signals["novelty"] + 0.3 * signals["evidence_support"]
                 + 0.15 * (1 - signals["contradiction_rate"]) + 0.2 * signals["testability"]
                 + 0.1 * signals["declared_impact"] + 0.05 * signals["cost_efficiency"], 6)


def _population_value(db: sqlite3.Connection, row: sqlite3.Row, ancestors: frozenset[str] = frozenset()) -> dict:
    try:
        campaign = _campaign(db, row["campaign_id"])
    except HTTPException:
        campaign = None
    variants = db.execute("SELECT * FROM research_variants_v5 WHERE population_id=? ORDER BY rank",
                          (row["id"],)).fetchall()
    group = db.execute("SELECT status FROM research_groups WHERE id=? AND campaign_id=?",
                       (row["group_id"], row["campaign_id"])).fetchone()
    current = bool(campaign and group and group["status"] != "cancelled"
                   and campaign["current_scope_snapshot_id"] == row["scope_snapshot_id"]
                   and campaign["current_policy_id"] == row["policy_id"]
                   and all(_inputs_current(db, row["campaign_id"], variant["claim_node_id"],
                                           _load(variant["input_hashes_json"])) for variant in variants))
    if current and row["parent_population_id"]:
        parent = db.execute("SELECT * FROM research_populations_v5 WHERE id=? AND campaign_id=?",
                            (row["parent_population_id"], row["campaign_id"])).fetchone()
        current = bool(parent and parent["id"] not in ancestors and parent["id"] != row["id"]
                       and _population_value(db, parent, ancestors | {row["id"]})["current"])
    return {**dict(row), "current": current,
            "variants": [{**dict(variant), "parent_variant_ids": _load(variant["parent_variant_ids_json"]),
                          "signals": _load(variant["signals_json"])} for variant in variants],
            "selection_basis": "deterministic research heuristic; no verification or automatic finding"}


@router.post("/populations", status_code=201)
def create_population(body: PopulationCreate):
    claim_ids = [item.claim_node_id for item in body.variants]
    if len(set(claim_ids)) != len(claim_ids) or body.selection_count > len(claim_ids):
        raise HTTPException(422, "population claims must be unique and selection count in range")
    with _core().connect() as db:
        db.execute("BEGIN IMMEDIATE")
        campaign = _campaign(db, body.campaign_id)
        group = _group(db, body.campaign_id, body.group_id)
        requested = sorted((item.claim_node_id, sorted(item.parent_variant_ids)) for item in body.variants)
        existing = db.execute("SELECT * FROM research_populations_v5 WHERE campaign_id=? AND group_id=? "
                              "AND request_key=?", (body.campaign_id, body.group_id,
                                                   body.idempotency_key)).fetchone()
        if existing:
            old = db.execute("SELECT claim_node_id,parent_variant_ids_json FROM research_variants_v5 "
                             "WHERE population_id=?", (existing["id"],)).fetchall()
            stored = sorted((row["claim_node_id"], sorted(_load(row["parent_variant_ids_json"]))) for row in old)
            if (requested != stored or existing["selection_count"] != body.selection_count
                    or existing["parent_population_id"] != body.parent_population_id):
                raise HTTPException(409, "idempotency key reused with different population")
            return _population_value(db, existing)
        latest = db.execute("SELECT MAX(generation) FROM research_populations_v5 WHERE campaign_id=? AND group_id=?",
                            (body.campaign_id, body.group_id)).fetchone()[0]
        parent = None
        previous_titles: set[str] = set()
        if body.parent_population_id:
            parent = db.execute("SELECT * FROM research_populations_v5 WHERE id=? AND campaign_id=? AND group_id=?",
                                (body.parent_population_id, body.campaign_id, body.group_id)).fetchone()
            if not parent or not _population_value(db, parent)["current"]:
                raise HTTPException(409, "current parent population in this group required")
            if parent["generation"] != latest:
                raise HTTPException(409, "parent population must be the latest generation")
            previous_titles = {db.execute("SELECT title FROM research_nodes WHERE id=?", (row["claim_node_id"],)).fetchone()[0].strip().casefold()
                               for row in db.execute("SELECT claim_node_id FROM research_variants_v5 WHERE population_id=?",
                                                     (parent["id"],))}
        generation = (latest + 1) if latest is not None else 0
        selected_parents = {}
        if parent:
            selected_parents = {row["id"]: row for row in db.execute(
                "SELECT * FROM research_variants_v5 WHERE population_id=? AND selected=1", (parent["id"],))}
        prepared = []
        titles = [_claim(db, body.campaign_id, claim_id)["title"].strip().casefold() for claim_id in claim_ids]
        for item in body.variants:
            if len(set(item.parent_variant_ids)) != len(item.parent_variant_ids):
                raise HTTPException(422, "parent variant ids must be unique")
            if parent and (not item.parent_variant_ids or not set(item.parent_variant_ids) <= set(selected_parents)):
                raise HTTPException(409, "next generation requires selected parent lineage")
            if not parent and item.parent_variant_ids:
                raise HTTPException(409, "first generation cannot cite parent variants")
            graph, hashes = _claim_inputs(db, body.campaign_id, item.claim_node_id)
            if group["run_id"] is not None and graph["claim"]["run_id"] != group["run_id"]:
                raise HTTPException(409, "population claim is outside the group run")
            attrs = _load(graph["claim"]["attributes_json"])
            if attrs.get("group_id") != group["id"]:
                producer_task = db.execute("SELECT group_id FROM agent_tasks WHERE id=? AND campaign_id=?",
                                           (attrs.get("producer_task_id"), body.campaign_id)).fetchone()
                if not producer_task or producer_task["group_id"] != group["id"]:
                    raise HTTPException(409, "population claim is not attributable to group")
            for parent_id in item.parent_variant_ids:
                parent_claim = selected_parents[parent_id]["claim_node_id"]
                if parent_claim == item.claim_node_id:
                    continue
                if not any(edge["source_id"] == item.claim_node_id and edge["target_id"] == parent_claim
                           and edge["relation_type"] == "derived_from" for edge in graph["edges"]):
                    raise HTTPException(409, "variant lineage requires a derived_from graph edge")
            signals = _signals(db, graph, previous_titles, titles)
            prepared.append((item, hashes, signals, _score(signals)))
        prepared.sort(key=lambda entry: (-entry[3], entry[0].claim_node_id))
        population_id = _id("population", body.campaign_id, body.group_id, str(generation))
        now = _now()
        db.execute("INSERT INTO research_populations_v5 VALUES(?,?,?,?,?,?,?,?,?,?)", (
            population_id, body.campaign_id, body.group_id, generation,
            parent["id"] if parent else None, campaign["current_scope_snapshot_id"],
            campaign["current_policy_id"], body.selection_count, body.idempotency_key, now))
        for rank, (item, hashes, signals, score) in enumerate(prepared, 1):
            db.execute("INSERT INTO research_variants_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                _id("variant", population_id, item.claim_node_id), population_id, body.campaign_id,
                item.claim_node_id, _dump(sorted(item.parent_variant_ids)), _dump(hashes),
                _dump(signals), score, rank, int(rank <= body.selection_count), now))
        _event(db, body.campaign_id, population_id, "population.evaluated",
               {"generation": generation, "variants": len(prepared), "selected": body.selection_count})
        return _population_value(db, db.execute("SELECT * FROM research_populations_v5 WHERE id=?",
                                                (population_id,)).fetchone())


@router.get("/populations/{population_id}")
def get_population(population_id: str):
    with _core().connect() as db:
        row = db.execute("SELECT * FROM research_populations_v5 WHERE id=?", (population_id,)).fetchone()
        if not row:
            raise HTTPException(404, "population not found")
        return _population_value(db, row)


@router.get("/populations")
def list_populations(campaign_id: str = Query(min_length=1), group_id: str | None = None,
                     limit: int = Query(100, ge=1, le=500)):
    with _core().connect() as db:
        rows = db.execute("SELECT * FROM research_populations_v5 WHERE campaign_id=? "
                          "AND (? IS NULL OR group_id=?) ORDER BY created_at DESC,id DESC LIMIT ?",
                          (campaign_id, group_id, group_id, limit)).fetchall()
        return {"items": [_population_value(db, row) for row in rows]}


def evolution_task_current(db: sqlite3.Connection, task: sqlite3.Row) -> bool:
    capsule = _load(task["context_capsule_json"])
    population_id = capsule.get("evolution_population_id")
    parents = capsule.get("parent_variant_ids")
    if (task["role"] != "evolver" or not population_id or not isinstance(parents, list)
            or not 1 <= len(parents) <= 2 or len(set(parents)) != len(parents)):
        return False
    parent = db.execute("SELECT * FROM research_populations_v5 WHERE id=? AND campaign_id=?",
                        (population_id, task["campaign_id"])).fetchone()
    selected = {row["id"] for row in db.execute(
        "SELECT id FROM research_variants_v5 WHERE population_id=? AND selected=1", (population_id,))}
    return bool(parent and _population_value(db, parent)["current"]
                and parent["group_id"] == task["group_id"] and set(parents) <= selected)


def _parent_refs(db: sqlite3.Connection, campaign_id: str, variant_ids: list[str]) -> tuple[list[str], list[str], list[str]]:
    claims, evidence, counters = [], set(), set()
    for variant_id in variant_ids:
        variant = db.execute("SELECT claim_node_id FROM research_variants_v5 WHERE id=? AND campaign_id=?",
                             (variant_id, campaign_id)).fetchone()
        if not variant:
            raise HTTPException(409, "parent variant is missing")
        graph, _ = _claim_inputs(db, campaign_id, variant["claim_node_id"])
        claims.append(variant["claim_node_id"])
        for edge in graph["edges"]:
            if edge["target_id"] != variant["claim_node_id"]:
                continue
            node = graph["nodes"][edge["source_id"]]
            if edge["relation_type"] == "supports" and node["node_type"] in {"evidence", "observation"}:
                evidence.add(node["id"])
            elif edge["relation_type"] == "contradicts" and node["node_type"] == "counterevidence":
                counters.add(node["id"])
    if len(evidence) > 100 or len(counters) > 100:
        raise HTTPException(413, "evolver context has too many evidence references")
    return claims, sorted(evidence), sorted(counters)


@router.post("/populations/{population_id}/advance")
def advance_population(population_id: str):
    with _core().connect() as db:
        db.execute("BEGIN IMMEDIATE")
        population = db.execute("SELECT * FROM research_populations_v5 WHERE id=?", (population_id,)).fetchone()
        if not population or not _population_value(db, population)["current"]:
            raise HTTPException(409, "current population required for evolution")
        group = _group(db, population["campaign_id"], population["group_id"])
        latest = db.execute("SELECT MAX(generation) FROM research_populations_v5 WHERE campaign_id=? AND group_id=?",
                            (population["campaign_id"], group["id"])).fetchone()[0]
        if latest != population["generation"]:
            raise HTTPException(409, "only the latest population can advance")
        selected = db.execute("SELECT id FROM research_variants_v5 WHERE population_id=? AND selected=1 ORDER BY rank",
                              (population_id,)).fetchall()
        parent_ids = [row["id"] for row in selected]
        specs = [("mutate", [variant_id]) for variant_id in parent_ids]
        for index in range(len(parent_ids) - 1):
            pair = parent_ids[index:index + 2]
            claims, _, _ = _parent_refs(db, population["campaign_id"], pair)
            scopes = [_load(_claim(db, population["campaign_id"], claim_id)["attributes_json"]).get("scope")
                      for claim_id in claims]
            named_scopes = [value for value in scopes if isinstance(value, str) and value]
            compatible_scope = (all(value is None or isinstance(value, str) for value in scopes)
                                and len(set(named_scopes)) <= 1)
            contradictory = db.execute(
                "SELECT 1 FROM research_edges WHERE campaign_id=? AND relation_type='contradicts' "
                "AND source_id IN (?,?) AND target_id IN (?,?) LIMIT 1",
                (population["campaign_id"], *claims, *claims),
            ).fetchone()
            if compatible_scope and not contradictory:
                specs.append(("combine", pair))
        # Retained parents also occupy next-generation slots. Keep every
        # mutation, then the highest-ranked compatible pairs that still fit.
        available_slots = MAX_POPULATION_VARIANTS - len(parent_ids)
        omitted_combinations = max(0, len(specs) - available_slots)
        specs = specs[:available_slots]
        task_ids = [_id("atask", population_id, mode, *parents) for mode, parents in specs]
        existing_count = db.execute("SELECT COUNT(*) FROM agent_tasks WHERE group_id=?", (group["id"],)).fetchone()[0]
        new_count = sum(not db.execute("SELECT 1 FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
                        for task_id in task_ids)
        budget = _load(group["budget_json"])
        if budget.get("max_tasks") is not None and existing_count + new_count > budget["max_tasks"]:
            raise HTTPException(409, "group task budget exhausted")
        now = _now()
        for (mode, parents), task_id in zip(specs, task_ids):
            if db.execute("SELECT 1 FROM agent_tasks WHERE id=?", (task_id,)).fetchone():
                continue
            claims, evidence, counters = _parent_refs(db, population["campaign_id"], parents)
            capsule = {"structured_worker": "evolver", "evolution_population_id": population_id,
                       "evolution_mode": mode, "parent_variant_ids": parents, "parent_claim_ids": claims,
                       "relevant_claim_ids": claims, "relevant_evidence_ids": evidence,
                       "counterevidence_ids": counters,
                       "scope_snapshot_id": population["scope_snapshot_id"], "policy_id": population["policy_id"],
                       "constraints": ["draft claim only", "preserve all inherited counterevidence",
                                       "no automatic verification or finding"]}
            db.execute("INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                task_id, population["campaign_id"], group["run_id"], group["id"], "evolver",
                f"{mode.capitalize()} selected research claims into one bounded draft proposal",
                _dump(capsule), _dump(["structured_evolver"]), _dump({"labels": {"location": "local"}}),
                _dump({"max_tokens": 4000, "max_cost_micros": 0}), 0, "queued", 0, 2,
                f"evolution:{task_id}", None, None, None, None, None, now, now))
            _event(db, population["campaign_id"], task_id, "evolver.task_created",
                   {"population_id": population_id, "mode": mode})
        return {"population_id": population_id, "task_ids": task_ids, "created": new_count,
                "omitted_combinations": omitted_combinations,
                "status": "queued" if new_count else "existing"}


class EvolverOutput(StrictModel):
    mode: Literal["mutate", "combine"]
    parent_variant_ids: list[str] = Field(min_length=1, max_length=2)
    statement: str = Field(min_length=10, max_length=500)
    scope: str = Field(min_length=3, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    counterevidence_ids: list[str] = Field(default_factory=list, max_length=100)
    limitations: list[str] = Field(min_length=1, max_length=20)
    open_questions: list[str] = Field(min_length=1, max_length=20)


class EvolverResult(StrictModel):
    runner_id: str = Field(min_length=1, max_length=200)
    lease_attempt: int | None = Field(default=None, ge=1)
    output: EvolverOutput
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    runtime_ms: int = Field(default=0, ge=0)


@router.post("/tasks/{task_id}/result")
def submit_evolver_result(task_id: str, body: EvolverResult):
    from v5_orchestration import _check_lease_attempt, _owned_running, _sync_runner_jobs

    with _core().connect() as db:
        db.execute("BEGIN IMMEDIATE")
        task = _owned_running(db, task_id, body.runner_id)
        _check_lease_attempt(task, body.lease_attempt)
        capsule = _load(task["context_capsule_json"])
        if task["role"] != "evolver" or capsule.get("structured_worker") != "evolver":
            raise HTTPException(409, "task is not a structured evolution worker")
        runner = db.execute("SELECT * FROM runner_registry_v5 WHERE id=? AND status='online'",
                            (body.runner_id,)).fetchone()
        if not runner or "structured_evolver" not in _load(runner["capabilities_json"]):
            raise HTTPException(409, "runner lacks structured evolution capability")
        output = body.output
        if (output.mode != capsule["evolution_mode"]
                or output.parent_variant_ids != capsule["parent_variant_ids"]
                or len(output.parent_variant_ids) != (1 if output.mode == "mutate" else 2)):
            raise HTTPException(409, "evolver result does not match parent and mode contract")
        for name, values in (("evidence", output.evidence_ids),
                             ("counterevidence", output.counterevidence_ids)):
            if len(set(values)) != len(values):
                raise HTTPException(422, f"{name} references must be unique")
        if (not set(output.evidence_ids) <= set(capsule["relevant_evidence_ids"])
                or set(output.counterevidence_ids) != set(capsule["counterevidence_ids"])
                or set(output.evidence_ids) & set(output.counterevidence_ids)):
            raise HTTPException(409, "evolver must preserve inherited counterevidence and only cite context evidence")
        if any(not value.strip() or len(value) > 1000 for value in output.limitations + output.open_questions):
            raise HTTPException(422, "limitations and open questions must be 1-1000 characters")
        parents = [_claim(db, task["campaign_id"], claim_id) for claim_id in capsule["parent_claim_ids"]]
        for parent in parents:
            parent_scope = _load(parent["attributes_json"]).get("scope")
            if parent_scope and parent_scope != output.scope:
                raise HTTPException(409, "proposal scope conflicts with parent claim")
            if parent["title"].strip().casefold() == output.statement.strip().casefold():
                raise HTTPException(409, "proposal repeats a parent claim without mutation")
        if body.input_tokens + body.output_tokens > 4000:
            raise HTTPException(409, "evolution task token budget exceeded")
        claim_id, _ = _bridge_node(
            db, task["campaign_id"], source_type="evolver_task", source_ref=task_id,
            node_type="claim", title=output.statement.strip(), body="", status="draft", run_id=task["run_id"],
            attributes={"group_id": task["group_id"], "scope": output.scope.strip(),
                        "limitations": output.limitations, "open_questions": output.open_questions,
                        "producer_task_id": task_id, "producer_runner_ref": body.runner_id,
                        "parent_variant_ids": output.parent_variant_ids,
                        "source_population_id": capsule["evolution_population_id"],
                        "evolution_mode": output.mode, "canonical_result_eligible": False},
        )
        for parent_id in capsule["parent_claim_ids"]:
            _bridge_edge(db, task["campaign_id"], claim_id, parent_id, "derived_from")
        for evidence_id in output.evidence_ids:
            _bridge_edge(db, task["campaign_id"], evidence_id, claim_id, "supports")
        for counter_id in output.counterevidence_ids:
            _bridge_edge(db, task["campaign_id"], counter_id, claim_id, "contradicts")
        result = {"claim_node_id": claim_id, "parent_variant_ids": output.parent_variant_ids,
                  "mode": output.mode, "status": "draft"}
        now = _now()
        db.execute("INSERT INTO agent_task_usage VALUES(?,?,?,?,?,?)", (
            task_id, body.input_tokens, body.output_tokens, 0, body.runtime_ms, now))
        db.execute("UPDATE agent_tasks SET status='succeeded',result_json=?,lease_owner=NULL,"
                   "lease_expires_at=NULL,heartbeat_at=NULL,updated_at=? WHERE id=?",
                   (_dump(result), now, task_id))
        _sync_runner_jobs(db, body.runner_id)
        _event(db, task["campaign_id"], task_id, "evolver.succeeded", result)
        return {"task_id": task_id, "result": result}


@router.post("/populations/{population_id}/collect")
def collect_generation(population_id: str):
    with _core().connect() as db:
        parent = db.execute("SELECT * FROM research_populations_v5 WHERE id=?", (population_id,)).fetchone()
        if not parent or not _population_value(db, parent)["current"]:
            raise HTTPException(409, "current parent population required")
        selected = db.execute("SELECT id,claim_node_id FROM research_variants_v5 WHERE population_id=? "
                              "AND selected=1 ORDER BY rank", (population_id,)).fetchall()
        tasks = db.execute("SELECT * FROM agent_tasks WHERE campaign_id=? AND group_id=? AND role='evolver' "
                           "AND idempotency_key LIKE ? ORDER BY id",
                           (parent["campaign_id"], parent["group_id"], f"evolution:atask-%")).fetchall()
        tasks = [task for task in tasks if _load(task["context_capsule_json"]).get("evolution_population_id") == population_id]
        if len(tasks) < len(selected) or any(task["status"] != "succeeded" for task in tasks):
            raise HTTPException(409, "all scheduled evolution tasks must succeed before collection")
        variants = [{"claim_node_id": row["claim_node_id"], "parent_variant_ids": [row["id"]]}
                    for row in selected]
        for task in tasks:
            result = _load(task["result_json"] or "{}")
            if not result.get("claim_node_id"):
                raise HTTPException(409, "evolution task has no draft claim")
            variants.append({"claim_node_id": result["claim_node_id"],
                             "parent_variant_ids": result["parent_variant_ids"]})
        if len(variants) > MAX_POPULATION_VARIANTS:
            raise HTTPException(409, "legacy evolution batch exceeds candidate limit; explicitly reseed a bounded population")
        body = PopulationCreate(campaign_id=parent["campaign_id"], group_id=parent["group_id"],
                                parent_population_id=population_id,
                                idempotency_key=f"collected:{population_id}",
                                variants=[VariantInput.model_validate(item) for item in variants],
                                selection_count=parent["selection_count"])
    return create_population(body)
