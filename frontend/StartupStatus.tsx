import { useEffect, useRef, useState } from 'react'

type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>
type Status = { enabled: boolean; running: boolean; phase: string; version: number; finished_at: string | null;
  results: { institution: string; product: string; status: string; message: string }[] }

export function StartupStatus({ request, enabled, onChanged, onRunning, showDetails, onDetails }: {
  request: Request; enabled: boolean; onChanged: () => void; onRunning: (running: boolean) => void;
  showDetails: boolean; onDetails: () => void
}) {
  const [status, setStatus] = useState<Status>()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [dismissed, setDismissed] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const changed = useRef(onChanged)
  const running = useRef(onRunning)
  changed.current = onChanged; running.current = onRunning
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let version = -1
    async function poll() {
      try {
        const data = await request<Status>('/startup', 'GET', undefined, controller.signal)
        if (controller.signal.aborted) return
        setStatus(data); setError(''); running.current(data.running)
        if (data.version !== version) { version = data.version; changed.current() }
        if (data.running) timer = setTimeout(() => void poll(), 1500)
      } catch {
        if (!controller.signal.aborted) setError('Could not load startup progress. Reload status to check whether it finished.')
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [enabled, request, attempt])
  const failures = status?.results.filter(row => row.status === 'Failed').length ?? 0
  const finished = status?.finished_at
  useEffect(() => { if (failures || error) setDismissed(false) }, [failures, error])
  useEffect(() => {
    if (!finished || status?.running || failures || error) return
    // Completion is a brief acknowledgement, not permanent dashboard content.
    if (Date.now() - Date.parse(finished) > 60_000) { setDismissed(true); return }
    const timer = setTimeout(() => setDismissed(true), 6000)
    return () => clearTimeout(timer)
  }, [finished, status?.running, failures, error])
  if (!enabled || (!error && !status?.enabled)) return null
  const title = error ? 'Startup status unavailable' : status?.running ? 'Updating your accounts…' : failures ? 'Startup needs attention' : 'Startup complete'
  if (!showDetails) return dismissed ? null : <aside className="startup-toast" aria-label="Startup notification">
    <span className={'startup-indicator' + (status?.running ? ' is-loading' : '')} aria-hidden="true">{status?.running ? '' : failures || error ? '!' : '✓'}</span>
    <div className="startup-toast-copy"><strong role="status" aria-live="polite">{title}</strong>
      <small>{error ? 'Check the connection to your local app.' : status?.running ? status.phase : failures ? 'Some actions failed. Your saved data is retained.' : 'Startup finished. Results are in Bank sync.'}</small>
      <div className="startup-toast-actions"><button className="secondary small" onClick={() => { setExpanded(true); onDetails() }}>View details</button>
        {error && <button className="secondary small" onClick={() => { setDismissed(false); setAttempt(value => value + 1) }}>Reload status</button>}</div>
    </div>
    <button type="button" className="startup-dismiss" aria-label="Dismiss startup notification" onClick={() => setDismissed(true)}>×</button>
  </aside>
  return <section className="startup-status" aria-label="Startup maintenance">
    <div className="section-heading"><div><strong>Startup {status?.running ? 'in progress' : failures ? 'finished with issues' : 'finished'}</strong>
      <p className="muted" role="status">{status?.running ? status.phase + ' · You can keep browsing. Leave the app running until this finishes.'
        : status?.finished_at ? 'Last run: ' + new Date(status.finished_at).toLocaleString() + '. ' + (failures ? 'See failed actions below; previous saved data is retained.' : 'Backup, sync and snapshot results are below.') : 'Checking startup status…'}</p></div></div>
    {error && <p className="notice" role="alert">{error} <button className="secondary small" onClick={() => setAttempt(value => value + 1)}>Reload status</button></p>}
    {!!status?.results.length && <details className="refresh-results" open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}><summary>Startup results · {status.results.filter(row => row.status === 'Updated').length} completed · {failures} failed · {status.results.filter(row => row.status === 'Skipped').length} skipped</summary>
      <p className="muted">Runs once per app launch. Reopening this page does not sync again. Saved snapshots retain each account’s update time; stale values do not become fresh chart observations.</p>
      <ul>{status.results.map((row, index) => <li key={index}><strong>{row.institution} · {row.product}</strong><span className={row.status === 'Failed' ? 'sync-warning' : 'muted'}>{row.status} — {row.message}</span></li>)}</ul>
    </details>}
  </section>
}
