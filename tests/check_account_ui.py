"""Opt-in Chrome visual checks against an isolated copy of the Sandbox dataset.

Run with a Python installation containing websocket-client after npm run build.
Screenshots go to ignored data/ui-qa; no provider requests are made.
"""
import base64
from calendar import monthrange
from contextlib import closing
import json
from datetime import date, timedelta
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.request

import websocket

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'data' / 'ui-qa'
CHROME = Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')


def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def wait_for(predicate, label):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (OSError, ValueError):
            pass
        time.sleep(.1)
    raise AssertionError('Timed out: ' + label)


def run():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='finance-ui-') as temporary:
        folder = Path(temporary)
        database = folder / 'data' / 'finance.db'
        database.parent.mkdir()
        for suffix in ('.db', '.plaid-sandbox.db'):
            source = ROOT / 'data' / ('finance' + suffix)
            with closing(sqlite3.connect(f'file:{source.as_posix()}?mode=ro', uri=True)) as current, closing(sqlite3.connect(database.with_suffix(suffix))) as copy:
                current.backup(copy)
        database.with_suffix('.workspace').write_text('sandbox')
        with closing(sqlite3.connect(database.with_suffix('.plaid-sandbox.db'))) as db:
            assert db.execute('SELECT COUNT(*) FROM accounts WHERE active=1').fetchone()[0] == 14
            # Populate elapsed days and a comparison month on the copy only.
            test_month = date.today().isoformat()[:7]
            through = date.today().day
            db.execute("UPDATE transactions SET date=? || '-' || printf('%02d',? + rowid % ?)", (test_month, min(3, through), max(1, through - 2)))
            previous_month = (date.today().replace(day=1) - timedelta(days=1)).isoformat()[:7]
            db.execute("UPDATE transactions SET date=? || '-01' WHERE rowid IN (SELECT rowid FROM transactions ORDER BY rowid LIMIT 10)", (previous_month,))
            account = db.execute("SELECT id FROM accounts WHERE type='depository' LIMIT 1").fetchone()[0]
            db.execute("INSERT INTO transactions VALUES ('ui-refund',?,?,?,-12500,'USD','credit',0,0)", (account, test_month + '-02', 'Synthetic refund'))
            db.execute("INSERT INTO preview_edits VALUES ('ui-refund','Synthetic refund','refund',NULL,'UI fixture only')")
            card = db.execute("SELECT id FROM accounts WHERE type='credit' LIMIT 1").fetchone()[0]
            saving = db.execute("SELECT id FROM accounts WHERE subtype='savings' LIMIT 1").fetchone()[0]
            for identifier, acct, amount, kind in [('ui-pair-out', account, 11001, 'expense'), ('ui-pair-card', card, -11001, 'credit'), ('ui-pair-alternative', saving, -11001, 'credit')]:
                observed = test_month + f'-{date.today().day:02}'
                db.execute('INSERT INTO transactions VALUES (?,?,?,?,?,\'USD\',?,0,0)', (identifier, acct, observed, 'QA card payment ' + identifier, amount, kind))
                payload = {'date': observed, 'name': 'QA card payment ' + identifier, 'amount': amount / 100, 'iso_currency_code': 'USD', 'pending': False, 'personal_finance_category': {'primary': 'TRANSFER_OUT' if amount > 0 else 'TRANSFER_IN'}}
                db.execute('INSERT INTO transaction_details VALUES (?,?,?)', (identifier, json.dumps(payload), '2026-11-11T00:00:00Z'))
            db.execute("UPDATE transaction_details SET payload=json_set(payload,'$.date',(SELECT date FROM transactions WHERE transactions.id=transaction_details.transaction_id)) WHERE transaction_id IN (SELECT id FROM transactions)")
            db.commit()
        server_port, chrome_port = port(), port()
        server = subprocess.Popen([
            str(ROOT / '.venv/Scripts/python.exe'), '-c',
            'from pathlib import Path; import sys,uvicorn; from finance.api import create_app; '
            'uvicorn.run(create_app(Path(sys.argv[1])),host="127.0.0.1",port=int(sys.argv[2]),access_log=False)',
            str(database), str(server_port),
        ], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        chrome = None
        ws = None
        try:
            url = f'http://127.0.0.1:{server_port}'
            wait_for(lambda: urllib.request.urlopen(url, timeout=1).status == 200, 'server')
            chrome = subprocess.Popen([
                str(CHROME), '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                '--remote-allow-origins=*', f'--remote-debugging-port={chrome_port}',
                f'--user-data-dir={folder / "chrome"}', 'about:blank',
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            targets = wait_for(lambda: json.load(urllib.request.urlopen(f'http://127.0.0.1:{chrome_port}/json/list')), 'Chrome')
            ws = websocket.create_connection(next(item['webSocketDebuggerUrl'] for item in targets if item['type'] == 'page'), timeout=15)
            sequence = 0
            def cdp(method, params=None):
                nonlocal sequence
                sequence += 1
                number = sequence
                ws.send(json.dumps({'id': number, 'method': method, 'params': params or {}}))
                while True:
                    message = json.loads(ws.recv())
                    if message.get('id') == number:
                        if 'error' in message:
                            raise AssertionError(message['error'])
                        return message.get('result', {})

            def js(expression):
                result = cdp('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': True})
                if 'exceptionDetails' in result:
                    raise AssertionError(result['exceptionDetails'])
                return result.get('result', {}).get('value')

            def ready(expression):
                return wait_for(lambda: js('Boolean(' + expression + ')'), expression)

            def nav(name):
                js(f"[...document.querySelectorAll('nav button')].find(b=>b.textContent==={json.dumps(name)}).click()")
                ready(f"document.querySelector('h1')?.textContent==={json.dumps(name)}")

            def screenshot(name):
                time.sleep(.15)
                assert js('document.documentElement.scrollWidth <= innerWidth'), 'Horizontal overflow: ' + name
                image = cdp('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})
                (OUTPUT / (name + '.png')).write_bytes(base64.b64decode(image['data']))

            def change(selector, value):
                if selector.startswith('input'):
                    js(f"document.querySelector({json.dumps(selector)}).focus(); document.querySelector({json.dumps(selector)}).select()")
                    cdp('Input.insertText', {'text': value})
                    return
                js(f"(()=>{{const e=document.querySelector({json.dumps(selector)}); const proto=e instanceof HTMLSelectElement?HTMLSelectElement.prototype:HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(proto,'value').set.call(e,{json.dumps(value)}); e.dispatchEvent(new Event('input',{{bubbles:true}})); e.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")

            cdp('Page.enable')
            cdp('Runtime.enable')
            cdp('Page.navigate', {'url': url})
            ready("document.querySelector('.hero-value')?.textContent.includes('$')")
            results = []
            for width in (1920, 1280, 390):
                cdp('Emulation.setDeviceMetricsOverride', {'width': width, 'height': 1000 if width > 400 else 844, 'deviceScaleFactor': 1, 'mobile': False})
                for theme in ('dark', 'light'):
                    if js('document.documentElement.dataset.theme') != theme:
                        js("document.querySelector('.theme-toggle').click()")
                        ready(f"document.documentElement.dataset.theme==='{theme}' && !document.querySelector('.theme-toggle').disabled")
                    label = f'{width}-{theme}'
                    nav('Overview')
                    ready("document.querySelector('.account-picker')")
                    before = js("document.querySelector('.hero').getBoundingClientRect().height")
                    screenshot(label + '-overview')
                    js("document.querySelector('.net-worth-modes button:nth-child(2)').click()")
                    assert js("document.querySelector('#net-worth-title').textContent") == 'Assets'
                    js("document.querySelector('.net-worth-modes button:nth-child(3)').click()")
                    assert js("document.querySelector('#net-worth-title').textContent") == 'Debt'
                    js("document.querySelector('.net-worth-modes button:first-child').click()")
                    js("document.querySelector('.account-picker > summary').click()")
                    assert js("document.querySelector('.picker-scroll').clientHeight <= 301")
                    assert js("document.querySelector('.hero').getBoundingClientRect().height") == before
                    screenshot(label + '-selector')
                    js("document.querySelector('.picker-done').click()")
                    js("document.querySelector('.home-spending').scrollIntoView({block:'start'})")
                    screenshot(label + '-home-calendar')
                    assert js("document.querySelectorAll('.home-spending .calendar-day').length") == monthrange(date.today().year, date.today().month)[1]
                    js("document.querySelector('.asset-summary').scrollIntoView({block:'start'})")
                    js("document.querySelector('.asset-table-details > summary').click()")
                    screenshot(label + '-asset-table')
                    nav('Spending')
                    ready("document.querySelector('.metric-card')")
                    report = js("fetch('/api/plaid/sandbox/preview?month='+document.querySelector('input[type=month]').value).then(r=>r.json()).then(d=>d.spending)")
                    assert js("document.querySelector('.metric-card > strong').textContent") == js(f"new Intl.NumberFormat('en-US',{{style:'currency',currency:'USD'}}).format({report['net_minor']}/100)")
                    screenshot(label + '-spending')
                    js("document.querySelector('.analytics-grid').scrollIntoView({block:'start'})")
                    screenshot(label + '-breakdowns')
                    js("document.querySelector('.breakdown-row').click()")
                    ready("document.querySelector('.drilldown-filter')")
                    actual = js("[...document.querySelectorAll('.ledger-row')].map(row=>Number(row.dataset.transactionId))")
                    assert set(actual).issubset(set(report['bank_categories'][0]['transaction_ids']))
                    js("document.querySelector('.drilldown-filter button').click()")
                    assert not js("Boolean(document.querySelector('.drilldown-filter'))")
                    nav('Spending')
                    change('select[aria-label="Category source"]', 'local')
                    assert js("document.querySelector('select[aria-label=\"Category source\"]').value") == 'local'
                    js("[...document.querySelectorAll('[aria-label=\"Spending display\"] button')].find(b=>b.textContent==='Calendar').click()")
                    js("document.querySelector('.spending-main').scrollIntoView({block:'start'})")
                    screenshot(label + '-spending-calendar')
                    js("document.querySelector('.calendar-day:not(:disabled)').click()")
                    ready("document.querySelector('.drilldown-filter')")
                    assert 'Spending on' in js("document.querySelector('.drilldown-filter').textContent")
                    screenshot(label + '-day-drilldown')
                    nav('Accounts')
                    assert js("document.querySelectorAll('.account-group').length") == 5
                    assert js("[...document.querySelectorAll('.account-identity small')].some(e=>e.textContent.includes('Certificate of deposit'))")
                    screenshot(label + '-accounts')
                    if width == 1920 and theme == 'dark':
                        js("[...document.querySelectorAll('.account-summary')].find(e=>e.textContent.includes('Plaid Checking')).click()")
                        js("document.querySelector('.compact-account[open] .account-expanded details > summary').click()")
                        ready("document.querySelector('.compact-account[open] .balance-chart svg')")
                        screenshot(label + '-balance-history')
                        js("[...document.querySelectorAll('.compact-account[open] button')].find(b=>b.textContent.includes('View account transactions')).click()")
                        ready("document.querySelector('h1')?.textContent==='Transactions' && document.querySelector('input[type=month]').disabled")
                        assert js("document.querySelector('select[aria-label=\"Filter account\"]').value") != '0'
                        screenshot(label + '-account-transaction-shortcut')
                        nav('Accounts')
                    # Exercise collapsed loan group and its long account names.
                    js("[...document.querySelectorAll('.group-summary')].find(e=>e.textContent.includes('Loans &')).click()")
                    js("[...document.querySelectorAll('.account-summary')].find(e=>e.textContent.includes('Home Equity')).click()")
                    js("[...document.querySelectorAll('.account-summary')].find(e=>e.textContent.includes('Home Equity')).scrollIntoView({block:'center'})")
                    screenshot(label + '-loans')
                    nav('Transactions')
                    assert js("document.querySelectorAll('.ledger-row').length <= 25")
                    assert js("document.querySelectorAll('select[aria-label=\"Filter account\"] optgroup').length") == 5
                    ready("document.querySelector('select[aria-label=\"Review filter\"]')?.disabled===false")
                    screenshot(label + '-transactions')
                    ready("document.querySelector('.transfer-panel > summary')")
                    js("document.querySelector('.transfer-panel > summary').click()")
                    ready("document.querySelector('.transfer-match')")
                    transfer_data = js("fetch('/api/plaid/sandbox/preview/review/transfers').then(r=>r.json())")
                    transfer = next(item for item in transfer_data['suggestions'] if item['outgoing']['merchant'].endswith('ui-pair-out') and item['kind'] == 'card_payment')
                    selector = '.transfer-match[data-outgoing-id="' + str(transfer['outgoing']['id']) + '"][data-incoming-id="' + str(transfer['incoming']['id']) + '"]'
                    js("document.querySelector('.transfer-panel').scrollIntoView({block:'start'})")
                    screenshot(label + '-transfer-suggestions')
                    js('document.querySelector(' + json.dumps(selector) + ').querySelector("button.primary").click()')
                    ready("document.querySelector('dialog[open] #transfer-dialog-title')")
                    screenshot(label + '-transfer-confirmation')
                    js("document.querySelector('dialog .form-actions .primary').click()")
                    ready("!document.querySelector('dialog[open]')")
                    ready("document.querySelector('.transfer-panel > summary')?.textContent.includes('1 linked')")
                    js("[...document.querySelectorAll('[aria-label=\"Transfer match view\"] button')].find(b=>b.textContent==='Linked').click()")
                    screenshot(label + '-linked-transfer')
                    js("[...document.querySelectorAll('.transfer-panel .transfer-match button')].find(b=>b.textContent==='Unlink pair').click()")
                    ready("document.querySelector('dialog[open] #transfer-dialog-title')")
                    js("document.querySelector('dialog .form-actions .primary').click()")
                    ready("!document.querySelector('dialog[open]') && document.querySelector('.transfer-panel > summary')?.textContent.includes('0 linked')")
                    js("[...document.querySelectorAll('[aria-label=\"Transfer match view\"] button')].find(b=>b.textContent==='Suggestions').click()")
                    js('document.querySelector(' + json.dumps(selector) + ').querySelector("button.secondary").click()')
                    ready("document.querySelector('.transfer-panel > summary')?.textContent.includes('1 suggestions')")
                    js("[...document.querySelectorAll('[aria-label=\"Transfer match view\"] button')].find(b=>b.textContent==='Dismissed').click()")
                    screenshot(label + '-dismissed-transfer')
                    js("document.querySelector('.transfer-panel .transfer-match button').click()")
                    ready("document.querySelector('dialog[open] #transfer-dialog-title')")
                    js("document.querySelector('dialog .form-actions .primary').click()")
                    ready("!document.querySelector('dialog[open]') && document.querySelector('.transfer-panel > summary')?.textContent.includes('2 suggestions')")
                    js("document.querySelector('.transfer-panel > summary').click()")
                    change('select[aria-label="Review date scope"]', 'all')
                    change('select[aria-label="Review filter"]', 'needs_review')
                    assert js("document.querySelector('input[type=month]').disabled")
                    screenshot(label + '-all-history-review')
                    change('select[aria-label="Review filter"]', 'all')
                    change('select[aria-label="Review date scope"]', 'month')
                    # Classify one posted purchase via the full preview/confirmation flow.
                    ready("document.querySelector('.ledger-row .row-expense')")
                    first_id = js("[...document.querySelectorAll('.ledger-row')].find(row=>row.querySelector('.row-expense') && !row.textContent.includes('QA card payment')).dataset.transactionId")
                    first_selector = '.ledger-row[data-transaction-id="' + first_id + '"]'
                    first_merchant = js('document.querySelector(' + json.dumps(first_selector) + ').querySelector(".row-main strong").textContent')
                    js('document.querySelector(' + json.dumps(first_selector) + ').closest(".selectable-ledger-row").querySelector("input").click()')
                    js("[...document.querySelectorAll('.bulk-actions button')].find(b=>b.textContent==='Classify selected').click()")
                    ready("document.querySelector('dialog[open] select[aria-label=\"Bulk category\"]')")
                    change('select[aria-label="Bulk type"]', 'expense')
                    change('select[aria-label="Bulk category"]', '1')
                    js("document.querySelector('dialog form button[type=submit]').click()")
                    ready("document.querySelector('.review-preview') && !document.querySelector('dialog .form-actions button.primary').disabled")
                    screenshot(label + '-bulk-preview')
                    js("document.querySelector('dialog .form-actions button.primary').click()")
                    ready("!document.querySelector('dialog[open]') && document.querySelector('.notice[role=status]')")
                    ready("document.querySelector('select[aria-label=\"Review filter\"]')?.disabled===false")
                    change('select[aria-label="Review filter"]', 'reviewed')
                    ready("[...document.querySelectorAll('.ledger-row')].some(row=>row.dataset.transactionId===" + json.dumps(first_id) + ")")
                    screenshot(label + '-reviewed-filter')
                    change('select[aria-label="Review filter"]', 'all')
                    js("[...document.querySelectorAll('.transaction-review button')].find(b=>b.textContent==='New merchant rule').click()")
                    ready("document.querySelector('dialog[open] input[aria-label=\"Rule merchant\"]')")
                    change('input[aria-label="Rule merchant"]', first_merchant)
                    change('select[aria-label="Bulk category"]', '2')
                    assert not js("document.querySelector('input[aria-label=\"Apply rule to future imports\"]').checked")
                    js("document.querySelector('dialog form button[type=submit]').click()")
                    ready("document.querySelector('.review-preview')")
                    screenshot(label + '-merchant-rule-preview')
                    if width == 1920 and theme == 'dark':
                        js("document.querySelector('input[aria-label=\"Apply rule to future imports\"]').click()")
                        js("document.querySelector('dialog form button[type=submit]').click()")
                        ready("document.querySelector('.review-preview') && !document.querySelector('dialog .form-actions button.primary').disabled")
                        js("document.querySelector('dialog .form-actions button.primary').click()")
                        ready("!document.querySelector('dialog[open]') && document.querySelector('.merchant-rules')")
                        js("document.querySelector('.merchant-rules > summary').click()")
                        screenshot(label + '-merchant-rules')
                    else:
                        js("document.querySelector('button[aria-label=\"Close review dialog\"]').click()")
                    if width == 1920 and theme == 'dark':
                        first = js("document.querySelector('.ledger-row').textContent")
                        js("[...document.querySelectorAll('.pagination button')].find(e=>e.textContent==='Next').click()")
                        assert js("document.querySelector('.ledger-row').textContent") != first
                    js("document.querySelector('.ledger-row').click()")
                    ready("document.querySelector('dialog[open] .transaction-details')")
                    assert not js("document.querySelector('.transaction-details').open")
                    screenshot(label + '-dialog')
                    js("document.querySelector('.transaction-details > summary').click()")
                    ready("document.querySelector('.transaction-fields')")
                    assert not js("document.querySelector('.transaction-fields').textContent.includes('Not supplied')")
                    js("document.querySelector('.transaction-details details > summary').click()")
                    screenshot(label + '-provider')
                    bounds = js("(()=>{const d=document.querySelector('dialog'),b=d.querySelector('button[type=submit]').getBoundingClientRect(),r=d.getBoundingClientRect();return {save:b.bottom<=r.bottom+1 && b.top>=r.top, fits:d.scrollWidth<=d.clientWidth+1}})()")
                    assert bounds['save'] and bounds['fits'], str(bounds)
                    js("document.querySelector('button[aria-label=Close]').click()")
                    nav('Bank sync')
                    ready("document.querySelectorAll('.sync-scroll').length===2")
                    screenshot(label + '-sync')
                    results.append(label + ': all layouts fit; groups, modal, selector and pagination verified')

            # Group selection, persistence, search, and mortgage empty state on the copy only.
            nav('Overview')
            ready("document.querySelector('.account-picker > summary')")
            js("document.querySelector('.account-picker > summary').click()")
            js("document.querySelector('.picker-group legend input').click()")
            ready("!document.querySelector('.picker-group legend input').disabled")
            selected = js("document.querySelector('.account-picker > summary').textContent")
            assert '9 of 14' in selected, selected
            cdp('Page.reload')
            ready("document.querySelector('.account-picker > summary')?.textContent.includes('9 of 14')")
            nav('Transactions')
            mortgage = js("[...document.querySelectorAll('select[aria-label=\"Filter account\"] option')].find(o=>o.textContent==='Plaid Mortgage').value")
            change('select[aria-label="Filter account"]', mortgage)
            ready("document.querySelector('.empty')?.textContent.includes('No spending transactions have been saved')")
            screenshot('390-light-mortgage-empty')
            nav('Accounts')
            change('input[aria-label="Search accounts"]', 'Plaid CD')
            ready("document.querySelectorAll('.compact-account').length===1")
            # Personal workspace remains usable with the grouped component.
            change('select[aria-label="Workspace"]', 'personal')
            ready("document.querySelector('h2')?.textContent==='Manual accounts'")
            nav('Spending')
            ready("document.querySelector('.spending-page .empty')")
            assert js("document.querySelector('.metric-card > strong').textContent") == '$0.00'
            screenshot('390-light-personal-empty-spending')
            # Verify that a refund-only month renders below zero without fabricating spending.
            with closing(sqlite3.connect(database.with_suffix('.plaid-sandbox.db'))) as db:
                db.execute("UPDATE transactions SET removed=1 WHERE id<>'ui-refund' AND date LIKE ?", (test_month + '%',))
                db.commit()
            change('select[aria-label="Workspace"]', 'sandbox')
            ready("document.querySelector('.metric-card > strong')?.textContent==='-$125.00'")
            cdp('Emulation.setDeviceMetricsOverride', {'width': 1280, 'height': 1000, 'deviceScaleFactor': 1, 'mobile': False})
            js("document.querySelector('.theme-toggle').click()")
            ready("document.documentElement.dataset.theme==='dark'")
            screenshot('1280-dark-refund-only-spending')
            print('\n'.join(results))
            print('Selection persistence, account search, spending drilldowns, refund-only charts, empty states and Personal isolation verified.')
            print('Screenshots:', OUTPUT)
        finally:
            if ws:
                ws.close()
            for process in (chrome, server):
                if process:
                    process.terminate()
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()


if __name__ == '__main__':
    run()
