from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from fastapi.testclient import TestClient
from finance.api import create_app
from finance.db import connect
from finance.category_management import merge_one


class CategoryTests(TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory();self.root=Path(self.folder.name);self.database=self.root/'data/finance.db'
        self.env=patch.dict('os.environ',{'APP_ENV':'development','APP_STARTUP_ENABLED':'false'});self.env.start()
        self.client=TestClient(create_app(self.database),base_url='http://127.0.0.1',headers={'x-finance-request':'1'});self.client.__enter__()
        self.path='/api/category-management';self.month=date.today().isoformat()[:7]
        self.account=self.client.post('/api/accounts',json={'name':'Cash','kind':'checking','date':date.today().isoformat(),'opening_balance_minor':100000}).json()['id']
        self.a=self.add('Gas');self.b=self.add('Transportation costs')
    def tearDown(self):
        self.client.__exit__(None,None,None);self.env.stop();self.folder.cleanup()
    def add(self,name,kind='expense'):
        r=self.client.post('/api/categories',json={'name':name,'kind':kind});self.assertEqual(r.status_code,201,r.text);return r.json()['id']
    def listing(self):
        r=self.client.get(self.path);self.assertEqual(r.status_code,200,r.text);return r.json()
    def tx(self,category,kind='expense'):
        r=self.client.post('/api/transactions',json={'account_id':self.account,'date':date.today().isoformat(),'merchant':'Synthetic fuel','amount_minor':1000,'kind':kind,'category_id':category});self.assertEqual(r.status_code,201,r.text);return r.json()['id']
    def edit(self,identifier,**changes):
        r=self.client.put(self.path+'/'+str(identifier),json={'token':self.listing()['token'],**changes});self.assertEqual(r.status_code,200,r.text);return r.json()
    def preview(self,a=None,b=None,policy='sum'):
        r=self.client.post(self.path+'/merge/preview',json={'source_id':a or self.a,'target_id':b or self.b,'limit_policy':policy});self.assertEqual(r.status_code,200,r.text);return r.json()
    def merge(self,preview,a=None,b=None,policy='sum'):
        return self.client.post(self.path+'/merge/apply',json={'source_id':a or self.a,'target_id':b or self.b,'limit_policy':policy,'token':preview['token'],'confirmation':preview['confirmation']})
    def shared(self):
        self.client.__exit__(None,None,None)
        with patch.dict('os.environ',{'APP_ENV':'production'}):self.client=TestClient(create_app(self.database),base_url='http://127.0.0.1',headers={'x-finance-request':'1'})
        self.client.__enter__();self.path='/api/plaid/live/preview/category-management'
        rows=self.listing()['categories']
        self.a=next(r['id'] for r in rows if r['name']=='Gas');self.b=next(r['id'] for r in rows if r['name']=='Transportation costs')
    def test_rename_archive_restore_preserve_history_and_pause_rules(self):
        tx=self.tx(self.a)
        with connect(self.database,write=True) as db:
            db.execute("INSERT INTO merchant_rules(merchant,match_kind,kind,category_id,active) VALUES ('fuel','expense','expense',?,1)",(self.a,))
        self.edit(self.a,name='Fuel and gas')
        self.edit(self.a,archived=True)
        with connect(self.database) as db:
            self.assertEqual(db.execute('SELECT category_id FROM transactions WHERE id=?',(tx,)).fetchone()[0],self.a)
            self.assertEqual(db.execute('SELECT active FROM merchant_rules').fetchone()[0],0)
        self.assertEqual(next(r for r in self.listing()['categories'] if r['id']==self.a)['archived'],1)
        self.edit(self.a,archived=False)
        self.assertEqual(next(r for r in self.listing()['categories'] if r['id']==self.a)['name'],'Fuel and gas')
    def test_delete_only_unused_and_name_conflicts(self):
        token=self.listing()['token'];self.tx(self.a)
        self.assertEqual(self.client.delete(self.path+'/'+str(self.a),params={'token':token}).status_code,409)
        token=self.listing()['token']
        self.assertEqual(self.client.delete(self.path+'/'+str(self.a),params={'token':token}).status_code,409)
        self.assertEqual(self.client.put(self.path+'/'+str(self.a),json={'token':token,'name':'Transportation costs'}).status_code,409)
        self.assertEqual(self.client.delete(self.path+'/'+str(self.b),params={'token':token}).status_code,200)
        self.assertNotIn(self.b,[r['id'] for r in self.listing()['categories']])
    def test_merge_preview_is_readonly_and_sums_or_keeps_limits(self):
        self.tx(self.a)
        for category,amount in [(self.a,20000),(self.b,10000)]:self.client.put(f'/api/budgets/{self.month}/{category}',json={'limit_minor':amount})
        before=self.listing();preview=self.preview()
        self.assertEqual(before,self.listing());self.assertEqual(len(preview['conflicts']),1)
        wrong=self.merge(preview,policy='keep');self.assertEqual(wrong.status_code,409)
        result=self.merge(preview);self.assertEqual(result.status_code,200,result.text)
        with connect(self.database) as db:
            self.assertEqual(db.execute('SELECT category_id FROM transactions').fetchone()[0],self.b)
            self.assertEqual(db.execute('SELECT limit_minor FROM monthly_budgets WHERE category_id=?',(self.b,)).fetchone()[0],30000)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
    def test_kind_mismatch_and_archived_destination_rejected(self):
        income=self.add('Income source','income')
        self.assertEqual(self.client.post(self.path+'/merge/preview',json={'source_id':self.a,'target_id':income}).status_code,422)
        self.edit(self.b,archived=True)
        self.assertEqual(self.client.post(self.path+'/merge/preview',json={'source_id':self.a,'target_id':self.b}).status_code,422)
    def test_funded_allocations_and_income_signatures_survive_merge(self):
        one=self.add('Salary one','income');two=self.add('Salary two','income')
        plan=self.client.get('/api/plan',params={'month':self.month}).json()
        self.client.post('/api/plan/setup',json={'token':plan['opening_token'],'cash_accounts':[self.account],'method':'zero'})
        receipt=self.tx(one,'income');plan=self.client.get('/api/plan',params={'month':self.month}).json()
        self.client.post('/api/plan/actions',params={'month':self.month},json={'token':plan['token'],'source':str(receipt),'lines':[{'category':'c:'+str(self.a),'amount_minor':500}]})
        preview=self.preview(one,two);result=self.merge(preview,one,two);self.assertEqual(result.status_code,200,result.text)
        preview=self.preview();result=self.merge(preview);self.assertEqual(result.status_code,200,result.text)
        plan=self.client.get('/api/plan',params={'month':self.month}).json()
        self.assertFalse(plan['warnings']);self.assertEqual(plan['receipts'][0]['remaining_minor'],500)
        self.assertEqual(next(c for c in plan['categories'] if c['id']==self.b)['available_minor'],500)
    def test_shared_rename_merge_refresh_and_atomic_failure(self):
        original=self.a;self.tx(original);self.shared()
        self.edit(self.a,name='Fuel renamed')
        for _ in range(2):self.assertNotIn('Gas',[r['name'] for r in self.listing()['categories']])
        with connect(self.database) as db:self.assertEqual(db.execute('SELECT name FROM categories WHERE id=?',(original,)).fetchone()[0],'Fuel renamed')
        preview=self.preview();calls=0
        def fail_last(*args):
            nonlocal calls
            calls+=1
            if calls==4:raise RuntimeError('Synthetic second-store failure')
            return merge_one(*args)
        with patch('finance.category_management.merge_one',side_effect=fail_last):
            with self.assertRaises(RuntimeError):self.merge(preview)
        self.assertIn(self.a,[r['id'] for r in self.listing()['categories']])
        with connect(self.database) as db:self.assertEqual(db.execute('SELECT category_id FROM transactions').fetchone()[0],original)
        preview=self.preview();result=self.merge(preview);self.assertEqual(result.status_code,200,result.text)
        for _ in range(2):self.assertNotIn('Fuel renamed',[r['name'] for r in self.listing()['categories']])
        with connect(self.database) as db:
            self.assertFalse(db.execute('SELECT 1 FROM categories WHERE id=?',(original,)).fetchone())
            self.assertEqual(db.execute('SELECT c.name FROM transactions t JOIN categories c ON t.category_id=c.id').fetchone()[0],'Transportation costs')
    def test_shared_archive_and_unused_delete_stay_gone_after_refresh(self):
        original=self.b;self.shared();self.edit(self.a,archived=True)
        for _ in range(2):
            self.assertEqual(next(r for r in self.listing()['categories'] if r['id']==self.a)['archived'],1)
        token=self.listing()['token']
        r=self.client.delete(self.path+'/'+str(self.b),params={'token':token});self.assertEqual(r.status_code,200,r.text)
        for _ in range(2):self.assertNotIn('Transportation costs',[r['name'] for r in self.listing()['categories']])
        with connect(self.database) as db:self.assertFalse(db.execute('SELECT 1 FROM categories WHERE id=?',(original,)).fetchone())
    def test_orphan_links_from_a_partial_restore_are_repaired(self):
        original=self.a;self.shared()
        with connect(self.database,write=True) as db:db.execute('DELETE FROM categories WHERE id=?',(original,))
        rows=self.listing()['categories'];source=next(r for r in rows if r['name']=='Gas')
        self.edit(source['id'],name='Restored gas')
        with connect(self.database) as db:self.assertTrue(db.execute("SELECT 1 FROM categories WHERE name='Restored gas'").fetchone())
