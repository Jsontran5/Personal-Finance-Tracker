# Live Plaid setup and BofA pilot

This is a single-user app served only on loopback. Production here means Plaid's real-data API, not public hosting of this app. It has no internet-facing login service. Keep the existing loopback binding and host/write protections.

## What is prepared

- A separate Live workspace: `data/finance.plaid-live.db`, schema 9, with an explicit Production environment marker. Personal and all Sandbox stores are unchanged. Sandbox tokens cannot be imported into Live, and Live backups restore only to Live.
- Independent encrypted authorizations, cursors, pending Link sessions, connection ownership, reporting, chart preferences and backup/restore. Explicitly selected manual Personal accounts can appear as read-only mirrors, including Nelnet. There are no sample balances or real bank connections in the project's Live workspace.
- Explicit Production credentials and a fixed Production API host. The Sandbox secret never serves as a Production fallback. The Production transport permits only the required read-only data/institution calls and Link/token-exchange operations; money movement, Item removal, identity/auth-number collection and paid Refresh operations are not enabled.
- First pilot scope: checking/credit-card Transactions plus balances, on demand. Live investment/debt-term sync is deferred, while manual debt profiles remain usable through Personal. New Link requests require only Transactions, without requesting Auth, Identity, Investments or Liabilities consent. Existing purchases, ambiguous credits and transfers follow the app's current reporting/review rules.
- A server-side linking lock. Browser state cannot bypass it. This preparation did not enable the pilot or create a real Item.

## Finish local configuration before linking

1. Restart the app and open **Workspace → Live → Bank sync**. The Live readiness card distinguishes local configuration, your Dashboard confirmations, and read-only institution coverage.
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
3. In **Dashboard confirmations**, record the plan, allowance and enabled Transactions product from your own Dashboard. Jason confirmed an active free Trial, 20 connections and Transactions on November 15, 2026; those three values are saved locally. OAuth/BofA access and Hosted Link enablement remain unchecked until verified. Trial teams may have different allowances: the generic public docs list 10, while this Dashboard shows 20. The app does not infer entitlement from generic documentation.
4. Check OAuth institution access and Hosted Link enablement in Plaid Dashboard. For the regular desktop browser Hosted Link flow, we use Plaid's hosted experience and server-side `/link/token/get` completion checks. No public webhook/tunnel or exposing this local app is necessary. Custom/native-app redirect behavior is different; an optional configured Production redirect must use HTTPS without credentials, queries, fragments or wildcard and be allowlisted in Dashboard.
5. Search **Add another institution** for Bank of America. **Add institution** performs a read-only Production institution lookup and saves a local draft. It verifies advertised Transactions support and reports OAuth metadata. It does not create a Link token, authorize a bank, or exchange a public token. A successful institution response does not prove account-level coverage or successful real OAuth. Coverage must be checked again within 24 hours of linking and after any credential change. Failed checks invalidate cached success.
6. Leave `PLAID_LIVE_ENABLED=false`. Real-bank linking is a separately approved next step. Readiness confirmations and backups cannot enable that environment switch. There is no enable-linking button in the browser.

Current project status: Production credentials are not configured, OAuth/Hosted Link confirmations and an actual BofA coverage lookup remain outstanding. No Production request was made during preparation; all Production-path browser/API tests used a synthetic provider or intercepted transport. No claim of successful BofA OAuth/linking has been made.

## Separately approved first pilot

After the setup is reviewed, enable the local switch for the pilot. Do not do this as part of preparation.

- Create and validate Personal/Live backups. Confirm actual usage and remaining allowance in Dashboard; local counts cannot see other projects, deleted Items or previously consumed slots. Pending new Link sessions reserve the local configured allowance, and duplicate institutions are blocked. This does not guarantee the number of remaining Dashboard slots.
- Use the existing BofA draft, open its saved Hosted Link URL in a normal browser, and choose the same institution. Select intended checking and credit-card accounts, finish bank consent, return and check the saved session. The app verifies institution metadata before exchanging the public token. Do not start another connection to work around an uncertain result.
- Sync transactions and explicitly refresh balances. Initial transaction history can arrive in stages; the saved history status appears under Sync details. Sync again after the 30-second cooldown if preparing. Compare both account balances to the bank, inspect posted/pending entries and dates, and classify ambiguous credits rather than assuming income. A requested 730-day window does not guarantee that the bank supplies all 730 days.
- Confirm card purchases count as spending once. Checking-side card payments and card-side payment credits should be transfers; confirm valid matching pairs. Synced account balances, not sums of transaction history or holdings, drive net worth.
- Restart the backend/browser and verify the same Item/cursor is reused. Exercise reconnect in update mode only if needed, never a new Link to repair the same institution. Back up the validated data afterward.

Stop the pilot on missing/incorrect balances, unexpected accounts, incomplete consent or uncertain token exchange. Preserve the database and request IDs for diagnosis. Do not delete/relink to troubleshoot: on Trial, removed/duplicate Items may still consume the allowance. An unresolved public-token exchange retries the same encrypted saved token; recovery from every consumed/expired public token is not guaranteed and cannot safely be automated into a new Item.

## Backups and verification

**Backup & restore → Live** includes Live data/setup/coverage and chart selection. Restore validates the environment/schema and matching connected Item set, preserves current encrypted authorization, and blocks pending Link sessions. It never restores API credentials or the environment linking switch. Manual mirrors refresh from Personal; back up Personal separately. Authorization is protected by the current Windows user's DPAPI; whole financial databases/backups are private local files and are not whole-file encrypted by this feature.

- Build: `npm.cmd run build`
- Unit/API checks: `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`
- Isolated UI checks: `python tests/check_live_ui.py`. Uses a synthetic Production institution provider, 15 synthetic accounts, long institutions and both themes at desktop/phone widths; checks readiness/coverage/locked actions, workspace separation, backend restart and confirmed restore. No real Production requests/Items.
- Existing regression checks: `python tests/check_combined_ui.py` and `python tests/check_account_ui.py`. Test artifacts and screenshots stay ignored in `data/ui-qa`.

Official references reviewed November 15, 2026: [Hosted Link](https://plaid.com/docs/link/hosted-link/), [OAuth guide](https://plaid.com/docs/link/oauth/), [Institutions API](https://plaid.com/docs/api/institutions/), [Trial plan](https://support.plaid.com/hc/en-us/articles/39994173227159-What-is-the-Plaid-Trial-plan). Plaid MCP tools were unavailable in this session; no Build Kit acceptance verdict is claimed.
