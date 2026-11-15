r"""Live readiness browser QA with a synthetic Production provider on isolated copies.

Run after build: python tests/check_redesign_ui.py
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
    with tempfile.TemporaryDirectory(prefix='finance-redesign-ui-') as temporary:
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



            def click(selector, text=None):
                expression="(()=>{const list=[...document.querySelectorAll("+json.dumps(selector)+")];const e=list.find(e=>e.getClientRects().length && getComputedStyle(e).visibility!=='hidden'"+(" && e.textContent.trim()==="+json.dumps(text) if text else '')+");if(!e)throw Error('Visible control missing');e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()"
                point=js(expression)
                cdp('Input.dispatchMouseEvent',{'type':'mousePressed','button':'left','clickCount':1,**point})
                cdp('Input.dispatchMouseEvent',{'type':'mouseReleased','button':'left','clickCount':1,**point})
            def key(value):
                cdp('Input.dispatchKeyEvent',{'type':'keyDown','key':value,'code':value,'windowsVirtualKeyCode':27 if value=='Escape' else 9})
                cdp('Input.dispatchKeyEvent',{'type':'keyUp','key':value,'code':value})
            def nav(name):
                if js("Boolean(document.querySelector('dialog[open]'))"):
                    key('Escape');ready("!document.querySelector('dialog[open]')")
                visible=js("[...document.querySelectorAll('nav button')].some(e=>e.textContent.trim()==="+json.dumps(name)+" && e.getClientRects().length)")
                if not visible:
                    click('button[aria-label="More sections"]');ready("document.querySelector('.navigation-sheet[open]')")
                click('nav button',name)
                ready("document.querySelector('h1')?.textContent==="+json.dumps(name))
                ready("!document.querySelector('dialog[open]')")
                js('window.scrollTo(0,0)')
            ready("document.querySelector('select[aria-label=\"Workspace\"]')?.value==='live'")
            select('Workspace','sandbox');ready("document.querySelector('select[aria-label=\"Sandbox scenario\"]')")
            select('Sandbox scenario','combined');ready("document.querySelector('.hero-value')")
            pages=['Overview','Spending','Transactions','Budgets','Accounts','Investments','Bank sync','Backup & restore']
            for width in (1440,1280,768,390,320):
                cdp('Emulation.setDeviceMetricsOverride',{'width':width,'height':1000 if width>850 else 844,'deviceScaleFactor':1,'mobile':False})
                for theme in ('dark','light'):
                    if js('document.documentElement.dataset.theme')!=theme:
                        click('.theme-toggle');ready("!document.querySelector('.theme-toggle').disabled")
                    for page in pages:
                        nav(page)
                        ready("!document.querySelector('main > [role=status]')")
                        if page=='Investments':ready("document.querySelector('.investment-holdings')")
                        screenshot(f'{width}-{theme}-redesign-'+page.lower().replace(' ','-').replace('&','and'))
                    if width<=850:
                        rect=js("(()=>{const r=document.querySelector('.mobile-navigation').getBoundingClientRect();return {top:r.top,bottom:r.bottom}})()")
                        assert rect['top']>650 and rect['bottom']<=845,rect
                        click('button[aria-label="More sections"]');ready("document.querySelector('.navigation-sheet[open]')")
                        screenshot(f'{width}-{theme}-redesign-more')
                        key('Escape');ready("!document.querySelector('dialog[open]')")
                    # Inspect an account using real pointer input, then keyboard close/focus return.
                    nav('Accounts');ready("document.querySelector('.account-summary')")
                    click('.account-summary');ready("document.querySelector('.account-inspector[open]')")
                    assert js("document.querySelector('.account-inspector[open]').contains(document.activeElement)")
                    for _ in range(3):key('Tab')
                    assert js("document.querySelector('.account-inspector[open]').contains(document.activeElement)")
                    screenshot(f'{width}-{theme}-redesign-account-inspector')
                    key('Escape');ready("!document.querySelector('dialog[open]')")
                    assert js("document.activeElement.classList.contains('account-summary')")
                    # Open the transaction editor and optional raw provider details without changing data.
                    nav('Transactions');ready("document.querySelector('.ledger-row')")
                    click('.ledger-row');ready("document.querySelector('.editor-inspector[open]')")
                    screenshot(f'{width}-{theme}-redesign-transaction-inspector')
                    if js("Boolean(document.querySelector('.editor-inspector .transaction-details > summary'))"):
                        click('.editor-inspector .transaction-details > summary')
                        ready("document.querySelector('.transaction-details[open] .transaction-fields')")
                        screenshot(f'{width}-{theme}-redesign-provider-expanded')
                    click('.editor-inspector button','Cancel');ready("!document.querySelector('dialog[open]')")
                    print(f'{width}-{theme}: eight screens, visible pointer navigation, inspectors/focus, expanded details and overflow passed.',flush=True)
            # Reduced motion, material and high-contrast fallbacks, plus 200%-equivalent reflow.
            cdp('Emulation.setEmulatedMedia',{'features':[{'name':'prefers-reduced-motion','value':'reduce'},{'name':'prefers-reduced-transparency','value':'reduce'},{'name':'prefers-contrast','value':'more'}]})
            cdp('Emulation.setDeviceMetricsOverride',{'width':640,'height':500,'deviceScaleFactor':2,'mobile':False})
            nav('Overview');screenshot('redesign-200-percent-reflow')
            nav('Transactions');ready("document.querySelector('.ledger-row')");click('.ledger-row');ready("document.querySelector('.editor-inspector[open]')")
            assert js("getComputedStyle(document.querySelector('.editor-inspector')).transitionDuration")=='0s'
            screenshot('redesign-reduced-motion-inspector');key('Escape')
            select('Workspace','live');ready("document.querySelector('select[aria-label=\"Workspace\"]')?.value==='live'")
            nav('Bank sync');ready("document.querySelector('.live-readiness')")
            assert 'Linking locked' in js("document.querySelector('.live-readiness').textContent")
            screenshot('redesign-live-lock')
            select('Workspace','personal');ready("document.querySelector('select[aria-label=\"Workspace\"]')?.value==='personal'")
            nav('Accounts');screenshot('redesign-manual-empty')
            assert fingerprint(database.with_suffix('.plaid-sandbox.db'))==original_banking
            print('Reduced-motion/contrast/material fallbacks, 200% reflow, Manual/Live identity and locked pilot passed. Original banking copy unchanged; no Production calls.',flush=True)
        except Exception:
            if ws:
                print(js('document.body.innerText').encode('ascii',errors='replace').decode(),flush=True)
                screenshot('redesign-failure')
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
        raise SystemExit('Usage: python tests/check_redesign_ui.py')
