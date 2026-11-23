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
        # Synthetic activity in an isolated copy: no provider calls.
        from datetime import date, timedelta
        from finance.multi_bank import initialize_combined
        from finance.plaid_sandbox import save_transactions
        from finance.sandbox_preview import initialize_preview
        with connect(database.with_suffix('.plaid-combined-sandbox.db'), write=True) as fixture:
            initialize_combined(fixture)
            initialize_preview(fixture)
            account = fixture.execute("SELECT id FROM accounts WHERE currency='USD' ORDER BY rowid LIMIT 1").fetchone()[0]
            month = date.today().isoformat()[:7]
            save_transactions(fixture, [dict(transaction_id='report-expense-'+str(index), account_id=account,
                name='Report purchase '+str(index), date=month+'-01', amount=index+1, iso_currency_code='USD', pending=False,
                personal_finance_category={'primary':'GENERAL_MERCHANDISE'}) for index in range(36)] +
                [dict(transaction_id='report-income', account_id=account, name='Report salary', date=month+'-01',
                amount=-5000, iso_currency_code='USD', pending=False, personal_finance_category={'primary':'INCOME'})], [], [])
            fixture.execute('DELETE FROM preview_snapshot_accounts')
            fixture.execute('DELETE FROM preview_snapshots')
            fixture.execute("UPDATE accounts SET source='fresh',checked_at=?", (date.today().isoformat()+'T12:00:00',))
            fixture.execute("UPDATE multi_connections SET history_status='HISTORICAL_UPDATE_COMPLETE' WHERE profile='banking'")
            identifier=fixture.execute('INSERT INTO preview_snapshots(recorded_at,date,assets_minor,liabilities_minor) VALUES (?,?,0,0)',
                                       (date.today().isoformat()+'T12:00:00',date.today().isoformat())).lastrowid
            fixture.execute('INSERT INTO preview_snapshot_accounts SELECT ?,id,name,currency,current_minor,source,checked_at FROM accounts WHERE active=1',(identifier,))
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
                assert js('document.documentElement.scrollWidth <= document.documentElement.clientWidth'), name
                assert js("[...document.querySelectorAll('dialog[open]')].every(e=>e.scrollWidth<=e.clientWidth+1)"), 'Dialog overflow: '+name
                data = cdp('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})
                (OUTPUT / (name + '.png')).write_bytes(base64.b64decode(data['data']))

            cdp('Page.enable'); cdp('Page.navigate', {'url': url})



            def contrast(first, second):
                def luminance(value):
                    value=value.lstrip('#');value=''.join(c*2 for c in value) if len(value)==3 else value;channels=[int(value[index:index+2],16)/255 for index in (0,2,4)]
                    values=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in channels]
                    return sum(a*b for a,b in zip(values,[.2126,.7152,.0722]))
                low,high=sorted([luminance(first),luminance(second)])
                return (high+.05)/(low+.05)
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
                    key('Escape')
                    if js("Boolean(document.querySelector('dialog[open]'))"): key('Escape')
                    ready("!document.querySelector('dialog[open]')")
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
            ready("document.querySelector('.hero [data-history-start]')")
            for width in (1440,390):
                cdp('Emulation.setDeviceMetricsOverride',{'width':width,'height':1000 if width>850 else 844,'deviceScaleFactor':1,'mobile':False})
                for theme in ('dark','light'):
                    if js('document.documentElement.dataset.theme')!=theme:
                        click('.theme-toggle');ready("!document.querySelector('.theme-toggle').disabled")
                    js("document.querySelector('[data-history-start]').scrollIntoView({block:'center'})")
                    screenshot(f'{width}-{theme}-snapshot-tracking-started')
            # Add synthetic older observations without touching current balances.
            # A missing day between observations must break the solid line.
            with connect(database.with_suffix('.plaid-combined-sandbox.db'),write=True) as fixture:
                for days in (1,3):
                    day=(date.today()-timedelta(days=days)).isoformat()
                    identifier=fixture.execute('INSERT INTO preview_snapshots(recorded_at,date,assets_minor,liabilities_minor) VALUES (?,?,0,0)',(day+'T12:00:00',day)).lastrowid
                    fixture.execute('INSERT INTO preview_snapshot_accounts SELECT ?,id,name,currency,current_minor-?,source,? FROM accounts WHERE active=1',(identifier,days*100,day+'T12:00:00'))
            # Drilldowns reconcile with the report's exact eligible IDs. Sorting
            # is global before pagination, and return navigation retains scope.
            report_month = js("document.querySelector('[aria-label=\"Reporting month\"]').dataset.month")
            preview = json.load(urllib.request.urlopen(url+'/api/plaid/combined-sandbox/preview?month='+report_month))
            nav('Spending');ready("document.querySelector('.report-calendar')")
            click('.report-account-filter > summary')
            click('.report-account-scroll input')
            ready("document.querySelector('.spending-main')")
            selected_account = preview['accounts'][0]['id']
            select('Report category','uncategorized');ready("document.querySelector('.spending-main')")
            expected = json.load(urllib.request.urlopen(url+f'/api/plaid/combined-sandbox/preview/analytics?month={report_month}&account={selected_account}&category=uncategorized'))
            matching=[row for row in preview['transactions'] if row['id'] in expected['eligible_ids']]
            ordered=sorted(matching,key=lambda row:(row['date'],row['id']),reverse=True)
            ready("JSON.stringify([...document.querySelectorAll('.report-transactions .ledger-row')].map(e=>Number(e.dataset.transactionId)))==="+json.dumps(json.dumps([row['id'] for row in ordered[:10]],separators=(',',':'))))
            select('Sort report transactions','largest')
            ordered=sorted(matching,key=lambda row:(-row['amount_minor'],-row['id']))
            ready("JSON.stringify([...document.querySelectorAll('.report-transactions .ledger-row')].map(e=>Number(e.dataset.transactionId)))==="+json.dumps(json.dumps([row['id'] for row in ordered[:10]],separators=(',',':'))))
            if len(ordered)>10:
                click('.report-transactions button','Next')
                ready("JSON.stringify([...document.querySelectorAll('.report-transactions .ledger-row')].map(e=>Number(e.dataset.transactionId)))==="+json.dumps(json.dumps([row['id'] for row in ordered[10:20]],separators=(',',':'))))
            click('.spending-main button','View transactions →');ready("document.querySelector('.drilldown-filter')")
            records=[row for row in preview['transactions'] if row['id'] in expected['eligible_ids']]
            select('Sort transactions','largest')
            expected_ids=[row['id'] for row in sorted(records,key=lambda row:(-row['amount_minor'],-row['id']))[:25]]
            ready("JSON.stringify([...document.querySelectorAll('.ledger-row')].map(e=>Number(e.dataset.transactionId)))==="+json.dumps(json.dumps(expected_ids,separators=(',',':'))))
            select('Filter transaction type','refund')
            ready("[...document.querySelectorAll('.ledger-row')].every(e=>e.querySelector('.transaction-meta').textContent.includes('Refund'))")
            click('.drilldown-filter button','Back to Spending');ready("document.querySelector('.spending-main')")
            assert js("document.querySelector('[aria-label=\"Report category\"]').value")=='uncategorized'
            click('.report-account-filter > summary')
            assert js("document.querySelectorAll('.report-account-scroll input:checked').length")==1
            screenshot('report-account-filter-expanded')
            click('.report-filter-fields button','Reset filters');ready("document.querySelector('.spending-main')")
            # Each Overview cash-flow link uses precisely the eligible entries.
            for control, kinds in [('.flow-income',{'income'}),('.flow-spending',{'expense','refund'}),('.cashflow-total',{'income','expense','refund'})]:
                nav('Overview');ready("document.querySelector('.cashflow-total')")
                click(control);ready("document.querySelector('.drilldown-filter')")
                rows=[row for row in preview['transactions'] if row['currency']=='USD' and not row.get('pending') and not row.get('needs_review') and not row.get('transfer_pair_id') and row['kind'] in kinds]
                expected_ids=[row['id'] for row in sorted(rows,key=lambda row:(row['date'],row['id']),reverse=True)[:25]]
                assert js("[...document.querySelectorAll('.ledger-row')].map(e=>Number(e.dataset.transactionId))")==expected_ids
                click('.drilldown-filter button','Back to Overview');ready("document.querySelector('.cashflow-total')")
            ready("document.querySelector('.composition-total')")
            saved_balances=js("[document.querySelector('.hero-value').textContent,document.querySelector('.composition-total').textContent]")
            index=int(report_month[:4])*12+int(report_month[5:])-2
            earlier=f'{index//12:04}-{index%12+1:02}'
            for current, chosen in ((report_month,earlier),(earlier,report_month)):
                click('.month-picker-trigger')
                ready("document.querySelector('.month-picker-panel:popover-open')")
                if current[:4] != chosen[:4]:
                    year = 'Previous year' if chosen < current else 'Next year'
                    click(f'.month-picker-year button[aria-label="{year}"]')
                click(f'.month-picker-cell:nth-child({int(chosen[5:])})')
                expected_month=date.fromisoformat(chosen+'-01').strftime('%B %Y')
                ready("document.querySelector('.cashflow .section-heading small')?.textContent.startsWith("+json.dumps(expected_month)+") && document.querySelector('.composition-total')")
                assert js("[document.querySelector('.hero-value').textContent,document.querySelector('.composition-total').textContent]")==saved_balances
            print('Report filters retained after drilldown, global sorting before pagination and all three Overview cash-flow links reconciled.',flush=True)
            assert js("document.fonts.load('400 16px Figtree').then(fonts=>fonts.length>0)")
            pages=['Overview','Spending','Transactions','Budgets','Accounts','Investments','Bank sync','Backup & restore']
            for width in (1440,1280,768,390,320):
                cdp('Emulation.setDeviceMetricsOverride',{'width':width,'height':1000 if width>850 else 844,'deviceScaleFactor':1,'mobile':False})
                for theme in ('dark','light'):
                    if js('document.documentElement.dataset.theme')!=theme:
                        click('.theme-toggle');ready("!document.querySelector('.theme-toggle').disabled")
                    palette=js("Object.fromEntries(['--text','--muted','--surface','--accent','--accent-ink','--control-line','--field'].map(k=>[k,getComputedStyle(document.documentElement).getPropertyValue(k).trim()]))")
                    assert contrast(palette['--text'],palette['--surface'])>=4.5
                    assert contrast(palette['--muted'],palette['--surface'])>=4.5
                    assert contrast(palette['--accent-ink'],palette['--accent'])>=4.5
                    assert contrast(palette['--control-line'],palette['--field'])>=3
                    for page in pages:
                        nav(page)
                        if page=='Spending':
                            ready("document.querySelector('.report-calendar .calendar-day')")
                            if width>850:
                                assert js("(()=>{const bottom=document.querySelector('[aria-label=\"Reporting month\"]').getBoundingClientRect().bottom;return [...document.querySelectorAll('.page-header .toolbar > button')].every(e=>Math.abs(e.getBoundingClientRect().bottom-bottom)<3)})()"), 'Header controls are not aligned'
                            click('.report-account-filter > summary')
                            screenshot(f'{width}-{theme}-report-account-filter-expanded')
                            click('.report-account-options button','Done')
                            assert 'By merchant' not in js("document.querySelector('.spending-page').textContent")
                            js("document.querySelector('.report-calendar').scrollIntoView({block:'start'})")
                            screenshot(f'{width}-{theme}-spending-calendar-donut')
                            click('.report-tabs button','Income')
                            ready("document.querySelector('.spending-main h2')?.textContent==='Income this month'")
                            assert 'Report salary' in js("document.querySelector('.report-transactions').textContent")
                            js("document.querySelector('.report-transactions').scrollIntoView({block:'center'})")
                            screenshot(f'{width}-{theme}-income-contributing-transactions')
                            js("document.querySelector('.report-calendar').scrollIntoView({block:'start'})")
                            screenshot(f'{width}-{theme}-income-calendar-donut')
                            click('.report-tabs button','Cash flow')
                            ready("document.querySelector('.spending-main h2')?.textContent==='Cash flow this month'")
                            js("document.querySelector('.report-transactions').scrollIntoView({block:'center'})")
                            screenshot(f'{width}-{theme}-cashflow-contributing-transactions')
                            screenshot(f'{width}-{theme}-cashflow-report')
                            click('.spending-main button','View transactions →')
                            ready("document.querySelector('h1')?.textContent==='Transactions' && document.querySelector('.drilldown-filter')")
                            assert 'Cash flow' in js("document.querySelector('.drilldown-filter').textContent")
                            click('.drilldown-filter button','Back to Spending')
                            ready("document.querySelector('.spending-main h2')?.textContent==='Cash flow this month'")
                            click('.report-tabs button','Spending')
                            ready("document.querySelector('.spending-main h2')?.textContent==='Spending this month'")
                            assert js("document.querySelectorAll('.report-transactions .ledger-row').length")<=10
                            js("document.querySelector('.report-transactions').scrollIntoView({block:'start'})")
                            screenshot(f'{width}-{theme}-spending-contributing-transactions')
                            js('window.scrollTo(0,0)')
                        if page=='Transactions':
                            ready("document.querySelector('.ledger-row .transaction-flow')")
                            assert js("[...document.querySelectorAll('.ledger-row')].every(e=>e.querySelectorAll('.flow-endpoint').length===2 && ['in','out','none'].includes(e.dataset.direction))")
                            assert js("[...document.querySelectorAll('.ledger-row')].every(e=>e.dataset.direction==='out' ? e.querySelector('.row-money .numeric').textContent.trim().startsWith(String.fromCharCode(0x2212)) : e.dataset.direction==='in' ? e.querySelector('.row-money .numeric').textContent.trim().startsWith('+') : true)")
                            js("document.querySelector('.ledger-row').scrollIntoView({block:'center'})")
                            screenshot(f'{width}-{theme}-transaction-flow-rows')

                        ready("!document.querySelector('main > [role=status]')")
                        if page=='Investments':ready("document.querySelector('.investment-holdings')")
                        screenshot(f'{width}-{theme}-redesign-'+page.lower().replace(' ','-').replace('&','and'))
                    if width<=850:
                        rect=js("(()=>{const r=document.querySelector('.mobile-navigation').getBoundingClientRect();return {top:r.top,bottom:r.bottom}})()")
                        assert rect['top']>650 and rect['bottom']<=845,rect
                        click('button[aria-label="More sections"]');ready("document.querySelector('.navigation-sheet[open]')")
                        screenshot(f'{width}-{theme}-redesign-more')
                        key('Escape')
                        if js("Boolean(document.querySelector('dialog[open]'))"): key('Escape')
                        ready("!document.querySelector('dialog[open]')")
                    nav('Overview');ready("document.querySelector('.account-picker')")
                    assert js("document.querySelector('.history-switch button[aria-pressed=true]')?.textContent")=='Snapshots'
                    assert js("document.querySelectorAll('.hero [data-chart-segment]').length")>=2, 'Missing dates were connected'
                    click('.history-switch button','Banking estimates')
                    ready("document.querySelector('.banking-history .estimated-chart .chart-line')")
                    assert 'banking accounts only' in js("document.querySelector('.banking-history').textContent")
                    click('.history-coverage > summary')
                    js("document.querySelector('.banking-history').scrollIntoView({block:'start'})")
                    screenshot(f'{width}-{theme}-banking-estimates-expanded')
                    click('.history-coverage > summary')
                    click('.history-switch button','Snapshots')
                    ready("document.querySelector('.hero .balance-chart:not(.estimated-chart)')")
                    click('.hero .snapshot-guide > summary')
                    js("document.querySelector('.hero .snapshot-guide').scrollIntoView({block:'center'})")
                    screenshot(f'{width}-{theme}-snapshot-guide')
                    click('.hero .snapshot-guide > summary')
                    click('.account-picker-trigger')
                    ready("document.querySelector('.account-options:popover-open')")
                    assert js("""(() => {
                        const done = document.querySelector('.picker-done'), rect = done.getBoundingClientRect();
                        return done.contains(document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2));
                    })()"""), 'Picker action is obscured'
                    screenshot(f'{width}-{theme}-redesign-chart-account-picker')
                    click('.account-picker button','Done')
                    # Inspect an account using real pointer input, then keyboard close/focus return.
                    nav('Accounts');ready("document.querySelector('.account-summary')")
                    click('.account-summary');ready("document.querySelector('.account-inspector[open]')")
                    assert js("document.querySelector('.account-inspector[open]').contains(document.activeElement)")
                    for _ in range(3):key('Tab')
                    assert js("document.querySelector('.account-inspector[open]').contains(document.activeElement)")
                    screenshot(f'{width}-{theme}-redesign-account-inspector')
                    key('Escape')
                    if js("Boolean(document.querySelector('dialog[open]'))"): key('Escape')
                    ready("!document.querySelector('dialog[open]')")
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
