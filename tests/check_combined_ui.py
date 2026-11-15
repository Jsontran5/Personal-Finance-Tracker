r"""Opt-in Combined Sandbox/restart/restore browser check on isolated database copies.

Run after build: python tests/check_combined_ui.py [--live]
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
    with patch('finance.plaid_sandbox.credentials', return_value=credentials(ROOT)), patch('finance.multi_bank.credentials', return_value=credentials(ROOT)):
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
    with tempfile.TemporaryDirectory(prefix='finance-combined-ui-') as temporary:
        folder = Path(temporary)
        database = folder / 'data/finance.db'
        database.parent.mkdir()
        for suffix in ['.db', '.plaid-sandbox.db', '.plaid-investments-sandbox.db', '.plaid-combined-sandbox.db']:
            source = ROOT / 'data' / ('finance' + suffix)
            with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as original, closing(sqlite3.connect(database.with_suffix(suffix))) as copy:
                original.backup(copy)
        combined = database.with_suffix('.plaid-combined-sandbox.db')
        with closing(sqlite3.connect(combined)) as fixture:
            for index in range(8):
                fixture.execute('INSERT INTO multi_connections(institution_id,name,profile) VALUES (?,?,?)', ('ins_fixture'+str(index), 'Test institution '+str(index)+' with a deliberately very long institution display name for narrow layouts', 'banking'))
            fixture.execute("UPDATE multi_connections SET synced_at=NULL,holdings_at=NULL,activity_at=NULL,balance_checked_at=NULL")
            fixture.commit()
        investment = database.with_suffix('.plaid-investments-sandbox.db')
        if live:
            with closing(sqlite3.connect(investment)) as db:
                db.execute("UPDATE investment_state SET holdings_at='2000-01-01T00:00:00+00:00',activity_at='2000-01-01T00:00:00+00:00'")
                db.commit()
            with closing(sqlite3.connect(database.with_suffix('.plaid-sandbox.db'))) as db:
                db.execute("UPDATE liability_state SET synced_at='2000-01-01T00:00:00+00:00'")
                db.commit()
        database.with_suffix('.workspace').write_text('sandbox')
        database.with_suffix('.sandbox-scenario').write_text('combined')
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

            def api(path, method='GET', body=None):
                return js("fetch('/api" + path + "',{method:"+json.dumps(method)+",headers:{'content-type':'application/json','x-finance-request':'1'},body:"+(json.dumps(json.dumps(body)) if body is not None else 'undefined')+"}).then(async r=>{const d=await r.json();if(!r.ok)throw Error(JSON.stringify(d));return d})")
            original_authorizations = None
            with closing(sqlite3.connect(combined)) as db:
                original_authorizations = db.execute('SELECT id,item_id,token FROM multi_connections ORDER BY id').fetchall()
            manual = api('/accounts', 'POST', {'name':'Nelnet manual student loan group for layout testing', 'kind':'loan', 'debt_subtype':'student', 'currency':'USD','date':time.strftime('%Y-%m-%d'),'opening_balance_minor':250000})['id']
            api(f'/accounts/{manual}/debt', 'PUT', {'subtype':'student','servicer':'Nelnet manual group','apr_percent':'5.1250'})
            nav('Bank sync');ready("document.querySelectorAll('.connection-card').length===6")
            assert len(api('/plaid/combined-sandbox')['connections']) == 10
            button('.pagination button', 'Next connections');ready("document.querySelectorAll('.connection-card').length===4")
            button('.pagination button', 'Previous connections');ready("document.querySelectorAll('.connection-card').length===6")
            js("document.querySelector('.manual-selection summary').click()")
            # Reload manager after adding a Personal account through the real local API.
            nav('Overview');nav('Bank sync');ready("document.querySelector('.manual-selection-list input')")
            js("document.querySelector('.manual-selection').open=true;document.querySelector('.manual-selection-list input').click()")
            button('.manual-selection button','Include selected manual accounts')
            ready("[...document.querySelectorAll('[role=status]')].some(e=>e.textContent.includes('selection saved'))")
            expected_accounts = len(api('/plaid/combined-sandbox/preview?month='+time.strftime('%Y-%m'))['accounts'])
            assert expected_accounts == 17
            if live:
                for identifier, product in [(1,'transactions'),(2,'holdings'),(2,'activity')]:
                    result = api(f'/plaid/combined-sandbox/connections/{identifier}/sync?product={product}','POST')
                    assert result['count'] >= 0 if product == 'transactions' else result['count'] > 0
                print('Actual Sandbox per-institution transaction, holdings and activity syncs passed; no Item creation.',flush=True)
            for width in (1280,390):
                cdp('Emulation.setDeviceMetricsOverride',{'width':width,'height':1000 if width>400 else 844,'deviceScaleFactor':1,'mobile':False})
                for theme in ('dark','light'):
                    if js('document.documentElement.dataset.theme') != theme:
                        js("document.querySelector('.theme-toggle').click()")
                        ready("!document.querySelector('.theme-toggle').disabled")
                    nav('Bank sync');ready("document.querySelectorAll('.connection-card').length===6")
                    screenshot(f'{width}-{theme}-combined-manager')
                    js("document.querySelector('.connection-card').scrollIntoView({block:'start'});document.querySelector('.connection-details summary').click()")
                    screenshot(f'{width}-{theme}-combined-sync-expanded')
                    js("document.querySelectorAll('.connection-card')[2].scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-combined-long-institution')
                    js("document.querySelector('.manual-selection').open=true;document.querySelector('.manual-selection').scrollIntoView({block:'center'})")
                    screenshot(f'{width}-{theme}-combined-manual-selection')
                    nav('Overview');ready("document.querySelector('.hero-value')")
                    screenshot(f'{width}-{theme}-combined-overview')
                    nav('Accounts');ready("document.querySelectorAll('.compact-account').length>=5")
                    # Expand every group to exercise the full seventeen-account list.
                    js("document.querySelectorAll('.account-group').forEach(e=>e.open=true)")
                    ready("document.querySelectorAll('.compact-account').length===17")
                    screenshot(f'{width}-{theme}-combined-accounts')
                    js("(()=>{const e=[...document.querySelectorAll('.compact-account')].find(e=>e.textContent.includes('Nelnet manual'));e.open=true;e.scrollIntoView({block:'start'});e.querySelector('.debt-details').open=true})()")
                    ready("[...document.querySelectorAll('.debt-facts')].some(e=>e.textContent.includes('5.1250'))")
                    screenshot(f'{width}-{theme}-combined-manual-debt')
                    nav('Investments');ready("document.querySelector('.investment-holdings')")
                    assert js("document.querySelector('.investment-actions').textContent") == 'Manage connection'
                    screenshot(f'{width}-{theme}-combined-portfolio')
                    js("document.querySelector('.investment-row').open=true;document.querySelector('.investment-provider').open=true;document.querySelector('.investment-row').scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-combined-position-expanded')
                    nav('Backup & restore');ready("document.querySelector('.backup-create')")
                    assert js("document.querySelector('select[aria-label=\"Backup workspace\"]').value") == 'sandbox_combined'
                    button('.backup-create button','Create backup');ready("document.querySelector('.backup-message')")
                    button('.backup-row button','Preview restore');ready("document.querySelector('dialog[open]')")
                    assert 'Connections' in js("document.querySelector('.restore-summary').textContent")
                    screenshot(f'{width}-{theme}-combined-backup')
                    button('dialog button','Cancel')
                    print(f'{width}-{theme}: combined manager, long lists, manual loan, portfolio and restore preview fit.',flush=True)
            # Real subprocess restart must retain all Items, selection and data.
            stop(server);server=start_server();wait_for(lambda:urllib.request.urlopen(url,timeout=1).status==200,'restart')
            cdp('Page.reload');ready("document.querySelector('select[aria-label=\"Sandbox scenario\"]')?.value==='combined'")
            nav('Bank sync');ready("document.querySelectorAll('.connection-card').length===6")
            assert api('/plaid/combined-sandbox')['manual_accounts'][0]['name'].startswith('Nelnet')
            nav('Backup & restore');ready("document.querySelector('.backup-create')")
            button('.backup-create button','Create backup');ready("document.querySelector('.backup-message')")
            with closing(sqlite3.connect(combined)) as db:
                db.execute('DELETE FROM investment_holdings');db.commit()
            button('.backup-row button','Preview restore');ready("document.querySelector('dialog[open]')")
            js("document.querySelector('input[aria-label=\"Restore confirmation\"]').focus()")
            cdp('Input.insertText',{'text':'RESTORE SANDBOX_COMBINED'})
            button('dialog button','Restore workspace');ready("document.querySelector('.restore-result')")
            assert len(api('/plaid/combined-sandbox/investments')['holdings']) == 13
            with closing(sqlite3.connect(combined)) as db:
                assert db.execute('SELECT id,item_id,token FROM multi_connections ORDER BY id').fetchall()==original_authorizations
            assert fingerprint(database.with_suffix('.plaid-sandbox.db')) == original_banking
            print('Combined restart and confirmed UI restore passed; authorizations and original banking tables unchanged.',flush=True)
        except Exception:
            if ws:
                print(js('document.body.innerText'),flush=True)
                screenshot('combined-failure')
            raise
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
        raise SystemExit('Usage: python tests/check_combined_ui.py [--live]')
