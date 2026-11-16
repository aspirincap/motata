from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from motata_cli.common.errors import CliError
from motata_cli.transport.tiktok_http import TikTokHTTP
from motata_cli.tiktok.codec import compact as _compact_params
from motata_cli.tiktok.endpoints import ENDPOINTS, endpoint_for
from motata_cli.tiktok.errors import check_response


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

    def __init__(self, access_token=None, *, transport=None, retry_policy=None):
        if os.getenv("MOTATA_AUTH_MODE", "direct") != "direct":
            raise CliError("TikTok supports Direct HTTP only; gateway mode has no automatic fallback.", exit_code=2)
        self.access_token = access_token
        self.transport = transport or TikTokHTTP(access_token, retry_policy=retry_policy)

    def _request(self, endpoint_id, *, params=None, body=None, files=None, timeout=None):
        return self.transport.request(ENDPOINTS[endpoint_id], params=params, body=body, files=files, timeout=timeout)

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
        return self.transport.request(endpoint_for(method, path), params=params, body=json_body, timeout=timeout)

    def _check_response(self, data: Any) -> dict[str, Any]:
        return check_response(data, token=self.access_token if isinstance(self.access_token, str) else None)

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
        return self._request('campaign_gmv_max_info', params={'advertiser_id': advertiser_id, 'campaign_id': str(campaign_id)})

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
        method = 'smart_plus_campaign_create' if smart_plus else 'campaign_create'
        return self._request(method, body=payload)

    def update_campaign(self, payload: dict[str, Any], *, smart_plus: bool = False) -> dict[str, Any]:
        method = 'smart_plus_campaign_update' if smart_plus else 'campaign_update'
        return self._request(method, body=payload)

    def update_campaign_status(self, payload: dict[str, Any], *, smart_plus: bool = False) -> dict[str, Any]:
        method = (
            'smart_plus_campaign_status_update'
            if smart_plus
            else 'campaign_status_update'
        )
        return self._request(method, body=payload)

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
        method = 'smart_plus_adgroup_create' if smart_plus else 'adgroup_create'
        return self._request(method, body=payload)

    def update_adgroup(self, payload: dict[str, Any], *, smart_plus: bool = False) -> dict[str, Any]:
        if smart_plus:
            from .smartplus_adgroup_update import CONTEXT_FIELDS, validate_update, validate_context
            validate_update(payload)
            if CONTEXT_FIELDS.intersection(payload):
                group = self.get_adgroup(payload['advertiser_id'], payload['adgroup_id'], smart_plus=True)
                campaign_id = group.get('campaign_id')
                if not isinstance(campaign_id, str) or not campaign_id:
                    raise CliError('Cannot validate Smart+ update: current adgroup has no campaign_id.')
                campaign = self.get_campaign(payload['advertiser_id'], campaign_id, smart_plus=True)
                validate_context(payload, group, campaign)
        method = 'smart_plus_adgroup_update' if smart_plus else 'adgroup_update'
        return self._request(method, body=payload)

    def update_adgroup_status(self, payload: dict[str, Any], *, smart_plus: bool = False) -> dict[str, Any]:
        method = (
            'smart_plus_adgroup_status_update'
            if smart_plus
            else 'adgroup_status_update'
        )
        return self._request(method, body=payload)

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
        method = 'smart_plus_ad_create' if smart_plus else 'ad_create'
        return self._request(method, body=payload)

    def update_ad(self, payload: dict[str, Any], *, smart_plus: bool = False) -> dict[str, Any]:
        method = 'smart_plus_ad_update' if smart_plus else 'ad_update'
        return self._request(method, body=payload)

    def update_ad_status(self, payload: dict[str, Any], *, smart_plus: bool = False) -> dict[str, Any]:
        method = 'smart_plus_ad_status_update' if smart_plus else 'ad_status_update'
        return self._request(method, body=payload)

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
        if not any((file_path, image_url, file_id)):
            raise CliError("Image upload requires file_path, image_url or file_id")
        if file_path and image_url:
            raise CliError("Specify either an image file or URL, not both")

        if upload_type is None:
            if file_id and not file_path and not image_url:
                raise CliError("file_id upload requires an explicit upload_type")
            upload_type = "UPLOAD_BY_URL" if image_url else "UPLOAD_BY_FILE"
        if (upload_type == "UPLOAD_BY_URL" and not image_url) or (upload_type == "UPLOAD_BY_FILE" and not file_path):
            raise CliError("Image upload_type does not match the supplied file/URL")

        kwargs: dict[str, Any] = {
            "advertiser_id": str(advertiser_id),
            "file_name": file_name,
            "upload_type": upload_type,
            "image_url": image_url,
            "file_id": file_id,
        }

        if not file_path:
            base_name = file_name or "image.jpg"
            stem = Path(base_name).stem or "image"
            suffix = Path(base_name).suffix or ".jpg"
            kwargs["file_name"] = f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"
            return self._request("ad_image_upload", body=_compact_params(kwargs))

        path = Path(file_path).expanduser()
        if not path.is_file():
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
                "image_signature": image_signature,
            }
        )
        return self._request("ad_image_upload", body=_compact_params(kwargs), files={"image_file": str(path)})

    def upload_video(
        self,
        advertiser_id: str,
        file_path: str | None = None,
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
        if not any((file_path, video_url, file_id, video_id)):
            raise CliError("Video upload requires file_path, video_url, file_id or video_id")
        if file_path and video_url:
            raise CliError("Specify either a video file or URL, not both")
        if (upload_type == "UPLOAD_BY_URL" and not video_url) or (upload_type == "UPLOAD_BY_FILE" and not file_path):
            raise CliError("Video upload_type does not match the supplied file/URL")
        path = Path(file_path).expanduser() if file_path else None
        if path and not path.is_file():
            raise CliError(f"Video file not found: {path}")

        # Calculate MD5 hash automatically if not provided (required by TikTok API)
        if path and video_signature is None:
            md5_hash = hashlib.md5()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    md5_hash.update(chunk)
            video_signature = md5_hash.hexdigest()

        # Build kwargs with only non-None values to avoid API validation errors
        kwargs = {
            "advertiser_id": str(advertiser_id),
            "file_name": file_name or (path.name if path else None),
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
        # Preserve the CLI's historical default, including explicit false.
        kwargs["is_third_party"] = is_third_party if is_third_party is not None else False

        if not path and video_url:
            kwargs.setdefault("upload_type", "UPLOAD_BY_URL")
        return self._request("ad_video_upload", body=_compact_params(kwargs),
                             files={"video_file": str(path)} if path else None)

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
        return self._request('ad_video_search', params={'advertiser_id': advertiser_id, 'filtering': filtering, 'page': page, 'page_size': page_size})

    def delete_assets(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request('creative_asset_delete', body=payload)

    def share_assets(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request('creative_asset_share', body=payload)

    def get_account_info(self, advertiser_ids: list[str], *, fields: list[str] | None = None) -> dict[str, Any]:
        return self._request('advertiser_info', params={'advertiser_ids': advertiser_ids, **_compact_params({'fields': fields})})

    def oauth2_advertiser_get(self, app_id: str, secret: str) -> dict[str, Any]:
        return self._request('oauth2_advertiser_get', params={'app_id': app_id, 'secret': secret})

    def update_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request('advertiser_update', body=payload)

    def list_pixels(self, advertiser_id: str) -> dict[str, Any]:
        return self._request('pixel_list', params={'advertiser_id': advertiser_id})

    def list_offline_event_sets(
        self,
        *,
        advertiser_id: str | None = None,
        event_set_ids: list[str] | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        return self._request('offline_get', params={**_compact_params({'advertiser_id': advertiser_id, 'event_set_ids': event_set_ids, 'name': name})})

    def list_apps(
        self,
        advertiser_id: str,
        *,
        app_platform_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._request('app_list', params={'advertiser_id': advertiser_id, **_compact_params({'app_platform_ids': app_platform_ids})})

    def get_app_info(self, advertiser_id: str, app_id: str) -> dict[str, Any]:
        return self._request('app_info', params={'advertiser_id': advertiser_id, 'app_id': app_id})

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

        return self._raw_request("GET", "page/get/", params=params, timeout=30)

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
        return self._request('app_optimization_event', params={'app_id': app_id, 'advertiser_id': advertiser_id, 'optimization_goal': optimization_goal, **_compact_params({'placement': placement, 'placement_type': placement_type, 'objective': objective, 'available_only': available_only, 'is_skan': is_skan, 'app_promotion_type': app_promotion_type})})

    def list_catalogs(
        self,
        bc_id: str,
        *,
        catalog_id: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._request('catalog_get', params={'bc_id': bc_id, **_compact_params({'catalog_id': catalog_id, 'page': page, 'page_size': page_size})})

    def get_catalog_eventsource_bindings(self, catalog_id: str, bc_id: str) -> dict[str, Any]:
        return self._request('catalog_eventsource_bind_get', params={'catalog_id': catalog_id, 'bc_id': bc_id})

    def list_stores(self, advertiser_id: str) -> dict[str, Any]:
        return self._request('gmv_max_store_list', params={'advertiser_id': advertiser_id})

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
        return self._request('gmv_max_report_get', params={'advertiser_id': advertiser_id, 'store_ids': store_ids, 'dimensions': dimensions, 'metrics': metrics, 'start_date': start_date, 'end_date': end_date, **_compact_params({'filtering': filtering, 'enable_total_metrics': enable_total_metrics, 'sort_field': sort_field, 'sort_type': sort_type, 'page': page, 'page_size': page_size})})

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
        return self._request('search_region', params={'advertiser_id': advertiser_id, 'language': language})

    def targeting_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request('tool_targeting_search', body=payload)

    def targeting_info(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request('tool_targeting_info', body=payload)

    def targeting_list(self, advertiser_id: str, location_ids: list[str], scene: str) -> dict[str, Any]:
        return self._request('tool_targeting_list', params={'advertiser_id': advertiser_id, 'location_ids': location_ids, 'scene': scene})

    def validate_url(self, advertiser_id: str, url: str) -> dict[str, Any]:
        return self._request('tool_url_validate', params={'advertiser_id': advertiser_id, 'url': url})

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
        return self._request('tool_vbo_status', params={'advertiser_id': advertiser_id, 'objective_type': objective_type, 'promotion_type': promotion_type, 'placements': placements, **_compact_params({'pixel_id': pixel_id, 'app_id': app_id, 'optimization_event': optimization_event, 'ios14_quota_type': ios14_quota_type, 'app_promotion_type': app_promotion_type, 'store_id': store_id, 'campaign_app_profile_page_state': campaign_app_profile_page_state, 'is_smart_performance_campaign': is_smart_performance_campaign, 'budget_optimize_on': budget_optimize_on, 'campaign_type': campaign_type})})

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
        return self._request('smart_plus_material_report_overview', params={'advertiser_id': advertiser_id, 'dimensions': dimensions, **_compact_params({'metrics': metrics, 'start_date': start_date, 'end_date': end_date, 'query_lifetime': query_lifetime, 'filtering': filtering, 'sort_field': sort_field, 'sort_type': sort_type, 'page': page, 'page_size': page_size})})

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
        return self._request('smart_plus_material_report_breakdown', params={'advertiser_id': advertiser_id, 'dimensions': dimensions, 'start_date': start_date, 'end_date': end_date, **_compact_params({'metrics': metrics, 'filtering': filtering, 'sort_field': sort_field, 'sort_type': sort_type, 'page': page, 'page_size': page_size})})

    def list_identities(
        self,
        advertiser_id: str,
        *,
        identity_type: str | None = None,
        identity_authorized_bc_id: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._request('identity_get', params={'advertiser_id': advertiser_id, **_compact_params({'identity_type': identity_type, 'identity_authorized_bc_id': identity_authorized_bc_id, 'page': page, 'page_size': page_size})})

    def create_identity(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request('identity_create', body=payload)

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
        return self._request('creative_portfolio_list', params={'advertiser_id': advertiser_id, **_compact_params({'filtering': filtering, 'page': page, 'page_size': page_size})})

    def get_creative_portfolio(self, advertiser_id: str, creative_portfolio_id: str) -> dict[str, Any]:
        return self._request('creative_portfolio_get', params={'advertiser_id': advertiser_id, 'creative_portfolio_id': creative_portfolio_id})

    def create_creative_portfolio(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request('creative_portfolio_create', body=payload)

    def create_shareable_link(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request('creative_shareable_link_create', body=payload)

    def generate_smart_text(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request('creative_smart_text_generate', body=payload)

    def list_native_series(self, advertiser_id: str, *, identity_id: str, identity_type: str,
                           identity_authorized_bc_id: str | None = None, page: int = 1,
                           page_size: int = 20) -> dict[str, Any]:
        return self._request("identity_native_series_get", params={
            "advertiser_id": advertiser_id, "identity_id": identity_id, "identity_type": identity_type,
            "identity_authorized_bc_id": identity_authorized_bc_id, "page": page, "page_size": page_size,
        })

    def close(self) -> None:
        self.transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
