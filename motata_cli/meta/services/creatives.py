from __future__ import annotations

from motata_cli.meta.utils import ad_account_path
import argparse
from typing import TYPE_CHECKING, Any

from motata_cli.meta.payloads import build_creative_payload

if TYPE_CHECKING:
    from motata_cli.meta.client import MetaClient


def create_creative(meta: MetaClient, account_id: str, args: argparse.Namespace) -> dict[str, Any]:
    payload = build_creative_payload(args)
    return meta.post(f"{ad_account_path(account_id)}/adcreatives", data=payload)
