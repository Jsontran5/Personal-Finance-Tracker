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
            if endpoint in {'/accounts/get','/accounts/balance/get','/investments/holdings/get','/investments/transactions/get','/transactions/sync'}:
                from finance.plaid_sandbox import PlaidError
                identifier=1 if payload.get('access_token')=='synthetic-ui-access' else 2
                if endpoint=='/accounts/balance/get' and identifier==1:
                    raise PlaidError('INTERNAL_SERVER_ERROR')
                if endpoint=='/transactions/sync':
                    return {'added':[],'modified':[],'removed':[],'has_more':False,'next_cursor':'synthetic-refreshed','transactions_update_status':'HISTORICAL_UPDATE_COMPLETE'}
                with closing(sqlite3.connect(Path(database).with_suffix('.plaid-live.db').as_uri()+'?mode=ro',uri=True)) as source:
                    source.row_factory=sqlite3.Row
                    accounts=[]
                    for a in source.execute('SELECT a.*,m.provider_id,n.provider_name FROM accounts a JOIN connection_accounts m ON m.account_id=a.id LEFT JOIN account_names n ON n.account_id=a.id WHERE m.connection_id=?',(identifier,)):
                        amount=a['current_minor']/100 if a['current_minor'] is not None else None
                        if amount is not None and a['type'] in {'credit','loan'}: amount=-amount
                        accounts.append({'account_id':a['provider_id'],'name':a['provider_name'] or a['name'],'type':a['type'],'subtype':a['subtype'],'balances':{'current':amount,'iso_currency_code':a['currency']}})
                    if endpoint.startswith('/accounts/'):
                        return {'accounts':accounts}
                    if endpoint=='/investments/holdings/get':
                        rows=list(source.execute('SELECT h.payload,h.security FROM investment_holdings h JOIN connection_accounts m ON m.account_id=h.account_id WHERE m.connection_id=?',(identifier,)))
                        holdings=[];securities=[]
                        for row in rows:
                            h=json.loads(row['payload']);h['account_id']=h['account_id'].split(':',1)[-1]
                            holdings.append(h);securities.append(json.loads(row['security']))
                        return {'accounts':[a for a in accounts if a['type']=='investment'],'holdings':holdings,'securities':securities}
                    rows=list(source.execute('SELECT i.payload,i.security FROM investment_activity i JOIN connection_accounts m ON m.account_id=i.account_id WHERE m.connection_id=? AND removed=0',(identifier,)))
                    records=[];securities=[]
                    for row in rows:
                        item=json.loads(row['payload']);item['account_id']=item['account_id'].split(':',1)[-1]
                        records.append(item);securities.append(json.loads(row['security']))
                    return {'total_investment_transactions':len(records),'investment_transactions':records,'securities':securities}
            raise AssertionError('UI test must never create Link or access real banks: '+endpoint)
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
                accounts.append({'account_id':str(index),'name':'Test account '+str(index)+' with a long display name','type':'investment' if index==0 else ['depository','credit','loan'][index%3], 'subtype':'brokerage' if index==0 else ['checking','credit card','student'][index%3], 'balances':{'current':index*100+20,'iso_currency_code':'USD'}})
            map_accounts(fixture,1,accounts)
            from finance.plaid_vault import protect
            fixture.execute("UPDATE multi_connections SET item_id='synthetic-ui-item',token=? WHERE id=1", (protect(b'synthetic-ui-access',True),))
            fixture.execute('INSERT INTO live_investment_connections VALUES (1,1)')
            fixture.execute("UPDATE multi_connections SET item_id='synthetic-banking-item',token=? WHERE id=2", (protect(b'synthetic-banking-access',True),))
            fixture.execute("UPDATE connection_accounts SET connection_id=2 WHERE account_id='p1:3'")
        from seed_investment_ui import seed
        seed(live_path)
        database.with_suffix('.workspace').write_text('live')
        database.with_suffix('.sandbox-scenario').write_text('combined')
        from finance.recovery import migrate
        migrate(database.with_suffix('.plaid-sandbox.db'),'sandbox')
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
            assert not js("document.querySelector('input[name=hosted_link_confirmed]')")
            for name in ['trial_confirmed','transactions_enabled','oauth_confirmed','investments_enabled']:
                js("document.querySelector('input[name="+name+"]').click()")
            js("document.querySelector('input[name=item_limit]').focus()")
            cdp('Input.insertText',{'text':'20'})
            button('.live-readiness button','Save confirmations')
            ready("document.querySelector('.live-readiness [role=status]')?.textContent.includes('confirmations saved')")
            assert api('/plaid/live/readiness')['setup']['item_limit']==20
            assert api('/plaid/live/readiness')['ready']
            assert not js("document.querySelector('.readiness-outstanding')")
            assert not js("document.querySelector('.readiness-configuration').open")
            button('.live-readiness button','Find your bank')
            ready("document.querySelector('.connection-add').open && document.activeElement.getAttribute('aria-label')==='Find institution'")
            assert js("document.querySelector('select[aria-label=\"Connection type\"] option[value=investments]')!==null")
            assert js("[...document.querySelectorAll('.connection-add label')].some(e=>e.textContent.includes('Also connect investments'))")
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
                    assert not js("document.querySelector('.readiness-outstanding')")
                    js("document.querySelector('.readiness-configuration').open=true;document.querySelector('.readiness-checks').scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-live-checklist')
                    js("document.querySelectorAll('.readiness-settings').forEach(e=>e.open=true);document.querySelector('.readiness-settings').scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-live-settings-expanded')
                    js("document.querySelector('.connection-add').open=true;document.querySelector('.connection-add').scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-live-search-and-locked-institutions')
                    js("document.querySelector('.connection-details').open=true;document.querySelector('.connection-card').scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-live-connection-expanded')
                    js("document.querySelector('input[aria-label=\"Filter connections\"]').focus()")
                    cdp('Input.insertText',{'text':'Test institution 0'})
                    ready("document.querySelectorAll('.connection-card').length===1")
                    js("document.querySelector('.connection-details').open=true;document.querySelector('.connection-card').scrollIntoView({block:'start'})")
                    assert js("[...document.querySelectorAll('.connection-card button')].some(b=>b.textContent==='Reload investment history')")
                    screenshot(f'{width}-{theme}-live-mixed-connection-expanded')
                    js("[...document.querySelectorAll('.connection-card button')].find(b=>b.textContent==='Sync transactions').setAttribute('aria-describedby','existing-description');[...document.querySelectorAll('.connection-card button')].find(b=>b.textContent==='Sync transactions').focus()")
                    ready("document.querySelector('[role=tooltip]')?.matches(':popover-open')")
                    assert js("document.querySelector('[role=tooltip]').textContent.includes('credit-card')")
                    assert js("document.querySelector('[role=tooltip]').getBoundingClientRect().right <= document.documentElement.clientWidth")
                    screenshot(f'{width}-{theme}-control-help')
                    cdp('Input.dispatchKeyEvent',{'type':'keyDown','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
                    cdp('Input.dispatchKeyEvent',{'type':'keyUp','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
                    ready("!document.querySelector('[role=tooltip]')")
                    assert js("document.activeElement.getAttribute('aria-describedby')")=='existing-description'
                    js('document.activeElement.blur()')
                    hover_box=js("(()=>{const e=[...document.querySelectorAll('.connection-card button')].find(b=>b.textContent==='Refresh balances');const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
                    cdp('Input.dispatchMouseEvent',{'type':'mouseMoved',**hover_box})
                    ready("document.querySelector('[role=tooltip]')?.textContent.includes('account balances')")
                    tooltip_box=js("(()=>{const r=document.querySelector('[role=tooltip]').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
                    cdp('Input.dispatchMouseEvent',{'type':'mouseMoved',**tooltip_box})
                    time.sleep(.7)
                    assert js("document.querySelector('[role=tooltip]')?.matches(':popover-open')")
                    cdp('Input.dispatchKeyEvent',{'type':'keyDown','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
                    cdp('Input.dispatchKeyEvent',{'type':'keyUp','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
                    ready("!document.querySelector('[role=tooltip]')")

                    for help_page in ['Overview','Spending','Transactions','Budgets','Accounts','Investments','Bank sync','Backup & restore']:
                        nav(help_page)
                        js("document.querySelector('.page-help-trigger').click()")
                        ready("document.querySelector('.page-help[open]')")
                        assert js("document.querySelector('.page-help h2').textContent")=='Help with '+help_page
                        assert js("document.querySelector('.page-help').scrollWidth <= document.querySelector('.page-help').clientWidth")
                        assert js("document.querySelectorAll('.help-topics dt').length")>=3
                        if help_page in ['Bank sync','Investments']:
                            screenshot(f'{width}-{theme}-{help_page.replace(" ","-").lower()}-help')
                        js("[...document.querySelectorAll('.page-help button')].find(b=>b.textContent==='Done').focus()")
                        cdp('Input.dispatchKeyEvent',{'type':'keyDown','key':'Tab','code':'Tab','windowsVirtualKeyCode':9})
                        cdp('Input.dispatchKeyEvent',{'type':'keyUp','key':'Tab','code':'Tab','windowsVirtualKeyCode':9})
                        assert js("document.activeElement.getAttribute('aria-label')")=='Close help'
                        cdp('Input.dispatchKeyEvent',{'type':'keyDown','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
                        cdp('Input.dispatchKeyEvent',{'type':'keyUp','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
                        ready("!document.querySelector('.page-help')")
                        assert js("document.activeElement.classList.contains('page-help-trigger')")

                    nav('Accounts');ready("document.querySelector('.account-group')")
                    js("document.querySelectorAll('.account-group').forEach(e=>e.open=true)")
                    ready("document.querySelectorAll('.compact-account').length===15")
                    screenshot(f'{width}-{theme}-live-long-accounts')
                    js("document.querySelector('.compact-account').open=true;document.querySelector('.compact-account').scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-live-account-expanded')
                    ready("document.querySelector('.account-inspector[open]')")
                    js("[...document.querySelectorAll('.account-inspector[open] summary')].find(e=>e.textContent.includes('Balance chart')).focus()")
                    ready("document.querySelector('[role=tooltip]')?.matches(':popover-open')")
                    assert js("document.querySelector('[role=tooltip]').closest('dialog')?.classList.contains('account-inspector')")
                    screenshot(f'{width}-{theme}-inspector-help')
                    cdp('Input.dispatchKeyEvent',{'type':'keyDown','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
                    cdp('Input.dispatchKeyEvent',{'type':'keyUp','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
                    ready("!document.querySelector('[role=tooltip]')")
                    assert js("document.querySelector('.account-inspector[open]')!==null")
                    original_name=js("document.querySelector('.account-inspector[open] h2').textContent")
                    button('.account-inspector[open] button','Rename account')
                    ready("document.querySelector('input[aria-label=\"Account nickname\"]')")
                    cdp('Input.insertText',{'text':'My savings account with a long local nickname for clarity'})
                    screenshot(f'{width}-{theme}-nickname-editor')
                    button('.account-inspector[open] button','Save nickname')
                    ready("document.querySelector('.account-inspector[open] h2').textContent.includes('My savings account')")
                    screenshot(f'{width}-{theme}-renamed-account')
                    current=next(a for a in api('/plaid/live/preview?month=2026-11')['accounts'] if a['name'].startswith('My savings account'))
                    assert current['provider_name']==original_name
                    button('.account-inspector[open] button','Rename account')
                    button('.account-inspector[open] button','Use bank name')
                    ready("document.querySelector('.account-inspector[open] h2').textContent==="+json.dumps(original_name))
                    button('.account-inspector[open] button','Rename account')
                    cdp('Input.insertText',{'text':'Unsaved draft'})
                    button('.account-inspector[open] button','Cancel')
                    assert js("document.querySelector('.account-inspector[open] h2').textContent")==original_name
                    js("document.querySelector('.account-inspector[open] button[aria-label=\"Close account details\"]').click()")

                    nav('Investments');ready("document.querySelector('.investment-row')")
                    assert js("document.querySelector('.investment-sync .chip').textContent")=='LIVE DATA'
                    assert len(api('/plaid/live/investments')['holdings'])==18
                    js('window.scrollTo(0,0)')
                    screenshot(f'{width}-{theme}-live-investments')
                    js("document.querySelector('.investment-row').open=true;document.querySelector('.investment-provider').open=true;document.querySelector('.investment-row').scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-live-investment-expanded')
                    button('.investment-records button','Activity');ready("document.querySelector('.investment-row summary')?.textContent.includes('Synthetic investment')")
                    screenshot(f'{width}-{theme}-live-investment-activity')
                    nav('Backup & restore');ready("document.querySelector('.backup-create')")
                    assert js("document.querySelector('select[aria-label=\"Backup workspace\"]').value")=='live'
                    button('.backup-create button','Create backup');ready("document.querySelector('.backup-message')")
                    button('.backup-row button','Preview restore');ready("document.querySelector('dialog[open]')")
                    screenshot(f'{width}-{theme}-live-backup-preview')
                    button('dialog button','Cancel')
                    print(f'{width}-{theme}: Live readiness, expanded settings, locked institutions, long accounts and backups fit.',flush=True)
            nav('Accounts');ready("document.querySelector('.account-freshness')")
            assert js("document.querySelector('.refresh-all button').textContent")=='Refresh all'
            button('.refresh-all button','Refresh all')
            ready("document.querySelector('.refresh-all > [role=status]')?.textContent.includes('finished')",timeout=40)
            assert js("document.querySelectorAll('.refresh-results li').length")==6
            assert js("document.querySelector('.refresh-results summary').textContent.includes('1 failed')")
            button('.refresh-all button','Refresh all')
            ready("document.querySelector('.refresh-all > [role=status]')?.textContent.includes('finished')",timeout=40)
            assert js("document.querySelector('.refresh-results summary').textContent.includes('5 skipped')")
            report=api('/plaid/live/preview?month=2026-11')
            assert any(a['sync'] and 'balances' in a['sync']['errors'] for a in report['accounts'])
            assert any(a['sync'] and not a['sync']['errors'] and a['sync']['timestamps'].get('transactions') for a in report['accounts'])
            for width in [1280,390]:
                cdp('Emulation.setDeviceMetricsOverride',{'width':width,'height':1000 if width>400 else 844,'deviceScaleFactor':1,'mobile':False})
                for theme in ['dark','light']:
                    if js('document.documentElement.dataset.theme')!=theme:
                        js("document.querySelector('.theme-toggle').click()");ready("!document.querySelector('.theme-toggle').disabled")
                    nav('Accounts');ready("document.querySelector('.account-freshness')")
                    js("document.querySelector('.refresh-results').open=false;document.querySelectorAll('.account-group').forEach(e=>e.open=true);document.querySelector('.accounts-workspace').scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-account-freshness')
                    js("[...document.querySelectorAll('.compact-account')].find(e=>e.querySelector('.sync-warning')?.textContent.includes('errors')).open=true")
                    ready("document.querySelector('.account-inspector[open] .account-sync-details')")
                    js("document.querySelector('.account-inspector[open] .account-sync-details').scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-account-update-details')
                    js("document.querySelector('.account-inspector[open] button[aria-label=\"Close account details\"]').click()")
                    nav('Bank sync');ready("document.querySelector('.refresh-results li')")
                    js("document.querySelector('.refresh-results').open=true;document.querySelector('.refresh-all').scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-refresh-all-results')
            nav('Backup & restore');ready("document.querySelector('.backup-create')")
            button('.backup-create button','Create backup');ready("document.querySelector('.backup-message')")
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
            assert len(api('/plaid/live/investments')['holdings'])==18
            assert api('/plaid/live/investments/activity')['total']==32
            assert next(c for c in api('/plaid/live')['connections'] if c['id']==1)['investments_enabled']
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
