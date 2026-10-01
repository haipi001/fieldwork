"""Additive Fieldwork V5 schema migration.

This module creates storage only. It deliberately does not bridge, copy, run,
or promote any historical record; those actions belong to later explicit tasks.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


V5_SCHEMA_VERSION = 5

V5_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS v5_schema_meta(
         key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS runtime_profiles(
         id TEXT PRIMARY KEY,name TEXT NOT NULL,
         mode TEXT NOT NULL CHECK(mode IN ('cloud','local','hybrid','offline')),
         config_json TEXT NOT NULL,immutable_hash TEXT NOT NULL,created_at TEXT NOT NULL)""",
    """CREATE TRIGGER IF NOT EXISTS runtime_profiles_no_update
         BEFORE UPDATE ON runtime_profiles BEGIN
         SELECT RAISE(ABORT,'runtime profile is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS runtime_profiles_no_delete
         BEFORE DELETE ON runtime_profiles BEGIN
         SELECT RAISE(ABORT,'runtime profile is immutable'); END""",
    """CREATE TABLE IF NOT EXISTS runtime_providers(
         id TEXT PRIMARY KEY,kind TEXT NOT NULL,name TEXT NOT NULL,base_url TEXT,model TEXT,
         secret_ref TEXT,enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
         metadata_json TEXT NOT NULL DEFAULT '{}',last_health TEXT,last_health_at TEXT,
         created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS runtime_usage(
         id INTEGER PRIMARY KEY AUTOINCREMENT,campaign_id TEXT,task_id TEXT,provider_id TEXT,
         route TEXT NOT NULL,input_tokens INTEGER NOT NULL DEFAULT 0,
         output_tokens INTEGER NOT NULL DEFAULT 0,cost_micros INTEGER NOT NULL DEFAULT 0,
         created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS runtime_route_decisions(
         id TEXT PRIMARY KEY,campaign_id TEXT,task_id TEXT,profile_id TEXT,
         request_json TEXT NOT NULL,route TEXT NOT NULL,provider_id TEXT,model TEXT,
         reason_json TEXT NOT NULL,max_tokens INTEGER NOT NULL,max_cost_micros INTEGER NOT NULL,
         status TEXT NOT NULL,created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS runtime_route_decisions_scope ON runtime_route_decisions(campaign_id,task_id,created_at)",
    """CREATE TABLE IF NOT EXISTS runtime_usage_reports(
         idempotency_key TEXT PRIMARY KEY,decision_id TEXT NOT NULL,usage_id INTEGER NOT NULL UNIQUE,
         payload_sha256 TEXT NOT NULL,created_at TEXT NOT NULL)""",
    """CREATE TRIGGER IF NOT EXISTS runtime_route_decisions_no_update
         BEFORE UPDATE ON runtime_route_decisions BEGIN
         SELECT RAISE(ABORT,'runtime route decision is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS runtime_route_decisions_no_delete
         BEFORE DELETE ON runtime_route_decisions BEGIN
         SELECT RAISE(ABORT,'runtime route decision is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS runtime_usage_no_update
         BEFORE UPDATE ON runtime_usage BEGIN
         SELECT RAISE(ABORT,'runtime usage is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS runtime_usage_no_delete
         BEFORE DELETE ON runtime_usage BEGIN
         SELECT RAISE(ABORT,'runtime usage is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS runtime_usage_reports_no_update
         BEFORE UPDATE ON runtime_usage_reports BEGIN
         SELECT RAISE(ABORT,'runtime usage report is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS runtime_usage_reports_no_delete
         BEFORE DELETE ON runtime_usage_reports BEGIN
         SELECT RAISE(ABORT,'runtime usage report is immutable'); END""",
    """CREATE TABLE IF NOT EXISTS research_nodes(
         id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,run_id TEXT,node_type TEXT NOT NULL,
         title TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL,confidence REAL,
         source_type TEXT NOT NULL,source_ref TEXT,attributes_json TEXT NOT NULL,
         created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS research_nodes_campaign ON research_nodes(campaign_id,node_type,status)",
    """CREATE TABLE IF NOT EXISTS research_edges(
         id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,source_id TEXT NOT NULL,target_id TEXT NOT NULL,
         relation_type TEXT NOT NULL,attributes_json TEXT NOT NULL,created_at TEXT NOT NULL,
         UNIQUE(campaign_id,source_id,target_id,relation_type))""",
    "CREATE INDEX IF NOT EXISTS research_edges_campaign ON research_edges(campaign_id,source_id,target_id)",
    """CREATE TABLE IF NOT EXISTS research_groups(
         id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,run_id TEXT,parent_group_id TEXT,role TEXT NOT NULL,
         objective TEXT NOT NULL,strategy TEXT NOT NULL,runtime_profile_id TEXT,budget_json TEXT NOT NULL,
         status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS research_groups_campaign ON research_groups(campaign_id,status)",
    """CREATE TABLE IF NOT EXISTS agent_tasks(
         id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,run_id TEXT,group_id TEXT,role TEXT NOT NULL,
         objective TEXT NOT NULL,context_capsule_json TEXT NOT NULL,tool_grants_json TEXT NOT NULL,
         route_requirement_json TEXT NOT NULL,budget_json TEXT NOT NULL,priority REAL NOT NULL DEFAULT 0,
         status TEXT NOT NULL,attempt INTEGER NOT NULL DEFAULT 0,max_attempts INTEGER NOT NULL DEFAULT 2,
         idempotency_key TEXT NOT NULL UNIQUE,lease_owner TEXT,lease_expires_at TEXT,heartbeat_at TEXT,
         result_json TEXT,error_json TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS agent_tasks_sched ON agent_tasks(status,priority DESC,created_at)",
    """CREATE TABLE IF NOT EXISTS agent_task_usage(
         task_id TEXT PRIMARY KEY,input_tokens INTEGER NOT NULL DEFAULT 0,
         output_tokens INTEGER NOT NULL DEFAULT 0,cost_micros INTEGER NOT NULL DEFAULT 0,
         runtime_ms INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS agent_task_checkpoints(
         id TEXT PRIMARY KEY,task_id TEXT NOT NULL,lease_owner TEXT NOT NULL,
         checkpoint_json TEXT NOT NULL,created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS agent_task_checkpoints_task ON agent_task_checkpoints(task_id,created_at)",
    """CREATE TABLE IF NOT EXISTS research_checkpoints(
         id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,run_id TEXT,graph_digest TEXT NOT NULL,
         summary_json TEXT NOT NULL,created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS verification_receipts_v5(
         id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,claim_node_id TEXT NOT NULL,
         verifier_id TEXT NOT NULL,runner_ref TEXT NOT NULL,environment_json TEXT NOT NULL,
         input_hashes_json TEXT NOT NULL,replay_contract_json TEXT NOT NULL,result_json TEXT NOT NULL,
         evidence_ids_json TEXT NOT NULL,limitations_json TEXT NOT NULL,
         receipt_sha256 TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS verification_requests_v5(
         id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,claim_node_id TEXT NOT NULL,
         claim_sha256 TEXT NOT NULL,producer_task_id TEXT,producer_runner_ref TEXT,
         verifier_task_id TEXT NOT NULL UNIQUE,input_node_ids_json TEXT NOT NULL,
         input_hashes_json TEXT NOT NULL,replay_contract_json TEXT NOT NULL,
         status TEXT NOT NULL,source_receipt_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS verification_requests_claim ON verification_requests_v5(campaign_id,claim_node_id,status)",
    """CREATE TABLE IF NOT EXISTS verification_receipt_bindings_v5(
         receipt_id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,verifier_task_id TEXT NOT NULL UNIQUE,
         claim_sha256 TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL)""",
    """CREATE TRIGGER IF NOT EXISTS verification_receipts_v5_no_update
         BEFORE UPDATE ON verification_receipts_v5 BEGIN
         SELECT RAISE(ABORT,'verification receipt is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS verification_receipts_v5_no_delete
         BEFORE DELETE ON verification_receipts_v5 BEGIN
         SELECT RAISE(ABORT,'verification receipt is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS verification_receipt_bindings_v5_no_update
         BEFORE UPDATE ON verification_receipt_bindings_v5 BEGIN
         SELECT RAISE(ABORT,'verification receipt binding is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS verification_receipt_bindings_v5_no_delete
         BEFORE DELETE ON verification_receipt_bindings_v5 BEGIN
         SELECT RAISE(ABORT,'verification receipt binding is immutable'); END""",
    """CREATE TABLE IF NOT EXISTS runner_registry_v5(
         id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,
         capabilities_json TEXT NOT NULL,labels_json TEXT NOT NULL,max_concurrency INTEGER NOT NULL DEFAULT 1,
         active_jobs INTEGER NOT NULL DEFAULT 0,heartbeat_at TEXT,metadata_json TEXT NOT NULL DEFAULT '{}',
         created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS runner_registry_v5_status ON runner_registry_v5(status,heartbeat_at)",
    """CREATE TABLE IF NOT EXISTS continuous_research_state(
         campaign_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
         policy_json TEXT NOT NULL,last_checkpoint_id TEXT,last_tick_at TEXT,next_tick_at TEXT,
         last_result_json TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS intel_records(
         id TEXT PRIMARY KEY,source TEXT NOT NULL,external_id TEXT NOT NULL,record_json TEXT NOT NULL,
         record_sha256 TEXT NOT NULL,published_at TEXT,fetched_at TEXT NOT NULL,
         UNIQUE(source,external_id,record_sha256))""",
    """CREATE TABLE IF NOT EXISTS intel_matches(
         id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,intel_record_id TEXT NOT NULL,
         fingerprint_json TEXT NOT NULL,applicability TEXT NOT NULL,rationale_json TEXT NOT NULL,
         research_node_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS v5_events(
         id INTEGER PRIMARY KEY AUTOINCREMENT,topic TEXT NOT NULL,campaign_id TEXT,entity_id TEXT,
         event_type TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS v5_events_topic ON v5_events(topic,id)",
    """CREATE TABLE IF NOT EXISTS agent_registry_v5(
         id TEXT PRIMARY KEY,name TEXT NOT NULL,provider TEXT,model TEXT,runtime TEXT,environment TEXT,
         status TEXT NOT NULL,risk TEXT NOT NULL DEFAULT 'unknown',capabilities_json TEXT NOT NULL DEFAULT '[]',
         permissions_json TEXT NOT NULL DEFAULT '{}',source_ref TEXT,
         first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS agent_memory_snapshots_v5(
         id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,source_path TEXT NOT NULL,sha256 TEXT NOT NULL,
         size_bytes INTEGER NOT NULL,modified_at TEXT,trust TEXT NOT NULL,metadata_json TEXT NOT NULL,
         created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS agent_memory_agent ON agent_memory_snapshots_v5(agent_id,source_path,created_at)",
    """CREATE TABLE IF NOT EXISTS agent_supply_components_v5(
         id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,component_type TEXT NOT NULL,name TEXT NOT NULL,
         version TEXT,source TEXT,sha256 TEXT,capabilities_json TEXT NOT NULL,permissions_json TEXT NOT NULL,
         metadata_json TEXT NOT NULL,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,
         UNIQUE(agent_id,component_type,name,source))""",
    """CREATE TABLE IF NOT EXISTS agent_attack_tests_v5(
         id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,scenario_id TEXT NOT NULL,mode TEXT NOT NULL,
         status TEXT NOT NULL,result_json TEXT NOT NULL,evidence_json TEXT NOT NULL,
         created_at TEXT NOT NULL,completed_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS research_transfers_v5(
         id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,source_group_id TEXT NOT NULL,
         target_group_id TEXT NOT NULL,source_claim_id TEXT NOT NULL,target_task_id TEXT NOT NULL UNIQUE,
         capsule_json TEXT NOT NULL,input_hashes_json TEXT NOT NULL,
         scope_snapshot_id TEXT NOT NULL,policy_id TEXT NOT NULL,
         capsule_sha256 TEXT NOT NULL,snapshot_sha256 TEXT NOT NULL,created_at TEXT NOT NULL,
         UNIQUE(campaign_id,source_group_id,target_group_id,source_claim_id,snapshot_sha256))""",
    "CREATE INDEX IF NOT EXISTS research_transfers_campaign ON research_transfers_v5(campaign_id,target_group_id,created_at)",
    """CREATE TABLE IF NOT EXISTS research_populations_v5(
         id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,group_id TEXT NOT NULL,
         generation INTEGER NOT NULL CHECK(generation>=0),parent_population_id TEXT,
         scope_snapshot_id TEXT NOT NULL,policy_id TEXT NOT NULL,
         selection_count INTEGER NOT NULL CHECK(selection_count>=1),request_key TEXT NOT NULL,
         created_at TEXT NOT NULL,
         UNIQUE(campaign_id,group_id,generation),UNIQUE(campaign_id,group_id,request_key))""",
    """CREATE TABLE IF NOT EXISTS research_variants_v5(
         id TEXT PRIMARY KEY,population_id TEXT NOT NULL,campaign_id TEXT NOT NULL,
         claim_node_id TEXT NOT NULL,parent_variant_ids_json TEXT NOT NULL,
         input_hashes_json TEXT NOT NULL,signals_json TEXT NOT NULL,
         score REAL NOT NULL,rank INTEGER NOT NULL,selected INTEGER NOT NULL CHECK(selected IN (0,1)),
         created_at TEXT NOT NULL,UNIQUE(population_id,claim_node_id),UNIQUE(population_id,rank))""",
    "CREATE INDEX IF NOT EXISTS research_variants_claim ON research_variants_v5(campaign_id,claim_node_id)",
    *(
        f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{operation}
             BEFORE {operation.upper()} ON {table} BEGIN
             SELECT RAISE(ABORT,'evolution snapshot is immutable'); END"""
        for table in ("research_transfers_v5", "research_populations_v5", "research_variants_v5")
        for operation in ("update", "delete")
    ),
)


def apply_v5_schema(database: Path, statements: Iterable[str] = V5_SCHEMA_STATEMENTS) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as db:
        try:
            db.execute("BEGIN IMMEDIATE")
            for statement in statements:
                db.execute(statement)
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "INSERT INTO v5_schema_meta(key,value,updated_at) VALUES('schema_version',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (str(V5_SCHEMA_VERSION), now),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
