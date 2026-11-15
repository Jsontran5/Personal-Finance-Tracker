import { useEffect, useId, useRef, useState } from 'react'
import { groupedAccounts, type GroupAccount } from './accounts'

type Money = (value: number, currency?: string) => string
type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>
export type ChartPoint = { date: string; value_minor: number | null }
export type HistoryPoint = {
  date: string
  net_worth_minor: number
  accounts: { account_id: number; balance_minor: number | null }[]
}
type Account = GroupAccount & { id: number; name: string; currency: string; include_in_net_worth: number; balance_minor: number | null }

export function Chart({ history, money, currency = 'USD', label = 'Recorded net worth' }: {
  history: ChartPoint[]; money: Money; currency?: string; label?: string
}) {
  const [range, setRange] = useState(0)
  const gradient = useId().replaceAll(':', '')
  const cutoff = new Date()
  cutoff.setHours(0, 0, 0, 0)
  const day = cutoff.getDate()
  cutoff.setDate(1)
  cutoff.setMonth(cutoff.getMonth() - range)
  const lastDay = new Date(cutoff.getFullYear(), cutoff.getMonth() + 1, 0).getDate()
  cutoff.setDate(Math.min(day, lastDay))
  const points = history.filter(point => !range || new Date(point.date + 'T00:00:00') >= cutoff)
  const values = points.flatMap(point => point.value_minor === null ? [] : [point.value_minor])
  const low = Math.min(...values)
  const high = Math.max(...values)
  const firstDate = Date.parse(points[0]?.date ?? '')
  const lastDate = Date.parse(points.at(-1)?.date ?? '')
  const segments: { x: number; y: number; point: ChartPoint }[][] = []
  let current: typeof segments[number] = []
  for (const point of points) {
    if (point.value_minor === null) {
      if (current.length) segments.push(current)
      current = []
      continue
    }
    current.push({
      x: lastDate > firstDate ? 12 + 576 * (Date.parse(point.date) - firstDate) / (lastDate - firstDate) : 300,
      y: high === low ? 80 : 142 - 124 * (point.value_minor - low) / (high - low),
      point,
    })
  }
  if (current.length) segments.push(current)
  const missing = points.filter(point => point.value_minor === null).length
  return <div className="balance-chart">
    <div className="range" aria-label={label + ' period'}>
      {[[1, '1M'], [3, '3M'], [6, '6M'], [12, '1Y'], [0, 'All']].map(([value, text]) =>
        <button key={value} aria-pressed={range === value} onClick={() => setRange(Number(value))}>{text}</button>)}
    </div>
    {values.length > 0 ? <>
      <div className="plot">
        <div className="chart-axis" aria-hidden="true">
          {high === low ? <span>{money(high, currency)}</span>
            : [high, Math.round((high + low) / 2), low].map((value, index) => <span key={index}>{money(value, currency)}</span>)}
        </div>
        <svg className="chart" viewBox="0 0 600 160" preserveAspectRatio="none" role="img" aria-label={label + ' from ' + points[0].date + ' to ' + points.at(-1)!.date}>
          <defs><linearGradient id={gradient} x2="0" y2="1"><stop stopColor="currentColor" stopOpacity=".26" /><stop offset="1" stopColor="currentColor" stopOpacity="0" /></linearGradient></defs>
          {[18, 80, 142].map(y => <line key={y} x1="12" x2="588" y1={y} y2={y} className="chart-grid" />)}
          {segments.map((segment, index) => {
            const line = segment.map(({ x, y }) => x + ',' + y).join(' ')
            return <g key={index} data-chart-segment>
              {segment.length > 1 && <>
                <polygon points={line + ' ' + segment.at(-1)!.x + ',150 ' + segment[0].x + ',150'} fill={'url(#' + gradient + ')'} />
                <polyline points={line} className="chart-line" />
              </>}
              {segment.map(({ x, y, point }) => <circle key={point.date} cx={x} cy={y} r={3.5} className="chart-point" tabIndex={0}>
                <title>{point.date} · {money(point.value_minor!, currency)}</title>
              </circle>)}
            </g>
          })}
        </svg>
      </div>
      <div className="chart-caption"><span>{points[0].date}</span><span>Recorded balances · {currency}</span><span>{points.length > 1 && points.at(-1)!.date}</span></div>
    </> : <p className="empty">No complete recorded balances in this period.</p>}
    {missing > 0 && <p className="muted chart-gap-note">{missing} snapshot dates have incomplete data for this selection. Lines stop at those gaps.</p>}
  </div>
}

export function NetWorth({ accounts, history, mode, request, money, onStart, onSelectionChange }: {
  accounts: Account[]; history: HistoryPoint[]; mode: 'personal' | 'sandbox'; request: Request; money: Money; onStart: () => void;
  onSelectionChange: (ids: number[] | null) => void
}) {
  const [chartMode, setChartMode] = useState('net')
  const [excluded, setExcluded] = useState<number[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [search, setSearch] = useState('')
  const picker = useRef<HTMLDetailsElement>(null)
  useEffect(() => {
    const closeOutside = (event: PointerEvent) => {
      if (picker.current && !picker.current.contains(event.target as Node)) picker.current.open = false
    }
    document.addEventListener('pointerdown', closeOutside)
    return () => document.removeEventListener('pointerdown', closeOutside)
  }, [])
  const eligible = accounts.filter(account => account.include_in_net_worth && account.currency === 'USD')
  const selected = eligible.filter(account => !excluded?.includes(account.id))
  const path = '/chart-selection/' + mode
  useEffect(() => {
    onSelectionChange(excluded === null ? null : accounts.filter(account => account.include_in_net_worth && account.currency === 'USD' && !excluded.includes(account.id)).map(account => account.id))
  }, [accounts, excluded, onSelectionChange])

  useEffect(() => {
    const controller = new AbortController()
    request<{ excluded_account_ids: number[] }>(path, 'GET', undefined, controller.signal)
      .then(result => { setExcluded(result.excluded_account_ids); setError('') })
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [request, path, attempt])

  async function saveSelection(next: number[]) {
    setBusy(true)
    setError('')
    try {
      const saved = await request<{ excluded_account_ids: number[] }>(path, 'PUT', { excluded_account_ids: next })
      setExcluded(saved.excluded_account_ids)
    } catch (failure) { setError((failure as Error).message) }
    finally { setBusy(false) }
  }

  const complete = selected.length > 0 && selected.every(account => account.balance_minor !== null)
  const assets = selected.reduce((sum, account) => sum + Math.max(account.balance_minor ?? 0, 0), 0)
  const liabilities = selected.reduce((sum, account) => sum - Math.min(account.balance_minor ?? 0, 0), 0)
  const points = history.map(snapshot => {
    const balances = new Map(snapshot.accounts.map(account => [account.account_id, account.balance_minor]))
    return {
      date: snapshot.date,
      value_minor: selected.every(account => balances.get(account.id) != null)
        ? selected.reduce((sum, account) => {
          const value = balances.get(account.id)!
          return sum + (chartMode === 'assets' ? Math.max(value, 0) : chartMode === 'debt' ? Math.max(-value, 0) : value)
        }, 0) : null,
    }
  })

  return <section className="card hero" aria-labelledby="net-worth-title">
    <div className="hero-header">
      <div><h2 id="net-worth-title">{chartMode === 'net' ? 'Net worth' : chartMode === 'assets' ? 'Assets' : 'Debt'}</h2>
        <strong className="hero-value numeric">{excluded !== null && complete ? money(chartMode === 'assets' ? assets : chartMode === 'debt' ? liabilities : assets - liabilities) : '—'}</strong>
      </div>
      <span className="chip">USD · {mode === 'sandbox' ? 'Sandbox balances' : 'Manual balances'}</span>
    </div>
    <div className="segmented net-worth-modes" aria-label="Balance chart measure">{[['net', 'Net worth'], ['assets', 'Assets'], ['debt', 'Debt']].map(([value, label]) => <button key={value} aria-pressed={chartMode === value} onClick={() => setChartMode(value)}>{label}</button>)}</div>
    {error && <p className="notice" role="alert">{error}{excluded === null && <button className="secondary small" onClick={() => setAttempt(value => value + 1)}>Retry</button>}</p>}
    {eligible.length > 0 && excluded !== null && <details ref={picker} className="account-picker" onKeyDown={event => {
      if (event.key === 'Escape') { event.currentTarget.open = false; event.currentTarget.querySelector('summary')?.focus() }
    }}>
      <summary>Accounts · {selected.length} of {eligible.length}</summary>
      <div className="account-options">
        <div className="picker-toolbar"><input type="search" aria-label="Search chart accounts" placeholder="Find an account" value={search} onChange={event => setSearch(event.target.value)} />
          <button className="secondary small" disabled={busy} onClick={() => void saveSelection([])}>Select all</button>
          <button className="secondary small" disabled={busy} onClick={() => void saveSelection(eligible.map(account => account.id))}>Clear</button></div>
        <div className="picker-scroll">
        {groupedAccounts(eligible).map(group => {
          const visible = group.accounts.filter(account => (account.name + ' ' + (account.institution_name ?? '')).toLowerCase().includes(search.toLowerCase()))
          if (!visible.length) return null
          const ids = group.accounts.map(account => account.id)
          const count = group.accounts.filter(account => !excluded.includes(account.id)).length
          return <fieldset key={group.name} className="picker-group">
            <legend><label className="checkbox"><input type="checkbox" disabled={busy} checked={count === ids.length}
              ref={input => { if (input) input.indeterminate = count > 0 && count < ids.length }}
              onChange={event => void saveSelection(event.target.checked ? excluded.filter(id => !ids.includes(id)) : excluded.concat(ids.filter(id => !excluded.includes(id))))} />
              {group.name} <span className="subtle">{count}/{ids.length}</span></label></legend>
            {visible.map(account => <label className="checkbox" key={account.id}>
              <input type="checkbox" checked={!excluded.includes(account.id)} disabled={busy} onChange={event =>
                void saveSelection(event.target.checked ? excluded.filter(id => id !== account.id) : [...excluded, account.id])} />{account.name}{account.institution_name && ` / ${account.institution_name}`}
            </label>)}
          </fieldset>
        })}
        {!eligible.some(account => (account.name + ' ' + (account.institution_name ?? '')).toLowerCase().includes(search.toLowerCase())) && <p className="muted">No matching accounts.</p>}
        </div>
        <button className="secondary small picker-done" onClick={() => { if (picker.current) { picker.current.open = false; picker.current.querySelector('summary')?.focus() } }}>Done</button>
      </div>
    </details>}
    {excluded === null && !error && <p className="muted">Loading chart selection…</p>}
    {excluded !== null && eligible.length === 0 && <div className="welcome">
      <h2>{accounts.length ? 'No eligible USD accounts' : 'Start with an account'}</h2>
      <p>{mode === 'sandbox' ? 'Sync a test bank to start recording balances.' : 'Add an included USD account to start tracking net worth.'}</p>
      <button className="primary" onClick={onStart}>{mode === 'sandbox' ? 'Open Bank sync' : 'Add your first account'}</button>
    </div>}
    {excluded !== null && eligible.length > 0 && selected.length === 0 && <p className="empty">Select an account to view net worth.</p>}
    {excluded !== null && selected.length > 0 && <Chart history={points} money={money} label={'Recorded ' + (chartMode === 'net' ? 'net worth' : chartMode)} />}
    {excluded !== null && selected.length > 0 && points.filter(point => point.value_minor !== null).length === 1 && <p className="muted chart-gap-note">One recorded date so far. Trends appear as more balances are recorded.</p>}
    {selected.length > 0 && !complete && <p className="muted">A selected account has no current balance. Totals are incomplete.</p>}
    <div className="balance-summary">
      <div><span>Total assets</span><strong className="numeric">{excluded !== null && complete ? money(assets) : '—'}</strong></div>
      <div><span>Total liabilities</span><strong className="numeric">{excluded !== null && complete ? money(liabilities) : '—'}</strong></div>
    </div>
  </section>
}
