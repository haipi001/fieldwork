#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lifecycle import backup_database, list_backups, sha256  # noqa: E402

DATABASE = ROOT / "data" / "src_control.db"
BACKUPS = ROOT / "data" / "backups"


def active_runs() -> int:
    if not DATABASE.is_file():
        return 0
    with sqlite3.connect(DATABASE) as db:
        try:
            return int(db.execute("SELECT COUNT(*) FROM analysis_runs WHERE status IN ('queued','running','paused')").fetchone()[0])
        except sqlite3.OperationalError:
            return 0


def clean_worktree() -> bool:
    result = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True, check=True)
    return not result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Rollback Fieldwork code and database to the latest verified pre-upgrade backup.")
    parser.add_argument("--yes", action="store_true", help="confirm rollback")
    parser.add_argument("--database-only", action="store_true", help="restore database without switching Git commit")
    args = parser.parse_args()
    if not args.yes:
        print("Refusing rollback without --yes")
        return 2
    if active_runs():
        print("Refusing rollback while analysis tasks are active or paused")
        return 3
    candidates = [item for item in list_backups(BACKUPS) if item["valid"]]
    if not candidates:
        print("No verified rollback backup is available")
        return 4
    selected = candidates[0]
    if not args.database_only and not clean_worktree():
        print("Refusing code rollback because the Git worktree has uncommitted changes")
        return 5
    backup_database(DATABASE, BACKUPS, ROOT, "pre-rollback-safety")
    source = Path(selected["database_path"])
    temporary = DATABASE.with_suffix(".rollback.tmp")
    shutil.copy2(source, temporary)
    if sha256(temporary) != selected["sha256"]:
        temporary.unlink(missing_ok=True)
        print("Rollback backup checksum mismatch")
        return 6
    os.chmod(temporary, 0o600)
    temporary.replace(DATABASE)
    commit = selected.get("git_commit")
    if commit and not args.database_only:
        subprocess.run(["git", "-C", str(ROOT), "switch", "--detach", commit], check=True)
    print(f"Rolled back to {selected['id']}" + (f" / {commit[:12]}" if commit else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

