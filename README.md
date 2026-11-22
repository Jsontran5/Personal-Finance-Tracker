# Personal Finance

A local Windows personal-finance app for connected banks, manual accounts, transactions, budgets, investments, debt and net worth. Run your own copy with your own Plaid credentials. The app serves only your local computer; financial databases, keys, backups and local context stay outside Git.

## Start on Windows

1. Install Python 3.13 and Node.js 22.12 or later if needed.
2. Run **Setup Finance.cmd** once to install dependencies and build the dashboard. This step needs internet access.
3. Copy `.env.example` to `.env` in the project root. Keep `APP_ENV=production` for the everyday **All accounts** interface. Manual accounts work without Plaid keys. For bank connections, enter your own Client ID and Production secret locally, complete Bank sync setup and enable `PLAID_LIVE_ENABLED=true` when ready. Never commit or share `.env`.
4. Double-click **Start Finance.cmd** to open the app in its own Python console. Leave that console open while using the dashboard. Press **Ctrl+C** there to stop it. Saved data and manual entry work offline; bank sync requires internet access.

From the VS Code PowerShell terminal, run `& .\.venv\Scripts\python.exe -m finance`, or use `.\Start Finance.ps1`. Both run directly in the terminal, so **Ctrl+C** stops the server without the batch-file **Terminate batch job (Y/N)?** prompt. Run the same command again to restart. If local PowerShell policy blocks the script, use the direct Python command.

## Everyday mode and developer mode

```dotenv
APP_ENV=production
```

Production opens one **All accounts** app. There is no workspace switch or Sandbox preview. Add manual accounts (including Nelnet) in **Accounts → Add manual account**; edit balances, names and debt details from their account inspector. Use **Transactions → Add manual transaction** for manual-account entries, or select an existing manual entry to edit/delete it. Templates and categories remain available. Connected bank transactions retain their existing classification/sync behavior. All manual accounts are included automatically; avoid creating manual duplicates of already connected bank accounts.

Bank feeds and manually entered records retain their existing separate files internally, so existing data and saved bank authorizations are preserved. They share the same overview, reports, budgets and account list. **Backup & restore → Back up all accounts** saves both source files together under locks. Keep both backups; to recover everything, restore the manual backup first and the connected backup second. Individual-source backups/restores remain available.

Set `APP_ENV=development` and restart to expose the separate Manual, Sandbox preview and Live workspaces for testing. Existing installations without APP_ENV retain that development interface until configured. Mode changes do not delete, merge or move existing databases. Production also rejects Sandbox API routes. `APP_ENV` controls the app interface, while `PLAID_LIVE_ENABLED` independently controls real-bank authorization; production mode never enables linking by itself. The legacy `PLAID_ENV=sandbox` and Sandbox secret are only for developer testing; real connections use the explicit Production secret.

The instructions below that mention a Workspace selector or Sandbox scenario apply to **development mode**. For everyday production use, stay in All accounts and open the same destination directly.

## Startup backup, sync and snapshot

Every production backend launch runs one background pass: create an automatic Manual + Connected backup pair if the previous successful automatic pair is at least 24 hours old, sync each existing authorized institution, then save a timestamped snapshot of current saved account balances. A compact notification shows progress without moving the dashboard. Successful completion disappears after six seconds; failures remain until dismissed. **View details** opens Bank sync, where the full startup results remain available. A browser reload or another tab does not trigger a new run. Leave the backend running until startup finishes; Ctrl+C requests shutdown after the in-flight operation finishes, skips remaining sync actions and attempts the final snapshot.

Startup uses the same transaction cursors, investment consent checks and 30-second product cooldowns as Refresh all. It never creates bank connections or calls paid Transactions/Investments Refresh add-ons. Individual failures do not stop other products. A backup failure is reported and sync still proceeds. No authorized banks is a normal skipped sync; manual accounts still appear in the snapshot.

Automatic pairs live under `backups/automatic/`, are listed in Backup & restore with dates and file sizes, and use the normal validated preview/restore flow. Keep both files from the same set; restore Manual first, then Connected. Only the newest 14 complete automatic sets are retained; manually created and recovery backups are untouched. Incomplete pairs are not published. A local backup is still on the same drive, so keep an external copy for drive-loss recovery.

Each startup attempts to save a snapshot even if sync fails. Its saved timestamp does not replace each account's last actual balance-update time. Stale values remain stale and do not create fresh chart observations. Raw snapshots are timestamped; charts still show the latest daily observation. Repeated same-day launches do not add more daily chart points. An empty installation has no balances to snapshot yet.

Startup maintenance is enabled by default in production. To disable it, set `APP_STARTUP_ENABLED=false` in `.env` and restart. Development mode never runs startup maintenance. Automatic backups do not run on a timer while the app is open.

## Dashboard layout

The interface uses graphite/blue dark mode and a complete light theme. On desktop, all eight sections are in the sidebar. On phones and smaller tablets, use the bottom navigation for **Overview**, **Spending**, **Transactions**, and **Accounts**; **More** opens **Budgets**, **Investments**, **Bank sync**, and **Backup & restore**. Workspace/scenario selectors and the appearance switch stay in the top area.

Use **ⓘ Help** in each page header for a guide to that page. Hover over supported controls or focus them with Tab for quick explanations of their effects. Tooltips dismiss with Escape; the Help panel also works on phones, traps keyboard focus and returns focus when closed. Help covers sync types, connection reuse, balance history, portfolio values, classification/review, budgets and backup/restore. Viewing help never runs a financial action or syncs a bank.

Select an account to open its inspector: balance, source information, history, transactions, manual editing and debt details remain available there. Transaction editors open as side panels on desktop and tall sheets on phones, with explicit Save/Cancel. Provider details remain collapsed until opened. Press Escape or use Close to dismiss a panel; keyboard focus returns to its source.

Overview places cash flow and budget progress alongside net worth, followed by the spending calendar, recent activity and account composition. Net worth account selection is beside its measure controls; its phone/tablet picker stays above the bottom navigation. **About this workspace** expands the environment explanation. Relevant warnings and the Live readiness/linking lock remain visible in their existing workflows. These layout changes preserve financial calculations, stored data, workspace separation and all connection/restore safeguards.

Reduced-motion, reduced-transparency and higher-contrast preferences have dedicated fallbacks. The interface bundles Lexend locally and adds no external font request or motion dependency.

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

## Budget planning and income allocation

**Budgets** opens in **Monthly overview**, showing full-month posted USD income, net spending, income left after spending and its percentage of income. Needs/Wants spending compares with a 50/30/20 guide, or your Custom percentages. No opening-cash setup, strict category limits or assignments are required. Existing savings are not new income. Surplus is potential savings, not confirmed deposits: principal payments and investment transfers can change cash separately. Uncategorized spending stays separate from Needs/Wants; pending and needs-review entries are excluded. Category bucket changes update the monthly guide. The donut shows positive net spending, with refund totals preserved in the legend.

**Detailed plan**, **Spending breakdown**, and **Income allocations** retain optional advanced tools and existing allocations. Category limits preserve existing monthly limits. Edit several values and choose Save changes, copy missing limits from last month, or open a category to set its group, Needs/Wants/Savings bucket, target and repeat behavior.

Choose **Set up funded budgeting** inside Detailed plan to assign cash to jobs. Percentage guides work without funding setup; only Custom percentages displays editable percentages. Review the saved cash-account balances and update dates, select the credit cards to track, and confirm. A validated backup is created first. Opening cash is anchored to today's reviewed saved balances; existing posted history is not added to that cash again. Your chosen cash accounts fund the plan independently of net worth. Retirement, investment, loan and property balances cannot fund it. Expected future income is not available money.

Funded plans show **Assigned / Activity / Available**. Assigned is funding added this month. Activity covers transactions after the opening anchor; earlier imported activity remains in monthly spending reports. Available includes carried funds. Positive balances carry forward; cash overspending reduces later funding, while unfunded card spending becomes card debt. **Move money** covers overspending without changing the total assigned. The Uncategorized row can be funded without pretending the underlying transaction was categorized.

Funded card purchases reserve cash for their card payment. Existing card debt needs an explicit assignment through **Assign for card debt**. Confirmed/mapped card payments use the reserve and do not count as spending twice. Link transfers correctly in Transactions; an unresolved outgoing movement is charged against unassigned cash until linked or reconciled. Investment journal entries are not cash transfers. Outgoing savings/investment/principal transfers can be linked once to a funded Savings & extra debt category under Goals, transfers and funding reconciliation.

Targets support monthly spending (carryover counts), contributions each month, save/refill up to an amount, and saving by a date. Percentage suggestions split new income into Needs/Wants/Savings, then fill category target gaps in category order. Any remainder stays visibly unallocated. For example, sufficient target gaps on a $200 receipt yield $100/$60/$40 using 50/30/20. Suggestions never apply automatically or move money at a bank. Edit the split before Apply, partially allocate or Skip. Only eligible posted income after setup appears; repeated syncs do not allocate it again. **Allocation history → Undo allocation** reverses budget assignments, not bank transactions.

Source corrections and cash/budget discrepancies display reconciliation notices. Review current balances and allocation history before confirming a budget-only adjustment in the current month. Stale form previews are rejected. Funded allocations cannot spend future income; historical assignments are rejected if they would over-allocate current funds. Changing methods preserves transactions, targets and allocations; category-limit mode shows its original limits rather than claiming those limits are funded.

Spending breakdown has category/group donuts, matching transaction drilldowns, account filters, budget-versus-actual bars, prior-month comparisons and an allocation-mix chart. Slices show positive net spending; net-refund categories are listed separately so totals reconcile. Transfers and savings allocations are not spending. The summary's monthly spending covers all saved USD accounts; the breakdown filter scopes its charts and drilldowns. Funding activity starts at setup and can therefore differ from full-month spending.

All budget configuration, opening anchors, target/group settings and allocation/undo records live in the corresponding financial database and are included in its normal backup. The budget feature has its own schema version; older supported backups acquire empty budget tables on a temporary migration copy. Manual and connected data still restore in the normal Manual-first/Connected-second order. Budgeting requires no additional external API or bank consent.

### Manage categories

Open **Budgets → Manage categories**, or use the management shortcut inside a category's details. Search and filter Active/Archived/All and Income/Expenses. Linked-record counts explain which transactions, limits, targets, allocation history, rules or templates use each category.

- **Rename** retains the category's identity and all existing links. Duplicate names are rejected; use Restore or Merge instead.
- **Archive / Restore** keeps history and money intact. Archived categories disappear from new manual/bulk assignment choices and income-split suggestions. Existing activity or funds can keep an archived budget row visible with an Archived label; move the funds out or restore the category to work with it. Saved merchant rules are paused when archived and are not automatically enabled on restore. Historical report filters retain the category.
- **Delete** appears only for categories with no linked records, including target settings and old allocation/rule/template references. Deletion is confirmed and backed up. Used categories should be archived or merged.
- **Merge** supports categories of the same type. Review counts and budget effects, choose whether conflicting monthly limits are added together or the destination limit is kept, then type `MERGE CATEGORIES`. Existing destination target/group settings win; source settings move if the destination has none. Transactions, templates, rules and allocation records move without changing transaction amounts, account balances or payment classifications. Category balances/card reserves are recalculated and previewed. Income/opening signatures are rebased only for the category changes made by the merge. A stale preview must be refreshed.

In production All accounts, category changes apply to both the connected projection and the canonical manual store in one transaction. Stable category links prevent the old name from returning on refresh. Merge/delete create validated backups first; for full recovery, restore Manual before Connected. Allocation Undo does not reverse a category merge. In development Manual, a category linked to connected data cannot be merged/deleted independently: use production All accounts to keep both stores consistent.

## Debt details and manual student loans

For Nelnet, select **Accounts → Add manual account → Student loan (manual)** in production mode. In development, choose Manual first and use Add account. Enter the current total amount owed as a positive opening balance. Expand the account, then **Debt details → Add debt details** to enter the servicer, APR/interest rate, fixed/variable rate type, original principal, principal balance, accrued interest, repayment plan, status, and notes. **More balances, payments and dates** contains optional credit limits, payment/statement information, and loan dates. Each Nelnet loan group can have its own account and rate. No Plaid connection is needed.

Saving or clearing debt terms does not change the account balance or create transactions. Use **Record balance** to reconcile with the servicer's reported total. For a manual loan payment, record principal as a transfer to the loan and interest/fees as a separate expense; this keeps principal out of spending and reduces net worth only by the expense. There is no automatic interest accrual, amortization calculation, or upcoming-bills feature.

For imported debts, choose **Sandbox preview → Banking → Bank sync → Sync debt details**, or expand a debt account and use its **Debt details → Sync debt details** button. This retrieves the latest available Liabilities data for all supported debts on the same saved Item. Accounts displays supplied credit limits/available credit, multiple card APRs, statement/payment information, student loan details, mortgages, other loans, and credit lines where available. Missing values remain unavailable, and zero rates/payments remain zero. Source timestamps and additional provider details are shown; unnecessary account/payment-reference numbers are discarded. Loan names and guarantors are not labeled as loan servicers.

Debt details are stored locally and included in workspace/scenario backups. Manual and Sandbox terms stay separate. The existing banking Sandbox connection was successfully tested with five reported liabilities. Institution-specific live coverage and Production connections remain separate rollout work.

## Back up the database

Open **Backup & restore** in the sidebar. In production, **Back up all accounts** creates a pair of bundles for Manual and Connected account data; restore Manual first, then Connected for a full recovery. Individual-source backups remain available. In development, choose Manual, a Sandbox scenario, or Live and back up each separately. Bundles in the project `backups/` folder include its database and chart selection: accounts, transactions, budgets, balance history, debt details, review/rule state, confirmed/dismissed transfer links, and investment holdings/activity. Appearance, the current workspace/scenario choices, `.env` credentials, and context documents are not included.

Choose **Preview restore** to validate a bundle and inspect its workspace/scenario, snapshot date, account/transaction and investment counts, sample accounts, and activity dates. Type the displayed confirmation phrase before restoring. A validated `recovery-…finance-backup.zip` of the current workspace is created first; use it to recover the previous data if needed. A bundle restores to its own named workspace/scenario, regardless of the current sidebar selection. The app refreshes its cached records after success. Other workspaces/scenarios are unaffected. Close other Finance instances and finish/cancel pending bank authorization for the destination before restoring.

Sandbox restores retain the current authorization when the backup belongs to the same Item, while restoring the backup's transaction cursor with its data. No bank request or new Item is created; the next explicit sync may replay later activity. Different-Item backups are rejected while connected. Empty recovery restores keep the current authorization and reset the feed checkpoint. Saved authorization can be recovered without a current connection only if the Windows profile can unlock it. API credentials must be retained separately.

Existing `.db` backups are also listed. They are checked and migrated on a temporary copy; current chart selection is retained because legacy files do not include it. Corrupt, incompatible, oversized, or stale restore previews are rejected. The UI supports databases up to approximately 256 MB. Bundles contain private financial records; only the stored bank tokens are Windows-user encrypted. Keep a separate copy on storage you control if you need protection from loss of the project or drive.

The original Manual-only CLI remains available: `.venv\Scripts\python.exe -m finance.backup`. It creates a consistent, integrity-checked `.db` file in `backups/`. Context notes and checkpoints remain local-only in `context_docs/`.

## Prepare Live bank connections

In production, open **Bank sync** for readiness checks. In development, choose **Workspace → Live → Bank sync**. Live data, credentials, authorizations, chart settings and backups are isolated from Sandbox. Completed checks are collapsed under **Setup details**; outstanding checks stay visible. Production credentials are configured locally, and record your own Dashboard connection allowance, product access and institution/OAuth availability. Trial entitlements and supported institutions can differ between developers. Hosted Link is requested automatically and needs no separate Dashboard enablement. Use **Find your bank** to check actual institution coverage and prepare its entry. Real-bank linking is controlled by `PLAID_LIVE_ENABLED`; it cannot be enabled from the browser.

Follow [Live setup and BofA pilot](LIVE_SETUP.md) to finish configuration without connecting a bank. Use the explicit Production entries in `.env.example`; keep the existing Sandbox secret. Live supports Transactions, balances and explicitly selected Investments. Live debt-provider sync and live stock quotes remain deferred. This app remains a local, single-user desktop service, not an internet deployment.

## Try Plaid Sandbox (development mode)

1. Open [Plaid Dashboard → Developers → Keys](https://dashboard.plaid.com/developers/keys) and locate the Sandbox credentials.
2. In the project root, create a local `.env` file with the following names. Replace the placeholders in your editor.

   ```dotenv
   APP_ENV=development
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

## Multiple institutions and manual accounts (development workspaces)

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

In **Accounts** or **Bank sync**, choose **Refresh all** to fetch the applicable products on each connected institution: banking transactions, investment holdings, investment activity, then balances. It reuses existing authorizations and does not create Items or request paid Refresh products/live quotes. Keep the app open until it finishes; progress remains available while browsing. Disconnected institutions are excluded, reconnect-required or consent-pending products are skipped, and each product keeps the normal 30-second cooldown. One failed request does not stop other products/institutions. Expand the result summary for each updated, failed or skipped action. A second run can retry failures while skipping recently completed products. Production also runs this sequence once per backend launch unless startup maintenance is disabled; manual sync/restore waits until startup finishes.

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

The **Reporting month** controls transaction reports and budgets. **Net worth** and **Where it sits** show current saved balances, independently of that month. The balance chart uses its own date range and account selection. One recorded day shows **History tracking started**, even after repeated same-day refreshes; observations on later days build a trend. Transaction backfill does not supply historical balance snapshots. Open Accounts for per-account data update times; saved observations are not live market quotes.

### Snapshot history and banking estimates

Overview defaults to **Snapshots**: saved account balance observations. A solid line connects consecutive recorded days; missing days or accounts leave gaps. One day shows a compact **History tracking started** state. An institution’s old balance copied during another bank’s refresh is not a new observation. Same-day fetches use the latest saved value for that day. Account balance history removes duplicate copied observations, while raw records remain available for recovery.

Choose **Banking estimates** for a separate dashed-line view of supported synced USD deposit and credit accounts in your chart selection. Investments, loans, Manual accounts and unsupported accounts are excluded; the current net-worth total above still covers the full selection. The estimate’s own total covers banking accounts only. **Banking estimate coverage** explains each account’s inclusion, transaction dates and anchor time/source. Accounts need completed historical transaction sync, a dated balance observation and posted transactions.

Estimates work backward from the latest saved banking balance using raw signed posted movements, including transfers, card payments and unclassified credits. Pending and removed records are excluded. Report category or merchant edits do not change this calculation. Estimates stop at the available transaction boundary or a 730-day lookback and at the saved balance anchor; only the overlap shared by included accounts is charted. Missing transactions, intraday/cached anchors and posting delays can cause drift. Coverage warns when newer transactions need a newer balance anchor or an earlier snapshot disagrees with the estimate. Estimates are calculated locally without a new provider request, extra product or price API, and never overwrite observed balances, snapshots or your ledger.

To collect more snapshots, launch the production backend with startup maintenance enabled, or use **Accounts → Refresh all** or **Bank sync → Refresh all**; alternatively choose **Refresh balances** for every included institution. Successful balance fetches save observations and also feed the investment portfolio chart. Holdings sync can save balance observations when those balances update. A failed fetch contributes no fresh balance for that account; the combined chart leaves a gap if any selected account is missing. Repeat on another day for another daily point. Browser reloads do not sync banks, and missing days are not invented. For Manual accounts, **Accounts → Record balance** updates dated account history and records net worth when saved.

The Investments chart continues to show recorded account values only. Historical market-value estimates need historical positions/cash and prices, which this app does not fetch. Portfolio value includes deposits and withdrawals and is not an investment-return calculation. Expand **Snapshots, estimates & adding history** on either chart for guidance and a link to the refresh controls.

## Source code and privacy

Clone this repository, run setup locally, and configure your own `.env`. This repository contains application source and synthetic test fixtures, not the author’s financial records or credentials. `.env`, `data/`, `backups/`, local context folders and development skills are ignored. Plaid authorizations in backups are encrypted for the Windows user that created them; configure and connect your own accounts. No GitHub Actions workflows or hosted service are required. Optional local regression-test commands are documented under Local development.
