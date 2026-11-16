import { useEffect, useRef, useState, type FormEvent } from 'react'

type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>
type Setup = { trial_confirmed: boolean; item_limit: number | null; transactions_enabled: boolean; oauth_confirmed: boolean }
type State = { configured: boolean; linking_enabled: boolean; ready: boolean; connected_items: number; setup: Setup;
  checks: { label: string; ok: boolean }[]; limit_note: string;
  preflights: { institution_id: string; name: string; transactions: boolean; oauth: boolean | null; checked_at: string; note: string }[] }

export function LiveReadiness({ request, revision, onFindInstitution }: { request: Request; revision?: number; onFindInstitution: () => void }) {
  const [data, setData] = useState<State>()
  const configuration = useRef<HTMLDetailsElement>(null)
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    request<State>('/plaid/live/readiness', 'GET', undefined, controller.signal).then(setData)
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [request, attempt, revision])
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(''); setMessage('')
    const form = new FormData(event.currentTarget)
    try {
      const setup: Setup = { trial_confirmed: form.has('trial_confirmed'), transactions_enabled: form.has('transactions_enabled'),
        oauth_confirmed: form.has('oauth_confirmed'),
        item_limit: form.get('item_limit') ? Number(form.get('item_limit')) : null }
      const saved = await request<State>('/plaid/live/readiness', 'PUT', setup)
      setData(saved)
      if (saved.ready && configuration.current) configuration.current.open = false
      setMessage('Dashboard confirmations saved. This does not enable real-bank linking.')
    } catch (failure) { setError((failure as Error).message) }
    finally { setBusy(false) }
  }
  const outstanding = data?.checks.filter(check => !check.ok) ?? []
  const complete = data?.checks.filter(check => check.ok) ?? []
  return <section className="card live-readiness">
    <div className="section-heading"><div><h2>Live readiness</h2><small>{data?.ready ? 'Setup complete' : 'Prepare your local app before connecting a real bank'}</small></div><span className="chip">{data?.linking_enabled ? 'Pilot enabled' : 'Linking locked'}</span></div>
    {!data && !error && <p role="status">Checking local setup…</p>}
    {data && <>
      {outstanding.length > 0 && <ul className="readiness-checks readiness-outstanding">{outstanding.map(check => <li key={check.label}><span className="muted">○</span><span>{check.label}<small>Needs attention</small></span></li>)}</ul>}
      <div className="readiness-next"><p>{data.preflights.length ? 'Coverage results are saved below. Review the institution before connecting.' : 'Search for your bank to check coverage and prepare its entry. Adding an institution does not connect your bank.'}</p><button className="primary small" disabled={busy || !data.configured} onClick={onFindInstitution}>Find your bank</button></div>
      <p className="readiness-status muted">{data.connected_items} locally connected Items · Dashboard allowance: {data.setup.item_limit ?? 'Not confirmed'}{!data.linking_enabled && ' · Real-bank linking remains locked'}</p>
      <details className="readiness-settings readiness-configuration" ref={configuration}><summary>Setup details · {complete.length} completed</summary>
        {!!complete.length && <ul className="readiness-checks readiness-completed">{complete.map(check => <li key={check.label}><span className="positive">✓</span><span>{check.label}<small>Configured / confirmed</small></span></li>)}</ul>}
        <p className="muted">Dashboard confirmations are your saved settings, not automatic verification of product access. Update them here if your plan changes. {data.limit_note}</p>
        <form key={JSON.stringify(data.setup)} onSubmit={event => void save(event)}><fieldset disabled={busy}>
          {([['trial_confirmed', 'Free Trial is active'], ['transactions_enabled', 'Transactions is enabled'], ['oauth_confirmed', 'OAuth bank access is available']] as const).map(([name, label]) => <label className="checkbox" key={name}><input type="checkbox" name={name} defaultChecked={data.setup[name]} />{label}</label>)}
          <label>Connection allowance shown in Dashboard<input type="number" name="item_limit" min="1" max="1000" defaultValue={data.setup.item_limit ?? ''} /></label>
          <button className="secondary small" type="submit">Save confirmations</button>
        </fieldset></form>
      </details>
      <details className="readiness-settings"><summary>Credentials and advanced settings</summary>
        <p className="muted">Production credentials stay in the private project .env file. The app requests Hosted Link automatically; no separate Dashboard enablement or confirmation is required.</p>
        <p className="muted">Keep PLAID_LIVE_ENABLED=false while preparing. For desktop web, no custom OAuth redirect is required. If you configure PLAID_PRODUCTION_REDIRECT_URI, use an HTTPS address allowlisted in Dashboard. Your saved checks do not enable real-bank linking.</p>
        <a href="https://dashboard.plaid.com/activity/status/oauth-institutions" target="_blank" rel="noopener noreferrer">View OAuth access in Plaid Dashboard ↗</a>
      </details>
      {data.preflights.length > 0 && <details className="readiness-settings"><summary>Institution coverage ({data.preflights.length})</summary>
        {data.preflights.map(item => <article className="preflight-result" key={item.institution_id}><strong>{item.name}</strong><p>Transactions: {item.transactions ? 'Supported' : 'Unavailable'} · OAuth: {item.oauth == null ? 'Not reported' : item.oauth ? 'Supported' : 'Not reported as supported'}</p><small>{new Date(item.checked_at).toLocaleString()} · {item.note}</small></article>)}
      </details>}
      <button className="secondary small" disabled={busy} onClick={() => { setError(''); setAttempt(value => value + 1) }}>Refresh checks</button>
    </>}
    {error && <p className="notice" role="alert">{error}<button className="secondary small" onClick={() => setAttempt(value => value + 1)}>Retry</button></p>}
    {message && <p className="notice" role="status">{message}</p>}
  </section>
}
