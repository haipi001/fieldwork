import shutil
import subprocess
from pathlib import Path

import pytest


def test_browser_worker_behaviors():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node runtime unavailable for extension unit tests')
    result = subprocess.run([node, '--test', 'tests/browser_collector.test.cjs'],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
