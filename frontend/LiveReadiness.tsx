import { useEffect, useState, type FormEvent } from 'react'

type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>
type Setup = { trial_confirmed: boolean; item_limit: number | null; transactions_enabled: boolean; oauth_confirmed: boolean; hosted_link_confirmed: boolean }
type State = { configured: boolean; linking_enabled: boolean; ready: boolean; connected_items: number; setup: Setup;
  checks: { label: string; ok: boolean }[]; limit_note: string;
  preflights: { institution_id: string; name: string; transactions: boolean; oauth: boolean | null; checked_at: string; note: string }[] }

export function LiveReadiness({ request, revision }: { request: Request; revision?: number }) {
  const [data, setData] = useState<State>()
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
        oauth_confirmed: form.has('oauth_confirmed'), hosted_link_confirmed: form.has('hosted_link_confirmed'),
        item_limit: form.get('item_limit') ? Number(form.get('item_limit')) : null }
      setData(await request<State>('/plaid/live/readiness', 'PUT', setup)); setMessage('Dashboard confirmations saved. This does not enable real-bank linking.')
    } catch (failure) { setError((failure as Error).message) }
    finally { setBusy(false) }
  }
  return <section className="card live-readiness">
    <div className="section-heading"><div><h2>Live readiness</h2><small>Prepare your local app before connecting a real bank</small></div><span className="chip">{data?.linking_enabled ? 'Pilot enabled' : 'Linking locked'}</span></div>
    <p className="muted">Preparation never creates a bank connection. Add an institution below to check Production coverage using your Production credentials. Link stays locked until the separately approved pilot is enabled locally.</p>
    {!data && !error && <p role="status">Checking local setup…</p>}
    {data && <>
      <ul className="readiness-checks">{data.checks.map(check => <li key={check.label}><span className={check.ok ? 'positive' : 'muted'}>{check.ok ? '✓' : '○'}</span><span>{check.label}<small>{check.ok ? 'Configured / confirmed' : 'Needs setup'}</small></span></li>)}</ul>
      <p className="muted">{data.connected_items} locally connected Items · Dashboard allowance: {data.setup.item_limit ?? 'Not confirmed'}. {data.limit_note}</p>
      <details className="readiness-settings"><summary>Dashboard confirmations</summary><p className="muted">Check these against your own Plaid Dashboard. These are your confirmations, not automatic verification of your plan or entitlements.</p>
        <form key={JSON.stringify(data.setup)} onSubmit={event => void save(event)}><fieldset disabled={busy}>
          {([['trial_confirmed', 'Free Trial is active'], ['transactions_enabled', 'Transactions is enabled'], ['oauth_confirmed', 'Bank of America / OAuth access is available'], ['hosted_link_confirmed', 'Hosted Link is enabled']] as const).map(([name, label]) => <label className="checkbox" key={name}><input type="checkbox" name={name} defaultChecked={data.setup[name]} />{label}</label>)}
          <label>Connection allowance shown in Dashboard<input type="number" name="item_limit" min="1" max="1000" defaultValue={data.setup.item_limit ?? ''} /></label>
          <button className="secondary small" type="submit">Save confirmations</button>
        </fieldset></form>
      </details>
      <details className="readiness-settings"><summary>Local credentials and OAuth setup</summary>
        <p className="muted">In the project’s private .env file, set PLAID_PRODUCTION_SECRET and optionally PLAID_PRODUCTION_CLIENT_ID. The shared PLAID_CLIENT_ID can be reused. Keep your existing Sandbox settings. Never enter secrets in this screen or commit them.</p>
        <p className="muted">Keep PLAID_LIVE_ENABLED=false during preparation. The app opens Hosted Link in a regular browser tab and checks completion server-side. No public webhook, tunnel or exposure of the local app is needed. A custom OAuth redirect is optional for desktop web; if configured, PLAID_PRODUCTION_REDIRECT_URI must be HTTPS and allowlisted in Dashboard. Hosted Link and institution-specific OAuth access must still be enabled.</p>
        <a href="https://dashboard.plaid.com/" target="_blank" rel="noopener noreferrer">Open Plaid Dashboard ↗</a>
      </details>
      <details className="readiness-settings"><summary>Institution coverage checks ({data.preflights.length})</summary>
        {data.preflights.length ? data.preflights.map(item => <article className="preflight-result" key={item.institution_id}><strong>{item.name}</strong><p>Transactions: {item.transactions ? 'Supported' : 'Unavailable'} · OAuth: {item.oauth == null ? 'Not reported' : item.oauth ? 'Supported' : 'Not reported as supported'}</p><small>{new Date(item.checked_at).toLocaleString()} · {item.note}</small></article>) : <p className="muted">No checks run yet. Configure credentials, then search and add the institution below. This performs a read-only institution lookup and saves a local draft.</p>}
      </details>
      <button className="secondary small" disabled={busy} onClick={() => { setError(''); setAttempt(value => value + 1) }}>Refresh checks</button>
    </>}
    {error && <p className="notice" role="alert">{error}<button className="secondary small" onClick={() => setAttempt(value => value + 1)}>Retry</button></p>}
    {message && <p className="notice" role="status">{message}</p>}
  </section>
}
