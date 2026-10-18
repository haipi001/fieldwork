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


def _normalize_record_import(body):
    try:
        if len(_dump(body.model_dump(mode='json')).encode()) > 2_000_000:
            raise HTTPException(413,'intelligence import exceeds the 2 MB bound')
    except (ValueError,RecursionError):
        raise HTTPException(422,'intelligence import is not bounded JSON') from None
    adapter = ADAPTERS[body.adapter]
    source = f"{body.adapter}:{body.source_name.strip()}"
    normalized = []
    for raw in body.records:
        value = adapter.normalize(raw)
        value["provenance"] = {"source_name": body.source_name.strip(), "license": body.license.strip(),
                               "source_url": body.source_url, "adapter": body.adapter, "transport": "offline_import"}
        normalized.append(value)
    return source, normalized


class ReviewedRecordImport(RecordImport):
    preview_hash: str = Field(min_length=64,max_length=64)


@router.post('/records/preview')
def preview_record_import(body: RecordImport):
    source,records=_normalize_record_import(body)
    return {'source':source,'received':len(records),'preview_hash':_sha({'source':source,'records':records}),
            'records':[{'external_id':x['external_id'],'affected_packages':len(x['affected']),
                        'record_sha256':_sha(x)} for x in records],
            'source_terms_verified':False,'network_requests':0}


@router.post('/records/import-reviewed',status_code=201)
def import_reviewed_records(body: ReviewedRecordImport):
    original=RecordImport.model_validate(body.model_dump(exclude={'preview_hash'}))
    if preview_record_import(original)['preview_hash']!=body.preview_hash:
        raise HTTPException(409,'intelligence import changed; preview again')
    return import_records(original)


@router.post("/records", status_code=201)
def import_records(body: RecordImport):
    source, normalized = _normalize_record_import(body)
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
    with _core().connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return _fingerprint_transaction(db, body)


def _fingerprint_transaction(db, body):
    matches = []
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
        previous = _load(row["rationale_json"], {})
        if (row["applicability"] == "verified" and previous.get("verification_receipt_id") == body.receipt_id
                and previous.get("verified_claim_node_id") == body.claim_node_id):
            return _match_value(db, row)
        rationale = {**previous, "verification_receipt_id": body.receipt_id,
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


class PackageVerificationCommit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_hash: str = Field(min_length=64, max_length=64)


def _package_verification_plan(db, match_id):
    from v5_verification import _input_snapshot, _read_trivy_artifact
    row = db.execute('SELECT * FROM intel_matches WHERE id=?', (match_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'intelligence match not found')
    _assert_current_match(db, row)
    fp = _load(row['fingerprint_json'], {})
    if fp.get('source_kind') != 'artifact' or not fp.get('version'):
        raise HTTPException(409, 'package verification requires an artifact-backed exact version')
    artifact = db.execute('SELECT a.*,r.engagement_id,r.scope_snapshot_id,r.policy_id,r.synthetic FROM artifacts a JOIN analysis_runs r ON r.id=a.run_id WHERE a.id=?', (fp.get('source_ref'),)).fetchone()
    campaign = _campaign(db, row['campaign_id'])
    if (not artifact or artifact['engagement_id'] != campaign['engagement_id'] or artifact['synthetic']
            or artifact['kind'] != 'traditional.tool.trivy' or artifact['media_type'] != 'application/json'
            or artifact['redacted'] != 1 or artifact['scope_snapshot_id'] != campaign['current_scope_snapshot_id']
            or artifact['policy_id'] != campaign['current_policy_id']):
        raise HTTPException(409, 'current real Trivy artifact required for package verification')
    if not _read_trivy_artifact(artifact['uri'], artifact['sha256']):
        raise HTTPException(409, 'package source artifact is missing or changed')
    rationale = _load(row['rationale_json'], {})
    artifact_node = db.execute("SELECT id FROM research_nodes WHERE campaign_id=? AND source_type='artifact' AND source_ref=? AND node_type='artifact'", (row['campaign_id'],artifact['id'])).fetchone()
    evidence = [rationale.get('advisory_node_id'), artifact_node['id'] if artifact_node else None]
    if any(not isinstance(value,str) for value in evidence):
        raise HTTPException(409, 'package advisory and artifact graph references required')
    inputs = _input_snapshot(db,row['campaign_id'],evidence)
    contract = {'type':'package_applicability_v1','match_id':match_id,'artifact_id':artifact['id']}
    value = {'campaign_id':row['campaign_id'],'match_id':match_id,'match_node_id':row['research_node_id'],
             'run_id':artifact['run_id'],'fingerprint':{key:fp[key] for key in ('ecosystem','name','version')},
             'scope_snapshot_id':campaign['current_scope_snapshot_id'],'policy_id':campaign['current_policy_id'],
             'evidence_ids':sorted(evidence),'input_hashes':inputs,'replay_contract':contract,
             'source_artifact_sha256':artifact['sha256'],'match_sha256':_sha(dict(row)),
             'verification_domain':'applicability_only','target_requests':0}
    return {**value,'preview_hash':_sha(value)}


@router.get('/matches/{match_id}/verification-plan')
def package_verification_plan(match_id: str):
    with _core().connect() as db:
        db.execute('BEGIN')
        return _package_verification_plan(db,match_id)


@router.post('/matches/{match_id}/verification-request',status_code=202)
def request_package_verification(match_id: str, body: PackageVerificationCommit):
    import v5_verification as verifier
    with _core().connect() as db:
        db.execute('BEGIN IMMEDIATE')
        plan = _package_verification_plan(db,match_id)
        if body.preview_hash != plan['preview_hash']:
            raise HTTPException(409,'package verification plan changed; review again')
        contract = plan['replay_contract']
        claim_id,_ = _bridge_node(db,plan['campaign_id'],source_type='package_verification_plan',
            source_ref=plan['preview_hash'],node_type='claim',
            title=f"Package version applicability: {plan['fingerprint']['name']} {plan['fingerprint']['version']}",
            body='Checks recorded package version against the cited advisory; does not establish exploitability.',
            status='draft',run_id=plan['run_id'],attributes={'claim_kind':'package_applicability',
                'verification_contract_sha256':verifier._sha(contract),'applicability_only':True})
        _bridge_edge(db,plan['campaign_id'],claim_id,plan['match_node_id'],'derived_from')
        claim = verifier._claim(db,claim_id,plan['campaign_id'])
        request,task,created = verifier._create_request_transaction(db,claim,verifier.VerifyRequest(
            campaign_id=plan['campaign_id'],evidence_ids=plan['evidence_ids'],replay_contract=contract))
        if verifier._local_replay_input(db,request) is None:
            raise HTTPException(409,'package materials do not satisfy the independent verifier contract')
        return {'status':request['status'],'created':created,'claim_id':claim_id,
                'verification_domain':'applicability_only','execution_started':False,
                'request':verifier._request_value(request,task)}


@router.get('/campaigns/{campaign_id}/package-artifacts')
def list_package_artifacts(campaign_id: str, limit: int = Query(25,ge=1,le=100), offset: int = Query(0,ge=0)):
    with _core().connect() as db:
        campaign=_campaign(db,campaign_id)
        args=(campaign['engagement_id'],campaign['current_scope_snapshot_id'],campaign['current_policy_id'])
        where="r.engagement_id=? AND r.scope_snapshot_id=? AND r.policy_id=? AND r.synthetic=0 AND a.kind='traditional.tool.trivy' AND a.redacted=1 AND a.media_type='application/json'"
        total=db.execute('SELECT count(*) FROM artifacts a JOIN analysis_runs r ON r.id=a.run_id WHERE '+where,args).fetchone()[0]
        rows=db.execute('SELECT a.id,a.run_id,a.created_at,a.sha256 FROM artifacts a JOIN analysis_runs r ON r.id=a.run_id WHERE '+where+' ORDER BY a.created_at DESC,a.id DESC LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        return {'items':[dict(row) for row in rows],'total':total,'limit':limit,'offset':offset}


def _artifact_fingerprint_plan(db,campaign_id,artifact_id):
    from urllib.parse import unquote
    from v5_verification import _read_trivy_artifact
    campaign=_campaign(db,campaign_id)
    if (campaign['status']!='active' or campaign['engagement_status']=='archived'
            or not campaign['confirmed_at'] or not campaign['current_policy_id']):
        raise HTTPException(409,'active campaign and confirmed scope/policy required')
    artifact=db.execute('SELECT a.*,r.engagement_id,r.scope_snapshot_id,r.policy_id,r.synthetic FROM artifacts a JOIN analysis_runs r ON r.id=a.run_id WHERE a.id=?',(artifact_id,)).fetchone()
    if (not artifact or artifact['engagement_id']!=campaign['engagement_id'] or artifact['synthetic']
            or artifact['scope_snapshot_id']!=campaign['current_scope_snapshot_id'] or artifact['policy_id']!=campaign['current_policy_id']
            or artifact['kind']!='traditional.tool.trivy' or artifact['redacted']!=1 or artifact['media_type']!='application/json'):
        raise HTTPException(409,'current real Trivy artifact required')
    source=_read_trivy_artifact(artifact['uri'],artifact['sha256'])
    tool=source.get('tool_result',{}) if source else {}
    if (not isinstance(tool,dict) or tool.get('capability')!='trivy' or tool.get('status')!='completed'
            or tool.get('exit_code')!=0 or tool.get('synthetic') is not False):
        raise HTTPException(409,'completed real Trivy source bytes required')
    observations=source.get('observations')
    if not isinstance(observations,list) or len(observations)>200:
        raise HTTPException(409,'package observations exceed the supported bound')
    packages={};unsupported=set()
    for item in observations:
        if not isinstance(item,dict):raise HTTPException(409,'invalid package observation')
        if not isinstance(item.get('observation_type'),str):raise HTTPException(409,'invalid package observation type')
        if item.get('observation_type')=='code.dependency_inventory_incomplete':
            raise HTTPException(409,'package inventory is incomplete')
        if item.get('observation_type') not in {'code.dependency_inventory','code.dependency_vulnerability'}:continue
        data=item.get('structured',{});identity=data.get('PkgIdentifier',{}) if isinstance(data,dict) else {}
        purl=identity.get('PURL') if isinstance(identity,dict) else None
        if not isinstance(purl,str) or not purl.startswith('pkg:') or len(purl)>500:
            raise HTTPException(409,'versioned package PURL required')
        try:
            kind,tail=purl[4:].split('/',1);name,version=tail.split('#',1)[0].split('?',1)[0].rsplit('@',1)
            name,version=unquote(name,errors='strict'),unquote(version,errors='strict')
        except (ValueError,UnicodeError):raise HTTPException(409,'invalid package PURL') from None
        if not name or not version or data.get('PkgName')!=name or data.get('InstalledVersion')!=version:
            raise HTTPException(409,'package identity disagrees with its PURL')
        ecosystem={'pypi':'PyPI','npm':'npm'}.get(kind.casefold())
        if not ecosystem:unsupported.add(kind);continue
        try:package=PackageFingerprint(ecosystem=ecosystem,name=name,version=version,source_kind='artifact',source_ref=artifact_id)
        except ValueError:raise HTTPException(409,'package identity exceeds the supported bound') from None
        packages[(ecosystem,name,version)]=package.model_dump(mode='json')
    if not packages:raise HTTPException(409,'no supported PyPI/npm package versions in this artifact')
    if len(packages)>100:raise HTTPException(409,'package fingerprint batch exceeds 100 unique versions')
    value={'campaign_id':campaign_id,'artifact_id':artifact_id,'run_id':artifact['run_id'],
        'artifact_sha256':artifact['sha256'],'scope_snapshot_id':campaign['current_scope_snapshot_id'],
        'policy_id':campaign['current_policy_id'],'packages':[packages[key] for key in sorted(packages)],
        'unsupported_purl_types':sorted(unsupported),'inventory_mode':'package_inventory' if any(x.get('observation_type')=='code.dependency_inventory' for x in observations) else 'vulnerability_records_only','target_requests':0,'model_calls':0}
    return {**value,'preview_hash':_sha(value)}


@router.get('/campaigns/{campaign_id}/artifacts/{artifact_id}/fingerprint-plan')
def artifact_fingerprint_plan(campaign_id: str,artifact_id: str):
    with _core().connect() as db:
        db.execute('BEGIN')
        return _artifact_fingerprint_plan(db,campaign_id,artifact_id)


@router.post('/campaigns/{campaign_id}/artifacts/{artifact_id}/fingerprint')
def fingerprint_artifact(campaign_id: str,artifact_id: str,body: PackageVerificationCommit):
    with _core().connect() as db:
        db.execute('BEGIN IMMEDIATE')
        plan=_artifact_fingerprint_plan(db,campaign_id,artifact_id)
        if body.preview_hash!=plan['preview_hash']:raise HTTPException(409,'package fingerprint plan changed; review again')
        result=_fingerprint_transaction(db,FingerprintRequest(campaign_id=campaign_id,packages=plan['packages']))
        if _artifact_fingerprint_plan(db,campaign_id,artifact_id)['preview_hash']!=plan['preview_hash']:
            raise HTTPException(409,'package source changed during fingerprinting')
        return {**result,'artifact_id':artifact_id,'unsupported_purl_types':plan['unsupported_purl_types']}
