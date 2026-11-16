import json
from unittest import TestCase

from finance.transaction_flow import project_flow


class TransactionFlowTests(TestCase):
    def row(self, signed, name='Transfer', **details):
        return {'account_name': 'My checking', 'merchant': name, 'source_merchant': name,
                'kind': 'transfer', 'amount_minor': abs(signed), '_flow_amount_minor': signed,
                '_flow_payload': json.dumps(details)}

    def test_bank_sign_defines_direction_for_both_transfer_legs(self):
        outgoing = self.row(1900, 'Robinhood', merchant_name='Robinhood')
        incoming = self.row(-13600, 'From Brokerage')
        project_flow([outgoing, incoming], provider=True)
        self.assertEqual(outgoing['flow'], {'direction': 'out', 'from': 'My checking', 'to': 'Robinhood', 'basis': 'reported'})
        self.assertEqual(incoming['flow'], {'direction': 'in', 'from': 'Brokerage', 'to': 'My checking', 'basis': 'description'})
        self.assertEqual(incoming['amount_minor'], 13600)
        self.assertEqual(incoming['kind'], 'transfer')
        self.assertNotIn('_flow_payload', outgoing)

    def test_confirmed_pair_uses_account_nicknames_before_bank_description(self):
        tx = self.row(-5000, 'Generic credit')
        tx.update(transfer_pair_id=1, transfer_role='incoming', transfer_other_account='Everyday checking')
        project_flow([tx], provider=True)
        self.assertEqual(tx['flow']['from'], 'Everyday checking')
        self.assertEqual(tx['flow']['to'], 'My checking')
        self.assertEqual(tx['flow']['basis'], 'confirmed')

    def test_structured_payer_payee_and_unknown_endpoints(self):
        incoming = self.row(-100, payment_meta={'payer': 'Employer'})
        outgoing = self.row(200, payment_meta={'payee': 'Utility company'})
        unknown = self.row(300)
        description_only = self.row(400, 'Monthly fee')
        project_flow([incoming, outgoing, unknown, description_only], provider=True)
        self.assertEqual(incoming['flow']['from'], 'Employer')
        self.assertEqual(outgoing['flow']['to'], 'Utility company')
        self.assertEqual(unknown['flow']['to'], 'Destination not supplied')
        self.assertEqual(unknown['flow']['basis'], 'unavailable')
        self.assertEqual(description_only['flow']['to'], 'Destination not supplied')

    def test_manual_destination_and_income_do_not_claim_bank_metadata(self):
        tx = {'account_name': 'Checking', 'destination_name': 'Retirement', 'merchant': 'Contribution', 'kind': 'transfer', 'amount_minor': 10000}
        income = {'account_name': 'Checking', 'merchant': 'Paycheck', 'kind': 'income', 'amount_minor': 10000}
        project_flow([tx, income])
        self.assertEqual(tx['flow'], {'direction': 'out', 'from': 'Checking', 'to': 'Retirement', 'basis': 'manual'})
        self.assertEqual(income['flow']['direction'], 'in')
        self.assertEqual(income['flow']['basis'], 'manual')

    def test_counterparty_and_zero_value_do_not_invent_account_matches(self):
        tx = self.row(100, counterparties=[{'name': 'Robinhood', 'type': 'financial_institution'}])
        zero = self.row(0)
        project_flow([tx, zero], provider=True)
        self.assertEqual(tx['flow']['basis'], 'reported')
        self.assertEqual(zero['flow']['direction'], 'none')
        self.assertEqual(zero['flow']['to'], 'No money movement')
