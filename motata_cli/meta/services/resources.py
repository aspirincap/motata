from __future__ import annotations

from motata_cli.common.utils import parse_fields
from motata_cli.meta.utils import ad_account_path
import argparse
from typing import TYPE_CHECKING, Any

from motata_cli.meta.payloads import build_ad_payload, build_adset_payload, build_campaign_payload

if TYPE_CHECKING:
    from motata_cli.meta.client import MetaClient


def create_campaign(meta: MetaClient, account_id: str, args: argparse.Namespace) -> dict[str, Any]:
    payload = build_campaign_payload(args, update=False)
    return meta.post(f"{ad_account_path(account_id)}/campaigns", data=payload)


def create_adset(meta: MetaClient, account_id: str, args: argparse.Namespace) -> dict[str, Any]:
    payload = build_adset_payload(args, update=False)
    return meta.post(f"{ad_account_path(account_id)}/adsets", data=payload)


def create_ad(meta: MetaClient, account_id: str, args: argparse.Namespace) -> dict[str, Any]:
    payload = build_ad_payload(args, update=False)
    return meta.post(f"{ad_account_path(account_id)}/ads", data=payload)


def cleanup_object(meta: MetaClient, object_id: str) -> dict[str, Any]:
    return meta.delete(object_id)


def list_entities(
    meta: MetaClient,
    parent_path: str,
    edge: str,
    *,
    fields: list[str],
    extra_params: dict[str, Any] | None = None,
    limit: int = 25,
    fetch_all: bool = False,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"fields": parse_fields(fields, fields), "limit": limit}
    if extra_params:
        params.update({k: v for k, v in extra_params.items() if v is not None})
    if fetch_all:
        return meta.paginate(f"{parent_path}/{edge}", params=params)
    payload = meta.get(f"{parent_path}/{edge}", params=params)
    return payload.get("data") or []


def get_entity(meta: MetaClient, object_id: str, fields: list[str]) -> dict[str, Any]:
    return meta.get(object_id, params={"fields": parse_fields(fields, fields)})


def update_entity(meta: MetaClient, object_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return meta.post(object_id, data=payload)
