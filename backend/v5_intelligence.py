"""Offline, source-backed vulnerability intelligence and conservative applicability matching."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from v5_graph import _bridge_edge, _bridge_node

router = APIRouter(prefix="/api/v1/intelligence", tags=["V5 Vulnerability Intelligence"])


def _core():
    import final_core
    return final_core


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _load(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _sha(value: Any) -> str:
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def _emit(db: sqlite3.Connection, campaign_id: str | None, entity_id: str,
          event_type: str, payload: dict[str, Any] | None = None) -> None:
    db.execute(
        "INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)",
        ("intelligence", campaign_id, entity_id, event_type, _dump(payload or {}), _now()),
    )


def _text(value: Any, label: str, length: int = 300) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > length:
        raise HTTPException(422, f"invalid {label}")
    return value.strip()


def _packages(raw: Any, *, osv: bool) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw or len(raw) > 200:
        raise HTTPException(422, "advisory requires 1-200 affected packages")
    result = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise HTTPException(422, "affected package must be an object")
        package = entry.get("package", {}) if osv else entry
        if not isinstance(package, dict):
            raise HTTPException(422, "affected package identity is invalid")
        ecosystem = _text(package.get("ecosystem"), "ecosystem", 100)
        name = _text(package.get("name"), "package name", 300)
        versions = entry.get("versions", [])
        if not isinstance(versions, list) or len(versions) > 2000 or any(
            not isinstance(version, str) or not version.strip() or len(version) > 200 for version in versions
        ):
            raise HTTPException(422, "affected versions are invalid")
        ranges = entry.get("ranges", []) if osv else []
        if not isinstance(ranges, list) or len(ranges) > 100:
            raise HTTPException(422, "affected ranges are invalid")
        # Range semantics vary by ecosystem. Keep only bounded type/event metadata;
        # this adapter never infers an affected version from a range alone.
        safe_ranges = []
        for range_item in ranges:
            if not isinstance(range_item, dict) or not isinstance(range_item.get("events"), list):
                raise HTTPException(422, "affected range is invalid")
            range_type = _text(range_item.get("type"), "range type", 30)
            if len(range_item["events"]) > 100:
                raise HTTPException(422, "affected range has too many events")
            events = []
            for event in range_item["events"]:
                if not isinstance(event, dict) or len(event) != 1:
                    raise HTTPException(422, "affected range event is invalid")
                key, value = next(iter(event.items()))
                if key not in {"introduced", "fixed", "last_affected", "limit"}:
                    raise HTTPException(422, "unknown affected range event")
                events.append({key: _text(value, "range version", 200)})
            safe_ranges.append({"type": range_type, "events": events})
        result.append({"ecosystem": ecosystem, "name": name,
                       "versions": sorted(set(version.strip() for version in versions)),
                       "ranges": safe_ranges})
    return result


class IntelSourceAdapter(Protocol):
    kind: str

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]: ...


class OSVAdapter:
    kind = "osv"

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {"external_id": _text(raw.get("id"), "OSV id", 200),
                "published_at": raw.get("published") if isinstance(raw.get("published"), str) else None,
                "modified_at": raw.get("modified") if isinstance(raw.get("modified"), str) else None,
                "affected": _packages(raw.get("affected"), osv=True)}


class NormalizedAdapter:
    kind = "normalized"

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {"external_id": _text(raw.get("id"), "advisory id", 200),
                "published_at": raw.get("published") if isinstance(raw.get("published"), str) else None,
                "modified_at": raw.get("modified") if isinstance(raw.get("modified"), str) else None,
                "affected": _packages(raw.get("affected"), osv=False)}


ADAPTERS: dict[str, IntelSourceAdapter] = {adapter.kind: adapter for adapter in (OSVAdapter(), NormalizedAdapter())}


class RecordImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: Literal["osv", "normalized"]
    source_name: str = Field(min_length=1, max_length=120)
    license: str = Field(min_length=1, max_length=200)
    source_url: str | None = Field(default=None, max_length=2048)
    records: list[dict[str, Any]] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def safe_source_url(self):
        if not self.source_name.strip() or not self.license.strip():
            raise ValueError("source name and license are required")
        if self.source_url:
            parsed = urlsplit(self.source_url)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment):
                raise ValueError("source URL must be credential-free HTTPS without query or fragment")
        return self


class PackageFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ecosystem: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=300)
    version: str | None = Field(default=None, max_length=200)
    source_kind: Literal["manual", "artifact", "observation"] = "manual"
    source_ref: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def source_binding(self):
        if (self.source_kind == "manual" and self.source_ref) or (self.source_kind != "manual" and not self.source_ref):
            raise ValueError("fingerprint source reference does not match source kind")
        return self


class FingerprintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: str = Field(min_length=1, max_length=200)
    packages: list[PackageFingerprint] = Field(min_length=1, max_length=100)


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicability: Literal["applicable", "not_applicable", "uncertain", "verification_required"]
    rationale: str = Field(min_length=10, max_length=5000)


class VerifyMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_node_id: str = Field(min_length=1, max_length=200)
    receipt_id: str = Field(min_length=1, max_length=200)


def _campaign(db: sqlite3.Connection, campaign_id: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT c.id,c.status,c.engagement_id,e.status AS engagement_status,"
        "e.current_scope_snapshot_id,e.current_policy_id,"
        "s.confirmed_at FROM research_campaigns c JOIN engagements_v2 e ON e.id=c.engagement_id "
        "LEFT JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id WHERE c.id=?",
        (campaign_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "research campaign not found")
    return row


def _match_value(db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    record = db.execute("SELECT source,external_id,record_sha256 FROM intel_records WHERE id=?",
                        (row["intel_record_id"],)).fetchone()
    latest = db.execute(
        "SELECT id FROM intel_records WHERE source=? AND external_id=? ORDER BY fetched_at DESC,id DESC LIMIT 1",
        (record["source"], record["external_id"]),
    ).fetchone() if record else None
    current = bool(latest and latest["id"] == row["intel_record_id"])
    if current:
        try:
            _assert_current_match(db, row)
            if row["applicability"] == "verified":
                from v5_verification import validate_receipt_for_promotion
                rationale = _load(row["rationale_json"], {})
                receipt = validate_receipt_for_promotion(
                    db, rationale["verification_receipt_id"], row["campaign_id"],
                    rationale["verified_claim_node_id"], "verified", "package_applicability_v1",
                )
                fp = _load(row["fingerprint_json"], {})
                contract = receipt["replay_contract"]
                if (contract.get("match_id") != row["id"] or fp.get("source_kind") != "artifact"
                        or contract.get("artifact_id") != fp.get("source_ref")
                        or rationale.get("advisory_node_id") not in receipt["evidence_ids"]) or not db.execute(
                    "SELECT 1 FROM research_edges WHERE campaign_id=? AND source_id=? AND target_id=? "
                    "AND relation_type='derived_from'", (row["campaign_id"],
                                                     rationale["verified_claim_node_id"], row["research_node_id"]),
                ).fetchone():
                    current = False
        except (HTTPException, KeyError):
            current = False
    return {"id": row["id"], "campaign_id": row["campaign_id"], "intel_record_id": row["intel_record_id"],
            "fingerprint": _load(row["fingerprint_json"], {}), "applicability": row["applicability"],
            "rationale": _load(row["rationale_json"], {}), "research_node_id": row["research_node_id"],
            "record": dict(record) if record else None, "is_latest_record": bool(latest and latest["id"] == row["intel_record_id"]),
            "current": current,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]}


def _assert_current_match(db: sqlite3.Connection, row: sqlite3.Row) -> None:
    campaign = _campaign(db, row["campaign_id"])
    fp = _load(row["fingerprint_json"], {})
    if (campaign["status"] != "active" or campaign["engagement_status"] == "archived"
            or not campaign["confirmed_at"] or not campaign["current_policy_id"]
            or fp.get("scope_snapshot_id") != campaign["current_scope_snapshot_id"]
            or fp.get("policy_id") != campaign["current_policy_id"]):
        raise HTTPException(409, "campaign scope or policy changed since fingerprinting")
    record = db.execute("SELECT source,external_id FROM intel_records WHERE id=?", (row["intel_record_id"],)).fetchone()
    if not record:
        raise HTTPException(409, "advisory record is missing")
    latest = db.execute(
        "SELECT id FROM intel_records WHERE source=? AND external_id=? ORDER BY fetched_at DESC,id DESC LIMIT 1",
        (record["source"], record["external_id"]),
    ).fetchone()
    if not latest or latest["id"] != row["intel_record_id"]:
        raise HTTPException(409, "advisory revision was superseded; fingerprint again")


@router.get("/adapters")
def adapters():
    return {"items": [{"kind": item.kind, "transport": "offline_import"} for item in ADAPTERS.values()]}


@router.post("/records", status_code=201)
def import_records(body: RecordImport):
    adapter = ADAPTERS[body.adapter]
    source = f"{body.adapter}:{body.source_name.strip()}"
    normalized = []
    for raw in body.records:
        value = adapter.normalize(raw)
        value["provenance"] = {"source_name": body.source_name.strip(), "license": body.license.strip(),
                               "source_url": body.source_url, "adapter": body.adapter, "transport": "offline_import"}
        normalized.append(value)
    inserted = 0
    with _core().connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for value in normalized:
            digest = _sha(value)
            record_id = f"intel-{_sha([source, value['external_id'], digest])[:24]}"
            cursor = db.execute(
                "INSERT OR IGNORE INTO intel_records VALUES(?,?,?,?,?,?,?)",
                (record_id, source, value["external_id"], _dump(value), digest,
                 value["published_at"], _now()),
            )
            inserted += int(cursor.rowcount)
            if cursor.rowcount:
                _emit(db, None, record_id, "advisory.imported",
                      {"source": source, "external_id": value["external_id"], "record_sha256": digest})
    return {"source": source, "received": len(normalized), "inserted": inserted,
            "duplicates": len(normalized) - inserted}


def _validate_source(db: sqlite3.Connection, campaign: sqlite3.Row, fp: PackageFingerprint) -> None:
    if fp.source_kind == "manual":
        return
    if fp.source_kind == "observation":
        row = db.execute("SELECT 1 FROM observations WHERE id=? AND engagement_id=?",
                         (fp.source_ref, campaign["engagement_id"])).fetchone()
    else:
        row = db.execute(
            "SELECT 1 FROM artifacts a JOIN analysis_runs r ON r.id=a.run_id "
            "WHERE a.id=? AND r.engagement_id=?", (fp.source_ref, campaign["engagement_id"]),
        ).fetchone()
    if not row:
        raise HTTPException(409, "fingerprint source is not part of this campaign engagement")


@router.post("/fingerprint")
def fingerprint(body: FingerprintRequest):
    matches = []
    with _core().connect() as db:
        db.execute("BEGIN IMMEDIATE")
        campaign = _campaign(db, body.campaign_id)
        if (campaign["status"] != "active" or campaign["engagement_status"] == "archived"
                or not campaign["confirmed_at"] or not campaign["current_policy_id"]):
            raise HTTPException(409, "active campaign and confirmed scope/policy required")
        records = db.execute("SELECT * FROM intel_records ORDER BY fetched_at DESC,id DESC").fetchall()
        latest = {}
        for record in records:
            latest.setdefault((record["source"], record["external_id"]), record)
        for fp in body.packages:
            _validate_source(db, campaign, fp)
            normalized_fp = {"ecosystem": fp.ecosystem.strip(), "name": fp.name.strip(),
                             "version": fp.version.strip() if fp.version else None,
                             "source_kind": fp.source_kind, "source_ref": fp.source_ref,
                             "trust": "operator_attested" if fp.source_kind == "manual" else "linked_unverified",
                             "scope_snapshot_id": campaign["current_scope_snapshot_id"],
                             "policy_id": campaign["current_policy_id"]}
            for record in latest.values():
                advisory = _load(record["record_json"], {})
                affected = [item for item in advisory.get("affected", [])
                            if item["ecosystem"].casefold() == normalized_fp["ecosystem"].casefold()
                            and item["name"].casefold() == normalized_fp["name"].casefold()]
                if not affected:
                    continue
                exact = bool(normalized_fp["version"] and any(
                    normalized_fp["version"] in item["versions"] for item in affected
                ))
                applicability = "verification_required" if exact else "uncertain"
                reason = "exact_affected_version_listed" if exact else (
                    "version_unknown" if not normalized_fp["version"] else "range_or_ecosystem_semantics_unresolved"
                )
                advisory_node, _ = _bridge_node(
                    db, body.campaign_id, source_type="intel_record", source_ref=record["id"],
                    node_type="observation", title=f"Advisory source · {record['external_id']}", body="",
                    status="advisory_unverified", attributes={"intel_record_id": record["id"],
                                                           "record_sha256": record["record_sha256"],
                                                           "source": record["source"],
                                                           "source_content_copied": False},
                )
                rationale = {"automated_reason": reason, "advisory_is_not_finding": True,
                             "source_license": advisory["provenance"]["license"],
                             "advisory_node_id": advisory_node}
                match_id = f"imatch-{_sha([body.campaign_id, record['id'], normalized_fp])[:24]}"
                now = _now()
                cursor = db.execute(
                    "INSERT OR IGNORE INTO intel_matches VALUES(?,?,?,?,?,?,?,?,?)",
                    (match_id, body.campaign_id, record["id"], _dump(normalized_fp), applicability,
                     _dump(rationale), None, now, now),
                )
                node_id, _ = _bridge_node(
                    db, body.campaign_id, source_type="intel_match", source_ref=match_id,
                    node_type="hypothesis", title=f"Advisory match · {record['external_id']}", body="",
                    status=applicability,
                    attributes={"intel_record_id": record["id"], "source": record["source"],
                                "fingerprint_sha256": _sha(normalized_fp), "candidate_only": True,
                                "source_content_copied": False},
                )
                _bridge_edge(db, body.campaign_id, node_id, advisory_node, "derived_from")
                db.execute("UPDATE intel_matches SET research_node_id=? WHERE id=? AND research_node_id IS NULL",
                           (node_id, match_id))
                if cursor.rowcount:
                    _emit(db, body.campaign_id, match_id, "match.created",
                          {"intel_record_id": record["id"], "applicability": applicability})
                row = db.execute("SELECT * FROM intel_matches WHERE id=?", (match_id,)).fetchone()
                matches.append(_match_value(db, row))
    return {"campaign_id": body.campaign_id, "fingerprints": len(body.packages), "matches": matches}


@router.get("/matches")
def list_matches(campaign_id: str = Query(min_length=1), limit: int = Query(100, ge=1, le=500),
                 offset: int = Query(0, ge=0)):
    with _core().connect() as db:
        _campaign(db, campaign_id)
        total = db.execute("SELECT COUNT(*) FROM intel_matches WHERE campaign_id=?", (campaign_id,)).fetchone()[0]
        rows = db.execute(
            "SELECT * FROM intel_matches WHERE campaign_id=? ORDER BY created_at,id LIMIT ? OFFSET ?",
            (campaign_id, limit, offset),
        ).fetchall()
        items = [_match_value(db, row) for row in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("/matches/{match_id}/assess")
def assess_match(match_id: str, body: Assessment):
    with _core().connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM intel_matches WHERE id=?", (match_id,)).fetchone()
        if not row:
            raise HTTPException(404, "intelligence match not found")
        _assert_current_match(db, row)
        if row["applicability"] == "verified":
            raise HTTPException(409, "verified applicability cannot be overwritten by assessment")
        rationale = {**_load(row["rationale_json"], {}),
                     "operator_assessment": body.rationale.strip(), "assessed_at": _now()}
        db.execute("UPDATE intel_matches SET applicability=?,rationale_json=?,updated_at=? WHERE id=?",
                   (body.applicability, _dump(rationale), _now(), match_id))
        db.execute("UPDATE research_nodes SET status=?,updated_at=? WHERE id=? AND campaign_id=?",
                   (body.applicability, _now(), row["research_node_id"], row["campaign_id"]))
        _emit(db, row["campaign_id"], match_id, "match.assessed", {"applicability": body.applicability})
        updated = db.execute("SELECT * FROM intel_matches WHERE id=?", (match_id,)).fetchone()
        return _match_value(db, updated)


@router.post("/matches/{match_id}/verify")
def verify_match(match_id: str, body: VerifyMatch):
    from v5_verification import validate_receipt_for_promotion

    with _core().connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM intel_matches WHERE id=?", (match_id,)).fetchone()
        if not row:
            raise HTTPException(404, "intelligence match not found")
        _assert_current_match(db, row)
        if row["applicability"] not in {"applicable", "verification_required", "verified"}:
            raise HTTPException(409, "match is not ready for independent verification")
        claim = db.execute(
            "SELECT id FROM research_nodes WHERE id=? AND campaign_id=? AND node_type='claim'",
            (body.claim_node_id, row["campaign_id"]),
        ).fetchone()
        linked = db.execute(
            "SELECT 1 FROM research_edges WHERE campaign_id=? AND source_id=? AND target_id=? "
            "AND relation_type='derived_from'", (row["campaign_id"], body.claim_node_id, row["research_node_id"]),
        ).fetchone()
        if not claim or not linked:
            raise HTTPException(409, "claim must derive from this intelligence hypothesis")
        receipt = validate_receipt_for_promotion(
            db, body.receipt_id, row["campaign_id"], body.claim_node_id,
            "verified", "package_applicability_v1",
        )
        fingerprint = _load(row["fingerprint_json"], {})
        contract = receipt["replay_contract"]
        if (contract.get("match_id") != match_id or fingerprint.get("source_kind") != "artifact"
                or contract.get("artifact_id") != fingerprint.get("source_ref")):
            raise HTTPException(409, "package receipt is not bound to this artifact-backed match")
        advisory_node_id = _load(row["rationale_json"], {}).get("advisory_node_id")
        if not advisory_node_id or advisory_node_id not in receipt["evidence_ids"]:
            raise HTTPException(409, "verification receipt must cover this advisory source")
        rationale = {**_load(row["rationale_json"], {}), "verification_receipt_id": body.receipt_id,
                     "verified_claim_node_id": body.claim_node_id,
                     "verification_scope": "source_artifact_exact_package_version_only"}
        db.execute("UPDATE intel_matches SET applicability='verified',rationale_json=?,updated_at=? WHERE id=?",
                   (_dump(rationale), _now(), match_id))
        receipt_node, _ = _bridge_node(
            db, row["campaign_id"], source_type="verification_receipt_v5", source_ref=body.receipt_id,
            node_type="verification", title="Independent applicability verification", body="",
            status="verified", attributes={"receipt_sha256": _sha(receipt),
                                           "claim_node_id": body.claim_node_id, "content_copied": False},
        )
        _bridge_edge(db, row["campaign_id"], row["research_node_id"], receipt_node, "verified_by")
        _emit(db, row["campaign_id"], match_id, "match.verified", {"receipt_id": body.receipt_id})
        updated = db.execute("SELECT * FROM intel_matches WHERE id=?", (match_id,)).fetchone()
        return _match_value(db, updated)
