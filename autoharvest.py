#!/usr/bin/env python3
import os
import sys
from pathlib import Path

# Auto-activate local .venv if run with system python
root_dir = Path(__file__).resolve().parent
venv_python = root_dir / ".venv" / "bin" / "python3"
if sys.prefix == sys.base_prefix and venv_python.exists() and sys.executable != str(venv_python):
    os.execv(str(venv_python), [str(venv_python)] + sys.argv)

if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from autoharvest.cli import main

if __name__ == "__main__":
    main()
