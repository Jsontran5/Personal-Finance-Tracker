# Personal Finance

A local Windows ledger for manual transactions, budgets, account balances, and net worth. It serves the dashboard from your own computer and stores the database at `data/finance.db`.

## Start on Windows

1. Install Python 3.13 and Node.js 22.12 or later if needed.
2. Run **Setup Finance.cmd** once to install dependencies and build the dashboard. This step needs internet access.
3. Run **Start Finance.cmd** to open the app. Leave its window open while using the dashboard. Close the launcher window to stop the app. Normal use works offline.

## Record your finances

- Use the **Dark mode** switch in the sidebar to change the appearance. The app remembers your choice on this computer.
- Add an account with its opening balance. For debt accounts, enter the amount owed as a positive number.
- Press **Alt+N** for Quick Add. Enter an amount, choose the account and type, then enter the merchant or source. Log credit card purchases as expenses and payments as transfers to the card.
- Set monthly limits under **Budgets**. Choose a category name to view its transactions.
- Save a recurring template under **Transactions** and use it to prefill a new entry whenever it occurs.
- Use **Accounts → Record balance** to reconcile an account or update a manually valued asset. Enter its balance after the entries already logged for that date. New same-day entries and later entries adjust the displayed balance. Historical net worth uses captured totals on the dates the app recorded them.
- USD is the reporting currency. Accounts in other currencies retain their original amounts and appear separately.
- On Overview, expand **Accounts** below the net worth figure to choose the included accounts. The net worth chart, assets, and liabilities update together. **All accounts** restores every eligible USD account. Choices are saved separately for Personal and Sandbox preview, and newly added eligible accounts start selected.
- On Accounts, expand **Balance chart & history** for an account's recorded daily balances and observation details. Choose 1M, 3M, 6M, 1Y, or All. Each date uses the last recorded balance for that day. Debt balances appear as negative values. Periods with no observations show an empty state.
- Net worth history uses recorded account breakdowns. Historical dates without all selected balances appear as gaps. Imported transactions alone do not provide historical balance points.

## Back up the database

From the project folder, run `.venv\Scripts\python.exe -m finance.backup`. The command takes a consistent, integrity-checked copy in `backups/`. Treat the database and backups as private files. Project notes and checkpoints are in [context_docs](context_docs/README.md).

## Try Plaid Sandbox

1. Open [Plaid Dashboard → Developers → Keys](https://dashboard.plaid.com/developers/keys) and locate the Sandbox credentials.
2. In the project root, create a local `.env` file with the following names. Replace the placeholders in your editor.

   ```dotenv
   PLAID_ENV=sandbox
   PLAID_CLIENT_ID=your_client_id
   PLAID_SECRET=your_sandbox_secret
   ```

3. Start or restart the app. Open **Bank sync → Connect a test bank → Open Plaid Sandbox**. Use a Plaid test institution and its Sandbox login, then return to the app and select **Check connection** and **Sync now**.

Select **Workspace → Sandbox preview** in the sidebar to use the saved test data in Overview, Accounts, Transactions, and Budgets. Select **Personal** to return to your own ledger. The app remembers this choice across launches.

In Sandbox preview, click a transaction to change its merchant, classification, category, or notes. Set test budgets and create categories under Budgets. These edits survive subsequent syncs. Pending transactions and unclassified credits are excluded from spending and income until posted or classified. Use the month picker and transaction filters to explore history.

Net worth uses saved account balances. Recorded balance snapshots begin when preview is first opened or a new sync observes balances. Transaction backfill does not reconstruct earlier net worth.

Sandbox data and edits live in `data/finance.plaid-sandbox.db`. Restarting the app reuses its saved test connection. Resetting `data/finance.db` does not reset the test connection. Keep the `.env` file private and use the Sandbox secret here.

Accounts are grouped into cash/savings, investments/retirement, health savings, credit cards, and loans/credit lines, with other assets shown when needed. Search or filter groups, expand an account for freshness and balance history, and use the net worth account selector to filter chart totals. Account subtypes update on normal sync without creating another connection.

Transactions display 25 entries per page. Click a synced transaction and expand **Provider transaction details** for supplied fields such as original description, authorization dates, payment channel, category confidence, location, counterparties, and payment information. Expand **Raw provider data** for the complete saved response. Decimal values in that local JSON are stored as exact strings.

Use **Bank sync → Refresh transaction details** to replay the available history and fill in details for older imports. The saved Item and local transaction edits are retained. **Manage test accounts** reopens account selection on that Item so you can share additional eligible accounts. Accounts removed from sharing stop contributing to current balances while their existing transaction history is retained.

Plaid Sandbox supports credit cards and loans. The sample [credit-card testing profile](sandbox_profiles/credit-card-testing.json) was verified against Plaid Sandbox. Import it as a custom user in Dashboard → Build → Sandbox Studio. Editing a custom profile does not change an existing Item. This app currently stores one Sandbox Item, so a separate profile connection needs a future scenario selector or a separate isolated test database.

## Local development

The local Git repository tracks source, documentation, the dependency lockfile, and synthetic test profiles. Credentials, databases, backups, and generated files are ignored. See [Git workflow](context_docs/GIT_WORKFLOW.md).

For the regression tests, install optional test dependencies with `.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`, then run `.venv\Scripts\python.exe -m unittest discover -s tests -v`. Build the dashboard with `npm.cmd run build`.

With Chrome installed and the 14-account Sandbox dataset available, run `.venv\Scripts\python.exe tests/check_account_ui.py` for the expanded-layout checks. It uses isolated database copies and writes screenshots under ignored `data/ui-qa`.

