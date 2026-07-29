from __future__ import annotations

import argparse

from motata_cli.meta.output import print_output

from .presets import load_probe_file, recommend_metric_presets


def command_metrics_presets_recommend(args: argparse.Namespace) -> None:
    probe = load_probe_file(getattr(args, "probe_file", None))
    result = recommend_metric_presets(
        platform=args.platform,
        user_type=args.user_type,
        probe=probe,
        w2a=getattr(args, "w2a", False),
    )
    print_output(result, as_json=True)


def register_metrics_commands(subparsers) -> None:
    metrics = subparsers.add_parser("metrics", help="Cross-platform metric probe and preset helpers")
    metrics_sub = metrics.add_subparsers(dest="metrics_command", required=True)

    presets = metrics_sub.add_parser("presets", help="Metric preset recommendations")
    presets_sub = presets.add_subparsers(dest="presets_command", required=True)

    p = presets_sub.add_parser("recommend")
    p.add_argument("--platform", choices=["meta", "tiktok", "all"], default="all")
    p.add_argument("--user-type", required=True)
    p.add_argument("--probe-file")
    p.add_argument("--w2a", action="store_true", help="Treat the account as App/W2A when building presets.")
    p.add_argument("--json", action="store_true", default=True)
    p.set_defaults(func=command_metrics_presets_recommend)
