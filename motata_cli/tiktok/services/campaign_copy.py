"""Campaign copy/bootstrap orchestration. No imports from the commands facade.

Injected collaborators are resolved per invocation to preserve legacy patch points."""
from __future__ import annotations
import argparse
import sys
from typing import Any
from ..client import TikTokClient

def load_tiktok_campaign_copy_source(client: TikTokClient, *, advertiser_id: str, campaign_id: str, smart_plus: bool, skip_adgroups: bool, skip_ads: bool, verbose: bool, deps) -> tuple[dict[str, Any], bool, list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    campaign_result = client.list_campaigns(advertiser_id, filtering={'campaign_ids': [campaign_id]}, page_size=1, smart_plus=smart_plus)
    campaign_list = campaign_result.get('data', {}).get('list', [])
    if not campaign_list:
        raise deps.CliError(f'Source campaign {campaign_id} not found')
    source_campaign_data = campaign_list[0]
    effective_smart_plus = smart_plus or deps.is_smart_plus_campaign_type(source_campaign_data)
    if effective_smart_plus:
        source_campaign_data = deps.merge_source_snapshot(source_campaign_data, client.get_campaign(advertiser_id, campaign_id, smart_plus=True))
    if effective_smart_plus and (not smart_plus) and verbose:
        print('Detected SmartPlus source campaign from campaign_automation_type; evaluating SmartPlus vs normal copy target.', file=sys.stderr)
    source_adgroups: list[dict[str, Any]] = []
    source_ads_by_adgroup: dict[str, list[dict[str, Any]]] = {}
    if not skip_adgroups:
        source_adgroups, source_ads_by_adgroup = deps.load_source_adgroups_and_ads_for_copy(client, advertiser_id=advertiser_id, campaign_id=campaign_id, smart_plus=effective_smart_plus, include_ads=not skip_ads)
    copy_strategy = deps.infer_campaign_copy_strategy(source_campaign_data, source_adgroups, [ad for ads in source_ads_by_adgroup.values() for ad in ads], smart_plus=effective_smart_plus)
    if copy_strategy.get('target_smart_plus') and source_campaign_data.get('objective_type') == 'APP_PROMOTION':
        deps.validate_smartplus_app_eligibility(client, advertiser_id=advertiser_id, payload=source_campaign_data, context_label=f'smartplus-campaigns copy source campaign {campaign_id}')
    if verbose:
        print(f'Copy strategy: {copy_strategy}', file=sys.stderr)
    return (source_campaign_data, effective_smart_plus, source_adgroups, source_ads_by_adgroup, copy_strategy)

def execute_tiktok_campaign_copy(client: TikTokClient, *, advertiser_id: str, campaign_id: str, name: str, copies: int, operation_status: str, adgroup_status: str, ad_status: str, landing_page_url: str | None, page_id: str | None, skip_adgroups: bool, skip_ads: bool, smart_plus: bool, copy_route: str='auto', verbose: bool, deps) -> dict[str, Any]:
    """Copy a campaign, retaining every successful write and failed child operation."""
    if copies < 1:
        raise deps.CliError('copies must be at least 1')
    source_campaign_data, effective_smart_plus, source_adgroups, source_ads_by_adgroup, copy_strategy = deps.load_tiktok_campaign_copy_source(client, advertiser_id=advertiser_id, campaign_id=campaign_id, smart_plus=smart_plus, skip_adgroups=skip_adgroups, skip_ads=skip_ads, verbose=verbose)
    if copy_route == 'smartplus':
        copy_strategy['target_smart_plus'] = True
    elif copy_route == 'normal':
        copy_strategy['target_smart_plus'] = False
    target_smart_plus = bool(copy_strategy.get('target_smart_plus', effective_smart_plus))
    created_campaigns = []
    failed_campaigns = []
    for i in range(copies):
        campaign_name = f'{name}_{i + 1}' if copies > 1 else name
        try:
            campaign_payload = deps.build_campaign_copy_payload(source_campaign_data, advertiser_id=advertiser_id, campaign_name=campaign_name, operation_status=operation_status, page_id=page_id, copy_strategy=copy_strategy)
            campaign_name = campaign_payload['campaign_name']
            if verbose:
                print(f'Creating campaign {i + 1}/{copies}: {campaign_name}...', file=sys.stderr)
            campaign_result = client.create_campaign(campaign_payload, smart_plus=target_smart_plus)
            campaign_data = campaign_result.get('data', {})
            new_campaign_id = campaign_data.get('campaign_id') or campaign_data.get('smart_plus_campaign_id')
            if not new_campaign_id:
                error_msg = campaign_result.get('message', 'Unknown error')
                if verbose:
                    print(f'  Failed: {error_msg}', file=sys.stderr)
                raise deps.CliError(f'Failed to create campaign: {error_msg}')
            if verbose:
                print(f'  Created campaign ID: {new_campaign_id}', file=sys.stderr)
            campaign_info = {'campaign_id': new_campaign_id, 'campaign_name': campaign_name, 'copy_strategy': copy_strategy, 'adgroups_created': 0, 'ads_created': 0, 'adgroups_failed': 0, 'ads_failed': 0, 'adgroups': [], 'ads': [], 'errors': []}
            if not skip_adgroups:
                if verbose:
                    print(f'  Found {len(source_adgroups)} adgroup(s) to copy', file=sys.stderr)
                for adgroup_idx, source_adgroup in enumerate(source_adgroups, 1):
                    try:
                        source_adgroup_id = deps.validate_non_empty(source_adgroup.get('adgroup_id'), 'adgroup_id')
                        adgroup_name = source_adgroup.get('adgroup_name', f'Adgroup {adgroup_idx}')
                        adgroup_strategy = deps.infer_adgroup_copy_strategy(source_adgroup, campaign_strategy=copy_strategy)
                        if verbose:
                            print(f'  Creating adgroup {adgroup_idx}/{len(source_adgroups)}: {adgroup_name}...', file=sys.stderr)
                            print(f'    Adgroup strategy: {adgroup_strategy}', file=sys.stderr)
                        adgroup_payload = deps.build_adgroup_copy_payload(source_adgroup, advertiser_id=advertiser_id, campaign_id=new_campaign_id, operation_status=adgroup_status, adgroup_strategy=adgroup_strategy)
                        adgroup_result = client.create_adgroup(adgroup_payload, smart_plus=target_smart_plus)
                        adgroup_data = adgroup_result.get('data', {})
                        new_adgroup_id = adgroup_data.get('adgroup_id') or adgroup_data.get('smart_plus_adgroup_id')
                        if not new_adgroup_id:
                            error_msg = adgroup_result.get('message', 'Unknown error')
                            campaign_info['adgroups_failed'] += 1
                            campaign_info['errors'].append({'kind': 'adgroup', 'source_adgroup_id': source_adgroup_id, 'error': error_msg})
                            if verbose:
                                print(f'    Failed: {error_msg}', file=sys.stderr)
                            continue
                        if verbose:
                            print(f'    Created adgroup ID: {new_adgroup_id}', file=sys.stderr)
                        campaign_info['adgroups_created'] += 1
                        campaign_info['adgroups'].append({'adgroup_id': new_adgroup_id, 'source_adgroup_id': source_adgroup_id})
                        if new_adgroup_id and (not skip_ads):
                            source_ads = source_ads_by_adgroup.get(source_adgroup_id, [])
                            if verbose:
                                print(f'    Creating {len(source_ads)} ad(s)...', file=sys.stderr)
                            for source_ad in source_ads:
                                try:
                                    ad_strategy = deps.infer_ad_copy_strategy(source_ad, adgroup_strategy=adgroup_strategy)
                                    if verbose:
                                        ad_name = source_ad.get('ad_name') or source_ad.get('smart_plus_ad_id') or source_ad.get('ad_id')
                                        print(f'      Ad strategy for {ad_name}: {ad_strategy}', file=sys.stderr)
                                    if target_smart_plus and ad_strategy.get('use_smart_plus_payload'):
                                        ad_payload = deps.build_smart_plus_ad_copy_payload(source_ad, advertiser_id=advertiser_id, adgroup_id=new_adgroup_id, operation_status=ad_status, landing_page_url=landing_page_url, promotion_type=source_adgroup.get('promotion_type'), ad_strategy=ad_strategy)
                                    else:
                                        ad_payload = deps.build_normal_ad_copy_payload(source_ad, advertiser_id=advertiser_id, adgroup_id=new_adgroup_id, operation_status=ad_status, landing_page_url=landing_page_url, promotion_type=source_adgroup.get('promotion_type'), client=client, source_smart_plus=effective_smart_plus, ad_strategy=ad_strategy)
                                    ad_result = client.create_ad(ad_payload, smart_plus=target_smart_plus)
                                    ad_data = ad_result.get('data', {})
                                    ad_id = ad_data.get('smart_plus_ad_id') or ad_data.get('ad_id')
                                    if not ad_id:
                                        ad_ids = ad_data.get('ad_ids') or ad_data.get('smart_plus_ad_ids')
                                        if isinstance(ad_ids, list) and ad_ids:
                                            ad_id = ad_ids[0]
                                    if ad_id:
                                        campaign_info['ads_created'] += 1
                                        campaign_info['ads'].append({'adgroup_id': new_adgroup_id, 'response': ad_data})
                                    else:
                                        campaign_info['ads_failed'] += 1
                                        campaign_info['errors'].append({'kind': 'ad', 'adgroup_id': new_adgroup_id, 'source_ad_id': source_ad.get('ad_id') or source_ad.get('smart_plus_ad_id'), 'error': ad_result.get('message', 'Unknown error')})
                                        if verbose:
                                            error_msg = ad_result.get('message', 'Unknown error')
                                            print(f'      Ad failed: {error_msg}', file=sys.stderr)
                                except deps.CliError as exc:
                                    campaign_info['ads_failed'] += 1
                                    campaign_info['errors'].append({'kind': 'ad', 'adgroup_id': new_adgroup_id, 'source_ad_id': source_ad.get('ad_id') or source_ad.get('smart_plus_ad_id'), 'error': str(exc)})
                                    if verbose:
                                        print(f'      Ad failed: {exc}', file=sys.stderr)
                    except deps.CliError as exc:
                        campaign_info['adgroups_failed'] += 1
                        campaign_info['errors'].append({'kind': 'adgroup', 'source_adgroup_id': source_adgroup.get('adgroup_id'), 'error': str(exc)})
                        if verbose:
                            print(f'    Adgroup failed: {exc}', file=sys.stderr)
            created_campaigns.append(campaign_info)
            if verbose:
                print(f"  Campaign complete: {campaign_info['adgroups_created']} adgroups, {campaign_info['ads_created']} ads created", file=sys.stderr)
        except deps.CliError as exc:
            failed_campaigns.append({'name': campaign_name, 'error': str(exc)})
            if verbose:
                print(f'  Campaign failed: {exc}', file=sys.stderr)
    has_failures = bool(failed_campaigns) or any((c['adgroups_failed'] or c['ads_failed'] for c in created_campaigns))
    status = 'failed' if not created_campaigns else 'partial_success' if has_failures else 'success'
    result = {
        'ok': status == 'success', 'status': status,
        'exit_code': 0 if status == 'success' else (3 if status == 'partial_success' else 1),
        'source_campaign_id': campaign_id, 'copies_requested': copies,
        'copies_created': len(created_campaigns), 'copies_failed': len(failed_campaigns),
        'copy_route': copy_route, 'campaigns': created_campaigns,
        'writes_attempted': True,
        'recovery': 'Inspect created IDs and remote state before retrying; this copy operation has no resume ledger.' if has_failures else None,
    }
    if failed_campaigns:
        result['failed'] = failed_campaigns
    if verbose and created_campaigns:
        total_adgroups = sum((c['adgroups_created'] for c in created_campaigns))
        total_ads = sum((c['ads_created'] for c in created_campaigns))
        print(f'Summary: {total_adgroups} adgroups, {total_ads} ads created across {len(created_campaigns)} campaign(s)', file=sys.stderr)
    return result

def command_tiktok_campaigns_copy(args: argparse.Namespace, *, deps) -> None:
    advertiser_id, client = deps.resolve_tiktok_client(args)
    result = deps.execute_tiktok_campaign_copy(client, advertiser_id=advertiser_id, campaign_id=args.campaign_id, name=args.name, copies=1 if args.copies is None else args.copies, operation_status=args.operation_status, adgroup_status=args.adgroup_status, ad_status=args.ad_status, landing_page_url=args.landing_page_url, page_id=getattr(args, 'page_id', None), skip_adgroups=args.skip_adgroups, skip_ads=args.skip_ads, smart_plus=args.smart_plus, copy_route=getattr(args, 'copy_route', 'auto'), verbose=args.verbose)
    deps.print_output(result, as_json=args.json)
    if not result['ok']:
        raise deps.CliError(f"Campaign copy {result['status']}; see output for created objects and errors", exit_code=result['exit_code'])

def command_tiktok_smartplus_campaigns_bootstrap_app(args: argparse.Namespace, *, deps) -> None:
    copies = 1 if args.copies is None else args.copies
    if copies < 1:
        raise deps.CliError('copies must be at least 1')
    advertiser_id, client = deps.resolve_tiktok_client(args)
    selected_app = None
    template_match: dict[str, Any]
    requested = deps.compact_mapping({'name': args.name, 'app_id': args.app_id, 'app_name': args.app_name, 'app_promotion_type': args.app_promotion_type, 'template_campaign_id': args.template_campaign_id, 'copies': args.copies})
    if args.template_campaign_id:
        source_campaign_data, effective_smart_plus, source_adgroups, source_ads_by_adgroup, copy_strategy = deps.load_tiktok_campaign_copy_source(client, advertiser_id=advertiser_id, campaign_id=args.template_campaign_id, smart_plus=True, skip_adgroups=False, skip_ads=False, verbose=args.verbose)
        if not effective_smart_plus:
            raise deps.CliError(f'Template campaign {args.template_campaign_id} is not a SmartPlus campaign')
        if source_campaign_data.get('objective_type') != 'APP_PROMOTION':
            raise deps.CliError(f'Template campaign {args.template_campaign_id} is not an APP_PROMOTION SmartPlus campaign')
        source_adgroup = deps.first_dict(source_adgroups) or {}
        source_ad = deps.first_dict(source_ads_by_adgroup.get(str(source_adgroup.get('adgroup_id') or ''), []))
        if source_adgroup.get('app_id'):
            try:
                selected_app = client.get_app_info(advertiser_id, str(source_adgroup.get('app_id')))
            except deps.CliError:
                selected_app = deps.compact_mapping({'app_id': source_adgroup.get('app_id')})
        template_match = {'selection_mode': 'template_campaign_id', 'requested': deps.compact_mapping({'template_campaign_id': args.template_campaign_id}), 'app': deps.summarize_tiktok_app(selected_app) if isinstance(selected_app, dict) and selected_app.get('app_id') else selected_app, 'candidate_count': 1, 'template_campaign': deps.summarize_tiktok_template_campaign(source_campaign_data), 'template_adgroup': deps.summarize_tiktok_template_adgroup(source_adgroup), 'template_ad': deps.summarize_tiktok_template_ad(source_ad) if source_ad else None, 'copy_strategy': copy_strategy}
        template_campaign_id = deps.validate_non_empty(source_campaign_data.get('campaign_id'), 'campaign_id')
    else:
        template_match = deps.find_tiktok_smartplus_app_template(client, advertiser_id=advertiser_id, app_id=args.app_id, app_name=args.app_name, app_promotion_type=args.app_promotion_type)
        template_match['selection_mode'] = 'auto_match'
        template_campaign_id = (template_match.get('template_campaign') or {}).get('campaign_id')
        if not template_campaign_id:
            raise deps.CliError(f"No SmartPlus APP_PROMOTION template campaign found for {requested or {'scope': 'current advertiser'}}")
    result = {'ok': True, 'advertiser_id': advertiser_id, 'mode': 'dry_run' if args.dry_run else 'create', 'status': 'success', 'writes_performed': False, 'requested': requested, 'matched': template_match}
    if args.dry_run:
        deps.print_output(result, as_json=args.json)
        return
    copy_result = deps.execute_tiktok_campaign_copy(client, advertiser_id=advertiser_id, campaign_id=str(template_campaign_id), name=args.name, copies=copies, operation_status=args.operation_status, adgroup_status=args.adgroup_status, ad_status=args.ad_status, landing_page_url=None, page_id=getattr(args, 'page_id', None), skip_adgroups=False, skip_ads=False, smart_plus=True, verbose=args.verbose)
    result['copy_result'] = copy_result
    result['ok'] = copy_result['ok']
    result['status'] = copy_result['status']
    result['writes_attempted'] = True
    result['writes_performed'] = True if copy_result['copies_created'] else None
    result['exit_code'] = copy_result['exit_code']
    deps.print_output(result, as_json=args.json)
    if not result['ok']:
        raise deps.CliError(f"SmartPlus bootstrap {result['status']}; see output for created objects and errors", exit_code=result['exit_code'])
