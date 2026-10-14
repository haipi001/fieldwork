"""Compatibility entry point; implementation lives in backend/desktop_server.py."""
import importlib
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))
if __name__ == "__main__":
    runpy.run_module("desktop_server", run_name="__main__")
else:
    sys.modules.pop(__name__, None)
    sys.modules[__name__] = importlib.import_module("desktop_server")
