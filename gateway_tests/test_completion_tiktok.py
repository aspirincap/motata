"""Exercise generated SDK and raw request builders, not just policy functions."""
from __future__ import annotations
import ast
import json
from pathlib import Path
from completion_helpers import CompletionCase
from motata_cli.transport.gateway import GatewayAuthRef
from motata_cli.tiktok.client import TikTokClient
from motata_gateway.policy import TIKTOK_COLLECTIONS,TIKTOK_POST_READS,TIKTOK_WRITES
from motata_gateway.errors import GatewayError


class TikTokCompletionTests(CompletionCase):
    async def tt(self, callback):
        def run(_):
            client=TikTokClient(GatewayAuthRef('tiktok','123'))
            self.assertIsNone(client.access_token)
            return callback(client)
        return await self.invoke(run)

    async def test_all_reachable_sdk_builders_have_exact_reviewed_routes(self):
        root=Path(__file__).resolve().parents[1]
        tree=ast.parse((root/'motata_cli/tiktok/client.py').read_text())
        methods=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Attribute) and isinstance(node.value.value,ast.Name) and node.value.value.id=='self' and node.value.attr.endswith('_api'):
                methods.add(node.attr)
        routes={}
        for path in (root/'motata_cli/_vendor/tiktok_business_api_sdk/python_sdk/business_api_client/api').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node,ast.FunctionDef) and node.name.endswith('_with_http_info'):
                    for call in ast.walk(node):
                        if isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute) and call.func.attr=='call_api' and len(call.args)>1 and all(isinstance(x,ast.Constant) for x in call.args[:2]):
                            routes[node.name.removesuffix('_with_http_info')]=(call.args[0].value.removeprefix('/open_api/v1.3/'),call.args[1].value)
        excluded={'oauth2_advertiser_get'}
        self.assertGreater(len(methods),40)
        for method in sorted(methods-excluded):
            with self.subTest(method=method):
                self.assertIn(method,routes)
                route,verb=routes[method]
                allowed=TIKTOK_COLLECTIONS if verb=='GET' else (set(TIKTOK_POST_READS)|set(TIKTOK_WRITES))
                self.assertIn(route,allowed)
        self.assertNotIn(routes['oauth2_advertiser_get'][0],TIKTOK_COLLECTIONS)

    async def test_bc_list_filters_broad_token_without_creating_grants(self):
        self.state.bind_object('tiktok','800','123','bc')
        self.reply={'code':0,'data':{'list':[{'bc_id':'800'},{'bc_id':'900'}],'page_info':{'total_number':2}}}
        response=await self.tt(lambda c:c.list_business_centers())
        self.assertEqual(response['data']['list'],[{'bc_id':'800'}]);self.assertNotIn('900',json.dumps(response))
        with self.assertRaises(GatewayError):self.state.require_object('tiktok','900','123','bc')

    async def test_bc_asset_read_requires_trusted_binding_before_secret(self):
        response=await self.post(self.body(platform='tiktok',path='bc/asset/get/',query={'bc_id':'800','asset_type':'PIXEL'}))
        self.assertEqual(response.status_code,403,response.text);self.assert_no_upstream()
        self.state.bind_object('tiktok','800','123','bc')
        self.reply={'code':0,'data':{'list':[{'asset_id':'900'}]}}
        result=await self.tt(lambda c:c.list_bc_assets('800',asset_type='PIXEL',page=1,page_size=20))
        self.assertEqual(result['code'],0);self.state.require_object('tiktok','900','123','pixel')

    async def test_bc_advertiser_assets_filtered_to_current_grants(self):
        self.grant('tiktok','456');self.state.bind_object('tiktok','800','123','bc')
        self.reply={'code':0,'data':{'list':[{'advertiser_id':'123'},{'advertiser_id':'456'},{'advertiser_id':'999'}]}}
        result=await self.tt(lambda c:c.list_bc_assets('800',asset_type='ADVERTISER'))
        self.assertEqual([x['advertiser_id'] for x in result['data']['list']],['123','456'])

    async def test_catalog_sdk_uses_bc_and_catalog_evidence(self):
        self.state.bind_object('tiktok','800','123','bc')
        def router(req):
            if req.url.path.endswith('/catalog/get/'):
                return {'code':0,'data':{'list':[{'catalog_id':'901'}]}}
            return {'code':0,'data':{'list':[]}}
        await self.router(router)
        await self.tt(lambda c:(c.list_catalogs('800',page=1,page_size=20),c.get_catalog_eventsource_bindings('901','800')))
        self.assertEqual(len(self.seen),2)
        self.assertEqual(self.seen[1].url.params['catalog_id'],'901')
        for req in self.seen:self.assertEqual(req.headers['Access-Token'],self.tiktok_secret)

    async def test_multi_advertiser_report_validates_every_account(self):
        params={'advertiser_ids':['123','456'],'report_type':'BASIC','dimensions':['advertiser_id'],'metrics':['spend']}
        response=await self.post(self.body(platform='tiktok',path='report/integrated/get/',query=params))
        self.assertEqual(response.status_code,403);self.assert_no_upstream()
        self.grant('tiktok','456')
        self.reply={'code':0,'data':{'list':[{'dimensions':{'advertiser_id':'123'},'metrics':{'spend':'1'}},
                                          {'dimensions':{'advertiser_id':'456'},'metrics':{'spend':'2'}}]}}
        result=await self.tt(lambda c:c.integrated_report('BASIC',advertiser_ids=['123','456'],dimensions=['advertiser_id'],metrics=['spend'],multi_adv_report_in_utc_time=True,enable_total_metrics=True))
        self.assertEqual(len(result['data']['list']),2)
        self.assertEqual(json.loads(self.seen[-1].url.params['advertiser_ids']),['123','456'])

    async def test_integrated_report_preserves_all_existing_parameters(self):
        self.state.bind_object('tiktok','800','123','bc');self.reply={'code':0,'data':{'list':[]}}
        params=dict(advertiser_id='123',bc_id='800',service_type='AUCTION',data_level='AUCTION_AD',
            dimensions=['ad_id','stat_time_day'],metrics=['spend','impressions'],start_date='2026-09-01',end_date='2026-09-07',
            query_lifetime=False,query_mode='REGULAR',order_field='spend',order_type='DESC',page=2,page_size=100,
            enable_total_metrics=True,multi_adv_report_in_utc_time=False,
            filtering=[{'field_name':'ad_ids','filter_type':'IN','filter_value':'["456"]'}])
        await self.tt(lambda c:c.integrated_report('BASIC',**params))
        query=dict(self.seen[-1].url.params)
        self.assertEqual(query['page'],'2');self.assertEqual(query['query_lifetime'],'false')
        self.assertEqual(json.loads(query['filtering']),params['filtering'])
        self.assertEqual(json.loads(query['dimensions']),params['dimensions'])
        self.assertEqual(set(query),set(params)|{'report_type'})

    async def test_gmv_store_report_and_product_queries_use_shared_evidence(self):
        self.state.bind_object('tiktok','800','123','bc')
        def route(req):
            if req.url.path.endswith('/gmv_max/store/list/'):
                return {'code':0,'data':{'store_list':[{'store_id':'901','is_gmv_max_available':True,'exclusive_authorized_advertiser_info':{'advertiser_id':'123'}}]}}
            return {'code':0,'data':{'list':[]}}
        await self.router(route)
        await self.tt(lambda c:(c.list_stores('123'),c.gmv_max_report('123',store_ids=['901'],dimensions=['campaign_id','stat_time_day'],metrics=['cost','gross_revenue'],start_date='2026-09-01',end_date='2026-09-07',filtering={'promotion_types':['PRODUCT_SHOP']},enable_total_metrics=True,sort_field='cost',sort_type='DESC',page=2,page_size=20),
            c.list_store_products('123',bc_id='800',store_id='901',filtering={'item_group_ids':['1001']},sort_field='gmv',sort_type='DESC',page=1,page_size=20),
            c.list_gmv_max_videos('123',store_id='901',store_authorized_bc_id='800',spu_id_list=['1001'],custom_posts_eligible=True,need_auth_code_video=False,keyword='summer',identity_list=[{'identity_id':'identity-opaque'}],page=1,page_size=20)))
        self.assertEqual(len(self.seen),4)
        q=self.seen[1].url.params
        self.assertEqual(json.loads(q['store_ids']),['901']);self.assertEqual(q['sort_type'],'DESC')
        self.state.require_object('tiktok','901','123','store')

    async def test_gmv_report_auto_discovers_store_before_platform_report(self):
        def route(req):
            if req.url.path.endswith('/gmv_max/store/list/'):
                return {'code':0,'data':{'store_list':[{'store_id':'901'}]}}
            return {'code':0,'data':{'list':[]}}
        await self.router(route)
        await self.tt(lambda c:c.gmv_max_report('123',store_ids=['901'],dimensions=['campaign_id'],metrics=['cost'],start_date='2026-09-01',end_date='2026-09-07'))
        self.assertEqual(len(self.seen),2);self.assertTrue(self.seen[0].url.path.endswith('/store/list/'))

    async def test_smart_plus_sdk_reports_preserve_breakdowns_and_filters(self):
        self.reply={'code':0,'data':{'list':[]}}
        def run(c):
            c.smart_plus_material_report_overview('123',['material_id'],metrics=['spend'],start_date='2026-09-01',end_date='2026-09-07',query_lifetime=False,filtering={'campaign_ids':['456']},sort_field='spend',sort_type='DESC',page=1,page_size=100)
            c.smart_plus_material_report_breakdown('123',['material_id','stat_time_day'],'2026-09-01','2026-09-07',metrics=['spend'],filtering={'campaign_ids':['456']},sort_field='spend',sort_type='DESC',page=2,page_size=20)
        await self.tt(run)
        self.assertEqual(len(self.seen),2)
        self.assertEqual(json.loads(self.seen[1].url.params['dimensions']),['material_id','stat_time_day'])
        self.assertEqual(self.seen[1].url.params['page'],'2')

    async def test_smart_plus_full_hierarchy_create_update_status(self):
        created={'campaign':'700','adgroup':'701','ad':'702'}
        def route(req):
            path=req.url.path.removeprefix('/open_api/v1.3/')
            entity=path.split('/')[1]
            if path.endswith('/create/'):return {'code':0,'data':{entity+'_id':created[entity]}}
            return {'code':0,'data':{}}
        await self.router(route)
        def run(c):
            c.create_campaign({'advertiser_id':'123','campaign_name':'Smart','operation_status':'DISABLE'},smart_plus=True)
            c.create_adgroup({'advertiser_id':'123','campaign_id':'700','adgroup_name':'G','operation_status':'DISABLE'},smart_plus=True)
            c.create_ad({'advertiser_id':'123','adgroup_id':'701','ad_name':'A','operation_status':'DISABLE'},smart_plus=True)
            for entity in ('campaign','adgroup','ad'):
                getattr(c,'update_'+entity)({'advertiser_id':'123',entity+'_id':created[entity],entity+'_name':'renamed'},smart_plus=True)
                getattr(c,'update_'+entity+'_status')({'advertiser_id':'123',entity+'_ids':[created[entity]],'operation_status':'DISABLE'},smart_plus=True)
        await self.tt(run)
        self.assertEqual(len(self.seen),9)
        for req in self.seen:self.assertIn('/smart_plus/',req.url.path)

    async def test_sdk_identity_portfolio_and_shared_asset_mutations(self):
        self.grant('tiktok','456')
        self.state.bind_object('tiktok','img-opaque','123','image')
        def route(req):
            path=req.url.path
            if path.endswith('/identity/create/'):return {'code':0,'data':{'identity_id':'identity-opaque'}}
            if path.endswith('/portfolio/create/'):return {'code':0,'data':{'creative_portfolio_id':'portfolio-opaque'}}
            if path.endswith('/shareable_link/create/'):return {'code':0,'data':{'shareable_link':'https://ads.tiktok.com/preview/example'}}
            return {'code':0,'data':{}}
        await self.router(route)
        await self.tt(lambda c:(c.create_identity({'advertiser_id':'123','display_name':'Test'}),
            c.create_creative_portfolio({'advertiser_id':'123','image_ids':['img-opaque'],'portfolio_name':'Test'}),
            c.share_assets({'advertiser_id':'123','target_advertiser_ids':['456'],'image_ids':['img-opaque']}),
            c.create_shareable_link({'advertiser_id':'123','creative_portfolio_id':'portfolio-opaque'}),
            c.delete_assets({'advertiser_id':'123','image_ids':['img-opaque']})))
        self.assertEqual(len(self.seen),5)
        self.state.require_object('tiktok','portfolio-opaque','123','portfolio')

    async def test_aigc_avatar_task_reads_and_write_receipts(self):
        self.state.bind_object('tiktok','video-opaque','123','video')
        def route(req):
            if req.url.path.endswith('/task/create/'):return {'code':0,'data':{'task_id':'task-'+str(len(self.seen))}}
            if req.url.path.endswith('/update/'):return {'code':0,'data':{}}
            return {'code':0,'data':{'list':[]}}
        await self.router(route)
        await self.tt(lambda c:(c.list_aigc_voices('123'),c.create_image_animation_task({'advertiser_id':'123'}),
            c.create_aigc_video_task({'advertiser_id':'123'}),c.list_aigc_video_tasks('123',aigc_video_type='IMAGE_ANIMATION'),c.list_aigc_videos('123',aigc_video_types=['IMAGE_ANIMATION']),
            c.list_digital_avatars('123'),c.create_digital_avatar_video_task({'advertiser_id':'123'}),
            c.get_digital_avatar_video_task('123','task-7'),c.list_digital_avatar_videos('123'),
            c.update_video_file_name(avatar_video_id='video-opaque',file_name='new.mp4')))
        self.assertEqual(len(self.seen),10)

    async def test_discovery_and_targeting_sdk_no_extra_secret_inputs(self):
        self.reply={'code':0,'data':{'list':[]}}
        await self.tt(lambda c:(c.list_pixels('123'),c.list_offline_event_sets(advertiser_id='123'),c.list_apps('123'),
            c.get_app_info('123','app1'),c.list_app_optimization_events('app1','123','INSTALL'),
            c.search_regions('123',language='en'),c.targeting_list('123',['6252001'],'GENERAL'),
            c.targeting_info({'advertiser_id':'123','targeting_ids':['abc']}),
            c.targeting_search({'advertiser_id':'123','query':'sport'}),c.generate_smart_text({'advertiser_id':'123','text':'Hello'}),
            c.validate_url('123','https://shop.example.com/'),c.list_creative_portfolios('123'),c.list_identities('123')))
        self.assertEqual(len(self.seen),13)
        for req in self.seen:self.assertEqual(req.headers['Access-Token'],self.tiktok_secret)

    async def test_unknown_and_oauth_paths_still_rejected(self):
        for path in ('oauth2/access_token/','oauth2/advertiser/get/','arbitrary/token/','user/permissions/'):
            response=await self.post(self.body(platform='tiktok',path=path,query={'advertiser_id':'123'}))
            self.assertEqual(response.status_code,403,response.text)
        self.assert_no_upstream()


    async def test_changelog_job_is_read_scope_but_not_blindly_replayed(self):
        self.reply = {'code': 0, 'data': {'task_id': 'log-task-1'}}
        request = self.body(platform='tiktok', method='POST', path='changelog/task/create/',
                            query={}, body={'advertiser_id':'123'}, idempotency_key='changelog-test-key-123')
        token = self.token(claims={**self.claims(), 'scope':'gateway:use tiktok:read'})
        first = await self.client.post('/v1/platform/request', json=request,
                                       headers={'Authorization':'Bearer '+token})
        second = await self.client.post('/v1/platform/request', json=request,
                                        headers={'Authorization':'Bearer '+token})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(len(self.seen), 1)

    async def test_smart_plus_plural_create_ids_are_confirmed_not_unknown(self):
        self.reply = {'code':0, 'data':{'smart_plus_ad_ids':['opaque-smart-ad-1', 'opaque-smart-ad-2']}}
        result = await self.tt(lambda c:c.create_ad({'advertiser_id':'123','ad_name':'Two creatives'}, smart_plus=True))
        self.assertEqual(result['code'], 0)
        for ident in ('opaque-smart-ad-1','opaque-smart-ad-2'):
            self.state.require_object('tiktok', ident, '123', 'ad')


    async def test_bc_unreviewed_collection_cannot_invent_membership(self):
        self.state.bind_object('tiktok','800','123','bc')
        self.reply={'code':0,'data':{'accounts':[{'bc_id':'900','name':'MUST_NOT_ESCAPE'}]}}
        response=await self.post(self.body(platform='tiktok',path='bc/get/',query={}))
        self.assertEqual(response.status_code,502,response.text)
        self.assertNotIn('MUST_NOT_ESCAPE',response.text)
        with self.assertRaises(GatewayError): self.state.require_object('tiktok','900','123','bc')
        self.reply={'code':0,'data':{'list':[{'bc_id':'800'}],'accounts':[{'bc_id':'900'}]}}
        response=await self.post(self.body(platform='tiktok',path='bc/get/',query={}))
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('900',response.text)
        with self.assertRaises(GatewayError): self.state.require_object('tiktok','900','123','bc')

    async def test_bc_advertiser_alternate_collection_is_not_forwarded(self):
        self.state.bind_object('tiktok','800','123','bc')
        self.reply={'code':0,'data':{'accounts':[{'advertiser_id':'999','name':'MUST_NOT_ESCAPE'}]}}
        response=await self.post(self.body(platform='tiktok',path='bc/asset/get/',query={'bc_id':'800','asset_type':'ADVERTISER'}))
        self.assertEqual(response.status_code,502,response.text)
        self.assertNotIn('MUST_NOT_ESCAPE',response.text)
