import { useEffect, useRef, useState } from 'react'

type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>
type Backup = { id: string; size_bytes: number; file_date: string; format: string }
type Counts = { accounts: number; transactions: number; active_transactions?: number; holdings?: number; investment_activity?: number }
type BackupWorkspace = 'personal' | 'sandbox' | 'sandbox_investments'
type Preview = { id: string; workspace: BackupWorkspace; schema_version: number; restored_schema_version: number;
  counts: Counts; current_counts: Counts | null; created_at: string | null; file_date: string; legacy: boolean;
  restores_chart_selection: boolean; bank_policy: string; preview_token: string; confirmation: string;
  contents: { accounts: { name: string; currency: string | null }[]; transaction_dates: { first: string | null; last: string | null } } }
const label = (mode: string) => mode === 'personal' ? 'Personal' : mode === 'sandbox_investments' ? 'Sandbox · Investments' : 'Sandbox · Banking'
const size = (bytes: number) => bytes < 1024 * 1024 ? Math.max(1, Math.round(bytes / 1024)) + ' KB' : (bytes / 1024 / 1024).toFixed(1) + ' MB'

export function BackupRestore({ request, onRestored, initialWorkspace }: { request: Request; onRestored: () => void; initialWorkspace: BackupWorkspace }) {
  const [backups, setBackups] = useState<Backup[]>()
  const [mode, setMode] = useState<BackupWorkspace>(initialWorkspace)
  const [filter, setFilter] = useState('')
  const [page, setPage] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [preview, setPreview] = useState<Preview>()
  const [confirmation, setConfirmation] = useState('')
  const [result, setResult] = useState<{ workspace: string; recovery_backup: string }>()
  const [attempt, setAttempt] = useState(0)
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const controller = new AbortController()
    request<{ backups: Backup[] }>('/backups', 'GET', undefined, controller.signal)
      .then(data => { setBackups(data.backups); setError('') })
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [request, attempt])
  useEffect(() => { if (preview) dialog.current?.showModal() }, [preview])
  async function run(work: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('')
    try { await work() } catch (failure) { setError((failure as Error).message) } finally { setBusy(false) }
  }
  const matching = backups?.filter(backup => backup.id.toLowerCase().includes(filter.toLowerCase())) ?? []
  const currentPage = Math.min(page, Math.max(0, Math.ceil(matching.length / 10) - 1))
  const policy = preview?.bank_policy === 'keep_authorization_reset_feed'
    ? 'Restores empty Sandbox data while retaining your current bank authorization. The next sync starts a fresh data checkpoint on the same connection.'
    : preview?.bank_policy === 'keep_current_authorization'
    ? 'Keeps your current bank authorization. Restores the backup’s data and transaction cursor; the next sync may replay later activity. No new connection is created.'
    : preview?.bank_policy === 'restore_saved_authorization'
      ? 'Restores the saved encrypted bank authorization for this Windows user. No bank connection is created or contacted during restore.'
      : preview?.workspace !== 'personal' ? 'This backup has no saved bank connection.' : 'Your Sandbox bank connections are unaffected.'
  return <div className="backup-page">
    <section className="card backup-create"><div><h2>Back up a workspace</h2><p className="analytics-note">Save a consistent copy of accounts, transactions, budgets, review/rule state, transfer links, and that workspace’s chart selection.</p></div>
      <div className="backup-create-controls"><label>Workspace to back up<select aria-label="Backup workspace" disabled={busy} value={mode} onChange={event => setMode(event.target.value as BackupWorkspace)}><option value="personal">Personal</option><option value="sandbox">Sandbox · Banking</option><option value="sandbox_investments">Sandbox · Investments</option></select></label>
        <button className="primary" disabled={busy} onClick={() => void run(async () => {
          const created = await request<{ id: string }>('/backups', 'POST', { workspace: mode })
          const listed = await request<{ backups: Backup[] }>('/backups')
          setBackups(listed.backups); setFilter(''); setPage(0); setMessage(label(mode) + ' backup created: ' + created.id)
        })}>Create backup</button></div>
      <p className="analytics-note">Stored in this project’s backups folder, outside Git. Backups contain private financial records. Sandbox authorization is Windows-user encrypted; API credentials and local context docs are not included. Appearance and current workspace stay unchanged.</p>
    </section>
    {busy && <p className="muted" role="status">Working with local backup files…</p>}
    {error && <p className="notice" role="alert">{error}{!backups && <button className="secondary small" onClick={() => setAttempt(value => value + 1)}>Retry</button>}</p>}
    {message && <p className="notice backup-message" role="status">{message}</p>}
    {result && <section className="card restore-result"><h2>{label(result.workspace)} restored</h2><p className="analytics-note">App data has been refreshed. The previous workspace is available as a recovery backup:</p><p className="backup-filename">{result.recovery_backup}</p>
      <button className="primary" onClick={() => window.location.reload()}>Reload application</button></section>}
    <section className="card"><div className="section-heading"><div><h2>Saved backups</h2><small>{backups?.length ?? 0} project-local files · newest first</small></div><button className="secondary small" disabled={busy} onClick={() => setAttempt(value => value + 1)}>Refresh list</button></div>
      <input type="search" aria-label="Search backups" placeholder="Search backups" value={filter} onChange={event => { setFilter(event.target.value); setPage(0) }} />
      {!backups && !error && <p className="muted" role="status">Loading backups…</p>}
      {backups && !matching.length && <p className="empty stack">{backups.length ? 'No backups match your search.' : 'No backups yet. Create one before making major changes.'}</p>}
      <div className="backup-list">{matching.slice(currentPage * 10, (currentPage + 1) * 10).map(backup => <article key={backup.id} className="backup-row"><div><strong className="backup-filename">{backup.id}</strong><small>{new Date(backup.file_date).toLocaleString()} · {size(backup.size_bytes)} · {backup.format === 'bundle' ? backup.id.startsWith('recovery-') ? 'Recovery bundle' : 'Workspace bundle' : 'Legacy database · settings not included'}</small></div>
        <button className="secondary small" disabled={busy} onClick={() => void run(async () => {
          const checked = await request<Preview>('/backups/preview?id=' + encodeURIComponent(backup.id))
          setConfirmation(''); setPreview(checked)
        })}>Preview restore</button></article>)}</div>
      {matching.length > 10 && <div className="pagination"><span className="muted">{currentPage * 10 + 1}–{Math.min((currentPage + 1) * 10, matching.length)} of {matching.length}</span><div className="inline-actions"><button className="secondary small" disabled={!currentPage || busy} onClick={() => setPage(currentPage - 1)}>Previous backups</button><button className="secondary small" disabled={(currentPage + 1) * 10 >= matching.length || busy} onClick={() => setPage(currentPage + 1)}>Next backups</button></div></div>}
      <p className="analytics-note">Legacy .db backups are validated and upgraded on a temporary copy. Damaged, incompatible, or wrong-Item files cannot be restored. Other project files are never read from this screen.</p>
    </section>
    {preview && <dialog ref={dialog} aria-labelledby="restore-title" onCancel={event => { event.preventDefault(); if (!busy) setPreview(undefined) }}>
      <div className="dialog-heading"><h2 id="restore-title">Restore {label(preview.workspace)}?</h2><button className="icon" aria-label="Close restore preview" disabled={busy} onClick={() => setPreview(undefined)}>×</button></div>
      <p className="backup-filename">{preview.id}</p>
      <dl className="restore-summary"><div><dt>{preview.created_at ? 'Snapshot created' : 'Legacy file date'}</dt><dd>{new Date(preview.created_at ?? preview.file_date).toLocaleString()}</dd></div>
        <div><dt>Workspace</dt><dd>{label(preview.workspace)}</dd></div><div><dt>Schema</dt><dd>{preview.schema_version}{preview.schema_version !== preview.restored_schema_version ? ' → ' + preview.restored_schema_version + ' on restore' : ' · compatible'}</dd></div>
        <div><dt>Accounts</dt><dd>{preview.current_counts?.accounts ?? 'Not initialized'} currently → {preview.counts.accounts} in backup</dd></div><div><dt>Transactions</dt><dd>{preview.current_counts?.transactions ?? 'Not initialized'} currently → {preview.counts.transactions} in backup{preview.counts.active_transactions !== undefined && ' (' + preview.counts.active_transactions + ' active)'}</dd></div>
        {preview.counts.holdings !== undefined && <><div><dt>Investment holdings</dt><dd>{preview.current_counts?.holdings ?? 'Not initialized'} currently → {preview.counts.holdings} in backup</dd></div><div><dt>Investment activity</dt><dd>{preview.current_counts?.investment_activity ?? 'Not initialized'} currently → {preview.counts.investment_activity} in backup</dd></div></>}
        <div><dt>Chart selection</dt><dd>{preview.restores_chart_selection ? 'Restored with this workspace' : 'Not included; current selection retained'}</dd></div></dl>
      <details className="backup-contents"><summary>Accounts and activity in this backup</summary><p className="analytics-note">{preview.contents.transaction_dates.first ? 'Transaction dates: ' + preview.contents.transaction_dates.first + ' – ' + preview.contents.transaction_dates.last : 'No saved transactions.'}</p>
        <ul>{preview.contents.accounts.map((account, index) => <li key={index}>{account.name} · {account.currency ?? 'Currency unavailable'}</li>)}</ul>
        {preview.counts.accounts > 8 && <p className="muted">First 8 of {preview.counts.accounts} accounts shown.</p>}</details>
      <p className="notice">Restoring replaces this workspace’s saved data. A validated recovery backup of the current workspace is created first.</p>
      <p className="analytics-note">{policy} Pending authorization sessions are not restored.</p>
      <p className="analytics-note">Finish any open bank authorization and close other Finance instances before restoring. No bank sync runs automatically.</p>
      <label className="restore-confirm-label">Type {preview.confirmation} to confirm<input aria-label="Restore confirmation" autoComplete="off" value={confirmation} disabled={busy} onChange={event => setConfirmation(event.target.value)} /></label>
      {error && <p className="notice" role="alert">{error}</p>}
      <div className="form-actions"><button className="secondary" disabled={busy} onClick={() => setPreview(undefined)}>Cancel</button><button className="danger" disabled={busy || confirmation !== preview.confirmation} onClick={() => void run(async () => {
        const restored = await request<{ workspace: string; recovery_backup: string }>('/backups/restore', 'POST', { id: preview.id, preview_token: preview.preview_token, confirmation })
        setPreview(undefined); setResult(restored); setAttempt(value => value + 1); onRestored()
      })}>Restore workspace</button></div>
    </dialog>}
  </div>
}
