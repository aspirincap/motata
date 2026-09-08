#!/usr/bin/env python3
"""Run unit tests without network or access to the user's Motata state.

No dependency installation is performed. Subprocess lock tests are allowed;
sitecustomize propagates the network audit guard to Python child processes.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = '''import sys

def _motata_offline(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.bind"}:
        raise RuntimeError("Offline unit tests prohibit network access")
sys.addaudithook(_motata_offline)
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pattern', default='test*.py')
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    with tempfile.TemporaryDirectory(prefix="motata-unit-") as directory:
        home = Path(directory)
        for key in list(os.environ):
            if any(word in key.upper() for word in ("TOKEN", "SECRET", "API_KEY", "PASSWORD")):
                os.environ.pop(key, None)
        (home / "sitecustomize.py").write_text(GUARD)
        dependency_paths = [entry for entry in sys.path if entry and "site-packages" in entry]
        os.environ.update(HOME=directory, MOTATA_HOME=str(home / ".motata"),
                          PYTHONDONTWRITEBYTECODE="1",
                          PYTHONPATH=os.pathsep.join((directory, str(ROOT), *dependency_paths)))
        if os.name == "nt":
            os.environ["USERPROFILE"] = directory
        sys.path.insert(0, str(ROOT))
        exec(GUARD, {})
        tests = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=args.pattern)
        result = unittest.TextTestRunner(verbosity=1).run(tests)
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
