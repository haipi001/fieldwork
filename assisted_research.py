"""Prepare bounded research from existing target, policy and local model records.

Preparation is read-only: it never confirms scope, installs tools or invokes a model.
"""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException


def infer_mode(target):
    from web3_contract_import import EXPLORERS
    raw=target.strip()
    if re.fullmatch(r"0x[0-9a-fA-F]{40}",raw) or urlsplit(raw).hostname in EXPLORERS:
        return 'web3'
    path=Path(raw).expanduser()
    if path.is_dir() and any((path/name).is_file() for name in ('foundry.toml','hardhat.config.js','hardhat.config.ts')):
        return 'web3'
    return 'traditional'


def automatic_name(resolved):
    target = resolved['normalized_target']
    label = urlsplit(target).hostname if '://' in target else Path(target).name
    label = label or target
    return (f"Research · {label}")[:160]


def prepare(engagement_id, overrides=None, preferred_profile_id=None):
    import final_core as core
    from v5_native_discovery import validate_profile
    engagement = core.get_engagement(engagement_id)
    values = {'execution_mode': 'real', 'scan_profile': 'quick', 'include_native_agent': False}
    derived = ['name', 'target_scope', 'request_budget', 'runtime_limit', 'scan_profile']
    source = engagement['normalized_target']
    if engagement.get('target_type') == 'repository' and Path(source).is_absolute() and Path(source).is_dir():
        values['source_path'] = source
        derived.append('source_path')
    ai = {'enabled': False, 'reason': 'no_healthy_local_profile', 'profile_id': None, 'name': None}
    if engagement['mode'] == 'traditional' and engagement.get('target_type') in {'host', 'url'}:
        with core.connect() as db:
            ids = [row['id'] for row in db.execute('SELECT id FROM runtime_profiles ORDER BY created_at DESC,id')]
        if preferred_profile_id in ids:
            ids.remove(preferred_profile_id)
            ids.insert(0, preferred_profile_id)
        for profile_id in ids:
            try:
                profile = validate_profile(profile_id)
            except HTTPException:
                continue
            values.update(include_native_agent=True, native_runtime_profile_id=profile_id)
            ai = {'enabled': True, 'reason': 'healthy_local_profile', 'profile_id': profile_id, 'name': profile['name']}
            derived.append('local_model_profile')
            break
    if overrides is not None:
        values.update(overrides)
    payload = core.StartAnalysisInput.model_validate(values)
    if payload.include_native_agent:
        # Explicit overrides never fall back to another profile or the cloud.
        profile = validate_profile(payload.native_runtime_profile_id)
        ai = {'enabled': True, 'reason': 'healthy_local_profile', 'profile_id': profile['id'], 'name': profile['name']}
    else:
        ai.update(enabled=False, profile_id=None, name=None)
    team_defaults=None
    if ai['enabled']:
        from v5_runtime import ProfileConfig
        config=ProfileConfig.model_validate(json.loads(profile['config_json']))
        team_defaults={'max_concurrency':1,'max_cost_micros':min(1_000_000,config.max_cost_micros) if config.max_cost_micros else 1_000_000,
                       'max_tokens_per_task':min(8000,config.max_tokens_per_call),'max_runtime_ms_per_task':min(45000,config.max_runtime_ms_per_call)}
    plan = core.build_execution_plan(engagement, payload)
    missing = [item for item in plan['blockers'] if item['id'] != 'scope']
    if ai['enabled'] and any(item['id']=='native-agent' and not item['ready'] for item in plan['tools']):
        missing.append({'id':'native-agent','message':'已匹配本机模型，但浏览器执行环境未就绪；修复后才能自动 AI 研究'})
    if payload.execution_mode == 'real' and plan['tools'] and not any(item['ready'] for item in plan['tools']):
        missing.append({'id': 'capabilities', 'message': '没有可运行的模型或工具；请配置至少一种执行能力'})
    # Confirmation changes readiness, but not the reviewed scope, input or plan.
    signed = {'engagement_id': engagement_id, 'scope_snapshot_id': engagement['current_scope_snapshot_id'],
              'policy_id': engagement['current_policy_id'], 'input': payload.model_dump(exclude={'assisted_plan_hash'}),
              'tools': plan['tools'], 'budget': plan['budget'], 'denied_actions': plan['denied_actions'],
              'warnings': plan['warnings'], 'missing': missing, 'contract_context': plan['contract_context']}
    digest = hashlib.sha256(json.dumps(signed, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return {'execution': payload.model_dump(exclude={'assisted_plan_hash'}), 'plan': plan, 'ai': ai,
            'team_defaults':team_defaults, 'derived': derived, 'missing': missing, 'plan_hash': digest,
            'scope_snapshot_id': engagement['current_scope_snapshot_id'], 'policy_id': engagement['current_policy_id'],
            'auto_start_ready': not missing}
