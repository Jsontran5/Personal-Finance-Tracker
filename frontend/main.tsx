import { trapDialogFocus } from './dialogFocus'
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { Icon } from './icons'
import { BankSync } from './BankSync'
import { TransactionDetails } from './TransactionDetails'
import { AccountList, type Account } from './AccountList'
import { groupedAccounts } from './accounts'
import { Chart, NetWorth, type HistoryPoint } from './Charts'
import { HomeSpending, SpendingPage, defaultReportFilters, type ReportFilters, type SpendingReport } from './Spending'
import { AssetSummary } from './AssetSummary'
import { TransactionReview } from './TransactionReview'
import { TransferReview } from './TransferReview'
import { SyncFreshness } from './SyncFreshness'
import { BackupRestore } from './BackupRestore'
import { DeleteTransaction } from './DeleteTransaction'
import { Investments } from './Investments'
import { DebtDetails } from './DebtDetails'
import { Connections } from './Connections'
import { Navigation, type Page } from './Navigation'
import { PageHelp } from './Help'
import { AccountNickname } from './AccountNickname'
import { RefreshAll } from './RefreshAll'
import { TransactionFlow, type Flow } from './TransactionFlow'
import './styles.css'
import './interface.css'

type Kind = 'expense' | 'income' | 'refund' | 'transfer' | 'investment'
type Category = { id: number; name: string; kind: 'expense' | 'income' }
type Entry = { id: number; account_id: number; destination_id: number | null; category_id: number | null; amount_minor: number; kind: Kind; merchant: string; notes: string; date: string; transfer_pair_id?: number | null; transfer_kind?: string | null; transfer_other_account?: string | null; transfer_role?: 'incoming' | 'outgoing' | null }
type Transaction = Entry & { flow?: Flow; currency: string; account_name: string; category_name: string | null; destination_name: string | null; pending?: number; needs_review?: number; source_merchant?: string; manual_source?: number }
type Template = Omit<Entry, 'date' | 'destination_id'> & { name: string }
type Budget = Category & { limit_minor: number | null; actual_minor: number; previous_actual_minor: number }
type Point = HistoryPoint
type Overview = {
  accounts: Account[]; categories: Category[]; budgets: Budget[]; templates: Template[];
  transactions: Transaction[]; history: Point[]; primary_account_id: number | null;
  recent_entries: Pick<Entry, 'merchant' | 'category_id'>[];
  assets_minor: number; liabilities_minor: number; net_worth_minor: number;
  income_minor: number; spending_minor: number; cash_flow_minor: number; uncategorized_minor: number;
  pending_count?: number; missing_balance_count?: number; review_count?: number;
  spending: SpendingReport;
}
type Theme = 'dark' | 'light'
type Mode = 'personal' | 'sandbox' | 'live'
type Scenario = 'banking' | 'investments' | 'combined'
type DialogState =
  | { type: 'transaction'; entry?: Entry | Template; template?: boolean }
  | { type: 'account'; account?: Account }
  | { type: 'balance'; account: Account }
  | { type: 'budget'; budget: Budget }
  | { type: 'category' }
  | { type: 'sandbox-transaction'; entry: Transaction }

const kinds: Record<Kind, string> = { expense: 'Expense', income: 'Income', refund: 'Refund', transfer: 'Transfer', investment: 'Investment activity' }
const accountKinds = { checking: 'Checking', savings: 'Savings', cash: 'Cash', investment: 'Investment', credit: 'Credit card', loan: 'Loan', property: 'Property', other: 'Other asset' }
const currencies = ['USD', 'CAD', 'EUR', 'GBP', 'AUD', 'CHF', 'JPY']
const isDebt = (kind: string) => kind === 'credit' || kind === 'loan'
const scale = (currency: string) => currency === 'JPY' ? 1 : 100
const money = (amount: number, currency = 'USD') => new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(amount / scale(currency))
const editableMoney = (amount: number, currency = 'USD') => (amount / scale(currency)).toFixed(currency === 'JPY' ? 0 : 2)
const today = () => {
  const now = new Date()
  return [now.getFullYear(), String(now.getMonth() + 1).padStart(2, '0'), String(now.getDate()).padStart(2, '0')].join('-')
}
const read = (form: FormData, name: string) => String(form.get(name) ?? '').trim()

function parseMoney(value: string, currency: string) {
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(value)
  if (!match || (currency === 'JPY' && match[3])) throw new Error(currency === 'JPY' ? 'Enter a whole amount.' : 'Enter an amount with up to two decimal places.')
  const amount = Number(match[2]) * scale(currency) + Number((match[3] ?? '').padEnd(2, '0'))
  if (amount > 1_000_000_000_000) throw new Error('Enter a smaller amount.')
  return match[1] ? -amount : amount
}

async function apiRequest<T>(path: string, method = 'GET', body?: object, signal?: AbortSignal): Promise<T> {
  const response = await fetch('/api' + path, {
    method, signal,
    headers: method === 'GET' ? {} : { 'Content-Type': 'application/json', 'X-Finance-Request': '1' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await response.json()
  if (!response.ok) {
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Check the form values and try again.')
  }
  return data
}

function Modal({ title, children, close, inspector }: { title: string; children: ReactNode; close: () => void; inspector?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    ref.current?.showModal()
    ref.current?.querySelector<HTMLElement>('[data-autofocus]')?.focus()
  }, [])
  return <dialog onKeyDown={trapDialogFocus} className={inspector ? 'inspector editor-inspector' : undefined} ref={ref} onCancel={event => { event.preventDefault(); close() }} aria-labelledby="dialog-title">
    <div className="dialog-heading"><div><small className="eyebrow">{inspector ? 'DETAILS & EDITING' : 'YOUR WORKSPACE'}</small><h2 id="dialog-title">{title}</h2></div><button type="button" className="icon" aria-label="Close" onClick={close}>×</button></div>
    {children}
  </dialog>
}

function EntryForm({ data, state, save, remove, busy }: {
  data: Overview; state: Extract<DialogState, { type: 'transaction' }>;
  save: (path: string, method: string, body: () => object) => Promise<void>;
  remove: (path: string) => Promise<void>; busy: boolean
}) {
  const entry = state.entry
  const existing = entry && 'date' in entry ? entry : undefined
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [kind, setKind] = useState<Kind>(entry?.kind ?? 'expense')
  const [accountId, setAccountId] = useState(entry?.account_id ?? data.primary_account_id ?? data.accounts[0].id)
  const [categoryId, setCategoryId] = useState(entry?.category_id ?? 0)
  const [destinationId, setDestinationId] = useState(existing?.destination_id ?? 0)
  const currency = data.accounts.find(account => account.id === accountId)?.currency ?? 'USD'
  const categories = data.categories.filter(category => category.kind === (kind === 'income' ? 'income' : 'expense'))
  const canCategorize = kind !== 'transfer' && kind !== 'investment'
  const merchants = [...new Set(data.recent_entries.map(tx => tx.merchant))].slice(0, 12)
  const recentCategories = [...new Set(data.recent_entries.map(tx => tx.category_id))]
  categories.sort((a, b) => (recentCategories.indexOf(a.id) + 1000 * Number(!recentCategories.includes(a.id))) -
    (recentCategories.indexOf(b.id) + 1000 * Number(!recentCategories.includes(b.id))))

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const fields = {
      account_id: accountId, category_id: canCategorize ? categoryId || null : null, kind,
      merchant: read(form, 'merchant'), notes: read(form, 'notes'),

    }
    if (state.template) void save('/templates', 'POST', () => ({ ...fields, name: read(form, 'name'), amount_minor: parseMoney(read(form, 'amount'), currency) }))
    else void save(existing ? '/transactions/' + existing.id : '/transactions', existing ? 'PUT' : 'POST',
      () => ({ ...fields, amount_minor: parseMoney(read(form, 'amount'), currency), date: read(form, 'date'), destination_id: kind === 'transfer' ? destinationId || null : null }))
  }

  return <form onSubmit={submit}>
    <fieldset disabled={busy || !!entry?.transfer_pair_id}>
      <div className="form-grid">
        <label>Amount ({currency})<input name="amount" inputMode="decimal" required data-autofocus defaultValue={entry ? editableMoney(entry.amount_minor, currency) : ''} placeholder="0.00" /></label>
        <label>Type<select value={kind} onChange={event => { setKind(event.target.value as Kind); setCategoryId(0); setDestinationId(0) }}>
          {Object.entries(kinds).filter(([value]) => !state.template || value === 'expense' || value === 'income').map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <label>Account<select value={accountId} onChange={event => { setAccountId(Number(event.target.value)); setDestinationId(0) }}>
          {data.accounts.map(account => <option key={account.id} value={account.id}>{account.name}{account.institution_name && ` / ${account.institution_name}`}</option>)}
        </select></label>
        {state.template ? <label>Template name<input name="name" required maxLength={120} /></label>
          : <label>Date<input name="date" type="date" required max={today()} defaultValue={existing?.date ?? today()} /></label>}
        {kind === 'transfer' && <label>Destination<select required value={destinationId || ''} onChange={event => setDestinationId(Number(event.target.value))}>
          <option value="">Select an account</option>
          {data.accounts.filter(account => account.id !== accountId && account.currency === currency).map(account => <option key={account.id} value={account.id}>{account.name}{account.institution_name && ` / ${account.institution_name}`}</option>)}
        </select></label>}
        {canCategorize && <label>Category<select value={categoryId} onChange={event => setCategoryId(Number(event.target.value))}>
          <option value={0}>Uncategorized</option>{categories.map(category => <option key={category.id} value={category.id}>{category.name}</option>)}
        </select></label>}
        <label className="full">{kind === 'transfer' ? 'Description' : 'Merchant or source'}<input name="merchant" required maxLength={120} defaultValue={entry?.merchant ?? ''} list="merchants" /></label>
        <datalist id="merchants">{merchants.map(merchant => <option key={merchant} value={merchant} />)}</datalist>
        <label className="full">Notes <span className="subtle">(optional)</span><textarea name="notes" maxLength={2000} defaultValue={entry?.notes ?? ''} /></label>
      </div>
      {kind === 'investment' && <p className="muted">Investment activity is kept in the journal. Record a balance separately to value the account.</p>}
      <div className="form-actions">
        {existing && <button type="button" className="danger" onClick={() => setConfirmDelete(true)}>Delete</button>}
        <button className="primary" type="submit">{state.template ? 'Save template' : 'Save transaction'}</button>
      </div>
    </fieldset>
    {confirmDelete && existing && <DeleteTransaction transaction={existing}
      account={data.accounts.find(account => account.id === existing.account_id)?.name ?? 'Account unavailable'}
      destination={data.accounts.find(account => account.id === existing.destination_id)?.name}
      amount={money(existing.amount_minor, data.accounts.find(account => account.id === existing.account_id)?.currency ?? 'USD')}
      close={() => setConfirmDelete(false)} remove={() => remove('/transactions/' + existing.id)} />}
  </form>
}

function CashFlow({ data, month, drill }: { data: Overview; month: string; drill: (ids: number[], label: string) => void }) {
  const eligible = data.transactions.filter(tx => tx.currency === 'USD' && !tx.pending && !tx.needs_review && !tx.transfer_pair_id && ['income', 'expense', 'refund'].includes(tx.kind))
  const magnitude = Math.max(data.income_minor, Math.abs(data.spending_minor), 1)
  return <section className="card cashflow" aria-labelledby="cashflow-title">
    <div className="section-heading"><div><h2 id="cashflow-title">Cash flow</h2><small>{month} · USD</small></div>
      <span className="panel-icon"><Icon name="transactions" /></span>
    </div>
    <p className="cashflow-label">Income minus spending</p>
    <button className={'cashflow-total cashflow-value numeric ' + (data.cash_flow_minor < 0 ? 'negative' : data.cash_flow_minor > 0 ? 'positive' : '')} data-help="View the posted income, purchases and refunds included in this month’s cash flow. Transfers and pending entries are excluded." onClick={() => drill(eligible.map(tx => tx.id), 'Cash flow · ' + month)}>{money(data.cash_flow_minor)}<small>View transactions →</small></button>
    <div className="cashflow-bars">
      <button className="flow-income cashflow-drill" onClick={() => drill(eligible.filter(tx => tx.kind === 'income').map(tx => tx.id), 'Income · ' + month)}><div><span><Icon name="income" /> Income</span><strong className="numeric">{money(data.income_minor)}</strong></div>
        <progress aria-label="Income relative to the larger monthly total" max={magnitude} value={data.income_minor} /></button>
      <button className="flow-spending cashflow-drill" onClick={() => drill(eligible.filter(tx => tx.kind !== 'income').map(tx => tx.id), 'Spending · ' + month)}><div><span><Icon name="expense" /> Spending</span><strong className="numeric">{money(data.spending_minor)}</strong></div>
        <progress aria-label="Net spending magnitude relative to the larger monthly total" max={magnitude} value={Math.abs(data.spending_minor)} /></button>
    </div>
  </section>
}

function TransactionList({ transactions, edit, empty = 'No transactions match this view.', selected = [], toggle, statuses = {} }: { transactions: Transaction[]; edit: (tx: Transaction) => void; empty?: string; selected?: number[]; toggle?: (id: number) => void; statuses?: Record<number, string> }) {
  if (!transactions.length) return <p className="empty">{empty}</p>
  return <div className="ledger">
    {transactions.map(tx => {
      const fallbackDirection = tx.transfer_role === 'incoming' || tx.kind === 'income' || tx.kind === 'refund' ? 'in' : tx.transfer_role === 'outgoing' || tx.kind === 'expense' || tx.destination_name ? 'out' : 'unknown'
      const flow: Flow = tx.flow ?? { direction: fallbackDirection, from: fallbackDirection === 'in' ? tx.transfer_other_account ?? 'Source not supplied' : fallbackDirection === 'out' ? tx.account_name : 'Direction unavailable', to: fallbackDirection === 'in' ? tx.account_name : tx.destination_name ?? tx.transfer_other_account ?? 'Destination not supplied', basis: tx.destination_name ? 'manual' : 'unavailable' }
      const row = <button className="ledger-row" data-transaction-id={tx.id} data-direction={flow.direction} key={tx.id} onClick={() => edit(tx)}>
      <span className={'row-icon row-' + tx.kind + ' flow-' + flow.direction}><Icon name={flow.direction === 'in' ? 'income' : flow.direction === 'out' ? 'expense' : 'transactions'} /></span>
      <span className="row-main"><strong>{tx.merchant}</strong><TransactionFlow flow={flow} /><small className="transaction-meta">{tx.transfer_pair_id ? tx.transfer_kind === 'card_payment' ? 'Card payment' : 'Transfer' : tx.needs_review ? 'Review credit' : kinds[tx.kind]} · <time dateTime={tx.date}>{tx.date}</time>{tx.pending ? ' · Pending' : ' · Posted'}{tx.transfer_pair_id ? ' · Confirmed pair' : ''}{tx.needs_review ? ' · Review credit type' : statuses[tx.id] === 'needs_review' ? ' · Flagged' : statuses[tx.id] === 'reviewed' ? ' · Reviewed' : ''}</small></span>
      <span className="chip row-category">{tx.transfer_pair_id ? tx.transfer_kind === 'card_payment' ? 'Card payment' : 'Linked transfer' : tx.needs_review ? 'Review credit' : tx.category_name ?? kinds[tx.kind]}</span>
      <span className="row-money"><span className={'numeric ' + (flow.direction === 'in' ? 'positive' : '')}>
        {flow.direction === 'out' ? '\u2212' : flow.direction === 'in' ? '+' : ''}{money(tx.amount_minor, tx.currency)}
      </span><small>{flow.direction === 'in' ? 'In' : flow.direction === 'out' ? 'Out' : flow.direction === 'none' ? 'No movement' : 'Direction unavailable'}</small></span>
    </button>
      return toggle ? <div className="selectable-ledger-row" key={tx.id}><input type="checkbox" aria-label={'Select ' + tx.merchant + ' ' + tx.date} checked={selected.includes(tx.id)} onChange={() => toggle(tx.id)} />{row}</div> : row
    })}
  </div>
}

function BudgetRows({ budgets, edit, filter, compare = false }: { budgets: Budget[]; edit: (budget: Budget) => void; filter: (id: number) => void; compare?: boolean }) {
  if (!budgets.length) return <p className="empty">Set a category limit to see your progress here.</p>
  return <div className="budget-list">{budgets.map(budget => {
    const remaining = budget.limit_minor === null ? null : budget.limit_minor - budget.actual_minor
    const limit = budget.limit_minor ?? 0
    const percent = limit > 0 ? budget.actual_minor / limit : budget.actual_minor > 0 ? 1 : 0
    return <div className="budget-row" key={budget.id}>
      <div><button className="text-button" onClick={() => filter(budget.id)}>{budget.name}</button><button className="secondary small" onClick={() => edit(budget)}>{remaining === null ? 'Set limit' : 'Edit limit'}</button></div>
      {remaining !== null && <progress aria-label={budget.name + ' budget used'} max={1} value={Math.max(0, Math.min(1, percent))} className={remaining < 0 ? 'over' : ''} />}
      <small>{money(budget.actual_minor)} spent{remaining !== null && (' · ' + money(Math.abs(remaining)) + (remaining < 0 ? ' over' : ' remaining'))}{compare && ' · ' + money(budget.previous_actual_minor) + ' prior month'}</small>
    </div>
  })}</div>
}

function BalanceHistory({ account, sandbox = false, request }: { account: Account; sandbox?: boolean; request: typeof apiRequest }) {
  const [rows, setRows] = useState<{ id: number; effective_date: string; balance_minor: number; notes: string }[] | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  async function load() {
    if (rows || loading) return
    setLoading(true)
    setError('')
    try { setRows(await request((sandbox ? '/plaid/sandbox/preview' : '') + '/accounts/' + account.id + '/balances')) }
    catch (failure) { setError((failure as Error).message) }
    finally { setLoading(false) }
  }
  const daily = new Map<string, number>()
  for (const row of rows ?? []) {
    if (!daily.has(row.effective_date)) daily.set(row.effective_date, row.balance_minor)
  }
  const points = [...daily].sort(([a], [b]) => a.localeCompare(b)).map(([date, value_minor]) => ({ date, value_minor }))
  return <details onToggle={event => { if (event.currentTarget.open) void load() }}>
    <summary>Balance chart &amp; history</summary>
    {error && <p role="alert">{error} <button className="secondary small" onClick={() => void load()}>Retry</button></p>}
    {loading && <p role="status" className="muted">Loading balances…</p>}
    {rows && <Chart history={points} money={money} currency={account.currency} label={'Recorded balance for ' + account.name} />}
    <div className="history-scroll">{rows?.map(row => <div className="history-row" key={row.id}>
      <span>{row.effective_date} · {sandbox ? row.notes : 'Manual balance' + (row.notes ? ' · ' + row.notes : '')}</span><strong className="numeric">{money(row.balance_minor, account.currency)}</strong>
    </div>)}</div>
  </details>
}


function App() {
  const [mode, setMode] = useState<Mode | null>(null)
  const synced = mode === 'sandbox' || mode === 'live'
  const [scenario, setScenario] = useState<Scenario>('banking')
  const [scenarioPending, setScenarioPending] = useState(false)
  const request = useCallback(<T,>(path: string, method?: string, body?: object, signal?: AbortSignal): Promise<T> => {
    const prefix = mode === 'live' ? '/plaid/live' : scenario === 'combined' ? '/plaid/combined-sandbox' : scenario === 'investments' ? '/plaid/investments-sandbox' : '/plaid/sandbox'
    const chart = mode === 'live' ? 'live' : scenario === 'combined' ? 'sandbox_combined' : scenario === 'investments' ? 'sandbox_investments' : 'sandbox'
    const mapped = path.replace(/^\/plaid\/sandbox(?=\/|\?|$)/, prefix).replace(/^\/chart-selection\/sandbox(?=\?|$)/, '/chart-selection/' + chart)
    return apiRequest<T>(mapped, method, body, signal)
  }, [scenario, mode])
  const [page, setPage] = useState<Page>('Overview')
  const [month, setMonth] = useState(today().slice(0, 7))
  const [data, setData] = useState<Overview>()
  const [loadedMonth, setLoadedMonth] = useState('')
  const [categoryFilter, setCategoryFilter] = useState(0)
  const [accountFilter, setAccountFilter] = useState(0)
  const [search, setSearch] = useState('')
  const [kindFilter, setKindFilter] = useState('all')
  const [postingFilter, setPostingFilter] = useState('all')
  const [sortOrder, setSortOrder] = useState('newest')
  const [reportFilters, setReportFilters] = useState<ReportFilters>(defaultReportFilters)
  const [drillOrigin, setDrillOrigin] = useState<Page>('Spending')
  const [transactionPage, setTransactionPage] = useState(0)
  const [drilldown, setDrilldown] = useState<{ ids: number[]; label: string } | null>(null)
  const [chartAccountIds, setChartAccountIds] = useState<number[] | null>(null)
  const [reviewFilter, setReviewFilter] = useState('all')
  const [reviewStatuses, setReviewStatuses] = useState<Record<number, string>>({})
  const [selectedTransactions, setSelectedTransactions] = useState<number[]>([])
  const [reviewMessage, setReviewMessage] = useState('')
  const [allReviewDates, setAllReviewDates] = useState(false)
  const [reviewHistory, setReviewHistory] = useState<Transaction[]>([])
  const [transferFocus, setTransferFocus] = useState<number | null>(null)
  const [dialog, setDialog] = useState<DialogState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [formError, setFormError] = useState('')
  const [revision, setRevision] = useState(0)
  const [theme, setTheme] = useState<Theme>('dark')
  const [themePending, setThemePending] = useState(true)
  const [themeError, setThemeError] = useState('')
  const [modePending, setModePending] = useState(false)
  const [modeError, setModeError] = useState('')
  const [sandboxKind, setSandboxKind] = useState<Kind>('expense')
  const [sandboxCategory, setSandboxCategory] = useState(0)
  const previewPath = '/plaid/sandbox/preview'
  useEffect(() => { setTransactionPage(0) }, [search, accountFilter, categoryFilter, month, mode])
  useEffect(() => { window.scrollTo(0, 0) }, [page])
  useEffect(() => { setDrilldown(null) }, [month, mode, scenario])
  useEffect(() => { setSelectedTransactions([]); setTransactionPage(0) }, [month, mode, search, accountFilter, categoryFilter, reviewFilter, drilldown, allReviewDates, kindFilter, postingFilter, sortOrder])
  useEffect(() => { setReviewFilter('all'); setReviewStatuses({}); setReviewMessage(''); setAllReviewDates(false); setReviewHistory([]); setSelectedTransactions([]); setTransactionPage(0) }, [month, mode, scenario])

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  useEffect(() => {
    request<{ theme: Theme }>('/appearance')
      .then(value => setTheme(value.theme))
      .catch(() => setThemeError('Could not load the saved appearance.'))
      .finally(() => setThemePending(false))
  }, [])

  useEffect(() => {
    Promise.all([apiRequest<{ mode: Mode }>('/workspace'), apiRequest<{ scenario: Scenario }>('/sandbox-scenario')])
      .then(([workspace, selected]) => { setScenario(selected.scenario); setMode(workspace.mode) })
      .catch(() => { setMode('personal'); setModeError('Could not load the saved workspace.') })
  }, [])

  useEffect(() => { setReportFilters(defaultReportFilters); setKindFilter('all'); setPostingFilter('all'); setSortOrder('newest') }, [mode, scenario])

  async function changeMode(next: Mode) {
    if (next === mode) return
    setModePending(true)
    setModeError('')
    try {
      await request('/workspace', 'PUT', { mode: next })
      setDialog(null)
      setData(undefined)
      setError('')
      setCategoryFilter(0)
      setAccountFilter(0)
      setSearch('')
      setMode(next)
    } catch (failure) {
      setModeError((failure as Error).message)
    } finally {
      setModePending(false)
    }
  }

  async function changeScenario(next: Scenario) {
    if (next === scenario) return
    setScenarioPending(true); setModeError('')
    try {
      await apiRequest('/sandbox-scenario', 'PUT', { scenario: next })
      setDialog(null); setData(undefined); setError(''); setCategoryFilter(0); setAccountFilter(0); setSearch(''); setChartAccountIds(null); setTransferFocus(null)
      setScenario(next)
    } catch (failure) { setModeError((failure as Error).message) }
    finally { setScenarioPending(false) }
  }

  async function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    setThemePending(true)
    setThemeError('')
    try {
      await request('/appearance', 'PUT', { theme: next })
    } catch (failure) {
      setTheme(theme)
      setThemeError((failure as Error).message)
    } finally {
      setThemePending(false)
    }
  }

  useEffect(() => {
    if (!mode) return
    const controller = new AbortController()
    request<Overview>((synced ? previewPath : '/overview') + '?month=' + month, 'GET', undefined, controller.signal).then(result => {
      setData(result)
      setLoadedMonth(mode + scenario + month)
      setError('')
    }).catch(failure => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [month, revision, mode, scenario, request])

  function open(state: DialogState) {
    setFormError('')
    setDialog(state)
  }

  function quickAdd() {
    if (data && mode === 'personal') open(data.accounts.length ? { type: 'transaction' } : { type: 'account' })
  }

  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if (mode !== 'personal' || dialog || !data || !event.altKey || event.key.toLowerCase() !== 'n' ||
          (event.target instanceof HTMLElement && (event.target.isContentEditable ||
            event.target.matches('input, select, textarea')))) return
      event.preventDefault()
      open(data.accounts.length ? { type: 'transaction' } : { type: 'account' })
    }
    window.addEventListener('keydown', shortcut)
    return () => window.removeEventListener('keydown', shortcut)
  }, [data, dialog, mode])

  async function save(path: string, method: string, body?: () => object) {
    setBusy(true)
    setFormError('')
    try {
      await request(path, method, body?.())
      setDialog(null)
      setRevision(value => value + 1)
    } catch (failure) {
      setFormError((failure as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function remove(path: string) {
    setBusy(true)
    setFormError('')
    try {
      await request(path, 'DELETE')
      setDialog(null)
      setRevision(value => value + 1)
    } finally { setBusy(false) }
  }

  function filterCategory(id: number) {
    setAllReviewDates(false)
    setReviewFilter('all')
    setDrilldown(null)
    setKindFilter('all'); setPostingFilter('all'); setSortOrder('newest')
    setCategoryFilter(id)
    setAccountFilter(0)
    setSearch('')
    setPage('Transactions')
  }

  function submitDialog(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    if (dialog?.type === 'sandbox-transaction') {
      void save(previewPath + '/transactions/' + dialog.entry.id, 'PUT', () => ({
        merchant: read(form, 'merchant'), kind: sandboxKind,
        category_id: sandboxKind === 'transfer' || sandboxKind === 'investment' ? null : sandboxCategory || null,
        notes: read(form, 'notes'),
      }))
    } else if (dialog?.type === 'account') {
      const account = dialog.account
      const metadata = { name: read(form, 'name'), include_in_net_worth: form.has('include'), primary: form.has('primary') }
      void save(account ? '/accounts/' + account.id : '/accounts', account ? 'PUT' : 'POST', () => account ? metadata : ({
        ...metadata, kind: read(form, 'kind') === 'student' ? 'loan' : read(form, 'kind'), debt_subtype: read(form, 'kind') === 'student' ? 'student' : undefined, currency: read(form, 'currency'),
        opening_balance_minor: parseMoney(read(form, 'amount'), read(form, 'currency')), date: read(form, 'date'),
      }))
    } else if (dialog?.type === 'balance') {
      const account = dialog.account
      void save('/accounts/' + account.id + '/balances', 'POST', () => ({
        balance_minor: parseMoney(read(form, 'amount'), account.currency), date: read(form, 'date'), notes: read(form, 'notes'),
      }))
    } else if (dialog?.type === 'budget') {
      void save((synced ? previewPath : '') + '/budgets/' + month + '/' + dialog.budget.id, 'PUT', () => ({
        limit_minor: read(form, 'amount') ? parseMoney(read(form, 'amount'), 'USD') : null,
      }))
    } else if (dialog?.type === 'category') {
      void save((synced ? previewPath : '') + '/categories', 'POST', () => ({ name: read(form, 'name'), kind: read(form, 'kind') }))
    }
  }

  const activeMonth = new Date(month + '-02T12:00:00').toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
  const ready = data && loadedMonth === mode + scenario + month ? data : undefined
  const budgetRows = ready?.budgets.filter(budget => budget.limit_minor !== null).slice(0, 4) ?? []
  const editTransaction = (entry: Transaction) => {
    if (entry.manual_source) { setModeError('This manual transaction is a read-only mirror. Switch to Manual to edit it.'); return }
    if (synced) {
      setSandboxKind(entry.kind)
      setSandboxCategory(entry.category_id ?? 0)
      open({ type: 'sandbox-transaction', entry })
    } else open({ type: 'transaction', entry })
  }
  const editBudget = (budget: Budget) => open({ type: 'budget', budget })
  const drillSpending = (ids: number[], label: string, origin: Page = 'Spending') => {
    setDrillOrigin(origin); setKindFilter('all'); setPostingFilter('all'); setSortOrder('newest')
    setAllReviewDates(false)
    setReviewFilter('all')
    setDrilldown({ ids, label }); setCategoryFilter(0); setAccountFilter(0); setSearch(''); setTransactionPage(0); setPage('Transactions')
  }
  const entries = (allReviewDates ? reviewHistory : ready?.transactions)?.filter(tx => (!categoryFilter || tx.category_id === categoryFilter) &&
    (reviewFilter === 'all' || (reviewFilter === 'uncategorized' ? !tx.transfer_pair_id && tx.category_id === null && ['expense', 'income', 'refund'].includes(tx.kind) : reviewStatuses[tx.id] === reviewFilter)) &&
    (!drilldown || drilldown.ids.includes(tx.id)) &&
    (kindFilter === 'all' || (tx.transfer_pair_id ? 'transfer' : tx.kind) === kindFilter) &&
    (postingFilter === 'all' || (postingFilter === 'pending' ? !!tx.pending : !tx.pending)) &&
    (!accountFilter || tx.account_id === accountFilter) &&
    [tx.merchant, tx.account_name, tx.category_name, tx.notes].some(value => value?.toLowerCase().includes(search.trim().toLowerCase())))?.sort((a, b) => sortOrder === 'largest' ? b.amount_minor - a.amount_minor || b.id - a.id : sortOrder === 'smallest' ? a.amount_minor - b.amount_minor || b.id - a.id : sortOrder === 'oldest' ? a.date.localeCompare(b.date) || a.id - b.id : b.date.localeCompare(a.date) || b.id - a.id) ?? []
  const pageSize = 25
  const lastPage = Math.max(0, Math.ceil(entries.length / pageSize) - 1)
  const currentPage = Math.min(transactionPage, lastPage)
  const filteredAccount = ready?.accounts.find(account => account.id === accountFilter)
  const linkedEntry = dialog?.type === 'sandbox-transaction' ? dialog.entry : dialog?.type === 'transaction' ? dialog.entry : undefined
  const linkedFlow = (linkedEntry as Transaction | undefined)?.flow
  const emptyMessage = synced && filteredAccount?.transaction_count === 0
    ? 'No spending transactions have been saved for this account. Its balance is tracked separately.' +
      (filteredAccount.provider_type === 'investment' ? ' See Investments for holdings and investment activity.' :
        filteredAccount.provider_type === 'loan' ? ' Expand Debt details on Accounts for supported loan terms.' : ' Availability varies by account and institution.')
    : allReviewDates ? 'No saved transactions match these filters.' : 'No transactions match this month and these filters. Try another month or clear the filters.'

  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark"><Icon name="logo" /></span><div><strong>Personal Finance</strong><small>Private workspace</small></div></div>
      <label className="workspace-picker">Workspace<select aria-label="Workspace" value={mode ?? 'personal'} disabled={!mode || modePending || busy || !!dialog} onChange={event => void changeMode(event.target.value as Mode)}>
        <option value="personal">Manual</option><option value="sandbox">Sandbox preview</option><option value="live">Live</option>
      </select></label>
      {mode === 'sandbox' && <label className="scenario-picker">Test scenario<select aria-label="Sandbox scenario" disabled={scenarioPending || modePending || busy || !!dialog} value={scenario} onChange={event => void changeScenario(event.target.value as Scenario)}><option value="banking">Banking</option><option value="investments">Investments</option><option value="combined">Combined</option></select></label>}
      <Navigation page={page} navigate={item => { setPage(item); setKindFilter('all'); setPostingFilter('all'); setSortOrder('newest'); setAllReviewDates(false); setReviewFilter('all'); setDrilldown(null); setCategoryFilter(0); setAccountFilter(0); setSearch('') }} />
      <div className="sidebar-footer">
        <button className="theme-toggle" type="button" role="switch" aria-checked={theme === 'dark'} disabled={themePending} onClick={() => void toggleTheme()}>
          <Icon name={theme === 'dark' ? 'moon' : 'sun'} /><span>Dark mode</span><i className="switch-track" aria-hidden="true" />
        </button>
        <div className="local-status"><i aria-hidden="true" /> Local data</div>
      </div>
    </aside>
    <main>
      <header className="page-header">
        <div><span className="page-context"><i data-mode={mode} />{mode === 'live' ? 'Live workspace' : mode === 'sandbox' ? 'Sandbox · ' + scenario : 'Manual workspace'}</span><h1>{page}</h1><p className="page-description">{{
          Overview: 'Net worth, monthly cash flow, and budget progress.',
          Spending: 'See when, where, and how your spending changes.',
          Transactions: 'Browse your ledger and recurring templates.',
          Budgets: 'Plan category spending and compare it with last month.',
          Accounts: 'Track balances across the accounts you own.',
          Investments: 'See your holdings, allocation, and investment activity.',
          'Bank sync': mode === 'live' ? 'Prepare and manage real-bank connections through Plaid.' : 'Connect and refresh a test bank through Plaid.',
          'Backup & restore': 'Save local checkpoints and recover a workspace.',
        }[page]}</p>{synced && (mode === 'live' || scenario === 'banking') && page !== 'Backup & restore' && <SyncFreshness request={request} revision={revision} open={() => setPage('Bank sync')} />}</div>
        <div className="toolbar">
          <PageHelp page={page} />
          {page !== 'Accounts' && page !== 'Backup & restore' && page !== 'Investments' && <label className="reporting-month">Reporting month<input type="month" aria-label="Reporting month" value={month} disabled={page === 'Transactions' && allReviewDates} onChange={event => { if (event.target.value) setMonth(event.target.value) }} /></label>}
          {page === 'Backup & restore' || page === 'Investments' ? null : synced ? <button className="primary" onClick={() => setPage('Bank sync')}>Bank sync</button>
            : <button className="primary with-icon" disabled={!data} onClick={quickAdd}><Icon name="plus" />Add transaction</button>}
        </div>
      </header>
      <RefreshAll request={request} visible={mode === 'live' && (page === 'Accounts' || page === 'Bank sync')} onChanged={() => setRevision(value => value + 1)} />
      {themeError && <p role="alert" className="notice">{themeError}</p>}
      {modeError && <p role="alert" className="notice">{modeError}</p>}
      {synced && page !== 'Backup & restore' && <details className="workspace-info"><summary><span className="status-dot" />{mode === 'live' ? 'Live data · Local storage' : 'Test data · Sandbox preview'}<span>About this workspace</span></summary><p>{mode === 'live' ? 'Real financial data stays separate from Sandbox. Manage connection readiness in Bank sync.' : 'Test accounts, categories, and budgets.'} Spending uses posted activity.</p></details>}
      {synced && page !== 'Backup & restore' && page !== 'Investments' && !!ready?.review_count && <p className="notice">{ready.review_count} credit entries need classification as income, refunds, or transfers before they count toward cash flow.</p>}
      {synced && page !== 'Backup & restore' && !!ready?.missing_balance_count && <p className="notice">{ready.missing_balance_count} accounts have no reported balance and are excluded from net worth.</p>}
      {error && <p role="alert" className="notice">{error} <button className="secondary small" onClick={() => setRevision(value => value + 1)}>Retry</button></p>}
      {!ready && !error && <p className="muted" role="status">Loading your ledger…</p>}
      {ready && page === 'Overview' && <>
        <div className="overview-grid">
        <NetWorth workspaceLabel={mode === 'live' ? 'Live' : mode === 'sandbox' ? 'Sandbox' : 'Manual'} key={mode + scenario} accounts={ready.accounts} history={ready.history} mode={synced ? 'sandbox' : 'personal'}
          request={request} money={money} onSelectionChange={setChartAccountIds} onStart={() => synced ? setPage('Bank sync') : open({ type: 'account' })} />
        <div className="overview-side"><CashFlow data={ready} month={activeMonth} drill={(ids, label) => drillSpending(ids, label, 'Overview')} />
          <section className="card"><div className="section-heading"><div><h2>Budget progress</h2><small>{activeMonth}</small></div><button className="secondary small with-icon" onClick={() => setPage('Budgets')}>View budgets <Icon name="right" /></button></div>
            <BudgetRows budgets={budgetRows} edit={editBudget} filter={filterCategory} />
          </section>
        </div>
        </div>
        <HomeSpending report={ready.spending} money={money} drill={(ids, label) => drillSpending(ids, label, 'Overview')} changeMonth={setMonth} openSpending={() => setPage('Spending')}
          recent={<TransactionList transactions={ready.transactions.filter(tx => ready.spending.eligible_ids.includes(tx.id)).slice(0, 5)} edit={editTransaction} empty="No posted spending this month." />} />
        <div className="dashboard-grid">
          <AssetSummary accounts={ready.accounts.filter(account => chartAccountIds?.includes(account.id))} loading={chartAccountIds === null} money={money} openAccounts={() => setPage('Accounts')} />

        </div>
      </>}
      {ready && page === 'Spending' && <SpendingPage key={mode + scenario} month={month} money={money} drill={drillSpending} changeMonth={setMonth} sandbox={synced} request={request} path={synced ? previewPath : ''} accounts={ready.accounts} categories={ready.categories} filters={reportFilters} setFilters={setReportFilters} revision={revision} />}
      {ready && page === 'Transactions' && <>
        <section className="card">
          <div className="section-heading"><div><h2>{allReviewDates ? 'All saved dates' : activeMonth}</h2><small>{entries.length} entries</small></div></div>
          {reviewMessage && <p className="notice" role="status">{reviewMessage}</p>}
          <TransferReview path={(synced ? previewPath : '') + '/review/transfers'} request={request} money={money} revision={ready.transactions} focusId={transferFocus} setFocusId={setTransferFocus}
            complete={message => { setReviewMessage(message); setRevision(value => value + 1) }} />
          <TransactionReview<Transaction> key={mode + scenario} path={(synced ? previewPath : '') + '/review'} request={request} transactions={ready.transactions} categories={ready.categories} accounts={ready.accounts} allDates={allReviewDates} setAllDates={setAllReviewDates} onHistory={setReviewHistory}
            selected={selectedTransactions} setSelected={setSelectedTransactions} pageIds={entries.slice(currentPage * pageSize, (currentPage + 1) * pageSize).map(tx => tx.id)} matchingIds={entries.map(tx => tx.id)} filter={reviewFilter} setFilter={setReviewFilter} onStatuses={setReviewStatuses}
            complete={message => { setReviewMessage(message); setRevision(value => value + 1) }} />
          {drilldown && <div className="drilldown-filter"><span>{drilldown.label}</span><div className="inline-actions"><button className="secondary small" onClick={() => setDrilldown(null)}>Clear report filter</button><button className="secondary small" onClick={() => { setDrilldown(null); setPage(drillOrigin) }}>Back to {drillOrigin}</button></div></div>}
          <div className="filters">
            <input type="search" aria-label="Search transactions" placeholder="Search transactions" value={search} onChange={event => setSearch(event.target.value)} />
            <select aria-label="Filter account" value={accountFilter} onChange={event => setAccountFilter(Number(event.target.value))}>
              <option value={0}>All accounts</option>{groupedAccounts(ready.accounts).map(group => <optgroup label={group.name} key={group.name}>
                {group.accounts.map(account => <option key={account.id} value={account.id}>{account.name}{account.institution_name && ` / ${account.institution_name}`}</option>)}
              </optgroup>)}
            </select>
            <select aria-label="Filter category" value={categoryFilter} onChange={event => setCategoryFilter(Number(event.target.value))}>
              <option value={0}>All categories</option>
              {ready.categories.map(category => <option value={category.id} key={category.id}>{category.name}</option>)}
            </select>
          </div>
          <div className="filters transaction-type-filters">
            <label>Transaction type<select aria-label="Filter transaction type" value={kindFilter} onChange={event => setKindFilter(event.target.value)}><option value="all">All types</option>{Object.entries(kinds).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            <label>Status<select aria-label="Filter posting status" value={postingFilter} onChange={event => setPostingFilter(event.target.value)}><option value="all">Posted &amp; pending</option><option value="posted">Posted only</option><option value="pending">Pending only</option></select></label>
            <label>Sort by<select aria-label="Sort transactions" value={sortOrder} onChange={event => setSortOrder(event.target.value)}><option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="largest">Largest amount</option><option value="smallest">Smallest amount</option></select></label>
          </div>
          {postingFilter !== 'posted' && <p className="analytics-note">Pending entries can appear here but are excluded from income and spending reports. Amount sorting uses magnitude within each currency.</p>}
          <TransactionList transactions={entries.slice(currentPage * pageSize, (currentPage + 1) * pageSize)} edit={editTransaction} empty={emptyMessage} selected={selectedTransactions} statuses={reviewStatuses}
            toggle={id => setSelectedTransactions(value => value.includes(id) ? value.filter(item => item !== id) : [...value, id])} />
          {entries.length > pageSize && <div className="pagination" aria-label="Transaction pages">
            <span className="muted" role="status">{currentPage * pageSize + 1}–{Math.min((currentPage + 1) * pageSize, entries.length)} of {entries.length}</span>
            <div className="inline-actions"><button className="secondary small" disabled={currentPage === 0} onClick={() => setTransactionPage(currentPage - 1)}>Previous</button>
              <button className="secondary small" disabled={currentPage === lastPage} onClick={() => setTransactionPage(currentPage + 1)}>Next</button></div>
          </div>}
        </section>
        {mode === 'personal' && <section className="card stack"><div className="section-heading"><div><h2>Recurring templates</h2><small>Reuse a paycheck or regular expense.</small></div>
          <button className="secondary small" disabled={!ready.accounts.length} onClick={() => open({ type: 'transaction', template: true })}>New template</button></div>
          {!ready.templates.length && <p className="empty">Saved templates will appear here.</p>}
          {ready.templates.map(template => <div className="history-row" key={template.id}><strong>{template.name}</strong><div className="inline-actions">
            <button className="secondary small" onClick={() => open({ type: 'transaction', entry: template })}>Use template</button>
            <button className="danger small" onClick={async () => {
              if (!window.confirm('Delete this template?')) return
              try { await request('/templates/' + template.id, 'DELETE'); setRevision(value => value + 1) }
              catch (failure) { setError((failure as Error).message) }
            }}>Delete</button></div></div>)}
        </section>}
      </>}
      {ready && page === 'Budgets' && <section className="card">
        <div className="section-heading"><div><h2>Category limits</h2><small>{activeMonth} · USD</small></div><button className="secondary small" onClick={() => open({ type: 'category' })}>New category</button></div>
        <BudgetRows budgets={ready.budgets} compare edit={editBudget} filter={filterCategory} />
        <p className="muted">Uncategorized spending: {money(ready.uncategorized_minor)}</p>
      </section>}
      {ready && page === 'Accounts' && <AccountList live={mode === 'live'} key={mode + scenario} accounts={ready.accounts} sandbox={synced} primary={ready.primary_account_id}
        money={money} add={() => open({ type: 'account' })} details={account => <>
          <button className="secondary small" onClick={() => { setPage('Transactions'); setAllReviewDates(true); setAccountFilter(account.id); setCategoryFilter(0); setReviewFilter('all'); setSearch(''); setDrilldown(null) }}>View account transactions →</button>
          {mode === 'personal' && <div className="inline-actions"><button className="secondary small" onClick={() => open({ type: 'balance', account })}>Record balance</button>
            <button className="secondary small" onClick={() => open({ type: 'account', account })}>Edit account</button></div>}
          {synced && account.balance_source !== 'manual' && <AccountNickname account={account} request={request} onChanged={() => setRevision(value => value + 1)} />}
          {synced && account.balance_source === 'manual' && <button className="secondary small" onClick={() => void changeMode('personal')}>Manage in Manual</button>}
          {isDebt(account.kind) && <DebtDetails live={mode === 'live'} account={account} sandbox={synced} managed={mode === 'live' || scenario === 'combined'} request={request} money={money} parseMoney={parseMoney} revision={revision} onChanged={() => setRevision(value => value + 1)} />}
          <BalanceHistory key={revision} account={account} sandbox={synced} request={request} />
        </>} />}
      {page === 'Bank sync' && (mode === 'live' || scenario === 'combined' ? <Connections key={mode + scenario} revision={revision} live={mode === 'live'} request={request} onChanged={() => setRevision(value => value + 1)} /> : <BankSync key={scenario} investmentScenario={scenario === 'investments'} openInvestments={() => setPage('Investments')} request={request} money={money} onChanged={() => setRevision(value => value + 1)} />)}
      {page === 'Investments' && <Investments live={mode === 'live'} key={mode + scenario} sandbox={synced} managed={mode === 'live' || scenario === 'combined'} request={request} money={money} openBank={() => setPage('Bank sync')} openTestScenario={mode === 'sandbox' && scenario === 'banking' ? () => void changeScenario('investments') : undefined} onChanged={() => setRevision(value => value + 1)} />}
      {page === 'Backup & restore' && <BackupRestore key={mode + scenario} request={request} initialWorkspace={mode === 'live' ? 'live' : synced && scenario === 'combined' ? 'sandbox_combined' : synced && scenario === 'investments' ? 'sandbox_investments' : mode ?? 'personal'} onRestored={() => {
        setData(undefined); setDialog(null); setSelectedTransactions([]); setReviewHistory([]); setReviewStatuses({}); setChartAccountIds(null)
        setAllReviewDates(false); setDrilldown(null); setAccountFilter(0); setCategoryFilter(0); setSearch(''); setTransferFocus(null); setReviewMessage(''); setRevision(value => value + 1)
      }} />}
    </main>
    {dialog && data && <Modal inspector={['transaction', 'sandbox-transaction', 'account', 'balance'].includes(dialog.type)} title={
      dialog.type === 'account' ? dialog.account ? 'Edit account' : 'Add account'
        : dialog.type === 'balance' ? 'Record balance'
        : dialog.type === 'budget' ? dialog.budget.name + ' limit'
        : dialog.type === 'category' ? 'New category'
        : dialog.type === 'sandbox-transaction' ? mode === 'live' ? 'Classify bank transaction' : 'Classify test transaction'
        : dialog.template ? 'New template' : dialog.entry && 'date' in dialog.entry ? 'Edit transaction' : 'Add transaction'
    } close={() => { if (!busy) setDialog(null) }}>
      {linkedFlow && <div className="transaction-flow-detail"><TransactionFlow flow={linkedFlow} /></div>}
      {formError && <p className="notice" role="alert">{formError}</p>}
      {!!linkedEntry?.transfer_pair_id && <p className="notice">This entry belongs to a confirmed transfer. Unlink the pair before changing its classification.<button type="button" className="secondary small" onClick={() => { setTransferFocus(linkedEntry.transfer_pair_id!); setDialog(null); setPage('Transactions') }}>Manage linked pair</button></p>}
      {dialog.type === 'transaction' ? <EntryForm data={data} state={dialog} save={save} remove={remove} busy={busy} /> :
        <form onSubmit={submitDialog}><fieldset disabled={busy || !!linkedEntry?.transfer_pair_id}>
          {dialog.type === 'account' && <>
            <label>Name<input data-autofocus name="name" required maxLength={120} defaultValue={dialog.account?.name} placeholder="e.g. Everyday checking" /></label>
            {!dialog.account && <>
              <div className="form-grid">
                <label>Type<select name="kind">{Object.entries(accountKinds).map(([value, label]) => <option value={value} key={value}>{label}</option>)}<option value="student">Student loan (manual)</option></select></label>
                <label>Currency<select name="currency">{currencies.map(currency => <option key={currency}>{currency}</option>)}</select></label>
                <label>Opening balance<input name="amount" inputMode="decimal" defaultValue="0" required /></label>
                <label>Balance date<input name="date" type="date" defaultValue={today()} max={today()} required /></label>
              </div>
              <p className="muted">For loans and credit cards, enter the amount owed as a positive number. For Nelnet, choose Student loan (manual); add rates and terms under the account’s Debt details.</p>
            </>}
            <label className="checkbox"><input name="include" type="checkbox" defaultChecked={dialog.account ? !!dialog.account.include_in_net_worth : true} /> Include in net worth</label>
            <label className="checkbox"><input name="primary" type="checkbox" defaultChecked={!!dialog.account && dialog.account.id === data.primary_account_id} /> Primary spending account</label>
          </>}
          {dialog.type === 'balance' && <>
            <p className="muted">Record the balance for {dialog.account.name} after any entries already logged for the selected day.</p>
            <label>{isDebt(dialog.account.kind) ? 'Amount owed' : 'Balance'} ({dialog.account.currency})<input data-autofocus name="amount" inputMode="decimal" required /></label>
            <label>Date<input name="date" type="date" defaultValue={today()} max={today()} required /></label>
            <label>Notes<textarea name="notes" maxLength={2000} /></label>
          </>}
          {dialog.type === 'budget' && <>
            <p className="muted">{activeMonth} · Leave blank to clear this limit.</p>
            <label>Limit (USD)<input data-autofocus name="amount" inputMode="decimal" defaultValue={dialog.budget.limit_minor === null ? '' : editableMoney(dialog.budget.limit_minor)} /></label>
          </>}
          {dialog.type === 'category' && <>
            <label>Name<input data-autofocus name="name" required maxLength={120} /></label>
            <label>Type<select name="kind"><option value="expense">Expense</option><option value="income">Income</option></select></label>
          </>}
          {dialog.type === 'sandbox-transaction' && <>
            <p className="muted">{dialog.entry.date} · {money(dialog.entry.amount_minor, dialog.entry.currency)} · {dialog.entry.account_name}</p>
            <label>Merchant<input name="merchant" data-autofocus required maxLength={120} defaultValue={dialog.entry.merchant} /></label>
            <label>Type<select value={sandboxKind} onChange={event => { setSandboxKind(event.target.value as Kind); setSandboxCategory(0) }}>
              {Object.entries(kinds).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}
            </select></label>
            {sandboxKind !== 'transfer' && sandboxKind !== 'investment' && <label>Category<select value={sandboxCategory} onChange={event => setSandboxCategory(Number(event.target.value))}>
              <option value={0}>Uncategorized</option>
              {data.categories.filter(category => category.kind === (sandboxKind === 'income' ? 'income' : 'expense')).map(category => <option key={category.id} value={category.id}>{category.name}</option>)}
            </select></label>}
            <label>Notes<textarea name="notes" maxLength={2000} defaultValue={dialog.entry.notes} /></label>
            <TransactionDetails id={dialog.entry.id} request={request} />
          </>}
          <div className="form-actions"><button className="secondary" type="button" onClick={() => setDialog(null)}>Cancel</button><button className="primary" type="submit">Save</button></div>
        </fieldset></form>}
    </Modal>}
  </div>
}

createRoot(document.getElementById('root')!).render(<App />)

