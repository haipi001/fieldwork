import sqlite3

import pytest

import lifecycle
from v6_schema import apply_v6_schema
from version import SCHEMA_VERSION


def test_receipt_binding_upgrade_preserves_history_and_is_immutable(tmp_path):
    database = tmp_path / 'fieldwork.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE verification_attempts(id TEXT PRIMARY KEY,result TEXT)')
        db.execute("INSERT INTO verification_attempts VALUES('historical','unchanged')")
        db.execute('PRAGMA user_version=34')
    backup = lifecycle.prepare_database_upgrade(database, tmp_path / 'backups', tmp_path)['backup']
    apply_v6_schema(database)
    lifecycle.finalize_database_version(database)
    with sqlite3.connect(database) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
        assert db.execute('SELECT result FROM verification_attempts').fetchone()[0] == 'unchanged'
        assert db.execute('SELECT COUNT(*) FROM verification_receipt_bindings_v6').fetchone()[0] == 0
        db.execute('INSERT INTO verification_receipt_bindings_v6 VALUES(?,?,?,?,?)',
                   ('receipt', 'candidate', 'run', 'a' * 64, 'fixture'))
        for statement in ('UPDATE verification_receipt_bindings_v6 SET receipt_sha256="changed"',
                          'DELETE FROM verification_receipt_bindings_v6'):
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                db.execute(statement)
    with sqlite3.connect(tmp_path / 'backups' / backup['database']) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 34
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
