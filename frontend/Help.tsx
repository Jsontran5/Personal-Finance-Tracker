import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { Page } from './Navigation'
import { trapDialogFocus } from './dialogFocus'

// Explanations describe effects, not just labels. Keep product behavior here in sync
// with the controls; a data-help attribute can override context-specific wording.
export const controlHelp: Record<string, string> = {
  'Snapshots': 'Show saved account balance observations. Only balances observed on a recorded day contribute to that day; missing days or accounts leave gaps. Opening a chart does not fetch banks.',
  'Banking estimates': 'Reconstruct supported banking balances from saved posted transaction amounts, including transfers. Dashed lines are approximate; investments, loans and manual accounts are excluded. Estimates never change snapshots or current balances.',
  'Snapshots, estimates & adding history': 'Learn the difference between recorded balances and reconstructed banking history, then open the existing balance-refresh controls to collect more observations.',
  'Banking estimate coverage': 'See included and excluded accounts, the available transaction dates, balance anchor timestamps, and any disagreement with earlier recorded balances.',
  'Report category': 'Filter all report totals, trends, calendars and drilldowns by your assigned category. This is separate from choosing bank or local categories for the breakdown.',
  'Report comparison': 'Compare against the previous month or an earlier month you choose. The same account and category filters apply to both periods.',
  'Filter transaction type': 'Show only expenses, income, refunds, transfers or investment journal entries. Confirmed transfer pairs are treated as transfers.',
  'Sort transactions': 'Sort the entire matching list before pagination. Largest and smallest use the unsigned amount; compare amounts within the same currency.',
  'Filter posting status': 'Pending entries can appear in the ledger but do not count toward income or spending reports.',
  'Income': 'Posted credits classified as income, excluding refunds, transfers, pending entries and credits awaiting classification.',
  'Transaction direction': 'From and To show the source and destination when known. Confirmed pairs use your account names; bank-reported counterparties and explicit bank descriptions are labeled. Unknown endpoints stay marked as not supplied. In/Out and amount signs describe money direction, independently of income, refund or transfer classification.',
  'Sync transactions': 'Pull newly available checking, savings and credit-card transactions and corrections. Investment trades are loaded separately with Sync activity. This reuses your connection.',
  'Sync holdings': 'Load the latest positions available from Plaid: securities, quantities, reported prices and values, including cash when supplied. This does not request live market quotes. Run this before Sync activity.',
  'Sync activity': 'Load broker-reported trades, dividends, fees and investment transfers. These records stay separate from everyday spending. Run Sync holdings first.',
  'Refresh all': 'Fetch applicable Live transactions, investment holdings and activity, then account balances, one institution at a time. Reuses saved connections, skips cooldowns or reconnect-required accounts, and continues after individual failures. This does not request live quotes or move money.',
  'Refresh balances': 'Request current account balances from the institution. Saved balances drive net worth and record a chart observation; they can differ from available cash or holdings values. A failure retains previous data.',
  'Reconnect / manage accounts': 'Reauthorize this existing connection or change shared accounts where supported. Update mode keeps the same bank-login connection (Plaid Item) rather than creating another one. It is not a normal data refresh.',
  'Connect institution': 'Start authorization for this saved institution. A successful new connection creates a Plaid Item that counts toward your Trial allowance. Select the same institution inside Plaid.',
  'Connect institution (locked)': 'Real-bank authorization is disabled by the local server setting. Setup confirmations alone cannot enable it.',
  'Check connection': 'Check whether the saved Plaid authorization session has finished. After it connects, use the sync buttons to load data.',
  'Cancel Link': 'Discard the pending authorization session. An existing connected Item and its saved data are retained. Cancellation is blocked while a token exchange is unresolved.',
  'Find your bank': 'Open institution search to check coverage and prepare an entry. Searching or adding a draft does not authorize your bank.',
  'Search institutions': 'Search institutions that advertise the selected products. This does not create a bank connection or prove that every account is supported.',
  'Connection type': 'Banking transactions loads checking, savings and card activity. Investments loads broker holdings and investment activity. Choose both only for a supported mixed institution.',
  'Also connect investments': 'Request investment access along with banking on this institution. Both products must be supported. It does not automatically include every account offered under the same brand.',
  'Add institution': 'Save this institution as a draft after checking product coverage. Authorizing it is a separate step on its saved entry.',
  'Enable investments on this connection': 'Prepare investment access on the existing banking Item. Reconnect afterward to grant consent; this button alone does not authorize or sync investments.',
  'Reload investment history': 'Recheck the full available investment history for older corrections. Normal activity sync reconciles a recent overlapping window. Broker history may be incomplete.',
  'Reload transaction history': 'Replay available bank transaction history while retaining the saved connection and local classifications. Available dates depend on the institution.',
  'Save confirmations': 'Save your Dashboard access confirmations locally. This neither changes your Plaid plan nor authorizes accounts or enables the server linking switch.',
  'Investments product access confirmed (optional)': 'Confirm that your Plaid developer account has Investments access. Required for investment connections, optional for banking-only use. This does not connect accounts.',
  'Refresh checks': 'Reload local readiness settings and saved coverage results. This does not sync transactions or refresh account balances.',
  'Sync details and additional data': 'View separate sync times, transaction history status and additional product actions for this institution. One successful sync does not mean every product is up to date.',
  'Manage connection': 'Open Bank sync, where each institution has its own authorization and data-sync controls.',
  'Create backup': 'Save a local snapshot of the selected workspace and its chart selection. Other workspaces need their own backups. API keys and local context documents are not included.',
  'Preview restore': 'Validate and inspect this backup without replacing data. Actual restoration requires a separate typed confirmation.',
  'Restore workspace': 'Replace this workspace with the previewed backup after confirmation. A recovery backup is saved first. Other workspaces stay separate; connected Items must match.',
  'Refresh list': 'Reload the list of local backup files. This does not contact any bank.',
  'Backup workspace': 'Select which independent data store to back up. Live, Manual and each Sandbox scenario have separate records.',
  'Workspace': 'Manual holds records you enter yourself. Sandbox contains test data. Live contains authorized real accounts and selected manual mirrors. Switching does not sync or connect banks.',
  'Sandbox scenario': 'Switch between independent Banking, Investments and Combined test data. This does not create a new connection.',
  'Reporting month': 'Choose the month for spending, income, cash flow, budgets and the ledger. Net worth and Where it sits use current saved balances, independent of this month. The balance chart has its own recorded-history range.',
  'Dark mode': 'Switch the app appearance. This does not change financial data or your workspace.',
  'Net worth': 'Assets minus debts, using selected saved account balances. Holdings are not added again. Historical transaction backfill does not supply historical balances.',
  'Assets': 'Show the selected accounts’ positive saved balances over recorded dates. Brokerage balances already include their reported cash and securities.',
  'Debt': 'Show liabilities from the selected accounts’ saved balances. Payments between your accounts should be transfers, so they are not counted twice as spending.',
  'All accounts': 'Include all eligible accounts in this net-worth chart selection. Spending filters and the underlying saved records do not change.',
  'Cash flow': 'Income minus posted spending for the reporting month. Transfers, investment activity, pending entries and credits awaiting classification are excluded.',
  'Budget progress': 'Compare posted spending assigned to your categories with the limits you set for this month. Bank categories and your budget categories are separate.',
  'Portfolio value': 'Use saved investment account balances, counting cash and securities once. This is account value, not a calculation of investment returns.',
  'Where it’s invested': 'Allocation uses positive reported USD holdings, including reported cash. Missing values, other currencies and short positions are excluded; holdings may differ from account balances.',
  'Holdings': 'Current saved investment positions. Values and prices come from the broker through Plaid, not a live stock-price feed.',
  'Activity': 'Broker-reported investment events such as trades, dividends, fees and transfers. They do not automatically enter your spending ledger.',
  'Investment account': 'Filter portfolio holdings, activity and observed value history to one investment account.',
  'Provider details': 'Expand the saved provider record. Missing fields mean unavailable data; raw values are not extra transactions or additional assets.',
  'Provider transaction details': 'See the bank-supplied details for this transaction. Availability varies; missing data is not zero.',
  'Raw provider data': 'Inspect the complete saved provider response. This is for reference and does not change the ledger.',
  'Balance chart & history': 'View balances recorded by this app. A single recorded date produces one dot; later observations build the trend.',
  'New merchant rule': 'Create an exact-merchant classification rule, optionally scoped to an account. Preview affected records before applying it to history or future imports.',
  'Select page': 'Select only the transactions on the current page for a bulk action.',
  'Select matching': 'Select transactions matching the current review filters, across pages. Check the selected count before applying an action.',
  'Clear selection': 'Clear the bulk-action selection without changing transactions.',
  'Classify selected': 'Choose income, spending, refund, transfer or investment treatment for selected records. Preview the changes before applying.',
  'Mark reviewed': 'Mark selected entries as reviewed. This does not change their amounts or classification; ambiguous credits still need classification.',
  'Flag for review': 'Add entries to your review queue. Flagging a purchase does not remove it from spending.',
  'Preview changes': 'Show which records would change and which would be skipped before applying this action.',
  'Confirm transfer': 'Link matching movements between your accounts so their reporting treatment is a transfer. This does not move money or change bank balances.',
  'Dismiss suggestion': 'Hide this proposed transfer match without changing the original transactions.',
  'Bank categories': 'Group spending by the categories supplied by Plaid. This does not assign your own budget categories.',
  'Your categories': 'Group spending by categories you assigned in this app. Uncategorized entries remain separate.',
  'Add transaction': 'Enter a manual ledger entry. Synced bank transactions are loaded through Bank sync instead.',
  'Rename account': 'Set a local display name for this synced account throughout the workspace. Account IDs, balances, transactions and bank permissions stay the same.',
  'Save nickname': 'Save this workspace account nickname. Future syncs keep it; a blank nickname restores the latest bank-provided name.',
  'Use bank name': 'Remove the local nickname and display the latest name supplied by the bank. No financial data is changed.',
  'Data updates': 'See exact successful fetch times and product-specific failures for an account. Balance time belongs to its saved observation; transactions, holdings and activity use institution fetch times. Needs refresh is a reminder after 24 hours or before the first fetch, not a live-data guarantee.',
  'Account nickname': 'Choose a local display name, up to 120 characters. Leave blank to use the bank name. This does not rename the account at your bank.',
  'Add account': 'Create a manually maintained account. A bank connection is added separately in Bank sync.',
  'Set limit': 'Set a monthly spending target for this category. It does not restrict payments or transfer money.',
  'Edit limit': 'Change this category’s target for the selected month. Existing transactions are not changed.',
  'New category': 'Create a category for your own transaction classifications and budgets. This does not change Plaid’s bank categories.',
  'Record balance': 'Save a dated balance for a manual account. This updates its recorded balance history; it does not fetch bank data.',
  'Edit account': 'Change this manual account’s saved details. Bank account permissions are managed separately in Bank sync.',
  'Manage in Manual': 'Open the original manual workspace to edit this mirrored account. The combined view reads from that original account.',
  'New template': 'Save a reusable manual transaction form. Templates do not automatically create transactions or schedule bank payments.',
  'Use template': 'Open a manual transaction form with this template’s values. Review and save it to create an entry.',
  'Clear report filter': 'Remove the drilldown from the spending report while leaving your saved transactions unchanged.',
  'Overview': 'Open your saved net worth, cash flow, spending calendar and account summary. Opening this page does not sync banks.',
  'Spending': 'Explore Spending, Income and Cash flow reports, with account and category filters, a calendar, comparison trends and category charts.',
  'Transactions': 'Browse, search and classify the ledger. Investment activity has its own view under Investments.',
  'Budgets': 'Start with Monthly overview: compare posted income, spending and income left over with a percentage guide. Detailed plan keeps optional limits and funded allocations. Budgets do not restrict bank payments.',
  'Accounts': 'View saved account balances and history, or maintain manual accounts and debt details.',
  'Investments': 'View reported portfolio positions and investment activity. Use Bank sync to retrieve updates.',
  'Bank sync': 'Manage each institution and retrieve its transactions, balances, holdings or activity. Opening the page does not sync banks.',
  'Backup & restore': 'Save local workspace snapshots and preview recovery. Creating a backup is separate from restoring it.',
  'Delete transaction': 'Permanently delete this manual transaction after acknowledging the warning. There is no per-entry undo; recovering from a backup replaces the workspace.',
  'Confirm link': 'Confirm the previewed pair as a transfer. Both entries are excluded from income and spending; saved bank balances and original source records remain unchanged.',
  'Unlink and restore reporting': 'Remove this pairing and restore each original entry’s reporting treatment. No bank transaction is deleted.',
  'Connection allowance shown in Dashboard': 'Record your Dashboard connection limit. Local counts cannot see connections created by other apps or previously consumed Trial slots.',
  'Review pair': 'Preview two proposed matching transactions before confirming them as a transfer. No money is moved.',
  'Unlink pair': 'Remove the confirmed pairing and return each transaction to its own reporting classification. Original bank records remain.',
  'Restore suggestion': 'Return a dismissed transfer match to the suggestion list without confirming it.',
  'Apply preview': 'Apply the changes shown in the preview. Records that no longer qualify are skipped.',
  'Apply preview & save rule': 'Apply the previewed classifications and save the merchant rule for future matching imports if enabled.',
  'Search transactions': 'Filter saved ledger entries by search text. This does not request more history from the bank.',
  'Review date scope': 'Choose whether review filters cover this reporting month or all saved transaction dates.',
  'Debt details': 'View available loan or card terms. Manual debt details are maintained by you; the account balance remains the total used in net worth.',
}

const guides: Record<Page, [string, string][]> = {
  Overview: ['Net worth', 'Cash flow', 'Budget progress', 'Reporting month'].map(k => [k, controlHelp[k]]),
  Spending: ['Reporting month', 'Income', 'Report category', 'Report comparison', 'Bank categories', 'Your categories', 'Cash flow'].map(k => [k, controlHelp[k]]),
  Transactions: ['Transaction direction', 'Filter transaction type', 'Filter posting status', 'Sort transactions', 'Sync transactions', 'New merchant rule', 'Classify selected', 'Mark reviewed', 'Flag for review', 'Provider transaction details'].map(k => [k, controlHelp[k]]),
  Budgets: [['Group by', 'Detailed plan defaults to Needs, Wants, Savings & extra debt, and a separate Uncategorized group. Switch to Custom group to use saved category headings. This changes presentation only; assignments and custom group names are preserved. Change a category’s Budget bucket to move it between bucket groups.'], ['Monthly overview', 'Full reporting-month posted USD income and purchases less refunds, independent of funding setup. Surplus is income minus spending, not confirmed savings or an account balance. Existing savings, transfers, pending and needs-review entries are excluded from income. Uncategorized spending stays outside Needs and Wants.'], ['Budget methods', '50/30/20 is a fixed guide; edit percentages only under Custom percentages. Neither requires opening cash or assignments for Monthly overview. Detailed plan retains optional category limits and funded zero-based budgeting. Changing methods preserves allocations and transactions.'], ['Manage categories', 'Rename keeps existing links. Archive hides new-entry choices, pauses saved merchant rules and preserves history/funds. Restore does not re-enable rules automatically. Delete is only for categories without linked records. Merge previews transaction/budget/rule/template changes, lets you add or keep conflicting limits, and creates a backup before you confirm. Destination target settings win if both categories have settings.'], ['Ready to assign', 'Opening budget cash plus eligible received income, less allocations and unreserved budget outflows. Zero means every available dollar has a job. Retirement and investment balances are not spendable budget funds.'], ['Assigned / Activity / Available', 'Assigned is this month’s funding. Activity starts after your opening anchor. Available includes carryover. Funded card purchases reserve cash for the card payment; the payment is not another expense.'], ['Income allocations', 'New posted income after setup can be split with your preset. Suggestions fill category target gaps and leave any remainder unassigned. Review before Apply; no bank transfer is sent. Undo changes the budget only.'], ['Targets and rollover', 'Use monthly, contribution, refill or dated savings targets. Positive funds carry over. Cash overspending reduces later funding; uncovered card spending becomes debt.'], ['Spending breakdown', 'Donuts show positive net spending, with net-refund categories separately listed. Assigned savings and transfers are not spending. Click categories or groups to inspect matching transactions.'], ['Reporting month', controlHelp['Reporting month']]],
  Accounts: ['Refresh all', 'Data updates', 'Net worth', 'Balance chart & history', 'Rename account', 'Use bank name', 'Add account', 'Debt details'].map(k => [k, controlHelp[k]]),
  Investments: ['Portfolio value', 'Holdings', 'Where it’s invested', 'Activity', 'Sync holdings', 'Sync activity'].map(k => [k, controlHelp[k]]),
  'Bank sync': ['Refresh all', 'Reconnect / manage accounts', 'Sync transactions', 'Sync holdings', 'Sync activity', 'Refresh balances', 'Connection type', 'Also connect investments', 'Investments product access confirmed (optional)', 'Sync details and additional data'].map(k => [k, controlHelp[k]]),
  'Backup & restore': ['Create backup', 'Preview restore', 'Restore workspace', 'Refresh list'].map(k => [k, controlHelp[k]]),
}

export function explanation(element: HTMLElement): string | undefined {
  if (element.dataset.help) return element.dataset.help
  const label = element.getAttribute('aria-label') || (element instanceof HTMLInputElement || element instanceof HTMLSelectElement ? element.labels?.[0]?.textContent : element.textContent) || ''
  const normalized = label.replace(/\s+/g, ' ').trim()
  if (controlHelp[normalized]) return controlHelp[normalized]
  if (/^Select matching\b/.test(normalized)) return controlHelp['Select matching']
  if (/^Accounts\s*[·•]/.test(normalized)) return 'Choose which accounts contribute to this net-worth chart. This changes chart totals only, not spending, bank consent or saved balances.'
  if (/^(1M|3M|6M|1Y|All)$/.test(normalized) && element.closest('.range')) return 'Change this chart’s date range using saved observations. This does not load additional bank history.'
  return undefined
}

function positionTooltip(anchor: HTMLElement, tooltip: HTMLElement) {
  const rect = anchor.getBoundingClientRect(), size = tooltip.getBoundingClientRect()
  tooltip.style.left = `${Math.max(12, Math.min(rect.left, document.documentElement.clientWidth - size.width - 12))}px`
  tooltip.style.top = `${Math.max(12, rect.top >= size.height + 20 ? rect.top - size.height - 10 : Math.min(rect.bottom + 10, window.innerHeight - size.height - 12))}px`
}

function HoverHelp({ page }: { page: Page }) {
  const id = useId()
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const bubble = useRef<HTMLDivElement>(null)
  const message = anchor ? explanation(anchor) : undefined
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    let focusedAnchor: HTMLElement | null = null
    const clear = () => { clearTimeout(timer); focusedAnchor = null; setAnchor(null) }
    const show = (target: EventTarget | null, delay: number) => {
      clearTimeout(timer)
      const element = target instanceof Element ? target.closest<HTMLElement>('[data-help],button,input,select,summary,h2') : null
      if (!element || element.closest('.page-help') || !explanation(element)) { setAnchor(null); return }
      if (delay) timer = setTimeout(() => setAnchor(element), delay)
      else setAnchor(element)
    }
    const over = (e: PointerEvent) => {
      if (focusedAnchor === document.activeElement) { clearTimeout(timer); return }
      if (e.target instanceof Element && e.target.closest('.context-tooltip')) { clearTimeout(timer); return }
      if (e.pointerType !== 'touch') show(e.target, 450)
    }
    const out = (e: PointerEvent) => {
      if (focusedAnchor === document.activeElement) { clearTimeout(timer); return }
      if (e.relatedTarget instanceof Element && e.relatedTarget.closest('.context-tooltip')) return
      clearTimeout(timer)
      timer = setTimeout(() => setAnchor(current => current?.contains(document.activeElement) ? current : null), 160)
    }
    const focus = (e: FocusEvent) => {
      show(e.target, 0)
      focusedAnchor = e.target instanceof HTMLElement && explanation(e.target) ? e.target : null
    }
    const blur = () => clear()
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape' && bubble.current) { e.preventDefault(); e.stopPropagation(); clear() } }
    const scrolled = () => {
      const focused = document.activeElement
      if (focusedAnchor && focusedAnchor === focused) {
        if (bubble.current) positionTooltip(focusedAnchor, bubble.current)
      } else clear()
    }
    document.addEventListener('pointerover', over)
    document.addEventListener('pointerout', out)
    document.addEventListener('focusin', focus)
    document.addEventListener('focusout', blur)
    document.addEventListener('keydown', key, true)
    document.addEventListener('click', clear)
    window.addEventListener('scroll', scrolled, true)
    window.addEventListener('resize', scrolled)
    return () => {
      clearTimeout(timer)
      document.removeEventListener('pointerover', over); document.removeEventListener('pointerout', out)
      document.removeEventListener('focusin', focus); document.removeEventListener('focusout', blur)
      document.removeEventListener('keydown', key, true); document.removeEventListener('click', clear)
      window.removeEventListener('scroll', scrolled, true); window.removeEventListener('resize', scrolled)
    }
  }, [page])
  useLayoutEffect(() => {
    if (!anchor || !message || !bubble.current || !anchor.isConnected) return
    const tooltip = bubble.current
    const descriptions = (anchor.getAttribute('aria-describedby') || '').split(' ').filter(Boolean)
    anchor.setAttribute('aria-describedby', [...descriptions, id].join(' '))
    tooltip.showPopover()
    positionTooltip(anchor, tooltip)
    return () => {
      const remaining = (anchor.getAttribute('aria-describedby') || '').split(' ').filter(value => value && value !== id)
      if (remaining.length) anchor.setAttribute('aria-describedby', remaining.join(' '))
      else anchor.removeAttribute('aria-describedby')
    }
  }, [anchor, message, id])
  useEffect(() => setAnchor(null), [page])
  return anchor && message ? createPortal(<div ref={bubble} id={id} role="tooltip" popover="manual" className="context-tooltip">{message}</div>, anchor.closest('dialog[open]') || document.body) : null
}

export function PageHelp({ page }: { page: Page }) {
  const [open, setOpen] = useState(false)
  const panel = useRef<HTMLDialogElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const title = useId()
  useEffect(() => {
    if (!open) return
    panel.current?.showModal()
    return () => trigger.current?.focus()
  }, [open])
  return <>
    <button ref={trigger} type="button" className="secondary page-help-trigger" aria-label={`Help with ${page}`} aria-haspopup="dialog" onClick={() => setOpen(true)}>ⓘ Help</button>
    <HoverHelp page={page} />
    {open && <dialog ref={panel} className="page-help" aria-labelledby={title} onKeyDown={trapDialogFocus} onCancel={event => { event.preventDefault(); setOpen(false) }}>
      <div className="dialog-heading"><h2 id={title}>Help with {page}</h2><button type="button" className="icon" aria-label="Close help" onClick={() => setOpen(false)}>×</button></div>
      <p className="muted">Hover over a control or focus it with Tab for a quick explanation. On a phone, use this guide. Help never runs an action or contacts your bank.</p>
      <dl className="help-topics">{guides[page].map(([label, text]) => <div key={label}><dt>{label}</dt><dd>{text}</dd></div>)}</dl>
      <details><summary>Workspaces and saved data</summary><p>{controlHelp.Workspace}</p><p>Production mode backs up if overdue, syncs existing banks and saves a snapshot once per backend launch, unless APP_STARTUP_ENABLED is false. Startup results show progress and failures. A browser refresh does not sync banks. Development workspaces sync on demand.</p></details>
      <button type="button" className="secondary" onClick={() => setOpen(false)}>Done</button>
    </dialog>}
  </>
}
