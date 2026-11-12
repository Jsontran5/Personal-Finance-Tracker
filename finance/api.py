from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal
from urllib.parse import urlsplit
import sqlite3

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from .db import DATABASE, ROOT, connect, initialize
from .ledger import LIABILITIES, overview, record_net_worth
from .plaid_sandbox import sandbox_router
from .review import apply_rules, mark, review_router

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Notes = Annotated[str, StringConstraints(max_length=2000)]
Money = Annotated[int, Field(strict=True, ge=-1_000_000_000_000, le=1_000_000_000_000)]
PositiveMoney = Annotated[int, Field(strict=True, gt=0, le=1_000_000_000_000)]
AccountKind = Literal["cash", "checking", "savings", "investment", "property", "credit", "loan", "other"]
TransactionKind = Literal["expense", "income", "refund", "transfer", "investment"]


class AccountFields(BaseModel):
    name: Name
    include_in_net_worth: bool = True
    primary: bool = False


class AccountCreate(AccountFields):
    kind: AccountKind
    currency: Literal["USD", "CAD", "EUR", "GBP", "AUD", "CHF", "JPY"] = "USD"
    opening_balance_minor: Money
    date: date


class Balance(BaseModel):
    balance_minor: Money
    date: date
    notes: Notes = ""


class Transaction(BaseModel):
    account_id: int
    destination_id: int | None = None
    category_id: int | None = None
    kind: TransactionKind
    amount_minor: PositiveMoney
    date: date
    merchant: Name
    notes: Notes = ""


class Template(BaseModel):
    name: Name
    account_id: int
    category_id: int | None = None
    kind: Literal["expense", "income"]
    amount_minor: PositiveMoney
    merchant: Name
    notes: Notes = ""


class Category(BaseModel):
    name: Name
    kind: Literal["expense", "income"]


class Budget(BaseModel):
    limit_minor: Money | None


class Appearance(BaseModel):
    theme: Literal["dark", "light"]


class Workspace(BaseModel):
    mode: Literal["personal", "sandbox"]


class ChartSelection(BaseModel):
    excluded_account_ids: Annotated[list[Annotated[int, Field(strict=True, gt=0)]], Field(max_length=1000)] = []


def account(db, account_id):
    row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Account not found.")
    return row


def category(db, category_id, kind):
    row = db.execute("SELECT kind FROM categories WHERE id=?", (category_id,)).fetchone()
    if row is None or row["kind"] != kind:
        raise HTTPException(422, "Choose a category that matches the transaction type.")


def validate_date(day, today):
    if day > today:
        raise HTTPException(422, "Use today or an earlier date.")


def normalized_balance(kind, value):
    if kind in LIABILITIES:
        if value < 0:
            raise HTTPException(422, "Enter debt owed as a positive amount.")
        return -value
    return value


def set_primary(db, account_id, primary):
    if primary:
        db.execute("UPDATE settings SET primary_account_id=?", (account_id,))
    else:
        db.execute("UPDATE settings SET primary_account_id=NULL WHERE primary_account_id=?", (account_id,))


def validate_transaction(db, tx, today):
    validate_date(tx.date, today)
    source = account(db, tx.account_id)
    destination = tx.destination_id
    if tx.kind == "transfer":
        if destination is None or destination == tx.account_id:
            raise HTTPException(422, "Choose a different destination account.")
        if account(db, destination)["currency"] != source["currency"]:
            raise HTTPException(422, "Transfers require accounts in the same currency.")
    elif destination is not None:
        raise HTTPException(422, "Only transfers have a destination.")
    if tx.category_id is not None:
        if tx.kind in {"investment", "transfer"}:
            raise HTTPException(422, "Transfers and investment activity use no budget category.")
        category(db, tx.category_id, "income" if tx.kind == "income" else "expense")
    for account_id in (tx.account_id, destination):
        if account_id is not None and not db.execute(
            "SELECT 1 FROM balance_snapshots WHERE account_id=? AND effective_date<=?",
            (account_id, tx.date.isoformat()),
        ).fetchone():
            raise HTTPException(422, "Record a balance on or before the transaction date for each account.")


def create_app(database: Path = DATABASE, static: Path = ROOT / "dist"):
    theme_path = database.with_suffix(".theme")
    workspace_path = database.with_suffix(".workspace")

    @asynccontextmanager
    async def lifespan(_app):
        initialize(database)
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.include_router(sandbox_router(database))
    app.include_router(review_router(database, after_write=lambda db: record_net_worth(db, date.today())), prefix='/api')

    @app.middleware("http")
    async def protect_local_writes(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            try:
                valid_origin = origin is None or (
                    urlsplit(origin).scheme == request.url.scheme and urlsplit(origin).netloc == request.url.netloc
                )
            except ValueError:
                valid_origin = False
            if not valid_origin or request.headers.get("x-finance-request") != "1":
                return JSONResponse({"detail": "Use the local Finance app to make changes."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(sqlite3.OperationalError)
    async def sqlite_error(_request, error):
        if getattr(error, "sqlite_errorcode", None) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return JSONResponse({"detail": "The database is busy. Try again shortly."}, status_code=503)
        raise error

    @app.get("/api/overview")
    def get_overview(month: Annotated[str, Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]):
        if month.startswith("0000"):
            raise HTTPException(422, "Choose a valid reporting month.")
        with connect(database) as db:
            return overview(db, date.today(), month)

    @app.get("/api/appearance", response_model=Appearance)
    def get_appearance():
        try:
            saved = theme_path.read_text(encoding="ascii").strip()
        except (FileNotFoundError, UnicodeError):
            saved = "dark"
        return {"theme": "light" if saved == "light" else "dark"}

    @app.get("/api/workspace", response_model=Workspace)
    def get_workspace():
        try:
            saved = workspace_path.read_text(encoding="ascii").strip()
        except (FileNotFoundError, UnicodeError):
            saved = "personal"
        return {"mode": "sandbox" if saved == "sandbox" else "personal"}

    @app.get("/api/chart-selection/{mode}", response_model=ChartSelection)
    def get_chart_selection(mode: Literal["personal", "sandbox"]):
        path = database.with_suffix(f".{mode}-chart.json")
        try:
            return ChartSelection.model_validate_json(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return ChartSelection()

    @app.put("/api/chart-selection/{mode}", response_model=ChartSelection)
    def set_chart_selection(mode: Literal["personal", "sandbox"], item: ChartSelection):
        path = database.with_suffix(f".{mode}-chart.json")
        temporary = None
        selection = ChartSelection(excluded_account_ids=sorted(set(item.excluded_account_ids)))
        try:
            with NamedTemporaryFile(dir=path.parent, delete=False) as file:
                temporary = Path(file.name)
                file.write(selection.model_dump_json().encode("utf-8"))
            temporary.replace(path)
        except OSError:
            raise HTTPException(503, "Could not save the chart selection. Try again.") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return selection

    @app.put("/api/workspace", response_model=Workspace)
    def set_workspace(item: Workspace):
        temporary = None
        try:
            with NamedTemporaryFile(dir=workspace_path.parent, delete=False) as file:
                temporary = Path(file.name)
                file.write(item.mode.encode("ascii"))
            temporary.replace(workspace_path)
        except OSError:
            raise HTTPException(503, "Could not save the workspace preference. Try again.") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return item

    @app.put("/api/appearance", response_model=Appearance)
    def set_appearance(item: Appearance):
        temporary = None
        try:
            with NamedTemporaryFile(dir=theme_path.parent, delete=False) as file:
                temporary = Path(file.name)
                file.write(item.theme.encode("ascii"))
            temporary.replace(theme_path)
        except OSError as error:
            raise HTTPException(503, "Could not save the appearance setting. Try again.") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return item

    @app.post("/api/accounts", status_code=201)
    def create_account(item: AccountCreate):
        today = date.today()
        validate_date(item.date, today)
        opening = normalized_balance(item.kind, item.opening_balance_minor)
        with connect(database, write=True) as db:
            account_id = db.execute("""
                INSERT INTO accounts(name, kind, currency, include_in_net_worth) VALUES (?, ?, ?, ?)
            """, (item.name, item.kind, item.currency, item.include_in_net_worth)).lastrowid
            db.execute("INSERT INTO balance_snapshots(account_id,effective_date,balance_minor,transaction_cutoff) VALUES (?,?,?,COALESCE((SELECT MAX(id) FROM transactions),0))",
                       (account_id, item.date.isoformat(), opening))
            has_primary = db.execute("SELECT primary_account_id FROM settings").fetchone()[0] is not None
            set_primary(db, account_id, item.primary or (not has_primary and item.kind in {"checking", "cash"}))
            record_net_worth(db, today)
        return {"id": account_id}

    @app.put("/api/accounts/{account_id}")
    def edit_account(account_id: int, item: AccountFields):
        with connect(database, write=True) as db:
            account(db, account_id)
            db.execute("UPDATE accounts SET name=?, include_in_net_worth=? WHERE id=?",
                       (item.name, item.include_in_net_worth, account_id))
            set_primary(db, account_id, item.primary)
            record_net_worth(db, date.today())
        return {"id": account_id}

    @app.post("/api/accounts/{account_id}/balances", status_code=201)
    def add_balance(account_id: int, item: Balance):
        today = date.today()
        validate_date(item.date, today)
        with connect(database, write=True) as db:
            source = account(db, account_id)
            balance = normalized_balance(source["kind"], item.balance_minor)
            balance_id = db.execute("""
                INSERT INTO balance_snapshots(account_id,effective_date,balance_minor,notes,transaction_cutoff) VALUES (?,?,?,?,COALESCE((SELECT MAX(id) FROM transactions),0))
            """, (account_id, item.date.isoformat(), balance, item.notes)).lastrowid
            record_net_worth(db, today)
        return {"id": balance_id}

    @app.get("/api/accounts/{account_id}/balances")
    def list_balances(account_id: int):
        with connect(database) as db:
            account(db, account_id)
            return [dict(row) for row in db.execute("""
                SELECT * FROM balance_snapshots WHERE account_id=? ORDER BY effective_date DESC,id DESC
            """, (account_id,))]

    @app.post("/api/transactions", status_code=201)
    def add_transaction(item: Transaction):
        today = date.today()
        with connect(database, write=True) as db:
            validate_transaction(db, item, today)
            tx_id = db.execute("""
                INSERT INTO transactions(account_id,destination_id,category_id,kind,amount_minor,date,merchant,notes)
                VALUES (:account_id,:destination_id,:category_id,:kind,:amount_minor,:date,:merchant,:notes)
            """, item.model_dump(mode="json")).lastrowid
            apply_rules(db, False, [tx_id])
            record_net_worth(db, today)
        return {"id": tx_id}

    @app.put("/api/transactions/{tx_id}")
    def edit_transaction(tx_id: int, item: Transaction):
        today = date.today()
        with connect(database, write=True) as db:
            validate_transaction(db, item, today)
            result = db.execute("""
                UPDATE transactions SET account_id=:account_id,destination_id=:destination_id,
                    category_id=:category_id,kind=:kind,amount_minor=:amount_minor,
                    date=:date,merchant=:merchant,notes=:notes WHERE id=:id
            """, {**item.model_dump(mode="json"), "id": tx_id})
            if not result.rowcount:
                raise HTTPException(404, "Transaction not found.")
            mark(db, tx_id)
            record_net_worth(db, today)
        return {"id": tx_id}

    @app.delete("/api/transactions/{tx_id}")
    def remove_transaction(tx_id: int):
        with connect(database, write=True) as db:
            if not db.execute("DELETE FROM transactions WHERE id=?", (tx_id,)).rowcount:
                raise HTTPException(404, "Transaction not found.")
            record_net_worth(db, date.today())
        return {"id": tx_id}

    @app.post("/api/categories", status_code=201)
    def add_category(item: Category):
        with connect(database, write=True) as db:
            result = db.execute("INSERT OR IGNORE INTO categories(name,kind) VALUES (?,?)", (item.name, item.kind))
            if not result.rowcount:
                raise HTTPException(409, "A category with that name already exists.")
        return {"id": result.lastrowid}

    @app.put("/api/budgets/{month}/{category_id}")
    def set_budget(month: str, category_id: int, item: Budget):
        try:
            if date.fromisoformat(f"{month}-01").isoformat()[:7] != month:
                raise ValueError
        except ValueError as error:
            raise HTTPException(422, "Choose a valid budget month.") from error
        limit = item.limit_minor
        if limit is not None and limit < 0:
            raise HTTPException(422, "Enter a nonnegative budget limit.")
        with connect(database, write=True) as db:
            category(db, category_id, "expense")
            if limit is None:
                db.execute("DELETE FROM monthly_budgets WHERE month=? AND category_id=?", (month, category_id))
            else:
                db.execute("""
                    INSERT INTO monthly_budgets(month,category_id,limit_minor) VALUES (?,?,?)
                    ON CONFLICT(month,category_id) DO UPDATE SET limit_minor=excluded.limit_minor
                """, (month, category_id, limit))
        return {"id": category_id}

    @app.post("/api/templates", status_code=201)
    def add_template(item: Template):
        with connect(database, write=True) as db:
            account(db, item.account_id)
            if item.category_id is not None:
                category(db, item.category_id, item.kind)
            template_id = db.execute("""
                INSERT INTO recurring_templates(name,account_id,category_id,kind,amount_minor,merchant,notes)
                VALUES (:name,:account_id,:category_id,:kind,:amount_minor,:merchant,:notes)
            """, item.model_dump()).lastrowid
        return {"id": template_id}

    @app.delete("/api/templates/{template_id}")
    def remove_template(template_id: int):
        with connect(database, write=True) as db:
            if not db.execute("DELETE FROM recurring_templates WHERE id=?", (template_id,)).rowcount:
                raise HTTPException(404, "Template not found.")
        return {"id": template_id}

    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="frontend")
    return app


app = create_app()


