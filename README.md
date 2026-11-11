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

