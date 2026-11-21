# Live Plaid setup

This is a single-user app served only on loopback. Production here means Plaid's real-data API, not public hosting of this app. It has no internet-facing login service. Keep the existing loopback binding and host/write protections.

With `APP_ENV=production`, each backend launch backs up if overdue, syncs existing authorized institutions and saves a snapshot. Progress appears in the dashboard. Set `APP_STARTUP_ENABLED=false` to disable this during setup or troubleshooting. This does not authorize new institutions or change the separate linking switch. Development mode remains on-demand. See the README startup section for retention, shutdown and freshness behavior.

## What is prepared

- With `APP_ENV=production`, **All accounts** combines connected and manually maintained accounts. Add/edit manual accounts and transactions directly from Accounts and Transactions. Sandbox controls and the workspace switch are hidden. `APP_ENV=development` restores the separate developer workspaces.
- Connected data remains in `data/finance.plaid-live.db`; manual records remain in their original database and appear automatically in the combined view. Existing files and encrypted authorizations are retained. Sandbox tokens cannot be imported into Live. No sample data is inserted.
- Explicit Production credentials and a fixed Production API host. The Sandbox secret never serves as a Production fallback. The Production transport permits only the required read-only data/institution calls and Link/token-exchange operations; money movement, Item removal, identity/auth-number collection and paid Refresh operations are not enabled.
- Live scope: checking/credit-card Transactions and balances, plus opt-in investment holdings/activity. Banking-only Link requests still require only Transactions. Investment-only connections request Investments; explicitly mixed connections request both. Live debt-provider sync is deferred; add manual debt accounts, including Nelnet, from Accounts. No Auth, Identity or Liabilities consent is added. Existing purchases, ambiguous credits and transfers follow the app's current reporting/review rules.
- A server-side linking lock. Browser state cannot bypass it. This preparation did not enable the pilot or create a real Item.

## Finish local configuration before linking

1. Set `APP_ENV=production`, restart the app and open **Bank sync**. In development mode, choose **Workspace → Live** first. The readiness card shows outstanding checks first. Completed confirmations are collapsed under **Setup details**, with credentials and advanced settings separate.
2. Keep the existing Sandbox settings in the private project `.env`. Add these entries; do not commit the file or enter secrets in chat/the browser:

   ```dotenv
   PLAID_PRODUCTION_SECRET=your_production_secret
   # Optional: leave blank to reuse PLAID_CLIENT_ID.
   PLAID_PRODUCTION_CLIENT_ID=
   PLAID_LIVE_ENABLED=false
   # Optional desktop-web redirect. If set, HTTPS and Dashboard allowlisting required.
   PLAID_PRODUCTION_REDIRECT_URI=
   ```

   `.env.example` contains the empty template. Environment variables override file settings. A configured credential indicator only means values are present; a successful institution lookup establishes acceptance for that endpoint, not every product entitlement.
3. Under **Setup details**, enter the plan, connection allowance, Transactions and OAuth access confirmations from your own Dashboard. Allowances and product access depend on your Plaid team. The app does not infer entitlement from generic documentation.
4. Hosted Link requires no separate Dashboard enablement or checkbox: the app already requests it with `hosted_link: {}`. For the regular desktop browser flow, we use Plaid's hosted experience and server-side `/link/token/get` completion checks. No public webhook/tunnel or exposing this local app is necessary. Custom/native-app redirect behavior is different; an optional configured Production redirect must use HTTPS without credentials, queries, fragments or wildcard and be allowlisted in Dashboard. Its validation appears only when configured.
5. Click **Find your bank**, then search **Add another institution** for Bank of America. **Add institution** performs a read-only Production institution lookup and saves a local draft. It verifies advertised Transactions support and reports OAuth metadata. It does not create a Link token, authorize a bank, or exchange a public token. A successful institution response does not prove account-level coverage or successful real OAuth. Coverage must be checked again within 24 hours of linking and after any credential change. Failed checks invalidate cached success.
6. Leave `PLAID_LIVE_ENABLED=false`. Real-bank linking is a separately approved next step. Readiness confirmations and backups cannot enable that environment switch. There is no enable-linking button in the browser.

Confirm Investments access before adding a broker. See the README Live investments section for investment-only, mixed and existing-Item consent flows. `APP_ENV` controls the app interface; `PLAID_LIVE_ENABLED` independently controls authorization of new real-bank connections.

## Separately approved first pilot

After the setup is reviewed, enable the local switch for the pilot. Do not do this as part of preparation.

- Create and validate Manual/Live backups. Confirm actual usage and remaining allowance in Dashboard; local counts cannot see other projects, deleted Items or previously consumed slots. Pending new Link sessions reserve the local configured allowance, and duplicate institutions are blocked. This does not guarantee the number of remaining Dashboard slots.
- Use the existing BofA draft, open its saved Hosted Link URL in a normal browser, and choose the same institution. Select intended checking and credit-card accounts, finish bank consent, return and check the saved session. The app verifies institution metadata before exchanging the public token. Do not start another connection to work around an uncertain result.
- Sync transactions and explicitly refresh balances. Initial transaction history can arrive in stages; the saved history status appears under Sync details. Sync again after the 30-second cooldown if preparing. Compare both account balances to the bank, inspect posted/pending entries and dates, and classify ambiguous credits rather than assuming income. A requested 730-day window does not guarantee that the bank supplies all 730 days.
- Confirm card purchases count as spending once. Checking-side card payments and card-side payment credits should be transfers; confirm valid matching pairs. Synced account balances, not sums of transaction history or holdings, drive net worth.
- Restart the backend/browser and verify the same Item/cursor is reused. Exercise reconnect in update mode only if needed, never a new Link to repair the same institution. Back up the validated data afterward.

Stop the pilot on missing/incorrect balances, unexpected accounts, incomplete consent or uncertain token exchange. Preserve the database and request IDs for diagnosis. Do not delete/relink to troubleshoot: on Trial, removed/duplicate Items may still consume the allowance. An unresolved public-token exchange retries the same encrypted saved token; recovery from every consumed/expired public token is not guaranteed and cannot safely be automated into a new Item.

## Backups and verification

In production, **Backup & restore → Back up all accounts** creates two validated bundles: Manual account data and Connected account data. Keep both. For a full recovery, restore Manual first, then Connected. Restore validates environment/schema and the matching connected Item set, preserves current encrypted authorization for matching Items, and blocks pending Link sessions. It never restores API credentials or the environment linking switch. Authorization is protected by the current Windows user's DPAPI; whole financial databases/backups are private local files and are not whole-file encrypted by this feature.

- Build: `npm.cmd run build`
- Unit/API checks: `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`
- Isolated UI checks: `python tests/check_live_ui.py`. Uses a synthetic Production institution provider, 15 synthetic accounts, long institutions and both themes at desktop/phone widths; checks readiness/coverage/locked actions, workspace separation, backend restart and confirmed restore. No real Production requests/Items.
- Existing regression checks: `python tests/check_combined_ui.py` and `python tests/check_account_ui.py`. Test artifacts and screenshots stay ignored in `data/ui-qa`.

References: [Hosted Link](https://plaid.com/docs/link/hosted-link/), [OAuth guide](https://plaid.com/docs/link/oauth/), [Institutions API](https://plaid.com/docs/api/institutions/), [Trial plan](https://support.plaid.com/hc/en-us/articles/39994173227159-What-is-the-Plaid-Trial-plan).
