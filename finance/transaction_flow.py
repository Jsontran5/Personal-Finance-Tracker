"""Display-only money direction; never infer a match between unlinked accounts."""
import json
import re


def project_flow(transactions, provider=False):
    for tx in transactions:
        signed = tx.pop('_flow_amount_minor', None)
        payload = tx.pop('_flow_payload', None)
        data = json.loads(payload) if payload else {}
        if not isinstance(data, dict):
            data = {}
        if provider:
            direction = 'in' if signed < 0 else 'out' if signed > 0 else 'none'
        else:
            direction = 'none' if not tx['amount_minor'] else 'in' if tx['kind'] in {'income', 'refund'} else 'out'
        if tx.get('transfer_role'):
            direction = 'in' if tx['transfer_role'] == 'incoming' else 'out'
        own = tx['account_name']
        other = None
        basis = 'unavailable'
        if tx.get('transfer_pair_id'):
            other, basis = tx['transfer_other_account'], 'confirmed'
        elif tx.get('destination_name'):
            other, basis = tx['destination_name'], 'manual'
        else:
            meta = data.get('payment_meta') or {}
            meta = meta if isinstance(meta, dict) else {}
            other = meta.get('payer' if direction == 'in' else 'payee')
            other = other.strip() if isinstance(other, str) else None
            if not other:
                counterparties = data.get('counterparties') or []
                names = {c['name'].strip() for c in counterparties if isinstance(c, dict) and isinstance(c.get('name'), str) and c['name'].strip() and c.get('type') != 'payment_terminal'}
                other = next(iter(names)) if len(names) == 1 else data.get('merchant_name')
            other = other.strip() if isinstance(other, str) else None
            if other:
                basis = 'reported'
            else:
                # Preserve the bank description as a description, not a guessed
                # account identity. Only remove an explicit direction prefix.
                description = tx.get('source_merchant') or tx.get('merchant') or ''
                pattern = r'^from\s+' if direction == 'in' else r'^to\s+'
                if description.strip() and (not provider or data.get('_finance_manual') or re.match(pattern, description, flags=re.IGNORECASE)):
                    other = re.sub(pattern, '', description, flags=re.IGNORECASE).strip()
                    basis = 'description'
        if (not provider or data.get('_finance_manual')) and basis != 'confirmed':
            basis = 'manual'
        tx['flow'] = {'direction': direction, 'from': other or 'Source not supplied' if direction == 'in' else own,
                      'to': own if direction == 'in' else other or 'Destination not supplied', 'basis': basis}
        if direction == 'none':
            tx['flow'].update({'from': own, 'to': 'No money movement'})
    return transactions
