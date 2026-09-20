"""Actual CLI report and migration workflows, with only upstream HTTP mocked.

These are explicit fixture workflows, not a claim of all combinations supported.
"""
from __future__ import annotations
import json
import os
import unittest
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import patch
import httpx
import test_gateway as base


class WorkflowTests(base.CLIIntegrationTests):
    async def install_router(self, router):
        await self.http.aclose()
        async def handler(req):
            self.seen.append(req)
            return httpx.Response(200, json=router(req))
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
        self.service.http = self.http

    async def test_meta_fast_report_collects_and_writes_complete_manifest(self):
        def router(req):
            path = req.url.path.removeprefix('/v23.0/')
            if path == 'act_123':
                return {'id': 'act_123', 'account_id': '123', 'name': 'Test', 'currency': 'USD', 'timezone_name': 'UTC', 'account_status': 1}
            if path.endswith('/insights') and req.method == 'POST':
                return {'report_run_id': '701'}
            if path == '701':
                return {'id': '701', 'async_status': 'Job Completed', 'async_percent_completion': 100}
            return {'data': []}
        await self.install_router(router)
        output = self.root / 'meta-report'
        code, stdout, stderr = await self.run_cli(['report', 'meta', 'run', '--account-id', '123', '--period', 'daily',
            '--depth', 'fast', '--no-compare', '--no-previews', '--retry', '1', '--retry-wait', '0', '--run-dir', str(output)],
            extra_env={'MOTATA_HOME': str(self.root / 'home')})
        self.assertEqual(code, 0, stderr + stdout)
        manifest = json.loads((output / 'manifest.json').read_text())
        self.assertTrue(manifest['completeness']['complete'], manifest)
        self.assertGreater(len(self.seen), 1)
        self.assertNotIn(self.meta_secret, ''.join(p.read_text() for p in output.glob('*.json')))

    async def test_tiktok_fast_auction_report_uses_sdk_account_info(self):
        def router(req):
            if req.url.path.endswith('/advertiser/info/'):
                return {'code': 0, 'data': {'list': [{'advertiser_id': '123', 'name': 'Test', 'currency': 'USD', 'timezone': 'UTC'}]}}
            return {'code': 0, 'data': {'list': [], 'page_info': {'page': 1, 'total_page': 1, 'total_number': 0}}}
        await self.install_router(router)
        output = self.root / 'tiktok-report'
        code, stdout, stderr = await self.run_cli(['report', 'tiktok', 'run', '--advertiser-id', '123', '--period', 'daily',
            '--depth', 'fast', '--no-compare', '--no-previews', '--retry', '1', '--retry-wait', '0',
            '--tiktok-report-mode', 'auction', '--include-gmv-max', 'never', '--run-dir', str(output)],
            extra_env={'MOTATA_HOME': str(self.root / 'home')})
        self.assertEqual(code, 0, stderr + stdout)
        manifest = json.loads((output / 'manifest.json').read_text())
        self.assertTrue(manifest['completeness']['complete'], manifest)
        self.assertTrue(any(req.url.path.endswith('/advertiser/info/') for req in self.seen))
        self.assertNotIn(self.tiktok_secret, ''.join(p.read_text() for p in output.glob('*.json')))

    async def test_object_id_lookup_is_minimal_and_account_bound(self):
        def router(req):
            if req.url.params.get('fields') == 'id,account_id':
                return {'id': '456', 'account_id': '123'}
            return {'id': '456', 'name': 'Campaign'}
        await self.install_router(router)
        code, out, err = await self.run_cli(['meta', 'campaigns', 'get', '456'])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.seen), 2)
        self.assertEqual(self.seen[0].url.params['fields'], 'id,account_id')
        self.assertEqual(json.loads(out)['id'], '456')

    async def test_object_probe_foreign_data_never_returned(self):
        await self.install_router(lambda req: {'id': '456', 'account_id': '999', 'name': 'DO_NOT_RETURN'})
        code, out, err = await self.run_cli(['meta', 'campaigns', 'get', '456'])
        self.assertEqual(code, 1)
        self.assertEqual(len(self.seen), 1)
        self.assertNotIn('DO_NOT_RETURN', out + err)

    async def test_accounts_list_exposes_grants_not_token_inventory(self):
        await self.install_router(lambda req: {'id':'act_123','account_id':'123','name':'Test','currency':'USD'})
        code, out, err = await self.run_cli(['meta', 'accounts', 'list'])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), [{'id': 'act_123', 'account_id': '123', 'name':'Test','currency':'USD'}])
        self.assertEqual(len(self.seen), 1)
        self.assertTrue(self.seen[0].url.path.endswith('/act_123'))
        self.assertNotIn(self.meta_secret,out+err)

    def migration_fixture(self):
        export = self.root / 'export'; export.mkdir()
        tree = {'source_account_id': '123', 'tree': [{'campaign': {'id': '111', 'name': 'Campaign', 'objective': 'OUTCOME_TRAFFIC'},
            'adsets': [{'adset': {'id': '222', 'name': 'Adset', 'optimization_goal': 'LINK_CLICKS',
                                 'billing_event': 'IMPRESSIONS', 'targeting': {}}, 'ads': []}]}]}
        (export / 'asset-tree.json').write_text(json.dumps(tree))
        (export / 'creatives.raw.json').write_text('{}')
        self.state.grant(subject='agent-a', client='motata-cli', workspace='single-user', platform='meta', account='456', reference='meta-main')
        self.state.bind_object('meta', '101', '456', 'pixel')
        return ['meta', 'migrate', 'run', '--export-dir', str(export), '--source-account-id', '123',
                '--target-account-id', '456', '--page-id', '789', '--pixel-id', '101', '--job-id', 'rebuild-job']

    async def test_migration_creates_paused_objects_and_resume_skips_writes(self):
        argv = self.migration_fixture()
        def router(req):
            if req.url.path.endswith('/campaigns'):
                return {'id': '900'}
            if req.url.path.endswith('/adsets'):
                return {'id': '901'}
            raise AssertionError('Unexpected platform call: ' + req.url.path)
        await self.install_router(router)
        from motata_cli.meta import commands
        jobs = self.root / 'jobs'
        with patch.object(commands, 'JOBS_DIR', jobs):
            for _ in range(2):
                code, out, err = await self.run_cli(argv)
                self.assertEqual(code, 0, err + out)
            code, out, err = await self.run_cli(['meta', 'migrate', 'resume', '--job-id', 'rebuild-job'])
            self.assertEqual(code, 0, err + out)
        self.assertEqual(len(self.seen), 2)
        for req in self.seen:
            self.assertEqual(parse_qs(req.content.decode())['status'], ['PAUSED'])
        ledger = (jobs / 'rebuild-job.json').read_text()
        self.assertEqual(json.loads(ledger)['status'], 'completed')
        self.assertNotIn(self.meta_secret, ledger)

    async def test_migration_unknown_write_remains_quarantined(self):
        argv = self.migration_fixture()
        def router(req):
            if req.url.path.endswith('/campaigns'):
                return {'id': '900'}
            raise httpx.ReadTimeout('upstream timeout ' + self.meta_secret)
        await self.install_router(router)
        from motata_cli.meta import commands
        jobs = self.root / 'jobs'
        with patch.object(commands, 'JOBS_DIR', jobs):
            for _ in range(2):
                code, out, err = await self.run_cli(argv)
                self.assertEqual(code, 1)
                self.assertNotIn(self.meta_secret, out + err)
        self.assertEqual(len(self.seen), 2)
        self.assertEqual(json.loads((jobs / 'rebuild-job.json').read_text())['status'], 'needs_review')

for cls in (base.GatewayTests, base.CLIIntegrationTests):
    for name in cls.__dict__:
        if name.startswith('test_') and name not in WorkflowTests.__dict__:
            setattr(WorkflowTests, name, None)

# unittest discovers class-valued globals, including loop variables.
del cls
