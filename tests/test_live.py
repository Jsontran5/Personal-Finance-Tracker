from datetime import date
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from finance.api import create_app
from finance.db import connect
from finance.live_setup import live_router, production_credentials
from finance.plaid_sandbox import PlaidError, plaid_client


class LiveTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.database = Path(self.folder.name) / 'data/finance.db'
        self.path = self.database.with_suffix('.plaid-live.db')
        self.calls = []
        self.fail = None
        self.url = 'https://secure.plaid.com/hl/synthetic'
        self.selected = 'ins_bofa'
        self.env = patch.dict(os.environ, {'PLAID_CLIENT_ID':'synthetic-client', 'PLAID_SECRET':'synthetic-sandbox-secret',
            'PLAID_PRODUCTION_SECRET':'synthetic-production-secret', 'PLAID_LIVE_ENABLED':'false', 'PLAID_ENV':'sandbox',
            'PLAID_PRODUCTION_REDIRECT_URI':''})
        self.env.start()
        def provider(endpoint, **payload):
            self.calls.append((endpoint,payload))
            if self.fail: raise PlaidError(self.fail)
            if endpoint == '/institutions/get_by_id':
                return {'institution':{'institution_id':payload['institution_id'],'name':'Bank of America','products':['transactions','liabilities'],'oauth':True}}
            if endpoint == '/institutions/search':
                return {'institutions':[{'institution_id':'ins_bofa','name':'Bank of America'}]}
            if endpoint == '/link/token/create':
                return {'link_token':'link-production-synthetic','hosted_link_url':self.url,'expiration':'2099-01-01T00:00:00+00:00'}
            if endpoint == '/link/token/get':
                return {'link_sessions':[{'finished_at':'2026-11-15T00:00:00Z','results':{'item_add_results':[{'institution':{'institution_id':self.selected},'public_token':'public-production-synthetic'}]},'events':[{'event_name':'HANDOFF'}]}]}
            if endpoint == '/item/public_token/exchange':
                return {'item_id':'synthetic-production-item','access_token':'access-production-synthetic'}
            if endpoint == '/transactions/sync':
                return {'added':[],'modified':[],'removed':[],'has_more':False,'next_cursor':'live-cursor'}
            if endpoint == '/accounts/get': return {'accounts':[]}
            raise AssertionError(endpoint)
        def factory(keys, *, environment):
            self.assertEqual(environment,'production')
            self.assertEqual(keys[1],'synthetic-production-secret')
            return provider
        self.router = patch('finance.api.live_router',lambda database:live_router(database,client_factory=factory))
        self.router.start()
        self.network = patch('finance.plaid_sandbox.urlopen',side_effect=AssertionError('Unexpected external request'))
        self.network.start()
        self.client=TestClient(create_app(self.database),base_url='http://127.0.0.1',headers={'x-finance-request':'1'})
        self.client.__enter__()
        self.base='/api/plaid/live'

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.network.stop();self.router.stop();self.env.stop();self.folder.cleanup()

    def prepare(self):
        self.client.put(self.base+'/readiness',json={'trial_confirmed':True,'item_limit':20,'transactions_enabled':True,'oauth_confirmed':True,'hosted_link_confirmed':True}).raise_for_status()
        self.client.post(self.base+'/preflight',json={'institution_id':'ins_bofa'}).raise_for_status()
        result=self.client.post(self.base+'/connections',json={'institution_id':'ins_bofa'})
        self.assertEqual(result.status_code,201,result.text)
        return result.json()['id']

    def test_readiness_is_local_and_live_reports_start_empty(self):
        state=self.client.get(self.base+'/readiness').json()
        self.assertTrue(state['configured']);self.assertFalse(state['linking_enabled']);self.assertFalse(state['ready'])
        self.assertEqual(state['connected_items'],0)
        data=self.client.get(self.base+'/preview?month='+date.today().isoformat()[:7]).json()
        self.assertEqual(data['accounts'],[])
        self.assertEqual(self.client.post(self.base+'/import-scenarios').status_code,403)
        self.assertEqual(self.calls,[])
        self.assertNotIn('synthetic-production-secret',json.dumps(state))

    def test_lock_cannot_be_bypassed_by_direct_routes(self):
        identifier=self.prepare()
        self.assertTrue(self.client.get(self.base+'/readiness').json()['ready'])
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/link').status_code,403)
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/link/{uuid4()}').status_code,403)
        self.assertFalse(any('/link/' in name or name.endswith('/exchange') for name,_ in self.calls))

    def test_legacy_hosted_link_checkbox_is_no_longer_a_requirement(self):
        self.client.get(self.base+'/readiness')
        with connect(self.path,write=True) as db:
            db.execute('INSERT INTO live_setup VALUES (1,?)', (json.dumps({'trial_confirmed':True,'item_limit':20,
                'transactions_enabled':True,'oauth_confirmed':True,'hosted_link_confirmed':False}),))
        state=self.client.get(self.base+'/readiness').json()
        self.assertTrue(state['ready'])
        self.assertNotIn('hosted_link_confirmed',state['setup'])
        self.assertFalse(any('Hosted Link' in check['label'] or 'redirect' in check['label'] for check in state['checks']))
        identifier=self.client.post(self.base+'/connections',json={'institution_id':'ins_bofa'}).json()['id']
        self.client.post(self.base+'/preflight',json={'institution_id':'ins_bofa'}).raise_for_status()
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/link').status_code,403)
        os.environ['PLAID_LIVE_ENABLED']='true'
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/link').status_code,200)
        self.assertFalse(any(name.endswith('/exchange') for name,_ in self.calls))

    def test_enabled_pilot_uses_minimum_products_and_reconnects_same_item(self):
        identifier=self.prepare();os.environ['PLAID_LIVE_ENABLED']='true'
        started=self.client.post(f'{self.base}/connections/{identifier}/link')
        self.assertEqual(started.status_code,200,started.text)
        options=next(payload for name,payload in self.calls if name=='/link/token/create')
        self.assertEqual(options['products'],['transactions']);self.assertNotIn('additional_consented_products',options)
        result=self.client.post(f'{self.base}/connections/{identifier}/link/'+started.json()['session'])
        self.assertEqual(result.status_code,200,result.text)
        with connect(self.path) as db:
            before=tuple(db.execute('SELECT item_id,token FROM multi_connections').fetchone())
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/sync?product=transactions').status_code,200)
        reconnect=self.client.post(f'{self.base}/connections/{identifier}/link').json()
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/link/'+reconnect['session']).status_code,200)
        with connect(self.path) as db:
            self.assertEqual(tuple(db.execute('SELECT item_id,token FROM multi_connections').fetchone()),before)
            self.assertEqual(db.execute('SELECT cursor FROM multi_connections').fetchone()[0],'live-cursor')
        self.assertEqual(sum(name.endswith('/exchange') for name,_ in self.calls),1)

    def test_missing_confirmations_coverage_and_rotated_credentials_block_link(self):
        self.client.get(self.base+'/readiness')
        identifier=self.client.post(self.base+'/connections',json={'institution_id':'ins_bofa'}).json()['id']
        os.environ['PLAID_LIVE_ENABLED']='true'
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/link').status_code,409)
        self.client.put(self.base+'/readiness',json={'trial_confirmed':True,'item_limit':20,'transactions_enabled':True,'oauth_confirmed':True,'hosted_link_confirmed':True})
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/link').status_code,409)
        self.client.post(self.base+'/preflight',json={'institution_id':'ins_bofa'})
        os.environ['PLAID_PRODUCTION_SECRET']='rotated-production-secret'
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/link').status_code,409)
        self.assertFalse(any(name=='/link/token/create' for name,_ in self.calls))

    def test_failed_preflight_clears_previous_success(self):
        self.prepare();self.fail='INVALID_API_KEYS'
        self.assertEqual(self.client.post(self.base+'/preflight',json={'institution_id':'ins_bofa'}).status_code,409)
        self.assertEqual(self.client.get(self.base+'/readiness').json()['preflights'],[])

    def test_production_secret_never_falls_back_to_sandbox(self):
        os.environ['PLAID_PRODUCTION_SECRET']=''
        self.assertIsNone(production_credentials(self.database.parent.parent))
        self.assertFalse(self.client.get(self.base+'/readiness').json()['configured'])
        self.assertEqual(self.client.get(self.base+'/institutions?query=Bank').status_code,503)
        self.assertEqual(self.calls,[])

    def test_unexpected_hosted_url_is_not_saved(self):
        identifier=self.prepare();os.environ['PLAID_LIVE_ENABLED']='true';self.url='https://attacker.example/link'
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/link').status_code,502)
        with connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM multi_sessions').fetchone()[0],0)

    def test_wrong_institution_is_rejected_before_exchange(self):
        identifier=self.prepare();os.environ['PLAID_LIVE_ENABLED']='true';self.selected='ins_other'
        started=self.client.post(f'{self.base}/connections/{identifier}/link').json()
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/link/'+started['session']).status_code,409)
        self.assertFalse(any(name.endswith('/exchange') for name,_ in self.calls))

    def test_backup_and_restore_keep_live_separate_and_link_gate_off(self):
        self.prepare()
        saved=self.client.post('/api/backups',json={'workspace':'live'})
        self.assertEqual(saved.status_code,201,saved.text)
        checked=self.client.get('/api/backups/preview',params={'id':saved.json()['id']})
        self.assertEqual(checked.status_code,200,checked.text)
        self.assertEqual(checked.json()['workspace'],'live')
        preview=checked.json()
        result=self.client.post('/api/backups/restore',json={key:preview[key] for key in ['id','preview_token','confirmation']})
        self.assertEqual(result.status_code,200,result.text)
        self.assertFalse(self.client.get(self.base+'/readiness').json()['linking_enabled'])
        self.assertEqual(self.client.get('/api/plaid/combined-sandbox').json()['connections'],[])
        self.assertEqual(self.client.put('/api/workspace',json={'mode':'live'}).status_code,200)
        self.assertEqual(self.client.get('/api/workspace').json()['mode'],'live')

    def test_pilot_blocks_unapproved_products_and_unsafe_redirect(self):
        identifier=self.prepare();os.environ['PLAID_LIVE_ENABLED']='true'
        os.environ['PLAID_PRODUCTION_REDIRECT_URI']='http://localhost/oauth'
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/link').status_code,409)
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/sync?product=holdings').status_code,409)
        self.assertEqual(self.client.post(self.base+'/connections',json={'institution_id':'ins_invest','profile':'investments'}).status_code,409)

    def test_pending_links_reserve_the_local_allowance(self):
        identifier=self.prepare();os.environ['PLAID_LIVE_ENABLED']='true'
        state=self.client.get(self.base+'/readiness').json()['setup'];state['item_limit']=1
        self.client.put(self.base+'/readiness',json=state)
        second=self.client.post(self.base+'/connections',json={'institution_id':'ins_other'}).json()['id']
        self.client.post(self.base+'/preflight',json={'institution_id':'ins_other'})
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/link').status_code,200)
        self.assertEqual(self.client.post(f'{self.base}/connections/{second}/link').status_code,409)

    def test_live_authorization_survives_restore_without_touching_sandbox(self):
        identifier=self.prepare();os.environ['PLAID_LIVE_ENABLED']='true'
        started=self.client.post(f'{self.base}/connections/{identifier}/link').json()
        self.client.post(f'{self.base}/connections/{identifier}/link/'+started['session']).raise_for_status()
        self.client.post(f'{self.base}/connections/{identifier}/sync?product=transactions').raise_for_status()
        saved=self.client.post('/api/backups',json={'workspace':'live'}).json()
        with connect(self.path,write=True) as db:
            db.execute("UPDATE multi_connections SET cursor='later-cursor'")
            original=tuple(db.execute('SELECT item_id,token FROM multi_connections').fetchone())
        checked=self.client.get('/api/backups/preview',params={'id':saved['id']}).json()
        self.assertEqual(self.client.post('/api/backups/restore',json={key:checked[key] for key in ['id','preview_token','confirmation']}).status_code,200)
        with connect(self.path) as db:
            self.assertEqual(tuple(db.execute('SELECT item_id,token FROM multi_connections').fetchone()),original)
            self.assertEqual(db.execute('SELECT cursor FROM multi_connections').fetchone()[0],'live-cursor')
        self.assertEqual(sum(name.endswith('/exchange') for name,_ in self.calls),1)

    def test_backup_rejects_invalid_coverage_metadata(self):
        self.prepare()
        with connect(self.path,write=True) as db:
            db.execute("UPDATE live_preflight SET payload='{}'")
        result=self.client.post('/api/backups',json={'workspace':'live'})
        self.assertEqual(result.status_code,422,result.text)


class ProductionTransportTests(TestCase):
    def test_fixed_production_host_and_no_money_movement_or_sandbox_tokens(self):
        def response(request,**kwargs):
            self.assertEqual(request.full_url,'https://production.plaid.com/institutions/search')
            self.assertEqual(json.loads(request.data)['secret'],'production-secret')
            return io.BytesIO(b'{"institutions":[],"request_id":"synthetic"}')
        call=plaid_client(('client','production-secret'),environment='production')
        with patch('finance.plaid_sandbox.urlopen',side_effect=response) as network:
            call('/institutions/search',query='Bank',country_codes=['US'])
            for endpoint,payload in [('/transfer/create',{}),('/item/remove',{}),('/accounts/get',{'access_token':'access-sandbox-secret'})]:
                with self.assertRaises(PlaidError):call(endpoint,**payload)
            self.assertEqual(network.call_count,1)
