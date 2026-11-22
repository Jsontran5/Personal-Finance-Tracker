from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from finance.api import create_app
from finance.db import connect
from finance.multi_bank import map_accounts


class LiveFeatureUpgradeTests(TestCase):
    def test_existing_live_versions_upgrade_before_manual_mirror_with_startup_disabled(self):
        with TemporaryDirectory() as folder, patch.dict('os.environ',{'APP_ENV':'production','APP_STARTUP_ENABLED':'false'}), \
                patch('finance.plaid_sandbox.urlopen',side_effect=AssertionError('Upgrade must not call a provider')):
            database=Path(folder)/'data/finance.db'
            live=database.with_suffix('.plaid-live.db')
            month=date.today().isoformat()[:7]
            with TestClient(create_app(database),base_url='http://127.0.0.1') as client:
                self.assertEqual(client.get('/api/plaid/live').status_code,200)
                with connect(live,write=True) as db:
                    db.execute("INSERT INTO multi_connections(id,institution_id,name,profile,item_id,token,cursor) VALUES (1,'ins_synthetic','Synthetic saved bank','banking','synthetic-saved-item',?,'saved-cursor')",(b'synthetic-saved-authorization',))
                    map_accounts(db,1,[{'account_id':'checking','name':'Saved checking','type':'depository','subtype':'checking','balances':{'current':123,'iso_currency_code':'USD'}}])
                    db.execute("INSERT INTO transactions VALUES ('p1:saved','p1:checking',?,'Saved purchase',500,'USD','expense',0,0)",(date.today().isoformat(),))
                    db.execute("INSERT INTO budget_events(batch,date,category,amount_minor,kind,note,created_at) VALUES ('saved-allocation',?,'c:1',1000,'assign','Existing allocation',?)",(date.today().isoformat(),date.today().isoformat()))
                    original={name:[tuple(r) for r in db.execute('SELECT * FROM '+name)] for name in ['accounts','transactions','multi_connections','budget_events']}
                for version in [9,10,13,14]:
                    for route in ['/api/plaid/live','/api/plaid/live/preview?month='+month]:
                        with self.subTest(version=version,route=route):
                            with connect(live,write=True) as db:
                                for name in ['category_sync','category_lifecycle','category_operations']:
                                    db.execute('DROP TABLE IF EXISTS '+name)
                                db.execute('PRAGMA user_version='+str(version))
                            response=client.get(route)
                            self.assertEqual(response.status_code,200,response.text)
                            with connect(live) as db:
                                for name,rows in original.items():
                                    self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM '+name)],rows,name)
                                self.assertTrue(db.execute("SELECT 1 FROM sqlite_schema WHERE name='category_sync'").fetchone())
                # An older installation may lack the whole budget feature, not only category links.
                with connect(live,write=True) as db:
                    for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND (name LIKE 'budget_%' OR name LIKE 'category_%')").fetchall():
                        db.execute('DROP TABLE '+row[0])
                    db.execute('PRAGMA user_version=14')
                self.assertEqual(client.get('/api/plaid/live').status_code,200)
                self.assertEqual(client.get('/api/plaid/live/preview?month='+month).status_code,200)
                self.assertEqual(client.get('/api/plaid/live/preview/plan?month='+month).status_code,200)
                self.assertFalse(client.get('/api/startup').json()['enabled'])
                with connect(live) as db:
                    for name in ['accounts','transactions','multi_connections']:
                        self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM '+name)],original[name],name)
