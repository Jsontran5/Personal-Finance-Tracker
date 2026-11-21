"""Local budget allocations. Never change bank balances or provider transactions."""
from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .db import connect
from .review import review_rows
from .spending import previous_month


def initialize_budget(db):
    statements = [
        'CREATE TABLE IF NOT EXISTS budget_schema(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL)',
        'CREATE TABLE IF NOT EXISTS budget_settings(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)',
        '''CREATE TABLE IF NOT EXISTS budget_categories(category_id INTEGER PRIMARY KEY,group_name TEXT NOT NULL,
           bucket TEXT NOT NULL,target_minor INTEGER NOT NULL DEFAULT 0,target_type TEXT NOT NULL DEFAULT 'monthly',
           target_date TEXT,repeat_monthly INTEGER NOT NULL DEFAULT 0)''',
        '''CREATE TABLE IF NOT EXISTS budget_events(id INTEGER PRIMARY KEY,batch TEXT NOT NULL,date TEXT NOT NULL,
           category TEXT NOT NULL,amount_minor INTEGER NOT NULL,kind TEXT NOT NULL,source TEXT,signature TEXT,
           note TEXT NOT NULL,created_at TEXT NOT NULL)''',
        'CREATE TABLE IF NOT EXISTS budget_undos(batch TEXT PRIMARY KEY,created_at TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS budget_skipped(source TEXT PRIMARY KEY,signature TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS budget_seen(source TEXT PRIMARY KEY,observed_at TEXT NOT NULL)',
    ]
    for sql in statements:
        db.execute(sql)
    db.execute('INSERT OR IGNORE INTO budget_schema VALUES (1,1)')
    if db.execute('SELECT version FROM budget_schema WHERE id=1').fetchone()[0] != 1:
        raise ValueError('Unsupported budget feature schema.')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def signature(tx):
    return digest([tx.get(key) for key in ['source_id','account_id','destination_id','other_account_id','direction','date','amount_minor','kind','category_id','pending','needs_review','transfer_pair_id']])


def ledger(db, sandbox):
    if sandbox:
        from .sandbox_preview import preview_accounts
        accounts = preview_accounts(db)
    else:
        from .ledger import account_balances
        accounts = account_balances(db, date.today())
    rows = review_rows(db, sandbox)
    seen=dict(db.execute('SELECT source,observed_at FROM budget_seen'))
    by_source = {str(tx['source_id']): tx for tx in rows}
    pairs = {row['id']: row for row in db.execute("SELECT * FROM transfer_links WHERE state='confirmed'")}
    for tx in rows:
        tx['source_id'] = str(tx['source_id'])
        tx['observed_at']=seen.get(tx['source_id'],datetime.now(timezone.utc).isoformat())
        tx['direction'] = -1 if (tx['amount_minor'] > 0 if sandbox else tx['kind'] not in {'income','refund'}) else 1
        tx['amount_minor'] = abs(tx['amount_minor'])
        tx['other_account_id'] = tx.get('destination_id')
        if tx.get('transfer_pair_id'):
            pair = pairs[tx['transfer_pair_id']]
            peer = by_source.get(str(pair['incoming_id'] if tx['transfer_role']=='outgoing' else pair['outgoing_id']))
            tx['other_account_id'] = peer['account_id'] if peer else None
            tx['direction'] = -1 if tx['transfer_role']=='outgoing' else 1
        if sandbox and tx['source_id'].startswith('m:'):
            raw = db.execute('SELECT payload FROM transaction_details WHERE transaction_id=?',(tx['source_id'],)).fetchone()
            payload = json.loads(raw[0]) if raw else {}
            original = payload.get('destination_id') if tx['direction'] < 0 else payload.get('account_id')
            if original:
                other = db.execute('SELECT a.rowid FROM accounts a JOIN connection_accounts m ON a.id=m.account_id WHERE m.manual_id=?',(original,)).fetchone()
                tx['other_account_id'] = other[0] if other else None
        tx['signature'] = signature(tx)
    return accounts, rows


def config(db):
    saved = db.execute('SELECT payload FROM budget_settings WHERE id=1').fetchone()
    return json.loads(saved[0]) if saved else {'method':'categories','ratios':[50,30,20], 'cash_accounts':[], 'credit_accounts':[]}


def projection(db, sandbox, month):
    accounts, rows = ledger(db, sandbox)
    settings = config(db)
    cats = [dict(row) for row in db.execute('SELECT * FROM '+('preview_categories' if sandbox else 'categories')+" WHERE kind='expense' ORDER BY name")]
    metadata = {row['category_id']:dict(row) for row in db.execute('SELECT * FROM budget_categories')}
    table = 'preview_budgets' if sandbox else 'monthly_budgets'
    limits = dict(db.execute(f'SELECT category_id,limit_minor FROM {table} WHERE month=?',(month,)))
    events = [dict(row) for row in db.execute('SELECT e.* FROM budget_events e LEFT JOIN budget_undos u ON e.batch=u.batch WHERE u.batch IS NULL ORDER BY e.id')]
    token = digest([settings, accounts, [{k:v for k,v in tx.items() if k!='observed_at'} for tx in rows], events, metadata, limits])
    current = [tx for tx in rows if tx['currency']=='USD' and tx['date'][:7]==month and tx['date']<=date.today().isoformat()]
    actual, previous, txids = defaultdict(int), defaultdict(int), defaultdict(list)
    for tx in rows:
        if tx['currency']!='USD' or tx['pending'] or tx['needs_review'] or tx.get('transfer_pair_id') or tx['kind'] not in {'expense','refund'} or tx['date']>date.today().isoformat():
            continue
        value = tx['amount_minor'] * (-1 if tx['kind']=='refund' else 1)
        if tx['date'][:7]==month:
            actual[tx['category_id']] += value; txids[tx['category_id']].append(tx['id'])
        elif tx['date'][:7]==previous_month(month):
            previous[tx['category_id']] += value
    balances, assigned, reserves, unpaid = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int)
    funding_actual = defaultdict(int)
    warnings = []
    receipts = []
    ready = settings.get('opening_minor',0)
    cash = set(settings['cash_accounts']); cards = set(settings['credit_accounts'])
    baseline = settings.get('baseline',{})
    start = settings.get('start_date')
    until = min(month+'-31', date.today().isoformat())
    eligible = [tx for tx in rows if tx['currency']=='USD' and not tx['pending'] and not tx['needs_review'] and start and start<=tx['date']<=until and tx['source_id'] not in baseline
        and not (tx['kind']=='investment' and (not sandbox or tx['source_id'].startswith('m:')))]
    bysource = {tx['source_id']:tx for tx in rows}
    for key, original in baseline.items():
        if key not in bysource or bysource[key]['signature']!=original:
            warnings.append('An opening transaction changed or disappeared. Review the opening funding anchor.')
            break
    for ev in events:
        current_signature=bysource.get(ev['source'],{}).get('signature')
        if ev['source'] and current_signature!=ev['signature'] and settings.get('acknowledged',{}).get(ev['source'],'unreviewed')!=current_signature:
            warnings.append('An allocated income or linked transfer changed. Undo its allocation and reconcile before assigning more.')
    timeline = [(tx['date'],tx['observed_at'],0,tx['id'],'transaction',tx) for tx in eligible]
    timeline += [(ev['date'],ev['created_at'],1,ev['id'],'event',ev) for ev in events if ev['date']<=until]
    last_month = start[:7] if start else month
    used_sources = {ev['source']:ev for ev in events if ev['kind']=='use'}

    def fund(key, amount):
        # Cover unfunded card purchases first; reserve the cash for that card.
        if amount > 0:
            for (category,card), owed in list(unpaid.items()):
                if category == key and owed:
                    covered = min(amount,owed); unpaid[(category,card)]-=covered
                    reserves[card]+=covered; amount-=covered
        balances[key]+=amount

    for day, _, _, _, kind, item in sorted(timeline):
        if day[:7]!=last_month:
            # Cash overspending consumes later unassigned cash; card debt stays explicit.
            for key,value in list(balances.items()):
                if value < 0:
                    ready+=value; balances[key]=0
            unpaid.clear()  # Prior unfunded credit spending is now existing card debt.
            last_month=day[:7]
        if kind=='event':
            key=item['category']; value=item['amount_minor']
            if item['kind']=='use':
                continue
            if item['kind']=='reconcile':
                ready+=value
                continue
            if key.startswith('card:'):
                reserves[int(key[5:])]+=value
            else:
                fund(key,value)
            ready-=value
            if day[:7]==month:
                assigned[key]+=value
            continue
        tx=item; amount=tx['amount_minor']; acc=tx['account_id']; key='c:'+str(tx['category_id'] or 0)
        transfer=tx['kind'] in {'transfer','investment'} or tx.get('transfer_pair_id')
        if not transfer and tx['kind'] in {'expense','refund'}:
            if acc not in cash|cards:
                continue
            if tx['date'][:7]==month:
                funding_actual[key]+=amount*(-1 if tx['kind']=='refund' else 1)
            if acc in cash:
                balances[key]+=amount if tx['kind']=='refund' else -amount
            elif tx['kind']=='expense':
                funded=min(max(balances[key],0),amount)
                balances[key]-=funded; reserves[acc]+=funded; unpaid[(key,acc)]+=amount-funded
            else:
                debt=min(unpaid[(key,acc)],amount); unpaid[(key,acc)]-=debt
                refund=amount-debt; release=min(reserves[acc],refund)
                reserves[acc]-=release; balances[key]+=release
                if refund>release:
                    warnings.append('A card refund exceeds reserved cash. Reconcile the card before assigning more.')
        elif acc in cash:
            if tx['kind']=='income' and not transfer:
                ready+=amount
                consumed=sum(ev['amount_minor'] for ev in events if ev['source']==tx['source_id'] and ev['kind']=='assign')
                skipped=db.execute('SELECT signature FROM budget_skipped WHERE source=?',(tx['source_id'],)).fetchone()
                if amount>consumed and not (skipped and skipped[0]==tx['signature']):
                    receipts.append({**tx,'remaining_minor':amount-consumed})
            elif transfer:
                other=tx['other_account_id']
                if other in cash:
                    continue
                if tx['direction']<0 and other in cards:
                    reserved=min(reserves[other],amount); reserves[other]-=reserved; ready-=amount-reserved
                    if amount>reserved:
                        warnings.append('A card payment used unreserved cash. Review card funding.')
                elif tx['direction']<0:
                    linked=used_sources.get(tx['source_id'])
                    if linked and linked['signature']==tx['signature']:
                        balances[linked['category']]-=amount
                    else:
                        ready-=amount
                        warnings.append('An outgoing transfer left the budget. Link it to a savings/debt goal, or leave it against unassigned cash.')
                else:
                    warnings.append('An incoming transfer has no confirmed budget counterpart. Classify/reconcile it before assigning this money.')
    if last_month < month:
        for key,value in list(balances.items()):
            if value<0:
                ready+=value; balances[key]=0
        unpaid.clear()
    modeled_cash=ready+sum(balances.values())+sum(reserves.values())
    saved_cash=sum(a['balance_minor'] or 0 for a in accounts if a['id'] in cash)
    difference=saved_cash-modeled_cash
    if start and month==date.today().isoformat()[:7] and difference:
        warnings.append('Opening funding and recorded activity differ from saved cash balances. Review funding before assigning more.')
    result=[]
    for cat in cats:
        meta=metadata.get(cat['id'],{})
        key='c:'+str(cat['id']); target=meta.get('target_minor',0)
        available=balances[key]-sum(value for (category,_),value in unpaid.items() if category==key)
        limit=limits.get(cat['id'])
        if limit is None and meta.get('repeat_monthly'):
            prior=db.execute(f'SELECT limit_minor FROM {table} WHERE category_id=? AND month<? ORDER BY month DESC LIMIT 1',(cat['id'],month)).fetchone()
            limit=prior[0] if prior else None
        target_type=meta.get('target_type','monthly')
        basis=assigned[key] if target_type=='contribution' else max(0,available+funding_actual[key]) if target_type=='monthly' else max(available,0)
        need=max(0,target-basis)
        if meta.get('target_type')=='dated' and meta.get('target_date'):
            end=date.fromisoformat(meta['target_date']); nowmonth=date.fromisoformat(month+'-01')
            months=max(1,(end.year-nowmonth.year)*12+end.month-nowmonth.month+1)
            need=(need+months-1)//months
        result.append({**cat,'group':meta.get('group_name','Everyday spending'),'bucket':meta.get('bucket','needs'),
            'target_minor':target,'target_type':meta.get('target_type','monthly'),'target_date':meta.get('target_date'),
            'repeat_monthly':bool(meta.get('repeat_monthly')),'limit_minor':limit,'assigned_minor':assigned[key],
            'activity_minor':-funding_actual[key],'available_minor':available,'cash_available_minor':balances[key],
            'actual_minor':actual[cat['id']],'previous_minor':previous[cat['id']],'need_minor':need,'transaction_ids':txids[cat['id']]})
    unknown_debt=sum(v for (category,_),v in unpaid.items() if category=='c:0')
    if actual[None] or balances['c:0'] or assigned['c:0'] or unknown_debt:
        result.append({'id':0,'name':'Uncategorized','kind':'expense','group':'Needs attention','bucket':'needs','target_minor':0,
            'target_type':'monthly','target_date':None,'repeat_monthly':False,'limit_minor':None,'assigned_minor':assigned['c:0'],
            'activity_minor':-funding_actual['c:0'],'available_minor':balances['c:0']-unknown_debt,'cash_available_minor':balances['c:0'],
            'actual_minor':actual[None],'previous_minor':previous[None],'need_minor':max(0,unknown_debt-balances['c:0']),'transaction_ids':txids[None]})
    transactions=[{k:tx.get(k) for k in ['id','source_id','account_id','account_name','date','merchant','amount_minor','kind','category_id','pending','needs_review','transfer_pair_id']} for tx in current]
    transfers=[{**tx,'linked':tx['source_id'] in used_sources} for tx in eligible if tx['account_id'] in cash and tx['direction']<0 and (tx['kind'] in {'transfer','investment'} or tx.get('transfer_pair_id')) and tx['other_account_id'] not in cash|cards]
    return {'month':month,'settings':settings,'token':token,'accounts':accounts,'categories':result,'ready_minor':ready,
        'reconciliation':{'saved_cash_minor':saved_cash,'modeled_cash_minor':modeled_cash,'difference_minor':difference},
        'receipts':receipts,'transfers':transfers,'reserves':[{'account_id':a['id'],'name':a['name'],'reserved_minor':reserves[a['id']],
            'balance_minor':a['balance_minor'],'unfunded_minor':sum(v for (_,card),v in unpaid.items() if card==a['id'])} for a in accounts if a['id'] in cards],
        'warnings':list(dict.fromkeys(warnings)), 'uncategorized_minor':actual[None], 'unbudgeted_minor':sum(c['actual_minor'] for c in result if c['limit_minor'] is None),
        'transactions':transactions,'previous_transactions':[{k:tx.get(k) for k in ['id','account_id','category_id','kind','amount_minor','pending','needs_review','transfer_pair_id']} for tx in rows if tx['currency']=='USD' and tx['date'][:7]==previous_month(month)],
        'history':[dict(row) for row in db.execute('SELECT e.*,u.batch IS NOT NULL AS undone FROM budget_events e LEFT JOIN budget_undos u ON e.batch=u.batch ORDER BY e.id DESC LIMIT 200')],
        'opening_token':digest([accounts,[(tx['source_id'],tx['signature']) for tx in rows]])}


Amount = int
class Setup(BaseModel):
    token:str
    cash_accounts:list[int]=Field(min_length=1,max_length=100)
    credit_accounts:list[int]=Field(default_factory=list,max_length=100)
    method:Literal['zero','ratio','custom']='zero'

class Settings(BaseModel):
    token:str
    method:Literal['categories','zero','ratio','custom']
    ratios:list[int]=Field(default=[50,30,20],min_length=3,max_length=3)

class CategoryEdit(BaseModel):
    group:str=Field(min_length=1,max_length=80)
    bucket:Literal['needs','wants','savings']='needs'
    target_minor:int=Field(default=0,strict=True,ge=0,le=1_000_000_000_000)
    target_type:Literal['monthly','contribution','refill','dated']='monthly'
    target_date:date|None=None
    repeat_monthly:bool=False

class Line(BaseModel):
    category:str=Field(pattern=r'^(c|card):[0-9]+$')
    amount_minor:int=Field(strict=True,ge=-1_000_000_000_000,le=1_000_000_000_000)

class Action(BaseModel):
    token:str
    kind:Literal['assign','move','use']='assign'
    lines:list[Line]=Field(min_length=1,max_length=100)
    source:str|None=Field(default=None,max_length=200)
    note:str=Field(default='',max_length=300)


def budget_router(path, *, sandbox=False, initialize=lambda:None, source_database=None):
    router=APIRouter(prefix='/plan')
    def prepare():
        initialize()
        with connect(path,write=True) as db:
            initialize_budget(db)
            _,rows=ledger(db,sandbox)
            stamp=datetime.now(timezone.utc).isoformat()
            db.executemany('INSERT OR IGNORE INTO budget_seen VALUES (?,?)',[(tx['source_id'],stamp) for tx in rows if not tx['pending'] and not tx['needs_review']])
    def month_valid(month):
        try:
            if date.fromisoformat(month+'-01').isoformat()[:7]!=month:
                raise ValueError()
        except ValueError:
            raise HTTPException(422,'Choose a valid month.') from None
    def state(db,month,token):
        data=projection(db,sandbox,month)
        if data['token']!=token:
            raise HTTPException(409,'The budget or transactions changed. Reload before applying.')
        return data
    @router.get('')
    def get(month:str=Query(pattern=r'^[0-9]{4}-(0[1-9]|1[0-2])$')):
        month_valid(month);prepare()
        with connect(path) as db:
            return projection(db,sandbox,month)
    @router.post('/setup')
    def setup(item:Setup):
        prepare()
        # Reviewed opening anchor is current saved cash, never inferred historical cash.
        from .recovery import BackupStore
        store=BackupStore(source_database or path)
        modes=['personal','live','sandbox','sandbox_investments','sandbox_combined']
        mode=next((m for m in modes if store.target(m)==path),None)
        if mode is None:
            raise HTTPException(409,'Cannot determine backup destination.')
        backup=store.create(mode)
        with connect(path,write=True) as db:
            data=projection(db,sandbox,date.today().isoformat()[:7])
            if data['settings'].get('start_date'):
                raise HTTPException(409,'Opening funding is already configured. Switch methods without resetting it.')
            if item.token!=data['opening_token']:
                raise HTTPException(409,'Balances changed. Review opening funds again.')
            accounts={a['id']:a for a in data['accounts']}
            cash=set(item.cash_accounts);cards=set(item.credit_accounts)
            def kind(a):return a.get('provider_type',a['kind'])
            if cash&cards or any(i not in accounts or accounts[i]['currency']!='USD' or accounts[i]['balance_minor'] is None or accounts[i]['balance_minor']<0 or kind(accounts[i]) not in {'cash','checking','savings','depository'} for i in cash):
                raise HTTPException(422,'Select funded USD cash/checking/savings accounts with saved nonnegative balances.')
            if any(i not in accounts or accounts[i]['currency']!='USD' or kind(accounts[i])!='credit' for i in cards):
                raise HTTPException(422,'Select USD credit cards only for payment reserves.')
            _,rows=ledger(db,sandbox)
            settings={**data['settings'],'method':item.method,'cash_accounts':sorted(cash),'credit_accounts':sorted(cards),
                'opening_minor':sum(accounts[i]['balance_minor'] for i in cash),'start_date':date.today().isoformat(),
                'opening_balances':{str(i):accounts[i]['balance_minor'] for i in cash|cards},
                'baseline':{tx['source_id']:tx['signature'] for tx in rows if not tx['pending'] and tx['date']<=date.today().isoformat()},'backup_id':backup['id']}
            db.execute('INSERT INTO budget_settings VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(json.dumps(settings),))
        return {'backup':backup['id']}
    @router.put('/settings')
    def settings(item:Settings,month:str):
        month_valid(month);prepare()
        if any(v<0 or v>100 for v in item.ratios) or sum(item.ratios)!=100:
            raise HTTPException(422,'Needs, Wants and Savings percentages must total 100.')
        with connect(path,write=True) as db:
            data=state(db,month,item.token)
            if item.method!='categories' and not data['settings'].get('start_date'):
                raise HTTPException(409,'Review and set up opening budget cash first.')
            saved={**data['settings'],'method':item.method,'ratios':[50,30,20] if item.method=='ratio' else item.ratios}
            db.execute('INSERT INTO budget_settings VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(json.dumps(saved),))
        return {'saved':True}
    @router.put('/categories/{identifier}')
    def category(identifier:int,item:CategoryEdit):
        prepare()
        if not item.group.strip():raise HTTPException(422,'Enter a category group name.')
        if item.target_type=='dated' and not item.target_date:
            raise HTTPException(422,'Choose a date for the savings goal.')
        with connect(path,write=True) as db:
            table='preview_categories' if sandbox else 'categories'
            if not db.execute(f"SELECT 1 FROM {table} WHERE id=? AND kind='expense'",(identifier,)).fetchone():
                raise HTTPException(404,'Category not found.')
            db.execute('INSERT INTO budget_categories VALUES (?,?,?,?,?,?,?) ON CONFLICT(category_id) DO UPDATE SET group_name=excluded.group_name,bucket=excluded.bucket,target_minor=excluded.target_minor,target_type=excluded.target_type,target_date=excluded.target_date,repeat_monthly=excluded.repeat_monthly',
                (identifier,item.group.strip(),item.bucket,item.target_minor,item.target_type,item.target_date.isoformat() if item.target_date else None,int(item.repeat_monthly)))
        return {'saved':True}
    @router.post('/copy')
    def copy(item:dict,month:str):
        month_valid(month);prepare()
        with connect(path,write=True) as db:
            state(db,month,item.get('token'))
            table='preview_budgets' if sandbox else 'monthly_budgets'
            db.execute(f'INSERT OR IGNORE INTO {table}(month,category_id,limit_minor) SELECT ?,category_id,limit_minor FROM {table} WHERE month=?',(month,previous_month(month)))
        return {'saved':True,'message':'Copied missing limits from last month. Existing limits and funded allocations were not changed.'}
    @router.put('/limits')
    def limits(item:dict,month:str):
        month_valid(month);prepare()
        values=item.get('limits',{})
        if not isinstance(values,dict) or len(values)>200:
            raise HTTPException(422,'Choose at most 200 category limits.')
        with connect(path,write=True) as db:
            data=state(db,month,item.get('token'));valid={str(c['id']) for c in data['categories']}
            if any(key not in valid or value is not None and (type(value)!=int or not 0<=value<=1_000_000_000_000) for key,value in values.items()):
                raise HTTPException(422,'Enter nonnegative category limits in cents, or clear a limit.')
            table='preview_budgets' if sandbox else 'monthly_budgets'
            for key,value in values.items():
                if value is None:db.execute(f'DELETE FROM {table} WHERE month=? AND category_id=?',(month,int(key)))
                else:db.execute(f'INSERT INTO {table} VALUES (?,?,?) ON CONFLICT(month,category_id) DO UPDATE SET limit_minor=excluded.limit_minor',(month,int(key),value))
        return {'saved':True}
    @router.post('/reconcile')
    def reconcile(item:dict,month:str):
        month_valid(month);prepare()
        if month!=date.today().isoformat()[:7]:
            raise HTTPException(422,'Reconcile funding in the current month using current saved balances.')
        adjustment=item.get('adjustment_minor',0)
        if type(adjustment)!=int or abs(adjustment)>1_000_000_000_000 or item.get('confirmed') is not True:
            raise HTTPException(422,'Review balances and confirm this budget-only adjustment.')
        with connect(path,write=True) as db:
            data=state(db,month,item.get('token'))
            saved=data['settings']
            if not saved.get('start_date'):raise HTTPException(409,'Set up opening cash first.')
            _,rows=ledger(db,sandbox);lookup={tx['source_id']:tx['signature'] for tx in rows}
            saved['baseline']={key:lookup[key] for key in saved['baseline'] if key in lookup}
            if adjustment:
                # A reviewed cash adjustment absorbs unresolved incoming movements;
                # subsequent classification must not introduce that cash twice.
                cash=set(saved['cash_accounts'])
                for tx in rows:
                    if not tx['pending'] and tx['account_id'] in cash and tx['direction']>0 and tx['kind'] not in {'income','refund'} and tx['other_account_id'] not in cash:
                        saved['baseline'][tx['source_id']]=tx['signature']
            saved['acknowledged']={row['source']:lookup.get(row['source']) for row in db.execute('SELECT DISTINCT source FROM budget_events WHERE source IS NOT NULL')}
            db.execute('UPDATE budget_settings SET payload=? WHERE id=1',(json.dumps(saved),))
            db.execute('INSERT INTO budget_events(batch,date,category,amount_minor,kind,note,created_at) VALUES (?,?,?,?,?,?,?)',
                (uuid4().hex,date.today().isoformat(),'c:0',adjustment,'reconcile','Reviewed funding reconciliation',datetime.now(timezone.utc).isoformat()))
        return {'saved':True}
    @router.post('/actions')
    def action(item:Action,month:str):
        month_valid(month);prepare()
        if month>date.today().isoformat()[:7]:
            raise HTTPException(422,'Fund received money in the current or a past month; future limits are planning only.')
        with connect(path,write=True) as db:
            data=state(db,month,item.token)
            if not data['settings'].get('start_date') or month<data['settings']['start_date'][:7]:
                raise HTTPException(409,'Set up opening cash before assigning money.')
            if data['warnings'] and any('changed' in w.lower() or 'opening' in w.lower() for w in data['warnings']):
                raise HTTPException(409,'Reconcile changed income/opening records before adding allocations.')
            allowed={'c:'+str(c['id']) for c in data['categories']}|{'card:'+str(c['account_id']) for c in data['reserves']}
            if len({line.category for line in item.lines})!=len(item.lines) or any(line.category not in allowed for line in item.lines):
                raise HTTPException(422,'Choose distinct valid categories or card reserves.')
            total=sum(line.amount_minor for line in item.lines)
            source=None
            if item.source:
                _,rows=ledger(db,sandbox);source=next((t for t in rows if t['source_id']==item.source),None)
                candidates=data['transfers'] if item.kind=='use' else data['receipts']
                candidate=next((t for t in candidates if t['source_id']==item.source),None)
                if not candidate or not source:
                    raise HTTPException(409,'Income/transfer is already allocated, skipped or no longer eligible.')
                if item.kind=='use':
                    if candidate['linked'] or len(item.lines)!=1 or total!=candidate['amount_minor']:
                        raise HTTPException(422,'Link the full outgoing transfer to one goal, once.')
                elif total<=0 or total>candidate['remaining_minor'] or any(line.amount_minor<0 for line in item.lines):
                    raise HTTPException(422,'Allocate no more than the remaining received income.')
            elif item.kind=='use':
                raise HTTPException(422,'Choose an outgoing goal transfer.')
            if item.kind=='move' and total!=0:
                raise HTTPException(422,'Moving money must preserve the total assigned amount.')
            for line in item.lines:
                available=next((c['cash_available_minor'] for c in data['categories'] if line.category=='c:'+str(c['id'])),None)
                if available is None:
                    available=next((c['reserved_minor'] for c in data['reserves'] if line.category=='card:'+str(c['account_id'])),0)
                if line.amount_minor<0 and -line.amount_minor>max(available,0):
                    raise HTTPException(422,'Cannot move more than the funded cash available.')
                if item.kind=='use' and (line.category.startswith('card:') or line.amount_minor>max(available,0)):
                    raise HTTPException(422,'Choose a savings/debt goal with enough assigned cash for this transfer.')
            if item.kind!='use' and total>0 and total>data['ready_minor']:
                raise HTTPException(422,'Not enough money ready to assign. Move existing funds or reduce the amount.')
            batch=uuid4().hex;stamp=datetime.now(timezone.utc).isoformat()
            effective=max(data['settings']['start_date'],min(date.today().isoformat(),month+'-28')) if month<date.today().isoformat()[:7] else date.today().isoformat()
            for line in item.lines:
                db.execute('INSERT INTO budget_events(batch,date,category,amount_minor,kind,source,signature,note,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                    (batch,effective,line.category,line.amount_minor,item.kind,item.source,source['signature'] if source else None,item.note,stamp))
            if item.kind!='use' and total>0 and month<date.today().isoformat()[:7] and projection(db,sandbox,date.today().isoformat()[:7])['ready_minor']<0:
                raise HTTPException(422,'This historical assignment would over-allocate current cash. Reduce it or move existing funds.')
        return {'batch':batch}
    @router.post('/undo/{batch}')
    def undo(batch:str):
        prepare()
        with connect(path,write=True) as db:
            if not db.execute('SELECT 1 FROM budget_events WHERE batch=?',(batch,)).fetchone():
                raise HTTPException(404,'Allocation not found.')
            db.execute('INSERT OR IGNORE INTO budget_undos VALUES (?,?)',(batch,datetime.now(timezone.utc).isoformat()))
        return {'undone':True}
    @router.post('/skip')
    def skip(item:dict,month:str):
        month_valid(month);prepare()
        with connect(path,write=True) as db:
            data=state(db,month,item.get('token'))
            receipt=next((tx for tx in data['receipts'] if tx['source_id']==item.get('source')),None)
            if not receipt:raise HTTPException(404,'Income not found.')
            db.execute('INSERT OR REPLACE INTO budget_skipped VALUES (?,?)',(receipt['source_id'],receipt['signature']))
        return {'skipped':True}
    return router


def overview_budgets(db,sandbox,month,budgets):
    """Keep Overview's small budget preview consistent with the funded Plan."""
    settings=config(db)
    if settings['method']=='categories':
        table='preview_budgets' if sandbox else 'monthly_budgets'
        repeated={row[0] for row in db.execute('SELECT category_id FROM budget_categories WHERE repeat_monthly=1')}
        for row in budgets:
            if row['limit_minor'] is None and row['id'] in repeated:
                prior=db.execute(f'SELECT limit_minor FROM {table} WHERE category_id=? AND month<? ORDER BY month DESC LIMIT 1',(row['id'],month)).fetchone()
                if prior:row['limit_minor']=prior[0]
        return budgets,settings['method']
    data=projection(db,sandbox,month);lookup={c['id']:c for c in data['categories']}
    result=[]
    for row in budgets:
        c=lookup[row['id']]
        if c['assigned_minor'] or c['available_minor'] or c['activity_minor']:
            result.append({**row,'limit_minor':max(0,c['available_minor']-c['activity_minor']),'actual_minor':-c['activity_minor']})
    return result,settings['method']
