r"""Opt-in scenario/restart/restore browser check on isolated database copies.

Run after build: python tests/check_scenario_ui.py [--live]
--live retrieves read-only investment data using the saved Sandbox Item. Without
it, browser checks use cached data only. Credentials are loaded only by the backend.
The actual project databases, connections, and backups are never changed.
"""
import base64
from contextlib import closing
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / 'data/ui-qa'


def serve(database, port):
    import uvicorn
    from unittest.mock import patch
    from finance.api import create_app
    from finance.plaid_sandbox import credentials
    # Existing .env remains at project root and is never copied or displayed.
    with patch('finance.plaid_sandbox.credentials', return_value=credentials(ROOT)):
        uvicorn.run(create_app(Path(database)), host='127.0.0.1', port=int(port), access_log=False)


def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def fingerprint(path):
    with closing(sqlite3.connect(path)) as db:
        return {row[0]: hashlib.sha256(repr(db.execute('SELECT * FROM "' + row[0] + '" ORDER BY rowid').fetchall()).encode()).hexdigest()
                for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def run(live=False):
    import websocket
    OUTPUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='finance-scenarios-ui-') as temporary:
        folder = Path(temporary)
        database = folder / 'data/finance.db'
        database.parent.mkdir()
        for suffix in ['.db', '.plaid-sandbox.db', '.plaid-investments-sandbox.db']:
            source = ROOT / 'data' / ('finance' + suffix)
            with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as original, closing(sqlite3.connect(database.with_suffix(suffix))) as copy:
                original.backup(copy)
        investment = database.with_suffix('.plaid-investments-sandbox.db')
        if live:
            with closing(sqlite3.connect(investment)) as db:
                db.execute("UPDATE investment_state SET holdings_at='2000-01-01T00:00:00+00:00',activity_at='2000-01-01T00:00:00+00:00'")
                db.commit()
        database.with_suffix('.workspace').write_text('sandbox')
        database.with_suffix('.sandbox-scenario').write_text('banking')
        original_banking = fingerprint(database.with_suffix('.plaid-sandbox.db'))
        server_port, chrome_port = port(), port()
        server = chrome = ws = None

        def start_server():
            return subprocess.Popen([str(ROOT / '.venv/Scripts/python.exe'), str(Path(__file__)), '--server', str(database), str(server_port)], cwd=ROOT,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)

        def stop(process):
            if process:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=8)

        def wait_for(check, description, timeout=15):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    if check():
                        return
                except (OSError, ValueError):
                    pass
                time.sleep(.15)
            raise AssertionError('Timed out: ' + description)

        try:
            server = start_server()
            url = f'http://127.0.0.1:{server_port}'
            wait_for(lambda: urllib.request.urlopen(url, timeout=1).status == 200, 'server')
            chrome = subprocess.Popen([r'C:\Program Files\Google\Chrome\Application\chrome.exe', '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                                       '--remote-allow-origins=*', f'--remote-debugging-port={chrome_port}', f'--user-data-dir={folder / "chrome"}', 'about:blank'],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            targets = []
            def discover():
                targets.extend(json.load(urllib.request.urlopen(f'http://127.0.0.1:{chrome_port}/json/list')))
                return bool(targets)
            wait_for(discover, 'Chrome')
            ws = websocket.create_connection(next(item['webSocketDebuggerUrl'] for item in targets if item['type'] == 'page'), timeout=20)
            sequence = 0
            def cdp(method, params=None):
                nonlocal sequence
                sequence += 1
                ws.send(json.dumps({'id': sequence, 'method': method, 'params': params or {}}))
                while True:
                    message = json.loads(ws.recv())
                    if message.get('id') == sequence:
                        assert 'error' not in message, message
                        return message.get('result', {})
            def js(expression):
                result = cdp('Runtime.evaluate', {'expression': expression, 'awaitPromise': True, 'returnByValue': True})
                assert 'exceptionDetails' not in result, result
                return result.get('result', {}).get('value')
            def ready(expression, timeout=15):
                wait_for(lambda: js('Boolean(' + expression + ')'), expression, timeout)
            def nav(name):
                js("[...document.querySelectorAll('nav button')].find(b=>b.textContent===" + json.dumps(name) + ').click()')
                ready('document.querySelector("h1")?.textContent===' + json.dumps(name))
            def select(label, value):
                js("(()=>{const e=document.querySelector('select[aria-label=\"" + label + "\"]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e," + json.dumps(value) + ");e.dispatchEvent(new Event('change',{bubbles:true}));})()")
            def button(selector, text):
                js('[...document.querySelectorAll(' + json.dumps(selector) + ')].find(b=>b.textContent===' + json.dumps(text) + ').click()')
            def screenshot(name):
                time.sleep(.2)
                assert js('document.documentElement.scrollWidth <= innerWidth'), name
                data = cdp('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})
                (OUTPUT / (name + '.png')).write_bytes(base64.b64decode(data['data']))

            cdp('Page.enable'); cdp('Page.navigate', {'url': url})
            ready("document.querySelector('select[aria-label=\"Sandbox scenario\"]')")
            select('Sandbox scenario', 'investments')
            nav('Investments')
            ready("document.querySelector('.investment-holdings')")
            if live:
                button('.investment-actions button', 'Sync holdings')
                ready("[...document.querySelectorAll('.investment-sync [role=status]')].some(e=>e.textContent.startsWith('Holdings synced'))", 180)
                button('.investment-actions button', 'Sync activity')
                ready("[...document.querySelectorAll('.investment-sync [role=status]')].some(e=>e.textContent.startsWith('Activity synced'))", 180)
                print('Actual Plaid investment sync buttons completed through the UI.', flush=True)
            portfolio = js("fetch('/api/plaid/investments-sandbox/investments').then(r=>r.json())")
            expected_holdings = len(portfolio['holdings'])
            expected_activity = js("fetch('/api/plaid/investments-sandbox/investments/activity').then(r=>r.json()).then(d=>d.total)")
            assert expected_holdings > 0 and expected_activity > 0
            for width in (1280, 390):
                cdp('Emulation.setDeviceMetricsOverride', {'width': width, 'height': 1000 if width > 400 else 844, 'deviceScaleFactor': 1, 'mobile': False})
                for theme in ('dark', 'light'):
                    if js('document.documentElement.dataset.theme') != theme:
                        js("document.querySelector('.theme-toggle').click()")
                        ready("!document.querySelector('.theme-toggle').disabled")
                    nav('Bank sync')
                    ready("document.querySelector('h2')?.textContent==='Investment test connection'")
                    ready("document.querySelector('.bank-actions')?.textContent.includes('View investments')")
                    screenshot(f'{width}-{theme}-scenario-connection')
                    nav('Investments')
                    ready("document.querySelectorAll('.investment-row').length>0")
                    screenshot(f'{width}-{theme}-scenario-portfolio')
                    js("document.querySelector('.investment-records').scrollIntoView({block:'start'});document.querySelector('.investment-row summary').click();document.querySelector('.investment-provider summary').click()")
                    screenshot(f'{width}-{theme}-scenario-holding-expanded')
                    nav('Backup & restore')
                    ready("document.querySelector('.backup-create')")
                    assert js("document.querySelector('select[aria-label=\"Backup workspace\"]').value") == 'sandbox_investments'
                    button('.backup-create button', 'Create backup')
                    ready("document.querySelector('.backup-message')")
                    button('.backup-row button', 'Preview restore')
                    ready("document.querySelector('dialog[open]')")
                    assert 'Investment holdings' in js("document.querySelector('.restore-summary').textContent")
                    js("document.querySelector('.backup-contents summary').click()")
                    screenshot(f'{width}-{theme}-scenario-restore-preview')
                    button('dialog button', 'Cancel')
                    select('Sandbox scenario', 'banking')
                    nav('Accounts')
                    ready("document.querySelectorAll('.compact-account').length===14")
                    select('Sandbox scenario', 'investments')
                    nav('Investments')
                    ready("document.querySelector('.investment-holdings')")
                    print(f'{width}-{theme}: scenario switching, holdings, expanded details and backup preview fit.', flush=True)

            select('Sandbox scenario', 'banking')
            nav('Investments')
            ready("[...document.querySelectorAll('button')].some(b=>b.textContent==='Switch to investment demo')")
            button('button', 'Switch to investment demo')
            ready("document.querySelector('select[aria-label=\"Sandbox scenario\"]')?.value==='investments' && document.querySelector('.investment-holdings')")
            # Browser reload and a real backend restart must retain scenario and Item data.
            for restart in (False, True):
                if restart:
                    stop(server); server = start_server()
                    wait_for(lambda: urllib.request.urlopen(url, timeout=1).status == 200, 'restarted server')
                cdp('Page.reload')
                ready("document.querySelector('select[aria-label=\"Sandbox scenario\"]')?.value==='investments'")
                nav('Investments')
                ready("document.querySelector('.investment-holdings')")
                assert js("fetch('/api/plaid/investments-sandbox/investments').then(r=>r.json()).then(d=>d.holdings.length)") == expected_holdings
                assert js("fetch('/api/plaid/investments-sandbox/investments/activity').then(r=>r.json()).then(d=>d.total)") == expected_activity

            # Restore only the isolated investment copy after deliberate fixture removal.
            nav('Backup & restore'); ready("document.querySelector('.backup-create')")
            button('.backup-create button', 'Create backup'); ready("document.querySelector('.backup-message')")
            with closing(sqlite3.connect(investment)) as db:
                db.execute('DELETE FROM investment_holdings'); db.execute('DELETE FROM investment_activity'); db.commit()
            button('.backup-row button', 'Preview restore'); ready("document.querySelector('dialog[open]')")
            assert '0 currently → ' + str(expected_holdings) in js("document.querySelector('.restore-summary').textContent")
            js("document.querySelector('input[aria-label=\"Restore confirmation\"]').focus()")
            cdp('Input.insertText', {'text': 'RESTORE SANDBOX_INVESTMENTS'})
            screenshot('390-light-scenario-restore-confirm')
            button('dialog button', 'Restore workspace')
            ready("document.querySelector('.restore-result') && !document.querySelector('dialog[open]')")
            nav('Investments'); ready("document.querySelector('.investment-holdings')")
            assert js("fetch('/api/plaid/investments-sandbox/investments').then(r=>r.json()).then(d=>d.holdings.length)") == expected_holdings
            assert js("fetch('/api/plaid/investments-sandbox/investments/activity').then(r=>r.json()).then(d=>d.total)") == expected_activity
            assert fingerprint(database.with_suffix('.plaid-sandbox.db')) == original_banking
            print(f'Reload, backend restart, and UI restore verified: {expected_holdings} holdings / {expected_activity} activity. Banking tables unchanged.', flush=True)
        finally:
            if ws:
                ws.close()
            stop(chrome); stop(server)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--server']:
        serve(sys.argv[2], sys.argv[3])
    elif sys.argv[1:] in ([], ['--live']):
        run(sys.argv[1:] == ['--live'])
    else:
        raise SystemExit('Usage: python tests/check_scenario_ui.py [--live]')
