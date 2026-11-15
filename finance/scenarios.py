"""Fixed project-local Sandbox scenarios; never derived from user-supplied paths."""
from typing import Literal

Scenario = Literal['banking', 'investments', 'combined']


def sandbox_path(database, scenario='banking'):
    if scenario == 'combined':
        return database.with_suffix('.plaid-combined-sandbox.db')
    return database.with_suffix('.plaid-investments-sandbox.db' if scenario == 'investments' else '.plaid-sandbox.db')


def chart_path(database, mode):
    return database.with_suffix(f'.{mode}-chart.json')
