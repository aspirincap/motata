#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

import requests

from motata_cli import __version__
from motata_cli.init_command import register_init_command
from motata_cli.meta.client import configure_meta_debug
from motata_cli.meta.output import configure_output
from motata_cli.meta import CliError, ensure_dirs, register_meta_commands
from motata_cli.metrics import register_metrics_commands
from motata_cli.product import register_product_commands
from motata_cli.report import register_report_commands
from motata_cli.tiktok import register_tiktok_commands
from motata_cli.update import maybe_emit_skills_drift_notice, register_update_command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="motata", description="Motata CLI")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    register_init_command(subparsers)
    register_product_commands(subparsers)
    register_meta_commands(subparsers)
    register_tiktok_commands(subparsers)
    register_metrics_commands(subparsers)
    register_report_commands(subparsers)
    register_update_command(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_dirs()
    parser = build_parser()
    args = parser.parse_args(argv)
    maybe_emit_skills_drift_notice(command_name=getattr(args, "command", None))
    configure_output(getattr(args, "output", None))
    configure_meta_debug(getattr(args, "debug", False))
    try:
        args.func(args)
        return 0
    except CliError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    except requests.HTTPError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
