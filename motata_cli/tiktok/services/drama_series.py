"""Upgraded Smart+ Drama Series plans and resumable, disabled creation."""
from __future__ import annotations

import copy
import math
import uuid
from urllib.parse import urlsplit

from motata_cli.common.errors import CliError
from motata_cli.tiktok.operations import OperationStore, digest
from motata_cli.tiktok import drama_rules

CONSTANTS = {
    "campaign": {"objective_type": "APP_PROMOTION", "app_promotion_type": "MINIS", "catalog_enabled": False,
                 "is_search_campaign": False, "campaign_type": "REGULAR_CAMPAIGN"},
    "adgroup": {"promotion_type": "NATIVE_SERIES", "optimization_goal": "VALUE", "optimization_event": "AD_REVENUE_VALUE",
                "bid_type": "BID_TYPE_NO_BID", "billing_event": "OCPM", "vbo_window": "ZERO_DAY",
                "placement_type": "PLACEMENT_TYPE_NORMAL", "placements": ["PLACEMENT_TIKTOK"],
                "targeting_optimization_mode": "AUTOMATIC"},
}


def build_plan(payload, advertiser_id=None):
    if not isinstance(payload, dict):
        raise CliError("Drama Series payload must be an object.")
    plan = copy.deepcopy(payload)
    account = str(advertiser_id or plan.get("advertiser_id") or "").strip()
    if not account:
        raise CliError("Drama Series requires advertiser_id.")
    plan.update(schema_version=1, advertiser_id=account, plan_id=plan.get("plan_id") or uuid.uuid4().hex)
    for level in ("campaign", "adgroup", "ad"):
        body = plan.get(level)
        if not isinstance(body, dict) or not body:
            raise CliError(f"Drama Series requires a nonempty {level} payload.")
        if body.get("advertiser_id") and str(body["advertiser_id"]) != account:
            raise CliError(f"{level}.advertiser_id conflicts with plan account.")
        body["advertiser_id"] = account
        body["operation_status"] = "DISABLE"
        for key, value in CONSTANTS.get(level, {}).items():
            if key in body and body[key] != value:
                raise CliError(f"Drama Series requires {level}.{key}={value}.")
            body[key] = copy.deepcopy(value)
    plan['ad'].setdefault('ad_name', '')
    creatives = plan['ad'].get('creative_list')
    if not isinstance(creatives, list):
        raise CliError('Drama Series requires a nonempty ad.creative_list.')
    for creative in creatives:
        if isinstance(creative, dict) and isinstance(creative.get("creative_info"), dict):
            info = creative['creative_info']
            # A creative may use its own authorized identity. Inherit the group
            # identity only when the creative has not selected a different one.
            if all(key not in info or info[key] == plan['adgroup'].get(key) for key in drama_rules.IDENTITY_FIELDS):
                for key in drama_rules.IDENTITY_FIELDS:
                    if plan['adgroup'].get(key):
                        info.setdefault(key, plan['adgroup'][key])
    validate_local(plan)
    return plan


def validate_local(plan):
    if not isinstance(plan, dict) or plan.get("schema_version") != 1 or not plan.get("plan_id"):
        raise CliError("Use drama-series plan to produce a versioned plan first.")
    account = str(plan.get("advertiser_id") or "")
    if not account.strip():
        raise CliError("Drama Series requires advertiser_id.")
    for level in ("campaign", "adgroup", "ad"):
        body = plan.get(level)
        if not isinstance(body, dict) or str(body.get("advertiser_id")) != account:
            raise CliError(f"Invalid {level} account in plan.")
        if body.get("operation_status") != "DISABLE":
            raise CliError("Drama Series creation plans must keep every object DISABLE.")
        for key, value in CONSTANTS.get(level, {}).items():
            if body.get(key) != value:
                raise CliError(f"Invalid {level}.{key} for Drama Series.")
    campaign, group, ad = (plan[level] for level in ("campaign", "adgroup", "ad"))
    if any(key in campaign for key in ("app_id", "campaign_app_profile_page_state")):
        raise CliError("Drama campaigns must omit app_id and campaign_app_profile_page_state.")
    for body, name in ((campaign, "campaign_name"), (group, "adgroup_name")):
        if not isinstance(body.get(name), str) or not body[name].strip():
            raise CliError(f"Missing {name}.")
    if not isinstance(ad.get('ad_name'), str):
        raise CliError('ad_name must be a string; use an empty string for a generated name.')
    if group.get("campaign_id") or ad.get("campaign_id") or ad.get("adgroup_id"):
        raise CliError("The workflow binds new parent IDs; omit campaign_id/adgroup_id from input payloads.")
    for body in (campaign, group):
        if "budget" in body and (isinstance(body["budget"], bool) or not isinstance(body["budget"], (int, float)) or not math.isfinite(body["budget"]) or body["budget"] <= 0):
            raise CliError("Budget, when supplied, must be a positive number.")
    native = group.get("native_series_id")
    selling = group.get("drama_selling_setting")
    if ('native_series_id' in group) == ('drama_selling_setting' in group):
        raise CliError("Specify exactly one of native_series_id or drama_selling_setting.")
    mode = "owned" if 'native_series_id' in group else "distribution"
    if mode == 'owned' and (not isinstance(native, str) or not native.strip()):
        raise CliError('native_series_id must be a nonempty string.')
    if plan.get("mode", mode) != mode:
        raise CliError("Drama Series mode conflicts with the adgroup payload.")
    if mode == 'distribution':
        link = urlsplit(str(selling.get("distribution_link", ""))) if isinstance(selling, dict) else None
        if not link or link.scheme not in ('http', 'https') or not link.hostname or link.username or link.password:
            raise CliError("Distribution mode requires drama_selling_setting.distribution_link as an absolute HTTP(S) URL.")
    drama_rules.validate_identity(group, 'adgroup')
    if not isinstance(ad.get("creative_list"), list) or not ad["creative_list"]:
        raise CliError("Drama Series requires a nonempty ad.creative_list.")
    for creative in ad.get("creative_list", []):
        if not isinstance(creative, dict):
            raise CliError("creative_list entries must be objects.")
        info = creative.get("creative_info", {})
        if not isinstance(info, dict):
            raise CliError("creative_info must be an object.")
        if any(key in creative for key in drama_rules.IDENTITY_FIELDS):
            raise CliError('Place creative identity fields inside creative_info.')
    drama_rules.validate_budget_and_bidding(campaign, group)
    drama_rules.validate_shared_settings(campaign, [group])
    drama_rules.validate_assets(group, ad)
    return {"valid": True, "mode": mode, "account_eligibility": "unknown", "writes_performed": False}


def _rows(response):
    if not isinstance(response, dict):
        raise CliError('Discovery response must be an object.')
    data = response.get("data")
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = next((data[key] for key in ('list', 'identity_list', 'native_series_list', 'identities')
                     if isinstance(data.get(key), list)), None)
    else:
        rows = None
    if rows is None or any(not isinstance(row, dict) for row in rows):
        raise CliError('Discovery response is missing a valid collection; validation is incomplete.')
    return rows


def _page_info(response):
    data = response.get('data')
    info = data.get('page_info') if isinstance(data, dict) else None
    if info is not None and not isinstance(info, dict):
        raise CliError('Invalid discovery pagination metadata; validation is incomplete.')
    return info or {}


def _validate_identities(account, client, identities):
    requests = {}
    for identity in identities:
        key = (identity['identity_type'], identity.get('identity_authorized_bc_id'))
        requests.setdefault(key, set()).add(identity['identity_id'])
    for (kind, bc), required in requests.items():
        options = {'identity_type': kind}
        if bc:
            options['identity_authorized_bc_id'] = bc
        remaining, seen = set(required), set()
        for page in range(1, 51):
            result = client.list_identities(account, page=page, page_size=100, **options)
            rows = _rows(result)
            remaining.difference_update(str(row.get('identity_id')) for row in rows
                if row.get('identity_type', kind) == kind and row.get('identity_authorized_bc_id', bc) == bc)
            if not remaining or not rows:
                break
            fingerprint = digest(rows)
            if fingerprint in seen:
                raise CliError('Identity pagination repeated a page; access validation is incomplete.')
            seen.add(fingerprint)
            info = _page_info(result)
            if info.get('total_page') and page >= int(info['total_page']):
                break
        if remaining:
            raise CliError('A group/creative TikTok identity was not found in its authorized identity/BC list.')


def validate_remote(plan, client, *, excluded_campaign_ids=()):
    checks = validate_local(plan)
    account, group = plan["advertiser_id"], plan["adgroup"]
    if not any(str(row.get("advertiser_id")) == account for row in _rows(client.get_account_info([account]))):
        raise CliError("The TikTok token cannot access the plan advertiser.")
    identity_options = {key: group[key] for key in ("identity_type", "identity_authorized_bc_id") if group.get(key)}
    _validate_identities(account, client, [group, *(creative['creative_info'] for creative in plan['ad']['creative_list'])])
    if group.get("native_series_id"):
        found = False
        seen = set()
        for page in range(1, 51):
            result = client.list_native_series(account, identity_id=group["identity_id"], page=page,
                                              page_size=100, **identity_options)
            rows = _rows(result)
            found = any(str(row.get("native_series_id")) == str(group["native_series_id"]) for row in rows)
            if found or not rows:
                break
            fingerprint = digest(rows)
            if fingerprint in seen:
                raise CliError("Native series pagination repeated a page; access validation is incomplete.")
            seen.add(fingerprint)
            info = _page_info(result)
            if info.get("total_page") and page >= int(info["total_page"]):
                break
        if not found:
            raise CliError("The native series was not found for the selected identity.")
    checks.update(account_access="verified", identity_access="verified",
                  creative_identity_access='verified',
                  series_access="verified" if group.get("native_series_id") else "distribution_link_requires_platform_validation")
    checks["target_roas_conflicts"] = _validate_roas_conflicts(plan, client, excluded_campaign_ids)
    return checks


def _validate_roas_conflicts(plan, client, excluded_campaign_ids):
    group = plan["adgroup"]
    if group.get("deep_bid_type") != "VO_MIN_ROAS" or not group.get("native_series_id"):
        return "not_applicable" if not group.get("drama_selling_setting") else "distribution_requires_platform_validation"
    target = group["targeting_spec"]
    seen, campaigns = set(), {}
    for page in range(1, 51):
        result = client.list_adgroups(plan["advertiser_id"], page=page, page_size=100, smart_plus=True)
        rows = _rows(result)
        if not rows:
            return "no_exact_id_conflict_platform_checks_geographic_hierarchy"
        fingerprint = digest(rows)
        if fingerprint in seen:
            raise CliError("Adgroup pagination repeated; Target ROAS conflict validation is incomplete.")
        seen.add(fingerprint)
        for row in rows:
            if str(row.get("native_series_id")) != str(group["native_series_id"]):
                continue
            campaign_id = str(row.get("campaign_id") or "")
            if campaign_id in excluded_campaign_ids:
                continue
            if not campaign_id:
                raise CliError("Target ROAS conflict check is missing campaign_id.")
            if campaign_id not in campaigns:
                campaigns[campaign_id] = client.get_campaign(plan["advertiser_id"], campaign_id, smart_plus=True)
            status = campaigns[campaign_id].get("operation_status")
            if status not in ("ENABLE", "DISABLE", "DELETE"):
                raise CliError("Target ROAS conflict check cannot establish existing campaign status.")
            if status != "ENABLE":
                continue
            other = row.get("targeting_spec") or {}
            if not isinstance(other, dict) or not any(other.get(key) for key in ("location_ids", "zipcode_ids")):
                raise CliError("An active campaign promotes this series but targeting is missing; inspect it before creating another.")
            if any(set(map(str, target.get(key) or [])) & set(map(str, other.get(key) or [])) for key in ("location_ids", "zipcode_ids")):
                raise CliError(f"Target ROAS conflict: active campaign {campaign_id} promotes the same series in a selected location.")
        info = _page_info(result)
        if info.get("total_page") and page >= int(info["total_page"]):
            return "no_exact_id_conflict_platform_checks_geographic_hierarchy"
    raise CliError("Target ROAS conflict scan exceeded 50 pages; validation is incomplete.")


def _verify(plan, client, level, object_id, *, parent=None):
    read = {"campaign": client.get_campaign, "adgroup": client.get_adgroup, "ad": client.get_ad}[level]
    result = read(plan["advertiser_id"], object_id, smart_plus=True)
    returned_id = (result.get('smart_plus_ad_id') or result.get('ad_id')) if level == 'ad' else result.get(level + '_id')
    if str(returned_id) != str(object_id) or str(result.get('advertiser_id')) != plan['advertiser_id']:
        raise CliError(f'Created {level} ID or advertiser does not match the job.')
    if parent and str(result.get(parent[0])) != str(parent[1]):
        raise CliError(f'Created {level} parent does not match the saved job.')
    if result.get("operation_status") != "DISABLE":
        raise CliError(f"Created {level} did not verify as DISABLE; inspect the object before continuing.")
    for key in (f"{level}_name", *CONSTANTS.get(level, {}), "budget", "budget_mode", "budget_optimize_on",
                "native_series_id", "drama_selling_setting", *drama_rules.IDENTITY_FIELDS, "deep_bid_type", "roas_bid"):
        if key == 'ad_name' and plan['ad'].get('ad_name') == '':
            if not isinstance(result.get('ad_name'), str) or not result['ad_name'].strip():
                raise CliError('The platform did not return an autogenerated ad name.')
            continue
        if key == "optimization_event" and result.get(key) == "IMPRESSION_LEVEL_AD_REVENUE":
            # The reviewed guide's response uses this canonical name for AD_REVENUE_VALUE.
            continue
        if key in result and key in plan[level] and result[key] != plan[level][key]:
            raise CliError(f"Created {level} field {key} differs from the plan.")
    return result


def execute(plan, client, *, job_id=None, store=None, retry_failed=False):
    validate_local(plan)
    store = store or OperationStore()
    job_id = job_id or plan["plan_id"]
    with store.lock(job_id):
        return _execute(plan, client, job_id=job_id, store=store, retry_failed=retry_failed)


def _execute(plan, client, *, job_id, store, retry_failed):
    validate_local(plan)
    store = store or OperationStore()
    job_id = job_id or plan["plan_id"]
    store.bind(job_id, plan)
    try:
        saved_campaigns = [row["object_id"] for row in store.rows(job_id) if row["step"] == "campaign" and row["state"] == "succeeded"]
        validate_remote(plan, client, excluded_campaign_ids=saved_campaigns)
        ids, names = {}, {}
        for level, keys in (("campaign", ("campaign_id", "smart_plus_campaign_id")),
                            ("adgroup", ("adgroup_id", "smart_plus_adgroup_id")),
                            ("ad", ("smart_plus_ad_id", "ad_id", "ad_ids"))):
            body = copy.deepcopy(plan[level])
            if level == "adgroup":
                body["campaign_id"] = ids["campaign"]
                body.setdefault("request_id", str(int(digest([job_id, level])[:16], 16) % (2**63 - 1) + 1))
            elif level == "ad":
                body["adgroup_id"] = ids["adgroup"]
            submit = lambda payload, level=level: getattr(client, "create_" + level)(payload, smart_plus=True)
            ids[level] = store.step(job_id, level, body, submit, keys, retry_failed=retry_failed)
            parent = ('campaign_id', ids['campaign']) if level == 'adgroup' else ('adgroup_id', ids['adgroup']) if level == 'ad' else None
            verified = _verify(plan, client, level, ids[level], parent=parent)
            names[level] = verified.get(level + '_name')
        return {"status": "success", "job_id": job_id, "objects": ids, "operation_status": "DISABLE",
                "steps": store.rows(job_id), "resolved_names": names, "account_eligibility": "creation_verified"}
    except Exception as exc:
        return {"status": "partial_success" if store.rows(job_id) else "failed", "job_id": job_id,
                "steps": store.rows(job_id), "error": str(exc), "exit_code": 3 if store.rows(job_id) else 1,
                "recovery": "Resolve failed/unknown steps before resume; successful objects are reused."}


def reconcile(plan, client, *, job_id, step, object_id, store=None):
    store = store or OperationStore()
    with store.lock(job_id):
        return _reconcile(plan, client, job_id=job_id, step=step, object_id=object_id, store=store)


def _reconcile(plan, client, *, job_id, step, object_id, store):
    """An explicitly supplied remote ID must pass a read before local recovery."""
    store = store or OperationStore()
    store.bind(job_id, plan)
    rows = {row["step"]: row for row in store.rows(job_id)}
    if step not in rows or rows[step]["state"] == "succeeded":
        raise CliError("Only an existing unresolved step can be reconciled.")
    parent = {"adgroup": ("campaign", "campaign_id"), "ad": ("adgroup", "adgroup_id")}.get(step)
    expected_parent = (parent[1], (rows.get(parent[0]) or {}).get('object_id')) if parent else None
    if expected_parent and not expected_parent[1]:
        raise CliError('Reconcile the parent before its child.')
    result = _verify(plan, client, step, object_id, parent=expected_parent)
    key = f"{step}_name"
    if plan[step].get(key) and result.get(key) != plan[step][key]:
        raise CliError("The supplied object's name does not match the plan.")
    if step == 'ad' and not plan['ad']['ad_name']:
        def proof(body):
            return [dict((key, creative.get('creative_info', {}).get(key)) for key in
                         ('ad_format', *drama_rules.IDENTITY_FIELDS, 'tiktok_item_id', 'video_info', 'image_info'))
                    for creative in body.get('creative_list', [])]
        # Autogenerated names are not matching evidence. Require supplied asset
        # fields to match, allowing only additional response metadata.
        def contains(actual, expected):
            if isinstance(expected, dict):
                return isinstance(actual, dict) and all(contains(actual.get(k), v) for k, v in expected.items() if v is not None)
            if isinstance(expected, list):
                return isinstance(actual, list) and len(actual) == len(expected) and all(contains(a, e) for a, e in zip(actual, expected))
            return actual == expected
        if not contains(proof(result), proof(plan['ad'])) or (result.get('ad_configuration') or {}).get('call_to_action_id') != plan['ad']['ad_configuration']['call_to_action_id']:
            raise CliError('Autonamed ad assets/identity/CTA do not match the saved plan.')
    store.update(job_id, step, "succeeded", str(object_id))
    return {"status": "reconciled", "job_id": job_id, "steps": store.rows(job_id), "writes_performed": False}
