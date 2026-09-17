from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import requests

from motata_cli.transport.gateway import GatewayAuthRef, RemoteGatewayTransport, gateway_enabled, auth_ref
from motata_cli.common.errors import CliError
from motata_cli.common.security import network_error, redact

_SDK_TIMEOUT = (10, 120)

from .sdk import get_business_api_client


def _to_plain(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return _to_plain(value.to_dict())
    if isinstance(value, dict):
        return {key: _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    return value


def _compact_params(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in values.items()
        if value is not None and value != [] and value != {}
    }


def _extract_collection(response: dict[str, Any]) -> list[dict[str, Any]]:
    data = response.get("data")
    if isinstance(data, dict):
        for key in ("list", "campaigns", "items", "adgroups", "ads", "videos"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


class TikTokClient:
    BASE_URL = "https://business-api.tiktok.com/open_api/v1.3/"
    PAGE_GET_URL = "https://business-api.tiktok.com/open_api/v1.3/page/get/"

    def __init__(self, access_token: str | GatewayAuthRef | None):
        self.gateway = None
        self.auth_ref = None
        if isinstance(access_token, GatewayAuthRef) or gateway_enabled():
            if access_token is not None and not isinstance(access_token, GatewayAuthRef):
                raise CliError("Direct access tokens are disabled in gateway mode.", exit_code=2)
            self.auth_ref = access_token or auth_ref("tiktok")
            self.gateway = RemoteGatewayTransport.from_environment()
            self.access_token = None
            # Do not create the SDK's thread pool or secret-bearing client for gateway reads.
            return
        self.access_token = access_token
        self.sdk = get_business_api_client()
        self.api_client = self.sdk.ApiClient()
        self.auth_api = self.sdk.AuthenticationApi(self.api_client)
        self.account_api = self.sdk.AccountManagementApi(self.api_client)
        self.app_api = self.sdk.APPManagementApi(self.api_client)
        self.campaign_api = self.sdk.CampaignCreationApi(self.api_client)
        self.adgroup_api = self.sdk.AdgroupApi(self.api_client)
        self.ad_api = self.sdk.AdApi(self.api_client)
        self.file_api = self.sdk.FileApi(self.api_client)
        self.creative_api = self.sdk.CreativeManagementApi(self.api_client)
        self.catalog_api = self.sdk.CatalogApi(self.api_client)
        self.measurement_api = self.sdk.MeasurementApi(self.api_client)
        self.store_api = self.sdk.StoreApi(self.api_client)
        self.tool_api = self.sdk.ToolApi(self.api_client)
        self.reporting_api = self.sdk.ReportingApi(self.api_client)
        self.identity_api = self.sdk.IdentityApi(self.api_client)

    def _invoke(self, fn, *args, **kwargs) -> dict[str, Any]:
        if getattr(self, "gateway", None) is not None:
            raise CliError("GATEWAY_OPERATION_UNAVAILABLE: this SDK path is not migrated yet.")
        kwargs["_request_timeout"] = _SDK_TIMEOUT
        try:
            return _to_plain(fn(*args, **kwargs))
        except Exception as exc:  # SDK exception bodies can contain credentials.
            # SDK method names are not a reliable read/write classification.
            # Conservatively warn on unknown outcomes; never retry here.
            raise CliError(network_error("TikTok API", exc, write=True)) from None

    def _list_entities(self, method, advertiser_id: str, **params) -> dict[str, Any]:
        return self._invoke(method, advertiser_id, self.access_token, **_compact_params(params))

    def _list_entities_raw(
        self,
        path: str,
        advertiser_id: str,
        *,
        filtering: dict[str, Any] | None = None,
        page: int | None = None,
        page_size: int | None = None,
        fields: list[str] | None = None,
        exclude_field_types: list[str] | None = None,
        exclude_field_types_in_response: list[str] | None = None,
    ) -> dict[str, Any]:
        excluded_fields = exclude_field_types if exclude_field_types is not None else exclude_field_types_in_response
        return self._raw_request(
            "GET",
            path,
            params={
                "advertiser_id": advertiser_id,
                "filtering": filtering,
                "page": page,
                "page_size": page_size,
                "fields": fields,
                "exclude_field_types_in_response": excluded_fields,
            },
            timeout=15,
        )

    def _raw_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> dict[str, Any]:
        if getattr(self, "gateway", None) is not None:
            envelope = self.gateway.request(auth=self.auth_ref, method=method.upper(), path=path,
                                            query=params, body=json_body)
            return self._check_response(envelope["data"])
        serialized_params: dict[str, Any] = {}
        for key, value in (params or {}).items():
            if value is None or value == [] or value == {}:
                continue
            if isinstance(value, (dict, list)):
                serialized_params[key] = json.dumps(value, ensure_ascii=False)
            else:
                serialized_params[key] = value

        try:
            response = requests.request(
                method.upper(),
                f"{self.BASE_URL}{path.lstrip('/')}",
                params=serialized_params or None,
                json=json_body,
                headers={"Access-Token": self.access_token},
                timeout=(10, timeout),
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            # No implicit retries: a failed write may already have been applied.
            raise CliError(network_error("TikTok API", exc, write=method.upper() not in {"GET", "HEAD", "OPTIONS"})) from None

        return self._check_response(data)

    def _check_response(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise CliError("TikTok API returned an invalid response object.")
        if data.get("code") not in (None, 0, "0"):
            safe = redact(data, (self.access_token,))
            raise CliError(redact(
                "TikTok API request failed: "
                f"Error Code: {safe.get('code')}, message: {safe.get('message')}, request_id: {safe.get('request_id')}",
                (self.access_token,),
            ))
        return data

    def create_changelog_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._raw_request(
            "POST",
            "changelog/task/create/",
            json_body=payload,
        )

    def check_changelog_task(self, advertiser_id: str, task_id: str) -> dict[str, Any]:
        return self._raw_request(
            "GET",
            "changelog/task/check/",
            params={
                "advertiser_id": str(advertiser_id),
                "task_id": str(task_id),
            },
        )

    def download_changelog_task(self, advertiser_id: str, task_id: str) -> dict[str, Any]:
        return self._raw_request(
            "GET",
            "changelog/task/download/",
            params={
                "advertiser_id": str(advertiser_id),
                "task_id": str(task_id),
            },
        )

    def list_business_centers(self) -> dict[str, Any]:
        return self._raw_request("GET", "bc/get/", params={})

    def list_bc_assets(
        self,
        bc_id: str,
        *,
        asset_type: str,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._raw_request(
            "GET",
            "bc/asset/get/",
            params={
                "bc_id": bc_id,
                "asset_type": asset_type,
                "page": page,
                "page_size": page_size,
            },
        )

    def _get_single_entity(
        self,
        list_fn,
        advertiser_id: str,
        *,
        filtering: dict[str, Any],
        page_size: int = 1,
        entity_label: str,
        entity_id: str,
        **extra,
    ) -> dict[str, Any]:
        response = list_fn(
            advertiser_id,
            filtering=filtering,
            page=1,
            page_size=page_size,
            **extra,
        )
        matches = _extract_collection(response)
        if not matches:
            raise CliError(f"TikTok {entity_label} not found: {entity_id}")
        return matches[0]

    def list_campaigns(
        self,
        advertiser_id: str,
        *,
        filtering: dict[str, Any] | None = None,
        page: int | None = None,
        page_size: int | None = None,
        fields: list[str] | None = None,
        exclude_field_types: list[str] | None = None,
        smart_plus: bool = False,
    ) -> dict[str, Any]:
        return self._list_entities_raw(
            "smart_plus/campaign/get/" if smart_plus else "campaign/get/",
            advertiser_id,
            filtering=filtering,
            page=page,
            page_size=page_size,
            fields=fields,
            exclude_field_types_in_response=exclude_field_types,
        )

    def list_gmv_max_campaigns(
        self,
        advertiser_id: str,
        *,
        filtering: dict[str, Any],
        page: int | None = None,
        page_size: int | None = None,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._raw_request(
            "GET",
            "gmv_max/campaign/get/",
            params={
                "advertiser_id": advertiser_id,
                "filtering": filtering,
                "page": page,
                "page_size": page_size,
                "fields": fields,
            },
            timeout=15,
        )

    def get_gmv_max_campaign(
        self,
        advertiser_id: str,
        campaign_id: str,
    ) -> dict[str, Any]:
        return self._invoke(
            self.campaign_api.campaign_gmv_max_info,
            advertiser_id,
            str(campaign_id),
            self.access_token,
        )

    def get_campaign(
        self,
        advertiser_id: str,
        campaign_id: str,
        *,
        fields: list[str] | None = None,
        smart_plus: bool = False,
    ) -> dict[str, Any]:
        return self._get_single_entity(
            self.list_campaigns,
            advertiser_id,
            filtering={"campaign_ids": [str(campaign_id)]},
            entity_label="campaign",
            entity_id=campaign_id,
            fields=fields,
            smart_plus=smart_plus,
        )

    def create_campaign(self, payload: dict[str, Any], *, smart_plus: bool = False) -> dict[str, Any]:
        method = self.campaign_api.smart_plus_campaign_create if smart_plus else self.campaign_api.campaign_create
        return self._invoke(method, self.access_token, body=payload)

    def update_campaign(self, payload: dict[str, Any], *, smart_plus: bool = False) -> dict[str, Any]:
        method = self.campaign_api.smart_plus_campaign_update if smart_plus else self.campaign_api.campaign_update
        return self._invoke(method, self.access_token, body=payload)

    def update_campaign_status(self, payload: dict[str, Any], *, smart_plus: bool = False) -> dict[str, Any]:
        method = (
            self.campaign_api.smart_plus_campaign_status_update
            if smart_plus
            else self.campaign_api.campaign_status_update
        )
        return self._invoke(method, self.access_token, body=payload)

    def list_adgroups(
        self,
        advertiser_id: str,
        *,
        filtering: dict[str, Any] | None = None,
        page: int | None = None,
        page_size: int | None = None,
        fields: list[str] | None = None,
        exclude_field_types: list[str] | None = None,
        smart_plus: bool = False,
    ) -> dict[str, Any]:
        return self._list_entities_raw(
            "smart_plus/adgroup/get/" if smart_plus else "adgroup/get/",
            advertiser_id,
            filtering=filtering,
            page=page,
            page_size=page_size,
            fields=fields,
            exclude_field_types_in_response=exclude_field_types,
        )

    def get_adgroup(
        self,
        advertiser_id: str,
        adgroup_id: str,
        *,
        fields: list[str] | None = None,
        smart_plus: bool = False,
    ) -> dict[str, Any]:
        return self._get_single_entity(
            self.list_adgroups,
            advertiser_id,
            filtering={"adgroup_ids": [str(adgroup_id)]},
            entity_label="adgroup",
            entity_id=adgroup_id,
            fields=fields,
            smart_plus=smart_plus,
        )

    def create_adgroup(self, payload: dict[str, Any], *, smart_plus: bool = False) -> dict[str, Any]:
        method = self.adgroup_api.smart_plus_adgroup_create if smart_plus else self.adgroup_api.adgroup_create
        return self._invoke(method, self.access_token, body=payload)

    def update_adgroup(self, payload: dict[str, Any], *, smart_plus: bool = False) -> dict[str, Any]:
        method = self.adgroup_api.smart_plus_adgroup_update if smart_plus else self.adgroup_api.adgroup_update
        return self._invoke(method, self.access_token, body=payload)

    def update_adgroup_status(self, payload: dict[str, Any], *, smart_plus: bool = False) -> dict[str, Any]:
        method = (
            self.adgroup_api.smart_plus_adgroup_status_update
            if smart_plus
            else self.adgroup_api.adgroup_status_update
        )
        return self._invoke(method, self.access_token, body=payload)

    def list_ads(
        self,
        advertiser_id: str,
        *,
        filtering: dict[str, Any] | None = None,
        page: int | None = None,
        page_size: int | None = None,
        fields: list[str] | None = None,
        exclude_field_types: list[str] | None = None,
        smart_plus: bool = False,
    ) -> dict[str, Any]:
        return self._list_entities_raw(
            "smart_plus/ad/get/" if smart_plus else "ad/get/",
            advertiser_id,
            filtering=filtering,
            page=page,
            page_size=page_size,
            fields=fields,
            exclude_field_types_in_response=exclude_field_types,
        )

    def get_ad(
        self,
        advertiser_id: str,
        ad_id: str,
        *,
        fields: list[str] | None = None,
        smart_plus: bool = False,
    ) -> dict[str, Any]:
        filter_key = "smart_plus_ad_ids" if smart_plus else "ad_ids"
        return self._get_single_entity(
            self.list_ads,
            advertiser_id,
            filtering={filter_key: [str(ad_id)]},
            entity_label="ad",
            entity_id=ad_id,
            fields=fields,
            smart_plus=smart_plus,
        )

    def create_ad(self, payload: dict[str, Any], *, smart_plus: bool = False) -> dict[str, Any]:
        method = self.ad_api.smart_plus_ad_create if smart_plus else self.ad_api.ad_create
        return self._invoke(method, self.access_token, body=payload)

    def update_ad(self, payload: dict[str, Any], *, smart_plus: bool = False) -> dict[str, Any]:
        method = self.ad_api.smart_plus_ad_update if smart_plus else self.ad_api.ad_update
        return self._invoke(method, self.access_token, body=payload)

    def update_ad_status(self, payload: dict[str, Any], *, smart_plus: bool = False) -> dict[str, Any]:
        method = self.ad_api.smart_plus_ad_status_update if smart_plus else self.ad_api.ad_status_update
        return self._invoke(method, self.access_token, body=payload)

    def upload_image(
        self,
        advertiser_id: str,
        file_path: str | None = None,
        *,
        file_name: str | None = None,
        upload_type: str | None = None,
        image_signature: str | None = None,
        image_url: str | None = None,
        file_id: str | None = None,
    ) -> dict[str, Any]:
        if not file_path and not image_url:
            raise CliError("Image upload requires either file_path or image_url")

        if upload_type is None:
            upload_type = "UPLOAD_BY_URL" if image_url else "UPLOAD_BY_FILE"

        kwargs: dict[str, Any] = {
            "advertiser_id": str(advertiser_id),
            "file_name": file_name,
            "upload_type": upload_type,
            "image_url": image_url,
            "file_id": file_id,
        }

        if image_url:
            base_name = file_name or "image.jpg"
            stem = Path(base_name).stem or "image"
            suffix = Path(base_name).suffix or ".jpg"
            kwargs["file_name"] = f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"
            return self._raw_request(
                "POST",
                "file/image/ad/upload/",
                json_body=_compact_params(kwargs),
            )

        path = Path(file_path).expanduser()
        if not path.exists():
            raise CliError(f"Image file not found: {path}")

        # Calculate MD5 hash automatically if not provided (required by TikTok API)
        if image_signature is None:
            md5_hash = hashlib.md5()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    md5_hash.update(chunk)
            image_signature = md5_hash.hexdigest()

        kwargs.update(
            {
                "file_name": file_name or path.name,
                "image_file": str(path),
                "image_signature": image_signature,
            }
        )
        return self._invoke(self.file_api.ad_image_upload, self.access_token, **_compact_params(kwargs))

    def upload_video(
        self,
        advertiser_id: str,
        file_path: str,
        *,
        file_name: str | None = None,
        upload_type: str | None = None,
        video_signature: str | None = None,
        video_url: str | None = None,
        file_id: str | None = None,
        video_id: str | None = None,
        auto_bind_enabled: bool | None = None,
        auto_fix_enabled: bool | None = None,
        flaw_detect: bool | None = None,
        is_third_party: bool | None = None,
    ) -> dict[str, Any]:
        path = Path(file_path).expanduser()
        if not path.exists():
            raise CliError(f"Video file not found: {path}")

        # Calculate MD5 hash automatically if not provided (required by TikTok API)
        if video_signature is None:
            md5_hash = hashlib.md5()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    md5_hash.update(chunk)
            video_signature = md5_hash.hexdigest()

        # Build kwargs with only non-None values to avoid API validation errors
        kwargs = {
            "advertiser_id": str(advertiser_id),
            "file_name": file_name or path.name,
            "video_file": str(path),
            "video_signature": video_signature,
        }

        # Only add optional parameters if they are not None
        if upload_type is not None:
            kwargs["upload_type"] = upload_type
        if video_url is not None:
            kwargs["video_url"] = video_url
        if file_id is not None:
            kwargs["file_id"] = file_id
        if video_id is not None:
            kwargs["video_id"] = video_id
        if auto_bind_enabled is not None:
            kwargs["auto_bind_enabled"] = auto_bind_enabled
        if auto_fix_enabled is not None:
            kwargs["auto_fix_enabled"] = auto_fix_enabled
        if flaw_detect is not None:
            kwargs["flaw_detect"] = flaw_detect
        # Explicitly set is_third_party to False if None (SDK doesn't handle None properly)
        kwargs["is_third_party"] = is_third_party if is_third_party is not None else False

        return self._invoke(self.file_api.ad_video_upload, self.access_token, **kwargs)

    def get_image_info(self, advertiser_id: str, image_ids: list[str]) -> dict[str, Any]:
        return self._raw_request(
            "GET",
            "file/image/ad/info/",
            params={"advertiser_id": advertiser_id, "image_ids": image_ids},
            timeout=60,
        )

    def get_video_info(self, advertiser_id: str, video_ids: list[str]) -> dict[str, Any]:
        return self._raw_request(
            "GET",
            "file/video/ad/info/",
            params={"advertiser_id": advertiser_id, "video_ids": video_ids},
            timeout=20,
        )

    def list_tt_videos(
        self,
        advertiser_id: str,
        *,
        keyword: str | None = None,
        item_types: list[str] | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._raw_request(
            "GET",
            "tt_video/list/",
            params={
                "advertiser_id": advertiser_id,
                "keyword": keyword,
                "item_types": item_types or ["VIDEO", "CAROUSEL"],
                "page": page or 1,
                "page_size": page_size or 20,
            },
        )

    def search_videos(
        self,
        advertiser_id: str,
        *,
        filtering: dict[str, Any] | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._list_entities(
            self.file_api.ad_video_search,
            advertiser_id,
            filtering=filtering,
            page=page,
            page_size=page_size,
        )

    def delete_assets(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._invoke(self.creative_api.creative_asset_delete, self.access_token, body=payload)

    def share_assets(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._invoke(self.creative_api.creative_asset_share, self.access_token, body=payload)

    def get_account_info(self, advertiser_ids: list[str], *, fields: list[str] | None = None) -> dict[str, Any]:
        return self._invoke(
            self.account_api.advertiser_info,
            advertiser_ids,
            self.access_token,
            **_compact_params({"fields": fields}),
        )

    def oauth2_advertiser_get(self, app_id: str, secret: str) -> dict[str, Any]:
        return self._invoke(self.auth_api.oauth2_advertiser_get, app_id, secret, self.access_token)

    def update_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._invoke(self.account_api.advertiser_update, self.access_token, body=payload)

    def list_pixels(self, advertiser_id: str) -> dict[str, Any]:
        return self._invoke(self.measurement_api.pixel_list, advertiser_id, self.access_token)

    def list_offline_event_sets(
        self,
        *,
        advertiser_id: str | None = None,
        event_set_ids: list[str] | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        return self._invoke(
            self.measurement_api.offline_get,
            self.access_token,
            **_compact_params(
                {
                    "advertiser_id": advertiser_id,
                    "event_set_ids": event_set_ids,
                    "name": name,
                }
            ),
        )

    def list_apps(
        self,
        advertiser_id: str,
        *,
        app_platform_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._invoke(
            self.app_api.app_list,
            advertiser_id,
            self.access_token,
            **_compact_params({"app_platform_ids": app_platform_ids}),
        )

    def get_app_info(self, advertiser_id: str, app_id: str) -> dict[str, Any]:
        return self._invoke(self.app_api.app_info, advertiser_id, app_id, self.access_token)

    def list_pages(
        self,
        advertiser_id: str,
        *,
        library_id: str | None = None,
        business_type: str | None = None,
        business_types: list[str] | None = None,
        app_id: str | None = None,
        title: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"advertiser_id": str(advertiser_id)}
        if library_id:
            params["library_id"] = str(library_id)
        if business_type:
            params["business_type"] = business_type
        if business_types:
            params["business_types"] = json.dumps(business_types, ensure_ascii=False)
        if app_id:
            params["app_id"] = str(app_id)
        if title:
            params["title"] = title
        if page is not None:
            params["page"] = page
        if page_size is not None:
            params["page_size"] = page_size

        try:
            response = requests.get(
                self.PAGE_GET_URL,
                params=params,
                headers={"Access-Token": self.access_token},
                timeout=(10, 30),
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise CliError(network_error("TikTok API", exc)) from None

        return self._check_response(data)

    def list_aigc_voices(
        self,
        advertiser_id: str,
        *,
        filtering: dict[str, Any] | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._raw_request(
            "GET",
            "creative/aigc/voice/get/",
            params=_compact_params(
                {
                    "advertiser_id": str(advertiser_id),
                    "filtering": filtering,
                    "page": page,
                    "page_size": page_size,
                }
            ),
        )

    def create_image_animation_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._raw_request(
            "POST",
            "creative/aigc/image_animation/task/create/",
            json_body=payload,
        )

    def create_aigc_video_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._raw_request(
            "POST",
            "creative/aigc/video/task/create/",
            json_body=payload,
        )

    def list_aigc_video_tasks(
        self,
        advertiser_id: str,
        *,
        aigc_video_type: str,
        task_ids: list[str] | None = None,
        filtering: dict[str, Any] | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._raw_request(
            "GET",
            "creative/aigc/video/task/list/",
            params=_compact_params(
                {
                    "advertiser_id": str(advertiser_id),
                    "aigc_video_type": aigc_video_type,
                    "task_ids": task_ids,
                    "filtering": filtering,
                    "page": page,
                    "page_size": page_size,
                }
            ),
        )

    def list_aigc_videos(
        self,
        advertiser_id: str,
        *,
        aigc_video_types: list[str],
        task_ids: list[str] | None = None,
        video_ids: list[str] | None = None,
        filtering: dict[str, Any] | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._raw_request(
            "GET",
            "creative/aigc/video/list/",
            params=_compact_params(
                {
                    "advertiser_id": str(advertiser_id),
                    "aigc_video_types": aigc_video_types,
                    "task_ids": task_ids,
                    "video_ids": video_ids,
                    "filtering": filtering,
                    "page": page,
                    "page_size": page_size,
                }
            ),
        )

    def list_digital_avatars(
        self,
        advertiser_id: str,
        *,
        filtering: dict[str, Any] | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "advertiser_id": str(advertiser_id),
            "page": page,
            "page_size": page_size,
        }
        if filtering:
            params.update(filtering)
        return self._raw_request(
            "GET",
            "creative/digital_avatar/get/",
            params=_compact_params(params),
        )

    def create_digital_avatar_video_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._raw_request(
            "POST",
            "creative/digital_avatar/video/task/create/",
            json_body=payload,
        )

    def get_digital_avatar_video_task(self, advertiser_id: str, task_id: str) -> dict[str, Any]:
        return self._raw_request(
            "GET",
            "creative/digital_avatar/video/task/get/",
            params={
                "advertiser_id": str(advertiser_id),
                "task_ids": [str(task_id)],
            },
        )

    def list_digital_avatar_videos(
        self,
        advertiser_id: str,
        *,
        task_ids: list[str] | None = None,
        avatar_video_ids: list[str] | None = None,
        filtering: dict[str, Any] | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._raw_request(
            "GET",
            "creative/digital_avatar/video/list/",
            params=_compact_params(
                {
                    "advertiser_id": str(advertiser_id),
                    "task_ids": task_ids,
                    "avatar_video_ids": avatar_video_ids,
                    "filtering": filtering,
                    "page": page,
                    "page_size": page_size,
                }
            ),
        )

    def update_video_file_name(self, *, avatar_video_id: str, file_name: str) -> dict[str, Any]:
        return self._raw_request(
            "POST",
            "file/video/ad/update/",
            json_body={
                "avatar_video_id": str(avatar_video_id),
                "file_name": str(file_name),
            },
        )

    def list_app_optimization_events(
        self,
        app_id: str,
        advertiser_id: str,
        optimization_goal: str,
        *,
        placement: list[str] | None = None,
        placement_type: str | None = None,
        objective: str | None = None,
        available_only: bool | None = None,
        is_skan: bool | None = None,
        app_promotion_type: str | None = None,
    ) -> dict[str, Any]:
        return self._invoke(
            self.app_api.app_optimization_event,
            app_id,
            advertiser_id,
            optimization_goal,
            self.access_token,
            **_compact_params(
                {
                    "placement": placement,
                    "placement_type": placement_type,
                    "objective": objective,
                    "available_only": available_only,
                    "is_skan": is_skan,
                    "app_promotion_type": app_promotion_type,
                }
            ),
        )

    def list_catalogs(
        self,
        bc_id: str,
        *,
        catalog_id: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._invoke(
            self.catalog_api.catalog_get,
            bc_id,
            self.access_token,
            **_compact_params(
                {
                    "catalog_id": catalog_id,
                    "page": page,
                    "page_size": page_size,
                }
            ),
        )

    def get_catalog_eventsource_bindings(self, catalog_id: str, bc_id: str) -> dict[str, Any]:
        return self._invoke(
            self.catalog_api.catalog_eventsource_bind_get,
            catalog_id,
            bc_id,
            self.access_token,
        )

    def list_stores(self, advertiser_id: str) -> dict[str, Any]:
        return self._invoke(self.store_api.gmv_max_store_list, advertiser_id, self.access_token)

    def list_store_products(
        self,
        advertiser_id: str,
        *,
        bc_id: str,
        store_id: str,
        filtering: dict[str, Any] | None = None,
        sort_field: str | None = None,
        sort_type: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._raw_request(
            "GET",
            "store/product/get/",
            params={
                "advertiser_id": advertiser_id,
                "bc_id": bc_id,
                "store_id": store_id,
                "filtering": filtering,
                "sort_field": sort_field,
                "sort_type": sort_type,
                "page": page,
                "page_size": page_size,
            },
            timeout=30,
        )

    def gmv_max_report(
        self,
        advertiser_id: str,
        *,
        store_ids: list[str],
        dimensions: list[str],
        metrics: list[str],
        start_date: str,
        end_date: str,
        filtering: dict[str, Any] | None = None,
        enable_total_metrics: bool | None = None,
        sort_field: str | None = None,
        sort_type: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._invoke(
            self.reporting_api.gmv_max_report_get,
            advertiser_id,
            store_ids,
            dimensions,
            metrics,
            start_date,
            end_date,
            self.access_token,
            **_compact_params(
                {
                    "filtering": filtering,
                    "enable_total_metrics": enable_total_metrics,
                    "sort_field": sort_field,
                    "sort_type": sort_type,
                    "page": page,
                    "page_size": page_size,
                }
            ),
        )

    def list_gmv_max_custom_anchor_videos(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._raw_request(
            "POST",
            "gmv_max/creation/custom_anchor_video_list/get/",
            json_body=payload,
            timeout=30,
        )

    def list_gmv_max_videos(
        self,
        advertiser_id: str,
        *,
        store_id: str,
        store_authorized_bc_id: str,
        spu_id_list: list[str] | None = None,
        custom_posts_eligible: bool | None = None,
        sort_field: str | None = None,
        sort_type: str | None = None,
        keyword: str | None = None,
        need_auth_code_video: bool | None = None,
        identity_list: list[dict[str, Any]] | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._raw_request(
            "GET",
            "gmv_max/video/get/",
            params={
                "advertiser_id": advertiser_id,
                "store_id": store_id,
                "store_authorized_bc_id": store_authorized_bc_id,
                "spu_id_list": spu_id_list,
                "custom_posts_eligible": custom_posts_eligible,
                "sort_field": sort_field,
                "sort_type": sort_type,
                "keyword": keyword,
                "need_auth_code_video": need_auth_code_video,
                "identity_list": identity_list,
                "page": page,
                "page_size": page_size,
            },
            timeout=30,
        )

    def search_regions(self, advertiser_id: str, *, language: str | None = None) -> dict[str, Any]:
        return self._invoke(
            self.tool_api.search_region,
            advertiser_id,
            self.access_token,
            language=language,
        )

    def targeting_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._invoke(self.tool_api.tool_targeting_search, self.access_token, body=payload)

    def targeting_info(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._invoke(self.tool_api.tool_targeting_info, self.access_token, body=payload)

    def targeting_list(self, advertiser_id: str, location_ids: list[str], scene: str) -> dict[str, Any]:
        return self._invoke(
            self.tool_api.tool_targeting_list,
            advertiser_id,
            location_ids,
            scene,
            self.access_token,
        )

    def validate_url(self, advertiser_id: str, url: str) -> dict[str, Any]:
        return self._invoke(self.tool_api.tool_url_validate, advertiser_id, url, self.access_token)

    def get_vbo_status(
        self,
        advertiser_id: str,
        objective_type: str,
        promotion_type: str,
        placements: list[str],
        *,
        pixel_id: str | None = None,
        app_id: str | None = None,
        optimization_event: str | None = None,
        ios14_quota_type: str | None = None,
        app_promotion_type: str | None = None,
        store_id: str | None = None,
        campaign_app_profile_page_state: str | None = None,
        is_smart_performance_campaign: bool | None = None,
        budget_optimize_on: bool | None = None,
        campaign_type: str | None = None,
    ) -> dict[str, Any]:
        return self._invoke(
            self.tool_api.tool_vbo_status,
            advertiser_id,
            objective_type,
            promotion_type,
            placements,
            self.access_token,
            **_compact_params(
                {
                    "pixel_id": pixel_id,
                    "app_id": app_id,
                    "optimization_event": optimization_event,
                    "ios14_quota_type": ios14_quota_type,
                    "app_promotion_type": app_promotion_type,
                    "store_id": store_id,
                    "campaign_app_profile_page_state": campaign_app_profile_page_state,
                    "is_smart_performance_campaign": is_smart_performance_campaign,
                    "budget_optimize_on": budget_optimize_on,
                    "campaign_type": campaign_type,
                }
            ),
        )

    def integrated_report(
        self,
        report_type: str,
        *,
        page: int | None = None,
        page_size: int | None = None,
        enable_total_metrics: bool | None = None,
        multi_adv_report_in_utc_time: bool | None = None,
        query_mode: str | None = None,
        advertiser_id: str | None = None,
        advertiser_ids: list[str] | None = None,
        bc_id: str | None = None,
        service_type: str | None = None,
        data_level: str | None = None,
        dimensions: list[str] | None = None,
        metrics: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        query_lifetime: bool | None = None,
        order_field: str | None = None,
        order_type: str | None = None,
        filtering: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._raw_request(
            "GET",
            "report/integrated/get/",
            params={
                "report_type": report_type,
                "page": page,
                "page_size": page_size,
                "enable_total_metrics": enable_total_metrics,
                "multi_adv_report_in_utc_time": multi_adv_report_in_utc_time,
                "query_mode": query_mode,
                "advertiser_id": advertiser_id,
                "advertiser_ids": advertiser_ids,
                "bc_id": bc_id,
                "service_type": service_type,
                "data_level": data_level,
                "dimensions": dimensions,
                "metrics": metrics,
                "start_date": start_date,
                "end_date": end_date,
                "query_lifetime": query_lifetime,
                "order_field": order_field,
                "order_type": order_type,
                "filtering": filtering,
            },
            timeout=20,
        )

    def smart_plus_material_report_overview(
        self,
        advertiser_id: str,
        dimensions: list[str],
        *,
        metrics: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        query_lifetime: bool | None = None,
        filtering: dict[str, Any] | None = None,
        sort_field: str | None = None,
        sort_type: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._invoke(
            self.reporting_api.smart_plus_material_report_overview,
            advertiser_id,
            dimensions,
            self.access_token,
            **_compact_params(
                {
                    "metrics": metrics,
                    "start_date": start_date,
                    "end_date": end_date,
                    "query_lifetime": query_lifetime,
                    "filtering": filtering,
                    "sort_field": sort_field,
                    "sort_type": sort_type,
                    "page": page,
                    "page_size": page_size,
                }
            ),
        )

    def smart_plus_material_report_breakdown(
        self,
        advertiser_id: str,
        dimensions: list[str],
        start_date: str,
        end_date: str,
        *,
        metrics: list[str] | None = None,
        filtering: dict[str, Any] | None = None,
        sort_field: str | None = None,
        sort_type: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._invoke(
            self.reporting_api.smart_plus_material_report_breakdown,
            advertiser_id,
            dimensions,
            start_date,
            end_date,
            self.access_token,
            **_compact_params(
                {
                    "metrics": metrics,
                    "filtering": filtering,
                    "sort_field": sort_field,
                    "sort_type": sort_type,
                    "page": page,
                    "page_size": page_size,
                }
            ),
        )

    def list_identities(
        self,
        advertiser_id: str,
        *,
        identity_type: str | None = None,
        identity_authorized_bc_id: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._invoke(
            self.identity_api.identity_get,
            advertiser_id,
            self.access_token,
            **_compact_params(
                {
                    "identity_type": identity_type,
                    "identity_authorized_bc_id": identity_authorized_bc_id,
                    "page": page,
                    "page_size": page_size,
                }
            ),
        )

    def create_identity(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._invoke(self.identity_api.identity_create, self.access_token, body=payload)

    def get_identity_video_info(
        self,
        advertiser_id: str,
        identity_type: str,
        identity_id: str,
        item_id: str | None = None,
        *,
        item_ids: list[str] | None = None,
        identity_authorized_bc_id: str | None = None,
        item_type: str | None = None,
    ) -> dict[str, Any]:
        return self._raw_request(
            "GET",
            "identity/video/info/",
            params={
                "advertiser_id": advertiser_id,
                "identity_type": identity_type,
                "identity_id": identity_id,
                "identity_authorized_bc_id": identity_authorized_bc_id,
                "item_id": item_id,
                "item_ids": item_ids,
                "item_type": item_type,
            },
            timeout=30,
        )

    def list_creative_portfolios(
        self,
        advertiser_id: str,
        *,
        filtering: dict[str, Any] | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._invoke(
            self.creative_api.creative_portfolio_list,
            advertiser_id,
            self.access_token,
            **_compact_params(
                {
                    "filtering": filtering,
                    "page": page,
                    "page_size": page_size,
                }
            ),
        )

    def get_creative_portfolio(self, advertiser_id: str, creative_portfolio_id: str) -> dict[str, Any]:
        return self._invoke(
            self.creative_api.creative_portfolio_get,
            advertiser_id,
            creative_portfolio_id,
            self.access_token,
        )

    def create_creative_portfolio(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._invoke(self.creative_api.creative_portfolio_create, self.access_token, body=payload)

    def create_shareable_link(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._invoke(self.creative_api.creative_shareable_link_create, self.access_token, body=payload)

    def generate_smart_text(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._invoke(self.creative_api.creative_smart_text_generate, self.access_token, body=payload)

    def close(self) -> None:
        if getattr(self, "gateway", None) is not None:
            self.gateway.close()
