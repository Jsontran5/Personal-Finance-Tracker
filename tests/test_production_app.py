from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from finance.api import create_app
from finance.app_config import application_config
from finance.db import connect
from finance.multi_bank import map_accounts


class ProductionAppTests(TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory()
        self.root=Path(self.folder.name)
        self.database=self.root/'data/finance.db'
        self.environment=patch.dict('os.environ',{'APP_ENV':'production','PLAID_LIVE_ENABLED':'false','APP_STARTUP_ENABLED':'false'})
        self.environment.start()
        self.client=TestClient(create_app(self.database),base_url='http://127.0.0.1',headers={'x-finance-request':'1'})
        self.client.__enter__()
        self.base='/api/plaid/live'
        self.month=date.today().isoformat()[:7]

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.environment.stop()
        self.folder.cleanup()

    def overview(self):
        result=self.client.get(self.base+'/preview',params={'month':self.month})
        self.assertEqual(result.status_code,200,result.text)
        return result.json()

    def add_account(self,name='Manual cash',kind='cash',amount=10000):
        result=self.client.post('/api/accounts',json={'name':name,'kind':kind,'currency':'USD',
            'date':date.today().isoformat(),'opening_balance_minor':amount,'include_in_net_worth':True})
        self.assertEqual(result.status_code,201,result.text)
        return result.json()['id']

    def test_production_forces_unified_view_without_enabling_plaid_or_sandbox(self):
        config=self.client.get('/api/app-config').json()
        self.assertEqual(config,{'environment':'production','unified':True,'sandbox_enabled':False})
        self.assertEqual(self.client.get('/api/workspace').json(),{'mode':'live'})
        self.assertEqual(self.client.put('/api/workspace',json={'mode':'sandbox'}).status_code,409)
        for path in ['/api/sandbox-scenario','/api/plaid/sandbox/preview?month='+self.month,
                     '/api/plaid/investments-sandbox/investments','/api/plaid/combined-sandbox',
                     '/api/chart-selection/sandbox']:
            self.assertEqual(self.client.get(path).status_code,404,path)
        self.overview()
        self.assertFalse(self.client.get(self.base).json()['linking_enabled'])
        self.assertEqual(self.client.put(self.base+'/manual',json={'account_ids':[]}).status_code,409)

    def test_manual_and_bank_accounts_coexist_with_stable_source_ids_and_shared_categories(self):
        self.overview()
        with connect(self.database.with_suffix('.plaid-live.db'),write=True) as db:
            db.execute("INSERT INTO multi_connections(id,institution_id,name,profile) VALUES (1,'fixture','Test bank','banking')")
            map_accounts(db,1,[{'account_id':'bank','name':'Connected checking','type':'depository','subtype':'checking',
                 'balances':{'current':200,'iso_currency_code':'USD'}}])
        source=self.add_account()
        first=self.overview()
        manual=next(a for a in first['accounts'] if a['manual_id']==source)
        self.assertNotEqual(manual['id'],source)
        self.assertEqual(first['net_worth_minor'],30000)
        category=self.client.post(self.base+'/preview/categories',json={'name':'Shared category','kind':'expense'})
        self.assertEqual(category.status_code,201)
        self.overview()
        canonical=self.client.get('/api/overview',params={'month':self.month}).json()
        canonical_category=next(c for c in canonical['categories'] if c['name']=='Shared category')['id']
        tx=self.client.post('/api/transactions',json={'account_id':source,'kind':'expense','category_id':canonical_category,
              'amount_minor':500,'date':date.today().isoformat(),'merchant':'Manual purchase','notes':''})
        self.assertEqual(tx.status_code,201,tx.text)
        data=self.overview()
        entry=next(t for t in data['transactions'] if t['manual_source'])
        self.assertEqual(entry['manual_transaction_id'],tx.json()['id'])
        self.assertEqual(entry['category_name'],'Shared category')
        self.assertEqual(data['spending_minor'],500)
        self.assertEqual(data['net_worth_minor'],29500)
        review=self.client.get(self.base+'/preview/review').json()
        self.assertEqual(next(t for t in review['items'] if t['manual_source'])['manual_transaction_id'],tx.json()['id'])
        self.assertEqual(self.client.delete('/api/transactions/'+str(tx.json()['id'])).status_code,200)
        self.assertEqual(self.overview()['transactions'],[])
        self.assertEqual(self.overview()['net_worth_minor'],30000)

    def test_manual_nelnet_balance_and_debt_edits_survive_and_do_not_require_bank_keys(self):
        source=self.add_account('Nelnet','loan',2500000)
        self.overview()
        self.assertEqual(self.client.put('/api/accounts/'+str(source)+'/debt',json={
            'subtype':'student','servicer':'Nelnet','apr_percent':'4.25','rate_type':'fixed'}).status_code,200)
        result=self.client.post('/api/accounts/'+str(source)+'/balances',json={
            'balance_minor':2400000,'date':date.today().isoformat(),'notes':'Updated servicer balance'})
        self.assertEqual(result.status_code,201,result.text)
        data=self.overview()
        self.assertEqual(data['net_worth_minor'],-2400000)
        account=data['accounts'][0]
        debt=self.client.get(self.base+'/debt/'+str(account['id'])).json()
        self.assertEqual(debt['fields']['servicer'],'Nelnet')
        self.assertEqual(debt['fields']['apr_percent'],'4.25')
        self.assertEqual(self.client.get(self.base).json()['connections'],[])

    def test_paired_backups_restore_manual_source_and_connected_state_without_losing_manual_records(self):
        source=self.add_account()
        self.overview()
        saved=self.client.post('/api/backups/all')
        self.assertEqual(saved.status_code,201,saved.text)
        saved=saved.json()
        self.assertEqual(set(saved),{'manual','connected'})
        self.client.put('/api/accounts/'+str(source),json={'name':'Changed','include_in_net_worth':True,'primary':False})
        self.overview()
        for key in ['manual','connected']:
            preview=self.client.get('/api/backups/preview',params={'id':saved[key]['id']})
            self.assertEqual(preview.status_code,200,preview.text)
            preview=preview.json()
            restored=self.client.post('/api/backups/restore',json={'id':saved[key]['id'],
                'preview_token':preview['preview_token'],'confirmation':preview['confirmation']})
            self.assertEqual(restored.status_code,200,restored.text)
        self.assertEqual(self.overview()['accounts'][0]['name'],'Manual cash')
        self.assertEqual(self.client.post('/api/backups',json={'workspace':'sandbox'}).status_code,409)

    def test_environment_is_explicit_and_reversible_without_moving_databases(self):
        (self.root/'.env').write_text('APP_ENV=production\n',encoding='utf-8')
        with patch.dict('os.environ',{'APP_ENV':'development'}):
            self.assertFalse(application_config(self.root)['unified'])
            with TestClient(create_app(self.database),base_url='http://127.0.0.1') as client:
                self.assertEqual(client.get('/api/sandbox-scenario').status_code,200)
        with patch.dict('os.environ',{'APP_ENV':'invalid'}):
            with self.assertRaises(ValueError):
                application_config(self.root)

    def test_existing_manual_transfer_pairs_keep_their_reporting_treatment(self):
        outgoing=self.add_account('Manual checking')
        incoming=self.add_account('Manual savings')
        for account,kind in [(outgoing,'expense'),(incoming,'income')]:
            result=self.client.post('/api/transactions',json={'account_id':account,'kind':kind,
                'amount_minor':1000,'date':date.today().isoformat(),'merchant':'Transfer payment'})
            self.assertEqual(result.status_code,201,result.text)
        path='/api/review/transfers'
        candidate=self.client.get(path).json()['suggestions'][0]
        body={'outgoing_id':candidate['outgoing']['id'],'incoming_id':candidate['incoming']['id']}
        preview=self.client.post(path+'/preview',json=body).json()
        self.assertEqual(self.client.post(path+'/confirm',json={**body,'preview_token':preview['preview_token']}).status_code,200)
        data=self.overview()
        self.assertEqual(data['spending_minor'],0)
        self.assertEqual(data['income_minor'],0)
        self.assertTrue(all(tx['kind']=='transfer' for tx in data['transactions']))
        link=self.client.get(path).json()['saved'][0]
        self.assertEqual(self.client.delete(path+'/'+str(link['id'])).status_code,200)
        self.assertEqual(self.overview()['spending_minor'],1000)
        self.assertEqual(self.overview()['income_minor'],1000)
