import { trapDialogFocus } from './dialogFocus'
import { useEffect, useRef, useState } from 'react'

type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>
type Leg = { id: number; account_name: string; merchant: string; date: string; currency: string; amount_minor: number; kind: string; category_name: string | null; notes: string; needs_review: number }
type Pair = { outgoing: Leg; incoming: Leg; kind: string; reasons: string[]; multiple_options?: boolean }
type Saved = { id: number; state: string; valid: boolean; pair: Pair | null; description: string | null }
type Data = { suggestions: Pair[]; saved: Saved[]; truncated: boolean }
type Preview = { pair: Pair; preview_token: string; effect: string }
type Money = (amount: number, currency?: string) => string

function Legs({ pair, money }: { pair: Pair; money: Money }) {
  return <div className="transfer-legs">{[pair.outgoing, pair.incoming].map((leg, index) => <div key={index}>
    <small>{index === 0 ? 'From' : 'To'}</small><strong>{leg.account_name}</strong><span>{leg.merchant}</span>
    <small>{leg.date} · {leg.needs_review ? 'Unclassified credit' : leg.kind}{leg.category_name && ' · ' + leg.category_name}</small>
    <strong className="numeric">{index === 0 ? '−' : '+'}{money(leg.amount_minor, leg.currency)}</strong>
  </div>)}</div>
}

export function TransferReview({ path, request, money, revision, complete, focusId, setFocusId }: {
  path: string; request: Request; money: Money; revision: unknown; complete: (message: string) => void;
  focusId: number | null; setFocusId: (value: number | null) => void
}) {
  const [data, setData] = useState<Data>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [view, setView] = useState('suggestions')
  const [page, setPage] = useState(0)
  const [preview, setPreview] = useState<Preview>()
  const [removing, setRemoving] = useState<Saved>()
  const panel = useRef<HTMLDetailsElement>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const controller = new AbortController()
    request<Data>(path, 'GET', undefined, controller.signal).then(result => { setData(result); setError('') })
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [path, request, attempt, revision])
  useEffect(() => { if (preview || removing) dialog.current?.showModal() }, [preview, removing])
  useEffect(() => {
    if (focusId === null) return
    if (panel.current) panel.current.open = true
    setView('linked')
    if (data) {
      const index = data.saved.filter(item => item.state === 'confirmed').findIndex(item => item.id === focusId)
      setPage(Math.max(0, Math.floor(index / 8)))
      panel.current?.scrollIntoView({ block: 'start' }); setFocusId(null)
    }
  }, [focusId, data, setFocusId])
  async function run(work: () => Promise<void>) {
    setBusy(true); setError('')
    try { await work() } catch (failure) { setError((failure as Error).message) } finally { setBusy(false) }
  }
  const saved = data?.saved.filter(item => item.state === (view === 'linked' ? 'confirmed' : 'dismissed')) ?? []
  const count = view === 'suggestions' ? data?.suggestions.length ?? 0 : saved.length
  const currentPage = Math.min(page, Math.max(0, Math.ceil(count / 8) - 1))
  const shown = (view === 'suggestions' ? data?.suggestions ?? [] : saved).slice(currentPage * 8, (currentPage + 1) * 8)
  const needsAttention = data?.saved.filter(item => item.state === 'confirmed' && !item.valid).length ?? 0
  function close() { setPreview(undefined); setRemoving(undefined) }
  return <details className="transfer-panel" ref={panel}>
    <summary>Transfer &amp; card-payment matches {data && <span className="subtle">· {data.suggestions.length}{data.truncated ? '+' : ''} suggestions · {data.saved.filter(item => item.state === 'confirmed').length} linked{needsAttention > 0 ? ' · ' + needsAttention + ' need attention' : ''}</span>}</summary>
    <p className="analytics-note">Suggestions require equal amounts/currencies, different accounts, posted dates within five days, and a transfer/payment clue. These are clues, not proof. Nothing is linked automatically. Loan accounts are excluded because payments may include interest.</p>
    <div className="segmented" aria-label="Transfer match view">{['suggestions', 'linked', 'dismissed'].map(value => <button key={value} aria-pressed={view === value} onClick={() => { setView(value); setPage(0) }}>{value[0].toUpperCase() + value.slice(1)}</button>)}</div>
    {error && <p className="notice" role="alert">{error}<button className="secondary small" onClick={() => setAttempt(value => value + 1)}>Retry</button></p>}
    {!data && !error && <p className="muted" role="status">Checking saved transactions…</p>}
    {data && !count && <p className="empty">{view === 'suggestions' ? 'No matching posted pairs found. A missing counterpart, different amount, or date gap may explain it. You can still classify an individual transaction.' : 'No ' + view + ' pairs.'}</p>}
    <div className="transfer-match-list">{shown.map(entry => {
      const item = view === 'suggestions' ? null : entry as Saved
      const pair = item ? item.pair : entry as Pair
      return <article className="transfer-match" data-outgoing-id={pair?.outgoing.id} data-incoming-id={pair?.incoming.id} key={item?.id ?? (pair!.outgoing.id + '-' + pair!.incoming.id)}>
        <div className="section-heading"><h3>{item?.state === 'confirmed' ? pair?.kind === 'card_payment' ? 'Card payment' : 'Account transfer' : pair?.kind === 'card_payment' ? 'Possible card payment' : 'Possible account transfer'}{item?.state === 'confirmed' && (item.valid ? ' · Linked' : ' · Needs attention')}</h3></div>
        {pair && <Legs pair={pair} money={money} />}
        {view === 'suggestions' && pair && <><p className="analytics-note">{pair.reasons.join(' · ')}</p>{pair.multiple_options && <p className="notice">More than one possible counterpart. Compare each account and description before linking.</p>}</>}
        {item?.description && <p className="notice">{item.description}</p>}
        {!pair && <p className="notice">A source entry is unavailable. Remove this link to release the remaining entry.</p>}
        <div className="inline-actions transfer-match-actions">{view === 'suggestions' && pair ? <>
          <button className="primary small" disabled={busy} onClick={() => void run(async () => { setPreview(await request<Preview>(path + '/preview', 'POST', { outgoing_id: pair.outgoing.id, incoming_id: pair.incoming.id })) })}>Review pair</button>
          <button className="secondary small" disabled={busy} onClick={() => void run(async () => { await request(path + '/dismiss', 'POST', { outgoing_id: pair.outgoing.id, incoming_id: pair.incoming.id }); setAttempt(value => value + 1) })}>Dismiss</button>
        </> : <button className="secondary small" disabled={busy} onClick={() => setRemoving(item!)}>{view === 'linked' ? 'Unlink pair' : 'Restore suggestion'}</button>}</div>
      </article>
    })}</div>
    {count > 8 && <div className="pagination"><span className="muted">{currentPage * 8 + 1}–{Math.min(count, (currentPage + 1) * 8)} of {count}{data?.truncated && view === 'suggestions' ? '+ · showing recent matches' : ''}</span><div className="inline-actions"><button className="secondary small" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>Previous matches</button><button className="secondary small" disabled={(currentPage + 1) * 8 >= count} onClick={() => setPage(currentPage + 1)}>Next matches</button></div></div>}
    {(preview || removing) && <dialog onKeyDown={trapDialogFocus} ref={dialog} aria-labelledby="transfer-dialog-title" onCancel={event => { event.preventDefault(); if (!busy) close() }}>
      <div className="dialog-heading"><h2 id="transfer-dialog-title">{removing ? removing.state === 'confirmed' ? 'Unlink this pair?' : 'Restore suggestion?' : 'Confirm transfer pair'}</h2><button className="icon" aria-label="Close transfer preview" disabled={busy} onClick={close}>×</button></div>
      {preview && <><Legs pair={preview.pair} money={money} /><p className="notice">{preview.effect}</p><p className="muted">Only confirm if both entries describe the same movement between your accounts. Two entries with equal amounts may be unrelated.</p>
        <details className="transfer-notes"><summary>Saved notes</summary>{[preview.pair.outgoing, preview.pair.incoming].map((leg, index) => <p className="muted" key={index}>{leg.account_name}: {leg.notes || 'No notes'}</p>)}</details></>}
      {removing && <p className="notice">{removing.state === 'confirmed' ? 'Removes the reporting exclusion. The original transaction types and categories will determine income/spending again. No transactions or balances are deleted.' : 'Allows this pair to appear in suggestions again. No financial records change.'}</p>}
      {error && <p className="notice" role="alert">{error}</p>}
      <div className="form-actions"><button className="secondary" disabled={busy} onClick={close}>Cancel</button><button className="primary" disabled={busy} onClick={() => void run(async () => {
        if (removing) await request(path + '/' + removing.id, 'DELETE')
        else if (preview) await request(path + '/confirm', 'POST', { outgoing_id: preview.pair.outgoing.id, incoming_id: preview.pair.incoming.id, preview_token: preview.preview_token })
        const message = removing ? removing.state === 'confirmed' ? 'Pair unlinked; normal reporting restored.' : 'Suggestion restored.' : 'Pair linked; both entries excluded from income and spending.'
        close(); setAttempt(value => value + 1); complete(message)
      })}>{removing ? removing.state === 'confirmed' ? 'Unlink and restore reporting' : 'Restore suggestion' : 'Confirm link'}</button></div>
    </dialog>}
  </details>
}
