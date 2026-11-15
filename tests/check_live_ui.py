r"""Live readiness browser QA with a synthetic Production provider on isolated copies.

Run after build: python tests/check_live_ui.py
No Production requests or Items are created. Checks both themes/sizes,
readiness forms, read-only coverage, locked actions, restart and restore.
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
    import os
    import uvicorn
    from unittest.mock import patch
    from finance.api import create_app
    from finance.live_setup import live_router
    def factory(keys, *, environment):
        assert environment == 'production'
        def call(endpoint, **payload):
            if endpoint == '/institutions/search':
                return {'institutions':[{'institution_id':'ins_live_bofa','name':'Bank of America (test coverage response)'}]}
            if endpoint == '/institutions/get_by_id':
                return {'institution':{'institution_id':payload['institution_id'],'name':'Bank of America (test coverage response)','oauth':True,'products':['transactions','liabilities']}}
            raise AssertionError('Readiness test must never create Link or access real banks: '+endpoint)
        return call
    with patch.dict(os.environ, {'PLAID_PRODUCTION_CLIENT_ID':'synthetic-client','PLAID_PRODUCTION_SECRET':'synthetic-production-secret','PLAID_LIVE_ENABLED':'false'}), patch('finance.api.live_router',lambda db:live_router(db,client_factory=factory)):
        uvicorn.run(create_app(Path(database)),host='127.0.0.1',port=int(port),access_log=False)


def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def fingerprint(path):
    with closing(sqlite3.connect(path)) as db:
        return {row[0]: hashlib.sha256(repr(db.execute('SELECT * FROM "' + row[0] + '" ORDER BY rowid').fetchall()).encode()).hexdigest()
                for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def run():
    import websocket
    OUTPUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='finance-live-ui-') as temporary:
        folder = Path(temporary)
        database = folder / 'data/finance.db'
        database.parent.mkdir()
        for suffix in ['.db', '.plaid-sandbox.db', '.plaid-investments-sandbox.db', '.plaid-combined-sandbox.db']:
            source = ROOT / 'data' / ('finance' + suffix)
            with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as original, closing(sqlite3.connect(database.with_suffix(suffix))) as copy:
                original.backup(copy)
        from finance.db import connect
        from finance.live_setup import initialize_live
        from finance.multi_bank import map_accounts
        live_path=database.with_suffix('.plaid-live.db')
        with connect(live_path,write=True) as fixture:
            initialize_live(fixture)
            for index in range(8):
                fixture.execute('INSERT INTO multi_connections(institution_id,name,profile) VALUES (?,?,?)',('ins_fixture'+str(index),'Test institution '+str(index)+' with a very long institution name for phone layouts','banking'))
            accounts=[]
            for index in range(15):
                accounts.append({'account_id':str(index),'name':'Test account '+str(index)+' with a long display name','type':['depository','credit','loan'][index%3], 'subtype':['checking','credit card','student'][index%3], 'balances':{'current':index*100+20,'iso_currency_code':'USD'}})
            map_accounts(fixture,1,accounts)
        database.with_suffix('.workspace').write_text('live')
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


            def api(path,method='GET',body=None):
                return js("fetch('/api"+path+"',{method:"+json.dumps(method)+",headers:{'content-type':'application/json','x-finance-request':'1'},body:"+(json.dumps(json.dumps(body)) if body is not None else 'undefined')+"}).then(async r=>{const d=await r.json();if(!r.ok)throw Error(JSON.stringify(d));return d})")
            ready("document.querySelector('select[aria-label=\"Workspace\"]')?.value==='live'")
            assert not js("document.querySelector('select[aria-label=\"Sandbox scenario\"]')")
            nav('Bank sync');ready("document.querySelector('.live-readiness form')")
            js("document.querySelector('.readiness-settings').open=true")
            for name in ['trial_confirmed','transactions_enabled']:
                js("document.querySelector('input[name="+name+"]').click()")
            js("document.querySelector('input[name=item_limit]').focus()")
            cdp('Input.insertText',{'text':'20'})
            button('.live-readiness button','Save confirmations')
            ready("document.querySelector('.live-readiness [role=status]')?.textContent.includes('confirmations saved')")
            assert api('/plaid/live/readiness')['setup']['item_limit']==20
            js("document.querySelector('.connection-add').open=true;document.querySelector('input[aria-label=\"Find institution\"]').focus()")
            cdp('Input.insertText',{'text':'Bank of America'})
            button('.connection-add button','Search institutions');ready("document.querySelector('.connection-result button')")
            button('.connection-result button','Add institution')
            ready("document.querySelector('.connections-page > [role=status]')?.textContent.includes('Institution added')")
            assert api('/plaid/live/readiness')['preflights'][0]['transactions']
            assert js("[...document.querySelectorAll('.connection-actions button')].filter(e=>e.textContent.includes('Connect institution')).every(e=>e.disabled)")
            assert js("fetch('/api/plaid/live/connections/9/link',{method:'POST',headers:{'x-finance-request':'1'}}).then(r=>r.status)")==403
            for width in (1280,390):
                cdp('Emulation.setDeviceMetricsOverride',{'width':width,'height':1000 if width>400 else 844,'deviceScaleFactor':1,'mobile':False})
                for theme in ('dark','light'):
                    if js('document.documentElement.dataset.theme') != theme:
                        js("document.querySelector('.theme-toggle').click()")
                        ready("!document.querySelector('.theme-toggle').disabled")
                    nav('Bank sync');ready("document.querySelector('.readiness-checks')")
                    js('window.scrollTo(0,0)')
                    screenshot(f'{width}-{theme}-live-readiness')
                    js("document.querySelector('.readiness-checks').scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-live-checklist')
                    js("document.querySelectorAll('.readiness-settings').forEach(e=>e.open=true);document.querySelector('.readiness-settings').scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-live-settings-expanded')
                    js("document.querySelector('.connection-add').open=true;document.querySelector('.connection-add').scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-live-search-and-locked-institutions')
                    js("document.querySelector('.connection-details').open=true;document.querySelector('.connection-card').scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-live-connection-expanded')
                    nav('Accounts');ready("document.querySelector('.account-group')")
                    js("document.querySelectorAll('.account-group').forEach(e=>e.open=true)")
                    ready("document.querySelectorAll('.compact-account').length===15")
                    screenshot(f'{width}-{theme}-live-long-accounts')
                    js("document.querySelector('.compact-account').open=true;document.querySelector('.compact-account').scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-live-account-expanded')
                    nav('Backup & restore');ready("document.querySelector('.backup-create')")
                    assert js("document.querySelector('select[aria-label=\"Backup workspace\"]').value")=='live'
                    button('.backup-create button','Create backup');ready("document.querySelector('.backup-message')")
                    button('.backup-row button','Preview restore');ready("document.querySelector('dialog[open]')")
                    screenshot(f'{width}-{theme}-live-backup-preview')
                    button('dialog button','Cancel')
                    print(f'{width}-{theme}: Live readiness, expanded settings, locked institutions, long accounts and backups fit.',flush=True)
            stop(server);server=start_server();wait_for(lambda:urllib.request.urlopen(url,timeout=1).status==200,'restart')
            cdp('Page.reload');ready("document.querySelector('select[aria-label=\"Workspace\"]')?.value==='live'")
            nav('Bank sync');ready("document.querySelector('.live-readiness form')")
            assert api('/plaid/live/readiness')['setup']['item_limit']==20
            nav('Backup & restore');ready("document.querySelector('.backup-row button')")
            button('.backup-row button','Preview restore');ready("document.querySelector('dialog[open]')")
            js("document.querySelector('input[aria-label=\"Restore confirmation\"]').focus()")
            cdp('Input.insertText',{'text':'RESTORE LIVE'})
            button('dialog button','Restore workspace');ready("document.querySelector('.restore-result')")
            assert not api('/plaid/live/readiness')['linking_enabled']
            select('Workspace','sandbox');ready("document.querySelector('select[aria-label=\"Sandbox scenario\"]')")
            nav('Accounts');ready("document.querySelector('.account-group')")
            assert fingerprint(database.with_suffix('.plaid-sandbox.db'))==original_banking
            print('Readiness settings persist across restart/restore. Sandbox unchanged; real-bank linking remained locked; no Production requests.',flush=True)
        except Exception:
            if ws:
                print(js('document.body.innerText').encode('ascii',errors='replace').decode(),flush=True)
                screenshot('live-failure')
            raise
        finally:
            if ws:
                ws.close()
            stop(chrome); stop(server)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--server']:
        serve(sys.argv[2], sys.argv[3])
    elif not sys.argv[1:]:
        run()
    else:
        raise SystemExit('Usage: python tests/check_live_ui.py')
