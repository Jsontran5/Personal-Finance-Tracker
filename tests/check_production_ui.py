"""Production All accounts browser QA on a new synthetic database; no bank calls."""
import base64
from datetime import date, datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
OUTPUT=ROOT/'data/ui-qa'

def serve(database,port):
    import os
    from unittest.mock import patch
    import uvicorn
    from finance.api import create_app
    with patch.dict(os.environ,{'APP_ENV':'production','PLAID_LIVE_ENABLED':'false'}):
        app=create_app(Path(database))
        maintenance=app.state.startup_maintenance
        original=maintenance.status
        fixture=Path(database).parent/'ui-startup.json'
        maintenance.status=lambda: json.loads(fixture.read_text()) if fixture.is_file() else original()
        uvicorn.run(app,host='127.0.0.1',port=int(port),access_log=False)

def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));return listener.getsockname()[1]

def run():
    import websocket
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from finance.api import create_app
    from finance.db import connect
    from finance.multi_bank import map_accounts
    OUTPUT.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix='finance-production-ui-') as folder:
        folder=Path(folder);database=folder/'data/finance.db';server=chrome=ws=None
        with patch.dict('os.environ',{'APP_ENV':'production','PLAID_LIVE_ENABLED':'false'}):
            with TestClient(create_app(database),base_url='http://127.0.0.1',headers={'x-finance-request':'1'}) as client:
                loan=client.post('/api/accounts',json={'name':'Manual Nelnet student loan with a long display name','kind':'loan','debt_subtype':'student','opening_balance_minor':2500000,'date':date.today().isoformat()})
                assert loan.status_code==201,loan.text
                client.get('/api/plaid/live/preview',params={'month':date.today().isoformat()[:7]})
        with connect(database.with_suffix('.plaid-live.db'),write=True) as db:
            db.execute("INSERT INTO multi_connections(id,institution_id,name,profile) VALUES (1,'fixture','Fixture bank','banking')")
            map_accounts(db,1,[{'account_id':str(index),'name':'Connected account '+str(index)+' with a long display name','type':'depository','subtype':'checking','balances':{'current':100,'iso_currency_code':'USD'}} for index in range(10)])
        database.with_suffix('.workspace').write_text('sandbox')
        server_port,chrome_port=port(),port()
        def stop(process):
            if process:
                process.terminate()
                try:process.wait(timeout=8)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=8)
        def wait(check):
            deadline=time.monotonic()+18
            while time.monotonic()<deadline:
                try:
                    if check():return
                except (OSError,ValueError):pass
                time.sleep(.12)
            raise AssertionError('Timed out waiting for production UI')
        try:
            server=subprocess.Popen([str(ROOT/'.venv/Scripts/python.exe'),str(Path(__file__)),'--server',str(database),str(server_port)],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            url=f'http://127.0.0.1:{server_port}';wait(lambda:urllib.request.urlopen(url,timeout=1).status==200)
            chrome=subprocess.Popen([r'C:\Program Files\Google\Chrome\Application\chrome.exe','--headless=new','--disable-gpu','--no-first-run','--remote-allow-origins=*',f'--remote-debugging-port={chrome_port}',f'--user-data-dir={folder/"chrome"}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            targets=[]
            def discover():
                targets.extend(json.load(urllib.request.urlopen(f'http://127.0.0.1:{chrome_port}/json/list')));return bool(targets)
            wait(discover)
            ws=websocket.create_connection(next(t['webSocketDebuggerUrl'] for t in targets if t['type']=='page'),timeout=25);sequence=0
            def cdp(method,params=None):
                nonlocal sequence
                sequence+=1;ws.send(json.dumps({'id':sequence,'method':method,'params':params or {}}))
                while True:
                    response=json.loads(ws.recv())
                    if response.get('id')==sequence:
                        assert 'error' not in response,response
                        return response.get('result',{})
            def js(expression):
                result=cdp('Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True})
                assert 'exceptionDetails' not in result,result
                return result.get('result',{}).get('value')
            def ready(expression):wait(lambda:js('Boolean('+expression+')'))
            def click(selector,text=None):
                point=js("(()=>{const e=[...document.querySelectorAll("+json.dumps(selector)+")].find(e=>e.getClientRects().length"+(" && e.textContent.trim()==="+json.dumps(text) if text else '')+");if(!e)throw Error('Missing control');e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
                cdp('Input.dispatchMouseEvent',{'type':'mousePressed','button':'left','clickCount':1,**point});cdp('Input.dispatchMouseEvent',{'type':'mouseReleased','button':'left','clickCount':1,**point})
            def change(selector,value):
                js("(()=>{const e=document.querySelector("+json.dumps(selector)+");const p=e instanceof HTMLSelectElement?HTMLSelectElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(p,'value').set.call(e,"+json.dumps(value)+");e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));})()")
            def close():
                while js("Boolean(document.querySelector('dialog[open]'))"):
                    js("(()=>{const d=[...document.querySelectorAll('dialog[open]')].at(-1);const b=d.querySelector('[aria-label=\"Close\"],[aria-label=\"Close account details\"]');if(b)b.click();else d.close()})()")
            def nav(name):
                close()
                if not js("[...document.querySelectorAll('nav button')].some(b=>b.getClientRects().length&&b.textContent.trim()==="+json.dumps(name)+")"):click('[aria-label="More sections"]');ready("document.querySelector('.navigation-sheet[open]')")
                click('nav button',name);ready("document.querySelector('h1')?.textContent==="+json.dumps(name));js('window.scrollTo(0,0)')
            def shot(name):
                time.sleep(.15)
                assert js('document.documentElement.scrollWidth<=document.documentElement.clientWidth'),name
                assert js("[...document.querySelectorAll('dialog[open]')].every(e=>e.scrollWidth<=e.clientWidth+1)"),name
                (OUTPUT/(name+'.png')).write_bytes(base64.b64decode(cdp('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})['data']))
            cdp('Page.enable');cdp('Page.navigate',{'url':url});ready("document.querySelector('.unified-workspace') && document.querySelector('.hero-value')")
            assert not js("Boolean(document.querySelector('[aria-label=\"Workspace\"],[aria-label=\"Sandbox scenario\"]'))")
            nav('Accounts');click('button','Add manual account');ready("document.querySelector('.editor-inspector[open] input[name=name]')")
            change('.editor-inspector input[name=name]','UI manual cash');change('.editor-inspector select[name=kind]','cash');change('.editor-inspector input[name=amount]','1000');click('.editor-inspector button','Save')
            ready("!document.querySelector('.editor-inspector[open]') && [...document.querySelectorAll('.account-summary')].some(e=>e.textContent.includes('UI manual cash'))")
            js("[...document.querySelectorAll('.account-summary')].find(e=>e.textContent.includes('UI manual cash')).click()")
            ready("document.querySelector('.account-inspector[open]')")
            click('.account-inspector[open] button','Record balance');ready("document.querySelector('.editor-inspector[open]')")
            change('.editor-inspector input[name=amount]','1200');click('.editor-inspector button','Save');ready("!document.querySelector('.editor-inspector[open]')")
            close();nav('Transactions');click('button','Add manual transaction');ready("document.querySelector('.editor-inspector input[name=amount]')")
            manual=json.load(urllib.request.urlopen(url+'/api/overview?month='+date.today().isoformat()[:7]))
            cash=next(a for a in manual['accounts'] if a['name']=='UI manual cash')
            js("(()=>{const e=[...document.querySelectorAll('.editor-inspector select')].find(s=>[...s.options].some(o=>o.textContent.startsWith('UI manual cash')));Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e,"+json.dumps(str(cash['id']))+");e.dispatchEvent(new Event('change',{bubbles:true}));})()")
            change('.editor-inspector input[name=amount]','25');change('.editor-inspector input[name=merchant]','Production manual purchase');click('.editor-inspector button','Save transaction')
            ready("!document.querySelector('.editor-inspector[open]') && [...document.querySelectorAll('.ledger-row')].some(e=>e.textContent.includes('Production manual purchase'))")
            js("[...document.querySelectorAll('.ledger-row')].find(e=>e.textContent.includes('Production manual purchase')).click()")
            ready("document.querySelector('.editor-inspector input[name=amount]')")
            change('.editor-inspector input[name=amount]','35');click('.editor-inspector button','Save transaction');ready("!document.querySelector('.editor-inspector[open]')")
            saved=json.load(urllib.request.urlopen(url+'/api/overview?month='+date.today().isoformat()[:7]))
            assert saved['transactions'][0]['amount_minor']==3500
            for width in (1440,390):
                cdp('Emulation.setDeviceMetricsOverride',{'width':width,'height':1000 if width>850 else 844,'deviceScaleFactor':1,'mobile':False})
                for theme in ('dark','light'):
                    close()
                    if js('document.documentElement.dataset.theme')!=theme:
                        click('.theme-toggle');ready("document.documentElement.dataset.theme==="+json.dumps(theme)+" && !document.querySelector('.theme-toggle').disabled")
                    for page in ['Overview','Accounts','Transactions','Budgets','Investments','Bank sync','Backup & restore']:
                        nav(page);ready("!document.querySelector('main > [role=status]')")
                        assert not js("Boolean(document.querySelector('[aria-label=\"Workspace\"],[aria-label=\"Sandbox scenario\"]'))")
                        if page=='Bank sync':ready("document.querySelector('.live-readiness')");assert not js("Boolean(document.querySelector('.manual-selection'))")
                        if page=='Backup & restore':assert js("[...document.querySelectorAll('[aria-label=\"Backup workspace\"] option')].every(o=>!o.textContent.includes('Sandbox'))")
                        shot(f'{width}-{theme}-production-'+page.lower().replace(' ','-').replace('&','and'))
                    nav('Bank sync');ready("document.querySelector('.startup-status')?.textContent.includes('Startup finished')")
                    js("document.querySelector('.startup-status summary').click()")
                    shot(f'{width}-{theme}-production-startup-expanded')
                    assert 'snapshot' in js("document.querySelector('.startup-status').textContent")
                    js("document.querySelector('.startup-status summary').click()")
                    fixture=database.parent/'ui-startup.json'
                    status={'enabled':True,'running':True,'phase':'A very long institution name · balances','version':10,'finished_at':None,'results':[]}
                    fixture.write_text(json.dumps(status));js('location.reload()')
                    ready("document.querySelector('.startup-toast')?.textContent.includes('Updating your accounts')")
                    shot(f'{width}-{theme}-production-startup-loading')
                    assert not js("Boolean(document.querySelector('.startup-status'))")
                    nav('Accounts') # Loading notification does not block navigation.
                    status.update(running=False,version=11,finished_at=datetime.now(timezone.utc).isoformat(),results=[{'institution':'Fixture bank','product':'balances','status':'Updated','message':'Saved balances.'}])
                    fixture.write_text(json.dumps(status))
                    ready("document.querySelector('.startup-toast')?.textContent.includes('Startup complete')")
                    click('.startup-toast button','View details');ready("document.querySelector('h1')?.textContent==='Bank sync' && document.querySelector('.startup-status details')?.open")
                    nav('Overview');ready("!document.querySelector('.startup-toast')")
                    assert not js("Boolean(document.querySelector('.startup-status'))")
                    shot(f'{width}-{theme}-production-overview-clean')
                    status.update(version=12,results=[{'institution':'Fixture bank','product':'balances','status':'Failed','message':'Reconnect this institution in Bank sync. Saved balances retained.'}])
                    fixture.write_text(json.dumps(status));js('location.reload()')
                    ready("document.querySelector('.startup-toast')?.textContent.includes('Startup needs attention')")
                    if width==1440 and theme=='dark':
                        time.sleep(6.5)
                        assert js("Boolean(document.querySelector('.startup-toast'))"),'Failure must not auto-dismiss'
                    shot(f'{width}-{theme}-production-startup-failure')
                    click('[aria-label="Dismiss startup notification"]');ready("!document.querySelector('.startup-toast')")
                    nav('Bank sync');ready("document.querySelector('.startup-status')?.textContent.includes('finished with issues')")
                    fixture.unlink()
                    js('location.reload()');ready("document.querySelector('.unified-workspace') && document.querySelector('.hero-value')")
                    nav('Accounts');js("[...document.querySelectorAll('.account-summary')].find(e=>e.textContent.includes('UI manual cash')).click()")
                    ready("document.querySelector('.account-inspector[open]')");shot(f'{width}-{theme}-production-manual-inspector')
                    assert 'Record balance' in js("document.querySelector('.account-inspector[open]').textContent")
                    print(f'{width}-{theme}: unified production pages, manual editor and no Sandbox controls passed.',flush=True)
            nav('Backup & restore');click('button','Back up all accounts');ready("document.querySelector('.backup-message')?.textContent.includes('All accounts backed up')")
            assert len(list((folder/'backups').glob('*.finance-backup.zip')))==2
            with connect(database.with_suffix('.plaid-live.db')) as db:
                assert db.execute("SELECT COUNT(*) FROM accounts WHERE source<>'manual' AND active=1").fetchone()[0]==10
                assert db.execute('SELECT COUNT(*) FROM multi_connections WHERE item_id IS NOT NULL').fetchone()[0]==0
            print('Manual add, dated balance, full transaction edit, paired backups and untouched bank records passed. No provider calls.',flush=True)
        except Exception:
            if ws:
                try:shot('production-ui-failure');print(js('document.body.innerText')[-2500:])
                except Exception:pass
            raise
        finally:
            if ws:ws.close()
            stop(chrome);stop(server)

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--server':serve(sys.argv[2],sys.argv[3])
    else:run()
