import { trapDialogFocus } from './dialogFocus'
import { AccountFreshness, type AccountSync } from './AccountFreshness'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { accountLabel, accountGroups, groupedAccounts, type GroupAccount } from './accounts'
import { Icon } from './icons'

const groupIcons = ['accounts', 'logo', 'plus', 'card', 'transactions', 'overview'] as const

export type Account = GroupAccount & {
  id: number; manual_id?: number | null; sync?: AccountSync | null; name: string; provider_name?: string | null; nickname?: string | null; kind: string; currency: string; balance_minor: number | null;
  balance_date: string | null; include_in_net_worth: number; balance_source?: string; transaction_count?: number; institution_name?: string | null
}

function AccountInspector({ account, open, close, money, children }: { account: Account; open: boolean; close: () => void; money: (value: number, currency?: string) => string; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const panel = ref.current
    if (open && !panel?.open) panel?.showModal()
    if (!open && panel?.open) panel.close()
  }, [open])
  return <dialog onKeyDown={trapDialogFocus} className="inspector account-inspector" ref={ref} aria-labelledby={'account-title-' + account.id} onCancel={event => { event.preventDefault(); close() }}>
    <div className="dialog-heading"><h2 id={'account-title-' + account.id}>{account.name}</h2><button className="icon" type="button" aria-label="Close account details" onClick={close}>×</button></div>
    <div className="account-inspector-value"><small>{accountLabel(account)} · {account.institution_name ?? 'Manual'} · {account.currency}</small><strong className="numeric">{account.balance_minor == null ? '—' : money(account.balance_minor, account.currency)}</strong></div>
    {children}
  </dialog>
}

export function AccountList({ accounts, sandbox, live, unified = false, primary, money, add, details }: {
  accounts: Account[]; sandbox: boolean; live?: boolean; unified?: boolean; primary: number | null;
  money: (amount: number, currency?: string) => string; add: () => void; details: (account: Account) => ReactNode
}) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 60_000); return () => clearInterval(timer) }, [])
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('')
  const [expanded, setExpanded] = useState<string[]>(['Cash & savings', 'Credit cards'])
  const [selected, setSelected] = useState<number | null>(null)
  const groups = groupedAccounts(accounts)
  const matching = accounts.filter(account => (!filter || groups.find(group => group.name === filter)?.accounts.includes(account)) &&
    (account.name + ' ' + (account.institution_name ?? '') + ' ' + accountLabel(account)).toLowerCase().includes(search.trim().toLowerCase()))
  return <section className="accounts-workspace">
    <div className="section-heading"><div><h2>{unified ? 'All accounts' : live ? 'Live accounts' : sandbox ? 'Sandbox accounts' : 'Manual accounts'}</h2><small>{accounts.length} accounts · Latest saved balances</small></div>
      {(!sandbox || unified) && <button className="primary" onClick={add}>{unified ? 'Add manual account' : '＋ Add account'}</button>}</div>
    <div className="account-browser">
    <aside className="account-directory" aria-label="Filter accounts">
      <h3>Find an account</h3>
      <label>Search<input type="search" aria-label="Search accounts" placeholder="Name or bank" value={search} onChange={event => setSearch(event.target.value)} /></label>
      <label>Group<select aria-label="Account group" value={filter} onChange={event => setFilter(event.target.value)}><option value="">All groups</option>
        {accountGroups.filter(name => groups.some(group => group.name === name)).map(name => <option key={name}>{name}</option>)}
      </select></label>
      <p className="muted" role="status">{matching.length} of {accounts.length} accounts</p>
      {accounts.some(account => account.currency !== 'USD') && <p className="muted">Balances are shown in their original currencies.</p>}
    </aside>
    <div className="account-groups">
      {!matching.length && <p className="empty">{accounts.length ? 'No accounts match your search.' : 'No accounts yet.'}</p>}
      {groupedAccounts(matching).map(group => {
        const groupIndex = accountGroups.indexOf(group.name)
        const totals = new Map<string, number | null>()
        for (const account of group.accounts) {
          const previous = totals.get(account.currency)
          totals.set(account.currency, account.balance_minor === null || previous === null ? null : (previous ?? 0) + account.balance_minor)
        }
        return <details className="card account-group" data-tone={groupIndex} key={group.name} open={!!search || !!filter || expanded.includes(group.name)}
          onToggle={event => {
            if (search || filter) return
            const isOpen = event.currentTarget.open
            setExpanded(value => isOpen ? value.includes(group.name) ? value : [...value, group.name] : value.filter(name => name !== group.name))
          }}>
          <summary className="group-summary"><span className="group-heading"><span className="group-icon"><Icon name={groupIcons[groupIndex]} /></span><span><strong role="heading" aria-level={2}>{group.name}</strong><small className="group-count">{group.accounts.length} {group.accounts.length === 1 ? 'account' : 'accounts'}</small></span></span>
            <span className="group-totals numeric"><small>Group total</small>{[...totals].map(([currency, amount]) => <span key={currency}>{amount === null ? 'Incomplete · ' + currency : money(amount, currency)}</span>)}</span>
            <span className="group-toggle" aria-hidden="true">›</span>
          </summary>
          <div className="group-accounts">{group.accounts.map(account => <details name="account-inspector" className="compact-account" key={account.id} open={selected === account.id} onToggle={event => { if (event.target === event.currentTarget) { const isOpen = event.currentTarget.open; setSelected(value => isOpen ? account.id : value === account.id ? null : value) } }}>
            <summary className="account-summary"><span className="account-identity"><strong>{account.name}</strong><small>{accountLabel(account)}{account.institution_name && ` / ${account.institution_name}`} · {account.currency}
              {account.id === primary && ' · Primary'}{!account.include_in_net_worth && ' · Excluded'}</small><AccountFreshness sync={account.sync} now={now} compact cached={account.balance_source === 'cached'} /></span>
              <span className={'numeric ' + ((account.balance_minor ?? 0) < 0 ? 'negative' : '')}>{account.balance_minor === null ? '—' : money(account.balance_minor, account.currency)}</span><span className="account-row-arrow" aria-hidden="true">›</span>
            </summary>
            <AccountInspector account={account} open={selected === account.id} close={() => setSelected(null)} money={money}><div className="account-expanded">
              <p className="muted">{sandbox
                ? (account.balance_source === 'manual' ? unified ? 'Manually maintained' : 'Mirrored from Manual' : account.balance_source === 'fresh' ? 'Fresh when checked' : account.balance_source === 'cached' ? 'Cached by Plaid' : 'Balance unavailable') +
                  (account.balance_date ? ' · Checked ' + new Date(account.balance_date).toLocaleString() : '')
                : 'Recorded ' + account.balance_date + ' · Includes later ledger activity'}</p>
              {sandbox && <p className="muted">{account.transaction_count ? account.transaction_count + ' saved transactions across all dates.' : 'No spending transactions saved for this account.'}
                {account.provider_type === 'investment' ? ' See Investments for holdings and investment activity.'
                  : account.provider_type === 'loan' || account.provider_type === 'credit' ? ' Expand Debt details for terms and reported payment information.' : ''}</p>}
              <AccountFreshness sync={account.sync} now={now} cached={account.balance_source === 'cached'} />
              {details(account)}
            </div></AccountInspector>
          </details>)}</div>
        </details>
      })}
    </div>
    </div>
  </section>
}
