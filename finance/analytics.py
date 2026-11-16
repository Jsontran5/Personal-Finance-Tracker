"""Filtered, read-only reports; shared across Manual, Sandbox and Live."""
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from .db import connect
from .spending import previous_month, spending_report

Month = Annotated[str, Query(pattern=r'^[0-9]{4}-(0[1-9]|1[0-2])$')]


def analytics_router(database, transactions, initialize=lambda: None):
    router = APIRouter()

    @router.get('/analytics')
    def report(month: Month, measure: Literal['spending', 'income', 'cashflow'] = 'spending',
               comparison: Month | None = None, account: Annotated[list[int] | None, Query()] = None,
               category: str = 'all'):
        if month.startswith('0000') or comparison and comparison.startswith('0000'):
            raise HTTPException(422, 'Choose a valid reporting month.')
        if category not in {'all', 'uncategorized'} and (not category.isdecimal() or int(category) < 1):
            raise HTTPException(422, 'Choose a valid category.')
        if comparison and comparison >= month:
            raise HTTPException(422, 'Choose a comparison month before the reporting month.')
        initialize()
        prior = comparison or previous_month(month)

        def selected(rows):
            return [tx for tx in rows if (not account or tx['account_id'] in account)
                    and (category == 'all' or (tx.get('category_id') is None if category == 'uncategorized'
                                              else tx.get('category_id') == int(category)))]

        with connect(database) as db:
            return spending_report(selected(transactions(db, month)), selected(transactions(db, prior)) if prior else [],
                                   month, date.today(), measure=measure, comparison_month=prior)

    return router
