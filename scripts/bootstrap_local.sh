#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="${0:A:h:h}"
VENV_PATH="${FIELDWORK_VENV_PATH:-$PROJECT_ROOT/.venv}"
PYTHON_BIN="${FIELDWORK_PYTHON:-python3}"
RECREATE=0
WITH_DEV=0

for arg in "$@"; do
  case "$arg" in
    --recreate) RECREATE=1 ;;
    --dev) WITH_DEV=1 ;;
    *) print -u2 "Unknown option: $arg"; exit 2 ;;
  esac
done

if [[ "$VENV_PATH" == "/" || "$VENV_PATH" == "$HOME" || -z "$VENV_PATH" ]]; then
  print -u2 "Refusing unsafe virtual environment path: $VENV_PATH"
  exit 2
fi

if (( RECREATE )); then
  "$PYTHON_BIN" -m venv --clear "$VENV_PATH"
elif [[ ! -x "$VENV_PATH/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_PATH"
fi

"$VENV_PATH/bin/python" -m pip install --disable-pip-version-check -r "$PROJECT_ROOT/requirements.txt"
if (( WITH_DEV )); then
  "$VENV_PATH/bin/python" -m pip install --disable-pip-version-check -r "$PROJECT_ROOT/requirements-dev.txt"
fi
PYTHONPATH="$PROJECT_ROOT" "$VENV_PATH/bin/python" - <<'PY'
import app
import cryptography
from version import APP_VERSION, BUILD_NUMBER, SCHEMA_VERSION

print(f"Fieldwork {APP_VERSION} build {BUILD_NUMBER} schema {SCHEMA_VERSION}")
print(f"cryptography {cryptography.__version__}")
print("bootstrap smoke: PASS")
PY

print "Environment ready: $VENV_PATH"
print "Start Fieldwork: $VENV_PATH/bin/uvicorn app:app --host 127.0.0.1 --port 8000"
