export type AccountSync = { timestamps: Record<string, string | null>; errors: Record<string, { code: string; attempted_at: string }>; needs_reauth: boolean; connection_error?: string | null }
const names: Record<string, string> = { balances: 'Balance', transactions: 'Transactions', holdings: 'Holdings', activity: 'Activity' }
function age(stamp: string | null, now: number) {
  if (!stamp || !Number.isFinite(Date.parse(stamp))) return 'Not fetched'
  const minutes = Math.max(0, Math.floor((now - Date.parse(stamp)) / 60_000))
  return minutes < 1 ? 'just now' : minutes < 60 ? `${minutes}m ago` : minutes < 1440 ? `${Math.floor(minutes / 60)}h ago` : `${Math.floor(minutes / 1440)}d ago`
}

export function AccountFreshness({ sync, now, compact = false, cached = false }: { sync: AccountSync | null | undefined; now: number; compact?: boolean; cached?: boolean }) {
  if (!sync) return null
  const failed = Object.keys(sync.errors).length > 0 || !!sync.connection_error
  const old = Object.values(sync.timestamps).some(stamp => !stamp || !Number.isFinite(Date.parse(stamp)) || now - Date.parse(stamp) >= 86_400_000)
  if (compact) return <span className="account-freshness" data-help="These are last successful fetch times for this account or its institution, not the latest transaction date or live market price. Open the account for exact times and failed attempts. A reminder appears after 24 hours without a fetch.">
    <span>{Object.entries(sync.timestamps).map(([product, stamp]) => `${names[product]} ${age(stamp, now)}${product === 'balances' && cached ? ' (cached)' : ''}`).join(' · ')}</span>
    {(sync.needs_reauth || failed || old) && <span className="sync-warning">{sync.needs_reauth ? 'Reconnect needed' : failed ? 'Last refresh had errors' : 'Needs refresh'}</span>}
  </span>
  return <section className="account-sync-details"><h3>Data updates</h3>{sync.needs_reauth && <p className="sync-warning">Reconnect this institution before syncing.</p>}{sync.connection_error && <p className="sync-warning">Last institution request: {sync.connection_error}. Check Bank sync for details.</p>}<dl className="debt-facts">{Object.entries(sync.timestamps).map(([product, stamp]) => <div key={product}><dt>{names[product]} {product === 'balances' ? cached ? 'saved from cache' : 'checked' : 'fetched'}</dt><dd>
    {stamp ? <><time dateTime={stamp}>{new Date(stamp).toLocaleString()}</time><small>{age(stamp, now)}</small></> : 'Not fetched yet'}
    {sync.errors[product] && <p className="sync-warning">Last attempt failed: {sync.errors[product].code} · {new Date(sync.errors[product].attempted_at).toLocaleString()}. Saved data retained.</p>}
  </dd></div>)}</dl><p className="muted">Transaction, holdings and activity fetch times apply to this institution. A recent fetch does not guarantee new bank activity or live prices. “Needs refresh” is a reminder after 24 hours or when a product has not been fetched.</p></section>
}
