"""Synthetic budget overview browser QA; no saved financial databases or providers."""
import base64,json,os,socket,subprocess,sys,tempfile,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def freeport():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def serve(database,port):
    os.environ.update(APP_ENV='development',APP_STARTUP_ENABLED='false')
    from unittest.mock import patch
    import uvicorn
    from finance.api import create_app
    with patch('finance.plaid_sandbox.urlopen',side_effect=AssertionError('No provider calls in UI QA')):
        uvicorn.run(create_app(Path(database)),host='127.0.0.1',port=int(port),access_log=False)

def run():
    import websocket
    from fastapi.testclient import TestClient
    from finance.api import create_app
    os.environ.update(APP_ENV='development',APP_STARTUP_ENABLED='false')
    output=ROOT/'data/ui-qa';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='finance-budget-overview-') as tmp:
        database=Path(tmp)/'data/finance.db'
        with TestClient(create_app(database),base_url='http://127.0.0.1',headers={'x-finance-request':'1'}) as c:
            from datetime import date
            today=date.today().isoformat();month=today[:7]
            account=c.post('/api/accounts',json=dict(name='Synthetic checking with a long account name',kind='checking',opening_balance_minor=2400000,date=today)).json()['id']
            plan=c.get('/api/plan?month='+month).json();category=plan['categories'][0]['id']
            for kind,amount in [('income',500000),('expense',119000),('refund',10000)]:
                r=c.post('/api/transactions',json=dict(account_id=account,kind=kind,amount_minor=amount,date=today,merchant='Synthetic '+kind,category_id=category if kind!='income' else None));assert r.status_code==201,r.text
            r=c.post('/api/transactions',json=dict(account_id=account,kind='expense',amount_minor=1000,date=today,merchant='Synthetic uncategorized purchase'));assert r.status_code==201,r.text
            for i in range(16):
                r=c.post('/api/accounts',json=dict(name='Another synthetic account with a long institution name '+str(i),kind='checking',opening_balance_minor=0,date=today));assert r.status_code==201,r.text
            for i in range(30):
                r=c.post('/api/categories',json=dict(name='Long synthetic category for everyday purchases '+str(i),kind='expense'));assert r.status_code==201,r.text
            for name,bucket in [('Shopping','wants'),('Utilities','savings')]:
                cat=next(cat for cat in c.get('/api/plan?month='+month).json()['categories'] if cat['name']==name)
                assert c.put('/api/plan/categories/'+str(cat['id']),json=dict(group='Everyday spending',bucket=bucket)).status_code==200
            plan=c.get('/api/plan?month='+month).json()
            assert c.put('/api/plan/settings?month='+month,json=dict(token=plan['token'],method='ratio',ratios=[50,30,20])).status_code==200
            # A different store uses its own IDs and bank classification editor.
            assert c.get('/api/plaid/live').status_code==200
            from finance.db import connect
            from finance.multi_bank import map_accounts
            with connect(database.with_suffix('.plaid-live.db'),write=True) as db:
                db.execute("INSERT INTO multi_connections(id,institution_id,name,profile) VALUES (1,'ins_synthetic','Synthetic bank','banking')")
                map_accounts(db,1,[dict(account_id='checking',name='Synthetic bank checking',type='depository',subtype='checking',balances=dict(current=24000,iso_currency_code='USD'))])
                db.execute("INSERT INTO transactions VALUES ('p1:purchase','p1:checking',?,'Synthetic bank purchase',4200,'USD','expense',0,0)",(today,))
                db.execute("INSERT INTO preview_edits VALUES ('p1:purchase','Synthetic bank purchase','expense',1,'Saved bank note')")

        sp,cp=freeport(),freeport();server=chrome=ws=None
        try:
            server=subprocess.Popen([sys.executable,__file__,'--server',str(database),str(sp)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            url=f'http://127.0.0.1:{sp}'
            def wait(fn):
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    try:
                        if fn():return
                    except (OSError,ValueError):pass
                    time.sleep(.1)
                raise AssertionError('Browser condition timed out')
            wait(lambda:urllib.request.urlopen(url).status==200)
            chrome=subprocess.Popen([r'C:\Program Files\Google\Chrome\Application\chrome.exe','--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--remote-allow-origins=*',f'--remote-debugging-port={cp}',f'--user-data-dir={Path(tmp)/"chrome"}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            targets=[]
            def discover():
                targets.extend(json.load(urllib.request.urlopen(f'http://127.0.0.1:{cp}/json/list')));return bool(targets)
            wait(discover)
            ws=websocket.create_connection(next(t['webSocketDebuggerUrl'] for t in targets if t['type']=='page'),timeout=20)
            sequence=0
            def cdp(method,params={}):
                nonlocal sequence
                sequence+=1;ws.send(json.dumps(dict(id=sequence,method=method,params=params)))
                while True:
                    result=json.loads(ws.recv())
                    if result.get('id')==sequence:
                        assert 'error' not in result,result
                        return result.get('result',{})
            def js(code):
                result=cdp('Runtime.evaluate',dict(expression=code,returnByValue=True,awaitPromise=True));assert 'exceptionDetails' not in result,result
                return result.get('result',{}).get('value')
            def click(text,selector='button'):
                js('[...document.querySelectorAll('+json.dumps(selector)+')].find(e=>e.textContent.trim()==='+json.dumps(text)+').click()')
            def shot(name):
                time.sleep(.2)
                assert js('document.documentElement.scrollWidth<=document.documentElement.clientWidth'),name
                assert js("[...document.querySelectorAll('dialog[open]')].every(e=>e.scrollWidth<=e.clientWidth+1)"),name
                (output/(name+'.png')).write_bytes(base64.b64decode(cdp('Page.captureScreenshot',dict(format='png',captureBeyondViewport=False))['data']))
            cdp('Page.enable');cdp('Page.navigate',dict(url=url));wait(lambda:js("[...document.querySelectorAll('nav button')].some(e=>e.textContent.trim()==='Budgets')"));click('Budgets','nav button')
            wait(lambda:js("Boolean(document.querySelector('.budget-monthly'))"))
            assert js("document.querySelector('.budget-monthly').innerText.includes('$3,900.00')")
            for width in (1440,390,320):
                cdp('Emulation.setDeviceMetricsOverride',dict(width=width,height=1000 if width>850 else 844,deviceScaleFactor=1,mobile=False))
                for theme in ('dark','light'):
                    if js('document.documentElement.dataset.theme')!=theme:
                        js("document.querySelector('.theme-toggle').click()")
                        wait(lambda:js('!document.querySelector(".theme-toggle").disabled'))
                    js('window.scrollTo(0,0)');shot(f'budget-overview-{width}-{theme}')
                    js("document.querySelector('.budget-monthly-categories').open=true;document.querySelector('.budget-monthly-categories').scrollIntoView()")
                    shot(f'budget-overview-categories-{width}-{theme}')
                    js("document.querySelector('.budget-monthly-categories button').click()")
                    wait(lambda:js("Boolean(document.querySelector('dialog[open]'))"));shot(f'budget-overview-inspector-{width}-{theme}')
                    js("[...document.querySelectorAll('dialog details')].find(e=>e.querySelector('summary').textContent.startsWith('Transactions')).open=true")
                    expected=js("document.querySelector('.budget-transaction-link strong').textContent")
                    shot(f'budget-transaction-link-{width}-{theme}')
                    cdp('Page.bringToFront')
                    js("document.querySelector('.budget-transaction-link').focus()")
                    assert js("document.activeElement.matches('.budget-transaction-link')")
                    cdp('Input.dispatchKeyEvent',dict(type='keyDown',key='Enter',code='Enter',windowsVirtualKeyCode=13,text='\r'))
                    cdp('Input.dispatchKeyEvent',dict(type='keyUp',key='Enter',code='Enter',windowsVirtualKeyCode=13))
                    wait(lambda:js("document.querySelector('#dialog-title')?.textContent==='Edit transaction'"))
                    assert js("document.querySelector('dialog input[name=merchant]').value") == expected
                    assert js("document.querySelector('h1').textContent==='Transactions'")
                    shot(f'budget-transaction-editor-{width}-{theme}')
                    js("document.querySelector('button[aria-label=\"Close\"]').click()")
                    click('Back to Budgets');wait(lambda:js("Boolean(document.querySelector('.budget-monthly'))"))
                    js("document.querySelector('.budget-monthly-categories').open=false")
            click('Guide: 50 / 30 / 20 ▾');wait(lambda:js("Boolean(document.querySelector('dialog[open]'))"))
            assert js("document.querySelectorAll('dialog input[type=number]').length===0")
            js("(()=>{const e=document.querySelector('dialog select');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e,'custom');e.dispatchEvent(new Event('change',{bubbles:true}));})()")
            wait(lambda:js("document.querySelectorAll('dialog input[type=number]').length===3"));shot('budget-overview-custom-percentages')
            js("(()=>{for(const [name,value] of [['needs','60'],['wants','20'],['savings','20']]){const e=document.querySelector('input[name='+name+']');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,value);e.dispatchEvent(new Event('input',{bubbles:true}));}})()")
            click('Save budget');wait(lambda:js("!document.querySelector('dialog[open]')"));wait(lambda:js("Boolean(document.querySelector('.budget-monthly'))"))
            assert js("document.querySelector('.budget-monthly').innerText.includes('60 / 20 / 20')")
            click('Detailed plan');wait(lambda:js("Boolean(document.querySelector('.budget-table'))"))
            def select_group(value):
                js("(()=>{const e=document.querySelector('select[aria-label=\"Group budget categories by\"]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e,"+json.dumps(value)+");e.dispatchEvent(new Event('change',{bubbles:true}));})()")
            assert js("document.querySelector('select[aria-label=\"Group budget categories by\"]').value==='buckets'")
            assert js("[...document.querySelectorAll('.budget-group summary strong')].map(e=>e.textContent).join('|')==='Needs|Wants|Savings & extra debt|Uncategorized'")
            # Switching presentation must not reset an in-progress assignment edit.
            js("(()=>{const e=document.querySelector('input[aria-label=\"Dining assigned\"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'12.34');e.dispatchEvent(new Event('input',{bubbles:true}));})()")
            for width in (1440,390,320):
                cdp('Emulation.setDeviceMetricsOverride',dict(width=width,height=1000 if width>850 else 844,deviceScaleFactor=1,mobile=False))
                for theme in ('dark','light'):
                    if js('document.documentElement.dataset.theme')!=theme:
                        js("document.querySelector('.theme-toggle').click()")
                        wait(lambda:js('!document.querySelector(".theme-toggle").disabled'))
                    for grouping in ('buckets','custom'):
                        select_group(grouping)
                        expected_groups='Needs|Wants|Savings & extra debt|Uncategorized' if grouping=='buckets' else 'Everyday spending|Needs attention'
                        assert js("[...document.querySelectorAll('.budget-group summary strong')].map(e=>e.textContent).join('|')")==expected_groups
                        assert js("document.querySelector('input[aria-label=\"Dining assigned\"]').value==='12.34'")
                        js("document.querySelector('.budget-grouping').scrollIntoView({block:'start'});document.querySelectorAll('.budget-group').forEach(e=>e.open=true)")
                        shot(f'budget-grouping-{grouping}-{width}-{theme}')
                        js("document.querySelectorAll('.budget-group').forEach(e=>e.open=false)")
                        shot(f'budget-grouping-{grouping}-collapsed-{width}-{theme}')
            select_group('buckets');js("document.querySelectorAll('.budget-group').forEach(e=>e.open=true)")
            click('Discard edits')
            # Changing a category bucket relocates its existing row, preserving its custom group.
            js("[...document.querySelectorAll('.budget-table .text-button')].find(e=>e.textContent==='Dining').click()")
            wait(lambda:js("Boolean(document.querySelector('dialog[open]'))"))
            js("(()=>{const e=document.querySelector('select[name=bucket]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e,'wants');e.dispatchEvent(new Event('change',{bubbles:true}));})()")
            click('Save category');wait(lambda:js("!document.querySelector('dialog[open]')"))
            wait(lambda:js("[...document.querySelectorAll('.budget-group')].find(e=>e.querySelector('summary strong').textContent==='Wants').innerText.includes('Dining')"))
            select_group('custom')
            assert js("[...document.querySelectorAll('.budget-group')].find(e=>e.querySelector('summary strong').textContent==='Everyday spending').innerText.includes('Dining')")
            select_group('buckets');shot('budget-overview-detailed-plan')
            click('Monthly overview');click('Explore spending');wait(lambda:js("document.body.innerText.includes('Where your money went')"));shot('budget-overview-breakdown')
            assert js("document.querySelector('select[aria-label=\"Budget report account\"]').options.length===18")
            click('Groups');js("document.querySelector('.budget-legend-row').click()")
            wait(lambda:js("Boolean(document.querySelector('.budget-transaction-link'))"))
            expected=js("document.querySelector('.budget-transaction-link strong').textContent")
            js("document.querySelector('.budget-transaction-link').click()")
            wait(lambda:js("document.querySelector('#dialog-title')?.textContent==='Edit transaction'"))
            assert js("document.querySelector('dialog input[name=merchant]').value")==expected
            js("document.querySelector('button[aria-label=\"Close\"]').click()")
            click('Back to Budgets');wait(lambda:js("Boolean(document.querySelector('.budget-monthly'))"))
            # Switch to the synthetic bank store and verify the exact imported entry opens.
            js("(()=>{const e=document.querySelector('select[aria-label=\"Workspace\"]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e,'live');e.dispatchEvent(new Event('change',{bubbles:true}));})()")
            wait(lambda:js("Boolean(document.querySelector('.budget-monthly')) && document.querySelector('select[aria-label=\"Workspace\"]').value==='live'"))
            wait(lambda:js("document.querySelector('.budget-monthly').innerText.includes('$42.00')"))
            js("document.querySelector('.budget-monthly-categories').open=true;[...document.querySelectorAll('.budget-monthly-categories button')].find(e=>e.textContent.includes('Groceries')).click()")
            wait(lambda:js("Boolean(document.querySelector('dialog[open]'))"))
            js("[...document.querySelectorAll('dialog details')].find(e=>e.querySelector('summary').textContent.startsWith('Transactions')).open=true;document.querySelector('.budget-transaction-link').click()")
            wait(lambda:js("document.querySelector('#dialog-title')?.textContent==='Classify bank transaction'"))
            assert js("document.querySelector('dialog input[name=merchant]').value==='Synthetic bank purchase'")
            shot('budget-bank-transaction-editor')
            print('Budget overview: exact income/spending/surplus, long category lists expanded/collapsed, custom-only percentage fields, method save without funding, detail/breakdown navigation, keyboard transaction links with return route, exact manual/bank editors, themes at 1440/390/320 passed.')
        except Exception:
            if ws:
                print(js('document.body.innerText').encode('ascii',errors='replace').decode(),flush=True)
                shot('budget-overview-failure')
            raise
        finally:
            if ws:ws.close()
            for process in [chrome,server]:
                if process:
                    process.terminate()
                    try:process.wait(timeout=8)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=8)

if __name__=='__main__':
    if sys.argv[1:2]==['--server']:serve(*sys.argv[2:])
    else:run()
