import { useEffect, useState } from 'react'

type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>
type Details = { provider: Record<string, unknown> | null; retrieved_at: string | null }

function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Not supplied'
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
}

export function TransactionDetails({ id, request }: { id: number; request: Request }) {
  const [data, setData] = useState<Details>()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setError('')
    request<Details>('/plaid/sandbox/preview/transactions/' + id + '/details', 'GET', undefined, controller.signal)
      .then(setData)
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [id, request, attempt])
  const provider = data?.provider
  const fields: [string, string][] = [
    ['Merchant', 'merchant_name'], ['Original description', 'original_description'],
    ['Posted date', 'date'], ['Posted time', 'datetime'],
    ['Authorized date', 'authorized_date'], ['Authorized time', 'authorized_datetime'],
    ['Payment channel', 'payment_channel'], ['Provider category', 'personal_finance_category'],
    ['Amount reported', 'amount'], ['Currency', 'iso_currency_code'],
    ['Location', 'location'], ['Counterparties', 'counterparties'],
    ['Payment information', 'payment_meta'], ['Check number', 'check_number'],
    ['Website', 'website'], ['Pending', 'pending'],
  ]
  return <section className="transaction-details" aria-label="Provider transaction details">
    <h3>Provider transaction details</h3>
    {error && <p className="notice" role="alert">{error} <button type="button" className="secondary small" onClick={() => setAttempt(value => value + 1)}>Retry</button></p>}
    {!data && !error && <p role="status" className="muted">Loading details…</p>}
    {data && !provider && <p className="muted">This record was imported before full details were saved. Use Bank sync → Refresh transaction details to retrieve them.</p>}
    {provider && <>
      <p className="muted">Retrieved {data?.retrieved_at ? new Date(data.retrieved_at).toLocaleString() : 'at an unknown time'}. Availability varies by institution.</p>
      <dl className="transaction-fields">
        {fields.map(([label, key]) => <div key={key}><dt>{label}</dt><dd>{display(provider[key])}</dd></div>)}
      </dl>
      <details><summary>All provider fields</summary><pre className="provider-json">{JSON.stringify(provider, null, 2)}</pre></details>
    </>}
  </section>
}
