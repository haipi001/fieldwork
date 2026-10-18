import json
import sqlite3
from unittest.mock import patch
import pytest
from storage_reference_audit import audit_database


def test_plain_and_escaped_json_references_report_counts_only(tmp_path):
    database=tmp_path.resolve()/'db.sqlite';root=tmp_path.resolve()/'旧数据'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE configs(id TEXT,config TEXT)')
        db.executemany('INSERT INTO configs VALUES(?,?)', [('a',str(root/'repo')),('b',json.dumps({'source':str(root/'repo')},ensure_ascii=True)),('c',str(root)+'-different/repo')])
    result=audit_database(database,root)
    assert result['locations']==[{'table':'configs','column':'config','matching_rows':2}]
    assert str(root) not in json.dumps(result)
    assert not result['activation_allowed'] and not result['external_files_audited']


def test_bounded_audit_fails_instead_of_truncating(tmp_path):
    database=tmp_path.resolve()/'db.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE configs(value TEXT)');db.execute("INSERT INTO configs VALUES('large')")
    with patch('storage_reference_audit.MAX_VALUE_BYTES',1),pytest.raises(ValueError,match='reference_value_limit'):
        audit_database(database,tmp_path.resolve())


def test_json_file_audit_binds_manifest_and_redacts_values(tmp_path):
    import hashlib
    from storage_reference_audit import audit_json_files
    root=tmp_path.resolve();old=root/'旧数据'
    raw=json.dumps({'source_path':str(old/'sources'), 'credential':'fixture-do-not-return'}).encode()
    (root/'job.json').write_bytes(raw)
    record={'path':'job.json','type':'file','sha256':hashlib.sha256(raw).hexdigest()}
    result=audit_json_files(root,[record],old)
    assert result['matching_files']==1 and result['checked_files']==1
    assert result['locations']==[{'relative_path':'job.json','old_root_reference':True}]
    assert 'fixture-do-not-return' not in json.dumps(result) and str(old) not in json.dumps(result)
    (root/'job.json').write_text('{}')
    with pytest.raises(ValueError,match='reference_file_changed'):
        audit_json_files(root,[record],old)


def test_duplicate_json_keys_do_not_hide_escaped_old_root(tmp_path):
    database=tmp_path.resolve()/'db.sqlite';root=tmp_path.resolve()/'旧数据'
    value='{"source":'+json.dumps(str(root/'repo'))+',"source":"replacement"}'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE configs(value TEXT)')
        db.execute('INSERT INTO configs VALUES(?)',(value,))
    assert audit_database(database,root)['unresolved_reference_cells']==1


def test_generated_column_names_are_reported_correctly(tmp_path):
    database=tmp_path.resolve()/'db.sqlite';root=tmp_path.resolve()/'old'
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE configs(base TEXT, derived TEXT GENERATED ALWAYS AS (base || '/repo') STORED)")
        db.execute('INSERT INTO configs(base) VALUES(?)',(str(root),))
    result=audit_database(database,root)
    assert {x['column'] for x in result['locations']}=={'base','derived'}


def test_parser_recursion_limit_is_not_reported_as_no_references(tmp_path):
    database=tmp_path.resolve()/'db.sqlite';root=tmp_path.resolve()/'旧数据'
    value='['*2000+json.dumps(str(root/'repo'))+']'*2000
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE configs(value TEXT)')
        db.execute('INSERT INTO configs VALUES(?)',(value,))
    with pytest.raises(ValueError,match='reference_json_depth_limit'):
        audit_database(database,root)
