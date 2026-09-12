"""Bounded local economic regression lab, separate from target findings."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from web3_lab import binary

router = APIRouter(prefix='/api/v1/web3/practice', tags=['Web3 local practice'])
ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / 'fixtures' / 'web3-practice'
RESULTS = ROOT / 'data' / 'web3-practice'
LOCK = threading.Lock()
SOURCES = ('foundry.toml', 'src/PracticeVaults.sol', 'test/EconomicPractice.t.sol')
CASES = ('Accounting', 'Authority', 'Inflation')
SEEDS = ('0xf13d01', '0xf13d02')
EXPECTED = {f'testFuzz_{case}{variant}(uint96)' for case in CASES for variant in ('Vulnerable', 'Fixed')} | {'test_InflationVulnerableMeasured()', 'test_InflationFixedMeasured()'}


def evaluate_round(payload, exit_code):
    tests = []
    for suite, result in payload.items():
        if not isinstance(result, dict):
            continue
        for name, item in result.get('test_results', {}).items():
            metrics = {}
            for line in item.get('decoded_logs', []):
                match = re.fullmatch(r'(accounting_drift_wei|attacker_capital_wei|attacker_gain_wei|victim_loss_wei): ([0-9]+)', str(line))
                if match:
                    metrics[match[1]] = match[2]  # Decimal strings avoid browser float precision loss.
            tests.append({'name': name, 'suite': suite, 'status': item.get('status'),
                          'reason': item.get('reason'), 'metrics': metrics,
                          'runs': item.get('kind', {}).get('Fuzz', {}).get('runs')})
    by_name = {test['name']: test for test in tests}
    checks = {
        'execution_success': exit_code == 0,
        'exact_test_inventory': set(by_name) == EXPECTED and len(tests) == len(EXPECTED),
        'all_assertions_passed': bool(tests) and all(t['status'] == 'Success' for t in tests),
        'fuzz_coverage': all(by_name.get(n, {}).get('runs', 0) == 64 for n in EXPECTED if n.startswith('testFuzz_')),
    }
    for variant, gain in [('Vulnerable', '100'), ('Fixed', '0')]:
        measured = by_name.get(f'test_Inflation{variant}Measured()', {}).get('metrics', {})
        checks[f'inflation_{variant.lower()}_economic_control'] = measured == {
            'attacker_capital_wei': '101', 'attacker_gain_wei': gain, 'victim_loss_wei': gain}
    return {'passed': all(checks.values()), 'checks': checks, 'tests': tests}


@router.get('')
def catalog():
    return {'available': bool(binary('forge')), 'cases': [
        {'id': 'Accounting', 'title': '账本一致性', 'invariant': '完整取款后余额与总资产同时归零'},
        {'id': 'Authority', 'title': '提款权限', 'invariant': '非所有者无法提款，所有者仍可正常提款'},
        {'id': 'Inflation', 'title': '份额舍入与捐赠', 'invariant': '存款最小份额约束，攻击收益与用户损失逐项核对'},
    ], 'boundary': '仅运行内置本地 EVM 夹具，无 RPC、私钥或链上交易。通过表示这些已知正反样本可复现，不表示真实项目安全或存在漏洞。收益以 wei 计，未扣 gas。'}


@router.post('/run')
def run_practice():
    forge = binary('forge')
    if not forge:
        raise HTTPException(409, 'Forge 未安装；实战校验未执行')
    if not LOCK.acquire(blocking=False):
        raise HTTPException(409, '已有本地实战校验正在运行')
    try:
        report = {'id': uuid.uuid4().hex, 'schema': 'fieldwork-local-economic-regression/1',
                  'created_at': datetime.now(timezone.utc).isoformat(), 'rounds': [],
                  'source_hashes': {}, 'environment': 'local_evm_fixture', 'boundary': catalog()['boundary']}
        with tempfile.TemporaryDirectory(prefix='fieldwork-economic-') as temp:
            root = Path(temp)
            for name in SOURCES:
                source = FIXTURE / name
                content = source.read_bytes()
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                report['source_hashes'][name] = hashlib.sha256(content).hexdigest()
            for seed in SEEDS:
                argv = [forge, 'test', '--json', '-vv', '--fuzz-runs', '64', '--fuzz-seed', seed]
                try:
                    process = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=45)
                    payload = json.loads(process.stdout)
                    round_result = evaluate_round(payload, process.returncode)
                    round_result['output_sha256'] = hashlib.sha256(process.stdout.encode()).hexdigest()
                    if not round_result['passed']:
                        round_result['diagnostic'] = process.stderr[-2000:]
                except (subprocess.TimeoutExpired, OSError, ValueError, TypeError, AttributeError) as error:
                    round_result = {'passed': False, 'error': type(error).__name__, 'checks': {}, 'tests': []}
                report['rounds'].append({'seed': seed, **round_result})
            report['status'] = 'passed' if all(r['passed'] for r in report['rounds']) else 'failed'
            report['replay'] = {'commands': [f'forge test --json -vv --fuzz-runs 64 --fuzz-seed {seed}' for seed in SEEDS],
                                'working_directory': 'source'}
            RESULTS.mkdir(parents=True, exist_ok=True)
            bundle_root = root / 'bundle'
            bundle_root.mkdir()
            (bundle_root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            for name in SOURCES:
                target = bundle_root / 'source' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / name, target)
            shutil.make_archive(str(RESULTS / report['id']), 'zip', bundle_root)
            (RESULTS / f"{report['id']}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        return report
    finally:
        LOCK.release()


@router.get('/results/{result_id}/download')
def download(result_id: str):
    if not re.fullmatch(r'[0-9a-f]{32}', result_id):
        raise HTTPException(404, '校验报告不存在')
    path = RESULTS / f'{result_id}.zip'
    if not path.is_file():
        raise HTTPException(404, '校验报告不存在')
    return FileResponse(path, media_type='application/zip', filename=f'fieldwork-web3-practice-{result_id}.zip')
