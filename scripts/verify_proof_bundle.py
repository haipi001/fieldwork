#!/usr/bin/env python3
"""Check a Fieldwork ZIP without extracting or executing its contents."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reporting import replay_bundle, verify_bundle

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('bundle', type=Path)
parser.add_argument('--replay', action='store_true', help='Run an isolated local replay when the bundle supports it')
args = parser.parse_args()
try:
    result = replay_bundle(args.bundle, execute=True) if args.replay else verify_bundle(args.bundle)
    print(json.dumps(result, ensure_ascii=False, indent=2))
except Exception as error:
    print(json.dumps({'integrity_ok': False, 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)
