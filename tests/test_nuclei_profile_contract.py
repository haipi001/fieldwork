from pathlib import Path
import pytest
from fastapi import HTTPException
import traditional_tools
import final_core
from tests.test_final import client, create_ready


def test_standard_never_silently_downgrades_to_technology_tags(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    with pytest.raises(HTTPException) as error:
        traditional_tools.command_for('nuclei','http://fixture.test',scan_profile='standard')
    assert error.value.status_code==409
    root=tmp_path/'nuclei-templates/http'
    names=['technologies/tech-detect.yaml','misconfiguration/http-missing-security-headers.yaml',
           'miscellaneous/robots-txt.yaml','vulnerabilities/generic/cors-misconfig.yaml']
    for name in names:
        file=root/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('fixture')
    args=traditional_tools.command_for('nuclei','http://fixture.test',scan_profile='standard')
    assert args.count('-t')==4 and '-tags' not in args
    (root/names[-1]).unlink()
    with pytest.raises(HTTPException):
        traditional_tools.command_for('nuclei','http://fixture.test',scan_profile='standard')


def test_missing_standard_templates_block_plan_and_do_not_spend_tool_budget(client,tmp_path,monkeypatch):
    engagement=create_ready(client)
    run=client.post(f"/api/v1/engagements/{engagement['id']}/start").json()
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    plan=client.post(f"/api/v1/engagements/{engagement['id']}/execution-plan",json={'execution_mode':'real','scan_profile':'standard','include_native_agent':False}).json()
    assert not plan['ready'] and any(item['id']=='nuclei_templates' for item in plan['blockers'])
    with final_core.connect() as db:
        before=db.execute('SELECT tool_calls_used FROM run_budgets_v2 WHERE run_id=?',(run['id'],)).fetchone()[0]
    response=client.post(f"/api/v1/traditional/runs/{run['id']}/tools/run",json={'capability':'nuclei','scan_profile':'standard'})
    assert response.status_code==409,response.text
    with final_core.connect() as db:
        assert db.execute('SELECT tool_calls_used FROM run_budgets_v2 WHERE run_id=?',(run['id'],)).fetchone()[0]==before


def test_deep_profile_has_explicit_larger_template_set(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    with pytest.raises(HTTPException):
        traditional_tools.command_for('nuclei','http://fixture.test',scan_profile='deep')
    names=['technologies/tech-detect.yaml','misconfiguration/http-missing-security-headers.yaml',
           'miscellaneous/robots-txt.yaml','vulnerabilities/generic/cors-misconfig.yaml',
           'exposures/configs/git-config.yaml','exposures/apis/openapi.yaml']
    for name in names:
        path=tmp_path/'nuclei-templates/http'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
    args=traditional_tools.command_for('nuclei','http://fixture.test',scan_profile='deep')
    assert args.count('-t')==6 and '-tags' not in args
    assert 'dos,fuzz,intrusive' in args


def test_template_inventory_refuses_linked_parent_directory(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    outside=tmp_path/'outside';outside.mkdir();(outside/'tech-detect.yaml').write_text('fixture')
    root=tmp_path/'nuclei-templates/http';root.mkdir(parents=True)
    (root/'technologies').symlink_to(outside,target_is_directory=True)
    with pytest.raises(HTTPException) as error:
        traditional_tools.nuclei_template_inventory('quick')
    assert error.value.status_code==409


def test_queued_run_rechecks_reviewed_template_before_tool_call(client,tmp_path,monkeypatch):
    from tests.test_v5_team_plan import _setup
    import traditional_runtime
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    template=tmp_path/'nuclei-templates/http/technologies/tech-detect.yaml'
    template.parent.mkdir(parents=True);template.write_text('reviewed')
    _,_,run_id=_setup(client)
    expected=traditional_tools.nuclei_template_inventory('quick')
    with final_core.connect() as db:
        db.execute('INSERT INTO run_configs_v2 VALUES(?,?,?)',(run_id,final_core.dump({'reviewed_nuclei_templates':expected}),final_core.utcnow()))
    template.write_text('changed after plan was accepted')
    monkeypatch.setattr(traditional_runtime,'network_guard',lambda *args:None)
    response=client.post(f'/api/v1/traditional/runs/{run_id}/tools/run',json={'capability':'nuclei'})
    assert response.status_code==409,response.text
    assert '模板已变化' in response.json()['detail']
    with final_core.connect() as db:
        assert db.execute('SELECT count(*) FROM artifacts WHERE run_id=?',(run_id,)).fetchone()[0]==0
