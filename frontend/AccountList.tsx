import { useState, type ReactNode } from 'react'
import { accountLabel, accountGroups, groupedAccounts, type GroupAccount } from './accounts'

export type Account = GroupAccount & {
  id: number; name: string; kind: string; currency: string; balance_minor: number | null;
  balance_date: string | null; include_in_net_worth: number; balance_source?: string; transaction_count?: number
}

export function AccountList({ accounts, sandbox, primary, money, add, details }: {
  accounts: Account[]; sandbox: boolean; primary: number | null;
  money: (amount: number, currency?: string) => string; add: () => void; details: (account: Account) => ReactNode
}) {
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('')
  const [expanded, setExpanded] = useState<string[]>(['Cash & savings', 'Credit cards'])
  const groups = groupedAccounts(accounts)
  const matching = accounts.filter(account => (!filter || groups.find(group => group.name === filter)?.accounts.includes(account)) &&
    (account.name + ' ' + accountLabel(account)).toLowerCase().includes(search.trim().toLowerCase()))
  return <section className="accounts-workspace">
    <div className="section-heading"><div><h2>{sandbox ? 'Sandbox accounts' : 'Manual accounts'}</h2><small>{accounts.length} accounts · Latest saved balances</small></div>
      {!sandbox && <button className="primary" onClick={add}>＋ Add account</button>}</div>
    <div className="filters">
      <input type="search" aria-label="Search accounts" placeholder="Find an account" value={search} onChange={event => setSearch(event.target.value)} />
      <select aria-label="Account group" value={filter} onChange={event => setFilter(event.target.value)}><option value="">All groups</option>
        {accountGroups.filter(name => groups.some(group => group.name === name)).map(name => <option key={name}>{name}</option>)}
      </select>
    </div>
    {accounts.some(account => account.currency !== 'USD') && <p className="muted">Currencies are shown separately; no conversion is applied.</p>}
    {!matching.length && <p className="empty">{accounts.length ? 'No accounts match your search.' : 'No accounts yet.'}</p>}
    <div className="account-groups">
      {groupedAccounts(matching).map(group => {
        const totals = new Map<string, number | null>()
        for (const account of group.accounts) {
          const previous = totals.get(account.currency)
          totals.set(account.currency, account.balance_minor === null || previous === null ? null : (previous ?? 0) + account.balance_minor)
        }
        return <details className="card account-group" key={group.name} open={!!search || !!filter || expanded.includes(group.name)}
          onToggle={event => {
            if (search || filter) return
            const isOpen = event.currentTarget.open
            setExpanded(value => isOpen ? value.includes(group.name) ? value : [...value, group.name] : value.filter(name => name !== group.name))
          }}>
          <summary className="group-summary"><span><strong>{group.name}</strong><small>{group.accounts.length} {group.accounts.length === 1 ? 'account' : 'accounts'}</small></span>
            <span className="group-totals numeric">{[...totals].map(([currency, amount]) => <span key={currency}>{amount === null ? 'Incomplete · ' + currency : money(amount, currency)}</span>)}</span>
          </summary>
          <div className="group-accounts">{group.accounts.map(account => <details name="account-inspector" className="compact-account" key={account.id}>
            <summary className="account-summary"><span className="account-identity"><strong>{account.name}</strong><small>{accountLabel(account)} · {account.currency}
              {account.id === primary && ' · Primary'}{!account.include_in_net_worth && ' · Excluded'}</small></span>
              <span className={'numeric ' + ((account.balance_minor ?? 0) < 0 ? 'negative' : '')}>{account.balance_minor === null ? '—' : money(account.balance_minor, account.currency)}</span>
            </summary>
            <div className="account-expanded">
              <p className="muted">{sandbox
                ? (account.balance_source === 'fresh' ? 'Fresh when checked' : account.balance_source === 'cached' ? 'Cached by Plaid' : 'Balance unavailable') +
                  (account.balance_date ? ' · Checked ' + new Date(account.balance_date).toLocaleString() : '')
                : 'Recorded ' + account.balance_date + ' · Includes later ledger activity'}</p>
              {sandbox && <p className="muted">{account.transaction_count ? account.transaction_count + ' saved transactions across all dates.' : 'No spending transactions saved for this account.'}
                {account.provider_type === 'investment' ? ' See Investments for holdings and investment activity.'
                  : account.provider_type === 'loan' || account.provider_type === 'credit' ? ' Expand Debt details for terms and reported payment information.' : ''}</p>}
              {details(account)}
            </div>
          </details>)}</div>
        </details>
      })}
    </div>
  </section>
}
