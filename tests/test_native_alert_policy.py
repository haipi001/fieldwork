"""Compile the native cursor state machine without notification permissions or delivery."""
from pathlib import Path
import subprocess
import shutil
import sys
import pytest


@pytest.mark.skipif(sys.platform!="darwin" or not shutil.which("swiftc"),reason="macOS Swift compiler required")
def test_native_cursor_baseline_dedup_failure_retry_and_disable_generation(tmp_path):
    root=Path(__file__).resolve().parents[1]
    source=(root/'macos/LocalAlertNotifications.swift').read_text().split('// NATIVE_ALERT_POLICY_BEGIN')[1].split('// NATIVE_ALERT_POLICY_END')[0]
    (tmp_path/'main.swift').write_text(source+'''
assert(NativeAlertPolicy.acceptsOrigin(scheme: "http", host: "127.0.0.1", port: 8000, path: "/v5", mainFrame: true, expectedPort: 8000))
assert(!NativeAlertPolicy.acceptsOrigin(scheme: "https", host: "remote.test", port: 8000, path: "/v5", mainFrame: true, expectedPort: 8000))
assert(!NativeAlertPolicy.acceptsOrigin(scheme: "http", host: "127.0.0.1", port: 8001, path: "/v5", mainFrame: true, expectedPort: 8000))
assert(!NativeAlertPolicy.acceptsOrigin(scheme: "http", host: "127.0.0.1", port: 8000, path: "/v5", mainFrame: false, expectedPort: 8000))
assert(!NativeAlertPolicy.acceptsOrigin(scheme: "http", host: "127.0.0.1", port: 8000, path: "/external", mainFrame: true, expectedPort: 8000))
assert(NativeAlertPolicy.acceptsCommand(action: "status", keys: ["action", "language"]))
assert(!NativeAlertPolicy.acceptsCommand(action: "notify", keys: ["action"]))
assert(!NativeAlertPolicy.acceptsCommand(action: "enable", keys: ["action", "body"]))
var policy = NativeAlertPolicy()
assert(!policy.shouldDeliver(cursor: 10, count: 3))
assert(!policy.shouldDeliver(cursor: 10, count: 3))
assert(policy.shouldDeliver(cursor: 11, count: 1))
assert(policy.shouldDeliver(cursor: 11, count: 1)) // failed delivery did not advance
let old = policy.generation
policy.commit(cursor: 11, generation: old)
assert(!policy.shouldDeliver(cursor: 11, count: 1))
assert(!policy.shouldDeliver(cursor: 12, count: 0))
policy.reset()
policy.commit(cursor: 99, generation: old) // disabled or re-enabled during async delivery
assert(policy.cursor == nil)
assert(!policy.shouldDeliver(cursor: 20, count: 5)) // no history on a fresh session
assert(!policy.shouldDeliver(cursor: 1, count: 0)) // database reset establishes baseline
assert(policy.shouldDeliver(cursor: 2, count: 1))
print("native cursor policy passed")
''')
    binary=tmp_path/'policy'
    subprocess.run(['/usr/bin/swiftc',str(tmp_path/'main.swift'),'-o',str(binary)],check=True,capture_output=True,timeout=30)
    result=subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=5)
    assert result.stdout.strip()=='native cursor policy passed'
