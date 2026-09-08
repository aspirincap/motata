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
from motata_cli.common.errors import CliError
from motata_cli.common.security import network_error, redact
from motata_cli.meta import ensure_dirs, register_meta_commands
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
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_output(getattr(args, "output", None))
    configure_meta_debug(getattr(args, "debug", False))
    try:
        if getattr(args, "requires_live_probe", False) and not getattr(args, "allow_live_probe", False):
            raise CliError(
                "This validation creates real platform objects. Review the side effects, "
                "then explicitly pass --allow-live-probe (and --cleanup to request cleanup). "
                "PAUSED is not a dry-run."
            )
        ensure_dirs()
        maybe_emit_skills_drift_notice(command_name=getattr(args, "command", None))
        result = args.func(args)
        # Handlers may return an explicit process result after writing their JSON
        # or report manifest. Legacy handlers returning None remain successful.
        if isinstance(result, int) and not isinstance(result, bool):
            return result
        if isinstance(result, dict):
            if "exit_code" in result:
                return int(result["exit_code"])
            if isinstance(result.get("completeness"), dict):
                return int(result["completeness"].get("exit_code", 0))
            status = result.get("execution_status") or result.get("status")
            if status in {"failed", "needs_review"}:
                return 1
            if status in {"partial_success", "degraded"}:
                return 3
            if result.get("ok") is False:
                return 1
        return 0
    except CliError as exc:
        print(json.dumps({"error": redact(str(exc)), "exit_code": exc.exit_code}, ensure_ascii=False, indent=2), file=sys.stderr)
        return exc.exit_code
    except requests.RequestException as exc:
        print(json.dumps({"error": network_error("HTTP", exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": redact(str(exc))}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
