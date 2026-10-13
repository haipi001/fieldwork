import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from storage_staging import stage


def fixture(tmp_path):
    source = tmp_path.resolve()/'source'; source.mkdir(mode=0o700)
    target = tmp_path.resolve()/'target'; target.mkdir(mode=0o700)
    (source/'artifacts').mkdir(); (source/'artifacts/proof').write_bytes(b'proof')
    (source/'export.zip').write_bytes(b'package')
    with sqlite3.connect(source/'src_control.db') as db:
        db.execute('CREATE TABLE artifacts(id TEXT PRIMARY KEY,uri TEXT,sha256 TEXT)')
        db.execute('INSERT INTO artifacts VALUES(?,?,?)', ('a',str(source/'artifacts/proof'),hashlib.sha256(b'proof').hexdigest()))
        db.execute('CREATE TABLE submission_packages_v2(id TEXT PRIMARY KEY,export_path TEXT)')
        db.execute('INSERT INTO submission_packages_v2 VALUES(?,?)', ('p',str(source/'export.zip')))
        db.execute('CREATE TABLE historical_receipt(id TEXT PRIMARY KEY,payload TEXT)')
        db.execute('INSERT INTO historical_receipt VALUES(?,?)', ('r',json.dumps({'historical_source':str(source/'artifacts/proof')})))
    return source,target


def test_stage_relocates_live_references_preserving_original_and_history(tmp_path):
    source,target=fixture(tmp_path)
    original=(source/'src_control.db').read_bytes()
    result=stage(source,target); root=Path(result['stage_directory'])/'data'
    assert result['reference_relocation']['changed_references']==2
    with sqlite3.connect(root/'src_control.db') as db:
        assert db.execute('SELECT uri FROM artifacts').fetchone()[0]==str(root/'artifacts/proof')
        assert db.execute('SELECT export_path FROM submission_packages_v2').fetchone()[0]==str(root/'export.zip')
        assert json.loads(db.execute('SELECT payload FROM historical_receipt').fetchone()[0])['historical_source']==str(source/'artifacts/proof')
    assert (source/'src_control.db').read_bytes()==original
    item=next(x for x in result['files'] if x['path']=='src_control.db')
    assert hashlib.sha256((root/'src_control.db').read_bytes()).hexdigest()==item['sha256']
    assert not result['activation_allowed']


@pytest.mark.parametrize('mode',['digest','external','missing','trigger'])
def test_bad_reference_rolls_back_all_relocations(tmp_path,mode):
    source,target=fixture(tmp_path)
    with sqlite3.connect(source/'src_control.db') as db:
        if mode=='digest': db.execute("UPDATE artifacts SET sha256='wrong'")
        elif mode=='external': db.execute("UPDATE submission_packages_v2 SET export_path='/outside/package.zip'")
        elif mode=='missing': db.execute('UPDATE submission_packages_v2 SET export_path=?',(str(source/'missing'),))
        else: db.execute("CREATE TRIGGER bad AFTER UPDATE ON artifacts BEGIN DELETE FROM historical_receipt; END")
    with pytest.raises((ValueError,OSError)):
        stage(source,target)
    root=next(target.glob('migration-stage-*'))
    assert (root/'FAILED.json').exists() and not (root/'manifest.json').exists()
    with sqlite3.connect(root/'data/src_control.db') as db:
        assert db.execute('SELECT uri FROM artifacts').fetchone()[0]==str(source/'artifacts/proof')
        assert db.execute('SELECT count(*) FROM historical_receipt').fetchone()[0]==1
