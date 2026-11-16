import { useEffect, useState, type ReactNode } from 'react'

type Estimate = { id: number; name: string; supported: boolean; reason: string | null; anchor_date: string | null;
  anchor_at: string | null; anchor_source: string | null; start_date: string | null; transaction_count: number;
  newer_transaction_count: number; max_difference_minor: number | null; points: { date: string; balance_minor: number }[] }
type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>

export function BankingHistory({ ids, measure, request, money, revision, renderChart }: {
  ids: number[]; measure: string; request: Request; money: (value: number) => string; revision: number;
  renderChart: (points: { date: string; value_minor: number | null }[], title: string) => ReactNode
}) {
  const [data, setData] = useState<{ accounts: Estimate[] }>()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setData(undefined); setError('')
    request<{ accounts: Estimate[] }>('/plaid/sandbox/preview/balance-history', 'GET', undefined, controller.signal)
      .then(value => { if (!controller.signal.aborted) setData(value) })
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [request, revision, attempt])
  const selected = data?.accounts.filter(account => ids.includes(account.id)) ?? []
  const supported = selected.filter(account => account.supported)
  const excluded = selected.filter(account => !account.supported)
  const dates = [...new Set(supported.flatMap(account => account.points.map(point => point.date)))].sort()
  const balances = supported.map(account => new Map(account.points.map(point => [point.date, point.balance_minor])))
  const points = dates.map(date => ({ date, value_minor: supported.length && balances.every(values => values.has(date))
    ? balances.reduce((sum, values) => { const value = values.get(date)!; return sum + (measure === 'assets' ? Math.max(value, 0) : measure === 'debt' ? Math.max(-value, 0) : value) }, 0) : null }))
  const complete = points.filter(point => point.value_minor !== null)
  const title = measure === 'assets' ? 'Estimated banking assets' : measure === 'debt' ? 'Estimated banking debt' : 'Estimated net banking balance'
  return <section className="banking-history" aria-label="Estimated banking history">
    <div className="section-heading"><div><small className="eyebrow">BANKING ONLY · ESTIMATED</small><h3>{title}</h3></div><span className="chip">{supported.length} of {ids.length} selected accounts</span></div>
    <p className="analytics-note">This chart covers supported banking accounts only. The net worth total above still includes your full account selection. Investments, loans and unsupported accounts are excluded here.</p>
    {error && <p className="notice" role="alert">{error} <button className="secondary small" onClick={() => setAttempt(value => value + 1)}>Retry history</button></p>}
    {!data && !error && <p className="muted" role="status">Calculating saved banking estimates…</p>}
    {data && <>
      {complete.length > 0 ? <><p className="estimate-latest"><span>Latest common estimate · {complete.at(-1)!.date}</span><strong className="numeric">{money(complete.at(-1)!.value_minor!)}</strong></p>
        <p className="analytics-note">Common coverage: {complete[0].date} → {complete.at(-1)!.date}. Only dates shared by every included banking account are shown. Select fewer accounts for a longer individual history.</p>
        {renderChart(complete, title)}
      </> : <p className="empty">{supported.length ? 'Selected banking accounts have no overlapping history. Select fewer accounts to view their available estimate.' : 'No selected accounts have enough supported banking data. See coverage below; sync transactions and refresh balances as needed.'}</p>}
      <p className="notice">Estimates work backward from saved balances using posted transactions, including transfers and card payments. Pending entries are excluded. Missing transactions, cached or intraday balances, and posting delays can shift the result. No history is estimated before the first available transaction or beyond its balance anchor.</p>
      <details className="history-coverage"><summary>Banking estimate coverage · {selected.length} accounts</summary><div className="history-coverage-list">{selected.map(account => <div key={account.id}>
        <strong>{account.name}</strong>{account.supported ? <><p>{account.start_date} → {account.anchor_date} · {account.transaction_count} posted entries · {account.anchor_source === 'fresh' ? 'Fresh when fetched' : 'Cached balance anchor'}</p>
          <small>Balance anchor fetched {account.anchor_at ? new Date(account.anchor_at).toLocaleString() : 'at an unknown time'}. Completeness of imported history is not guaranteed.</small>
          {account.newer_transaction_count > 0 && <p className="notice">{account.newer_transaction_count} newer posted entries are beyond this anchor. Refresh balances to extend the estimate.</p>}
          {!!account.max_difference_minor && <p className="notice">Differs from an earlier recorded balance by up to {money(account.max_difference_minor)}. Use snapshots for the observed value.</p>}
        </> : <p>{account.reason}</p>}</div>)}</div></details>
    </>}
  </section>
}

export function SnapshotGuide({ manual = false, open }: { manual?: boolean; open: () => void }) {
  return <details className="snapshot-guide"><summary>Snapshots, estimates &amp; adding history</summary>
    <p><strong>Snapshots</strong> are saved account values on recorded dates. Solid lines connect consecutive recorded days; missing or incomplete days leave gaps. The latest observation per account is used for each day. A saved provider balance can still be cached; see Accounts for its source and update time.</p>
    <p><strong>Banking estimates</strong> reconstruct supported checking, savings and credit-card values from transactions. Dashed lines are approximate and cover banking accounts only. They do not change snapshots, current balances or your ledger.</p>
    <p>{manual ? 'For manual accounts, open Accounts and Record balance with the correct date. Dated manual balances appear in the account history; net-worth snapshots are recorded when you save. Record balances for every included account when you want a complete daily total.' : 'Open Bank sync and use Refresh all, or Refresh balances for every institution. Successful balance fetches save observations. Sync holdings can also save investment balance observations when those balances update. A failed refresh adds no fresh observation for that account.'}</p>
    <p>Repeat on another day to add another daily point. Same-day updates use the latest value for that day. Refreshing the browser or launching the app does not fetch banks; there is no automatic daily sync. Missing days stay empty. Portfolio value includes deposits and withdrawals and is not an investment-return calculation.</p>
    <button className="secondary small" onClick={open}>{manual ? 'Open Accounts' : 'Open Bank sync'}</button>
  </details>
}
