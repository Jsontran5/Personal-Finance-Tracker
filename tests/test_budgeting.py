from datetime import date,timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from fastapi.testclient import TestClient
from finance.api import create_app
from finance.db import connect
from finance.recovery import BackupStore


class BudgetTests(TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory();self.db=Path(self.folder.name)/'data/finance.db'
        self.env=patch.dict('os.environ',{'APP_ENV':'development','APP_STARTUP_ENABLED':'false'});self.env.start()
        self.client=TestClient(create_app(self.db),base_url='http://127.0.0.1',headers={'x-finance-request':'1'});self.client.__enter__()
        self.month=date.today().isoformat()[:7];self.day=date.today().isoformat()
        self.cash=self.account('Cash','checking',100000);self.card=self.account('Card','credit',10000)
        self.category=self.get()['categories'][0]['id']
    def tearDown(self):
        self.client.__exit__(None,None,None);self.env.stop();self.folder.cleanup()
    def account(self,name,kind,amount):
        r=self.client.post('/api/accounts',json={'name':name,'kind':kind,'opening_balance_minor':amount,'date':self.day});self.assertEqual(r.status_code,201,r.text);return r.json()['id']
    def get(self,month=None):
        r=self.client.get('/api/plan',params={'month':month or self.month});self.assertEqual(r.status_code,200,r.text);return r.json()
    def setup(self):
        r=self.client.post('/api/plan/setup',json={'token':self.get()['opening_token'],'cash_accounts':[self.cash],'credit_accounts':[self.card],'method':'zero'});self.assertEqual(r.status_code,200,r.text);return r.json()
    def action(self,lines,source=None,kind='assign',month=None,expected=200):
        r=self.client.post('/api/plan/actions',params={'month':month or self.month},json={'token':self.get(month)['token'],'lines':lines,'source':source,'kind':kind});self.assertEqual(r.status_code,expected,r.text);return r.json()
    def tx(self,kind,amount,account=None,**extra):
        r=self.client.post('/api/transactions',json={'account_id':account or self.cash,'kind':kind,'amount_minor':amount,'date':self.day,'merchant':'Synthetic '+kind,'category_id':self.category if kind in {'expense','refund'} else None,**extra});self.assertEqual(r.status_code,201,r.text);return r.json()['id']
    def row(self):return next(c for c in self.get()['categories'] if c['id']==self.category)
    def test_opening_existing_limits_backfill_and_stale_token(self):
        self.tx('income',10000)
        self.client.put(f'/api/budgets/{self.month}/{self.category}',json={'limit_minor':20000})
        before=self.get();result=self.setup();self.assertTrue(BackupStore(self.db).resolve(result['backup']).is_file())
        self.assertEqual(self.get()['ready_minor'],110000);self.assertEqual(self.get()['receipts'],[])
        self.assertEqual(self.row()['limit_minor'],20000);self.assertEqual(self.row()['assigned_minor'],0)
        self.assertEqual(self.client.post('/api/plan/actions',params={'month':self.month},json={'token':before['token'],'lines':[{'category':f'c:{self.category}','amount_minor':100}]}).status_code,409)
        self.assertEqual(self.client.post('/api/plan/setup',json={'token':self.get()['opening_token'],'cash_accounts':[self.cash]}).status_code,409)
    def test_income_partial_split_repeat_sync_and_undo(self):
        self.setup();identifier=self.tx('income',20000)
        self.assertEqual(self.get()['ready_minor'],120000)
        receipt=self.get()['receipts'][0];batch=self.action([{'category':f'c:{self.category}','amount_minor':12500}],str(identifier))['batch']
        self.assertEqual(self.get()['receipts'][0]['remaining_minor'],7500)
        self.assertEqual(self.row()['available_minor'],12500)
        self.action([{'category':f'c:{self.category}','amount_minor':8000}],str(identifier),expected=422)
        for _ in range(3):self.assertEqual(self.get()['ready_minor'],107500)
        self.assertEqual(self.client.post('/api/plan/undo/'+batch).status_code,200)
        self.assertEqual(self.get()['receipts'][0]['remaining_minor'],20000)
        self.assertEqual(self.row()['available_minor'],0)
    def test_funded_credit_purchase_reserve_payment_refund_and_existing_debt(self):
        self.setup()
        self.action([{'category':f'c:{self.category}','amount_minor':30000}])
        self.tx('expense',15000,self.card)
        self.assertEqual(self.row()['available_minor'],15000)
        self.assertEqual(self.get()['reserves'][0]['reserved_minor'],15000)
        self.tx('transfer',15000,destination_id=self.card)
        self.assertEqual(self.get()['reserves'][0]['reserved_minor'],0)
        self.assertEqual(self.get()['ready_minor'],70000)
        self.assertEqual(self.row()['actual_minor'],15000)
        self.action([{'category':f'card:{self.card}','amount_minor':10000}])
        self.assertEqual(self.get()['reserves'][0]['reserved_minor'],10000)
        self.assertEqual(self.get()['reserves'][0]['assigned_minor'],10000)
    def test_unfunded_card_spending_then_cover_and_refund(self):
        self.setup();self.tx('expense',15000,self.card)
        self.assertEqual(self.row()['available_minor'],-15000)
        self.assertEqual(self.get()['reserves'][0]['unfunded_minor'],15000)
        self.action([{'category':f'c:{self.category}','amount_minor':20000}])
        self.assertEqual(self.row()['available_minor'],5000)
        self.assertEqual(self.get()['reserves'][0]['reserved_minor'],15000)
        self.tx('refund',10000,self.card)
        self.assertEqual(self.get()['reserves'][0]['reserved_minor'],5000)
        self.assertEqual(self.row()['available_minor'],15000)
    def test_moving_funds_conserves_total_and_goal_transfer_not_double_spending(self):
        self.setup();other=self.get()['categories'][1]['id']
        self.action([{'category':f'c:{self.category}','amount_minor':30000}])
        self.action([{'category':f'c:{self.category}','amount_minor':-10000},{'category':f'c:{other}','amount_minor':10000}],kind='move')
        self.assertEqual(self.get()['ready_minor'],70000)
        loan=self.account('Debt','loan',20000)
        tx=self.tx('transfer',10000,destination_id=loan)
        self.assertEqual(self.get()['ready_minor'],60000)
        self.action([{'category':f'c:{other}','amount_minor':10000}],str(tx),kind='use')
        self.assertEqual(self.get()['ready_minor'],70000)
        self.assertEqual(next(c for c in self.get()['categories'] if c['id']==other)['available_minor'],0)
        self.assertEqual(sum(c['actual_minor'] for c in self.get()['categories']),0)
    def test_correction_is_visible_and_reconciled_without_silent_reallocation(self):
        self.setup();tx=self.tx('income',10000)
        self.action([{'category':f'c:{self.category}','amount_minor':10000}],str(tx))
        self.client.put('/api/transactions/'+str(tx),json={'account_id':self.cash,'kind':'income','amount_minor':5000,'date':self.day,'merchant':'Corrected'})
        data=self.get();self.assertTrue(any('changed' in w for w in data['warnings']))
        self.action([{'category':f'c:{self.category}','amount_minor':1000}],expected=409)
        r=self.client.post('/api/plan/reconcile',params={'month':self.month},json={'token':data['token'],'confirmed':True,'adjustment_minor':0});self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.get()['ready_minor'],95000)
        self.assertEqual(self.row()['assigned_minor'],10000)
    def test_rollover_cash_overspending_and_positive_savings(self):
        self.setup()
        start=(date.today().replace(day=1)-timedelta(days=40)).replace(day=1)
        nextmonth=(start.replace(day=28)+timedelta(days=4)).replace(day=1).isoformat()[:7]
        with connect(self.db,write=True) as db:
            settings=json.loads(db.execute('SELECT payload FROM budget_settings').fetchone()[0]);settings['start_date']=start.isoformat();settings['baseline']={}
            db.execute('UPDATE budget_settings SET payload=?',(json.dumps(settings),))
            db.execute('INSERT INTO budget_events(batch,date,category,amount_minor,kind,note,created_at) VALUES (?,?,?,?,?,?,?)',('fixture',start.isoformat(),f'c:{self.category}',30000,'assign','fixture',start.isoformat()))
            db.execute('INSERT INTO transactions(account_id,category_id,kind,amount_minor,date,merchant,notes) VALUES (?,?,?,?,?,?,?)',(self.cash,None,'expense',10000,start.isoformat(),'Uncategorized overspend',''))
        data=self.get(nextmonth)
        self.assertEqual(next(c for c in data['categories'] if c['id']==self.category)['available_minor'],30000)
        self.assertEqual(data['ready_minor'],60000)
    def test_copy_limits_targets_and_backup_restore_allocations(self):
        self.setup();self.action([{'category':f'c:{self.category}','amount_minor':10000}])
        meta={'group':'Savings goals','bucket':'savings','target_minor':60000,'target_type':'refill','repeat_monthly':True}
        self.assertEqual(self.client.put(f'/api/plan/categories/{self.category}',json=meta).status_code,200)
        self.assertEqual(self.row()['need_minor'],50000)
        saved=self.client.post('/api/backups',json={'workspace':'personal'}).json()
        self.action([{'category':f'c:{self.category}','amount_minor':5000}])
        preview=self.client.get('/api/backups/preview',params={'id':saved['id']});self.assertEqual(preview.status_code,200,preview.text)
        p=preview.json();restored=self.client.post('/api/backups/restore',json={k:p[k] for k in ['id','preview_token','confirmation']});self.assertEqual(restored.status_code,200,restored.text)
        self.assertEqual(self.row()['assigned_minor'],10000)
    def test_live_mirrored_manual_income_uses_stable_source_identity(self):
        self.client.get('/api/plaid/live/preview',params={'month':self.month})
        selected=self.client.put('/api/plaid/live/manual',json={'account_ids':[self.cash,self.card]})
        self.assertEqual(selected.status_code,200,selected.text)
        path='/api/plaid/live/preview/plan'
        data=self.client.get(path,params={'month':self.month}).json()
        cash=next(a['id'] for a in data['accounts'] if a['name']=='Cash')
        card=next(a['id'] for a in data['accounts'] if a['name']=='Card')
        setup=self.client.post(path+'/setup',json={'token':data['opening_token'],'cash_accounts':[cash],'credit_accounts':[card],'method':'ratio'})
        self.assertEqual(setup.status_code,200,setup.text)
        self.tx('income',20000)
        data=self.client.get(path,params={'month':self.month}).json()
        self.assertEqual(data['ready_minor'],120000)
        self.assertEqual(len(data['receipts']),1)
        self.assertTrue(data['receipts'][0]['source_id'].startswith('m:'))
        r=self.client.post(path+'/actions',params={'month':self.month},json={'token':data['token'],'source':data['receipts'][0]['source_id'],'lines':[{'category':'c:'+str(data['categories'][0]['id']),'amount_minor':10000}]})
        self.assertEqual(r.status_code,200,r.text)
        for _ in range(2):
            data=self.client.get(path,params={'month':self.month}).json()
            self.assertEqual(data['receipts'][0]['remaining_minor'],10000)
    def test_balance_drift_requires_review_and_budget_adjustment_is_separate(self):
        self.setup()
        r=self.client.post(f'/api/accounts/{self.cash}/balances',json={'balance_minor':90000,'date':self.day})
        self.assertEqual(r.status_code,201,r.text)
        data=self.get();self.assertEqual(data['reconciliation']['difference_minor'],-10000)
        self.action([{'category':f'c:{self.category}','amount_minor':1000}],expected=409)
        r=self.client.post('/api/plan/reconcile',params={'month':self.month},json={'token':data['token'],'adjustment_minor':-10000,'confirmed':True})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.get()['ready_minor'],90000)
        self.assertEqual(self.get()['reconciliation']['difference_minor'],0)
    def test_saved_category_method_can_initialize_funding_and_ratios_validate(self):
        r=self.client.put('/api/plan/settings',params={'month':self.month},json={'token':self.get()['token'],'method':'categories','ratios':[50,30,20]})
        self.assertEqual(r.status_code,200,r.text);self.setup()
        invalid=self.client.put('/api/plan/settings',params={'month':self.month},json={'token':self.get()['token'],'method':'custom','ratios':[60,30,20]})
        self.assertEqual(invalid.status_code,422)
    def test_non_cash_assets_cannot_fund_budget(self):
        investing=self.account('Investment','investment',1000000)
        r=self.client.post('/api/plan/setup',json={'token':self.get()['opening_token'],'cash_accounts':[investing],'method':'zero'})
        self.assertEqual(r.status_code,422,r.text)
    def test_uncategorized_overspending_can_be_funded_without_changing_transaction(self):
        self.setup();tx=self.tx('expense',10000,category_id=None)
        unknown=next(c for c in self.get()['categories'] if c['id']==0)
        self.assertEqual(unknown['available_minor'],-10000)
        self.action([{'category':'c:0','amount_minor':10000}])
        self.assertEqual(next(c for c in self.get()['categories'] if c['id']==0)['available_minor'],0)
        self.assertEqual(self.get()['ready_minor'],90000)
        with connect(self.db) as db:
            self.assertIsNone(db.execute('SELECT category_id FROM transactions WHERE id=?',(tx,)).fetchone()[0])
    def test_partial_unassignment_can_repair_negative_ready_balance(self):
        self.setup();start=(date.today().replace(day=1)-timedelta(days=70)).replace(day=1)
        month=(start.replace(day=28)+timedelta(days=4)).replace(day=1).isoformat()[:7]
        with connect(self.db,write=True) as db:
            saved=json.loads(db.execute('SELECT payload FROM budget_settings').fetchone()[0]);saved['start_date']=start.isoformat();saved['baseline']={}
            db.execute('UPDATE budget_settings SET payload=?',(json.dumps(saved),))
            db.execute('INSERT INTO budget_events(batch,date,category,amount_minor,kind,note,created_at) VALUES (?,?,?,?,?,?,?)',('seed',start.isoformat(),f'c:{self.category}',80000,'assign','fixture',start.isoformat()))
            db.execute('INSERT INTO transactions(account_id,kind,amount_minor,date,merchant,notes) VALUES (?,?,?,?,?,?)',(self.cash,'expense',50000,start.isoformat(),'Overspending',''))
        self.assertEqual(self.get(month)['ready_minor'],-30000)
        self.action([{'category':f'c:{self.category}','amount_minor':-10000}],month=month)
        self.assertEqual(self.get(month)['ready_minor'],-20000)
