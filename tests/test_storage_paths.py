import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]


def run(code,directory=None):
    environment=dict(os.environ)
    environment.pop('FIELDWORK_DATA_DIRECTORY',None)
    environment['FIELDWORK_PORT']='8000'
    if directory is not None:environment['FIELDWORK_DATA_DIRECTORY']=str(directory)
    return subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=environment,capture_output=True,text=True,timeout=30)


CODE='''
import json,os
import app,final_core,reporting,native_agent,web3_practice,web3_analysis,traditional_runtime,traditional_tools,runtime_secrets
from storage_paths import data_directory
root=data_directory()
os.environ['FIELDWORK_DATA_DIRECTORY']='/invalid-after-import'
assert data_directory()==root
assert app.DATA==final_core.LOCAL_DATA_ROOT==root
assert app.DB==final_core.DB==root/'src_control.db'
assert reporting.EXPORTS==root/'exports'
assert native_agent.WORKSPACE_ROOT==root/'agent_workspaces'
assert web3_practice.RESULTS==root/'web3-practice'
assert web3_analysis.ARTIFACT_ROOT==traditional_runtime.ARTIFACT_ROOT==traditional_tools.ARTIFACT_ROOT==root/'artifacts'
assert traditional_tools.REPOSITORY_ROOT==root/'repositories'
assert runtime_secrets.secret_root()==root/'runtime-secrets'
print(json.dumps({'root':str(root)}))
'''


def test_default_shared_location():
    result=run(CODE)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['root']==str(ROOT/'data')


def test_configured_shared_location_and_real_writes(tmp_path):
    directory=tmp_path.resolve()/'isolated';directory.mkdir(mode=0o700)
    code=CODE+'''
app.init_db()
final_core.init_final_db()
runtime_secrets.put('test-reference','isolated-fixture')
assert runtime_secrets.get('test-reference')=='isolated-fixture'
reporting.EXPORTS.mkdir()
(reporting.EXPORTS/'fixture').write_text('fixture')
'''
    result=run(code,directory)
    assert result.returncode==0,result.stderr
    assert (directory/'src_control.db').is_file()
    assert (directory/'runtime-secrets'/'test-reference').is_file()
    assert (directory/'exports'/'fixture').read_text()=='fixture'


@pytest.mark.parametrize('mode',['relative','empty','missing','public','link','parent_link'])
def test_invalid_configuration_stops_instead_of_fallback(tmp_path,mode):
    root=tmp_path.resolve();directory=root/'private';directory.mkdir(mode=0o700)
    value=str(directory)
    if mode=='relative':value='relative'
    elif mode=='empty':value=''
    elif mode=='missing':value=str(root/'missing')
    elif mode=='public':directory.chmod(0o755)
    elif mode=='link':
        link=root/'link';link.symlink_to(directory,target_is_directory=True);value=str(link)
    else:
        (directory/'child').mkdir(mode=0o700)
        link=root/'link';link.symlink_to(directory,target_is_directory=True);value=str(link/'child')
    result=run('import app',value)
    assert result.returncode!=0
    assert not (directory/'src_control.db').exists()


def test_configured_backend_startup_and_restart_use_same_database(tmp_path):
    directory=tmp_path.resolve()/'isolated-backend';directory.mkdir(mode=0o700)
    code=CODE+'''
from fastapi.testclient import TestClient
import sqlite3
os.environ['FIELDWORK_DISABLE_AGENT_MONITOR']='1'
os.environ['FIELDWORK_DISABLE_CAMPAIGN_SCHEDULER']='1'
os.environ['FIELDWORK_SESSION_TOKEN']='s'*48
with TestClient(app.app,base_url='http://127.0.0.1:8000') as client:
    response=client.get('/api/v1/workspace/maintenance',headers={'X-Fieldwork-Session':'s'*48})
    assert response.status_code==200,response.text
    assert response.json()['data_directory']==str(root)
    assert response.json()['schema']==__import__('version').SCHEMA_VERSION
with sqlite3.connect(root/'src_control.db') as db:
    db.execute('CREATE TABLE IF NOT EXISTS storage_restart_fixture (id INTEGER PRIMARY KEY)')
    db.execute('INSERT OR IGNORE INTO storage_restart_fixture VALUES(1)')
    assert db.execute('SELECT COUNT(*) FROM storage_restart_fixture').fetchone()[0]==1
'''
    result=run(code,directory)
    assert result.returncode==0,result.stderr
    restart=code.replace("    db.execute('CREATE TABLE IF NOT EXISTS storage_restart_fixture (id INTEGER PRIMARY KEY)')\n",'').replace("    db.execute('INSERT OR IGNORE INTO storage_restart_fixture VALUES(1)')\n",'')
    for _ in range(2):
        result=run(restart,directory)
        assert result.returncode==0,result.stderr
    assert (directory/'src_control.db').is_file()
