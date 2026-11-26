# Personal Finance

A local-first personal finance app for tracking accounts, transactions, budgets, investments, debt, and net worth. Use manual accounts on their own or connect financial institutions with your own Plaid credentials.

## Features

- **Overview:** net worth, cash flow, spending calendar, account composition, and budget progress.
- **Accounts:** connected and manual accounts in one view, local account names, balance history, and update timestamps.
- **Transactions:** search, filters, categories, bulk review, merchant rules, recurring-entry templates, and transfer/card-payment matching.
- **Spending reports:** income, spending, and cash flow with category charts, trends, calendars, comparisons, and transaction drilldowns.
- **Pending activity:** posted + pending totals, posted-only/pending-only filters, and distinct status badges. Pending income stays separate from assignable budget funds.
- **Budgeting:** a monthly percentage guide, custom percentages, category limits, and optional zero-based planning with assignments, rollover, savings goals, and card-payment reserves.
- **Category management:** create, edit, group, archive, restore, merge, or delete unused categories in one hub.
- **Investments:** holdings, quantities, allocation, broker-reported values, cost basis when available, and investment activity.
- **Debt:** manual loan and credit-card balances, interest rates, repayment details, and notes.
- **History:** recorded balance snapshots and separately labeled banking estimates. New accounts join the graph without erasing earlier history.
- **Local backups:** backup previews, restore tools, and optional startup backup/sync/snapshot maintenance.
- **Interface:** light and dark themes, responsive navigation, keyboard support, tooltips, and page help.

## Run locally

1. Install **Python 3.13** and **Node.js 22.12 or later**.
2. Clone or download this repository. On **Windows**, run **Setup Finance.cmd**. On **macOS**, open Terminal in the project folder and run `bash "Setup Finance.command"`. Setup installs dependencies and builds the frontend.
3. Copy [.env.example](.env.example) to `.env`. Keep `APP_ENV=production` for the unified **All accounts** interface. Manual accounts work without Plaid credentials.
4. Run **Start Finance.cmd** on Windows, or `bash "Start Finance.command"` on macOS. The app opens in your browser and listens only on `127.0.0.1`. Keep its console open while using it.

You can also launch from PowerShell:

```powershell
& .\.venv\Scripts\python.exe -m finance
```

Press **Ctrl+C** in the running console to stop the app. Saved data remains available between launches. Local viewing and manual entry work offline; bank sync requires an internet connection.

On macOS, the equivalent Python command is `.venv/bin/python -m finance`. To skip startup maintenance for one launch, run `APP_STARTUP_ENABLED=false bash "Start Finance.command"`. Both Apple Silicon and Intel use the same setup; create a fresh virtual environment on each computer.

## Optional bank connections

Set your own `PLAID_CLIENT_ID` and `PLAID_PRODUCTION_SECRET` in `.env`, enable `PLAID_LIVE_ENABLED=true` when ready, and complete setup under **Bank sync**. Production connections require the appropriate Plaid access; supported accounts and data vary by institution.

**Refresh all** updates the applicable transactions, balances, holdings, and investment activity for existing connections. Provider data may be delayed or cached; account timestamps show when it was last retrieved. Manual entries and debt details remain under your control.

## Startup and backups

With `APP_STARTUP_ENABLED=true`, each production launch backs up local data if the automatic backup is due, syncs existing bank connections, and saves a balance snapshot. Set it to `false` to start without that maintenance. Reloading a browser tab does not trigger another startup run.

Data is stored locally in SQLite files under `data/`. Backup bundles are ZIP files under `backups/`, with automatic sets under `backups/automatic/`. The newest 14 complete automatic sets are retained. Use **Backup & restore** to create, inspect, or restore backups; keep an additional copy outside your computer.

Unified backups contain both Manual and Connected source files. Keep each pair together and restore **Manual first, then Connected** when recovering both. The supplied Git ignore rules exclude `.env`, local databases, and backups; do not publish them.

## Move between Windows and Mac

Bank tokens use Windows DPAPI or a macOS Keychain encryption key. Ordinary backups require the original OS user and vault; copying databases alone does not move bank access.

1. Update and run setup on both computers, then **stop the app on both**. On the original Windows computer, export from PowerShell:

   ```powershell
   & .\.venv\Scripts\python.exe -m finance.migration export "$HOME\Desktop\finance.finance-migration"
   ```

2. Enter a strong passphrase when prompted. The encrypted file includes saved workspaces, transactions, budgets, snapshots, chart selections and existing bank authorizations. It excludes `.env`, old backup archives and unfinished bank-link sessions. Transfer the file privately and keep its passphrase separately.
3. On a **fresh Mac clone after setup, before first app launch**, import:

   ```bash
   .venv/bin/python -m finance.migration import "$HOME/Desktop/finance.finance-migration"
   ```

4. Enter the passphrase, allow Finance access to your login Keychain if prompted, and configure `.env` privately with the **same Plaid project/environment credentials**. Start the app and check your accounts. Import makes no Plaid calls and retains existing Items and sync cursors; a bank can still require normal reauthentication later.

The same commands work in reverse using the appropriate Python executable. Import refuses existing workspace files; use a separate fresh clone rather than deleting your current data. Export leaves the original computer unchanged. Treat the migration file as sensitive, keep the passphrase safe, and remove transfer copies after verification. The import does not copy historical backup archives; start a new backup on the destination. Run one computer as your primary app to avoid diverging edits.

## Development

Set `APP_ENV=development` to expose separate Manual, Sandbox preview, and Live workspaces. Sandbox credentials in `.env.example` are for testing; changing the app mode does not enable real-bank connections automatically.

Rebuild the frontend after source changes:

```powershell
npm run build
```

Install the development dependencies and run the tests:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
& .\.venv\Scripts\python.exe -m unittest discover -s tests
```

On macOS, replace `& .\.venv\Scripts\python.exe` with `.venv/bin/python` in the test commands.
