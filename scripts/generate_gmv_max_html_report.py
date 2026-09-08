#!/usr/bin/env python3
"""Compatibility entry point; the renderer is included in wheel and npm installs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motata_cli.report.gmv_max_html import *  # noqa: F403 - historical script API

if __name__ == "__main__":
    raise SystemExit(main())
