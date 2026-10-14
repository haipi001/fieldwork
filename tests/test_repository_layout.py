"""Exercise legacy launch/import paths from outside the source checkout."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_app_entry_resolves_one_backend_with_repository_resources(tmp_path):
    environment = dict(os.environ, PYTHONPATH=str(ROOT))
    environment.pop("FIELDWORK_DATA_DIRECTORY", None)
    code = """
from pathlib import Path
import app, final_core, reporting
root = Path(__import__('sys').argv[1])
assert Path(app.__file__) == root / 'backend/app.py'
assert app is __import__('app')
assert app.ROOT == final_core.ROOT == reporting.ROOT == root
assert app.DB.parent == root / 'data'
assert (app.ROOT / 'templates/index.html').is_file()
assert (app.ROOT / 'static/app.js').is_file()
assert reporting.FINAL.is_dir()
assert reporting.adapter_config('hackerone')['required']
"""
    result = subprocess.run([sys.executable, '-c', code, str(ROOT)], cwd=tmp_path,
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_backend_benchmark_cli_finds_repository_manifest(tmp_path):
    environment = dict(os.environ, PYTHONPATH=str(ROOT / 'backend'))
    code = """
import benchmarking
from pathlib import Path
assert (Path(benchmarking.__file__).resolve().parents[1] / 'benchmarks/logic-v4.json').is_file()
"""
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path,
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
