import { useEffect, useState } from 'react'

type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>
type Account = { id: string; name: string; mask: string | null; type: string; currency: string | null; current_minor: number | null; source: 'cached' | 'fresh' | null; checked_at: string | null }
type Transaction = { id: string; date: string; name: string; amount_minor: number; currency: string; kind: string; pending: number; account_name: string }
type Status = {
  configured: boolean
  pending_session: string | null
  connection: { synced_at: string | null; history_status: string | null; needs_reauth: number } | null
  accounts: Account[]
  transactions: Transaction[]
}
type Session = { session: string; url: string }
const path = '/plaid/sandbox'

export function BankSync({ request, money, onChanged }: { request: Request; money: (value: number, currency?: string) => string; onChanged: () => void }) {
  const [status, setStatus] = useState<Status>()
  const [session, setSession] = useState<Session>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    request<Status>(path, 'GET', undefined, controller.signal)
      .then(setStatus)
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [request])

  async function action(work: () => Promise<void>) {
    setBusy(true)
    setError('')
    setMessage('')
    try { await work() }
    catch (failure) {
      setError((failure as Error).message)
      await request<Status>(path).then(setStatus).catch(() => {})
    }
    finally { setBusy(false) }
  }

  const pending = session?.session ?? status?.pending_session

  function checkConnection() {
    if (!pending) return
    void action(async () => {
      const result = await request<{ status: string }>(path + '/link/' + pending, 'POST')
      if (result.status === 'pending') {
        setMessage('Finish authorization in the Plaid tab, then check again.')
        return
      }
      setSession(undefined)
      setStatus(await request<Status>(path))
      setMessage(result.status === 'connected' ? 'Connected. Select Sync now to fetch test data.' : 'The Plaid session ended. Start a new test session.')
    })
  }

  function sync(fresh: boolean, details = false) {
    void action(async () => {
      const result = await request<{ added?: number; modified?: number; removed?: number }>(
        path + '/sync' + (fresh ? '?fresh=true' : details ? '?details=true' : ''), 'POST')
      setStatus(await request<Status>(path))
      onChanged()
      setMessage(fresh ? 'Balance check complete. Each row shows its latest available observation.'
        : 'Sync complete. ' + result.added + ' added, ' + result.modified + ' updated, ' + result.removed + ' removed.')
    })
  }

  const connection = status?.connection
  return <>
    <section className="card">
      <div className="section-heading"><div><h2>Plaid Sandbox</h2><small>Test data is stored separately from your personal ledger.</small></div><span className="chip">TEST MODE</span></div>
      {!status && !error && <p role="status" className="muted">Loading bank sync…</p>}
      {status && !status.configured && <p className="notice">Set your local Plaid Sandbox credentials to connect. See the project README for setup.</p>}
      {error && <p role="alert" className="notice">{error}</p>}
      {message && <p role="status" className="notice">{message}</p>}
      {connection && <p className="muted">{connection.needs_reauth ? 'Reauthorization required.'
        : connection.synced_at ? 'Last transaction sync: ' + new Date(connection.synced_at).toLocaleString()
        : 'Connected. Transaction sync has not run.'}{connection.history_status === 'INITIAL_UPDATE_COMPLETE'
        ? ' Historical data is still loading.' : ''}</p>}
      {status?.configured && <div className="inline-actions bank-actions">
        {!pending && (!connection || connection.needs_reauth) &&
          <button className="primary" disabled={busy} onClick={() => void action(async () => {
            setSession(await request<Session>(path + '/link' + (connection ? '?reconnect=true' : ''), 'POST'))
          })}>{connection ? 'Reconnect test bank' : 'Connect a test bank'}</button>}
        {pending && <>
          {session && <a href={session.url} className="bank-link" target="_blank" rel="noopener noreferrer">Open Plaid Sandbox ↗</a>}
          <button className="primary" disabled={busy} onClick={checkConnection}>Check connection</button>
          <button className="secondary" disabled={busy} onClick={() => void action(async () => {
            await request(path + '/link', 'DELETE')
            setSession(undefined)
            setStatus(await request<Status>(path))
          })}>Cancel</button>
        </>}
        {connection && !connection.needs_reauth && !pending && <>
          <button className="primary" disabled={busy} onClick={() => sync(false)}>Sync now</button>
          <button className="secondary" disabled={busy} onClick={() => sync(true)}>Refresh balances</button>
          <button className="secondary" disabled={busy} onClick={() => sync(false, true)}>Refresh transaction details</button>
          <button className="secondary" disabled={busy} onClick={() => void action(async () => {
            setSession(await request<Session>(path + '/link?reconnect=true', 'POST'))
          })}>Manage test accounts</button>
        </>}
        {busy && <span role="status" className="muted">Working…</span>}
      </div>}
    </section>
    {status && status.accounts.length > 0 && <section className="card stack">
      <div className="section-heading"><div><h2>Test account balances</h2><small>{status.accounts.length} accounts · See Accounts for groups and charts.</small></div></div>
      <div className="sync-scroll">{status.accounts.map(account => <div className="history-row" key={account.id}>
        <div><strong>{account.name}{account.mask && ' • ' + account.mask}</strong><br />
          <small>{account.source === 'fresh' ? 'Fresh when checked' : account.source === 'cached' ? 'Cached by Plaid' : 'Balance unavailable'}
            {account.checked_at && ' · checked ' + new Date(account.checked_at).toLocaleString()}</small></div>
        <strong className="numeric">{account.current_minor === null ? '—' : money(account.current_minor, account.currency ?? undefined)}</strong>
      </div>)}</div>
    </section>}
    {status && status.transactions.length > 0 && <section className="card stack">
      <div className="section-heading"><div><h2>Recent test transactions</h2><small>Latest 50 entries</small></div></div>
      <div className="sync-scroll">{status.transactions.map(tx => <div className="history-row" key={tx.id}>
        <div><strong>{tx.name}</strong><br /><small>{tx.account_name} · {tx.date} · {tx.kind}{tx.pending && ' · Pending' || ''}</small></div>
        <strong className="numeric">{money(-tx.amount_minor, tx.currency)}</strong>
      </div>)}</div>
    </section>}
  </>
}

