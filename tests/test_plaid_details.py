import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from finance.api import create_app
from finance.db import connect
from finance.plaid_sandbox import SCHEMA, changes, sandbox_router, save_transactions
from finance.plaid_vault import protect
from finance.sandbox_preview import initialize_preview


class TransactionDetailsTests(TestCase):
    def test_sync_requests_original_description(self):
        requests = []

        def provider(endpoint, **payload):
            requests.append((endpoint, payload))
            return {
                "next_cursor": "next", "has_more": False, "added": [], "modified": [], "removed": [],
            }

        changes(provider, "synthetic-access", "existing-cursor")
        endpoint, payload = requests[0]
        self.assertEqual(endpoint, "/transactions/sync")
        self.assertEqual(payload["cursor"], "existing-cursor")
        self.assertTrue(payload["options"]["include_original_description"])
        self.assertEqual(payload["options"]["personal_finance_category_version"], "v2")

    def test_migration_payload_and_edits_survive_replay(self):
        with TemporaryDirectory() as temporary:
            database = Path(temporary) / "data" / "finance.db"
            database.parent.mkdir()
            sandbox = database.with_suffix(".plaid-sandbox.db")
            with connect(sandbox, write=True) as db:
                for statement in SCHEMA:
                    db.execute(statement)
                initialize_preview(db)
                db.execute("DROP TABLE transaction_details")
                db.execute("ALTER TABLE accounts DROP COLUMN active")
                db.execute("ALTER TABLE accounts DROP COLUMN subtype")
                db.execute("PRAGMA user_version=1")
                db.execute("INSERT INTO accounts VALUES ('card','Card',NULL,'credit','USD',-2000,'cached',NULL)")
                db.execute(
                    "INSERT INTO connection(id,item_id,token,cursor) VALUES (1,?,?,?)",
                    ("synthetic-item", protect(b"synthetic-access", True), "saved-cursor"),
                )

            with TestClient(create_app(database), base_url="http://127.0.0.1",
                            headers={"x-finance-request": "1"}) as client:
                url = "/api/plaid/sandbox/preview"
                self.assertEqual(client.get(url, params={"month": "2026-11"}).status_code, 200)
                tx = {
                    "transaction_id": "purchase", "account_id": "card", "name": "Raw description",
                    "merchant_name": "Shop", "date": "2026-11-11", "amount": Decimal("12.34"),
                    "iso_currency_code": "USD", "pending": False, "original_description": "ORIGINAL SHOP",
                    "location": {"lat": Decimal("45.123456"), "city": "Test City"},
                    "counterparties": [{"name": "Shop"}], "future_field": {"keep": True},
                }
                with connect(sandbox, write=True) as db:
                    self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 11)
                    self.assertEqual(db.execute("SELECT cursor FROM connection").fetchone()[0], "saved-cursor")
                    save_transactions(db, [tx], [], [])

                preview = client.get(url, params={"month": "2026-11"}).json()
                row_id = preview["transactions"][0]["id"]
                detail_url = url + f"/transactions/{row_id}/details"
                details = client.get(detail_url).json()
                self.assertEqual(details["provider"]["original_description"], "ORIGINAL SHOP")
                self.assertEqual(details["provider"]["location"]["lat"], "45.123456")
                self.assertTrue(details["provider"]["future_field"]["keep"])
                self.assertEqual(client.put(url + f"/transactions/{row_id}", json={
                    "merchant": "My edit", "kind": "expense", "category_id": None, "notes": "Keep this",
                }).status_code, 200)
                with connect(sandbox, write=True) as db:
                    save_transactions(db, [], [{**tx, "amount": Decimal("15.00")}], [])
                    save_transactions(db, [{
                        **tx, "transaction_id": "unpriced", "amount": Decimal("0.0000001"),
                        "iso_currency_code": None, "unofficial_currency_code": "TEST",
                    }], [], [])
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM transaction_details").fetchone()[0], 2)
                    raw = db.execute("SELECT payload FROM transaction_details WHERE transaction_id='unpriced'").fetchone()[0]
                    self.assertEqual(json.loads(raw)["amount"], "1E-7")
                updated = client.get(url, params={"month": "2026-11"}).json()["transactions"][0]
                self.assertEqual(updated["merchant"], "My edit")
                self.assertEqual(updated["notes"], "Keep this")
                self.assertEqual(updated["amount_minor"], 1500)

    def test_manage_accounts_uses_update_mode(self):
        calls = []

        def provider(endpoint, **payload):
            calls.append((endpoint, payload))
            return {
                "link_token": "synthetic-link", "hosted_link_url": "https://secure.plaid.com/hl/test",
                "expiration": "2030-01-01T00:00:00Z",
            }

        with TemporaryDirectory() as temporary:
            database = Path(temporary) / "data" / "finance.db"
            with patch("finance.plaid_sandbox.credentials", return_value=("synthetic-client", "synthetic-secret")), patch(
                "finance.api.sandbox_router", lambda db: sandbox_router(db, client_factory=lambda keys: provider),
            ):
                with TestClient(create_app(database), base_url="http://127.0.0.1",
                                headers={"x-finance-request": "1"}) as client:
                    client.get("/api/plaid/sandbox/preview", params={"month": "2026-11"})
                    with connect(database.with_suffix(".plaid-sandbox.db"), write=True) as db:
                        db.execute("INSERT INTO connection(id,item_id,token) VALUES (1,?,?)",
                                   ("existing-item", protect(b"synthetic-access", True)))
                    result = client.post("/api/plaid/sandbox/link?reconnect=true")
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(calls[0][0], "/link/token/create")
                    self.assertEqual(calls[0][1]["update"], {"account_selection_enabled": True})
                    self.assertEqual(calls[0][1]["access_token"], "synthetic-access")
                    self.assertNotIn("products", calls[0][1])
