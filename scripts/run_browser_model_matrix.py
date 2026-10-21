"""Run one frozen public development matrix; never retry paid calls automatically."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def snapshot():
    paths=[]
    for directory in ('backend','static','templates','tests'):
        paths.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py','.js','.css','.html'})
    paths.extend([Path(__file__).resolve(),ROOT/'scripts/archive_browser_matrix_evidence.py'])
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}


def run():
    if os.environ.get('FIELDWORK_LIVE_BROWSER_MODEL')!='1':
        raise SystemExit('Explicit FIELDWORK_LIVE_BROWSER_MODEL=1 required; public loopback data only.')
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output=ROOT/'build'/('browser-model-frozen-matrix-'+stamp)
    output.mkdir(parents=True,exist_ok=False)
    manifest={'started_at':stamp,'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'sources':snapshot(),'scenarios':['vulnerable','fixed','legitimate_shared'],
        'expected_findings':{'vulnerable':1,'fixed':0,'legitimate_shared':0},
        'model_call_limit_per_scenario':1,'scenario_budget_micros':250000,
        'fixture_kind':'public synthetic development loopback; not holdout or native clean-start acceptance',
        'status':'running'}
    (output/'manifest-before.json').write_text(json.dumps(manifest,indent=2))
    print('Frozen matrix:',output,flush=True)
    env=dict(os.environ,FIELDWORK_BROWSER_ACCEPTANCE_OUTPUT=str(output))
    with (output/'pytest.log').open('w') as log:
        process=subprocess.Popen([sys.executable,'-m','pytest','-q','tests/test_live_browser_model_pipeline.py'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        # Wait for exactly this child; an observation timeout never starts a replacement.
        result=process.wait()
    after=snapshot();stable=manifest['sources']==after
    summaries={}
    for case in manifest['scenarios']:
        path=output/(case+'.json')
        summaries[case]=json.loads(path.read_text()) if path.exists() else {'missing':True}
    cost=sum(item['cost_micros'] for report in summaries.values() for item in report.get('usage',[]))
    artifacts={str(p.relative_to(output)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()}
    final={'process_exit_code':result,'source_stable':stable,'sources_after':after,'case_summaries':summaries,
        'settled_cost_micros_at_configured_bound':cost,'artifact_sha256s':artifacts,
        'status':'passed' if result==0 and stable and all(not row.get('missing') and row.get('registered_evidence') for row in summaries.values()) else 'failed_or_source_changed',
        'scope':manifest['fixture_kind']}
    (output/'matrix-result.json').write_text(json.dumps(final,indent=2))
    print(json.dumps({k:final[k] for k in ('process_exit_code','source_stable','status','settled_cost_micros_at_configured_bound')}),flush=True)
    return 0 if final['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(run())
