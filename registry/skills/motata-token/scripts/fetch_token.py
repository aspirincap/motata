#!/usr/bin/env python3
"""Delegate to the installed CLI so security fixes have one source of truth."""
from motata_cli.auth_center.fetch_token import main

if __name__ == "__main__":
    raise SystemExit(main())
