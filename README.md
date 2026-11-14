# Personal Finance

A local Windows ledger for manual transactions, budgets, account balances, and net worth. It serves the dashboard from your own computer and stores the database at `data/finance.db`.

## Start on Windows

1. Install Python 3.13 and Node.js 22.12 or later if needed.
2. Run **Setup Finance.cmd** once to install dependencies and build the dashboard. This step needs internet access.
3. Double-click **Start Finance.cmd** to open the app in its own Python console. Leave that console open while using the dashboard. Press **Ctrl+C** there to stop it. Normal use works offline.

From the VS Code PowerShell terminal, run `& .\.venv\Scripts\python.exe -m finance`, or use `.\Start Finance.ps1`. Both run directly in the terminal, so **Ctrl+C** stops the server without the batch-file **Terminate batch job (Y/N)?** prompt. Run the same command again to restart. If local PowerShell policy blocks the script, use the direct Python command.

## Record your finances

- Deleting a manual transaction opens a warning with its saved merchant, amount, date, and account. Tick **I understand this permanently deletes the transaction.** to enable **Delete transaction**. Cancel or Escape returns to your unsaved edits. Deletion has no Undo.
- Under **Transactions → Transfer & card-payment matches**, review possible movements between your accounts. Suggestions require equal amounts/currencies, different accounts, posted dates within five days, and a transfer/payment clue. Competing matches are identified; nothing is linked automatically. Loan accounts are excluded because payments may include interest.
- **Review pair → Confirm link** excludes both entries from income, spending, and category budgets while the link is valid. Original transaction types, categories, notes, and account balances stay saved. **Linked → Unlink pair** restores normal reporting; **Dismissed → Restore suggestion** allows a dismissed candidate to appear again. Linked entries must be unlinked before editing/deleting/reclassifying. Provider corrections or removals invalidate the exclusion and show **Needs attention** rather than silently hiding changed activity.
- On Accounts, expand an account and choose **View account transactions** to browse its saved activity across all dates. The page header shows the last saved Sandbox sync time and links to Bank sync for details.

- Transactions has separate **Needs review**, **Uncategorized**, and **Reviewed** views. An initial backfill does not require reviewing every transaction: ambiguous credits and entries you flag form the review queue. Unclassified credits stay out of cash flow until given an explicit type; an uncategorized purchase still counts toward spending. Manually flagging a purchase does not remove it from financial totals.
- Choose **All saved dates** to handle historical exceptions together, or use **Reporting month**. Select a page or all filtered matches, then preview bulk classification, acknowledgement, or flags before applying. Pending entries and incompatible money directions are skipped with reasons. Personal transfers need a destination in the individual editor.
- **New merchant rule** previews exact imported-merchant matches across all saved dates, optionally scoped to one account and current type. Existing individual edits are preserved. **Also apply to future imports** is off by default. Enable it when you want the rule reused on later syncs (or compatible new uncategorized manual entries). Rules can be disabled without undoing existing classifications. More specific account rules take priority; previews show at most 50 sample rows, with full match/applicable counts. Narrow rules with more than 10,000 matches to an account.

- Use the **Dark mode** switch in the sidebar to change the appearance. The app remembers your choice on this computer.
- Add an account with its opening balance. For debt accounts, enter the amount owed as a positive number.
- Press **Alt+N** for Quick Add. Enter an amount, choose the account and type, then enter the merchant or source. Log credit card purchases as expenses and payments as transfers to the card.
- Set monthly limits under **Budgets**. Choose a category name to view its transactions.
- Save a recurring template under **Transactions** and use it to prefill a new entry whenever it occurs.
- Use **Accounts → Record balance** to reconcile an account or update a manually valued asset. Enter its balance after the entries already logged for that date. New same-day entries and later entries adjust the displayed balance. Historical net worth uses captured totals on the dates the app recorded them.
- USD is the reporting currency. Accounts in other currencies retain their original amounts and appear separately.
- On Overview, expand **Accounts** below the net worth figure to choose the included accounts. The chart and **Where it sits** composition update together. **Select all** restores every eligible USD account. Choices are saved separately for Personal and Sandbox preview, and newly added eligible accounts start selected. Switch the chart between **Net worth**, **Assets**, and **Debt**.
- The Overview spending calendar shows recorded daily spending alongside recent purchases/refunds. Click a day for its transactions; calendar labels are rounded, with exact amounts available in the tooltip and drilldown.
- Open **Spending** for purchases, refunds, net spending, daily averages, and cumulative trends against the previous month. Current-month comparisons use the same elapsed days; completed months compare full periods. Switch between Trend and Calendar, then select categories or merchants to view matching transactions. **Clear spending filter** removes the drilldown, and **Back to Spending** returns to the dashboard.
- In Sandbox, choose **Bank categories** or **Your categories** for the category breakdown. This changes the report view without changing your budget assignments. Spending includes posted USD purchases/refunds across all accounts, independently of the net worth chart selection; pending activity, credits awaiting review, transfers, and investment activity are excluded. Reports use recorded activity and cannot guarantee complete imported history.
- On Accounts, expand **Balance chart & history** for an account's recorded daily balances and observation details. Choose 1M, 3M, 6M, 1Y, or All. Each date uses the last recorded balance for that day. Debt balances appear as negative values. Periods with no observations show an empty state.
- Net worth history uses recorded account breakdowns. Historical dates without all selected balances appear as gaps. Imported transactions alone do not provide historical balance points.

## Back up the database

Open **Backup & restore** in the sidebar. Choose Personal or Sandbox, then **Create backup**. Back up each workspace separately. Bundles in the project `backups/` folder include its database and chart selection: accounts, transactions, budgets, balance history, review/rule state, and confirmed/dismissed transfer links. Appearance, the current workspace choice, `.env` credentials, and context documents are not included.

Choose **Preview restore** to validate a bundle and inspect its workspace, snapshot date, account/transaction counts, sample accounts, and activity dates. Type the displayed confirmation phrase before restoring. A validated `recovery-…finance-backup.zip` of the current workspace is created first; use it to recover the previous data if needed. The app refreshes its cached records after success. Other workspaces are unaffected. Close other Finance instances and finish/cancel pending bank authorization before restoring.

Sandbox restores retain the current authorization when the backup belongs to the same Item, while restoring the backup's transaction cursor with its data. No bank request or new Item is created; the next explicit sync may replay later activity. Different-Item backups are rejected while connected. Empty recovery restores keep the current authorization and reset the feed checkpoint. Saved authorization can be recovered without a current connection only if the Windows profile can unlock it. API credentials must be retained separately.

Existing `.db` backups are also listed. They are checked and migrated on a temporary copy; current chart selection is retained because legacy files do not include it. Corrupt, incompatible, oversized, or stale restore previews are rejected. The UI supports databases up to approximately 256 MB. Bundles contain private financial records; only the stored bank tokens are Windows-user encrypted. Keep a separate copy on storage you control if you need protection from loss of the project or drive.

The original Personal-only CLI remains available: `.venv\Scripts\python.exe -m finance.backup`. It creates a consistent, integrity-checked `.db` file in `backups/`. Context notes and checkpoints remain local-only in `context_docs/`.

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

## Investment holdings and activity

Choose **Sandbox preview → Investments → Sync holdings**, then **Sync activity**. Investment sync reuses the saved Item and retrieves Plaid's available data; it does not request live market quotes or call the separately entitled Investments Refresh endpoint. The Investments product must be supported by the connected institution and consented on the Item. **Manage connection** opens Bank sync; **Manage test accounts** uses that Item's update flow, including consent for Investments and Liabilities.

Holdings include existing positions, cash/equivalents, quantities, broker prices, values, and cost basis where supplied. Expand a position for price dates and unrealized gain/loss; **Provider details** retains the complete supplied holding/security objects. Missing values show a dash. Allocation uses positive USD holdings only and excludes short positions, unknown values, and other currencies. Portfolio value uses saved USD account balances, counting cash and securities once. Recorded portfolio history begins with observations; it is not a historical return calculation and includes the effects of deposits and withdrawals.

The **Activity** tab has account, security/description, type, and date filters with 25 records per page. Buys, sells, dividends, contributions, withdrawals, fees, and cancellations stay separate from everyday spending and budgets. Positive provider amounts use cash; negative amounts add cash. Initial sync requests up to two years of available history. Later syncs reconcile a seven-day overlap plus the period since the previous sync; **History coverage → Reload available history** rechecks older corrections within the available two-year range. Holdings sync separately, so a delayed activity request does not discard loaded positions. No automated polling or public webhook service is added.

The project's existing test institution was verified to return `PRODUCTS_NOT_SUPPORTED` for Investments, despite investment-type account balances being available. Its original connection and data are preserved. Investment-capable Sandbox tests use a separate temporary database. Custom Sandbox users require `investments` in the initial required products, rather than only additional consent. Do not delete/relink the saved Item to try a new test scenario. Real-bank connections and multi-Item/scenario selection remain separate milestones.

## Local development

The local Git repository tracks source, the public README, the dependency lockfile, and synthetic test profiles. `context_docs/` and `context_folder/` are local-only and must never be committed or pushed. Credentials, databases, backups, and generated files are also ignored. Make small, frequent commits after each verified piece of work. Local Git hooks reject context files in commits and pushes.

For the regression tests, install optional test dependencies with `.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`, then run `.venv\Scripts\python.exe -m unittest discover -s tests -v`. Build the dashboard with `npm.cmd run build`.

With Chrome installed and the 14-account Sandbox dataset available, run `.venv\Scripts\python.exe tests/check_account_ui.py` for the expanded-layout checks. It uses isolated database copies and writes screenshots under ignored `data/ui-qa`.

For an opt-in check against Plaid's actual investment APIs, run `.venv\Scripts\python.exe tests/check_investment_sandbox.py --run`. It loads the existing server-side Sandbox credentials, creates a temporary investment-capable Sandbox Item, imports holdings/activity into a temporary database, and removes that test Item afterward. It does not use or replace the application's saved Item and makes no Production calls.

