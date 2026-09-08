#!/usr/bin/env python3
"""Delegate to motata-cli installed in this interpreter; npm users use the CLI."""
from __future__ import annotations

from motata_cli.product.intake import main


if __name__ == "__main__":
    raise SystemExit(main())
