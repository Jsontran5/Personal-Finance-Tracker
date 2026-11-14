"""Manual debt terms and broker-reported liabilities, independent of balances."""
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .db import connect
from .investments import money_minor

Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]
Amount = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000_000)]
DebtSubtype = Literal['student', 'mortgage', 'auto', 'consumer', 'credit card', 'line of credit', 'other']


class ManualDebt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    subtype: DebtSubtype = 'other'
    servicer: Text = ''
    apr_percent: Annotated[Decimal, Field(ge=0, le=1000, max_digits=8, decimal_places=4)] | None = None
    rate_type: Literal['fixed', 'variable', 'unknown'] = 'unknown'
    original_principal_minor: Amount | None = None
    principal_balance_minor: Amount | None = None
    outstanding_interest_minor: Amount | None = None
    minimum_payment_minor: Amount | None = None
    last_payment_minor: Amount | None = None
    last_payment_date: date | None = None
    statement_balance_minor: Amount | None = None
    statement_date: date | None = None
    credit_limit_minor: Amount | None = None
    available_credit_minor: Amount | None = None
    origination_date: date | None = None
    maturity_date: date | None = None
    repayment_plan: Text = ''
    loan_status: Text = ''
    notes: Annotated[str, StringConstraints(max_length=2000)] = ''

    @model_validator(mode='after')
    def valid_dates(self):
        for value in [self.last_payment_date, self.statement_date, self.origination_date]:
            if value and value > date.today():
                raise ValueError('Payment, statement and origination dates must be today or earlier.')
        if self.origination_date and self.maturity_date and self.maturity_date < self.origination_date:
            raise ValueError('Maturity date must be on or after origination.')
        return self


def initialize_manual_debt(db):
    db.execute('''CREATE TABLE IF NOT EXISTS manual_debt_details (
        account_id INTEGER PRIMARY KEY REFERENCES accounts(id),payload TEXT NOT NULL,updated_at TEXT NOT NULL)''')


def initialize_liabilities(db):
    db.execute('''CREATE TABLE IF NOT EXISTS liability_details (
        account_id TEXT PRIMARY KEY REFERENCES accounts(id),category TEXT NOT NULL,
        payload TEXT NOT NULL,balances TEXT NOT NULL,retrieved_at TEXT NOT NULL)''')
    db.execute('CREATE TABLE IF NOT EXISTS liability_state (id INTEGER PRIMARY KEY CHECK(id=1),synced_at TEXT NOT NULL)')


def manual_debt_router(database):
    router = APIRouter(prefix='/api/accounts')

    def eligible(db, identifier):
        account = db.execute('SELECT kind FROM accounts WHERE id=?', (identifier,)).fetchone()
        if not account:
            raise HTTPException(404, 'Account not found.')
        if account['kind'] not in {'loan', 'credit'}:
            raise HTTPException(422, 'Debt details belong to loan or credit accounts.')
        return account

    @router.get('/{identifier}/debt')
    def details(identifier: int):
        with connect(database) as db:
            account = eligible(db, identifier)
            row = db.execute('SELECT * FROM manual_debt_details WHERE account_id=?', (identifier,)).fetchone()
            fields = ManualDebt.model_validate_json(row['payload']) if row else ManualDebt(subtype='credit card' if account['kind'] == 'credit' else 'other')
            return {'source': 'manual', 'fields': fields.model_dump(mode='json'), 'updated_at': row['updated_at'] if row else None}

    @router.put('/{identifier}/debt')
    def save(identifier: int, item: ManualDebt):
        with connect(database, write=True) as db:
            account = eligible(db, identifier)
            if account['kind'] == 'loan' and item.subtype == 'credit card':
                raise HTTPException(422, 'Choose a loan type for this account.')
            if account['kind'] == 'credit' and item.subtype not in {'credit card', 'line of credit', 'other'}:
                raise HTTPException(422, 'Choose a credit card or credit line type for this account.')
            stamp = datetime.now(timezone.utc).isoformat()
            db.execute('''INSERT INTO manual_debt_details VALUES (?,?,?)
                ON CONFLICT(account_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at''',
                       (identifier, item.model_dump_json(), stamp))
        return {'source': 'manual', 'fields': item.model_dump(mode='json'), 'updated_at': stamp}

    return router


def save_liabilities(db, response, retrieved_at):
    accounts = {item['account_id']: item for item in response['accounts']}
    rows = []
    for category in ['credit', 'student', 'mortgage', 'loan', 'line_of_credit']:
        for supplied in (response.get('liabilities') or {}).get(category) or []:
            identifier = supplied['account_id']
            if identifier not in accounts or accounts[identifier]['type'] not in {'credit', 'loan'}:
                raise ValueError('A liability has no matching debt account.')
            # These identifiers are unnecessary for tracking terms and payments.
            payload = {key: value for key, value in supplied.items() if key not in {'account_number', 'payment_reference_number'}}
            rows.append((identifier, category, json.dumps(payload, default=str, ensure_ascii=False),
                         json.dumps(accounts[identifier]['balances'], default=str), retrieved_at))
    db.execute('DELETE FROM liability_details')
    db.executemany('INSERT INTO liability_details VALUES (?,?,?,?,?)', rows)
    db.execute('INSERT INTO liability_state VALUES (1,?) ON CONFLICT(id) DO UPDATE SET synced_at=excluded.synced_at', (retrieved_at,))


def project_liability(row, currency):
    if not row:
        return {'source': 'plaid', 'fields': None, 'updated_at': None, 'provider': None, 'aprs': []}
    record, balances = json.loads(row['payload']), json.loads(row['balances'])
    currency = balances.get('iso_currency_code')
    rate = record.get('interest_rate') or {}
    fields = {'subtype': row['category'], 'servicer': record.get('servicer_name') or '',
              'apr_percent': record.get('interest_rate_percentage') if row['category'] == 'student' else rate.get('percentage'),
              'rate_type': rate.get('type') or 'unknown',
              'last_payment_date': record.get('last_payment_date'), 'statement_date': record.get('last_statement_issue_date'),
              'origination_date': record.get('origination_date'), 'maturity_date': record.get('maturity_date'),
              'repayment_plan': (record.get('repayment_plan') or {}).get('description') or (record.get('repayment_plan') or {}).get('type') or '',
              'loan_status': (record.get('loan_status') or {}).get('type') or '', 'notes': ''}
    for key, source in [('original_principal_minor', 'origination_principal_amount'), ('principal_balance_minor', 'principal_balance'),
                        ('outstanding_interest_minor', 'outstanding_interest_amount'), ('minimum_payment_minor', 'minimum_payment_amount'),
                        ('last_payment_minor', 'last_payment_amount'), ('statement_balance_minor', 'last_statement_balance')]:
        fields[key] = money_minor(record.get(source), currency)
    limit = record.get('credit_limit')
    available = record.get('available_credit')
    if row['category'] == 'credit':
        limit = limit if limit is not None else balances.get('limit')
        available = available if available is not None else balances.get('available')
    fields['credit_limit_minor'] = money_minor(limit, currency)
    fields['available_credit_minor'] = money_minor(available, currency)
    aprs = [{'percent': item.get('apr_percentage'), 'type': item.get('apr_type'),
             'balance_minor': money_minor(item.get('balance_subject_to_apr'), currency),
             'interest_minor': money_minor(item.get('interest_charge_amount'), currency)} for item in record.get('aprs') or []]
    return {'source': 'plaid', 'fields': fields, 'updated_at': row['retrieved_at'], 'provider': record,
            'aprs': aprs, 'is_overdue': record.get('is_overdue'), 'currency': currency}
