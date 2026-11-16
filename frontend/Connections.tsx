import { useEffect, useRef, useState } from 'react'
import { LiveReadiness } from './LiveReadiness'

type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>
type Connection = { id: number; institution_id: string; name: string; profile: 'banking' | 'investments'; investments_enabled?: boolean; investment_consent_pending?: boolean; connected: number; account_count: number;
  needs_reauth: number; last_error: string | null; synced_at: string | null; history_status?: string | null; balance_checked_at: string | null;
  holdings_at: string | null; activity_at: string | null; liabilities_at: string | null; session: string | null; url: string | null }
type Status = { configured: boolean; linking_enabled?: boolean; connections: Connection[]; manual_accounts: { name: string }[] }
type ManualAccount = { id: number; name: string; kind: string; selected: boolean }
const time = (value: string | null) => value ? new Date(value).toLocaleString() : 'Not synced'

export function Connections({ request, onChanged, live = false }: { live?: boolean; request: Request; onChanged: () => void }) {
  const base = live ? '/plaid/live' : '/plaid/combined-sandbox'
  const addPanel = useRef<HTMLDetailsElement>(null)
  const [status, setStatus] = useState<Status>()
  const [manual, setManual] = useState<ManualAccount[]>([])
  const [selected, setSelected] = useState<number[]>([])
  const [query, setQuery] = useState('')
  const [profile, setProfile] = useState('banking')
  const [includeInvestments, setIncludeInvestments] = useState(false)
  const [results, setResults] = useState<{ id: string; name: string }[]>()
  const [filter, setFilter] = useState('')
  const [page, setPage] = useState(0)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    request<Status>(base, 'GET', undefined, controller.signal).then(setStatus).catch(failure => { if (!controller.signal.aborted) setError(failure.message) })
    request<{ accounts: ManualAccount[] }>(base + '/manual', 'GET', undefined, controller.signal).then(result => {
      setManual(result.accounts); setSelected(result.accounts.filter(account => account.selected).map(account => account.id))
    }).catch(failure => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [request, base])

  async function action(label: string, work: () => Promise<string>) {
    setBusy(label); setError(''); setMessage('')
    try { setMessage(await work()); setStatus(await request<Status>(base)); onChanged() }
    catch (failure) { setError((failure as Error).message); await request<Status>(base).then(setStatus).catch(() => {}) }
    finally { setBusy('') }
  }
  const matching = status?.connections.filter(item => item.name.toLowerCase().includes(filter.toLowerCase())) ?? []
  const currentPage = Math.min(page, Math.max(0, Math.ceil(matching.length / 6) - 1))
  function sync(connection: Connection, product: string, full = false) {
    void action(connection.name + ' · ' + product, async () => {
      const result = await request<{ count: number }>(`${base}/connections/${connection.id}/sync?product=${product}${full ? '&full=true' : ''}`, 'POST')
      return `${connection.name}: ${product} synced · ${result.count} records retrieved.`
    })
  }

  return <div className="connections-page">
    {live && <LiveReadiness request={request} revision={status?.connections.length} onFindInstitution={() => {
      if (addPanel.current) {
        addPanel.current.open = true
        addPanel.current.scrollIntoView({ block: 'start' })
        addPanel.current.querySelector<HTMLInputElement>('input[aria-label="Find institution"]')?.focus({ preventScroll: true })
      }
    }} />}
    <section className="card"><div className="section-heading"><div><h2>Connected institutions</h2><small>{live ? 'Live workspace' : 'Combined Sandbox'} · each connection keeps its own authorization and sync state</small></div><span className="chip">{live ? 'LIVE DATA' : 'TEST MODE'}</span></div>
      <p className="muted">Accounts, spending, investments and net worth share this workspace. Reconnect an existing institution to repair access or select more accounts.</p>
      {!live && <button className="secondary" disabled={!!busy || !status?.configured} onClick={() => void action('Import saved scenarios', async () => {
        const result = await request<{ imported: number }>(base + '/import-scenarios', 'POST')
        return `${result.imported} saved connections copied into Combined. Existing scenarios and Items are unchanged.`
      })}>Import saved Sandbox connections</button>}
      {status && !status.configured && <p className="notice">Set local {live ? 'Production' : 'Sandbox'} credentials before connecting or syncing. Saved reports remain available.</p>}
    </section>
    {busy && <p className="muted" role="status">{busy}…</p>}{error && <p className="notice" role="alert">{error}</p>}{message && <p className="notice" role="status">{message}</p>}
    <section className="card"><details className="connection-add" ref={addPanel}><summary>Add another institution</summary>
      <p className="muted">This single-user app allows one connection per institution. Existing entries use Reconnect; adding the same login again can consume another connection slot.</p>
      <form className="filters" onSubmit={event => { event.preventDefault(); void action('Searching institutions', async () => {
        const found = await request<{ institutions: { id: string; name: string }[] }>(base + '/institutions?' + new URLSearchParams({ query, profile, include_investments: String(live && profile === 'banking' && includeInvestments) }))
        setResults(found.institutions); return `${found.institutions.length} institutions found.`
      }) }}><label>Connection type<select aria-label="Connection type" value={profile} disabled={!!busy} onChange={event => { setProfile(event.target.value); setResults(undefined) }}><option value="banking">Banking transactions</option><option value="investments">Investments</option></select></label>
        {live && profile === 'banking' && <label className="checkbox"><input type="checkbox" checked={includeInvestments} disabled={!!busy} onChange={event => { setIncludeInvestments(event.target.checked); setResults(undefined) }} />Also connect investments</label>}
        <label>Institution name<input aria-label="Find institution" value={query} minLength={2} maxLength={100} required disabled={!!busy} onChange={event => setQuery(event.target.value)} /></label><button className="primary" disabled={!!busy || !status?.configured}>Search institutions</button></form>
      <div className="connection-results">{results?.slice(0, 8).map(item => {
        const existing = status?.connections.some(connection => connection.institution_id === item.id)
        return <div className="connection-result" key={item.id}><strong>{item.name}</strong><button className="secondary small" disabled={!!busy || existing} onClick={() => void action('Adding institution', async () => {
          if (live) await request(base + '/preflight', 'POST', { institution_id: item.id, profile, include_investments: includeInvestments })
          await request(base + '/connections', 'POST', { institution_id: item.id, profile, include_investments: includeInvestments }); setResults(undefined); return live ? 'Institution added. Coverage checked; connect from its saved entry below.' : 'Institution added. Start its Link session below and choose this same institution inside Plaid.'
        })}>{existing ? 'Already listed' : 'Add institution'}</button></div>
      })}</div>{results && !results.length && <p className="empty">No institutions match. Try another name or connection type.</p>}
      {(results?.length ?? 0) > 8 && <p className="muted">First eight matches shown. Refine the institution name.</p>}
    </details></section>
    <section className="connections-list" aria-label="Saved institutions">
      <div className="filters"><input type="search" aria-label="Filter connections" placeholder="Find a saved institution" value={filter} onChange={event => { setFilter(event.target.value); setPage(0) }} /></div>
      {!status && !error && <p role="status">Loading connections…</p>}
      {status && !matching.length && <p className="empty">{status.connections.length ? 'No connections match this filter.' : live ? 'No institutions added yet. Use Find your bank above to get started.' : 'Import your saved Sandbox connections, or add an institution.'}</p>}
      {matching.slice(currentPage * 6, (currentPage + 1) * 6).map(connection => <article className="card connection-card" key={connection.id}>
        <div className="section-heading"><div><h3>{connection.name}</h3><small>{connection.profile === 'investments' ? 'Investments' : connection.investments_enabled ? 'Banking + investments' : 'Banking'} · {connection.account_count} accounts</small></div><span className="chip">{connection.needs_reauth ? 'Reconnect needed' : connection.connected ? 'Connected' : 'Setup needed'}</span></div>
        {connection.last_error && <p className="notice">Last request: {connection.last_error}. Saved data is retained.</p>}
        <div className="connection-actions">
          {connection.session ? <><a className="bank-link" href={connection.url ?? undefined} target="_blank" rel="noopener noreferrer">Open saved Plaid session ↗</a>
            <button className="primary small" disabled={!!busy} onClick={() => void action('Checking connection', async () => {
              const result = await request<{ status: string }>(`${base}/connections/${connection.id}/link/${connection.session}`, 'POST')
              return result.status === 'connected' ? `${connection.name} connected. Sync its data below.` : result.status === 'pending' ? 'Finish the saved Plaid session, then check again.' : 'Session ended. You can start another session for this institution.'
            })}>Check connection</button><button className="secondary small" disabled={!!busy} onClick={() => void action('Cancelling session', async () => {
              await request(`${base}/connections/${connection.id}/link`, 'DELETE'); return 'Link session cancelled. Existing authorization is retained.'
            })}>Cancel Link</button></> : <button className="secondary small" disabled={!!busy || (live && !status?.linking_enabled)} onClick={() => void action('Opening Link', async () => {
              await request(`${base}/connections/${connection.id}/link`, 'POST'); return 'Open the saved Plaid session below. Select the same institution shown on this card.'
            })}>{connection.connected ? 'Reconnect / manage accounts' : live ? status?.linking_enabled ? 'Connect institution' : 'Connect institution (locked)' : 'Connect test institution'}</button>}
          {!!connection.connected && !connection.needs_reauth && <>
            {connection.profile === 'banking' ? <button className="primary small" disabled={!!busy} onClick={() => sync(connection, 'transactions')}>Sync transactions</button> : <><button className="primary small" disabled={!!busy || !!connection.investment_consent_pending} onClick={() => sync(connection, 'holdings')}>Sync holdings</button><button className="secondary small" disabled={!!busy || !!connection.investment_consent_pending} onClick={() => sync(connection, 'activity')}>Sync activity</button></>}
            {live && connection.profile === 'banking' && connection.investments_enabled && !connection.investment_consent_pending && <><button className="secondary small" disabled={!!busy || !!connection.investment_consent_pending} onClick={() => sync(connection, 'holdings')}>Sync holdings</button><button className="secondary small" disabled={!!busy || !!connection.investment_consent_pending} onClick={() => sync(connection, 'activity')}>Sync activity</button></>}
            <button className="secondary small" disabled={!!busy} onClick={() => sync(connection, 'balances')}>Refresh balances</button>
          </>}
        </div>
        {connection.investment_consent_pending && <p className="notice">Investment access is pending. Complete Connect or Reconnect / manage accounts, then sync holdings first and activity second.</p>}
        <details className="connection-details"><summary>Sync details and additional data</summary><dl className="debt-facts">{[['Transactions', connection.synced_at], ['Balances', connection.balance_checked_at], ['Holdings', connection.holdings_at], ['Investment activity', connection.activity_at], ['Debt terms', connection.liabilities_at]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{time(value)}</dd></div>)}</dl>
          {connection.history_status && <p className="muted">Transaction history: {connection.history_status.replaceAll('_', ' ').toLowerCase()}. Initial history can arrive in stages; sync again after 30 seconds if still preparing.</p>}
          {!!connection.connected && !connection.needs_reauth && <div className="connection-actions">
            {live && connection.profile === 'banking' && !connection.investments_enabled && <button className="secondary small" disabled={!!busy} onClick={() => void action('Preparing investment access', async () => {
              await request(base + '/preflight', 'POST', { institution_id: connection.institution_id, profile: 'investments' })
              const result = await request<{ status: string }>(`${base}/connections/${connection.id}/enable-investments`, 'POST')
              return result.status
            })}>Enable investments on this connection</button>}
            {!live && connection.profile === 'banking' && <><button className="secondary small" disabled={!!busy || !!connection.investment_consent_pending} onClick={() => sync(connection, 'holdings')}>Sync holdings</button><button className="secondary small" disabled={!!busy || !!connection.investment_consent_pending} onClick={() => sync(connection, 'activity')}>Sync activity</button><button className="secondary small" disabled={!!busy} onClick={() => sync(connection, 'transactions', true)}>Reload transaction history</button></>}
            {live && connection.investments_enabled && !connection.investment_consent_pending && <button className="secondary small" disabled={!!busy} onClick={() => sync(connection, 'activity', true)}>Reload investment history</button>}
            {!live && <><button className="secondary small" disabled={!!busy} onClick={() => sync(connection, 'liabilities')}>Sync debt details</button>
            <button className="secondary small" disabled={!!busy} onClick={() => sync(connection, 'activity', true)}>Reload investment history</button></>}
          </div>}<small>Product support varies by institution and consent. Syncing one connection does not reset another.</small>
        </details>
      </article>)}
      {matching.length > 6 && <div className="pagination"><button className="secondary small" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>Previous connections</button><small>Page {currentPage + 1} of {Math.ceil(matching.length / 6)}</small><button className="secondary small" disabled={(currentPage + 1) * 6 >= matching.length} onClick={() => setPage(currentPage + 1)}>Next connections</button></div>}
    </section>
    <section className="card"><details className="manual-selection"><summary>Include accounts from Manual</summary><p className="muted">Choose only accounts that are not already connected through Plaid. Selected balances and transactions appear here as read-only mirrors; edit them in Manual. Updates are reflected when this workspace reloads.</p>
      <div className="manual-selection-list">{manual.map(account => <label className="checkbox" key={account.id}><input type="checkbox" checked={selected.includes(account.id)} disabled={!!busy} onChange={event => setSelected(value => event.target.checked ? [...value, account.id] : value.filter(id => id !== account.id))} />{account.name}</label>)}</div>
      {!manual.length && <p className="empty">No Manual accounts yet. Add a manual account, such as Nelnet, in Manual first.</p>}
      <button className="secondary small" disabled={!!busy} onClick={() => void action('Saving manual selection', async () => {
        await request(base + '/manual', 'PUT', { account_ids: selected }); return 'Manual account selection saved. Manual records are unchanged.'
      })}>Include selected manual accounts</button>
    </details></section>
  </div>
}
