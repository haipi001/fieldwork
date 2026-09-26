import copy
import json
import platform
import shutil
import subprocess
from pathlib import Path

import pytest
import native_ai_events as native


@pytest.fixture(scope='module')
def emitted(tmp_path_factory):
    if platform.system() != 'Darwin' or not shutil.which('xcrun'):
        pytest.skip('Apple SDK required for native collector build')
    root = Path(__file__).resolve().parents[1]
    binary = tmp_path_factory.mktemp('native') / 'collector'
    build = subprocess.run(['xcrun','clang','-fobjc-arc','-fblocks','-Wall','-Wextra','-Werror',
        str(root/'collectors/native-ai/collector.m'),'-framework','Foundation','-lbsm','-lEndpointSecurity','-o',str(binary)],
        capture_output=True,text=True,timeout=60)
    assert build.returncode == 0, build.stderr
    result = subprocess.run([str(binary),'--self-test'],capture_output=True,text=True,timeout=10)
    assert result.returncode == 0 and 'SYNTHETIC' in result.stderr
    return [json.loads(line) for line in result.stdout.splitlines()]


def test_native_close_mapping_does_not_claim_read_or_write_success(emitted):
    assert len(emitted) == 2
    assert all(event['synthetic'] is True for event in emitted)
    assert emitted[0]['modified'] is False and emitted[0]['mapped_writable'] is True
    assert emitted[1]['modified'] is True
    assert emitted[0]['path_truncated'] is False
    assert emitted[1]['path_truncated'] is True
    assert all(event['action_type'] == 'unknown' for event in emitted)
    assert all(event['status'] == 'observed' for event in emitted)
    assert emitted[1]['kernel_dropped'] == 1
    assert all(event['process_pid_version'] == 7 for event in emitted)


def test_native_contract_rejects_simulation_private_fields_and_false_success(emitted):
    with pytest.raises(ValueError,match='模拟'):
        native.normalize(emitted[0],'session')
    record = copy.deepcopy(emitted[0])
    record['synthetic'] = False  # Contract fixture only, never imported as real telemetry.
    normalized = native.normalize(record,'session')
    assert normalized['session_id'] == 'session'
    assert normalized['mapped_writable'] is True
    assert normalized['path_truncated'] is False
    with pytest.raises(ValueError,match='布尔值'):
        native.normalize({**record,'path_truncated':'false'},'session')
    private = {**record,'argv':['secret']}
    with pytest.raises(ValueError,match='未知字段'):
        native.normalize(private,'session')
    with pytest.raises(ValueError,match='副作用成功'):
        native.normalize({**record,'status':'success'},'session')
    with pytest.raises(ValueError,match='不一致'):
        native.normalize({**record,'action_type':'read'},'session')
