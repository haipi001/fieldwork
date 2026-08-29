#!/bin/zsh
set -euo pipefail
PROJECT_ROOT="${0:A:h:h}"
exec /Users/lizekai/anaconda3/bin/python3 "$PROJECT_ROOT/scripts/fieldwork_rollback.py" --yes

