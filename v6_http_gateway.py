"""First-party native browser read gateway over the existing task and run ledgers."""
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


def _emit(db: sqlite3.Connection, campaign_id: str, action_id: str, kind: str, payload: dict) -> None:
    db.execute("INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) "
               "VALUES(?,?,?,?,?,?)", ("tool", campaign_id, action_id, kind,
                                      json.dumps(payload, sort_keys=True, separators=(",", ":")),
                                      datetime.now(timezone.utc).isoformat()))


def authorize_native_browser_read(run_id: str, engagement_id: str, url: str) -> str:
    """Consume a one-shot read Grant before the already guarded pinned GET."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("browser gateway requires an HTTP URL without embedded credentials")
    resource = urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))
    if not RESOURCE_RE.fullmatch(resource):
        raise ValueError("browser gateway resource cannot be represented without sensitive URL data")
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
        if (not authority or authority["mode"] != "traditional" or authority["synthetic"]
                or authority["run_status"] != "running" or authority["engagement_status"] != "ready"
                or not authority["confirmed_at"]
                or authority["run_scope_id"] != authority["current_scope_snapshot_id"]
                or authority["run_policy_id"] != authority["current_policy_id"]):
            raise ValueError("native browser Run authority is stale")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        campaign_name = f"Native browser reads {run_id}"
        campaign = db.execute("SELECT id FROM research_campaigns WHERE engagement_id=? AND name=?",
                              (engagement_id, campaign_name)).fetchone()
        campaign_id = campaign["id"] if campaign else f"browser-campaign-{uuid.uuid4().hex}"
        if not campaign:
            db.execute("INSERT INTO research_campaigns VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                campaign_id, engagement_id, campaign_name, "Run-bound read-only browser requests",
                "active", "business_logic", 12, 0, 1, .85, now, now,
            ))
        runner = db.execute("SELECT kind,metadata_json FROM runner_registry_v5 WHERE id=?",
                            (BROWSER_RUNNER_ID,)).fetchone()
        if runner:
            try:
                builtin = json.loads(runner["metadata_json"]).get("builtin") is True
            except (TypeError, ValueError, AttributeError):
                builtin = False
            if runner["kind"] != "native-browser" or not builtin:
                raise ValueError("built-in browser Runner identity is occupied")
        else:
            db.execute("INSERT INTO runner_registry_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                BROWSER_RUNNER_ID, "Built-in native browser transport", "native-browser", "online",
                '["browser.navigate"]', '{"location":"local"}', 32, 0, now,
                '{"builtin":true}', now, now,
            ))
        from v5_orchestration import _sync_runner_jobs
        _sync_runner_jobs(db, BROWSER_RUNNER_ID)
        runner_load = db.execute("SELECT active_jobs,max_concurrency FROM runner_registry_v5 WHERE id=?",
                                 (BROWSER_RUNNER_ID,)).fetchone()
        if runner_load["active_jobs"] >= runner_load["max_concurrency"]:
            raise ValueError("native browser Runner is at capacity")
        action_id = f"browser-read-{uuid.uuid4().hex}"
        task_id, intent_id, grant_id = (f"atask-{uuid.uuid4().hex}",
                                        f"intent-{uuid.uuid4().hex}", f"grant-{uuid.uuid4().hex}")
        capsule = json.dumps({"native_browser_read": True, "scope_snapshot_id": authority["run_scope_id"],
                              "policy_id": authority["run_policy_id"], "resource": resource},
                             sort_keys=True, separators=(",", ":"))
        expires = (now_dt + timedelta(seconds=30)).isoformat()
        db.execute("INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            task_id, campaign_id, run_id, None, "browser-reader", "Execute one bounded read-only page request",
            capsule, '["browser.navigate"]', '{"kind":"native-browser"}',
            '{"max_runtime_ms":30000}', 0, "running", 1, 1, action_id,
            BROWSER_RUNNER_ID, expires, now, None, None, now, now,
        ))
        _sync_runner_jobs(db, BROWSER_RUNNER_ID)
        scope_hash, policy_hash = _sha256(authority["rules"]), _sha256(authority["policy"])
        db.execute("INSERT INTO action_intents_v6 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            intent_id, campaign_id, run_id, task_id, BROWSER_AGENT, BROWSER_PRINCIPAL,
            "first_party_native_browser_code_path", "browser.navigate", "navigate", resource,
            hashlib.sha256(url.encode()).hexdigest(), None, "investigation", 0,
            authority["run_scope_id"], scope_hash, authority["run_policy_id"], policy_hash,
            "proposed", now,
        ))
        db.execute("INSERT INTO capability_grants_v6 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            grant_id, intent_id, BROWSER_PRINCIPAL, "browser.navigate", "navigate", resource,
            campaign_id, run_id, task_id, authority["run_scope_id"], scope_hash,
            authority["run_policy_id"], policy_hash,
            '{"operation":"navigate","side_effect":false}', 1, "active",
            "fieldwork:control-plane", now, expires,
        ))
        eligible, _ = capability_matches(db, intent_id, grant_id, principal_id=BROWSER_PRINCIPAL)
        decision = record_policy_decision(db, intent_id=intent_id, grant_id=grant_id,
            principal_id=BROWSER_PRINCIPAL, legacy_guard_allowed=True,
            legacy_rule_id="v5.native_browser_network_guard", budget_ok=True)
        denied = not eligible or decision["decision"] not in {"allow", "allow_with_limit"}
        if denied:
            db.execute("UPDATE agent_tasks SET status='failed',lease_owner=NULL,lease_expires_at=NULL,"
                       "heartbeat_at=NULL,updated_at=? WHERE id=?", (now, task_id))
            _sync_runner_jobs(db, BROWSER_RUNNER_ID)
        else:
            use_id = f"grant-use-{uuid.uuid4().hex}"
            db.execute("INSERT INTO capability_uses_v6 VALUES(?,?,?,?,?)", (
                use_id, grant_id, intent_id, action_id, now,
            ))
            db.execute("INSERT INTO http_gateway_executions_v6 VALUES(?,?,?,?,?,?,?,?,?,?)", (
                action_id, task_id, intent_id, grant_id, decision["id"], use_id,
                BROWSER_RUNNER_ID, "browser.navigate", resource, now,
            ))
            _emit(db, campaign_id, action_id, "tool.execution.started", {"task_id": task_id})
    if denied:
        raise ValueError("native browser read denied by V6 policy")
    return action_id


def finish_native_browser_read(action_id: str, *, response: dict | None = None,
                               error_type: str | None = None, cancelled: bool = False) -> None:
    """Record one immutable metadata-only receipt for a started browser GET."""
    with final_core.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        execution = db.execute("SELECT e.*,t.campaign_id FROM http_gateway_executions_v6 e "
                               "JOIN agent_tasks t ON t.id=e.task_id WHERE e.action_id=?", (action_id,)).fetchone()
        if not execution or db.execute("SELECT 1 FROM http_gateway_receipts_v6 WHERE action_id=?",
                                       (action_id,)).fetchone():
            raise ValueError("browser action is missing or already settled")
        if response is not None and error_type is not None:
            raise ValueError("browser receipt cannot have both response and error")
        status = "cancelled" if cancelled else "completed" if response is not None else "failed"
        if response is not None:
            http_status, body_sha, body_bytes = (response["status"], response["body_sha256"],
                                                 response["body_bytes"])
            if (not isinstance(http_status, int) or not 100 <= http_status <= 599
                    or not isinstance(body_sha, str) or len(body_sha) != 64
                    or not isinstance(body_bytes, int) or body_bytes < 0):
                raise ValueError("browser response metadata is invalid")
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
