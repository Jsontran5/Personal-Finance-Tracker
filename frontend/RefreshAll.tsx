import { useRef, useState } from 'react'

type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>
type Connection = { id: number; name: string; profile: string; connected: number; needs_reauth: number;
  investments_enabled: boolean; investment_consent_pending: boolean; synced_at: string | null; balance_checked_at: string | null; holdings_at: string | null; activity_at: string | null }
type Result = { institution: string; product: string; status: 'Updated' | 'Failed' | 'Skipped'; message: string }
const columns = { transactions: 'synced_at', holdings: 'holdings_at', activity: 'activity_at', balances: 'balance_checked_at' } as const
type Product = keyof typeof columns

export function RefreshAll({ request, visible, onChanged }: { request: Request; visible: boolean; onChanged: () => void }) {
  const running = useRef(false)
  const [busy, setBusy] = useState('')
  const [results, setResults] = useState<Result[] | null>(null)
  const [error, setError] = useState('')
  async function refresh() {
    if (running.current) return
    running.current = true; setBusy('Preparing refresh…'); setResults([]); setError('')
    const rows: Result[] = []
    try {
      const data = await request<{ connections: Connection[] }>('/plaid/live')
      for (const connection of data.connections.filter(item => item.connected)) {
        const products: Product[] = connection.profile === 'banking' ? ['transactions'] : []
        if (connection.investments_enabled) products.push('holdings', 'activity')
        products.push('balances')
        for (const product of products) {
          const last = connection[columns[product]]
          const skip = connection.needs_reauth ? 'Reconnect this institution first.'
            : connection.investment_consent_pending && (product === 'holdings' || product === 'activity') ? 'Investment consent is pending; reconnect first.'
              : last && Date.now() - Date.parse(last) < 30_000 ? 'Already fetched within the 30-second cooldown.' : null
          if (skip) rows.push({ institution: connection.name, product, status: 'Skipped', message: skip })
          else {
            setBusy(`${connection.name} · ${product}`)
            try {
              const result = await request<{ count: number }>(`/plaid/live/connections/${connection.id}/sync?product=${product}`, 'POST')
              rows.push({ institution: connection.name, product, status: 'Updated', message: `${result.count} records retrieved.` })
            } catch (failure) {
              const message = (failure as Error).message
              rows.push({ institution: connection.name, product, status: /Wait 30 seconds/.test(message) ? 'Skipped' : 'Failed', message })
            }
          }
          setResults([...rows]); onChanged()
        }
      }
      if (!rows.length) setError('No connected Live institutions to refresh. Add and authorize an institution in Bank sync first.')
    } catch (failure) { setError((failure as Error).message) }
    finally { running.current = false; setBusy(''); onChanged() }
  }
  const failed = results?.filter(row => row.status === 'Failed').length ?? 0
  return <div className="refresh-all" hidden={!visible && !busy}>
    <button type="button" className="secondary" disabled={!!busy} onClick={() => void refresh()}>{busy ? 'Refreshing…' : 'Refresh all'}</button>
    {busy && <p className="muted" role="status">Fetching {busy}. You can keep browsing; leave the app open until it finishes.</p>}
    {error && <p className="notice" role="alert">{error}</p>}
    {!busy && !!results?.length && <p className="muted" role="status">Live refresh finished{failed ? ` with ${failed} failed ${failed === 1 ? "action" : "actions"}. Saved data is retained for those products.` : ". See results for updated and skipped products."}</p>}
    {!!results?.length && <details className="refresh-results">
      <summary>{busy ? 'Refresh progress' : 'Refresh finished'} · {results.filter(row => row.status === 'Updated').length} updated · {failed} failed · {results.filter(row => row.status === 'Skipped').length} skipped</summary>
      <p className="muted">Each product is fetched separately. Available broker data may lag; this does not request live quotes or create bank connections.</p>
      <ul>{results.map((row, index) => <li key={index}><strong>{row.institution} · {row.product}</strong><span className={row.status === 'Failed' ? 'sync-warning' : 'muted'}>{row.status} — {row.message}</span></li>)}</ul>
    </details>}
  </div>
}
