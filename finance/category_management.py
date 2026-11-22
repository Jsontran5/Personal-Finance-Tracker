"""Category lifecycle and reviewed merges, including atomic shared-store changes."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import re
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints
from typing import Annotated, Literal

from .db import connect, database_lock


def initialize_categories(db):
    db.execute('CREATE TABLE IF NOT EXISTS category_lifecycle(category_id INTEGER PRIMARY KEY,archived INTEGER NOT NULL CHECK(archived IN (0,1)))')
    db.execute('CREATE TABLE IF NOT EXISTS category_sync(manual_id INTEGER PRIMARY KEY,local_id INTEGER NOT NULL UNIQUE)')
    db.execute('''CREATE TABLE IF NOT EXISTS category_operations(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,
        source_id INTEGER NOT NULL,target_id INTEGER,payload TEXT NOT NULL,created_at TEXT NOT NULL)''')


def catalog(db,sandbox=False):
    table='preview_categories' if sandbox else 'categories'
    return [dict(r) for r in db.execute(f'SELECT c.*,COALESCE(s.archived,0) AS archived FROM {table} c LEFT JOIN category_lifecycle s ON s.category_id=c.id ORDER BY c.name')]


class SchemaView:
    """Qualify internal SQL table references when using an attached DB transaction."""
    def __init__(self,db,schema):
        self.db,self.schema=db,schema
        names=[r[0] for r in db.execute(f"SELECT name FROM {schema}.sqlite_schema WHERE type='table'")]+['sqlite_schema']
        self.pattern=re.compile(r'\b(FROM|JOIN|INTO|UPDATE)\s+('+ '|'.join(re.escape(n) for n in names)+r')\b',re.I)
    def execute(self,sql,parameters=()):
        if self.schema!='main':
            sql=self.pattern.sub(lambda m:m[1]+' '+self.schema+'.'+m[2],sql)
        return self.db.execute(sql,parameters)


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}


def references(db,sandbox,identifier):
    found={}
    existing=tables(db)
    for table in ['preview_edits' if sandbox else 'transactions','preview_budgets' if sandbox else 'monthly_budgets','merchant_rules','categorization_rules','recurring_templates','budget_categories']:
        if table in existing:
            found[table]=db.execute(f'SELECT COUNT(*) FROM {table} WHERE category_id=?',(identifier,)).fetchone()[0]
    found['budget_events']=db.execute('SELECT COUNT(*) FROM budget_events WHERE category=?',('c:'+str(identifier),)).fetchone()[0]
    return found


def state_token(stores):
    value=[]
    for db,sandbox,_,_ in stores:
        names=tables(db)
        selected=['preview_categories' if sandbox else 'categories','preview_edits' if sandbox else 'transactions','accounts','transactions','transfer_links','balance_snapshots','budget_seen',
            'preview_budgets' if sandbox else 'monthly_budgets','merchant_rules','categorization_rules','recurring_templates',
            'budget_categories','budget_events','budget_undos','budget_settings','category_lifecycle','category_sync']
        value.append({name:[dict(r) for r in db.execute(f'SELECT * FROM {name} ORDER BY rowid')] for name in selected if name in names})
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def rebase_signatures(db,sandbox,before):
    """Only acknowledge the category-only changes this merge actually performed."""
    from .budgeting import ledger
    after={t['source_id']:t['signature'] for t in ledger(db,sandbox)[1]}
    row=db.execute('SELECT payload FROM budget_settings WHERE id=1').fetchone()
    if row:
        settings=json.loads(row[0])
        for field in ['baseline','acknowledged']:
            for source,signature in settings.get(field,{}).items():
                if signature==before.get(source) and source in after:
                    settings[field][source]=after[source]
        db.execute('UPDATE budget_settings SET payload=? WHERE id=1',(json.dumps(settings),))
    for source,signature in before.items():
        if source in after and signature!=after[source]:
            db.execute('UPDATE budget_events SET signature=? WHERE source=? AND signature=?',(after[source],source,signature))
            db.execute('UPDATE budget_skipped SET signature=? WHERE source=? AND signature=?',(after[source],source,signature))


def merge_one(db,sandbox,source,target,policy):
    from .budgeting import ledger
    before={t['source_id']:t['signature'] for t in ledger(db,sandbox)[1]}
    table='preview_categories' if sandbox else 'categories'
    old=dict(db.execute(f'SELECT * FROM {table} WHERE id=?',(source,)).fetchone())
    existing=tables(db)
    for name in ['preview_edits' if sandbox else 'transactions','merchant_rules','categorization_rules','recurring_templates']:
        if name in existing:db.execute(f'UPDATE {name} SET category_id=? WHERE category_id=?',(target,source))
    limits='preview_budgets' if sandbox else 'monthly_budgets'
    for row in db.execute(f'SELECT * FROM {limits} WHERE category_id=?',(source,)).fetchall():
        current=db.execute(f'SELECT limit_minor FROM {limits} WHERE month=? AND category_id=?',(row['month'],target)).fetchone()
        value=row['limit_minor']+(current[0] if current and policy=='sum' else 0) if not current or policy=='sum' else current[0]
        if value>1_000_000_000_000:raise HTTPException(422,'Combined limit is too large. Choose Keep destination limits.')
        db.execute(f'INSERT INTO {limits} VALUES (?,?,?) ON CONFLICT(month,category_id) DO UPDATE SET limit_minor=excluded.limit_minor',(row['month'],target,value))
    db.execute(f'DELETE FROM {limits} WHERE category_id=?',(source,))
    if db.execute('SELECT 1 FROM budget_categories WHERE category_id=?',(target,)).fetchone():
        db.execute('DELETE FROM budget_categories WHERE category_id=?',(source,))
    else:db.execute('UPDATE budget_categories SET category_id=? WHERE category_id=?',(target,source))
    db.execute('UPDATE budget_events SET category=? WHERE category=?',('c:'+str(target),'c:'+str(source)))
    db.execute('DELETE FROM category_lifecycle WHERE category_id=?',(source,))
    db.execute('DELETE FROM category_sync WHERE local_id=?',(source,))
    db.execute(f'DELETE FROM {table} WHERE id=?',(source,))
    rebase_signatures(db,sandbox,before)
    db.execute('INSERT INTO category_operations(kind,source_id,target_id,payload,created_at) VALUES (?,?,?,?,?)',
        ('merge',source,target,json.dumps({'source':old,'limit_policy':policy,'targets':'destination wins when both have settings'}),datetime.now(timezone.utc).isoformat()))


Name=Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=120)]
class Edit(BaseModel):
    name:Name|None=None
    archived:bool|None=None
    token:str
class Merge(BaseModel):
    source_id:int=Field(gt=0)
    target_id:int=Field(gt=0)
    limit_policy:Literal['sum','keep']='sum'
    token:str|None=None
    confirmation:str=''


def category_router(path,*,sandbox=False,initialize=lambda:None,source_database=None):
    router=APIRouter(prefix='/category-management')
    from .app_config import application_config
    shared=bool(sandbox and source_database and path==source_database.with_suffix('.plaid-live.db') and application_config(source_database.parent.parent)['unified'])
    def prepare():
        if not sandbox and application_config(path.parent.parent)['unified']:
            raise HTTPException(409,'Manage categories from All accounts so manual and connected records stay aligned.')
        initialize()
    def guard_manual_reference(identifier):
        other=path.with_suffix('.plaid-live.db')
        if not sandbox and other.is_file():
            with connect(other) as db:
                if 'category_sync' in tables(db) and db.execute('SELECT 1 FROM category_sync WHERE manual_id=?',(identifier,)).fetchone():
                    raise HTTPException(409,'This category is linked to connected data. Use category management in production All accounts for merge/delete.')
    @contextmanager
    def context():
        if shared:
            # One SQLite transaction for both files, preserving all-or-nothing changes.
            with database_lock(source_database),database_lock(path),connect(source_database,write=True) as db:
                db.execute('ATTACH DATABASE ? AS managed',(str(path.resolve()),))
                if any(db.execute(f'PRAGMA {s}.journal_mode').fetchone()[0].lower() in {'wal','off','memory'} for s in ['main','managed']):
                    raise HTTPException(409,'Shared category management requires the app’s normal on-disk rollback journals. Close external database tools first.')
                yield SchemaView(db,'managed'),SchemaView(db,'main')
        else:
            with connect(path,write=True) as db:yield db,None
    def stores(local,manual,source,target=None):
        result=[(local,sandbox,source,target)]
        if manual:
            ids=[]
            for identifier in [source,target]:
                if identifier is None:ids.append(None);continue
                link=local.execute('SELECT manual_id FROM category_sync WHERE local_id=?',(identifier,)).fetchone()
                if not link:raise HTTPException(409,'Category links changed. Reload the app before continuing.')
                ids.append(link[0])
            result.append((manual,False,ids[0],ids[1]))
        return result
    def require(db,preview,identifier):
        row=db.execute('SELECT * FROM '+('preview_categories' if preview else 'categories')+' WHERE id=?',(identifier,)).fetchone()
        if not row:raise HTTPException(404,'Category not found. Reload the list.')
        return dict(row)
    def backup():
        from .recovery import BackupStore
        store=BackupStore(source_database or path)
        if shared:return store.create_pair()
        mode=next((m for m in ['personal','live','sandbox','sandbox_investments','sandbox_combined'] if store.target(m)==path),None)
        if mode is None:raise HTTPException(409,'Backup destination is unavailable.')
        return store.create(mode)
    @router.get('')
    def listing():
        prepare()
        with context() as (db,manual):
            rows=catalog(db,sandbox)
            token=state_token([(db,sandbox,None,None)]+([(manual,False,None,None)] if manual else []))
            for row in rows:
                linked=stores(db,manual,row['id'])
                row['references']={('connected' if shared and preview else 'manual' if shared else 'workspace'):references(current,preview,s) for current,preview,s,_ in linked}
                row['can_delete']=not any(sum(counts.values()) for counts in row['references'].values())
            return {'categories':rows,'token':token,'shared':shared}
    @router.put('/{identifier}')
    def edit(identifier:int,item:Edit):
        prepare()
        with context() as (db,manual):
            linked=stores(db,manual,identifier)
            if item.token!=state_token(linked):raise HTTPException(409,'Categories or records changed. Reload before applying.')
            for current,preview,source,_ in linked:
                old=require(current,preview,source);table='preview_categories' if preview else 'categories'
                if item.name is not None:
                    duplicate=current.execute(f'SELECT 1 FROM {table} WHERE name=? AND id<>?',(item.name,source)).fetchone()
                    if duplicate:raise HTTPException(409,'That name already exists. Restore or merge the existing category instead.')
                    current.execute(f'UPDATE {table} SET name=? WHERE id=?',(item.name,source))
                if item.archived is not None:
                    current.execute('INSERT INTO category_lifecycle VALUES (?,?) ON CONFLICT(category_id) DO UPDATE SET archived=excluded.archived',(source,int(item.archived)))
                    if item.archived:current.execute('UPDATE merchant_rules SET active=0 WHERE category_id=?',(source,))
                current.execute('INSERT INTO category_operations(kind,source_id,payload,created_at) VALUES (?,?,?,?)',
                    ('edit',source,json.dumps({'before':old,'name':item.name,'archived':item.archived}),datetime.now(timezone.utc).isoformat()))
        return {'saved':True}
    @router.delete('/{identifier}')
    def delete(identifier:int,token:str):
        prepare();guard_manual_reference(identifier);saved=backup()
        with context() as (db,manual):
            linked=stores(db,manual,identifier)
            if token!=state_token(linked):raise HTTPException(409,'Categories or records changed. Reload before deleting.')
            for current,preview,source,_ in linked:
                require(current,preview,source)
                if sum(references(current,preview,source).values()):raise HTTPException(409,'This category has history, rules, targets or templates. Archive or merge it instead.')
            for current,preview,source,_ in linked:
                current.execute('DELETE FROM category_lifecycle WHERE category_id=?',(source,))
                current.execute('DELETE FROM category_sync WHERE local_id=?',(source,))
                current.execute('DELETE FROM '+('preview_categories' if preview else 'categories')+' WHERE id=?',(source,))
        return {'deleted':True,'backup':saved}
    def describe(local,manual,item):
        linked=stores(local,manual,item.source_id,item.target_id)
        if item.source_id==item.target_id:raise HTTPException(422,'Choose two different categories.')
        counts=[];conflicts=[]
        for db,preview,source,target in linked:
            old=require(db,preview,source);dest=require(db,preview,target)
            if old['kind']!=dest['kind']:raise HTTPException(422,'Income categories can only merge with income; expense categories with expense.')
            if db.execute('SELECT archived FROM category_lifecycle WHERE category_id=?',(target,)).fetchone() and db.execute('SELECT archived FROM category_lifecycle WHERE category_id=?',(target,)).fetchone()[0]:
                raise HTTPException(422,'Restore the destination category before merging into it.')
            counts.append({'store':'connected' if shared and preview else 'manual' if shared else 'workspace','counts':references(db,preview,source)})
            table='preview_budgets' if preview else 'monthly_budgets'
            conflicts.extend([{'store':'connected' if shared and preview else 'manual' if shared else 'workspace',**dict(r)} for r in db.execute(f'SELECT a.month,a.limit_minor AS source_minor,b.limit_minor AS target_minor FROM {table} a JOIN {table} b ON a.month=b.month WHERE a.category_id=? AND b.category_id=?',(source,target))])
        token=hashlib.sha256(json.dumps([state_token(linked),item.source_id,item.target_id,item.limit_policy]).encode()).hexdigest()
        # Preview the actual budget reclassification without committing it.
        from .budgeting import projection
        month=date.today().isoformat()[:7]
        before=projection(local,sandbox,month)
        raw=local.db if isinstance(local,SchemaView) else local
        raw.execute('SAVEPOINT category_preview')
        try:
            for db,preview,source,target in linked:merge_one(db,preview,source,target,item.limit_policy)
            after=projection(local,sandbox,month)
        finally:
            raw.execute('ROLLBACK TO category_preview');raw.execute('RELEASE category_preview')
        return {'source':require(local,sandbox,item.source_id),'target':require(local,sandbox,item.target_id),'token':token,
            'references':counts,'conflicts':conflicts,'limit_policy':item.limit_policy,'confirmation':'MERGE CATEGORIES',
            'ready_before':before['ready_minor'],'ready_after':after['ready_minor'],'reserves_before':sum(r['reserved_minor'] for r in before['reserves']),
            'reserves_after':sum(r['reserved_minor'] for r in after['reserves']),
            'note':'All linked records and budget assignments move to the destination. Existing destination group/target settings win; source settings move only if the destination has none. Category balances and card reserves are recalculated.'}
    @router.post('/merge/preview')
    def preview(item:Merge):
        prepare();guard_manual_reference(item.source_id)
        with context() as (db,manual):return describe(db,manual,item)
    @router.post('/merge/apply')
    def merge(item:Merge):
        prepare();guard_manual_reference(item.source_id);saved=backup()
        with context() as (db,manual):
            plan=describe(db,manual,item)
            if item.token!=plan['token'] or item.confirmation!=plan['confirmation']:raise HTTPException(409,'Preview is stale or confirmation is missing. Review the merge again.')
            for current,preview,source,target in stores(db,manual,item.source_id,item.target_id):merge_one(current,preview,source,target,item.limit_policy)
        return {'merged':True,'backup':saved}
    return router
