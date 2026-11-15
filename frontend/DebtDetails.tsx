import { useEffect, useRef, useState, type FormEvent } from 'react'
import type { Account } from './AccountList'

type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>
type Fields = Record<string, string | number | null>
type Debt = { source: 'manual' | 'plaid'; fields: Fields | null; updated_at: string | null; last_sync?: string | null; currency?: string | null;
  aprs?: { percent: number | string | null; type: string | null; balance_minor: number | null; interest_minor: number | null }[]; is_overdue?: boolean | null; provider?: object | null }
const types: Record<string, string> = { student: 'Student loan', mortgage: 'Mortgage', auto: 'Auto loan', consumer: 'Personal loan', 'credit card': 'Credit card', 'line of credit': 'Credit line', other: 'Other debt' }
const amounts: [string, string][] = [['credit_limit_minor', 'Credit limit'], ['available_credit_minor', 'Available credit'], ['original_principal_minor', 'Original principal'], ['principal_balance_minor', 'Principal balance'], ['outstanding_interest_minor', 'Accrued interest'], ['minimum_payment_minor', 'Minimum payment'], ['last_payment_minor', 'Last payment'], ['statement_balance_minor', 'Statement balance']]
const dates: [string, string][] = [['last_payment_date', 'Last payment date'], ['statement_date', 'Statement date'], ['origination_date', 'Origination date'], ['maturity_date', 'Maturity date']]
const strings: [string, string][] = [['servicer', 'Servicer / lender'], ['repayment_plan', 'Repayment plan'], ['loan_status', 'Loan status']]
const supplied = (value: unknown) => value !== null && value !== undefined && value !== ''
const human = (value: string) => value.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase())

export function DebtDetails({ account, sandbox, managed, request, money, parseMoney, revision, onChanged }: {
  account: Account; sandbox: boolean; managed?: boolean; request: Request; money: (value: number, currency?: string) => string;
  parseMoney: (value: string, currency: string) => number; revision: number; onChanged: () => void
}) {
  const manualMirror = sandbox && account.balance_source === 'manual'
  const reported = sandbox && !manualMirror
  const panel = useRef<HTMLDetailsElement>(null)
  const [data, setData] = useState<Debt>()
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const path = sandbox ? '/plaid/sandbox/debt/' + account.id : '/accounts/' + account.id + '/debt'
  async function load() {
    setError('')
    try { setData(await request<Debt>(path)) }
    catch (failure) { setError((failure as Error).message) }
  }
  useEffect(() => { if (panel.current?.open) void load() }, [revision, request, path])
  async function sync() {
    setBusy(true); setError(''); setMessage('')
    try {
      const result = await request<{ count: number }>('/plaid/sandbox/liabilities/sync' + (managed ? '?account_id=' + account.id : ''), 'POST')
      setData(await request<Debt>(path)); onChanged()
      setMessage(`Debt details synced for ${result.count} supported accounts on this connection.`)
    } catch (failure) { setError((failure as Error).message) }
    finally { setBusy(false) }
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(''); setMessage('')
    try {
      const form = new FormData(event.currentTarget)
      const text = (name: string) => String(form.get(name) ?? '').trim()
      const body: Fields = { subtype: text('subtype'), apr_percent: text('apr_percent') || null, rate_type: text('rate_type'), notes: text('notes') }
      for (const [key] of strings) body[key] = text(key)
      for (const [key] of dates) body[key] = text(key) || null
      for (const [key] of amounts) body[key] = text(key) ? parseMoney(text(key), account.currency) : null
      setData(await request<Debt>(path, 'PUT', body)); setEditing(false); onChanged(); setMessage('Debt details saved.')
    } catch (failure) { setError((failure as Error).message) }
    finally { setBusy(false) }
  }
  const fields = data?.fields
  const currency = data?.currency || account.currency
  const rows: [string, string][] = []
  if (fields) {
    rows.push(['Debt type', types[String(fields.subtype)] ?? human(String(fields.subtype))])
    for (const [key, label] of strings) if (supplied(fields[key])) rows.push([label, String(fields[key])])
    if (supplied(fields.apr_percent)) rows.push(['APR / interest rate', fields.apr_percent + '%' + (fields.rate_type && fields.rate_type !== 'unknown' ? ' · ' + human(String(fields.rate_type)) : '')])
    for (const [key, label] of amounts) if (supplied(fields[key])) rows.push([label, money(Number(fields[key]), currency)])
    for (const [key, label] of dates) if (supplied(fields[key])) rows.push([label, String(fields[key])])
  }
  const editMoney = (key: string) => fields?.[key] == null ? '' : (Number(fields[key]) / (account.currency === 'JPY' ? 1 : 100)).toFixed(account.currency === 'JPY' ? 0 : 2)
  const primaryAmounts = account.kind === 'credit' ? ['credit_limit_minor', 'available_credit_minor'] : ['original_principal_minor', 'principal_balance_minor', 'outstanding_interest_minor']
  const moneyInput = ([key, label]: [string, string]) => <label key={key}>{label} ({account.currency})<input name={key} inputMode="decimal" defaultValue={editMoney(key)} placeholder="Not supplied" /></label>

  return <details className="debt-details" ref={panel} onToggle={event => { if (event.currentTarget.open && !data) void load() }}>
    <summary>Debt details <span className="chip">{reported ? 'Plaid' : 'Manual'}</span></summary>
    <div className="debt-content">
      {!data && !error && <p role="status" className="muted">Loading debt details…</p>}
      {data && <>
        <div className="section-heading"><div><h3>{reported ? 'Reported debt terms' : 'Manual debt terms'}</h3><small>{data.updated_at ? (reported ? 'Retrieved ' : 'Updated ') + new Date(data.updated_at).toLocaleString() : 'No details saved yet.'}</small></div>
          {reported ? <button className="secondary small" disabled={busy} onClick={() => void sync()}>Sync debt details</button> : !sandbox && !editing && <button className="secondary small" onClick={() => { setEditing(true); setMessage(''); setError('') }}>{rows.length > 1 ? 'Edit debt details' : 'Add debt details'}</button>}</div>
        {reported && <p className="muted">Retrieves supported debts on this account’s saved connection. Availability varies by institution; missing fields are unavailable.</p>}
        {editing && fields ? <form className="debt-editor" onSubmit={event => void save(event)}><fieldset disabled={busy}>
          <div className="form-grid"><label>Debt type<select name="subtype" defaultValue={String(fields.subtype)}>{Object.entries(types).filter(([value]) => account.kind === 'credit' ? ['credit card', 'line of credit', 'other'].includes(value) : value !== 'credit card').map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>Servicer / lender<input name="servicer" maxLength={120} defaultValue={String(fields.servicer ?? '')} placeholder="e.g. Nelnet" /></label>
            <label>APR / interest rate (%)<input name="apr_percent" type="number" min="0" max="1000" step="0.0001" defaultValue={String(fields.apr_percent ?? '')} /></label><label>Rate type<select name="rate_type" defaultValue={String(fields.rate_type)}><option value="unknown">Not specified</option><option value="fixed">Fixed</option><option value="variable">Variable</option></select></label>
            {amounts.filter(([key]) => primaryAmounts.includes(key)).map(moneyInput)}
            {strings.filter(([key]) => key !== 'servicer').map(([key, label]) => <label key={key}>{label}<input name={key} maxLength={120} defaultValue={String(fields[key] ?? '')} placeholder="Not supplied" /></label>)}
          </div>
          <details className="debt-payment-fields"><summary>More balances, payments and dates</summary><div className="form-grid">
            {amounts.filter(([key]) => !primaryAmounts.includes(key)).map(moneyInput)}
            {dates.map(([key, label]) => <label key={key}>{label}<input name={key} type="date" defaultValue={String(fields[key] ?? '')} /></label>)}
          </div></details>
          <label>Notes<textarea name="notes" maxLength={2000} defaultValue={String(fields.notes ?? '')} /></label>
          <p className="muted">Blank fields clear saved values. Saving terms leaves the account balance unchanged. For Nelnet loans with different rates, add a separate account for each loan group.</p>
          <div className="form-actions"><button className="secondary" type="button" disabled={busy} onClick={() => { setEditing(false); setError('') }}>Cancel</button><button className="primary" type="submit">{busy ? 'Saving…' : 'Save debt details'}</button></div>
        </fieldset></form> : <>
          {rows.length > 0 && <dl className="debt-facts">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>}
          {(!fields || rows.length <= 1) && <p className="empty">{reported ? 'No supported details returned for this account yet. Try Sync debt details.' : 'Add the servicer, rate and any loan details you want to track.'}</p>}
          {data.is_overdue != null && <p className="muted">Reported overdue: {data.is_overdue ? 'Yes' : 'No'}</p>}
          {!!data.aprs?.length && <div className="debt-aprs"><h4>Reported APRs</h4>{data.aprs.map((apr, index) => <div key={index}><strong>{apr.percent == null ? 'Unavailable' : apr.percent + '%'} · {human(apr.type ?? 'Other')}</strong><small>Balance subject to rate: {apr.balance_minor == null ? 'Not supplied' : money(apr.balance_minor, currency)} · Interest charge: {apr.interest_minor == null ? 'Not supplied' : money(apr.interest_minor, currency)}</small></div>)}</div>}
          {fields?.notes && <p className="debt-notes">{fields.notes}</p>}
          {data.provider && <details className="debt-provider"><summary>Additional provider details</summary><pre>{JSON.stringify(data.provider, null, 2)}</pre></details>}
        </>}
        {!sandbox && !editing && <p className="muted">Use Record balance to reconcile with Nelnet’s balance. For a manual payment, record principal as a transfer to this loan and interest or fees as a separate expense. Interest is not accrued automatically.</p>}
      </>}
      {manualMirror && <p className="muted">Edit this account’s balance and debt terms in Personal. Combined shows a read-only mirror.</p>}
      {busy && reported && <p role="status" className="muted">Retrieving debt details…</p>}
      {error && <p className="notice" role="alert">{error}{!data && <button className="secondary small" onClick={() => void load()}>Retry</button>}</p>}
      {message && <p className="notice" role="status">{message}</p>}
    </div>
  </details>
}
