import { useId, useState, type ReactNode } from 'react'

export type Bucket = { key: string; label: string; purchases_minor: number; refunds_minor: number; net_minor: number; transaction_ids: number[] }
type Day = { day: number; date: string; purchases_minor: number; refunds_minor: number; net_minor: number | null; cumulative_minor: number | null; transaction_ids: number[] }
export type SpendingReport = {
  month: string; days: number; through_day: number; comparison_month: string | null; comparison_days: number;
  purchases_minor: number; refunds_minor: number; net_minor: number; average_minor: number | null;
  previous_minor: number | null; change_minor: number | null; change_percent: number | null;
  daily: Day[]; comparison_daily: { day: number; cumulative_minor: number }[]; eligible_ids: number[];
  categories: Bucket[]; bank_categories: Bucket[]; merchants: Bucket[]
}
type Money = (amount: number, currency?: string) => string
type Drill = (ids: number[], label: string) => void
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
          aria-current={day.date === today ? 'date' : undefined} aria-label={day.date + ': ' + (day.net_minor === null ? 'Future date' : money(day.net_minor) + ' net spending, ' + day.transaction_ids.length + ' transactions')}
          onClick={() => drill(day.transaction_ids, 'Spending on ' + day.date + ' · ' + money(day.net_minor!))}>
          <span>{day.day}</span><strong className="numeric">{amount}</strong>
        </button>
      })}
    </div>
    <div className="calendar-key"><span>Less</span>{[0, 1, 2, 3, 4].map(level => <i key={level} className={'heat-' + level} />)}<span>More</span><span className="refund-key">Green = net refunds</span></div>
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
        <svg viewBox="0 0 600 200" className="spending-svg" preserveAspectRatio="none" role="img" aria-label="Cumulative posted net spending by day of month; dashed line is the comparison month">
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
    </> : <p className="empty">No posted spending in this period. Choose another month or record an expense.</p>}
  </div>
}

function Breakdown({ title, rows, money, drill, children }: { title: string; rows: Bucket[]; money: Money; drill: Drill; children?: ReactNode }) {
  const maximum = Math.max(1, ...rows.map(row => Math.abs(row.net_minor)))
  return <section className="card breakdown-card"><div className="section-heading"><h2>{title}</h2>{children}</div>
    {!rows.length ? <p className="empty">No posted spending to break down.</p> : <div className="breakdown-scroll">{rows.map(row => <button className="breakdown-row" key={row.key} onClick={() => drill(row.transaction_ids, title + ': ' + row.label)}>
      <span className="breakdown-heading"><strong>{row.label}</strong><strong className={'numeric ' + (row.net_minor < 0 ? 'positive' : '')}>{money(row.net_minor)}</strong></span>
      <span className="breakdown-track"><span className={row.net_minor < 0 ? 'refund-bar' : ''} style={{ width: Math.abs(row.net_minor) / maximum * 100 + '%' }} /></span>
      <small>{row.transaction_ids.length} {row.transaction_ids.length === 1 ? 'transaction' : 'transactions'}{row.refunds_minor > 0 && ' · ' + money(row.refunds_minor) + ' refunded'}</small>
    </button>)}</div>}
  </section>
}

export function SpendingPage({ report, money, drill, changeMonth, sandbox }: { report: SpendingReport; money: Money; drill: Drill; changeMonth: (month: string) => void; sandbox: boolean }) {
  const [categorySource, setCategorySource] = useState('bank')
  const [view, setView] = useState('trend')
  const source = sandbox ? categorySource : 'local'
  return <div className="spending-page">
    <div className="spending-metrics">
      {[['Net spending', report.net_minor], ['Purchases', report.purchases_minor], ['Refunds', report.refunds_minor], ['Daily average', report.average_minor]].map(([label, value]) =>
        <section className="card metric-card" key={label}><h2>{label}</h2><strong className="numeric">{value === null ? '—' : money(Number(value))}</strong>
          <small>{label === 'Daily average' ? 'Per elapsed calendar day' : label === 'Net spending' ? 'Purchases minus refunds' : 'Posted USD activity'}</small></section>)}
    </div>
    <section className="card spending-main">
      <div className="section-heading"><div><h2>Spending this month</h2><small>{monthLabel(report.month)} · {report.through_day ? 'Through day ' + report.through_day : 'No elapsed days'}</small></div>
        <div className="segmented" aria-label="Spending display">{['trend', 'calendar'].map(item => <button key={item} aria-pressed={view === item} onClick={() => setView(item)}>{item === 'trend' ? 'Trend' : 'Calendar'}</button>)}</div></div>
      <div className="spend-comparison"><strong className={'numeric ' + (report.change_minor !== null && report.change_minor < 0 ? 'positive' : '')}>
        {report.change_minor === null ? 'No posted comparison activity' : (report.change_minor >= 0 ? '+' : '−') + money(Math.abs(report.change_minor)) + (report.change_percent !== null ? ` (${Math.abs(report.change_percent).toFixed(1)}% ${report.change_minor >= 0 ? 'more' : 'less'})` : '')}</strong>
        <small>{report.comparison_month && `${monthLabel(report.comparison_month)} · `}{report.comparison_days ? `days 1–${report.comparison_days}` : 'No comparable elapsed days'}{report.previous_minor !== null && ' · ' + money(report.previous_minor) + ' net'}</small></div>
      {view === 'trend' ? <SpendingTrend report={report} money={money} /> : <SpendingCalendar report={report} money={money} drill={drill} changeMonth={changeMonth} />}
      <p className="analytics-note">Recorded activity only. Pending entries, credits awaiting review, transfers, and investment activity are excluded. Imported history may be incomplete.</p>
    </section>
    <div className="analytics-grid">
      <Breakdown title="By category" rows={source === 'bank' ? report.bank_categories : report.categories} money={money} drill={drill}>
        {sandbox && <select aria-label="Category source" value={source} onChange={event => setCategorySource(event.target.value)}><option value="bank">Bank categories</option><option value="local">Your categories</option></select>}
      </Breakdown>
      <Breakdown title="By merchant" rows={report.merchants} money={money} drill={drill} />
    </div>
    <p className="analytics-note">Bank categories describe the imported feed. Your categories and budget assignments are unchanged. Green negative amounts indicate net refunds.</p>
    <button className="secondary" onClick={() => drill(report.eligible_ids, 'Posted spending · ' + monthLabel(report.month))}>View spending transactions →</button>
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
