"""Reviewed TikTok v1.3 endpoints. No generated SDK or remote runtime schema."""
from dataclasses import dataclass
from motata_cli.common.errors import CliError

API_VERSION = "v1.3"
SCHEMA_REVISION = "2026-11-12"

@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    write: bool

ENDPOINTS = {
    'ad_create': Endpoint('POST', 'ad/create/', True),
    'ad_get': Endpoint('GET', 'ad/get/', False),
    'ad_image_upload': Endpoint('POST', 'file/image/ad/upload/', True),
    'ad_status_update': Endpoint('POST', 'ad/status/update/', True),
    'ad_update': Endpoint('POST', 'ad/update/', True),
    'ad_video_search': Endpoint('GET', 'file/video/ad/search/', False),
    'ad_video_upload': Endpoint('POST', 'file/video/ad/upload/', True),
    'adgroup_create': Endpoint('POST', 'adgroup/create/', True),
    'adgroup_get': Endpoint('GET', 'adgroup/get/', False),
    'adgroup_status_update': Endpoint('POST', 'adgroup/status/update/', True),
    'adgroup_update': Endpoint('POST', 'adgroup/update/', True),
    'advertiser_info': Endpoint('GET', 'advertiser/info/', False),
    'advertiser_update': Endpoint('POST', 'advertiser/update/', True),
    'app_info': Endpoint('GET', 'app/info/', False),
    'app_list': Endpoint('GET', 'app/list/', False),
    'app_optimization_event': Endpoint('GET', 'app/optimization_event/', False),
    'bc_asset_get': Endpoint('GET', 'bc/asset/get/', False),
    'bc_get': Endpoint('GET', 'bc/get/', False),
    'campaign_create': Endpoint('POST', 'campaign/create/', True),
    'campaign_get': Endpoint('GET', 'campaign/get/', False),
    'campaign_gmv_max_info': Endpoint('GET', 'campaign/gmv_max/info/', False),
    'campaign_status_update': Endpoint('POST', 'campaign/status/update/', True),
    'campaign_update': Endpoint('POST', 'campaign/update/', True),
    'catalog_eventsource_bind_get': Endpoint('GET', 'catalog/eventsource_bind/get/', False),
    'catalog_get': Endpoint('GET', 'catalog/get/', False),
    'changelog_task_check': Endpoint('GET', 'changelog/task/check/', False),
    'changelog_task_create': Endpoint('POST', 'changelog/task/create/', True),
    'changelog_task_download': Endpoint('GET', 'changelog/task/download/', False),
    'creative_aigc_image_animation_task_create': Endpoint('POST', 'creative/aigc/image_animation/task/create/', True),
    'creative_aigc_video_list': Endpoint('GET', 'creative/aigc/video/list/', False),
    'creative_aigc_video_task_create': Endpoint('POST', 'creative/aigc/video/task/create/', True),
    'creative_aigc_video_task_list': Endpoint('GET', 'creative/aigc/video/task/list/', False),
    'creative_aigc_voice_get': Endpoint('GET', 'creative/aigc/voice/get/', False),
    'creative_asset_delete': Endpoint('POST', 'creative/asset/delete/', True),
    'creative_asset_share': Endpoint('POST', 'creative/asset/share/', True),
    'creative_digital_avatar_get': Endpoint('GET', 'creative/digital_avatar/get/', False),
    'creative_digital_avatar_video_list': Endpoint('GET', 'creative/digital_avatar/video/list/', False),
    'creative_digital_avatar_video_task_create': Endpoint('POST', 'creative/digital_avatar/video/task/create/', True),
    'creative_digital_avatar_video_task_get': Endpoint('GET', 'creative/digital_avatar/video/task/get/', False),
    'creative_portfolio_create': Endpoint('POST', 'creative/portfolio/create/', True),
    'creative_portfolio_get': Endpoint('GET', 'creative/portfolio/get/', False),
    'creative_portfolio_list': Endpoint('GET', 'creative/portfolio/list/', False),
    'creative_shareable_link_create': Endpoint('POST', 'creative/shareable_link/create/', True),
    'creative_smart_text_generate': Endpoint('POST', 'creative/smart_text/generate/', True),
    'file_image_ad_info': Endpoint('GET', 'file/image/ad/info/', False),
    'file_video_ad_info': Endpoint('GET', 'file/video/ad/info/', False),
    'file_video_ad_update': Endpoint('POST', 'file/video/ad/update/', True),
    'gmv_max_campaign_get': Endpoint('GET', 'gmv_max/campaign/get/', False),
    'gmv_max_creation_custom_anchor_video_list_get': Endpoint('POST', 'gmv_max/creation/custom_anchor_video_list/get/', False),
    'gmv_max_report_get': Endpoint('GET', 'gmv_max/report/get/', False),
    'gmv_max_store_list': Endpoint('GET', 'gmv_max/store/list/', False),
    'gmv_max_video_get': Endpoint('GET', 'gmv_max/video/get/', False),
    'identity_create': Endpoint('POST', 'identity/create/', True),
    'identity_get': Endpoint('GET', 'identity/get/', False),
    'identity_native_series_get': Endpoint('GET', 'identity/native_series/get/', False),
    'identity_video_info': Endpoint('GET', 'identity/video/info/', False),
    'oauth2_advertiser_get': Endpoint('GET', 'oauth2/advertiser/get/', False),
    'offline_get': Endpoint('GET', 'offline/get/', False),
    'page_get': Endpoint('GET', 'page/get/', False),
    'pixel_list': Endpoint('GET', 'pixel/list/', False),
    'report_integrated_get': Endpoint('GET', 'report/integrated/get/', False),
    'search_region': Endpoint('GET', 'search/region/', False),
    'smart_plus_ad_create': Endpoint('POST', 'smart_plus/ad/create/', True),
    'smart_plus_ad_get': Endpoint('GET', 'smart_plus/ad/get/', False),
    'smart_plus_ad_status_update': Endpoint('POST', 'smart_plus/ad/status/update/', True),
    'smart_plus_ad_update': Endpoint('POST', 'smart_plus/ad/update/', True),
    'smart_plus_adgroup_create': Endpoint('POST', 'smart_plus/adgroup/create/', True),
    'smart_plus_adgroup_get': Endpoint('GET', 'smart_plus/adgroup/get/', False),
    'smart_plus_adgroup_status_update': Endpoint('POST', 'smart_plus/adgroup/status/update/', True),
    'smart_plus_adgroup_update': Endpoint('POST', 'smart_plus/adgroup/update/', True),
    'smart_plus_campaign_create': Endpoint('POST', 'smart_plus/campaign/create/', True),
    'smart_plus_campaign_get': Endpoint('GET', 'smart_plus/campaign/get/', False),
    'smart_plus_campaign_status_update': Endpoint('POST', 'smart_plus/campaign/status/update/', True),
    'smart_plus_campaign_update': Endpoint('POST', 'smart_plus/campaign/update/', True),
    'smart_plus_material_report_breakdown': Endpoint('GET', 'smart_plus/material_report/breakdown/', False),
    'smart_plus_material_report_overview': Endpoint('GET', 'smart_plus/material_report/overview/', False),
    'store_product_get': Endpoint('GET', 'store/product/get/', False),
    'tool_targeting_info': Endpoint('POST', 'tool/targeting/info/', False),
    'tool_targeting_list': Endpoint('GET', 'tool/targeting/list/', False),
    'tool_targeting_search': Endpoint('POST', 'tool/targeting/search/', False),
    'tool_url_validate': Endpoint('GET', 'tool/url_validate/', False),
    'tool_vbo_status': Endpoint('GET', 'tool/vbo_status/', False),
    'tt_video_list': Endpoint('GET', 'tt_video/list/', False),
}
BY_PATH = {(value.method, value.path): value for value in ENDPOINTS.values()}


def endpoint_for(method: str, path: str) -> Endpoint:
    try:
        return BY_PATH[(method.upper(), path)]
    except KeyError:
        raise CliError("Unsupported TikTok endpoint or HTTP method.") from None
