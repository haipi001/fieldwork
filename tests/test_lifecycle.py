from pathlib import Path
import sqlite3

import lifecycle


def create_database(path: Path, version: int = 0) -> None:
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO sample(value) VALUES('evidence')")
        db.execute(f"PRAGMA user_version={version}")


def test_upgrade_backup_is_consistent_and_versioned(tmp_path, monkeypatch):
    database = tmp_path / "fieldwork.db"
    backup_root = tmp_path / "backups"
    create_database(database, 0)
    monkeypatch.setattr(lifecycle, "SCHEMA_VERSION", 1)
    result = lifecycle.prepare_database_upgrade(database, backup_root, tmp_path)
    assert result["previous_schema"] == 0 and result["target_schema"] == 1
    backup = result["backup"]
    assert backup and Path(backup["database_path"]).is_file()
    assert lifecycle.sha256(Path(backup["database_path"])) == backup["sha256"]
    assert lifecycle.database_integrity(Path(backup["database_path"]))
    lifecycle.finalize_database_version(database)
    assert lifecycle.database_schema_version(database) == 1
    assert lifecycle.list_backups(backup_root)[0]["valid"] is True


def test_newer_database_fails_closed(tmp_path, monkeypatch):
    database = tmp_path / "fieldwork.db"
    create_database(database, 9)
    monkeypatch.setattr(lifecycle, "SCHEMA_VERSION", 1)
    try:
        lifecycle.prepare_database_upgrade(database, tmp_path / "backups", tmp_path)
    except RuntimeError as error:
        assert "newer" in str(error)
    else:
        raise AssertionError("newer schema must fail closed")


def test_system_version_and_onboarding_contract(tmp_path, monkeypatch):
    import final_core
    database = tmp_path / "fieldwork.db"
    create_database(database, 1)
    monkeypatch.setattr(final_core, "DB", database)
    monkeypatch.setattr(final_core, "LOCAL_DATA_ROOT", tmp_path)
    monkeypatch.setattr(final_core, "runtime_status", lambda: {
        "provider": {"connected": True}, "native_agent": {"available": True, "browser": "system_chrome"},
        "storage": {"free_percent": 20, "free_bytes": 20 * 1024**3},
    })
    monkeypatch.setattr(final_core, "runtime_readiness", lambda: {
        "ready": True, "available_core": 14, "required_core": 14,
        "traditional": {name: True for name in ("httpx", "katana", "nuclei", "semgrep", "gitleaks", "trivy")},
        "web3": {name: True for name in ("forge", "anvil", "cast", "slither", "aderyn", "echidna", "medusa", "halmos")},
    })
    version = final_core.system_version()
    assert version["schema_version"] == 11
    status = final_core.onboarding_status()
    assert status["ready"] is True
    assert {item["id"] for item in status["checks"]} >= {"tools", "model", "chrome", "traditional", "web3", "database"}
