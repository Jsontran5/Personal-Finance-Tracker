import { trapDialogFocus } from './dialogFocus'
import { useEffect, useRef, useState } from 'react'
import { Icon } from './icons'

export type Page = 'Overview' | 'Spending' | 'Transactions' | 'Budgets' | 'Accounts' | 'Bank sync' | 'Backup & restore' | 'Investments'
const destinations = [
  ['Overview', 'overview'], ['Spending', 'expense'], ['Transactions', 'transactions'],
  ['Budgets', 'budgets'], ['Accounts', 'accounts'], ['Investments', 'logo'],
  ['Bank sync', 'connection'], ['Backup & restore', 'backup'],
] as const
const primary: Page[] = ['Overview', 'Spending', 'Transactions', 'Accounts']

export function Navigation({ page, navigate }: { page: Page; navigate: (page: Page) => void }) {
  const [more, setMore] = useState(false)
  const sheet = useRef<HTMLDialogElement>(null)
  useEffect(() => { if (more) sheet.current?.showModal() }, [more])
  const go = (destination: Page) => { setMore(false); navigate(destination) }
  function item([name, icon]: typeof destinations[number]) {
    return <button key={name} type="button" aria-current={page === name ? 'page' : undefined} onClick={() => go(name)}><Icon name={icon} /><span>{name}</span></button>
  }
  return <>
    <nav className="desktop-navigation" aria-label="Finance sections">
      <div className="nav-group"><span className="nav-caption">Your finances</span>{destinations.slice(0, 6).map(item)}</div>
      <div className="nav-group"><span className="nav-caption">Manage</span>{destinations.slice(6).map(item)}</div>
    </nav>
    <nav className="mobile-navigation" aria-label="Mobile finance sections">
      {primary.map(name => item(destinations.find(destination => destination[0] === name)!))}
      <button type="button" aria-label="More sections" aria-haspopup="dialog" aria-expanded={more} aria-current={!primary.includes(page) ? 'page' : undefined} onClick={() => setMore(true)}><Icon name="more" /><span>More</span></button>
    </nav>
    {more && <dialog onKeyDown={trapDialogFocus} className="navigation-sheet" ref={sheet} aria-labelledby="navigation-title" onCancel={event => { event.preventDefault(); setMore(false) }}>
      <div className="dialog-heading"><h2 id="navigation-title">More sections</h2><button className="icon" type="button" aria-label="Close navigation" onClick={() => setMore(false)}>×</button></div>
      <nav aria-label="More finance sections">{destinations.filter(([name]) => !primary.includes(name)).map(item)}</nav>
    </dialog>}
  </>
}
