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
        self.products = ['transactions', 'liabilities']
        self.accounts = []
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
                return {'institution':{'institution_id':payload['institution_id'],'name':'Bank of America','products':self.products,'oauth':True}}
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
            if endpoint in {'/accounts/get','/accounts/balance/get'}: return {'accounts':self.accounts}
            if endpoint == '/investments/holdings/get':
                return {'accounts':[a for a in self.accounts if a['type']=='investment'],
                    'holdings':[{'account_id':'portfolio','security_id':'voo','quantity':10,'institution_price':50,'institution_value':500,'cost_basis':400,'iso_currency_code':'USD'},
                                {'account_id':'portfolio','security_id':'cash','quantity':500,'institution_value':500,'iso_currency_code':'USD'}],
                    'securities':[{'security_id':'voo','name':'VOO','type':'etf','ticker_symbol':'VOO'}, {'security_id':'cash','name':'Brokerage cash','type':'cash'}]}
            if endpoint == '/investments/transactions/get':
                return {'total_investment_transactions':1,'securities':[], 'investment_transactions':[
                    {'investment_transaction_id':'buy1','account_id':'portfolio','date':date.today().isoformat(),'name':'Buy VOO','type':'buy','amount':500,'iso_currency_code':'USD'}]}

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


    def prepare_investment(self, mixed=False):
        self.products = ['investments'] + (['transactions'] if mixed else [])
        state={'trial_confirmed':True,'item_limit':20,'transactions_enabled':True,'oauth_confirmed':True,'investments_enabled':True}
        self.client.put(self.base+'/readiness',json=state).raise_for_status()
        profile='banking' if mixed else 'investments'
        self.client.post(self.base+'/preflight',json={'institution_id':'ins_bofa','profile':profile}).raise_for_status()
        result=self.client.post(self.base+'/connections',json={'institution_id':'ins_bofa','profile':profile,'include_investments':mixed})
        result.raise_for_status()
        self.accounts=[{'account_id':'portfolio','name':'Brokerage','type':'investment','subtype':'brokerage','balances':{'current':1000,'iso_currency_code':'USD'}}]
        if mixed:
            self.accounts.append({'account_id':'checking','name':'Checking','type':'depository','subtype':'checking','balances':{'current':100,'iso_currency_code':'USD'}})
        os.environ['PLAID_LIVE_ENABLED']='true'
        return result.json()['id']

    def finish(self, identifier):
        result=self.client.post(f'{self.base}/connections/{identifier}/link');result.raise_for_status()
        self.client.post(f"{self.base}/connections/{identifier}/link/{result.json()['session']}").raise_for_status()

    def test_live_investment_only_consent_holdings_activity_and_no_double_count(self):
        identifier=self.prepare_investment()
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/sync?product=holdings').status_code,409)
        self.finish(identifier)
        options=next(p for e,p in self.calls if e=='/link/token/create')
        self.assertEqual(options['products'],['investments'])
        self.assertNotIn('additional_consented_products',options)
        for product in ['holdings','activity']:
            r=self.client.post(f'{self.base}/connections/{identifier}/sync?product={product}')
            self.assertEqual(r.status_code,200,r.text)
        portfolio=self.client.get(self.base+'/investments').json()
        self.assertEqual(len(portfolio['holdings']),2)
        self.assertEqual(portfolio['accounts'][0]['current_minor'],100000)
        activity=self.client.get(self.base+'/investments/activity').json()
        self.assertEqual(activity['total'],1)
        report=self.client.get(self.base+'/preview?month='+date.today().isoformat()[:7]).json()
        self.assertEqual(report['net_worth_minor'],100000)
        self.assertEqual(report['spending_minor'],0)
        self.assertEqual(report['income_minor'],0)
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/sync?product=transactions').status_code,409)
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/sync?product=liabilities').status_code,409)
        self.fail='PRODUCT_NOT_READY'
        with connect(self.path,write=True) as db: db.execute('UPDATE multi_connections SET holdings_at=NULL')
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/sync?product=holdings').status_code,409)
        self.assertEqual(len(self.client.get(self.base+'/investments').json()['holdings']),2)

    def test_mixed_connection_reuses_item_and_preserves_portfolio_and_banking(self):
        identifier=self.prepare_investment(mixed=True)
        self.finish(identifier)
        options=next(p for e,p in self.calls if e=='/link/token/create')
        self.assertEqual(options['products'],['transactions','investments'])
        for product in ['transactions','holdings','activity']:
            r=self.client.post(f'{self.base}/connections/{identifier}/sync?product={product}')
            self.assertEqual(r.status_code,200,r.text)
        report=self.client.get(self.base+'/preview?month='+date.today().isoformat()[:7]).json()
        self.assertEqual(len(report['accounts']),2)
        self.assertEqual(report['net_worth_minor'],110000)
        saved=self.client.post('/api/backups',json={'workspace':'live'});saved.raise_for_status()
        self.finish(identifier)
        self.assertEqual(sum(e.endswith('/exchange') for e,p in self.calls),1)
        options=[p for e,p in self.calls if e=='/link/token/create'][-1]
        self.assertIn('access_token',options)
        self.assertNotIn('products',options)
        preview=self.client.get('/api/backups/preview',params={'id':saved.json()['id']}).json()
        restored=self.client.post('/api/backups/restore',json={k:preview[k] for k in ['id','preview_token','confirmation']})
        self.assertEqual(restored.status_code,200,restored.text)
        self.assertEqual(len(self.client.get(self.base+'/investments').json()['holdings']),2)
        self.assertTrue(self.client.get(self.base).json()['connections'][0]['investments_enabled'])

    def test_existing_banking_investment_upgrade_requires_explicit_consent(self):
        identifier=self.prepare();os.environ['PLAID_LIVE_ENABLED']='true'
        self.finish(identifier)
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/enable-investments').status_code,409)
        self.products.append('investments')
        state=self.client.get(self.base+'/readiness').json()['setup'];state['investments_enabled']=True
        self.client.put(self.base+'/readiness',json=state).raise_for_status()
        # Cached banking-only coverage cannot authorize the investment upgrade.
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/enable-investments').status_code,409)
        self.client.post(self.base+'/preflight',json={'institution_id':'ins_bofa'}).raise_for_status()
        self.client.post(f'{self.base}/connections/{identifier}/enable-investments').raise_for_status()
        self.assertEqual(self.client.post(f'{self.base}/connections/{identifier}/sync?product=holdings').status_code,409)
        self.finish(identifier)
        options=[p for e,p in self.calls if e=='/link/token/create'][-1]
        self.assertEqual(options['additional_consented_products'],['investments'])
        self.assertNotIn('products',options)
        self.assertEqual(sum(e.endswith('/exchange') for e,p in self.calls),1)
        self.assertFalse(self.client.get(self.base).json()['connections'][0]['investment_consent_pending'])



    def test_older_live_schema_backup_migrates_copy_without_changing_source(self):
        self.prepare()
        with connect(self.path,write=True) as db:
            db.execute('DROP TABLE live_investment_connections')
            db.execute('PRAGMA user_version=9')
        result=self.client.post('/api/backups',json={'workspace':'live'})
        self.assertEqual(result.status_code,201,result.text)
        with connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_schema WHERE name='live_investment_connections'").fetchone())
        preview=self.client.get('/api/backups/preview',params={'id':result.json()['id']})
        self.assertEqual(preview.status_code,200,preview.text)

    def test_restore_older_banking_backup_keeps_current_investment_authorization(self):
        identifier=self.prepare();os.environ['PLAID_LIVE_ENABLED']='true';self.finish(identifier)
        saved=self.client.post('/api/backups',json={'workspace':'live'}).json()
        self.products.append('investments')
        state=self.client.get(self.base+'/readiness').json()['setup'];state['investments_enabled']=True
        self.client.put(self.base+'/readiness',json=state).raise_for_status()
        self.client.post(self.base+'/preflight',json={'institution_id':'ins_bofa'}).raise_for_status()
        self.client.post(f'{self.base}/connections/{identifier}/enable-investments').raise_for_status()
        self.finish(identifier)
        preview=self.client.get('/api/backups/preview',params={'id':saved['id']}).json()
        restored=self.client.post('/api/backups/restore',json={k:preview[k] for k in ['id','preview_token','confirmation']})
        self.assertEqual(restored.status_code,200,restored.text)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT consented FROM live_investment_connections WHERE connection_id=?',(identifier,)).fetchone()[0],1)
        self.assertEqual(sum(e.endswith('/exchange') for e,p in self.calls),1)



    def test_account_nickname_survives_sync_and_restore_without_financial_changes(self):
        identifier=self.prepare_investment(mixed=True);self.finish(identifier)
        for product in ['transactions','holdings','activity']:
            self.client.post(f'{self.base}/connections/{identifier}/sync?product={product}').raise_for_status()
        month=date.today().isoformat()[:7]
        report=self.client.get(self.base+'/preview?month='+month).json()
        account=next(a for a in report['accounts'] if a['provider_type']=='investment')
        with connect(self.path) as db:
            identity=tuple(db.execute('SELECT item_id,token,cursor FROM multi_connections').fetchone())
        endpoint=f"{self.base}/preview/accounts/{account['id']}/name"
        self.client.put(endpoint,json={'nickname':'  My long-term portfolio  '}).raise_for_status()
        renamed=self.client.get(self.base+'/preview?month='+month).json()
        current=next(a for a in renamed['accounts'] if a['id']==account['id'])
        self.assertEqual(current['name'],'My long-term portfolio');self.assertEqual(current['provider_name'],'Brokerage')
        self.assertEqual(renamed['net_worth_minor'],report['net_worth_minor'])
        self.assertEqual(renamed['spending_minor'],report['spending_minor'])
        self.assertIn('My long-term portfolio',self.client.get(self.base+'/investments').json()['accounts'][0]['name'])
        self.assertIn('My long-term portfolio',self.client.get(self.base+'/investments/activity').json()['rows'][0]['account_name'])
        saved=self.client.post('/api/backups',json={'workspace':'live'});saved.raise_for_status()
        self.accounts[0]['name']='Brokerage renamed by bank'
        with connect(self.path,write=True) as db: db.execute('UPDATE multi_connections SET synced_at=NULL,holdings_at=NULL')
        for product in ['transactions','holdings']:
            self.client.post(f'{self.base}/connections/{identifier}/sync?product={product}').raise_for_status()
        current=next(a for a in self.client.get(self.base+'/preview?month='+month).json()['accounts'] if a['id']==account['id'])
        self.assertEqual(current['name'],'My long-term portfolio');self.assertEqual(current['provider_name'],'Brokerage renamed by bank')
        self.client.put(endpoint,json={'nickname':None}).raise_for_status()
        current=next(a for a in self.client.get(self.base+'/preview?month='+month).json()['accounts'] if a['id']==account['id'])
        self.assertEqual(current['name'],'Brokerage renamed by bank');self.assertIsNone(current['nickname'])
        preview=self.client.get('/api/backups/preview',params={'id':saved.json()['id']}).json()
        restored=self.client.post('/api/backups/restore',json={k:preview[k] for k in ['id','preview_token','confirmation']});restored.raise_for_status()
        current=next(a for a in self.client.get(self.base+'/preview?month='+month).json()['accounts'] if a['id']==account['id'])
        self.assertEqual(current['name'],'My long-term portfolio')
        with connect(self.path) as db:
            self.assertEqual(tuple(db.execute('SELECT item_id,token,cursor FROM multi_connections').fetchone()),identity)
        self.assertEqual(sum(e.endswith('/exchange') for e,p in self.calls),1)

    def test_account_nickname_validation_and_manual_mirror_guard(self):
        identifier=self.prepare_investment();self.finish(identifier)
        self.client.post(f'{self.base}/connections/{identifier}/sync?product=holdings').raise_for_status()
        a=self.client.get(self.base+'/preview?month='+date.today().isoformat()[:7]).json()['accounts'][0]
        endpoint=f"{self.base}/preview/accounts/{a['id']}/name"
        for value in ['', ' '*3, 'x'*121, 'name\nline', 'name\u0000']:
            self.assertEqual(self.client.put(endpoint,json={'nickname':value}).status_code,422)
        self.assertEqual(self.client.put(self.base+'/preview/accounts/99999/name',json={'nickname':'test'}).status_code,404)
        with connect(self.path,write=True) as db: db.execute("UPDATE accounts SET source='manual'")
        self.assertEqual(self.client.put(endpoint,json={'nickname':'test'}).status_code,409)



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
