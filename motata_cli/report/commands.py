from __future__ import annotations

import argparse

from motata_cli.init_profile import PLATFORM_TOKEN_ENV_VARS
from motata_cli.report.meta import command_meta_report_run
from motata_cli.report.tiktok import command_tiktok_report_run


def add_common_report_arguments(parser: argparse.ArgumentParser, *, platform: str) -> None:
    parser.add_argument("--access-token", default=None)
    parser.add_argument("--json", action="store_true", default=True)
    env_vars = PLATFORM_TOKEN_ENV_VARS.get(platform, ())
    if env_vars:
        parser.epilog = (
            (parser.epilog + "\n\n") if parser.epilog else ""
        ) + f"Access token can also come from environment: {', '.join(env_vars)}."


def register_report_commands(subparsers) -> None:
    report = subparsers.add_parser("report", help="Report data-pull orchestration")
    report_subparsers = report.add_subparsers(dest="report_command", required=True)

    meta = report_subparsers.add_parser("meta", help="Meta report data pulls")
    meta_subparsers = meta.add_subparsers(dest="report_meta_command", required=True)

    p = meta_subparsers.add_parser("run", help="Pull Meta data sources for daily/weekly/custom reports")
    p.add_argument("--account-id", "--account", dest="account_id")
    p.add_argument("--all-init-accounts", action="store_true", help="Run once for every account saved by `motata init` for this platform.")
    add_common_report_arguments(p, platform="meta")
    p.add_argument("--period", choices=["daily", "weekly", "custom"], default="weekly")
    p.add_argument("--depth", choices=["fast", "standard", "full", "deep"], default="standard")
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--previous-since")
    p.add_argument("--previous-until")
    p.add_argument("--no-compare", dest="compare", action="store_false", default=True)
    p.add_argument("--run-dir")
    p.add_argument("--dry-run", action="store_true", help="Print the planned data sources without calling Meta APIs.")
    p.add_argument("--retry", type=int, default=2, help="Attempts per source before marking it degraded.")
    p.add_argument("--retry-wait", type=float, default=60.0, help="Seconds to wait before retrying a failed source.")
    p.add_argument("--top-objects", type=int, default=30, help="Top ad/adset objects to structure-enrich for standard/full runs.")
    p.add_argument("--limit", type=int, help="Override per-request insight/list page size.")
    p.add_argument(
        "--include-previews",
        dest="include_previews",
        action="store_true",
        default=None,
        help="Allow landing/ad preview enrichment during the pull. Default: on for standard/full/deep, off for fast.",
    )
    p.add_argument(
        "--no-previews",
        dest="include_previews",
        action="store_false",
        help="Disable preview enrichment for faster pulls.",
    )
    p.add_argument("--include-product", action="store_true", help="Enable landing product page scraping/enrichment.")
    p.set_defaults(func=command_meta_report_run)

    tiktok = report_subparsers.add_parser("tiktok", help="TikTok report data pulls")
    tiktok_subparsers = tiktok.add_subparsers(dest="report_tiktok_command", required=True)

    p = tiktok_subparsers.add_parser("run", help="Pull TikTok data sources for daily/weekly/custom reports")
    p.add_argument("--advertiser-id", "--account-id", "--account", dest="advertiser_id")
    p.add_argument("--all-init-accounts", action="store_true", help="Run once for every account saved by `motata init` for this platform.")
    add_common_report_arguments(p, platform="tiktok")
    p.add_argument("--period", choices=["daily", "weekly", "custom"], default="weekly")
    p.add_argument("--depth", choices=["fast", "standard", "full", "deep"], default="standard")
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--previous-since")
    p.add_argument("--previous-until")
    p.add_argument("--no-compare", dest="compare", action="store_false", default=True)
    p.add_argument("--run-dir")
    p.add_argument("--dry-run", action="store_true", help="Print the planned data sources without calling TikTok APIs.")
    p.add_argument("--retry", type=int, default=2, help="Attempts per source before marking it degraded.")
    p.add_argument("--retry-wait", type=float, default=60.0, help="Seconds to wait before retrying a failed source.")
    p.add_argument("--top-objects", type=int, help="Top campaign/adgroup/ad objects to enrich for standard/full runs.")
    p.add_argument("--page-size", type=int, help="Override TikTok report page size.")
    p.add_argument("--smart-plus", action="store_true", help="Use Smart+ routes for detail enrichment when appropriate.")
    p.add_argument(
        "--tiktok-report-mode",
        choices=["auto", "auction", "gmv_max", "hybrid"],
        default="auto",
        help="TikTok report source mode. auto detects GMV Max-first accounts before pulling full sources.",
    )
    p.add_argument(
        "--include-gmv-max",
        choices=["auto", "always", "never"],
        default="auto",
        help="Whether to include GMV Max sources in TikTok report pulls.",
    )
    p.add_argument("--gmv-max-store-id", dest="gmv_max_store_ids", action="append", help="Restrict GMV Max reports to one store_id. Repeatable.")
    p.add_argument(
        "--gmv-max-promotion-type",
        dest="gmv_max_promotion_types",
        action="append",
        choices=["PRODUCT_GMV_MAX", "LIVE_GMV_MAX"],
        help="GMV Max promotion type to include. Defaults to both PRODUCT_GMV_MAX and LIVE_GMV_MAX.",
    )
    p.add_argument(
        "--gmv-max-creative-dimensions",
        choices=["official", "fast"],
        default="official",
        help="GMV Max creative report dimensions. official uses campaign_id+item_group_id+item_id; fast uses campaign_id+item_id.",
    )
    p.add_argument(
        "--include-previews",
        dest="include_previews",
        action="store_true",
        default=None,
        help="Allow targeted creative preview enrichment after top ads are selected. Default: on for standard/full/deep, off for fast.",
    )
    p.add_argument(
        "--no-previews",
        dest="include_previews",
        action="store_false",
        help="Disable preview enrichment for faster pulls.",
    )
    p.add_argument("--include-product", action="store_true", help="Enable landing product page scraping/enrichment.")
    p.set_defaults(func=command_tiktok_report_run)
