import { Icon } from './icons'
import { FinanceMeter } from './FinanceMeter'

type Category = { id: number; name: string; bucket: string; actual_minor: number; archived?: number }
export type MonthlySummary = {
  income_minor: number
  spending_minor: number
  surplus_minor: number
  surplus_rate: number | null
  buckets: Record<string, number>
  ratios: number[]
  excluded_count: number
}

const money = (minor: number) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(minor / 100)
const buckets = [
  { key: 'needs', label: 'Needs', tone: 'accent' },
  { key: 'wants', label: 'Wants', tone: 'coral' },
  { key: 'savings', label: 'Other goal spending', tone: 'positive' },
  { key: 'uncategorized', label: 'Uncategorized', tone: 'amber' },
] as const

export function BudgetOverview({ summary, postedSummary, includePending, pendingIncome, pendingNet, categories, month, ratios, onCategory, onBreakdown, onManage }: {
  summary: MonthlySummary
  postedSummary: MonthlySummary
  includePending: boolean
  pendingIncome: number
  pendingNet: number
  categories: Category[]
  month: string
  ratios: number[]
  onCategory: (id: number) => void
  onBreakdown: () => void
  onManage: () => void
}) {
  const { income_minor: income, spending_minor: spending, surplus_minor: surplus } = summary
  const savingsTarget = Math.round(income * ratios[2] / 100)
  const rows = buckets.map(bucket => ({ ...bucket, amount: summary.buckets[bucket.key] ?? 0 }))
  const positive = rows.filter(row => row.amount > 0)
  const total = positive.reduce((sum, row) => sum + row.amount, 0)
  let offset = 0
  const gradient = positive.map(row => {
    const start = offset
    offset += row.amount / total * 100
    return `var(--${row.tone}) ${start}% ${offset}%`
  }).join(', ')

  return <div className="budget-monthly">
    <p className="pending-report-note"><strong>{includePending?'Estimated spending · posted + pending':'Posted spending only'}</strong><span>Posted spending: {money(postedSummary.spending_minor)} · Pending net spending: {money(pendingNet)}</span><small>Income and guide percentages use posted income only. Pending income {money(pendingIncome)} is provisional. Pending amounts, dates and categories may change or disappear on sync.</small></p>
    <div className="budget-summary" aria-label={'Monthly totals for ' + month}>
      <section className="budget-ready" data-over={surplus < 0 || undefined}>
        <small><Icon name="budgets" /> {surplus < 0 ? 'Spending above income' : 'Income left after spending'}</small>
        <strong className={surplus < 0 ? 'negative' : ''}>{money(Math.abs(surplus))}</strong>
        <span>{income > 0 ? `${summary.surplus_rate!.toFixed(1)}% of income remains after spending.` : 'Record income to see a percentage.'}</span>
      </section>
      <div className="budget-month-flows">
        <section><small><Icon name="income" /> Income received</small><strong>{money(income)}</strong><span>Posted income · USD</span></section>
        <section><small><Icon name="expense" /> Net spending</small><strong>{money(spending)}</strong><span>{includePending?'Posted + pending purchases minus refunds · estimate':'Posted purchases minus refunds · USD'}</span></section>
      </div>
    </div>
    <div className="budget-monthly-grid">
      <section className="card">
        <div className="section-heading"><div><h2>Monthly guide</h2><small>{ratios.join(' / ')} split for new income</small></div></div>
        {rows.slice(0, 2).map(({ key, label, amount }, index) => {
          const allowance = Math.round(income * ratios[index] / 100)
          return <div className="budget-guide" data-bucket={key} key={key}>
            <div><strong>{label}</strong><span>{ratios[index]}% guide</span></div>
            <div><strong className="numeric">{money(amount)} spent</strong><span>{income > 0 ? `${money(allowance)} guide · ${money(Math.abs(allowance - amount))} ${amount > allowance ? 'above' : 'below'}` : 'Record income to compare'}</span></div>
            <FinanceMeter label={label + ' spending compared with guide'} max={Math.max(1, allowance, amount)} value={Math.max(0, amount)} over={income > 0 && amount > allowance} />
          </div>
        })}
        <div className="budget-guide" data-bucket="savings">
          <div><strong>Savings potential</strong><span>{ratios[2]}% guide</span></div>
          <div><strong className="numeric">{money(surplus)} left</strong><span>{income > 0 ? `${money(savingsTarget)} guide · ${surplus >= savingsTarget ? 'Guide met so far' : 'Below guide so far'}` : 'Record income to compare'}</span></div>
          <FinanceMeter label="Surplus compared with savings guide" max={Math.max(1, savingsTarget, surplus)} value={Math.max(0, surplus)} />
        </div>
        {(summary.buckets.uncategorized !== 0 || summary.excluded_count > 0) &&
          <p className="notice">{money(summary.buckets.uncategorized)} uncategorized net spending. {summary.excluded_count} entries excluded (pending income or needs review{includePending?'':' / pending spending'}).</p>}
        <details className="metric-note"><summary>How the guide works</summary>
          <p>The guide uses the month’s posted USD income. Spending follows the pending-preview switch; combined spending and surplus are estimates. Income left after spending measures potential savings. Actual savings deposits, principal payments, and investment transfers can change cash separately. Existing savings and funded assignments are separate.</p>
        </details>
      </section>
      <section className="card budget-distribution">
        <div className="section-heading"><h2>Where spending went</h2><button className="secondary small" onClick={onBreakdown}>Explore spending</button></div>
        <div className="budget-donut">
          <div className="budget-ring budget-monthly-ring" role="img" aria-label="Spending by budget bucket"
            style={{ background: gradient ? `conic-gradient(${gradient})` : 'var(--line)' }}>
            <div><small>Net spending</small><strong>{money(spending)}</strong></div>
          </div>
          <div className="budget-chart-legend">{rows.map(row => <div className="budget-legend-row" key={row.key}>
            <i style={{ background: `var(--${row.tone})` }} /><span>{row.label}</span><strong>{money(row.amount)}</strong>
          </div>)}</div>
        </div>
        <details className="metric-note"><summary>What the ring includes</summary>
          <p>The ring shows buckets with positive net spending. Net refunds still count in the total and legend. Savings allocations and transfers are excluded.</p>
        </details>
      </section>
    </div>
    <section className="card">
      <div className="section-heading"><div><h2>Budget categories</h2><small>Open a category to edit its bucket or view its transactions.</small></div>
        <button className="secondary small" onClick={onManage}>Manage categories</button>
      </div>
      {buckets.slice(0, 3).map(({ key, label }) => {
        const members = categories.filter(category => category.id && category.bucket === key && (!category.archived || category.actual_minor))
        return <details className="budget-monthly-categories" data-bucket={key} key={key}>
          <summary>{key === 'savings' ? 'Savings & extra debt categories' : label} · {members.length} {members.length === 1 ? 'category' : 'categories'}</summary>
          {members.map(category => <button type="button" className="budget-comparison" key={category.id} onClick={() => onCategory(category.id)}>
            <span>{category.name}{category.archived ? ' (Archived)' : ''}</span><strong>{money(category.actual_minor)} {includePending?'estimated spending':'spent'}</strong>
          </button>)}
        </details>
      })}
    </section>
  </div>
}
