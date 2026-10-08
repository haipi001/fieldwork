"""First-party read gateways over the existing task and run ledgers."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import final_core
from v6_capabilities import capability_matches
from v6_intents import RESOURCE_RE, _sha256
from v6_policy import record_policy_decision

BROWSER_RUNNER_ID = f"builtin-native-browser-{uuid.uuid4().hex[:12]}"
BROWSER_PRINCIPAL = "fieldwork:native-browser-worker"
BROWSER_AGENT = "fieldwork:native-browser-agent-v1"
REPLAY_RUNNER_ID = f"builtin-http-replay-{uuid.uuid4().hex[:12]}"
REPLAY_PRINCIPAL = "fieldwork:http-replay-worker"
REPLAY_AGENT = "fieldwork:http-replay-agent-v1"


def _emit(db: sqlite3.Connection, campaign_id: str, action_id: str, kind: str, payload: dict) -> None:
    db.execute("INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) "
               "VALUES(?,?,?,?,?,?)", ("tool", campaign_id, action_id, kind,
                                      json.dumps(payload, sort_keys=True, separators=(",", ":")),
                                      datetime.now(timezone.utc).isoformat()))


def authorize_native_browser_read(run_id: str, engagement_id: str, url: str) -> str:
    """Consume a one-shot read Grant before the already guarded pinned GET."""
    return _authorize_read(run_id, engagement_id, url, kind="native_browser")


def authorize_http_replay_read(run_id: str, engagement_id: str, candidate_id: str, url: str,
                               *, headers: dict[str, str], replay_round: int, replay_role: str) -> str:
    """Consume a one-shot read Grant before an already guarded replay GET."""
    if replay_round not in {0, 1} or replay_role not in {
            "baseline", "attack", "negative_control", "baseline_identity", "attack_identity"}:
        raise ValueError("HTTP replay request lineage is invalid")
    arguments_hash = hashlib.sha256(json.dumps({"url": url, "method": "GET", "headers": headers},
                                       sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return _authorize_read(run_id, engagement_id, url, kind="http_replay", candidate_id=candidate_id,
                           arguments_hash=arguments_hash, replay_round=replay_round, replay_role=replay_role)


def _authorize_read(run_id: str, engagement_id: str, url: str, *, kind: str,
                    candidate_id: str | None = None, arguments_hash: str | None = None,
                    replay_round: int | None = None, replay_role: str | None = None) -> str:
    if kind == "native_browser":
        runner_id, runner_kind = BROWSER_RUNNER_ID, "native-browser"
        principal, agent = BROWSER_PRINCIPAL, BROWSER_AGENT
        capability, operation, role = "browser.navigate", "navigate", "browser-reader"
        rule_id, reason = "v5.native_browser_network_guard", "investigation"
        statuses = {"running"}
    elif kind == "http_replay":
        runner_id, runner_kind = REPLAY_RUNNER_ID, "http-replay"
        principal, agent = REPLAY_PRINCIPAL, REPLAY_AGENT
        capability, operation, role = "network.request", "get", "http-replay-reader"
        rule_id, reason = "v5.http_replay_network_guard", "verification"
        statuses = {"running", "paused", "completed"}
        if not candidate_id:
            raise ValueError("HTTP replay requires a candidate")
    else:
        raise ValueError("unsupported read gateway kind")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("read gateway requires an HTTP URL without embedded credentials")
    resource = urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))
    if not RESOURCE_RE.fullmatch(resource):
        raise ValueError("read gateway resource cannot be represented without sensitive URL data")
    with final_core.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        authority = db.execute("""
            SELECT r.id run_id,r.mode,r.status run_status,r.synthetic,
                   r.scope_snapshot_id run_scope_id,r.policy_id run_policy_id,
                   e.status engagement_status,e.current_scope_snapshot_id,e.current_policy_id,
                   s.rules,s.confirmed_at,p.policy
            FROM analysis_runs r JOIN engagements_v2 e ON e.id=r.engagement_id
            JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id AND s.engagement_id=e.id
            JOIN execution_policies p ON p.id=e.current_policy_id AND p.engagement_id=e.id
            WHERE r.id=? AND e.id=?
        """, (run_id, engagement_id)).fetchone()
        if (not authority or authority["mode"] != "traditional"
                or (kind == "native_browser" and authority["synthetic"])
                or authority["run_status"] not in statuses or authority["engagement_status"] != "ready"
                or not authority["confirmed_at"]
                or authority["run_scope_id"] != authority["current_scope_snapshot_id"]
                or authority["run_policy_id"] != authority["current_policy_id"]):
            raise ValueError("read gateway Run authority is stale")
        if kind == "http_replay":
            candidate = db.execute("SELECT status FROM candidate_findings WHERE id=? AND run_id=? AND engagement_id=?",
                                   (candidate_id, run_id, engagement_id)).fetchone()
            if not candidate or candidate["status"] in {"verified", "archived", "graveyard"}:
                raise ValueError("HTTP replay candidate is missing or no longer eligible")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        campaign_name = f"{'Native browser reads' if kind == 'native_browser' else 'HTTP replay reads'} {run_id}"
        campaign = db.execute("SELECT id FROM research_campaigns WHERE engagement_id=? AND name=?",
                              (engagement_id, campaign_name)).fetchone()
        campaign_id = campaign["id"] if campaign else f"read-campaign-{uuid.uuid4().hex}"
        if not campaign:
            db.execute("INSERT INTO research_campaigns VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                campaign_id, engagement_id, campaign_name, "Run-bound read-only first-party requests",
                "active", "business_logic", 12, 0, 1, .85, now, now,
            ))
        runner = db.execute("SELECT kind,metadata_json FROM runner_registry_v5 WHERE id=?",
                            (runner_id,)).fetchone()
        if runner:
            try:
                builtin = json.loads(runner["metadata_json"]).get("builtin") is True
            except (TypeError, ValueError, AttributeError):
                builtin = False
            if runner["kind"] != runner_kind or not builtin:
                raise ValueError("built-in read Runner identity is occupied")
        else:
            db.execute("INSERT INTO runner_registry_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                runner_id, "Built-in first-party read transport", runner_kind, "online",
                json.dumps([capability]), '{"location":"local"}', 32, 0, now,
                '{"builtin":true}', now, now,
            ))
        from v5_orchestration import _sync_runner_jobs
        _sync_runner_jobs(db, runner_id)
        runner_load = db.execute("SELECT active_jobs,max_concurrency FROM runner_registry_v5 WHERE id=?",
                                 (runner_id,)).fetchone()
        if runner_load["active_jobs"] >= runner_load["max_concurrency"]:
            raise ValueError("read Runner is at capacity")
        action_id = f"read-{uuid.uuid4().hex}"
        task_id, intent_id, grant_id = (f"atask-{uuid.uuid4().hex}",
                                        f"intent-{uuid.uuid4().hex}", f"grant-{uuid.uuid4().hex}")
        capsule = json.dumps({"native_browser_read": kind == "native_browser",
                              "http_replay_read": kind == "http_replay", "candidate_id": candidate_id,
                              "replay_round": replay_round, "replay_role": replay_role,
                              "scope_snapshot_id": authority["run_scope_id"],
                              "policy_id": authority["run_policy_id"], "resource": resource},
                             sort_keys=True, separators=(",", ":"))
        expires = (now_dt + timedelta(seconds=30)).isoformat()
        db.execute("INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            task_id, campaign_id, run_id, None, role, "Execute one bounded read-only request",
            capsule, json.dumps([capability]), json.dumps({"kind": runner_kind}),
            '{"max_runtime_ms":30000}', 0, "running", 1, 1, action_id,
            runner_id, expires, now, None, None, now, now,
        ))
        _sync_runner_jobs(db, runner_id)
        scope_hash, policy_hash = _sha256(authority["rules"]), _sha256(authority["policy"])
        db.execute("INSERT INTO action_intents_v6 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            intent_id, campaign_id, run_id, task_id, agent, principal,
            "first_party_read_code_path", capability, operation, resource,
            arguments_hash or hashlib.sha256(url.encode()).hexdigest(), None, reason, 0,
            authority["run_scope_id"], scope_hash, authority["run_policy_id"], policy_hash,
            "proposed", now,
        ))
        db.execute("INSERT INTO capability_grants_v6 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            grant_id, intent_id, principal, capability, operation, resource,
            campaign_id, run_id, task_id, authority["run_scope_id"], scope_hash,
            authority["run_policy_id"], policy_hash,
            json.dumps({"operation": operation, "side_effect": False}, sort_keys=True, separators=(",", ":")),
            1, "active",
            "fieldwork:control-plane", now, expires,
        ))
        eligible, _ = capability_matches(db, intent_id, grant_id, principal_id=principal)
        decision = record_policy_decision(db, intent_id=intent_id, grant_id=grant_id,
            principal_id=principal, legacy_guard_allowed=True,
            legacy_rule_id=rule_id, budget_ok=True)
        denied = not eligible or decision["decision"] not in {"allow", "allow_with_limit"}
        if denied:
            db.execute("UPDATE agent_tasks SET status='failed',lease_owner=NULL,lease_expires_at=NULL,"
                       "heartbeat_at=NULL,updated_at=? WHERE id=?", (now, task_id))
            _sync_runner_jobs(db, runner_id)
        else:
            use_id = f"grant-use-{uuid.uuid4().hex}"
            db.execute("INSERT INTO capability_uses_v6 VALUES(?,?,?,?,?)", (
                use_id, grant_id, intent_id, action_id, now,
            ))
            db.execute("INSERT INTO http_gateway_executions_v6 VALUES(?,?,?,?,?,?,?,?,?,?)", (
                action_id, task_id, intent_id, grant_id, decision["id"], use_id,
                runner_id, capability, resource, now,
            ))
            _emit(db, campaign_id, action_id, "tool.execution.started", {"task_id": task_id})
    if denied:
        raise ValueError("first-party read denied by V6 policy")
    return action_id


def finish_http_read(action_id: str, *, response: dict | None = None,
                     error_type: str | None = None, cancelled: bool = False) -> None:
    """Record one immutable metadata-only receipt for a started read GET."""
    with final_core.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        execution = db.execute("SELECT e.*,t.campaign_id FROM http_gateway_executions_v6 e "
                               "JOIN agent_tasks t ON t.id=e.task_id WHERE e.action_id=?", (action_id,)).fetchone()
        if not execution or db.execute("SELECT 1 FROM http_gateway_receipts_v6 WHERE action_id=?",
                                       (action_id,)).fetchone():
            raise ValueError("read action is missing or already settled")
        if response is not None and error_type is not None:
            raise ValueError("read receipt cannot have both response and error")
        status = "cancelled" if cancelled else "completed" if response is not None else "failed"
        if response is not None:
            http_status, body_sha, body_bytes = (response["status"], response["body_sha256"],
                                                 response["body_bytes"])
            if (not isinstance(http_status, int) or not 100 <= http_status <= 599
                    or not isinstance(body_sha, str) or len(body_sha) != 64
                    or not isinstance(body_bytes, int) or body_bytes < 0):
                raise ValueError("read response metadata is invalid")
        else:
            http_status = body_sha = body_bytes = None
        now = datetime.now(timezone.utc).isoformat()
        db.execute("INSERT INTO http_gateway_receipts_v6 VALUES(?,?,?,?,?,?,?)", (
            action_id, status, http_status, body_sha, body_bytes, error_type, now,
        ))
        db.execute("UPDATE agent_tasks SET status=?,lease_owner=NULL,lease_expires_at=NULL,"
                   "heartbeat_at=NULL,updated_at=? WHERE id=?", (
                       "succeeded" if status == "completed" else "failed", now, execution["task_id"],
                   ))
        from v5_orchestration import _sync_runner_jobs
        _sync_runner_jobs(db, execution["runner_id"])
        _emit(db, execution["campaign_id"], action_id, f"tool.execution.{status}",
              {"task_id": execution["task_id"], "http_status": http_status})
