"""Fixed project-local Sandbox scenarios; never derived from user-supplied paths."""
from typing import Literal

Scenario = Literal['banking', 'investments']


def sandbox_path(database, scenario='banking'):
    return database.with_suffix('.plaid-investments-sandbox.db' if scenario == 'investments' else '.plaid-sandbox.db')


def chart_path(database, mode):
    return database.with_suffix(f'.{mode}-chart.json')
