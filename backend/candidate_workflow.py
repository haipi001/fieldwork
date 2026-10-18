"""Deterministic dispatch triage, not a vulnerability verdict.

Only exact records in the same run are coalesced. Similar titles or targets
across runs never suffice to discard evidence or reuse a verdict.
"""
import hashlib
import json
from collections import Counter

RULE_VERSION = 'candidate-workflow-v1'


def annotate(candidates, items):
    records = {record['id']: record for record in candidates}
    owners = {}
    for item in items:
        record = records[item['candidate_id']]
        evidence = record.get('evidence_ids', [])
        if isinstance(evidence, str):
            evidence = json.loads(evidence)
        material = [record.get(key) for key in (
            'engagement_id', 'run_id', 'mode', 'category', 'target', 'title',
            'hypothesis', 'status')]
        material.append(sorted(set(evidence)))
        digest = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
        duplicate = owners.get(digest)
        owners.setdefault(digest, record['id'])
        method = item['draft']['verification_method']
        blockers = sorted({gap['code'] for gap in item['gaps']})
        if duplicate:
            lane = 'duplicate'
        elif item.get('missing_evidence_ids') or not evidence or not item['sources']:
            lane = 'needs_evidence'
        elif method == 'observation_review':
            lane = 'observation'
        elif method == 'impact_review':
            lane = 'impact_review'
        elif method in ('specialized', 'web3_property'):
            lane = 'unsupported_verifier'
        elif item['draft'].get('http_binding') and not blockers:
            lane = 'verification_ready'
        else:
            lane = 'blocked'
        item['triage'] = {
            'rule_version': RULE_VERSION, 'lane': lane, 'duplicate_of': duplicate,
            'fingerprint': digest, 'blockers': blockers,
            'dispatch_eligible': lane == 'verification_ready',
            'verdict': 'unconfirmed', 'history_preserved': True,
        }
    return dict(Counter(item['triage']['lane'] for item in items))
