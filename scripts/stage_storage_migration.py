"""Create a verified file staging copy; does not activate or migrate the App."""
import argparse
import json
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'backend'), str(Path(__file__).resolve().parents[1])]
from storage_staging import stage

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    args = parser.parse_args()
    result = stage(args.source, args.destination)
    print(json.dumps({key: value for key, value in result.items() if key != 'files'}, indent=2))
