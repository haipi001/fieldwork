"""Create a verified file staging copy; does not activate or migrate the App."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage_staging import stage, verify_stage

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', nargs='?')
    parser.add_argument('destination', nargs='?')
    parser.add_argument('--verify', metavar='STAGE_DIRECTORY', help='Recheck an existing stage without copying or activating it')
    args = parser.parse_args()
    if args.verify:
        if args.source or args.destination:
            parser.error('--verify cannot be combined with source or destination')
        result = verify_stage(args.verify)
    else:
        if not args.source or not args.destination:
            parser.error('source and destination are required for staging')
        result = stage(args.source, args.destination)
    print(json.dumps({key: value for key, value in result.items() if key != 'files'}, indent=2))
