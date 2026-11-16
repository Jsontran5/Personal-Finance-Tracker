import { useEffect, useState } from 'react'
import { Chart } from './Charts'
import { SnapshotGuide } from './BankingHistory'

type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>
type Money = (value: number, currency?: string) => string
type Account = { id: string; name: string; subtype: string | null; currency: string | null; current_minor: number | null; checked_at: string | null }
type Holding = { account_id: string; security_id: string; name: string; ticker: string | null; type: string | null; cash_equivalent: boolean; currency: string | null;
  quantity: number | string | null; price: number | string | null; value_minor: number | null; cost_basis_minor: number | null; gain_minor: number | null;
  price_as_of: string | null; retrieved_at: string; provider: object; security: object }
type Portfolio = { accounts: Account[]; holdings: Holding[]; state: { holdings_at: string | null; activity_at: string | null; activity_start: string | null; activity_end: string | null } | null;
  history: { date: string; accounts: { account_id: string; currency: string | null; value_minor: number | null }[] }[] }
type Activity = { id: string; account_name: string; date: string; name: string; type: string | null; subtype: string | null; quantity: number | string | null;
  security_name: string | null; ticker: string | null; amount_minor: number | null; price: number | string | null; fees_minor: number | null; currency: string | null; provider: object; security: object }
type ActivityPage = { rows: Activity[]; total: number; page_size: number }
const sandboxPath = '/plaid/sandbox/investments'
const stamp = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : 'Not synced'
const quantity = (value: number | string | null) => value != null && Number.isFinite(Number(value)) ? new Intl.NumberFormat('en-US', { maximumFractionDigits: 8 }).format(Number(value)) : '—'
const nameOf = (kind: string | null) => kind === 'etf' ? 'ETF' : kind ? kind.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase()) : 'Other'

function ProviderDetails({ provider, security }: { provider: object; security: object }) {
  return <details className="investment-provider"><summary>Provider details</summary><pre>{JSON.stringify({ record: provider, security }, null, 2)}</pre></details>
}

export function Investments({ sandbox, managed, live, request, money, openBank, onChanged, openTestScenario }: {
  sandbox: boolean; managed?: boolean; live?: boolean; request: Request; money: Money; openBank: () => void; onChanged: () => void; openTestScenario?: () => void
}) {
  const path = live ? '/plaid/live/investments' : sandboxPath
  const [data, setData] = useState<Portfolio>()
  const [activity, setActivity] = useState<ActivityPage>()
  const [tab, setTab] = useState('holdings')
  const [account, setAccount] = useState('')
  const [search, setSearch] = useState('')
  const [kind, setKind] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [page, setPage] = useState(1)
  const [holdingPage, setHoldingPage] = useState(1)
  const [revision, setRevision] = useState(0)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [activityError, setActivityError] = useState('')
  const [message, setMessage] = useState('')

  useEffect(() => {
    if (!sandbox) return
    const controller = new AbortController()
    request<Portfolio>(path, 'GET', undefined, controller.signal).then(setData)
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [sandbox, live, request, revision])

  useEffect(() => { setPage(1); setHoldingPage(1) }, [account, search, kind, start, end, revision])
  useEffect(() => {
    if (!sandbox || tab !== 'activity') return
    const controller = new AbortController()
    const params = new URLSearchParams({ search, page: String(page) })
    if (account) params.set('account', account)
    if (kind) params.set('kind', kind)
    if (start) params.set('start', start)
    if (end) params.set('end', end)
    setLoading(true); setActivity(undefined); setActivityError('')
    request<ActivityPage>(path + '/activity?' + params, 'GET', undefined, controller.signal).then(setActivity)
      .catch(failure => { if (!controller.signal.aborted) setActivityError(failure.message) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [sandbox, live, request, tab, account, search, kind, start, end, page, revision])

  async function sync(activityOnly: boolean, full = false) {
    setBusy(true); setError(''); setMessage('')
    try {
      const result = await request<{ count: number }>(path + '/sync' + (activityOnly ? '?activity=true' + (full ? '&full=true' : '') : ''), 'POST')
      setMessage(`${activityOnly ? 'Activity' : 'Holdings'} synced · ${result.count} records retrieved.`)
      setRevision(value => value + 1); onChanged()
    } catch (failure) { setError((failure as Error).message) }
    finally { setBusy(false) }
  }

  if (!sandbox) return <section className="card"><h2>Investment sync starts in Sandbox</h2><p className="muted">Choose Sandbox preview in the workspace selector to view and test holdings and investment activity. Your manual accounts remain on Accounts.</p></section>
  if (!data) return <section className="card">{error ? <p className="notice" role="alert">{error}<button className="secondary small" onClick={() => { setError(''); setRevision(value => value + 1) }}>Retry</button></p> : <p role="status">Loading investments…</p>}</section>
  const accounts = data.accounts.filter(item => !account || item.id === account)
  const usd = accounts.filter(item => item.currency === 'USD')
  const complete = usd.length > 0 && usd.every(item => item.current_minor !== null)
  const total = complete ? usd.reduce((sum, item) => sum + item.current_minor!, 0) : null
  const allHoldings = data.holdings.filter(item => accounts.some(a => a.id === item.account_id))
  const holdings = allHoldings.filter(item => !search || `${item.name} ${item.ticker ?? ''}`.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => (b.value_minor ?? -Infinity) - (a.value_minor ?? -Infinity))
  const visible = holdings.slice((holdingPage - 1) * 12, holdingPage * 12)
  const exposure = new Map<string, number>()
  allHoldings.filter(item => item.currency === 'USD' && (item.value_minor ?? 0) > 0).forEach(item => {
    const name = item.cash_equivalent ? 'Cash & equivalents' : nameOf(item.type)
    exposure.set(name, (exposure.get(name) ?? 0) + item.value_minor!)
  })
  const allocation = [...exposure.entries()].sort((a, b) => b[1] - a[1])
  const allocationTotal = allocation.reduce((sum, item) => sum + item[1], 0)
  const history = data.history.map(point => {
    const selected = usd.map(item => point.accounts.find(observed => observed.account_id === item.id && observed.currency === 'USD'))
    return { date: point.date, value_minor: selected.length && selected.every(item => item?.value_minor != null) ? selected.reduce((sum, item) => sum + item!.value_minor!, 0) : null }
  })
  const format = (value: number | null, currency: string | null) => value === null || !currency ? '—' : money(value, currency)
  function price(value: number | string | null, currency: string | null) {
    if (value == null || !currency || !Number.isFinite(Number(value))) return '—'
    try { return new Intl.NumberFormat('en-US', { style: 'currency', currency, maximumFractionDigits: 8 }).format(Number(value)) }
    catch { return quantity(value) + ' ' + currency }
  }

  return <div className="investments-page">
    {openTestScenario && <div className="notice"><p>The prepared holdings demo is in the Investments test scenario. Your banking connection stays saved separately.</p><button className="secondary small" onClick={openTestScenario}>Switch to investment demo</button></div>}
    <section className="card investment-sync">
      <div className="section-heading"><div><h2>Broker-reported investments</h2><small>{managed ? "View sync times for each institution in Bank sync." : <>Holdings: {stamp(data.state?.holdings_at)} / Activity: {stamp(data.state?.activity_at)}</>}</small></div><span className="chip">{live ? 'LIVE DATA' : 'TEST MODE'}</span></div>
      <p className="muted">Sync retrieves Plaid’s latest available data, not live quotes. Holdings can update overnight. Initial activity may take up to two minutes.</p>
      <div className="investment-actions">{!managed && <><button className="primary" disabled={busy} onClick={() => void sync(false)}>Sync holdings</button><button className="secondary" disabled={busy || !data.accounts.length} onClick={() => void sync(true)}>Sync activity</button>
        </>}<button className="secondary" disabled={busy} onClick={openBank}>Manage connection</button></div>
      {busy && <p role="status">Retrieving investment data…</p>}{error && <p className="notice" role="alert">{error}</p>}{message && <p className="notice" role="status">{message}</p>}
    </section>
    <div className="filters investment-filters"><label>Investment account<select aria-label="Investment account" value={account} onChange={event => setAccount(event.target.value)}><option value="">All investment accounts</option>{data.accounts.map(item => <option key={item.id} value={item.id}>{item.name} · {nameOf(item.subtype)}</option>)}</select></label></div>
    {!data.state?.holdings_at ? <section className="card"><h2>Load your existing portfolio</h2><p className="empty">In Bank sync, choose Sync holdings first, then Sync activity for each investment institution. Existing positions load as holdings, without becoming new income or spending.</p></section> : <>
      <div className="investment-overview">
        <section className="card investment-value"><h2>Portfolio value</h2><strong className="hero-value numeric">{total === null ? '—' : money(total)}</strong><p className="muted">Saved USD account balances · cash and securities counted once</p>
          {accounts.some(item => item.currency !== 'USD' || item.current_minor === null) && <p className="notice">Other currencies and missing USD balances are excluded from this total.</p>}
          <Chart history={history} money={money} label="Recorded portfolio value" /><small>History uses saved account values, including successful balance refreshes. Investment estimates need historical positions and prices; banking estimates do not apply here. Deposits and withdrawals also change value; this is not a return calculation.</small>
          <SnapshotGuide open={openBank} />
        </section>
        <section className="card investment-allocation"><h2>Where it’s invested</h2><p className="muted">Positive USD holdings · cash included</p>
          {allocation.map(([name, value], index) => <div className="allocation-row" key={name}><div><span>{name}</span><strong className="numeric">{money(value)}</strong></div><div className="allocation-track"><span style={{ width: `${value / allocationTotal * 100}%`, background: `var(${index % 2 ? '--accent' : '--blue'})` }} /></div><small>{(value / allocationTotal * 100).toFixed(1)}%</small></div>)}
          {!allocation.length && <p className="empty">No valued USD holdings reported.</p>}<small>Missing values, other currencies, and short positions are excluded. Holdings may differ from balances because of settlement or update timing.</small>
        </section>
      </div>
      <section className="card investment-records">
        <div className="section-heading"><div className="segmented" aria-label="Investment view">{['holdings', 'activity'].map(value => <button key={value} aria-pressed={tab === value} onClick={() => setTab(value)}>{nameOf(value)}</button>)}</div><small>{tab === 'holdings' ? `${holdings.length} positions` : `${activity?.total ?? '…'} records`}</small></div>
        <div className="filters"><input type="search" aria-label="Search investments" placeholder="Find a security or activity" maxLength={120} value={search} onChange={event => setSearch(event.target.value)} />
          {tab === 'activity' && <><select aria-label="Investment activity type" value={kind} onChange={event => setKind(event.target.value)}><option value="">All activity types</option>{['buy', 'sell', 'cash', 'fee', 'transfer', 'cancel'].map(value => <option key={value} value={value}>{nameOf(value)}</option>)}</select>
            <label>From<input type="date" aria-label="Investment activity from" value={start} onChange={event => setStart(event.target.value)} /></label><label>Through<input type="date" aria-label="Investment activity through" value={end} onChange={event => setEnd(event.target.value)} /></label></>}
        </div>
        {tab === 'holdings' ? <>
          <div className="investment-holdings">{visible.map(item => <details className="investment-row" key={item.account_id + ':' + item.security_id}>
            <summary><div><strong>{item.name}</strong><small>{item.ticker ?? nameOf(item.type)} · {data.accounts.find(a => a.id === item.account_id)?.name}</small></div><div className="investment-row-value"><strong className="numeric">{format(item.value_minor, item.currency)}</strong><small>{quantity(item.quantity)} units</small></div></summary>
            <div className="investment-expanded"><dl className="investment-facts"><div><dt>Reported price</dt><dd>{price(item.price, item.currency)}</dd></div><div><dt>Price as of</dt><dd>{item.price_as_of ?? 'Not supplied'}</dd></div><div><dt>Cost basis</dt><dd>{format(item.cost_basis_minor, item.currency)}</dd></div><div><dt>Unrealized gain / loss</dt><dd>{format(item.gain_minor, item.currency)}</dd></div></dl>
              <small>Retrieved {stamp(item.retrieved_at)} · {item.currency ?? 'Currency not supplied'}. Missing fields are unavailable, not zero.</small><ProviderDetails provider={item.provider} security={item.security} /></div>
          </details>)}</div>
          {!holdings.length && <p className="empty">No holdings match this view.</p>}
          {holdings.length > 12 && <div className="pagination"><button className="secondary small" disabled={holdingPage === 1} onClick={() => setHoldingPage(value => value - 1)}>Previous</button><small>Page {holdingPage} of {Math.ceil(holdings.length / 12)}</small><button className="secondary small" disabled={holdingPage * 12 >= holdings.length} onClick={() => setHoldingPage(value => value + 1)}>Next</button></div>}
        </> : <>
          <p className="muted">Investment activity is separate from everyday spending. Positive amounts use cash; negative amounts add cash. Shares and cash changes are reported by the broker.</p>
          {loading && <p role="status">Loading activity…</p>}{activityError && <p className="notice" role="alert">{activityError}<button className="secondary small" onClick={() => setRevision(value => value + 1)}>Retry</button></p>}
          {activity?.rows.map(item => <details className="investment-row" key={item.id}><summary><div><strong>{item.name}</strong><small>{item.date} · {item.account_name} · {nameOf(item.subtype ?? item.type)}</small></div><strong className="numeric">{format(item.amount_minor, item.currency)}</strong></summary>
            <div className="investment-expanded"><dl className="investment-facts"><div><dt>Security</dt><dd>{item.security_name ?? 'Not supplied'} {item.ticker && `(${item.ticker})`}</dd></div><div><dt>Quantity</dt><dd>{quantity(item.quantity)}</dd></div><div><dt>Execution price</dt><dd>{price(item.price, item.currency)}</dd></div><div><dt>Fees</dt><dd>{format(item.fees_minor, item.currency)}</dd></div></dl><ProviderDetails provider={item.provider} security={item.security} /></div></details>)}
          {activity?.total === 0 && <p className="empty">No investment activity matches this view. Sync activity if you haven’t loaded history yet.</p>}
          {activity && activity.total > 25 && <div className="pagination"><button className="secondary small" disabled={page === 1 || loading} onClick={() => setPage(value => value - 1)}>Previous</button><small>Page {page} of {Math.ceil(activity.total / 25)}</small><button className="secondary small" disabled={page * 25 >= activity.total || loading} onClick={() => setPage(value => value + 1)}>Next</button></div>}
          <details className="investment-history-help"><summary>History coverage</summary><p className="muted">{managed ? "History coverage varies by connection. View sync details in Bank sync." : <>Loaded range: {data.state?.activity_start ?? 'Not synced'} to {data.state?.activity_end ?? 'Not synced'}. Initial sync requests up to two years of available history. Later syncs reconcile the last seven days plus time since the previous sync. Reload history to check older corrections; older positions remain visible even when their purchase history is unavailable.</>}</p>{!managed && <button className="secondary small" disabled={busy} onClick={() => void sync(true, true)}>Reload available history</button>}</details>
        </>}
      </section>
    </>}
  </div>
}
