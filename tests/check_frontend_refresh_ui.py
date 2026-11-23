"""Run browser QA or serve a disposable synthetic preview from this worktree."""
import argparse
import base64
from contextlib import contextmanager
from datetime import date, timedelta
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / 'data/ui-qa/frontend-refresh'
SCRIPT = Path(__file__).resolve()


@contextmanager
def preview_environment():
    settings = {key: '' for key in os.environ if key.startswith('PLAID_')}
    settings.update(APP_ENV='production', APP_STARTUP_ENABLED='false', PLAID_LIVE_ENABLED='false')
    with patch.dict(os.environ, settings), patch('finance.plaid_sandbox.urlopen', side_effect=AssertionError('Provider access is disabled in the fixture')):
        yield


def seed(database):
    from fastapi.testclient import TestClient
    from finance.api import create_app
    from finance.db import connect
    from finance.live_setup import initialize_live
    from finance.multi_bank import map_accounts, scoped, transaction_page
    from finance.sandbox_preview import record_preview_snapshot
    from tests.seed_investment_ui import seed as seed_investments

    today = date.today()
    month = today.isoformat()[:7]
    first = today.replace(day=1)
    source = database.with_suffix('.plaid-live.db')
    with TestClient(create_app(database), base_url='http://127.0.0.1', headers={'X-Finance-Request': '1'}) as client:
        def write(path, values, method='POST'):
            result = client.request(method, '/api' + path, json=values)
            assert result.is_success, (path, result.text)
            return result.json()

        cash = write('/accounts', dict(name='Household checking', kind='checking', opening_balance_minor=724000, date=first.isoformat()))['id']
        write('/accounts', dict(name='Student loan · fixed rate', kind='loan', debt_subtype='student', opening_balance_minor=587000, date=first.isoformat()))
        categories = client.get('/api/plan?month=' + month).json()['categories']
        ids = {category['name']: category['id'] for category in categories}
        for index in range(30):
            write('/categories', dict(name=f'Household supplies and recurring purchases for shared spaces {index + 1}', kind='expense'))
        for day, merchant, amount, kind, category in [
            (1, 'Monthly payroll', 510000, 'income', None),
            (1, 'Maple Court Rent', 153000, 'expense', 'Housing'),
            (2, 'Grocery refund', 3500, 'refund', 'Groceries'),
            (3, 'Juniper Market', 12775, 'expense', 'Groceries'),
            (4, 'Cedar & Salt Cafe', 4850, 'expense', 'Dining'),
            (7, 'North Coast Electric', 8380, 'expense', 'Utilities'),
            (9, 'Harbor Market and International Groceries', 9458, 'expense', 'Groceries'),
            (12, 'Sunday Coffee', 1840, 'expense', 'Dining'),
            (16, 'Harbor Market', 11452, 'expense', 'Groceries'),
            (17, 'Train tickets', 3820, 'expense', None),
            (19, 'Maple Table', 6790, 'expense', 'Dining'),
        ]:
            write('/transactions', dict(account_id=cash, kind=kind, merchant=merchant, amount_minor=amount,
                                        category_id=ids[category] if category else None, date=first.replace(day=min(day, today.day)).isoformat()))
        write('/templates', dict(account_id=cash, kind='expense', name='Monthly rent', merchant='Maple Court Rent', amount_minor=153000, category_id=ids['Housing']))

    with connect(source, write=True) as db:
        initialize_live(db)
        db.executemany('INSERT INTO multi_connections(id,institution_id,name,profile) VALUES (?,?,?,?)', [
            (1, 'fixture-depository', 'Cedar Community Credit Union', 'banking'),
            (2, 'fixture-investments', 'Harbor Investment Services', 'investments'),
        ])
        bank_accounts = [
            dict(account_id=str(index), name=f'Long-term household savings and upcoming expenses · Account {index + 1}',
                 type='depository', subtype='savings', balances={'current': 400 + index * 100, 'iso_currency_code': 'USD'})
            for index in range(18)
        ]
        map_accounts(db, 1, bank_accounts + [
            dict(account_id='card', name='Everyday Rewards Card', type='credit', subtype='credit card', balances={'current': 482.21, 'iso_currency_code': 'USD'}),
        ])
        map_accounts(db, 2, [
            dict(account_id='brokerage', name='Long-term brokerage portfolio', type='investment', subtype='brokerage', balances={'current': 28957.42, 'iso_currency_code': 'USD'}),
        ])
        transaction_page(db, 1, [{
            'transaction_id': 'fixture-purchase', 'account_id': 'card', 'date': today.isoformat(),
            'name': 'Sample bookstore purchase', 'amount': 25.12, 'iso_currency_code': 'USD', 'pending': False,
            'personal_finance_category': {'primary': 'GENERAL_MERCHANDISE'},
        }], [], [])
    seed_investments(source)
    with TestClient(create_app(database), base_url='http://127.0.0.1', headers={'X-Finance-Request': '1'}) as client:
        assert client.get('/api/plaid/live/preview', params={'month': month}).is_success
        plan_path = '/api/plaid/live/preview/plan'
        plan = client.get(plan_path, params={'month': month}).json()
        active_ids = {category['name']: category['id'] for category in plan['categories']}
        for name, limit in [('Groceries', 72000), ('Dining', 42000), ('Housing', 180000)]:
            response = client.put(f'/api/plaid/live/preview/budgets/{month}/{active_ids[name]}', json={'limit_minor': limit})
            assert response.is_success, response.text
        assert client.put(f'{plan_path}/categories/{active_ids["Dining"]}', json={'bucket': 'wants', 'group': 'Everyday spending'}).is_success
        assert client.post('/api/backups', json={'workspace': 'personal'}).is_success
    with connect(source, write=True) as db:
        for index in range(15):
            snapshot = record_preview_snapshot(db)
            observed = (today - timedelta(days=14 - index)).isoformat()
            db.execute('UPDATE preview_snapshots SET date=? WHERE id=?', (observed, snapshot))
            db.execute('UPDATE preview_snapshot_accounts SET checked_at=? WHERE snapshot_id=?', (observed + 'T12:00:00', snapshot))
            db.execute('UPDATE preview_snapshot_accounts SET balance_minor=balance_minor-? WHERE snapshot_id=? AND account_id=?',
                       ((14 - index) * 17000 + index % 3 * 9000, snapshot, scoped(2, 'brokerage')))


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def serve(port, folder):
    import uvicorn
    from finance.api import create_app

    with preview_environment():
        database = Path(folder) / 'data/finance.db'
        seed(database)
        print(f'Synthetic preview at http://127.0.0.1:{port}', flush=True)
        uvicorn.run(create_app(database), host='127.0.0.1', port=port, access_log=False)


def wait_for(check, description):
    deadline = time.monotonic() + 16
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except OSError:
            pass
        time.sleep(.08)
    raise AssertionError('Timed out waiting for ' + description)


def contrast(first, second):
    def luminance(color):
        color = color.lstrip('#')
        if len(color) == 3:
            color = ''.join(channel * 2 for channel in color)
        channels = [int(color[index:index + 2], 16) / 255 for index in (0, 2, 4)]
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
        return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))

    low, high = sorted((luminance(first), luminance(second)))
    return (high + .05) / (low + .05)


@contextmanager
def process(*args):
    child = subprocess.Popen(args, cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        yield
    finally:
        child.terminate()
        try:
            child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def browser_check(folder):
    import websocket

    port, cdp_port = free_port(), free_port()
    url = f'http://127.0.0.1:{port}'
    chrome = Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe'
    browser_options = ('--headless=new', '--disable-gpu', '--no-first-run', '--remote-allow-origins=*',
                       f'--remote-debugging-port={cdp_port}', f'--user-data-dir={Path(folder) / "chrome"}', 'about:blank')
    with process(sys.executable, str(SCRIPT), '--serve', str(port), '--folder', folder):
        wait_for(lambda: urllib.request.urlopen(url, timeout=1).status == 200, 'fixture server')
        with process(str(chrome), *browser_options):
            targets = []

            def discover():
                with urllib.request.urlopen(f'http://127.0.0.1:{cdp_port}/json/list') as response:
                    targets.extend(target for target in json.load(response) if target['type'] == 'page')
                return targets

            wait_for(discover, 'Chrome')
            browser = websocket.create_connection(targets[0]['webSocketDebuggerUrl'], timeout=20)
            try:
                exercise(browser, url)
            finally:
                browser.close()


def exercise(browser, url):
    command_id = 0
    runtime_errors = []

    def cdp(method, **params):
        nonlocal command_id
        command_id += 1
        browser.send(json.dumps({'id': command_id, 'method': method, 'params': params}))
        while True:
            message = json.loads(browser.recv())
            if message.get('method') == 'Runtime.exceptionThrown':
                runtime_errors.append(message['params']['exceptionDetails'])
            if message.get('id') == command_id:
                assert 'error' not in message, message
                return message.get('result', {})

    def js(expression):
        result = cdp('Runtime.evaluate', expression=expression, returnByValue=True, awaitPromise=True)
        assert 'exceptionDetails' not in result, result
        return result['result'].get('value')

    def ready(expression):
        try:
            wait_for(lambda: js(f'Boolean({expression})'), expression)
        except AssertionError as error:
            raise AssertionError(f'{error}\n{js("document.body.innerText.slice(0, 1600)")}\n{runtime_errors}') from error

    def click(selector, label=None):
        match = '' if label is None else f' && el.textContent.trim().startsWith({json.dumps(label)})'
        js(f'[...document.querySelectorAll({json.dumps(selector)})].find(el => el.getClientRects().length{match}).click()')

    def key(name, code, virtual_key):
        text = {'Enter': '\r', ' ': ' '}.get(name)
        cdp('Input.dispatchKeyEvent', type='keyDown', key=name, code=code, windowsVirtualKeyCode=virtual_key, **({'text': text} if text else {}))
        cdp('Input.dispatchKeyEvent', type='keyUp', key=name, code=code, windowsVirtualKeyCode=virtual_key)

    def search(selector, value):
        js(f'''(() => {{
          const input = document.querySelector({json.dumps(selector)});
          Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, {json.dumps(value)});
          input.dispatchEvent(new Event('input', {{ bubbles: true }}));
        }})()''')

    def nav(page):
        label = json.dumps(page)
        if not js(f"[...document.querySelectorAll('nav button')].some(el => el.getClientRects().length && el.textContent.trim() === {label})"):
            click('[aria-label="More sections"]')
            ready("document.querySelector('.navigation-sheet[open]')")
        click('nav button', page)
        ready(f'document.querySelector("h1")?.textContent === {label} && !document.querySelector("dialog[open]")')
        js('window.scrollTo(0, 0)')

    def shot(name):
        time.sleep(.16)
        assert js('document.documentElement.scrollWidth <= document.documentElement.clientWidth'), (name, js("""
          [...document.querySelectorAll('body *')].filter(el => el.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
            .slice(0, 12).map(el => [el.tagName, el.className, Math.round(el.getBoundingClientRect().right)])
        """))
        assert js("[...document.querySelectorAll('dialog[open]')].every(el => el.scrollWidth <= el.clientWidth + 1)"), name
        data = cdp('Page.captureScreenshot', format='png', captureBeyondViewport=False)['data']
        (OUTPUT / (name + '.png')).write_bytes(base64.b64decode(data))

    selectors = {
        'Overview': '.hero .chart',
        'Spending': '.spending-main .spending-svg',
        'Transactions': '.transaction-workspace .ledger-row',
        'Budgets': '.budget-monthly',
        'Accounts': '.account-summary',
        'Investments': '.investment-row',
        'Bank sync': '.connection-card',
        'Backup & restore': '.backup-row',
    }
    cdp('Runtime.enable')
    cdp('Page.enable')
    cdp('Page.navigate', url=url)
    cdp('Page.bringToFront')
    ready("document.querySelector('.hero .chart')")
    OUTPUT.mkdir(parents=True, exist_ok=True)

    for width in (1440, 1024, 768, 390, 320):
        cdp('Emulation.setDeviceMetricsOverride', width=width, height=1000 if width > 850 else 844, deviceScaleFactor=1, mobile=False)
        for theme in ('light', 'dark'):
            if js('document.documentElement.dataset.theme') != theme:
                click('.theme-toggle')
                ready(f'document.documentElement.dataset.theme === "{theme}" && !document.querySelector(".theme-toggle").disabled')
            palette = js("""Object.fromEntries(['text', 'muted', 'surface', 'accent', 'accent-ink', 'control-line', 'field', 'rail-muted', 'rail', 'rail-text', 'rail-active', 'rail-selected', 'rail-selected-ink']
                .map(name => [name, getComputedStyle(document.documentElement).getPropertyValue('--' + name).trim()]))""")
            for foreground, background, minimum in [
                ('text', 'surface', 4.5), ('muted', 'surface', 4.5), ('accent-ink', 'accent', 4.5),
                ('control-line', 'field', 3), ('accent', 'surface', 3), ('rail-muted', 'rail', 4.5), ('rail-text', 'rail-active', 4.5),
                ('rail-selected-ink', 'rail-selected', 4.5), ('rail-selected', 'rail', 3),
            ]:
                assert contrast(palette[foreground], palette[background]) >= minimum, (theme, foreground, background)
            for page, selector in selectors.items():
                nav(page)
                ready(f'document.querySelector({json.dumps(selector)})')
                if page == 'Budgets':
                    assert js("""(() => {
                        const meter = document.querySelector('.budget-guide [role=meter]');
                        const track = meter?.querySelector('.finance-meter-track')?.getBoundingClientRect();
                        const fill = meter?.querySelector('.finance-meter-fill')?.getBoundingClientRect();
                        return meter?.getAttribute('aria-label') && track?.height >= 6 && fill?.width > 0 && fill.width < track.width;
                    })()"""), (width, theme, 'budget meter fill')
                shot(f'{width}-{theme}-' + page.lower().replace(' & ', '-').replace(' ', '-'))

            if width in (1440, 390):
                nav('Overview')
                ready("document.querySelector('.account-picker-trigger')")
                js("document.querySelector('.account-picker-trigger').scrollIntoView({block:'center'})")
                js("document.querySelector('.account-picker-trigger').focus()")
                key('Enter', 'Enter', 13)
                ready("document.querySelector('.account-options:popover-open')")
                ready("""(() => {
                    const panel = document.querySelector('.account-options:popover-open');
                    const list = panel.querySelector('.picker-scroll');
                    const body = panel.getBoundingClientRect();
                    const covers = (x, y) => panel.contains(document.elementFromPoint(x, y));
                    return body.left >= -1 && body.right <= innerWidth + 1 && body.bottom <= innerHeight + 1 &&
                        body.top >= -1 && getComputedStyle(panel).backgroundColor.startsWith('rgb(') &&
                        list.scrollHeight > list.clientHeight &&
                        [16, body.height / 2, body.height - 16].every(y => covers(body.left + body.width / 2, body.top + y));
                })()""")
                shot(f'{width}-{theme}-chart-accounts')
                key('Escape', 'Escape', 27)
                ready("!document.querySelector('.account-options:popover-open') && document.activeElement.matches('.account-picker-trigger')")

                js("window.scrollTo(0, 0); document.querySelector('.month-picker-trigger').focus()")
                key('Enter', 'Enter', 13)
                ready("document.querySelector('.month-picker-panel:popover-open')")
                assert js("document.activeElement.matches('.month-picker-cell[aria-current=date]') && getComputedStyle(document.activeElement).outlineStyle === 'solid'")
                shot(f'{width}-{theme}-reporting-month')
                original_month = js("document.querySelector('.month-picker-trigger').dataset.month")
                click('.month-picker-year button[aria-label="Previous year"]')
                assert js("document.querySelector('.month-picker-year strong').textContent") == str(int(original_month[:4]) - 1)
                key('Escape', 'Escape', 27)
                ready("!document.querySelector('.month-picker-panel:popover-open') && document.activeElement.matches('.month-picker-trigger')")
                assert js("document.querySelector('.month-picker-trigger').dataset.month") == original_month

                nav('Budgets')
                ready("document.querySelector('.budget-toolbar')")
                for label, panel in [('Detailed plan', 'budget-group'), ('Spending breakdown', 'budget-breakdown'), ('Income allocations', 'budget-income')]:
                    click('.budget-toolbar .view-selector button', label)
                    ready(f'document.querySelector(".{panel}")')
                    assert js("""[...document.querySelectorAll('.budget-summary section button')].every(button =>
                        button.getBoundingClientRect().right <= button.closest('section').getBoundingClientRect().right &&
                        button.scrollWidth <= button.clientWidth + 1
                    )"""), (width, theme, label, 'budget action clipping')
                    shot(f'{width}-{theme}-{panel}')

                nav('Investments')
                ready("document.querySelector('.investment-records .investment-row')")
                js("document.querySelector('.investment-records').scrollIntoView({block: 'start'})")
                shot(f'{width}-{theme}-investment-holdings')
                click('[aria-label="Investment view"] button', 'Activity')
                ready("document.querySelector('.investment-row')?.textContent.includes('Synthetic investment')")
                shot(f'{width}-{theme}-investment-activity')

            nav('Accounts')
            if width > 850:
                assert js("""(() => {
                    const rail = document.querySelector('.app-bar');
                    const nav = rail.querySelector('.desktop-navigation');
                    const [first, last] = [nav.querySelector('button'), [...nav.querySelectorAll('button')].at(-1)];
                    return getComputedStyle(rail).position === 'sticky' &&
                        rail.getBoundingClientRect().right <= document.querySelector('main').getBoundingClientRect().left &&
                        first.getBoundingClientRect().top < last.getBoundingClientRect().top &&
                        nav.querySelector('[aria-current="page"]').textContent.trim() === 'Accounts';
                })()"""), (width, theme, 'desktop sidebar')
            else:
                assert js("getComputedStyle(document.querySelector('.mobile-navigation')).position === 'fixed'")
            search('[aria-label="Search accounts"]', 'Long-term household')
            ready("document.querySelectorAll('.account-summary').length === 18")
            shot(f'{width}-{theme}-account-list')
            click('.account-summary')
            ready("document.querySelector('.account-inspector[open]')")
            shot(f'{width}-{theme}-account-inspector')
            key('Escape', 'Escape', 27)
            ready("!document.querySelector('dialog[open]')")
            search('[aria-label="Search accounts"]', '')
            ready("document.querySelectorAll('.account-group').length > 1")
            js("document.querySelector('.group-summary').scrollIntoView({block:'start'})")
            click('.group-summary')
            ready("!document.querySelector('.account-group').open")
            shot(f'{width}-{theme}-account-groups-collapsed')

            nav('Spending')
            ready("document.querySelector('.report-tabs [data-checked]')")
            if width == 1440:
                ready("document.querySelector('.report-transactions')")
                assert js("""(() => {
                    const bounds = document.querySelector('.spending-page').getBoundingClientRect();
                    const ledger = document.querySelector('.report-transactions').getBoundingClientRect();
                    return Math.abs(ledger.left - bounds.left) < 1 && Math.abs(ledger.right - bounds.right) < 1;
                })()"""), (width, theme, 'report ledger uses full content width')
                js("document.querySelector('.report-transactions').scrollIntoView({block:'start'})")
                shot(f'{width}-{theme}-spending-ledger')
            if width in (1440, 390):
                js("""(() => {
                    const compare = document.querySelector('[aria-label="Report comparison"]');
                    compare.value = 'custom';
                    compare.dispatchEvent(new Event('change', {bubbles:true}));
                })()""")
                ready('document.querySelector(\'[aria-label="Comparison month"]\')')
                js("document.querySelector('[aria-label=\"Comparison month\"]').scrollIntoView({block:'center'})")
                click('[aria-label="Comparison month"]')
                ready("document.querySelector('.month-picker-panel:popover-open')")
                ready("""(() => {
                    const go = document.querySelector('.month-picker-panel:popover-open .month-picker-jump button');
                    const bounds = go.getBoundingClientRect();
                    return go.contains(document.elementFromPoint(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2));
                })()""")
                assert js("""(() => {
                    const cutoff = document.querySelector('[aria-label="Reporting month"]').dataset.month;
                    const panel = document.querySelector('.month-picker-panel:popover-open');
                    const year = Number(panel.querySelector('.month-picker-year strong').textContent);
                    return [...panel.querySelectorAll('.month-picker-cell')].every((cell, index) =>
                        cell.disabled === (year === Number(cutoff.slice(0, 4)) && index + 1 >= Number(cutoff.slice(5))));
                })()"""), (width, theme, 'comparison dates before reporting month')
                shot(f'{width}-{theme}-comparison-month')
                key('Escape', 'Escape', 27)
                ready("!document.querySelector('.month-picker-panel:popover-open')")
                click('.report-filter-fields button', 'Reset filters')
            click('.report-account-filter summary')
            ready("document.querySelectorAll('.report-account-scroll input').length >= 20")
            js("document.querySelector('.report-account-filter').scrollIntoView({block: 'start'})")
            shot(f'{width}-{theme}-report-account-filter')
            click('.report-account-options button', 'Done')
            assert js("document.activeElement.matches('.report-account-filter summary')")

    nav('Spending')
    js('''(() => {
      const select = document.querySelector('[aria-label="Report category"]');
      select.value = 'uncategorized';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    })()''')
    ready('document.querySelector(\'[aria-label="Report category"]\').value === "uncategorized"')
    js("document.querySelector('.report-tabs [data-checked]').focus()")
    key('Enter', 'Enter', 13)
    ready('document.querySelector(\'[aria-label="Report category"]\').value === "all"')
    key('ArrowRight', 'ArrowRight', 39)
    ready("document.querySelector('.spending-main h2')?.textContent === 'Income this month'")
    assert js("document.activeElement.matches('.report-tabs [data-checked]') && getComputedStyle(document.activeElement).outlineStyle === 'solid'")
    click('.spending-main button', 'View transactions')
    ready("document.querySelector('.drilldown-filter')")
    click('.drilldown-filter button', 'Back to Spending')
    ready("document.querySelector('.spending-main h2')?.textContent === 'Income this month'")
    click('[aria-label="Help with Spending"]')
    ready("document.querySelector('.page-help[open]')")
    shot('320-dark-contextual-help')
    key('Escape', 'Escape', 27)
    ready("!document.querySelector('.page-help[open]')")
    assert js("document.activeElement.getAttribute('aria-label') === 'Help with Spending'")

    nav('Overview')
    ready("document.querySelector('.cashflow-total')")
    reporting_month = js("document.querySelector('.month-picker-trigger').dataset.month")
    click('.month-picker-trigger')
    ready("document.querySelector('.month-picker-panel:popover-open')")
    click('.month-picker-year button[aria-label="Previous year"]')
    click('.month-picker-cell', 'Jan')
    earlier_month = f'{int(reporting_month[:4]) - 1:04}-01'
    ready(f"document.querySelector('.month-picker-trigger').dataset.month === {json.dumps(earlier_month)}")
    ready(f"document.querySelector('.cashflow .section-heading small')?.textContent.startsWith('January {int(earlier_month[:4])}')")
    click('.month-picker-trigger')
    ready("document.querySelector('.month-picker-panel:popover-open')")
    search('[aria-label="Jump to reporting month"]', reporting_month)
    click('.month-picker-jump button', 'Go')
    ready(f"document.querySelector('.month-picker-trigger').dataset.month === {json.dumps(reporting_month)} && !document.querySelector('.month-picker-panel:popover-open')")
    js("document.querySelector('.cashflow-total').focus()")
    ready("document.querySelector('[role=tooltip]:popover-open')?.textContent.includes('Transfer')")
    assert js("document.querySelector('.cashflow-total').getAttribute('aria-describedby') === document.querySelector('[role=tooltip]').id")
    key('Escape', 'Escape', 27)
    ready("!document.querySelector('[role=tooltip]')")

    nav('Transactions')
    js("document.querySelector('.ledger-options button').focus()")
    key('Enter', 'Enter', 13)
    ready("document.querySelector('.ledger-options button').getAttribute('aria-expanded') === 'true'")
    assert js("document.querySelector('[aria-label=\"Sort transactions\"]').getClientRects().length > 0")
    search('[aria-label="Search transactions"]', 'Cedar')
    ready("document.querySelectorAll('.ledger-row').length === 1")
    click('.ledger-row')
    ready("document.activeElement.name === 'amount'")
    shot('320-dark-transaction-editor')
    key('Escape', 'Escape', 27)
    ready("!document.querySelector('dialog[open]')")
    click('.transfer-panel summary')
    ready("document.querySelector('.transfer-panel').open")

    nav('Investments')
    ready("document.querySelector('.investment-row summary')")
    click('.investment-row summary')
    ready("document.querySelector('.investment-row[open]')")
    js("document.querySelector('.investment-row[open]').scrollIntoView({block:'start'})")
    shot('320-dark-holding-expanded')
    click('[aria-label="Investment view"] button', 'Activity')
    ready("document.querySelector('.investment-row')?.textContent.includes('Synthetic investment')")

    nav('Bank sync')
    ready("document.querySelector('.connection-card')")
    click('.connection-details summary')
    ready("document.querySelector('.connection-details').open")
    js("document.querySelector('.connection-details').scrollIntoView({block:'start'})")
    shot('320-dark-bank-details-expanded')

    nav('Backup & restore')
    ready("document.querySelector('.backup-row')")
    js("document.querySelector('.backup-row button').focus()")
    key('Enter', 'Enter', 13)
    ready("document.querySelector('[aria-label=\"Restore confirmation\"]')")
    assert js("document.querySelector('dialog button.danger').disabled")
    click('.backup-contents summary')
    shot('320-dark-restore-preview')
    click('dialog button', 'Cancel')
    ready("!document.querySelector('dialog[open]')")
    assert js("document.activeElement.textContent === 'Preview restore'")

    cdp('Emulation.setEmulatedMedia', features=[{'name': 'prefers-reduced-motion', 'value': 'reduce'}])
    theme = js('document.documentElement.dataset.theme')
    js("document.querySelector('.theme-toggle').focus()")
    key(' ', 'Space', 32)
    ready(f'document.documentElement.dataset.theme !== "{theme}" && !document.querySelector(".theme-toggle").disabled')
    assert js("getComputedStyle(document.querySelector('.theme-toggle')).transitionDuration === '0s'")
    assert not runtime_errors, runtime_errors
    print('All eight pages rendered in both themes at 1440, 1024, 768, 390, and 320 px. Meters, filters, disclosures, drilldowns, dialogs, keyboard and reduced motion checked.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', action='store_true', help='Serve synthetic data until stopped with Ctrl+C.')
    parser.add_argument('--serve', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--folder', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not (ROOT / 'dist/index.html').is_file():
        parser.error('Build the frontend with npm run build first.')
    if args.serve:
        serve(args.serve, args.folder)
    else:
        (ROOT / 'data').mkdir(exist_ok=True)
        with TemporaryDirectory(prefix='frontend-refresh-', dir=ROOT / 'data') as temporary:
            if args.preview:
                serve(free_port(), temporary)
            else:
                browser_check(temporary)
