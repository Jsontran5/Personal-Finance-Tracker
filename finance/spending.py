"""Read-only spending projections shared by manual and imported ledgers."""
from calendar import monthrange
from datetime import date, timedelta


def eligible_spending(tx):
    return (tx['currency'] == 'USD' and not tx.get('pending') and not tx.get('needs_review') and not tx.get('transfer_pair_id')
            and tx['kind'] in {'expense', 'refund'})


def previous_month(month):
    first = date.fromisoformat(month + '-01')
    return (first - timedelta(days=1)).isoformat()[:7] if first > date.min else None


def spending_report(transactions, previous, month, today):
    first = date.fromisoformat(month + '-01')
    days = monthrange(first.year, first.month)[1]
    through = days if month < today.isoformat()[:7] else today.day if month == today.isoformat()[:7] else 0
    prior_month = previous_month(month)
    prior_days = monthrange(int(prior_month[:4]), int(prior_month[5:]))[1] if prior_month else 0
    comparison_days = min(through, prior_days) if month >= today.isoformat()[:7] else prior_days
    current = [tx for tx in transactions if eligible_spending(tx) and tx['date'][:7] == month and int(tx['date'][8:]) <= through]
    prior = [tx for tx in previous if eligible_spending(tx) and tx['date'][:7] == prior_month and int(tx['date'][8:]) <= comparison_days]

    def signed(tx):
        return tx['amount_minor'] * (-1 if tx['kind'] == 'refund' else 1)

    def breakdown(source):
        groups = {}
        for tx in current:
            if source == 'merchant':
                label = ' '.join(tx['merchant'].split()) or 'Unknown merchant'
                key = label.casefold()
            elif source == 'bank':
                raw = tx.get('bank_category')
                key = raw or 'uncategorized'
                label = raw.replace('_', ' ').capitalize() if raw else 'Uncategorized'
            else:
                key = str(tx.get('category_id') or 'uncategorized')
                label = tx.get('category_name') or 'Uncategorized'
            group = groups.setdefault(key, {'key': key, 'label': label, 'purchases_minor': 0,
                                            'refunds_minor': 0, 'net_minor': 0, 'transaction_ids': []})
            group['refunds_minor' if tx['kind'] == 'refund' else 'purchases_minor'] += tx['amount_minor']
            group['net_minor'] += signed(tx)
            group['transaction_ids'].append(tx['id'])
        return sorted(groups.values(), key=lambda group: (-abs(group['net_minor']), group['label']))

    cumulative = prior_cumulative = 0
    daily = []
    for number in range(1, days + 1):
        entries = [tx for tx in current if int(tx['date'][8:]) == number]
        purchases = sum(tx['amount_minor'] for tx in entries if tx['kind'] == 'expense')
        refunds = sum(tx['amount_minor'] for tx in entries if tx['kind'] == 'refund')
        cumulative += purchases - refunds
        prior_cumulative += sum(signed(tx) for tx in prior if int(tx['date'][8:]) == number)
        daily.append({'day': number, 'date': f'{month}-{number:02}', 'purchases_minor': purchases,
                      'refunds_minor': refunds, 'net_minor': purchases - refunds if number <= through else None,
                      'cumulative_minor': cumulative if number <= through else None,
                      'previous_cumulative_minor': prior_cumulative if prior and number <= comparison_days else None,
                      'transaction_ids': [tx['id'] for tx in entries]})
    purchases = sum(tx['amount_minor'] for tx in current if tx['kind'] == 'expense')
    refunds = sum(tx['amount_minor'] for tx in current if tx['kind'] == 'refund')
    net = purchases - refunds
    previous_net = sum(signed(tx) for tx in prior) if prior else None
    comparison = []
    running = 0
    if prior:
        for number in range(1, comparison_days + 1):
            running += sum(signed(tx) for tx in prior if int(tx['date'][8:]) == number)
            comparison.append({'day': number, 'cumulative_minor': running})
    return {'month': month, 'days': days, 'through_day': through, 'comparison_month': prior_month,
            'comparison_days': comparison_days, 'purchases_minor': purchases, 'refunds_minor': refunds,
            'net_minor': net, 'average_minor': round(net / through) if through else None,
            'previous_minor': previous_net, 'change_minor': net - previous_net if previous_net is not None else None,
            'change_percent': (net - previous_net) / previous_net * 100 if previous_net and previous_net > 0 else None,
            'daily': daily, 'comparison_daily': comparison, 'eligible_ids': [tx['id'] for tx in current],
            'categories': breakdown('local'), 'bank_categories': breakdown('bank'), 'merchants': breakdown('merchant')}
