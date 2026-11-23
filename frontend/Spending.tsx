import { useEffect, useId, useState, type ReactNode } from 'react'
import { ViewSelector } from './ViewSelector'

export type Bucket = { key: string; label: string; purchases_minor: number; refunds_minor: number; net_minor: number; transaction_ids: number[]; purchase_transaction_ids: number[] }
type Day = { day: number; date: string; purchases_minor: number; refunds_minor: number; net_minor: number | null; cumulative_minor: number | null; transaction_ids: number[] }
export type SpendingReport = {
  measure?: string; kind_ids?: Record<string, number[]>; income_minor: number; spending_minor: number;
  month: string; days: number; through_day: number; comparison_month: string | null; comparison_days: number;
  purchases_minor: number; refunds_minor: number; net_minor: number; average_minor: number | null;
  previous_minor: number | null; change_minor: number | null; change_percent: number | null;
  daily: Day[]; comparison_daily: { day: number; cumulative_minor: number }[]; eligible_ids: number[];
  categories: Bucket[]; bank_categories: Bucket[]; merchants: Bucket[]
}
type Money = (amount: number, currency?: string) => string
type Drill = (ids: number[], label: string) => void
type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>
export type ReportFilters = { measure: string; accounts: number[]; category: string; comparison: string }
export const defaultReportFilters: ReportFilters = { measure: 'spending', accounts: [], category: 'all', comparison: '' }
const reportLabel = (report: SpendingReport) => report.measure === 'income' ? 'Income' : report.measure === 'cashflow' ? 'Cash flow' : 'Spending'
export const monthLabel = (month: string) => new Date(month + '-02T12:00:00').toLocaleDateString('en-US', { month: 'long', year: 'numeric' })

function MonthNavigation({ month, change }: { month: string; change: (month: string) => void }) {
  function shift(offset: number) {
    const index = Number(month.slice(0, 4)) * 12 + Number(month.slice(5)) - 1 + offset
    change(String(Math.floor(index / 12)).padStart(4, '0') + '-' + String(index % 12 + 1).padStart(2, '0'))
  }
  return <div className="inline-actions month-navigation"><button className="secondary small" disabled={month === '0001-01'} aria-label="Previous spending month" onClick={() => shift(-1)}>‹</button>
    <span>{monthLabel(month)}</span><button className="secondary small" disabled={month === '9999-12'} aria-label="Next spending month" onClick={() => shift(1)}>›</button></div>
}

export function SpendingCalendar({ report, money, drill, changeMonth }: { report: SpendingReport; money: Money; drill: Drill; changeMonth: (month: string) => void }) {
  const first = new Date(report.month + '-01T12:00:00').getDay()
  const maximum = Math.max(1, ...report.daily.map(day => Math.abs(day.net_minor ?? 0)))
  const today = new Date().toLocaleDateString('en-CA')
  return <div className="spending-calendar">
    <MonthNavigation month={report.month} change={changeMonth} />
    <div className="calendar-grid">
      {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map(label => <span className="calendar-weekday" key={label} aria-hidden="true">{label}</span>)}
      {Array.from({ length: first }, (_, index) => <span key={'blank' + index} />)}
      {report.daily.map(day => {
        const level = day.net_minor === null ? 0 : Math.ceil(Math.abs(day.net_minor) / maximum * 4)
        const amount = day.net_minor === null ? '—' : new Intl.NumberFormat('en-US', {
          style: 'currency', currency: 'USD', notation: Math.abs(day.net_minor) >= 100000 ? 'compact' : 'standard', maximumFractionDigits: 0,
        }).format(day.net_minor / 100)
        return <button key={day.date} disabled={day.net_minor === null} className={'calendar-day heat-' + level + (day.net_minor !== null && day.net_minor < 0 ? ' refund-day' : '')}
          title={day.date + ' · ' + (day.net_minor === null ? 'No actual total' : money(day.net_minor))}
          aria-current={day.date === today ? 'date' : undefined} aria-label={day.date + ': ' + (day.net_minor === null ? 'Future date' : money(day.net_minor) + ' ' + reportLabel(report).toLowerCase() + ', ' + day.transaction_ids.length + ' transactions')}
          onClick={() => drill(day.transaction_ids, reportLabel(report) + ' on ' + day.date + ' · ' + money(day.net_minor!))}>
          <span>{day.day}</span><strong className="numeric">{amount}</strong>
        </button>
      })}
    </div>
    <div className="calendar-key"><span>Less</span>{[0, 1, 2, 3, 4].map(level => <i key={level} className={'heat-' + level} />)}<span>More</span>{report.measure !== 'income' && <span className="refund-key">{report.measure === 'cashflow' ? 'Red = net outflow' : 'Green = net refunds'}</span>}</div>
  </div>
}

function SpendingTrend({ report, money }: { report: SpendingReport; money: Money }) {
  const gradient = useId().replaceAll(':', '')
  const current = report.daily.filter(day => day.cumulative_minor !== null).map(day => ({ day: day.day, value: day.cumulative_minor! }))
  const previous = report.comparison_daily.map(day => ({ day: day.day, value: day.cumulative_minor }))
  const values = [...current, ...previous].map(point => point.value)
  const high = Math.max(0, ...values), low = Math.min(0, ...values), span = high - low || 1
  const last = Math.max(report.days, report.comparison_days)
  const x = (day: number) => 14 + (day - 1) / Math.max(1, last - 1) * 572
  const y = (value: number) => 175 - (value - low) / span * 150
  const line = (points: typeof current) => points.map(point => `${x(point.day)},${y(point.value)}`).join(' ')
  return <div className="spending-trend">
    {report.eligible_ids.length || previous.length ? <>
      <div className="plot"><div className="chart-axis"><span>{money(high)}</span><span>{money(Math.round((high + low) / 2))}</span><span>{money(low)}</span></div>
        <svg viewBox="0 0 600 200" className="spending-svg" preserveAspectRatio="none" role="img" aria-label={'Cumulative posted ' + reportLabel(report).toLowerCase() + ' by day of month; dashed line is the comparison month'}>
          <defs><linearGradient id={gradient} x2="0" y2="1"><stop stopColor="currentColor" stopOpacity=".22" /><stop offset="1" stopColor="currentColor" stopOpacity="0" /></linearGradient></defs>
          {[25, 100, 175].map(value => <line key={value} x1="14" x2="586" y1={value} y2={value} className="chart-grid" />)}
          {previous.length > 0 && <polyline points={line(previous)} className="comparison-line" />}
          {current.length > 0 && <>
            <polygon points={`${x(1)},${y(0)} ${line(current)} ${x(current.at(-1)!.day)},${y(0)}`} fill={'url(#' + gradient + ')'} />
            <polyline points={line(current)} className="chart-line" />
            {current.map(point => <circle key={point.day} cx={x(point.day)} cy={y(point.value)} r="3" className="chart-point" tabIndex={0}><title>Day {point.day}: {money(point.value)}</title></circle>)}
          </>}
        </svg></div>
      <div className="trend-axis"><span>Day 1</span><span>Day {Math.round(last / 2)}</span><span>Day {last}</span></div>
      <div className="trend-legend"><span>● {monthLabel(report.month)}</span>{report.comparison_month && previous.length > 0 && <span>┄ {monthLabel(report.comparison_month)}</span>}</div>
    </> : <p className="empty">No eligible posted {reportLabel(report).toLowerCase()} in this period. Try another month or clear filters.</p>}
  </div>
}

function Breakdown({ title, rows, money, drill, children }: { title: string; rows: Bucket[]; money: Money; drill: Drill; children?: ReactNode }) {
  const maximum = Math.max(1, ...rows.map(row => Math.abs(row.net_minor)))
  return <section className="card breakdown-card"><div className="section-heading"><h2>{title}</h2>{children}</div>
    {!rows.length ? <p className="empty">No eligible posted activity to break down.</p> : <div className="breakdown-scroll">{rows.map(row => <button className="breakdown-row" key={row.key} onClick={() => drill(row.transaction_ids, title + ': ' + row.label)}>
      <span className="breakdown-heading"><strong>{row.label}</strong><strong className={'numeric ' + (row.net_minor < 0 ? 'positive' : '')}>{money(row.net_minor)}</strong></span>
      <span className="breakdown-track"><span className={row.net_minor < 0 ? 'refund-bar' : ''} style={{ width: Math.abs(row.net_minor) / maximum * 100 + '%' }} /></span>
      <small>{row.transaction_ids.length} {row.transaction_ids.length === 1 ? 'transaction' : 'transactions'}{row.refunds_minor > 0 && ' · ' + money(row.refunds_minor) + ' refunded'}</small>
    </button>)}</div>}
  </section>
}

const palette = ['#3697a2', '#72a15e', '#5585bd', '#d68b30', '#cb7059', '#6594a3', '#a09539', '#a57258']

function CategoryDonut({ rows, money, drill, income }: { rows: Bucket[]; money: Money; drill: Drill; income: boolean }) {
  const positive = rows.filter(row => row.purchases_minor > 0)
  const total = positive.reduce((sum, row) => sum + row.purchases_minor, 0)
  let offset = 0
  return <div className="category-donut">
    {total > 0 ? <>
      <div className="donut-plot">
        <svg viewBox="0 0 160 160" role="group" aria-label={(income ? 'Income' : 'Purchases') + ' by category'}>
          <circle cx="80" cy="80" r="60" fill="none" stroke="var(--line)" strokeWidth="19" />
          {positive.map((row, index) => {
            const start = offset, length = row.purchases_minor / total * 100
            offset += length
            return <circle key={row.key} className="donut-slice" cx="80" cy="80" r="60" fill="none" stroke={palette[index % palette.length]}
              strokeWidth="19" pathLength="100" strokeDasharray={`${length} ${100 - length}`} strokeDashoffset={-start}
              transform="rotate(-90 80 80)" role="button" tabIndex={0}
              aria-label={`${row.label}: ${money(row.purchases_minor)}, ${(length).toFixed(1)} percent. View transactions`}
              onClick={() => drill(row.purchase_transaction_ids, row.label + ' · ' + (income ? 'Income' : 'Purchases'))}
              onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); drill(row.purchase_transaction_ids, row.label + ' · ' + (income ? 'Income' : 'Purchases')) } }}>
              <title>{row.label}: {money(row.purchases_minor)}</title>
            </circle>
          })}
        </svg>
        <div className="donut-total"><small>{income ? 'Income' : 'Purchases'}</small><strong className="numeric">{money(total)}</strong></div>
      </div>
      <div className="donut-legend">{positive.map((row, index) => <button className="donut-legend-row" key={row.key}
        onClick={() => drill(row.purchase_transaction_ids, row.label + ' · ' + (income ? 'Income' : 'Purchases'))}>
        <i style={{ background: palette[index % palette.length] }} /><span>{row.label}</span>
        <strong className="numeric">{money(row.purchases_minor)}<small>{(row.purchases_minor / total * 100).toFixed(1)}%</small></strong>
      </button>)}</div>
    </> : <p className="empty">No {income ? 'income' : 'purchases'} for this selection.</p>}
    {!income && <p className="analytics-note">Slices show purchases before refunds. Refunds and net spending are shown separately; category totals below include both.</p>}
  </div>
}

export function SpendingPage({ month, money, drill, changeMonth, sandbox, request, path, accounts, categories, filters, setFilters, revision, transactions, renderTransactions }: {
  month: string; money: Money; drill: Drill; changeMonth: (month: string) => void; sandbox: boolean; request: Request; path: string;
  accounts: { id: number; name: string; institution_name?: string | null }[]; categories: { id: number; name: string; kind: string }[];
  filters: ReportFilters; setFilters: (value: ReportFilters) => void; revision: number;
  transactions: { id: number; date: string; amount_minor: number }[]; renderTransactions: (ids: number[]) => ReactNode
}) {
  const [categorySource, setCategorySource] = useState('bank')
  const [report, setReport] = useState<SpendingReport>()
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [accountSearch, setAccountSearch] = useState('')
  const [transactionPage, setTransactionPage] = useState(0)
  const [transactionSort, setTransactionSort] = useState('newest')
  useEffect(() => { setTransactionPage(0) }, [report, transactionSort])
  const income = filters.measure === 'income', cashflow = filters.measure === 'cashflow'
  const source = sandbox ? categorySource : 'local'
  const comparison = filters.comparison && filters.comparison < month ? filters.comparison : ''
  useEffect(() => {
    const controller = new AbortController()
    setReport(undefined); setError('')
    const params = new URLSearchParams({ month, measure: filters.measure, category: filters.category })
    filters.accounts.forEach(id => params.append('account', String(id)))
    if (comparison) params.set('comparison', comparison)
    request<SpendingReport>(path + '/analytics?' + params, 'GET', undefined, controller.signal)
      .then(result => { if (!controller.signal.aborted) setReport(result) })
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [month, filters, comparison, request, path, revision, retry])
  const change = (value: Partial<ReportFilters>) => setFilters({ ...filters, ...value })
  const selectedCategories = categories.filter(category => cashflow || category.kind === (income ? 'income' : 'expense'))
  const rows = report ? source === 'bank' ? report.bank_categories : report.categories : []
  const label = income ? 'Income' : cashflow ? 'Cash flow' : 'Spending'
  const eligible = new Set(report?.eligible_ids ?? [])
  const contributing = transactions.filter(tx => eligible.has(tx.id)).sort((a, b) =>
    transactionSort === 'largest' ? b.amount_minor - a.amount_minor || b.id - a.id :
      transactionSort === 'smallest' ? a.amount_minor - b.amount_minor || a.id - b.id :
        transactionSort === 'oldest' ? a.date.localeCompare(b.date) || a.id - b.id : b.date.localeCompare(a.date) || b.id - a.id)
  const pageSize = 10
  const lastPage = Math.max(0, Math.ceil(contributing.length / pageSize) - 1)
  const currentPage = Math.min(transactionPage, lastPage)
  return <div className="spending-page">
    <ViewSelector label="Financial report" className="report-tabs" value={filters.measure}
      onChange={measure => change({ measure, category: 'all' })}
      onReselect={() => change({ category: 'all' })}
      options={[{ value: 'spending', label: 'Spending' }, { value: 'income', label: 'Income' }, { value: 'cashflow', label: 'Cash flow' }]} />
    <section className="card report-filters" aria-label="Report filters">
      <h2>Report filters</h2>
      <div className="report-filter-fields">
        <details className="report-account-filter"><summary>Accounts · {filters.accounts.length ? filters.accounts.length + ' selected' : 'All'}</summary>
          <div className="report-account-options">
            <input type="search" aria-label="Search report accounts" placeholder="Find an account" value={accountSearch} onChange={event => setAccountSearch(event.target.value)} />
            <button className="secondary small" onClick={() => change({ accounts: [] })}>Use all accounts</button>
            <div className="report-account-scroll">{accounts.filter(account => (account.name + ' ' + (account.institution_name ?? '')).toLowerCase().includes(accountSearch.toLowerCase())).map(account =>
              <label className="checkbox" key={account.id}><input type="checkbox" checked={filters.accounts.includes(account.id)} onChange={event => change({ accounts: event.target.checked ? [...filters.accounts, account.id] : filters.accounts.filter(id => id !== account.id) })} />
                <span>{account.name}{account.institution_name && <small>{account.institution_name}</small>}</span></label>)}</div>
            <p className="analytics-note">No accounts selected means all accounts.</p>
            <button className="secondary small" onClick={event => { const details = event.currentTarget.closest('details'); if (details) { details.open = false; details.querySelector('summary')?.focus() } }}>Done</button>
          </div>
        </details>
        <label>Assigned category<select aria-label="Report category" value={filters.category} onChange={event => change({ category: event.target.value })}>
          <option value="all">All categories</option><option value="uncategorized">Uncategorized</option>{selectedCategories.map(category => <option key={category.id} value={category.id}>{category.name}</option>)}
        </select></label>
        <label>Compare with<select aria-label="Report comparison" value={comparison ? 'custom' : 'previous'} onChange={event => {
          if (event.target.value === 'previous') change({ comparison: '' })
          else { const date = new Date(month + '-01T12:00:00'); date.setMonth(date.getMonth() - 1); change({ comparison: String(date.getFullYear()).padStart(4, '0') + '-' + String(date.getMonth() + 1).padStart(2, '0') }) }
        }}><option value="previous">Previous month</option><option value="custom" disabled={month === '0001-01'}>Choose month</option></select></label>
        {comparison && <label>Comparison month<input type="month" aria-label="Comparison month" max={month} value={comparison} onChange={event => { if (event.target.value && event.target.value < month) change({ comparison: event.target.value }) }} /></label>}
        <button className="secondary small" onClick={() => setFilters({ ...defaultReportFilters, measure: filters.measure })}>Reset filters</button>
      </div>
      <p className="analytics-note">Filters apply to the entire report · Posted USD activity</p>
    </section>
    <div className="report-results">
    {error && <p className="notice" role="alert">{error} <button className="secondary small" onClick={() => setRetry(value => value + 1)}>Retry report</button></p>}
    {!report && !error && <p className="muted" role="status">Loading report…</p>}
    {report && <>
      <div className={'spending-metrics' + (income ? ' income-metrics' : '')}>
        {(income ? [['Income', report.net_minor], ['Daily average', report.average_minor]] : cashflow ? [['Cash flow', report.net_minor], ['Income', report.income_minor], ['Net spending', report.spending_minor], ['Daily average', report.average_minor]] : [['Net spending', report.net_minor], ['Purchases', report.purchases_minor], ['Refunds', report.refunds_minor], ['Daily average', report.average_minor]]).map(([title, value]) =>
          <section className="card metric-card" key={title} data-metric={title}><h2>{title}</h2><strong className="numeric">{value === null ? '—' : money(Number(value))}</strong>
            {title === 'Daily average' ? <small>Per elapsed calendar day</small> : title === 'Net spending' ? <small>Purchases minus refunds</small> : title === 'Cash flow' && <small>Income minus net spending</small>}</section>)}
      </div>
      <section className="card spending-main">
        <div className="section-heading"><div><h2>{label} this month</h2><small>{monthLabel(month)} · {report.through_day ? 'Through day ' + report.through_day : 'No elapsed days'}</small></div>
          <button className="secondary small" onClick={() => drill(report.eligible_ids, label + ' · ' + monthLabel(month) + ' · ' + money(report.net_minor))}>View transactions →</button></div>
        <div className="spend-comparison"><strong className="numeric">{report.change_minor === null ? 'No posted comparison activity' : (report.change_minor >= 0 ? '+' : '−') + money(Math.abs(report.change_minor)) + (report.change_percent !== null ? ` (${Math.abs(report.change_percent).toFixed(1)}% ${report.change_minor >= 0 ? 'more' : 'less'})` : '')}</strong>
          <small>{report.comparison_month && monthLabel(report.comparison_month) + ' · '}{report.comparison_days ? `days 1–${report.comparison_days}` : 'No comparable elapsed days'}{report.previous_minor !== null && ' · ' + money(report.previous_minor)}</small></div>
        <SpendingTrend report={report} money={money} />
      </section>
      <div className="analytics-grid report-calendar-grid">
        <section className={'card report-calendar ' + (cashflow ? 'cashflow-calendar' : '')}><div className="section-heading"><h2>{label} calendar</h2></div>
          <SpendingCalendar report={report} money={money} drill={drill} changeMonth={changeMonth} />
          <p className="analytics-note">Select a day for exact transactions. Calendar amounts are rounded; future dates have no actual totals.</p>
        </section>
        {!cashflow ? <section className="card"><div className="section-heading"><h2>{income ? 'Income' : 'Purchases'} by category</h2>
          {sandbox && <select aria-label="Category source" value={source} onChange={event => setCategorySource(event.target.value)}><option value="bank">Bank categories</option><option value="local">Your categories</option></select>}</div>
          <CategoryDonut rows={rows} money={money} drill={drill} income={income} />
        </section> : <section className="card"><h2>What makes up your cash flow</h2><p className="analytics-note">Income minus purchases, plus refunds. Transfers and investment activity are excluded.</p>
          {['income', 'expense', 'refund'].map(kind => <button className="cashflow-drill" key={kind} onClick={() => drill(report.eligible_ids.filter(id => report.kind_ids?.[kind]?.includes(id)), kind === 'income' ? 'Cash flow · Income' : kind === 'expense' ? 'Cash flow · Purchases' : 'Cash flow · Refunds')}>
            <span>{kind === 'income' ? 'Income' : kind === 'expense' ? 'Purchases' : 'Refunds'}</span><strong className="numeric">{money(kind === 'income' ? report.income_minor : kind === 'expense' ? report.spending_minor + report.refunds_minor : report.refunds_minor)} →</strong>
          </button>)}
        </section>}
      </div>
      {!cashflow && <Breakdown title={income ? 'Income category totals' : 'Net spending by category'} rows={rows} money={money} drill={drill} />}
      <details className="metric-note report-method"><summary>What this report includes</summary><p>Reports use recorded, posted activity. Pending entries, credits awaiting classification, transfers and investment activity are excluded. Imported history may be incomplete. Bank categories are separate from your assigned categories and budgets.</p></details>
      <section className="card report-transactions" aria-label="Transactions in this report">
        <div className="section-heading"><div><h2>Transactions in this report</h2><small>{monthLabel(report.month)} · {contributing.length} contributing {contributing.length === 1 ? 'transaction' : 'transactions'}</small></div>
          <label className="report-transaction-sort">Sort by<select aria-label="Sort report transactions" value={transactionSort} onChange={event => setTransactionSort(event.target.value)}>
            <option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="largest">Largest amount</option><option value="smallest">Smallest amount</option>
          </select></label>
        </div>
        {contributing.length ? renderTransactions(contributing.slice(currentPage * pageSize, (currentPage + 1) * pageSize).map(tx => tx.id)) : <p className="empty">No transactions contribute to this report. Try another month or clear the report filters.</p>}
        {contributing.length > pageSize && <div className="pagination" aria-label="Report transaction pages"><span className="muted" role="status">{currentPage * pageSize + 1}–{Math.min((currentPage + 1) * pageSize, contributing.length)} of {contributing.length}</span>
          <div className="inline-actions"><button className="secondary small" disabled={currentPage === 0} onClick={() => setTransactionPage(currentPage - 1)}>Previous</button>
            <button className="secondary small" disabled={currentPage === lastPage} onClick={() => setTransactionPage(currentPage + 1)}>Next</button></div>
        </div>}
      </section>
    </>}
    </div>
  </div>
}

export function HomeSpending({ report, money, drill, changeMonth, recent, openSpending }: {
  report: SpendingReport; money: Money; drill: Drill; changeMonth: (month: string) => void; recent: ReactNode; openSpending: () => void
}) {
  return <section className="card stack home-spending"><div className="section-heading"><div><h2>Spent in {monthLabel(report.month)}</h2><small>Posted purchases minus refunds · USD</small></div><button className="secondary small" onClick={openSpending}>Explore spending →</button></div>
    <strong className="home-spend-value numeric">{money(report.net_minor)}</strong>
    <div className="home-spending-grid"><SpendingCalendar report={report} money={money} drill={drill} changeMonth={changeMonth} />
      <div className="recent-spending"><div className="section-heading"><h3>Recent spending</h3><button className="text-button" onClick={() => drill(report.eligible_ids, 'Posted spending · ' + monthLabel(report.month))}>View all</button></div>{recent}</div></div>
    <p className="analytics-note">Calendar labels are rounded; select a day for exact amounts. Refunds reduce spending; blank future days have no actual totals.</p>
  </section>
}
