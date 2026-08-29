from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from version import APP_VERSION, SCHEMA_VERSION


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit(root: Path) -> str | None:
    try:
        value = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
        return value or None
    except (OSError, subprocess.SubprocessError):
        return None


def database_schema_version(database: Path) -> int:
    if not database.is_file():
        return 0
    with sqlite3.connect(database) as db:
        return int(db.execute("PRAGMA user_version").fetchone()[0])


def database_integrity(database: Path) -> bool:
    if not database.is_file():
        return True
    with sqlite3.connect(database) as db:
        return db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def backup_database(database: Path, backup_root: Path, project_root: Path, reason: str) -> dict[str, Any] | None:
    if not database.is_file():
        return None
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup_id = f"{utc_stamp()}-{reason}"
    backup_path = backup_root / f"{backup_id}.db"
    with sqlite3.connect(database) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    os.chmod(backup_path, 0o600)
    if not database_integrity(backup_path):
        backup_path.unlink(missing_ok=True)
        raise RuntimeError("database backup integrity check failed")
    manifest = {
        "id": backup_id,
        "reason": reason,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": backup_path.name,
        "sha256": sha256(backup_path),
        "app_version": APP_VERSION,
        "schema_version": database_schema_version(database),
        "git_commit": git_commit(project_root),
    }
    manifest_path = backup_root / f"{backup_id}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    os.chmod(manifest_path, 0o600)
    return {**manifest, "database_path": str(backup_path), "manifest_path": str(manifest_path)}


def prepare_database_upgrade(database: Path, backup_root: Path, project_root: Path) -> dict[str, Any]:
    previous = database_schema_version(database)
    if previous > SCHEMA_VERSION:
        raise RuntimeError(f"database schema {previous} is newer than application schema {SCHEMA_VERSION}")
    backup = None
    if database.is_file() and previous < SCHEMA_VERSION:
        backup = backup_database(database, backup_root, project_root, f"pre-schema-{previous}-to-{SCHEMA_VERSION}")
    return {"previous_schema": previous, "target_schema": SCHEMA_VERSION, "backup": backup}


def finalize_database_version(database: Path) -> None:
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE IF NOT EXISTS app_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL)")
        now = datetime.now(timezone.utc).isoformat()
        db.execute("INSERT OR REPLACE INTO app_metadata VALUES(?,?,?)", ("app_version", APP_VERSION, now))
        db.execute("INSERT OR REPLACE INTO app_metadata VALUES(?,?,?)", ("schema_version", str(SCHEMA_VERSION), now))
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def list_backups(backup_root: Path) -> list[dict[str, Any]]:
    results = []
    for manifest_path in sorted(backup_root.glob("*.json"), reverse=True) if backup_root.is_dir() else []:
        try:
            manifest = json.loads(manifest_path.read_text())
            database_path = backup_root / manifest["database"]
            valid = database_path.is_file() and sha256(database_path) == manifest.get("sha256") and database_integrity(database_path)
            results.append({**manifest, "valid": valid, "manifest_path": str(manifest_path), "database_path": str(database_path)})
        except (OSError, KeyError, json.JSONDecodeError, sqlite3.Error):
            continue
    return results

