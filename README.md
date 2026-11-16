# Personal Finance

A local Windows ledger for manual transactions, budgets, account balances, and net worth. It serves the dashboard from your own computer and stores the database at `data/finance.db`.

## Start on Windows

1. Install Python 3.13 and Node.js 22.12 or later if needed.
2. Run **Setup Finance.cmd** once to install dependencies and build the dashboard. This step needs internet access.
3. Double-click **Start Finance.cmd** to open the app in its own Python console. Leave that console open while using the dashboard. Press **Ctrl+C** there to stop it. Normal use works offline.

From the VS Code PowerShell terminal, run `& .\.venv\Scripts\python.exe -m finance`, or use `.\Start Finance.ps1`. Both run directly in the terminal, so **Ctrl+C** stops the server without the batch-file **Terminate batch job (Y/N)?** prompt. Run the same command again to restart. If local PowerShell policy blocks the script, use the direct Python command.

## Dashboard layout

The interface uses graphite/blue dark mode and a complete light theme. On desktop, all eight sections are in the sidebar. On phones and smaller tablets, use the bottom navigation for **Overview**, **Spending**, **Transactions**, and **Accounts**; **More** opens **Budgets**, **Investments**, **Bank sync**, and **Backup & restore**. Workspace/scenario selectors and the appearance switch stay in the top area.

Use **ⓘ Help** in each page header for a guide to that page. Hover over supported controls or focus them with Tab for quick explanations of their effects. Tooltips dismiss with Escape; the Help panel also works on phones, traps keyboard focus and returns focus when closed. Help covers sync types, connection reuse, balance history, portfolio values, classification/review, budgets and backup/restore. Viewing help never runs a financial action or syncs a bank.

Select an account to open its inspector: balance, source information, history, transactions, manual editing and debt details remain available there. Transaction editors open as side panels on desktop and tall sheets on phones, with explicit Save/Cancel. Provider details remain collapsed until opened. Press Escape or use Close to dismiss a panel; keyboard focus returns to its source.

Overview places cash flow and budget progress alongside net worth, followed by the spending calendar, recent activity and account composition. Net worth account selection is beside its measure controls; its phone/tablet picker stays above the bottom navigation. **About this workspace** expands the environment explanation. Relevant warnings and the Live readiness/linking lock remain visible in their existing workflows. These layout changes preserve financial calculations, stored data, workspace separation and all connection/restore safeguards.

Reduced-motion, reduced-transparency and higher-contrast preferences have dedicated fallbacks. The interface uses local system fonts and adds no external font or motion dependency.

## Record your finances

- Deleting a manual transaction opens a warning with its saved merchant, amount, date, and account. Tick **I understand this permanently deletes the transaction.** to enable **Delete transaction**. Cancel or Escape returns to your unsaved edits. Deletion has no Undo.
- Under **Transactions → Transfer & card-payment matches**, review possible movements between your accounts. Suggestions require equal amounts/currencies, different accounts, posted dates within five days, and a transfer/payment clue. Competing matches are identified; nothing is linked automatically. Loan accounts are excluded because payments may include interest.
- **Review pair → Confirm link** excludes both entries from income, spending, and category budgets while the link is valid. Original transaction types, categories, notes, and account balances stay saved. **Linked → Unlink pair** restores normal reporting; **Dismissed → Restore suggestion** allows a dismissed candidate to appear again. Linked entries must be unlinked before editing/deleting/reclassifying. Provider corrections or removals invalidate the exclusion and show **Needs attention** rather than silently hiding changed activity.
- On Accounts, expand an account and choose **View account transactions** to browse its saved activity across all dates. The page header shows the last saved Sandbox sync time and links to Bank sync for details.

- Transactions has separate **Needs review**, **Uncategorized**, and **Reviewed** views. An initial backfill does not require reviewing every transaction: ambiguous credits and entries you flag form the review queue. Unclassified credits stay out of cash flow until given an explicit type; an uncategorized purchase still counts toward spending. Manually flagging a purchase does not remove it from financial totals.
- Choose **All saved dates** to handle historical exceptions together, or use **Reporting month**. Select a page or all filtered matches, then preview bulk classification, acknowledgement, or flags before applying. Pending entries and incompatible money directions are skipped with reasons. Manual transfers need a destination in the individual editor.
- **New merchant rule** previews exact imported-merchant matches across all saved dates, optionally scoped to one account and current type. Existing individual edits are preserved. **Also apply to future imports** is off by default. Enable it when you want the rule reused on later syncs (or compatible new uncategorized manual entries). Rules can be disabled without undoing existing classifications. More specific account rules take priority; previews show at most 50 sample rows, with full match/applicable counts. Narrow rules with more than 10,000 matches to an account.

- Use the **Dark mode** switch in the sidebar to change the appearance. The app remembers your choice on this computer.
- Add an account with its opening balance. For debt accounts, enter the amount owed as a positive number.
- Press **Alt+N** for Quick Add. Enter an amount, choose the account and type, then enter the merchant or source. Log credit card purchases as expenses and payments as transfers to the card.
- Set monthly limits under **Budgets**. Choose a category name to view its transactions.
- Save a recurring template under **Transactions** and use it to prefill a new entry whenever it occurs.
- Use **Accounts → Record balance** to reconcile an account or update a manually valued asset. Enter its balance after the entries already logged for that date. New same-day entries and later entries adjust the displayed balance. Historical net worth uses captured totals on the dates the app recorded them.
- USD is the reporting currency. Accounts in other currencies retain their original amounts and appear separately.
- On Overview, expand **Accounts** below the net worth figure to choose the included accounts. The chart and **Where it sits** composition update together. **Select all** restores every eligible USD account. Choices are saved separately for Manual and Sandbox preview, and newly added eligible accounts start selected. Switch the chart between **Net worth**, **Assets**, and **Debt**.
- The Overview spending calendar shows recorded daily spending alongside recent purchases/refunds. Click a day for its transactions; calendar labels are rounded, with exact amounts available in the tooltip and drilldown.
- Open **Spending** for **Spending / Income / Cash flow** reports, daily averages, cumulative comparisons and an always-visible calendar. Filter accounts and assigned categories, and choose an earlier comparison month. Current-month comparisons use the same elapsed days; completed months compare full periods. Select a day, category or total for matching transactions; **Clear report filter** removes the drilldown and the Back action returns to its source.
- For synced accounts, choose **Bank categories** or **Your categories** for category breakdowns without changing budget assignments. Spending includes posted USD purchases/refunds in the report’s selected accounts, independently of the net worth selection; pending activity, credits awaiting review, transfers, and investment activity are excluded. Reports use recorded activity and cannot guarantee complete imported history.
- On Accounts, expand **Balance chart & history** for an account's recorded daily balances and observation details. Choose 1M, 3M, 6M, 1Y, or All. Each date uses the last recorded balance for that day. Debt balances appear as negative values. Periods with no observations show an empty state.
- Net worth history uses recorded account breakdowns. Historical dates without all selected balances appear as gaps. Imported transactions alone do not provide historical balance points.

## Debt details and manual student loans

For Nelnet, select **Manual → Accounts → Add account → Student loan (manual)**. Enter the current total amount owed as a positive opening balance. Expand the account, then **Debt details → Add debt details** to enter the servicer, APR/interest rate, fixed/variable rate type, original principal, principal balance, accrued interest, repayment plan, status, and notes. **More balances, payments and dates** contains optional credit limits, payment/statement information, and loan dates. Each Nelnet loan group can have its own account and rate. No Plaid connection is needed.

Saving or clearing debt terms does not change the account balance or create transactions. Use **Record balance** to reconcile with the servicer's reported total. For a manual loan payment, record principal as a transfer to the loan and interest/fees as a separate expense; this keeps principal out of spending and reduces net worth only by the expense. There is no automatic interest accrual, amortization calculation, or upcoming-bills feature.

For imported debts, choose **Sandbox preview → Banking → Bank sync → Sync debt details**, or expand a debt account and use its **Debt details → Sync debt details** button. This retrieves the latest available Liabilities data for all supported debts on the same saved Item. Accounts displays supplied credit limits/available credit, multiple card APRs, statement/payment information, student loan details, mortgages, other loans, and credit lines where available. Missing values remain unavailable, and zero rates/payments remain zero. Source timestamps and additional provider details are shown; unnecessary account/payment-reference numbers are discarded. Loan names and guarantors are not labeled as loan servicers.

Debt details are stored locally and included in workspace/scenario backups. Manual and Sandbox terms stay separate. The existing banking Sandbox connection was successfully tested with five reported liabilities. Institution-specific live coverage and Production connections remain separate rollout work.

## Back up the database

Open **Backup & restore** in the sidebar. Choose **Manual**, **Sandbox · Banking**, **Sandbox · Investments**, **Sandbox · Combined**, or **Live**, then **Create backup**. Back up each separately. Bundles in the project `backups/` folder include its database and chart selection: accounts, transactions, budgets, balance history, debt details, review/rule state, confirmed/dismissed transfer links, and investment holdings/activity. Appearance, the current workspace/scenario choices, `.env` credentials, and context documents are not included.

Choose **Preview restore** to validate a bundle and inspect its workspace/scenario, snapshot date, account/transaction and investment counts, sample accounts, and activity dates. Type the displayed confirmation phrase before restoring. A validated `recovery-…finance-backup.zip` of the current workspace is created first; use it to recover the previous data if needed. A bundle restores to its own named workspace/scenario, regardless of the current sidebar selection. The app refreshes its cached records after success. Other workspaces/scenarios are unaffected. Close other Finance instances and finish/cancel pending bank authorization for the destination before restoring.

Sandbox restores retain the current authorization when the backup belongs to the same Item, while restoring the backup's transaction cursor with its data. No bank request or new Item is created; the next explicit sync may replay later activity. Different-Item backups are rejected while connected. Empty recovery restores keep the current authorization and reset the feed checkpoint. Saved authorization can be recovered without a current connection only if the Windows profile can unlock it. API credentials must be retained separately.

Existing `.db` backups are also listed. They are checked and migrated on a temporary copy; current chart selection is retained because legacy files do not include it. Corrupt, incompatible, oversized, or stale restore previews are rejected. The UI supports databases up to approximately 256 MB. Bundles contain private financial records; only the stored bank tokens are Windows-user encrypted. Keep a separate copy on storage you control if you need protection from loss of the project or drive.

The original Manual-only CLI remains available: `.venv\Scripts\python.exe -m finance.backup`. It creates a consistent, integrity-checked `.db` file in `backups/`. Context notes and checkpoints remain local-only in `context_docs/`.

## Prepare Live bank connections

Choose **Workspace → Live → Bank sync** for the readiness checks. Live data, credentials, authorizations, chart settings and backups are isolated from Sandbox. Completed checks are collapsed under **Setup details**; outstanding checks stay visible. Production credentials are configured locally, and the confirmed Trial allowance (20), Transactions and automatic OAuth access are recorded. Hosted Link is requested automatically and needs no separate Dashboard enablement. Use **Find your bank** to check actual institution coverage and prepare its entry. Real-bank linking is controlled by `PLAID_LIVE_ENABLED`; it cannot be enabled from the browser.

Follow [Live setup and BofA pilot](LIVE_SETUP.md) to finish configuration without connecting a bank. Use the explicit Production entries in `.env.example`; keep the existing Sandbox secret. Live supports Transactions, balances and explicitly selected Investments. Live debt-provider sync and live stock quotes remain deferred. This app remains a local, single-user desktop service, not an internet deployment.

## Try Plaid Sandbox

1. Open [Plaid Dashboard → Developers → Keys](https://dashboard.plaid.com/developers/keys) and locate the Sandbox credentials.
2. In the project root, create a local `.env` file with the following names. Replace the placeholders in your editor.

   ```dotenv
   PLAID_ENV=sandbox
   PLAID_CLIENT_ID=your_client_id
   PLAID_SECRET=your_sandbox_secret
   ```

3. Start or restart the app. Open **Bank sync → Connect a test bank → Open Plaid Sandbox**. Use a Plaid test institution and its Sandbox login, then return to the app and select **Check connection** and **Sync now**.

Select **Workspace → Sandbox preview** in the sidebar to use the saved test data in Overview, Accounts, Transactions, and Budgets. Select **Manual** to return to your own ledger. The app remembers this choice across launches.

Within Sandbox preview, **Test scenario → Banking / Investments / Combined** switches between independent test connections and data. Banking keeps your original data. Investments uses its own connection initialized with the Investments product; choose **Investments** in the sidebar to sync holdings/activity. The scenario choice persists across restarts, and chart selections remain separate. Switching scenarios does not call Plaid or create another Item. A new, empty investment scenario can be connected from Bank sync using First Platypus Bank and the standard Sandbox user_good / pass_good credentials.

In Sandbox preview, click a transaction to change its merchant, classification, category, or notes. Set test budgets and create categories under Budgets. These edits survive subsequent syncs. Pending transactions and unclassified credits are excluded from spending and income until posted or classified. Use the month picker and transaction filters to explore history.

Net worth uses saved account balances. Recorded balance snapshots begin when preview is first opened or a new sync observes balances. Transaction backfill does not reconstruct earlier net worth.

Banking Sandbox data and edits live in `data/finance.plaid-sandbox.db`; the investment scenario uses `data/finance.plaid-investments-sandbox.db`. Restarting the app reuses their saved test connections. Resetting `data/finance.db` does not reset either test connection. Keep the `.env` file private and use the Sandbox secret here.

Accounts are grouped into cash/savings, investments/retirement, health savings, credit cards, and loans/credit lines, with other assets shown when needed. Open a synced account and choose **Rename account** to set a local nickname (up to 120 characters). The nickname appears throughout that workspace and survives bank syncs. Account details retain the original bank name; **Use bank name**, or saving a blank nickname, restores the latest bank-provided name. Nicknames are included in workspace backups and do not change account IDs, balances, transactions, holdings or bank permissions. Manual mirrors are renamed in their original Manual workspace. Search or filter groups, expand an account for freshness and balance history, and use the net worth account selector to filter chart totals. Account subtypes update on normal sync without creating another connection.

Transactions display 25 entries per page. Click a synced transaction and expand **Provider transaction details** for supplied fields such as original description, authorization dates, payment channel, category confidence, location, counterparties, and payment information. Expand **Raw provider data** for the complete saved response. Decimal values in that local JSON are stored as exact strings.

Use **Bank sync → Refresh transaction details** to replay the available history and fill in details for older imports. The saved Item and local transaction edits are retained. **Manage test accounts** reopens account selection on that Item so you can share additional eligible accounts. Accounts removed from sharing stop contributing to current balances while their existing transaction history is retained.

Plaid Sandbox supports credit cards and loans. The sample [credit-card testing profile](sandbox_profiles/credit-card-testing.json) was verified against Plaid Sandbox. Import it as a custom user in Dashboard → Build → Sandbox Studio. Editing a custom profile does not change an existing Item. Banking and Investments each store one Item and refuse to overwrite it. Combined supports multiple institutions; add another institution there rather than deleting/relinking a saved scenario.

## Live investments

In **Live > Bank sync > Setup details**, confirm Investments product access from your Plaid Dashboard and save. This optional setting does not change existing banks or start consent. The Trial product bundle includes Investments; account/institution coverage still varies. Do not upgrade to a paid plan as part of this setup.

- To add a broker, choose **Find your bank**, set **Connection type: Investments**, search and add the correct institution, then connect its saved entry. This requests Investments without banking Transactions.
- For an institution that supports both, select **Banking transactions** and **Also connect investments** before searching. Both products must be advertised; account-level availability is confirmed during authorization and sync. One Item owns the selected banking and investment accounts.
- For an existing banking institution, expand **Sync details and additional data**, choose **Enable investments on this connection**, then **Reconnect / manage accounts** to grant consent in update mode. This retains the saved Item/cursor; do not create a duplicate institution. Fresh product coverage and Dashboard confirmation are required. Merely clicking enable does not grant consent or fetch holdings.
- After authorization, **Sync holdings** first, then **Sync activity** on that institution. Open **Investments** for accounts, holdings, quantities, broker-reported prices/value/cost basis, allocation and activity. Missing fields stay unavailable. Brokerage cash appears when reported as a holding; account balances include cash and securities and drive net worth once. Trades and dividends in investment activity do not enter the spending ledger automatically.
- Data is broker-reported, not a live quote feed. No separate stock API or Investments Refresh calls are used. Initial activity can take time; failed syncs retain saved data. Later activity sync reconciles recent history; **Reload investment history** under the connection details checks the full available range.
- Live backups include investment records and consent-flow status. Restore keeps current authorization for matching Items; older schema-9 backups migrate on a temporary copy. Schema 10 introduced per-connection investment access, leaving existing banking Items and financial records intact.

Official references: [Investments](https://plaid.com/docs/investments/), [update mode](https://plaid.com/docs/link/update-mode/), [Trial product access](https://support.plaid.com/hc/en-us/articles/16186552926743-How-do-I-add-new-Plaid-products). Production portfolio coverage must be checked with your actual institution; Sandbox/synthetic success does not establish it.

## Multiple institutions and manual accounts

Choose **Workspace: Sandbox preview → Test scenario: Combined**. The prepared Combined workspace already includes the two saved test connections (Bank of America and First Platypus), with 16 accounts, spending data, holdings/activity and debt details. **Bank sync → Import saved Sandbox connections** can safely copy any not-yet-imported Banking/Investments fixture again. It reuses the existing encrypted authorizations and transaction cursors, without creating or exchanging another Item. The original scenarios remain independent afterward; edits and later syncs in one do not change another. Budgets, merchant rules and older charts are not copied from the fixtures; Combined has its own settings and observation history.

- **Bank sync** lists institutions with their own status, sync actions, saved Link session and expandable timestamps/additional products. Lists have search and pagination. Each account shows its institution; net worth and spending combine their data. Account and provider transaction IDs are separated by connection, so identical IDs from different providers cannot collide. A failed sync or reconnect requirement affects only that institution. There is no background polling.
- **Add another institution** searches Plaid by banking/investment support. Add the institution, then start its Link session and select that same institution inside Plaid. The server verifies Hosted Link's institution metadata before exchanging a new token. This single-user milestone allows one connection per institution; duplicate institutions are blocked. Separate logins at one institution are not supported yet.
- **Reconnect / manage accounts** opens update mode for the saved Item. It reuses its authorization, preserves sync state, and does not exchange a new token. Finish or cancel the saved Link session before starting another. After a token-exchange timeout, **Check connection** retries the same durably saved public token. Pending exchange cannot be cancelled blindly: if the public token expires or cannot be recovered, resolve it before attempting a new connection to avoid duplicates. This does not guarantee recovery from every provider/network failure.
- **Include accounts from Manual** explicitly selects read-only mirrors, such as a manually entered Nelnet student loan. Add/edit the original balance, transactions and debt terms in Manual. Selected records refresh when Combined reloads; deselecting removes them from Combined without deleting the originals. Include only accounts not already represented by a bank connection. No real manual loan or invented balance is created for you.
- Use **Investments** for the combined portfolio and **Bank sync** to sync holdings/activity for the relevant institution. Product support and consent still vary; a displayed investment account balance alone does not establish Holdings API support. Syncing account balances does not count holdings again in net worth.

Combined data is project-local in `data/finance.plaid-combined-sandbox.db` (schema 8). The existing single-Item scenarios stay at schema 7 and Manual at schema 4. Live bank linking remains locked; these changes do not connect real banks or consume Trial Production connections. See the Live setup guide to prepare real-data access.

Back up Combined from **Backup & restore → Sandbox · Combined**. Restore requires the same connected Item set when authorizations are already present; a backup cannot silently remove a newer connection. Pending Link sessions block restore. Manual mirrors refresh from Manual, so back up Manual separately to preserve their canonical records. No new connection or provider request occurs during restore.

## Investment holdings and activity

Choose **Sandbox preview → Test scenario: Investments → Investments → Sync holdings**, then **Sync activity**. Investment sync reuses that scenario's saved Item and retrieves Plaid's available data; it does not request live market quotes or call the separately entitled Investments Refresh endpoint. The Investments product must be supported by the connected institution and consented on the Item. **Manage connection** opens Bank sync; **Manage test accounts** uses account-selection update mode on that Item. It does not add unrelated product consent to an institution that cannot support it.

Holdings include existing positions, cash/equivalents, quantities, broker prices, values, and cost basis where supplied. Expand a position for price dates and unrealized gain/loss; **Provider details** retains the complete supplied holding/security objects. Missing values show a dash. Allocation uses positive USD holdings only and excludes short positions, unknown values, and other currencies. Portfolio value uses saved USD account balances, counting cash and securities once. Recorded portfolio history begins with observations; it is not a historical return calculation and includes the effects of deposits and withdrawals.

The **Activity** tab has account, security/description, type, and date filters with 25 records per page. Buys, sells, dividends, contributions, withdrawals, fees, and cancellations stay separate from everyday spending and budgets. Positive provider amounts use cash; negative amounts add cash. Initial sync requests up to two years of available history. Later syncs reconcile a seven-day overlap plus the period since the previous sync; **History coverage → Reload available history** rechecks older corrections within the available two-year range. Holdings sync separately, so a delayed activity request does not discard loaded positions. No automated polling or public webhook service is added.

The original banking test institution returns `PRODUCTS_NOT_SUPPORTED` for Investments despite exposing investment-type account balances. It stays in the Banking scenario, with its original data intact. The separate Investments scenario has been populated with an investment-capable Sandbox connection. Custom Sandbox users require `investments` in the initial required products, rather than only additional consent. Multiple Sandbox institutions can now share Combined. Real-bank linking remains a separately approved milestone; a separate Live workspace/readiness flow is now prepared.

## Local development

The local Git repository tracks source, the public README, the dependency lockfile, and synthetic test profiles. `context_docs/` and `context_folder/` are local-only and must never be committed or pushed. Credentials, databases, backups, and generated files are also ignored. Make small, frequent commits after each verified piece of work. Local Git hooks reject context files in commits and pushes.

For the regression tests, install optional test dependencies with `.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`, then run `.venv\Scripts\python.exe -m unittest discover -s tests -v`. Build the dashboard with `npm.cmd run build`.

With Chrome installed and the 14-account Sandbox dataset available, run `.venv\Scripts\python.exe tests/check_account_ui.py` for the expanded-layout checks. It uses isolated database copies and writes screenshots under ignored `data/ui-qa`.

For an opt-in check against Plaid's actual investment APIs, run `.venv\Scripts\python.exe tests/check_investment_sandbox.py --run`. It loads the existing server-side Sandbox credentials, creates a temporary investment-capable Sandbox Item, imports holdings/activity into a temporary database, and removes that test Item afterward. It does not use or replace the application's saved Item and makes no Production calls.

With both saved scenarios and Chrome available, run `python tests/check_scenario_ui.py` for scenario switching, reload/backend restart, and full investment restore checks on isolated database copies. Add `--live` to exercise debt-detail sync on the banking Item and both investment sync buttons on the investment Item; no new Item is created. This checks both themes at desktop/phone widths and writes ignored screenshots. The actual project data is not restored or altered by the test.


For Combined browser verification, run `python tests/check_combined_ui.py` after the build, with Chrome and the prepared Combined fixture available. It uses isolated copies, adds only synthetic manual accounts/institution rows to those copies, checks long lists and expanded/collapsed details in both themes at desktop/phone widths, restarts the backend, and performs a confirmed restore. Add `--live` to sync transactions and holdings/activity on the existing Sandbox Items without creating new Items. Screenshots stay ignored under `data/ui-qa`.

For the complete redesign/browser checks, run `python tests/check_redesign_ui.py` after building. It uses isolated data copies and a synthetic Production provider, verifies all eight screens in both themes at 1440/1280/768/390/320 widths, uses real pointer/keyboard events for navigation/inspectors, checks chart picker reachability, text/control palette contrast, focus return, reduced-motion/material/contrast fallbacks and 200%-equivalent reflow. It never links a real bank. Screenshots remain ignored under `data/ui-qa`.

Current provider-workspace schema versions: Sandbox Banking/Investments 11, Combined 12, Live 14. These migrations add account-name metadata; Manual remains schema 4. Older backups migrate on a temporary copy before validation.

## Refresh all and account update times

In **Live > Accounts** or **Live > Bank sync**, choose **Refresh all** to fetch the applicable products on each connected institution: banking transactions, investment holdings, investment activity, then balances. It reuses existing authorizations and does not create Items or request paid Refresh products/live quotes. Keep the app open until it finishes; progress remains available while browsing. Disconnected institutions are excluded, reconnect-required or consent-pending products are skipped, and each product keeps the normal 30-second cooldown. One failed request does not stop other products/institutions. Expand the result summary for each updated, failed or skipped action. A second run can retry failures while skipping recently completed products. Sync remains on demand, not automatic on app launch.

Account rows show relative times for applicable data. Open an account's **Data updates** section for exact local timestamps, cached-balance labels and per-product failed attempts. Balance time belongs to the account's saved observation; transaction, holdings and activity times are shared institution fetch times. These are retrieval times, not the latest transaction date, broker valuation date or a guarantee of live prices. **Needs refresh** is a reminder after 24 hours without a successful fetch or when data has never been fetched. Product errors remain visible even if a different product sync succeeds; a successful retry clears only that product's error. Manual mirrors are maintained in Manual.

Live schema 14 adds product-specific sync outcomes and includes them in backups. Existing Live schemas 9/10/13 migrate locally; Banking/Investments remain 11, Combined 12, Manual 4. Existing account names, financial data and authorizations remain intact.

## Transaction direction and readable text

The app uses locally bundled **Lexend** with higher-contrast primary/secondary text in both themes. The font license and source are included under frontend/fonts. Disabled buttons remain disabled and use readable muted text rather than low-opacity text.

Ledger rows, recent spending and transaction inspectors show explicit **From / To** endpoints and signed amounts with **In / Out**. The direction comes from the bank-reported amount sign, independently of the income/spending/transfer classification. A card payment into a credit-card account can be incoming while remaining a transfer. Dates, posted/pending state, classification and review state remain visible.

Confirmed pairs use the current local names of the two accounts. Otherwise, the app uses supplied payer/payee, a unique reported counterparty or merchant name, or an explicit “From …” / “To …” description. Bank-reported names and descriptions are labeled as such; they do not establish a match to one of your other accounts. Missing endpoints stay marked as not supplied. Manual entries use their selected destination or entered merchant. This is display-only metadata: no transaction, matching decision, classification, balance or net-worth calculation changes.

### Spending, income and cash-flow reports

Open **Spending** and choose **Spending**, **Income** or **Cash flow**. Account selections, assigned-category filters and the comparison month apply to the totals, trend, calendar, category breakdowns and transaction links together. With no accounts selected, all accounts are included. Income includes posted USD credits classified as income; transfers, refunds, pending entries and credits awaiting classification are excluded. Cash flow is income minus purchases plus refunds. Reports read saved data without syncing banks.

Each Spending, Income and Cash flow report ends with **Transactions in this report**: the exact posted entries contributing to its charts and totals under the selected reporting month, accounts and category. Select a row for the existing transaction inspector. Sort by newest/oldest/largest/smallest across all matching rows, with 10 entries per page.

The calendar is always available below the trend. Select a day for exact transactions. Category donuts show purchases before refunds, or income; refunds and net spending remain separate so refund-heavy categories are represented accurately. Select a slice or legend row to inspect its contributing entries. Category breakdowns can use bank categories or your assigned categories, independently of the assigned-category report filter. Merchant search remains available in Transactions.

On **Overview**, click the cash-flow total, Income or Spending to open its contributing transactions for the reporting month. Use **Back to Overview** or **Back to Spending** to return; report filters are retained. Transactions supports type and posted/pending filters and newest/oldest/largest/smallest ordering across the entire filtered list before pagination. Amount ordering uses face value without currency conversion. Pending entries visible in the ledger remain excluded from reports.

The **Reporting month** controls transaction reports and budgets. **Net worth** and **Where it sits** show current saved balances, independently of that month. The balance chart uses its own date range and account selection. One recorded day produces one dot, even after repeated same-day refreshes; observations on later days build a trend. Transaction backfill does not supply historical balance snapshots. Open Accounts for per-account data update times; saved observations are not live market quotes.
