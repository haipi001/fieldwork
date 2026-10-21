"""Preserve public acceptance artifacts from an explicitly owned pytest fixture."""
import hashlib
import json
from pathlib import Path
import sqlite3


def archive(output, case, fixture_root):
    output=Path(output);root=Path(fixture_root).resolve(strict=True)
    inputs=json.loads((output/(case+'-inputs.json')).read_text())
    runs={row['run_id'] for row in inputs}
    if len(runs)!=1:raise ValueError('expected exactly one frozen fixture Run')
    run_id=runs.pop()
    database=root/'final-test.db'
    if database.is_symlink():raise ValueError('fixture database must not be a link')
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        run=db.execute('SELECT * FROM analysis_runs WHERE id=?',(run_id,)).fetchone()
        if not run:raise ValueError('fixture Run mismatch')
        artifacts=[dict(row) for row in db.execute('SELECT * FROM artifacts WHERE run_id=? ORDER BY id',(run_id,))]
        files=[]
        for artifact in artifacts:
            path=Path(artifact['uri'])
            if path.is_symlink():raise ValueError('artifact must not be a link')
            path.resolve(strict=True).relative_to(root)
            if not path.is_file() or path.stat().st_size>2_000_000:raise ValueError('artifact bounds exceeded')
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=artifact['sha256']:raise ValueError('registered artifact digest mismatch')
            if any(marker in raw for marker in (b'fixture=a',b'fixture=b',b'Cookie',b'Bearer ')):
                raise ValueError('artifact includes excluded credential field')
            name=case+'-registered-'+artifact['id']+'.json'
            destination=output/name
            if destination.exists():
                if destination.is_symlink() or destination.read_bytes()!=raw:raise ValueError('archived artifact differs')
            else:
                with destination.open('xb') as handle:handle.write(raw)
            destination.chmod(0o600)
            files.append({'artifact_id':artifact['id'],'kind':artifact['kind'],'sha256':artifact['sha256'],'file':name})
        facts={'run':dict(run),'artifacts':files}
        for table in ('observations','evidence_v2','candidate_findings','run_budgets_v2'):
            facts[table]=[dict(row) for row in db.execute('SELECT * FROM '+table+' WHERE run_id=?',(run_id,))]
        facts['canonical_findings']=[dict(row) for row in db.execute('SELECT f.* FROM canonical_findings f JOIN candidate_findings c ON c.id=f.candidate_id WHERE c.run_id=?',(run_id,))]
        for table,column in (('scope_snapshots','scope_snapshot_id'),('execution_policies','policy_id')):
            row=db.execute('SELECT * FROM '+table+' WHERE id=?',(run[column],)).fetchone()
            facts[table]=dict(row) if row else None
    raw=json.dumps(facts,indent=2).encode()
    destination=output/(case+'-registered-evidence.json')
    with destination.open('xb') as handle:handle.write(raw)
    destination.chmod(0o600)
    return {'case':case,'run_id':run_id,'artifact_count':len(files),'canonical_findings':len(facts['canonical_findings']),
        'facts_file':destination.name,'facts_sha256':hashlib.sha256(raw).hexdigest()}
