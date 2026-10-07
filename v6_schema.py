"""Additive V6 control-plane storage. No historical rows are rewritten."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

V6_SCHEMA_VERSION = 2
V6_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS v6_schema_meta(
         key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS action_intents_v6(
         id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,run_id TEXT NOT NULL,task_id TEXT NOT NULL,
         agent_id TEXT NOT NULL,principal_id TEXT NOT NULL,principal_binding TEXT NOT NULL,
         capability TEXT NOT NULL,operation TEXT NOT NULL,resource TEXT NOT NULL,
         arguments_hash TEXT,risk_hint REAL,reason_summary TEXT NOT NULL,
         side_effect INTEGER NOT NULL CHECK(side_effect IN (0,1)),
         scope_snapshot_id TEXT NOT NULL,scope_sha256 TEXT NOT NULL,
         policy_id TEXT NOT NULL,policy_sha256 TEXT NOT NULL,
         status TEXT NOT NULL CHECK(status='proposed'),created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS action_intents_v6_task ON action_intents_v6(task_id,created_at,id)",
    """CREATE TRIGGER IF NOT EXISTS action_intents_v6_no_update BEFORE UPDATE ON action_intents_v6
         BEGIN SELECT RAISE(ABORT,'action intent is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS action_intents_v6_no_delete BEFORE DELETE ON action_intents_v6
         BEGIN SELECT RAISE(ABORT,'action intent is immutable'); END""",
    """CREATE TABLE IF NOT EXISTS capability_grants_v6(
         id TEXT PRIMARY KEY,intent_id TEXT NOT NULL,principal_id TEXT NOT NULL,
         capability TEXT NOT NULL,operation TEXT NOT NULL,resource_pattern TEXT NOT NULL,
         campaign_id TEXT NOT NULL,run_id TEXT NOT NULL,task_id TEXT NOT NULL,
         scope_snapshot_id TEXT NOT NULL,scope_sha256 TEXT NOT NULL,
         policy_id TEXT NOT NULL,policy_sha256 TEXT NOT NULL,
         constraints_json TEXT NOT NULL,max_uses INTEGER NOT NULL CHECK(max_uses>=1),
         approval_state TEXT NOT NULL CHECK(approval_state IN ('active','pending_approval')),
         issued_by TEXT NOT NULL,issued_at TEXT NOT NULL,expires_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS capability_grants_v6_intent ON capability_grants_v6(intent_id,issued_at)",
    """CREATE TABLE IF NOT EXISTS capability_revocations_v6(
         grant_id TEXT PRIMARY KEY,revoked_by TEXT NOT NULL,revoked_at TEXT NOT NULL,
         reason_code TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS capability_uses_v6(
         id TEXT PRIMARY KEY,grant_id TEXT NOT NULL,intent_id TEXT NOT NULL,
         action_id TEXT NOT NULL UNIQUE,recorded_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS capability_uses_v6_grant ON capability_uses_v6(grant_id)",
    *(
        f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{operation}
             BEFORE {operation.upper()} ON {table}
             BEGIN SELECT RAISE(ABORT,'V6 capability audit row is immutable'); END"""
        for table in ("capability_grants_v6", "capability_revocations_v6", "capability_uses_v6")
        for operation in ("update", "delete")
    ),
)


def apply_v6_schema(database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        for statement in V6_SCHEMA_STATEMENTS:
            db.execute(statement)
        db.execute(
            "INSERT INTO v6_schema_meta(key,value,updated_at) VALUES('schema_version',?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (str(V6_SCHEMA_VERSION), datetime.now(timezone.utc).isoformat()),
        )
