import sqlite3
from pathlib import Path

import lifecycle
from v5_schema import V5_SCHEMA_STATEMENTS, V5_SCHEMA_VERSION, apply_v5_schema


EXPECTED_TABLES = {
    "v5_schema_meta", "runtime_profiles", "runtime_providers", "runtime_usage",
    "runtime_route_decisions", "runtime_usage_reports",
    "research_nodes", "research_edges", "research_groups", "agent_tasks",
    "research_checkpoints", "verification_receipts_v5", "verification_requests_v5",
    "verification_receipt_bindings_v5", "runner_registry_v5",
    "continuous_research_state", "intel_records", "intel_matches", "v5_events",
    "agent_registry_v5", "agent_memory_snapshots_v5", "agent_supply_components_v5",
    "agent_attack_tests_v5",
    "research_transfers_v5", "research_populations_v5", "research_variants_v5",
}


def tables(database: Path) -> set[str]:
    with sqlite3.connect(database) as db:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_additive_schema_preserves_legacy_and_does_not_promote(tmp_path, monkeypatch):
    database = tmp_path / "fieldwork.db"
    backup_root = tmp_path / "backups"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE candidate_findings(id TEXT PRIMARY KEY,status TEXT NOT NULL)")
        db.execute("INSERT INTO candidate_findings VALUES('legacy-candidate','verified')")
        db.execute("PRAGMA user_version=19")
    monkeypatch.setattr(lifecycle, "SCHEMA_VERSION", 20)
    prepared = lifecycle.prepare_database_upgrade(database, backup_root, tmp_path)
    assert prepared["backup"] and prepared["previous_schema"] == 19
    apply_v5_schema(database)
    lifecycle.finalize_database_version(database)
    assert EXPECTED_TABLES <= tables(database)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT status FROM candidate_findings WHERE id='legacy-candidate'").fetchone()[0] == "verified"
        assert db.execute("SELECT COUNT(*) FROM research_nodes").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM verification_receipts_v5").fetchone()[0] == 0
        assert db.execute("SELECT value FROM v5_schema_meta WHERE key='schema_version'").fetchone()[0] == str(V5_SCHEMA_VERSION)
    assert lifecycle.database_schema_version(database) == 20
    assert lifecycle.list_backups(backup_root)[0]["valid"] is True


def test_schema_is_idempotent(tmp_path):
    database = tmp_path / "fieldwork.db"
    apply_v5_schema(database)
    before = tables(database)
    apply_v5_schema(database)
    assert tables(database) == before


def test_failed_schema_transaction_rolls_back_every_v5_object(tmp_path):
    database = tmp_path / "fieldwork.db"
    statements = (V5_SCHEMA_STATEMENTS[0], "CREATE TABLE rollback_probe(id TEXT)", "THIS IS NOT SQL")
    try:
        apply_v5_schema(database, statements)
    except sqlite3.Error:
        pass
    else:
        raise AssertionError("invalid migration must fail")
    assert "v5_schema_meta" not in tables(database)
    assert "rollback_probe" not in tables(database)


def test_backup_is_a_working_rollback_source(tmp_path, monkeypatch):
    database = tmp_path / "fieldwork.db"
    backup_root = tmp_path / "backups"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE legacy(id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO legacy VALUES('preserved')")
        db.execute("PRAGMA user_version=19")
    monkeypatch.setattr(lifecycle, "SCHEMA_VERSION", 20)
    backup = lifecycle.prepare_database_upgrade(database, backup_root, tmp_path)["backup"]
    apply_v5_schema(database)
    restored = tmp_path / "restored.db"
    with sqlite3.connect(backup["database_path"]) as source, sqlite3.connect(restored) as target:
        source.backup(target)
    assert lifecycle.database_integrity(restored)
    assert lifecycle.database_schema_version(restored) == 19
    assert EXPECTED_TABLES.isdisjoint(tables(restored))
    with sqlite3.connect(restored) as db:
        assert db.execute("SELECT id FROM legacy").fetchone()[0] == "preserved"


def test_schema_23_to_24_backup_preserves_existing_v5_data(tmp_path, monkeypatch):
    database = tmp_path / "fieldwork.db"
    backup_root = tmp_path / "backups"
    apply_v5_schema(database)
    with sqlite3.connect(database) as db:
        for table in ("research_transfers_v5", "research_populations_v5", "research_variants_v5"):
            db.execute(f"DROP TABLE {table}")
        db.execute("UPDATE v5_schema_meta SET value='4' WHERE key='schema_version'")
        db.execute("CREATE TABLE legacy_marker(id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO legacy_marker VALUES('kept')")
        db.execute("PRAGMA user_version=23")
    monkeypatch.setattr(lifecycle, "SCHEMA_VERSION", 24)
    prepared = lifecycle.prepare_database_upgrade(database, backup_root, tmp_path)
    assert prepared["previous_schema"] == 23 and prepared["target_schema"] == 24
    assert prepared["backup"] and lifecycle.database_integrity(Path(prepared["backup"]["database_path"]))
    assert {"research_transfers_v5", "research_populations_v5", "research_variants_v5"}.isdisjoint(
        tables(Path(prepared["backup"]["database_path"])))
    apply_v5_schema(database)
    lifecycle.finalize_database_version(database)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT id FROM legacy_marker").fetchone()[0] == "kept"
        assert db.execute("SELECT value FROM v5_schema_meta WHERE key='schema_version'").fetchone()[0] == "5"
    assert lifecycle.database_schema_version(database) == 24
    assert lifecycle.database_schema_version(Path(prepared["backup"]["database_path"])) == 23
