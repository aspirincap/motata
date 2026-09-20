"""Non-empty report and complete creative/media/hierarchy migration fixtures."""
from __future__ import annotations
import json
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import patch
import httpx
from completion_helpers import CompletionCase
from motata_cli.meta.client import MetaClient
from motata_cli.transport.gateway import GatewayAuthRef


class ReportCompletionTests(CompletionCase):
    def meta_data(self):
        creative={'id':'444','name':'Creative','image_url':'https://a.fbcdn.net/original.jpg',
            'thumbnail_url':'https://a.fbcdn.net/thumb.jpg','object_story_id':'800_801',
            'object_story_spec':{'page_id':'800','link_data':{'link':'https://shop.example.com/one','message':'Body','name':'Headline','image_hash':'image-hash'}}}
        campaign={'id':'111','name':'Campaign','account_id':'123','objective':'OUTCOME_TRAFFIC','status':'ACTIVE','effective_status':'ACTIVE'}
        adset={'id':'222','account_id':'123','name':'Adset','campaign_id':'111','optimization_goal':'LINK_CLICKS','billing_event':'IMPRESSIONS','status':'ACTIVE','targeting':{},'promoted_object':{'page_id':'800','pixel_id':'900'}}
        ad={'id':'333','account_id':'123','name':'Ad','campaign_id':'111','adset_id':'222','status':'ACTIVE','creative':creative,'campaign':campaign,'adset':adset}
        return campaign,adset,ad,creative

    async def install_meta(self, app=False):
        campaign,adset,ad,creative=self.meta_data();reports={}
        if app:
            campaign['objective']='OUTCOME_APP_PROMOTION'
            adset['promoted_object']['application_id']='902'
            adset['destination_type']='APP'
            adset['optimization_goal']='APP_INSTALLS'
        def insight(params):
            fields=params.get('fields','');level=params.get('level','account')
            row={'account_id':'123','account_name':'Test','date_start':'2026-09-01','date_stop':'2026-09-07',
                 'spend':'10','impressions':'1000','clicks':'25','reach':'900','frequency':'1.1',
                 'cpc':'0.4','cpm':'10','ctr':'2.5','actions':[{'action_type':'link_click','value':'25'},{'action_type':'purchase','value':'2'}],
                 'action_values':[{'action_type':'purchase','value':'50'}],'purchase_roas':[{'action_type':'purchase','value':'5'}]}
            if level in ('campaign','adset','ad') or 'campaign_id' in fields:row.update(campaign_id='111',campaign_name='Campaign',objective='OUTCOME_TRAFFIC')
            if level in ('adset','ad') or 'adset_id' in fields:row.update(adset_id='222',adset_name='Adset')
            if level=='ad' or 'ad_id' in fields:row.update(ad_id='333',ad_name='Ad')
            values={'country':'US','age':'25-34','gender':'female','publisher_platform':'facebook','platform_position':'feed','impression_device':'iphone','device_platform':'mobile_app','region':'California'}
            for key in str(params.get('breakdowns','')).split(','):
                if key:row[key]=values.get(key,'unknown')
            return {'data':[row]}
        def route(req):
            path=req.url.path.removeprefix('/v23.0/');q=dict(req.url.params)
            if req.method=='POST' and path.endswith('/insights'):
                ident=str(700+len(reports));reports[ident]={k:v[0] for k,v in parse_qs(req.content.decode()).items()}
                return {'report_run_id':ident}
            if path in reports:return {'id':path,'async_status':'Job Completed','async_percent_completion':100}
            if path.split('/')[0] in reports and path.endswith('/insights'):return insight(reports[path.split('/')[0]])
            if path.endswith('/insights'):return insight(q)
            if path=='act_123':return {'id':'act_123','account_id':'123','name':'Test','currency':'USD','timezone_name':'UTC','account_status':1}
            if path.endswith('/campaigns'):return {'data':[campaign]}
            if path.endswith('/adsets'):return {'data':[adset]}
            if path.endswith('/ads'):return {'data':[ad]}
            if path.endswith('/adcreatives'):return {'data':[creative]}
            if path.endswith('/promote_pages'):return {'data':[{'id':'800','name':'Page'}]}
            if path=='me/accounts':return {'data':[{'id':'800','name':'Page','access_token':'SECRET_CANARY_DERIVED_REPORT_PAGE'}]}
            if path.endswith('/adspixels'):return {'data':[{'id':'900','name':'Pixel'}]}
            if path.endswith(('/advertisable_applications','/applications','/activities','/instagram_accounts')):return {'data':[]}
            if path=='902':return {'id':'902','name':'Scoped Demo App','app_domains':['shop.example.com'],'category':'Shopping','link':'https://shop.example.com/app','namespace':'testapp'}
            if path=='800_801':return {'id':path,'permalink_url':'https://www.facebook.com/posts/800_801','attachments':{'data':[{'url':'https://shop.example.com/one'}]}}
            if path in ('111','222','333','444'):return {'111':campaign,'222':adset,'333':ad,'444':{**creative,'account_id':'123'}}[path]
            raise AssertionError('unhandled Meta fixture request '+path)
        await self.router(route)

    async def test_meta_full_report_nonempty_compare_structures_and_previews(self):
        await self.install_meta();out=self.root/'full-meta'
        code,stdout,stderr=await self.run_cli(['report','meta','run','--account-id','123','--period','custom','--since','2026-09-01','--until','2026-09-07','--previous-since','2026-08-25','--previous-until','2026-08-31','--depth','full','--include-previews','--limit','100','--top-objects','10','--retry','1','--retry-wait','0','--run-dir',str(out)])
        manifest=json.loads((out/'manifest.json').read_text())
        self.assertEqual(code,0,stderr+stdout+json.dumps(manifest,ensure_ascii=False))
        self.assertTrue(manifest['completeness']['complete'])
        sources={x['name'] for x in manifest['sources']}
        self.assertIn('previous_ad_insights',sources);self.assertIn('landing_pages',sources)
        self.assertGreater(len(self.seen),20)
        data=''.join(p.read_text() for p in out.glob('*.json'))
        self.assertIn('333',data);self.assertNotIn(self.meta_secret,data);self.assertNotIn('SECRET_CANARY_DERIVED_REPORT_PAGE',data)

    async def test_meta_deep_report_keeps_explicit_window_and_breakdowns(self):
        await self.install_meta();out=self.root/'deep-meta'
        code,stdout,stderr=await self.run_cli(['report','meta','run','--account-id','123','--period','weekly','--depth','deep','--include-previews','--retry','1','--retry-wait','0','--run-dir',str(out)])
        self.assertEqual(code,0,stderr+stdout+(out/'manifest.json').read_text())
        self.assertTrue(any('breakdowns' in req.url.params for req in self.seen) or any(b'breakdowns' in req.content for req in self.seen))

    async def test_meta_full_app_report_uses_account_bound_application_metadata(self):
        await self.install_meta(app=True)
        out=self.root/'meta-app-report'
        code,stdout,stderr=await self.run_cli(['report','meta','run','--account-id','123','--period','weekly',
            '--depth','full','--retry','1','--retry-wait','0','--run-dir',str(out)])
        self.assertEqual(code,0,stderr+stdout+(out/'manifest.json').read_text())
        self.assertTrue(json.loads((out/'manifest.json').read_text())['completeness']['complete'])
        text=''.join(p.read_text() for p in out.glob('*.json'))
        self.assertIn('Scoped Demo App',text)
        self.assertTrue(any(req.url.path.endswith('/902') for req in self.seen))
        self.state.require_object('meta','902','123','app')

    async def test_meta_account_fields_limit_and_pagination_preserved(self):
        self.grant('meta','456');self.grant('meta','789')
        def route(req):
            account=req.url.path.split('act_')[1]
            self.assertEqual(req.url.params['fields'],'id,name,currency')
            return {'id':'act_'+account,'account_id':account,'name':'Account '+account,'currency':'USD'}
        await self.router(route)
        def run(_):
            meta=MetaClient(GatewayAuthRef('meta'),version='v23.0')
            return meta.paginate('me/adaccounts',params={'fields':'id,name,currency','limit':1})
        result=await self.invoke(run)
        self.assertEqual([r['account_id'] for r in result],['123','456','789']);self.assertEqual(len(self.seen),3)
        self.assertTrue(all('/me/adaccounts' not in str(q.url) for q in self.seen))

    async def install_tiktok(self):
        self.state.bind_object('tiktok','800','123','bc')
        campaign={'campaign_id':'111','campaign_name':'Campaign','objective_type':'TRAFFIC','operation_status':'ENABLE'}
        adgroup={'adgroup_id':'222','campaign_id':'111','adgroup_name':'Group','promotion_type':'WEBSITE','pixel_id':'900','operation_status':'ENABLE'}
        ad={'ad_id':'333','smart_plus_ad_id':'333','adgroup_id':'222','campaign_id':'111','ad_name':'Ad','ad_text':'Text','landing_page_url':'https://shop.example.com/one','ad_format':'SINGLE_VIDEO','video_id':'video-1','identity_id':'identity-1','identity_type':'CUSTOMIZED_USER','operation_status':'ENABLE'}
        def listing(rows):return {'code':0,'data':{'list':rows,'page_info':{'page':1,'total_page':1,'total_number':len(rows)}}}
        def route(req):
            path=req.url.path.removeprefix('/open_api/v1.3/');q=dict(req.url.params)
            if path=='advertiser/info/':return listing([{'advertiser_id':'123','name':'Test','currency':'USD','timezone':'UTC'}])
            if path=='gmv_max/store/list/':return {'code':0,'data':{'store_list':[{'store_id':'901','store_name':'Shop','is_gmv_max_available':True,'exclusive_authorized_advertiser_info':{'advertiser_id':'123'},'bc_id':'800'}]}}
            if path in ('campaign/get/','smart_plus/campaign/get/'):return listing([campaign])
            if path in ('adgroup/get/','smart_plus/adgroup/get/'):return listing([adgroup])
            if path in ('ad/get/','smart_plus/ad/get/'):return listing([ad])
            if path=='gmv_max/campaign/get/':return listing([{'campaign_id':'888','campaign_name':'GMV','promotion_type':'PRODUCT_GMV_MAX','store_id':'901'}])
            if path=='campaign/gmv_max/info/':return {'code':0,'data':{'campaign_id':'888','campaign_name':'GMV','store_id':'901'}}
            if 'report/' in path or 'material_report/' in path:
                dimensions=json.loads(q.get('dimensions','[]'))
                defaults={'advertiser_id':'123','campaign_id':'888' if path.startswith('gmv_max/') else '111','adgroup_id':'222','ad_id':'333','stat_time_day':'2026-09-01','item_group_id':'1001','item_id':'1002','video_id':'video-1','material_id':'video-1','country_code':'US','age':'25-34','gender':'FEMALE','placement':'PLACEMENT_TIKTOK','platform':'ANDROID'}
                values={k:defaults.get(k,'1') for k in dimensions}
                requested=json.loads(q.get('metrics','[]'))
                metrics={key:defaults.get(key,'1') for key in requested}
                metrics.update(spend='10',cost='10',impressions='1000',clicks='20',conversion='2',gross_revenue='50',roas='5')
                return listing([{'dimensions':values,'metrics':metrics}])
            if path=='changelog/task/create/':return {'code':0,'data':{'task_id':'task1'}}
            if path=='changelog/task/check/':return {'code':0,'data':{'status':'SUCCESS','list':[]}}
            if path=='changelog/task/download/':return {'code':0,'data':{'changelog':{'file_name':'logs.csv','file_data':'Time,Activity details,Object ID\n'}}}
            if path=='file/video/ad/info/':return listing([{'video_id':'video-1','preview_url':'https://ads.tiktok.com/preview/one','video_cover_url':'https://a.tiktokcdn.com/thumb.jpg'}])
            if path=='identity/video/info/':return listing([{'item_id':'1002','video_info':{'video_cover_url':'https://a.tiktokcdn.com/thumb.jpg'}}])
            if path=='store/product/get/':return listing([{'item_group_id':'1001','product_title':'Product','product_image_url':'https://a.tiktokcdn.com/product.jpg'}])
            if path in ('pixel/list/','app/list/','app/info/','identity/get/','file/video/ad/search/','page/get/','creative/portfolio/list/'):return listing([])
            raise AssertionError('unhandled TikTok fixture '+path)
        await self.router(route)

    async def test_tiktok_full_auction_smart_plus_compare_and_creative_retention(self):
        await self.install_tiktok();out=self.root/'tt-auction'
        code,stdout,stderr=await self.run_cli(['report','tiktok','run','--advertiser-id','123','--period','custom','--since','2026-09-01','--until','2026-09-07','--depth','full','--smart-plus','--include-previews','--tiktok-report-mode','auction','--include-gmv-max','never','--page-size','100','--top-objects','10','--retry','1','--retry-wait','0','--run-dir',str(out)])
        self.assertEqual(code,0,stderr+stdout+(out/'manifest.json').read_text())
        manifest=json.loads((out/'manifest.json').read_text());self.assertTrue(manifest['completeness']['complete'])
        sources={x['name'] for x in manifest['sources']}
        self.assertIn('targeted_creative_retention',sources);self.assertIn('previous_ad_insights',sources)
        self.assertTrue(any('/smart_plus/' in req.url.path for req in self.seen))

    async def test_tiktok_full_gmv_max_store_product_creative_and_comparison(self):
        await self.install_tiktok();out=self.root/'tt-gmv'
        code,stdout,stderr=await self.run_cli(['report','tiktok','run','--advertiser-id','123','--period','weekly','--depth','full','--include-previews','--tiktok-report-mode','gmv_max','--include-gmv-max','always','--gmv-max-store-id','901','--gmv-max-promotion-type','PRODUCT_GMV_MAX','--gmv-max-creative-dimensions','official','--page-size','100','--retry','1','--retry-wait','0','--run-dir',str(out)])
        self.assertEqual(code,0,stderr+stdout+(out/'manifest.json').read_text())
        manifest=json.loads((out/'manifest.json').read_text());self.assertTrue(manifest['completeness']['complete'])
        data=''.join(p.read_text() for p in out.glob('*.json'))
        self.assertIn('1001',data);self.assertIn('1002',data);self.assertNotIn(self.tiktok_secret,data)
        self.assertTrue(any('gmv_max/report' in req.url.path for req in self.seen))


class FullMediaMigrationTests(CompletionCase):
    def export_fixture(self):
        self.grant('meta','456');self.state.bind_object('meta','900','456','pixel')
        creatives={
            '441':{'id':'441','name':'Image','image_url':'motata-download:expired-not-a-real-ref',
                   'object_story_spec':{'page_id':'800','link_data':{'link':'https://shop.example.com/one','message':'Image body','name':'Image headline','image_hash':'old-image'}}},
            '442':{'id':'442','name':'Video','thumbnail_url':'motata-download:expired-not-a-real-ref',
                   'object_story_spec':{'page_id':'800','video_data':{'video_id':'666','image_url':'motata-download:expired-not-a-real-ref','message':'Video body','title':'Video headline','call_to_action':{'type':'SHOP_NOW','value':{'link':'https://shop.example.com/two'}}}}},
            '443':{'id':'443','name':'Post','object_story_id':'800_801'},
        }
        tree={'source_account_id':'123','tree':[{'campaign':{'id':'111','name':'Campaign','objective':'OUTCOME_TRAFFIC'},'adsets':[{'adset':{'id':'222','name':'Group','optimization_goal':'LINK_CLICKS','billing_event':'IMPRESSIONS','targeting':{},'promoted_object':{'page_id':'800','pixel_id':'900'}},'ads':[{'ad':{'id':str(331+i),'name':'Ad'+str(i)},'creative':{'id':key}} for i,key in enumerate(creatives)]}]}]}
        folder=self.root/'export';folder.mkdir()
        (folder/'asset-tree.json').write_text(json.dumps(tree));(folder/'creatives.raw.json').write_text(json.dumps(creatives))
        return folder,creatives

    async def install_migration(self,creatives,fail_write=False):
        counters={'adcreatives':1000,'campaigns':2000,'adsets':3000,'ads':4000};self.writes=[]
        def route(req):
            path=req.url.path.removeprefix('/v23.0/')
            if req.method=='GET':
                if path=='act_123/advideos':return {'data':[{'id':'666'}]}
                if path=='666':return {'id':'666','source':'https://a.fbcdn.net/video.mp4?sig=opaque'}
                if path in creatives:
                    if req.url.params.get('fields')=='id,account_id':return {'id':path,'account_id':'123'}
                    value=json.loads(json.dumps(creatives[path]));value['image_url']='https://a.fbcdn.net/'+path+'.jpg'
                    value['thumbnail_url']='https://a.fbcdn.net/LOW_RESOLUTION.jpg'
                    if 'video_data' in value.get('object_story_spec',{}):value['object_story_spec']['video_data']['image_url']=value['image_url']
                    return value
                if path=='act_456/promote_pages':return {'data':[{'id':'800'}]}
                raise AssertionError('unexpected migration read '+path)
            self.writes.append(req)
            if path=='act_456/advideos':
                self.assertIn(b'video-binary-content',req.content)
                return {'id':'777'}
            if path=='act_456/adimages':
                self.assertIn(b'image-binary-content',req.content)
                return {'images':{'file.jpg':{'hash':'new-image-'+str(len(self.writes))}}}
            edge=path.split('/')[-1]
            if fail_write and edge=='ads':raise httpx.ReadTimeout('synthetic connection lost')
            if edge in counters:
                counters[edge]+=1
                return {'id':str(counters[edge])}
            raise AssertionError('unexpected migration mutation '+path)
        await self.router(route)
        self.media_router(lambda req:b'video-binary-content'*1000 if req.url.path.endswith('.mp4') else b'image-binary-content'*1000)

    async def run_migration(self,folder,jobs):
        from motata_cli.meta import commands
        args=['meta','migrate','run','--export-dir',str(folder),'--source-account-id','123','--target-account-id','456','--page-id','800','--pixel-id','900','--job-id','complete-media']
        with patch.object(commands,'JOBS_DIR',jobs):return await self.run_cli(args)

    async def test_full_image_video_post_and_ad_hierarchy_resume_no_duplicates(self):
        folder,creatives=self.export_fixture();await self.install_migration(creatives);jobs=self.root/'jobs'
        code,out,err=await self.run_migration(folder,jobs)
        self.assertEqual(code,0,err+out+(jobs/'complete-media.json').read_text())
        result=json.loads(out)
        self.assertEqual(len(result['old_to_new_ads']),3);self.assertEqual(len(result['old_to_new_creatives']),3)
        self.assertEqual(result['old_to_new_videos'],{'666':'777'})
        self.assertEqual(len(result['uploaded_image_hashes']),2)
        self.assertEqual(len(self.media_seen),3)
        self.assertTrue(all('LOW_RESOLUTION' not in str(req.url) for req in self.media_seen))
        self.assertTrue(all('/act_456/' in req.url.path for req in self.writes))
        write_count=len(self.writes);download_count=len(self.media_seen)
        from motata_cli.meta import commands
        with patch.object(commands,'JOBS_DIR',jobs):
            code,out,err=await self.run_cli(['meta','migrate','resume','--job-id','complete-media'])
        self.assertEqual(code,0,err+out)
        self.assertEqual(len(self.writes),write_count);self.assertEqual(len(self.media_seen),download_count)
        ledger=(jobs/'complete-media.json').read_text();self.assertEqual(json.loads(ledger)['status'],'completed')
        self.assertNotIn(self.meta_secret,ledger)
        for req in self.writes:
            if req.url.path.endswith(('/campaigns','/adsets','/ads')):self.assertEqual(parse_qs(req.content.decode())['status'],['PAUSED'])
        creative_writes=[parse_qs(req.content.decode()) for req in self.writes if req.url.path.endswith('/adcreatives')]
        self.assertIn('800_801',str(creative_writes));self.assertIn('777',str(creative_writes))

    async def test_complete_media_uncertain_ad_write_resume_does_not_resend(self):
        folder,creatives=self.export_fixture();await self.install_migration(creatives,fail_write=True);jobs=self.root/'jobs'
        code,out,err=await self.run_migration(folder,jobs)
        self.assertEqual(code,1,err+out)
        ledger=json.loads((jobs/'complete-media.json').read_text());self.assertEqual(ledger['status'],'needs_review')
        count=len(self.writes);downloads=len(self.media_seen)
        code,out,err=await self.run_migration(folder,jobs)
        self.assertEqual(code,1);self.assertEqual(len(self.writes),count);self.assertEqual(len(self.media_seen),downloads)


    async def test_actual_export_then_full_media_run_and_resume(self):
        original, creatives = self.export_fixture()
        tree = json.loads((original/'asset-tree.json').read_text())['tree'][0]
        campaign = {**tree['campaign'], 'account_id':'123'}
        group = {**tree['adsets'][0]['adset'], 'account_id':'123','campaign_id':'111'}
        ads = [{**row['ad'],'account_id':'123','adset_id':'222','campaign_id':'111',
                'creative':row['creative']} for row in tree['adsets'][0]['ads']]
        def exporter(req):
            path = req.url.path.removeprefix('/v23.0/')
            if path == '111': return campaign
            if path == '111/adsets': return {'data':[group]}
            if path == '222/ads': return {'data':ads}
            if path in creatives:
                value = json.loads(json.dumps(creatives[path]))
                value['image_url'] = 'https://a.fbcdn.net/' + path + '.jpg'
                value['thumbnail_url'] = 'https://a.fbcdn.net/LOW_RESOLUTION.jpg'
                return value
            raise AssertionError('Unexpected export path '+path)
        await self.router(exporter)
        destination = self.root/'real-cli-export'
        code, out, err = await self.run_cli(['meta','migrate','export','--source-account-id','123',
            '--campaign-id','111','--export-dir',str(destination)])
        self.assertEqual(code, 0, err+out)
        manifest = json.loads((destination/'export-manifest.json').read_text())
        self.assertEqual(manifest['counts'], {'campaigns':1,'adsets':1,'ads':3,'creatives':3})
        text = (destination/'creatives.raw.json').read_text()
        self.assertIn('motata-download:', text)
        self.assertNotIn(self.meta_secret, text)
        # Expire every exported URL before using the bundle; migration refetches
        # authorized media rather than sending a stale reference or direct URL.
        self.service.downloads.entries.clear()
        await self.install_migration(creatives)
        jobs = self.root/'export-run-jobs'
        code, out, err = await self.run_migration(destination,jobs)
        self.assertEqual(code,0,err+out)
        count = len(self.writes)
        code, out, err = await self.run_migration(destination,jobs)
        self.assertEqual(code,0,err+out)
        self.assertEqual(len(self.writes),count)


class TikTokMediaCopyTests(CompletionCase):
    async def test_actual_cli_copy_materializes_cover_and_preserves_disabled_status(self):
        campaign={'campaign_id':'111','campaign_name':'Source','objective_type':'TRAFFIC',
                  'operation_status':'ENABLE','budget_mode':'BUDGET_MODE_DAY','budget':100}
        group={'adgroup_id':'222','campaign_id':'111','adgroup_name':'Source group',
               'promotion_type':'WEBSITE','placement_type':'PLACEMENT_TYPE_AUTOMATIC',
               'budget_mode':'BUDGET_MODE_DAY','budget':30,'optimization_goal':'CLICK',
               'billing_event':'CPC','schedule_type':'SCHEDULE_FROM_NOW','operation_status':'ENABLE'}
        ad={'ad_id':'333','adgroup_id':'222','campaign_id':'111','ad_name':'Source ad',
            'ad_format':'SINGLE_VIDEO','video_id':'video-opaque','identity_type':'CUSTOMIZED_USER',
            'identity_id':'identity-opaque','ad_text':'Text','landing_page_url':'https://shop.example.com/one',
            'call_to_action':'SHOP_NOW','operation_status':'ENABLE'}
        writes=[]
        def listing(rows):return {'code':0,'data':{'list':rows,'page_info':{'page':1,'total_page':1}}}
        def route(req):
            path=req.url.path.removeprefix('/open_api/v1.3/')
            if req.method=='GET':
                if path=='campaign/get/':return listing([campaign])
                if path=='adgroup/get/':return listing([group])
                if path=='ad/get/':return listing([ad])
                if path=='file/video/ad/info/':return listing([{'video_id':'video-opaque','video_cover_url':'https://a.tiktokcdn.com/cover.jpg'}])
                raise AssertionError('Unexpected copy read '+path)
            writes.append(req)
            if path=='file/image/ad/upload/':
                self.assertIn(b'cover-binary-content',req.content)
                self.assertIn(b'UPLOAD_BY_FILE',req.content)
                self.assertNotIn(b'motata-download:',req.content)
                return {'code':0,'data':{'image_id':'new-cover-image'}}
            if path=='campaign/create/':return {'code':0,'data':{'campaign_id':'700'}}
            if path=='adgroup/create/':return {'code':0,'data':{'adgroup_id':'701'}}
            if path=='ad/create/':return {'code':0,'data':{'ad_ids':['702']}}
            raise AssertionError('Unexpected copy write '+path)
        await self.router(route)
        self.media_router(lambda req:b'cover-binary-content'*100)
        code,out,err=await self.run_cli(['tiktok','campaigns','copy','111','--advertiser-id','123','--name','Copy'])
        self.assertEqual(code,0,err+out)
        result=json.loads(out)
        self.assertEqual(result['campaigns'][0]['ads_created'],1)
        self.assertEqual(result['campaigns'][0]['adgroups_created'],1)
        self.assertEqual(len(self.media_seen),1)
        for req in writes:
            if req.url.path.endswith(('/campaign/create/','/adgroup/create/')):
                self.assertEqual(json.loads(req.content)['operation_status'],'DISABLE')
            if req.url.path.endswith('/ad/create/'):
                payload=json.loads(req.content)
                self.assertEqual(payload['creatives'][0]['operation_status'],'DISABLE')
                self.assertEqual(payload['creatives'][0]['image_ids'],['new-cover-image'])
        self.assertNotIn(self.tiktok_secret,out+err)
