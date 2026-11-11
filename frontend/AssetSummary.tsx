import { useState } from 'react'
import type { Account } from './AccountList'
import { accountGroup, accountLabel } from './accounts'

export function AssetSummary({ accounts, loading, money, openAccounts }: {
  accounts: Account[]; loading: boolean; money: (amount: number, currency?: string) => string; openAccounts: () => void
}) {
  const [side, setSide] = useState('assets')
  const known = accounts.filter(account => account.balance_minor !== null)
  const included = known.filter(account => side === 'assets' ? account.balance_minor! >= 0 : account.balance_minor! < 0)
  const grouped = new Map<string, number>()
  for (const account of included) grouped.set(accountGroup(account), (grouped.get(accountGroup(account)) ?? 0) + Math.abs(account.balance_minor!))
  const groups = [...grouped].sort((a, b) => b[1] - a[1])
  const total = groups.reduce((sum, [, amount]) => sum + amount, 0)
  return <section className="card asset-summary">
    <div className="section-heading"><div><h2>Where it sits</h2><small>Follows your net worth account selection · USD</small></div><button className="secondary small" onClick={openAccounts}>Accounts →</button></div>
    <div className="segmented" aria-label="Account composition">{['assets', 'debt'].map(value => <button key={value} aria-pressed={side === value} onClick={() => setSide(value)}>{value === 'assets' ? 'Assets' : 'Debt'}</button>)}</div>
    {loading ? <p className="muted">Loading selected accounts…</p> : <>
      <strong className="composition-total numeric">{money(total)}</strong>
      {known.length < accounts.length && <p className="muted">Mix uses known balances only; {accounts.length - known.length} selected balances are unavailable.</p>}
      {groups.length > 0 ? <>
        <div className="composition-bar" role="img" aria-label={side + ' composition'}>{groups.map(([label, amount], index) => <span key={label} className={'mix-' + index % 6} style={{ flexGrow: amount }}><span className="sr-only">{label}: {money(amount)}</span></span>)}</div>
        <div className="composition-legend">{groups.map(([label, amount], index) => <div key={label}><span><i className={'mix-' + index % 6} />{label}</span><strong className="numeric">{money(amount)} <small>{total ? (amount / total * 100).toFixed(1) : '0.0'}%</small></strong></div>)}</div>
      </> : <p className="empty">{accounts.length ? 'No ' + side + ' balances in this selection.' : 'Select accounts in the net worth chart to see their composition.'}</p>}
      {included.length > 0 && <details className="asset-table-details"><summary>Account balances · {included.length}</summary><div className="asset-table-scroll"><table className="asset-table"><thead><tr><th>Account</th><th>Balance</th></tr></thead><tbody>
        {included.map(account => <tr key={account.id}><td><strong>{account.name}</strong><small>{accountLabel(account)} · {account.balance_date?.slice(0, 10) ?? 'Date unavailable'}</small></td><td className="numeric">{money(account.balance_minor!)}</td></tr>)}
      </tbody></table></div><p className="analytics-note">Saved observations, not live quotes. Open Accounts for source and freshness details.</p></details>}
    </>}
  </section>
}
