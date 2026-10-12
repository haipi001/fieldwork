"""Compile the production startup policy and exercise lifecycle/race decisions."""
from pathlib import Path
import shutil
import subprocess
import sys
import pytest


@pytest.mark.skipif(sys.platform!='darwin' or not shutil.which('swiftc'),reason='macOS Swift compiler required')
def test_owned_startup_recovers_late_without_relaunch_and_rejects_stale_callbacks(tmp_path):
    source=(Path(__file__).resolve().parents[1]/'macos/FieldworkApp.swift').read_text()
    policy=source.split('// STARTUP_RECOVERY_POLICY_BEGIN\n',1)[1].split('// STARTUP_RECOVERY_POLICY_END',1)[0]
    harness='''
var policy = StartupRecovery()
let first = policy.begin()
assert(policy.accepts(first))
assert(policy.action(probe: .absent, attempt: 0, ownedProcessAlive: nil) == .start)
assert(policy.action(probe: .absent, attempt: 0, ownedProcessAlive: true) == .poll)
assert(policy.action(probe: .absent, attempt: 59, ownedProcessAlive: true) == .poll)
assert(policy.action(probe: .absent, attempt: 60, ownedProcessAlive: true) == .wait)
assert(policy.action(probe: .absent, attempt: 900, ownedProcessAlive: true) == .wait)
assert(policy.action(probe: .ready, attempt: 900, ownedProcessAlive: true) == .load)
assert(policy.action(probe: .absent, attempt: 60, ownedProcessAlive: false) == .stopped)
assert(policy.action(probe: .absent, attempt: 60, ownedProcessAlive: nil) == .stopped)
for alive: Bool? in [nil, true, false] {
    assert(policy.action(probe: .foreign, attempt: 0, ownedProcessAlive: alive) == .rejectForeign)
    assert(policy.action(probe: .foreign, attempt: 900, ownedProcessAlive: alive) == .rejectForeign)
}
let retry = policy.begin()
assert(!policy.accepts(first) && policy.accepts(retry))
// Even a late successful response from the old probe cannot load the WebView.
assert(!policy.accepts(first))
policy.cancel()
assert(!policy.accepts(retry))
print("Late startup, owned-process preservation, foreign rejection and generation fences passed")
'''
    file=tmp_path/'main.swift';file.write_text(policy+harness)
    binary=tmp_path/'startup-policy'
    compiled=subprocess.run(['swiftc',str(file),'-o',str(binary)],capture_output=True,text=True,timeout=30)
    assert compiled.returncode==0,compiled.stderr
    result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
