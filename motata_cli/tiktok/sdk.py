from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path


REQUIRED_SDK_SYMBOLS = (
    "AccountManagementApi",
    "APPManagementApi",
    "CampaignCreationApi",
    "AdgroupApi",
    "AdApi",
    "FileApi",
    "CreativeManagementApi",
    "CatalogApi",
    "StoreApi",
    "ToolApi",
    "ReportingApi",
    "IdentityApi",
    "MeasurementApi",
    "AuthenticationApi",
)


def _has_required_sdk_symbols(module) -> bool:
    return all(hasattr(module, name) for name in REQUIRED_SDK_SYMBOLS)


def _clear_business_api_modules() -> None:
    for name in list(sys.modules):
        if name == "business_api_client" or name.startswith("business_api_client."):
            sys.modules.pop(name, None)


def _vendor_roots() -> list[Path]:
    roots: list[Path] = []

    env_root = os.environ.get("MOTATA_VENDOR_ROOT")
    if env_root:
        roots.append(Path(env_root).expanduser())

    # Bundled SDK inside the installed motata package.
    roots.append(Path(__file__).resolve().parents[1] / "_vendor" / "tiktok_business_api_sdk" / "python_sdk")
    # Local source-tree fallback for development checkouts.
    roots.append(Path(__file__).resolve().parents[2] / ".vendor" / "tiktok-business-api-sdk" / "python_sdk")
    return roots


def _load_vendor_business_api_client():
    for vendor_root in _vendor_roots():
        if not vendor_root.exists():
            continue
        _clear_business_api_modules()
        vendor_path = str(vendor_root)
        if vendor_path not in sys.path:
            sys.path.insert(0, vendor_path)
        import business_api_client  # type: ignore

        if _has_required_sdk_symbols(business_api_client):
            return business_api_client
    return None


@lru_cache(maxsize=1)
def get_business_api_client():
    try:
        import business_api_client  # type: ignore

        if _has_required_sdk_symbols(business_api_client):
            return business_api_client
    except ImportError as original_exc:
        vendor_client = _load_vendor_business_api_client()
        if vendor_client is not None:
            return vendor_client
        raise ImportError(
            "TikTok Business API SDK is unavailable. Reinstall motata-cli so the bundled SDK "
            "package is present."
        ) from original_exc
    vendor_client = _load_vendor_business_api_client()
    if vendor_client is not None:
        return vendor_client
    raise ImportError(
        "TikTok Business API SDK is missing required endpoints in the current environment. "
        "Use the bundled SDK that ships with motata-cli."
    )
