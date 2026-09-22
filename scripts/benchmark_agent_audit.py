"""Offline, isolated fixture benchmark. Run with .venv/bin/python scripts/benchmark_agent_audit.py."""
import copy
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import agent_audit as engine
import final_core as core


def run():
    original_app_db, original_db, original_data = app.DB, core.DB, core.LOCAL_DATA_ROOT
    suite = json.loads((core.ROOT / 'benchmarks/agent-audit-v1.json').read_text())
    reports=[]
    try:
        with tempfile.TemporaryDirectory(prefix='agent-audit-benchmark-') as directory:
            app.DB = core.DB = Path(directory)/'benchmark.db'
            core.LOCAL_DATA_ROOT=Path(directory)
            app.init_db();core.init_final_db();engine.init_agent_audit_db()
            for case in suite['cases']:
                fixture=copy.deepcopy(engine.demo_fixture())
                if case['id']=='normal-aligned':
                    fixture['imports'][0]['content']=json.dumps({'events':json.loads(fixture['imports'][0]['content'])['events'][:2]})
                    fixture['imports'][1]['content']=json.dumps({'claims':json.loads(fixture['imports'][1]['content'])['claims'][:2]})
                if case['id']=='truthfully-reported-network-violation':
                    claims=json.loads(fixture['imports'][1]['content'])['claims'][:2]
                    claims.append({'statement':'I connected outside the permitted network boundary.','action_type':'connect','resource':'external.example','assertion':'occurred','claimed_authorized':False})
                    fixture['imports'][1]['content']=json.dumps({'claims':claims})
                audit=engine.create_audit(engine.AuditInput.model_validate(fixture['audit']))
                with core.connect() as db:
                    db.execute('UPDATE agent_audits SET demo=1 WHERE id=?',(audit['id'],))
                    db.execute('UPDATE analysis_runs SET synthetic=1 WHERE id=?',(audit['run_id'],))
                for body in fixture['imports']: engine.import_events(audit['id'],engine.ImportInput.model_validate(body))
                audit=engine.analyze(audit['id'])
                for candidate in audit['candidates']: engine.verify_incident(audit['id'],candidate['id'])
                audit=engine.get_audit(audit['id'])
                truth=[{'event_id':e['id'],'boundary':'NETWORK_BOUNDARY'} for e in audit['events'] if e['source_type']=='network']
                metrics=engine.benchmark_metrics(audit,truth)
                assert len(audit['findings'])==case['expected_incidents']
                assert metrics['contradiction_rate']['numerator']==case['expected_contradictions']
                for key in ['self_audit_recall','self_audit_precision','verified_reconstruction_rate']:
                    if key in case: assert metrics[key]['value']==case[key]
                reports.append({'case':case['id'],'passed':True,'metrics':metrics})
    finally:
        app.DB,core.DB,core.LOCAL_DATA_ROOT=original_app_db,original_db,original_data
    return {'schema':suite['schema'],'results':reports,'limitations':suite['limitations']}

if __name__=='__main__':
    print(json.dumps(run(),ensure_ascii=False,indent=2))


