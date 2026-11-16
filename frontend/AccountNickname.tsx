import { useState } from 'react'
import type { Account } from './AccountList'

type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>

export function AccountNickname({ account, request, onChanged }: { account: Account; request: Request; onChanged: () => void }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(account.nickname ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  async function save(nickname: string | null) {
    setBusy(true); setError(''); setMessage('')
    try {
      await request('/plaid/sandbox/preview/accounts/' + account.id + '/name', 'PUT', { nickname })
      setEditing(false); setMessage(nickname ? 'Local name saved.' : 'Bank name restored.'); onChanged()
    } catch (failure) { setError((failure as Error).message) }
    finally { setBusy(false) }
  }
  return <section className="account-nickname">
    {account.nickname && <p className="muted">Bank name: {account.provider_name}</p>}
    {!editing ? <button type="button" className="secondary small" onClick={() => { setValue(account.nickname ?? ''); setEditing(true); setError(''); setMessage('') }}>Rename account</button> : <form onSubmit={event => { event.preventDefault(); void save(value.trim() || null) }}>
      <label>Account nickname<input autoFocus aria-label="Account nickname" value={value} maxLength={120} disabled={busy} placeholder={account.provider_name ?? account.name} onChange={event => setValue(event.target.value)} /></label>
      <p className="muted">Only changes the name in this workspace. Bank name: {account.provider_name ?? account.name}. Leave blank to use the bank name.</p>
      <div className="inline-actions"><button className="primary small" disabled={busy} type="submit">Save nickname</button>
        {account.nickname && <button className="secondary small" type="button" disabled={busy} onClick={() => void save(null)}>Use bank name</button>}
        <button className="secondary small" type="button" disabled={busy} onClick={() => { setEditing(false); setError('') }}>Cancel</button></div>
    </form>}
    {error && <p className="notice" role="alert">{error}</p>}
    {message && <p className="muted" role="status">{message}</p>}
  </section>
}
