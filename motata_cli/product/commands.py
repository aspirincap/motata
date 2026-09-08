from __future__ import annotations

import argparse
from pathlib import Path

from motata_cli.common.errors import CliError
from motata_cli.meta.output import print_output
from motata_cli.common.utils import validate_non_empty, write_json_file

from .intake import build_product_intake
from .scraper import scrape_product


def command_product_scrape(args: argparse.Namespace) -> None:
    url = validate_non_empty(getattr(args, "url", None), "url")
    result = scrape_product(url)
    if "error" in result:
        raise CliError(result["error"])

    output_file = getattr(args, "output_file", None)
    if output_file:
        write_json_file(Path(output_file).expanduser(), result)
    print_output(result, as_json=args.json)


def command_product_intake(args: argparse.Namespace) -> None:
    url = validate_non_empty(getattr(args, "url", None), "url")
    result = build_product_intake(url)
    if "error" in result:
        raise CliError(result["error"])

    output_file = getattr(args, "output_file", None)
    if output_file:
        write_json_file(Path(output_file).expanduser(), result)
    print_output(result, as_json=args.json)


def register_product_commands(subparsers) -> None:
    product = subparsers.add_parser("product", help="Product and app page scraping helpers")
    product_sub = product.add_subparsers(dest="product_command", required=True)

    p = product_sub.add_parser("scrape", help="Scrape product/app name, price, and image URLs from a page")
    p.add_argument("url")
    p.add_argument("--output-file")
    p.add_argument("--json", action="store_true", default=True)
    p.set_defaults(func=command_product_scrape)

    intake = product_sub.add_parser(
        "intake",
        help="Build a strategy-ready product/app intake with page signals and execution hints",
    )
    intake.add_argument("url")
    intake.add_argument("--output-file")
    intake.add_argument("--json", action="store_true", default=True)
    intake.set_defaults(func=command_product_intake)
