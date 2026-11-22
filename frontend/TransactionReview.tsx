import { trapDialogFocus } from './dialogFocus'
import { useEffect, useRef, useState } from 'react'

type Kind = 'expense' | 'income' | 'refund' | 'transfer' | 'investment'
type Tx = { id: number; merchant: string; source_merchant?: string; kind: Kind; account_id: number; category_id: number | null }
type Category = { id: number; name: string; kind: string; archived?:number }
type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>
type Rule = { id: number; merchant: string; kind: Kind; category_id: number | null; account_id: string | null; active: number }
type ReviewData = { rows: { id: number; review_status: string }[]; rules: Rule[]; items: Tx[] }
type Batch = { ids: number[]; action: { operation: string; kind?: Kind; category_id?: number | null; rule?: { merchant: string; account_id: number | null; match_kind: Kind; future: boolean } } }
type Preview = { preview_token: string; matched: number; applicable: number; rows: { id: number; merchant: string; date: string; account_name: string; kind: string; skip_reason: string | null }[] }
const labels: Record<Kind, string> = { expense: 'Expense', income: 'Income', refund: 'Refund', transfer: 'Transfer', investment: 'Investment activity' }

export function TransactionReview<T extends Tx>({ path, request, transactions, categories, accounts, selected, setSelected, pageIds, matchingIds, filter, setFilter, onStatuses, onHistory, allDates, setAllDates, complete }: {
  path: string; request: Request; transactions: Tx[]; categories: Category[]; accounts: { id: number; name: string }[];
  selected: number[]; setSelected: (ids: number[]) => void; pageIds: number[]; matchingIds: number[];
  filter: string; setFilter: (filter: string) => void; onStatuses: (values: Record<number, string>) => void; onHistory: (rows: T[]) => void;
  allDates: boolean; setAllDates: (all: boolean) => void; complete: (message: string) => void
}) {
  const [data, setData] = useState<ReviewData>()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState(false)
  const [operation, setOperation] = useState<string | null>(null)
  const [kind, setKind] = useState<Kind>('expense')
  const [category, setCategory] = useState(0)
  const [merchant, setMerchant] = useState('')
  const [account, setAccount] = useState(0)
  const [matchKind, setMatchKind] = useState<Kind>('expense')
  const [future, setFuture] = useState(false)
  const [preview, setPreview] = useState<{ result: Preview; batch: Batch }>()
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const controller = new AbortController()
    request<ReviewData>(path, 'GET', undefined, controller.signal).then(result => {
      setData(result); onStatuses(Object.fromEntries(result.rows.map(row => [row.id, row.review_status]))); onHistory(result.items as T[]); setError('')
    }).catch(failure => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [path, request, onStatuses, onHistory, attempt, transactions])
  useEffect(() => { if (operation) dialog.current?.showModal() }, [operation])
  function start(next: string) {
    const first = (allDates ? data?.items ?? [] : transactions).find(tx => selected.includes(tx.id))
    setKind(first?.kind ?? 'expense'); setCategory(first?.category_id ?? 0)
    setMerchant(first?.source_merchant ?? first?.merchant ?? ''); setMatchKind(first?.kind ?? 'expense')
    setAccount(first?.account_id ?? 0); setFuture(false); setPreview(undefined); setError(''); setOperation(next)
  }
  async function run(work: () => Promise<void>) {
    setBusy(true); setError('')
    try { await work() } catch (failure) { setError((failure as Error).message) } finally { setBusy(false) }
  }
  function makeBatch(): Batch {
    const action: Batch['action'] = { operation: operation === 'rule' ? 'classify' : operation! }
    if (action.operation === 'classify') {
      action.kind = kind; action.category_id = kind === 'transfer' || kind === 'investment' ? null : category || null
    }
    if (operation === 'rule') action.rule = { merchant, account_id: account || null, match_kind: matchKind, future }
    return { ids: operation === 'rule' ? [] : selected, action }
  }
  const statuses = new Map(data?.rows.map(row => [row.id, row.review_status]))
  const needsReview = (allDates ? data?.items ?? [] : transactions).filter(tx => statuses.get(tx.id) === 'needs_review').length
  return <div className="transaction-review">
    <div className="review-heading"><label>Review view<select aria-label="Review filter" value={filter} disabled={!data} onChange={event => setFilter(event.target.value)}>
      <option value="all">All activity</option><option value="needs_review">Needs review ({needsReview}{allDates ? ' across all dates' : ' this month'})</option><option value="uncategorized">Uncategorized</option><option value="reviewed">Reviewed</option>
    </select></label><label>Date scope<select aria-label="Review date scope" value={allDates ? 'all' : 'month'} disabled={!data} onChange={event => setAllDates(event.target.value === 'all')}><option value="month">Reporting month</option><option value="all">All saved dates</option></select></label><button className="secondary small" disabled={!data} onClick={() => start('rule')}>New merchant rule</button></div>
    <details className="review-help"><summary>How review works</summary><p className="analytics-note">Backfills do not require reviewing every purchase. Needs review contains ambiguous credits and entries you flag. Uncategorized is separate. Flagging a purchase does not remove it from spending.</p></details>
    {error && <p className="notice" role="alert">{error}{!data && <button className="secondary small" onClick={() => setAttempt(value => value + 1)}>Retry</button>}</p>}
    {data && <div className="review-selection"><div className="inline-actions"><button className="secondary small" onClick={() => setSelected([...new Set([...selected, ...pageIds])])} disabled={!pageIds.length}>Select page</button>
      <button className="secondary small" disabled={!matchingIds.length || matchingIds.length > 10000} onClick={() => setSelected(matchingIds)}>Select matching ({matchingIds.length})</button>
      <button className="secondary small" disabled={!selected.length} onClick={() => setSelected([])}>Clear selection</button><span className="muted" role="status">{selected.length} selected</span></div>
      {selected.length > 0 && <div className="bulk-actions"><button className="primary small" onClick={() => start('classify')}>Classify selected</button><button className="secondary small" onClick={() => start('review')}>Mark reviewed</button><button className="secondary small" onClick={() => start('flag')}>Flag for review</button></div>}
    </div>}
    {!!data?.rules.length && <details className="merchant-rules"><summary>Merchant rules · {data.rules.length}</summary><p className="analytics-note">Enabled rules apply on future imports. Disabling one keeps existing classifications. Exact merchant matches only; account-specific rules take priority.</p>
      <div className="rules-scroll">{data.rules.map(rule => <div className="rule-row" key={rule.id}><div><strong>{rule.merchant}</strong><small>{rule.account_id === null ? 'All accounts' : 'One account'} · {labels[rule.kind]} · {categories.find(item => item.id === rule.category_id)?.name ?? 'No category'}</small></div>
        <label className="checkbox"><input type="checkbox" aria-label={'Enable rule for ' + rule.merchant} checked={!!rule.active} disabled={busy} onChange={event => {
          const active = event.target.checked
          void run(async () => { await request(path + '/rules/' + rule.id, 'PUT', { active }); setAttempt(value => value + 1) })
        }} />Future imports</label></div>)}</div></details>}
    {operation && <dialog onKeyDown={trapDialogFocus} ref={dialog} aria-labelledby="review-title" onCancel={event => { event.preventDefault(); if (!busy) setOperation(null) }}>
      <div className="dialog-heading"><h2 id="review-title">{operation === 'rule' ? 'Merchant rule' : operation === 'classify' ? 'Classify selected transactions' : operation === 'review' ? 'Mark selected as reviewed' : 'Flag selected for review'}</h2><button type="button" className="icon" disabled={busy} aria-label="Close review dialog" onClick={() => setOperation(null)}>×</button></div>
      <form onSubmit={event => { event.preventDefault(); const batch = makeBatch(); void run(async () => { setPreview({ result: await request<Preview>(path + '/preview', 'POST', batch), batch }) }) }}>
        <fieldset disabled={busy} onChange={() => setPreview(undefined)}>
          {operation === 'rule' && <>
            <label>Exact imported merchant or description<input aria-label="Rule merchant" required maxLength={120} value={merchant} onChange={event => setMerchant(event.target.value)} /></label>
            <label>Account scope<select aria-label="Rule account scope" value={account} onChange={event => setAccount(Number(event.target.value))}><option value={0}>All accounts</option>{accounts.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
            <label>Match current type<select aria-label="Rule match type" value={matchKind} onChange={event => setMatchKind(event.target.value as Kind)}>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <p className="muted">Preview searches all saved dates in this workspace. Individual edits are preserved. An ambiguous credit appears as Refund until classified.</p>
          </>}
          {(operation === 'classify' || operation === 'rule') && <>
            <label>Set type<select aria-label="Bulk type" value={kind} onChange={event => { setKind(event.target.value as Kind); setCategory(0) }}>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            {kind !== 'transfer' && kind !== 'investment' && <label>Set category<select aria-label="Bulk category" value={category} onChange={event => setCategory(Number(event.target.value))}><option value={0}>Uncategorized</option>{categories.filter(item => !item.archived && item.kind === (kind === 'income' ? 'income' : 'expense')).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
          </>}
          {operation === 'rule' && <label className="checkbox"><input type="checkbox" aria-label="Apply rule to future imports" checked={future} onChange={event => setFuture(event.target.checked)} />Also apply to future imports</label>}
          {operation === 'review' && <p className="muted">This acknowledges transactions without assigning categories. Ambiguous credits must be classified first; pending entries are skipped.</p>}
          {operation === 'classify' && <p className="muted">Explicitly selected entries can replace existing classifications. Merchant names and notes are preserved.</p>}
          {operation === 'flag' && <p className="muted">Adds these posted entries to Needs review without changing their financial classification.</p>}
          <button type="submit" className="secondary">Preview changes</button>
        </fieldset>
      </form>
      {preview && <section className="review-preview"><h3>{preview.result.applicable} applicable · {preview.result.matched - preview.result.applicable} skipped</h3>
        <p className="muted">{preview.result.matched} matched{preview.result.matched > 50 ? ' · first 50 shown' : ''}. Changes apply only after confirmation.</p>
        <div className="preview-scroll">{preview.result.rows.map(row => <div className="preview-row" key={row.id}><strong>{row.merchant}</strong><small>{row.date} · {row.account_name}</small><span>{row.skip_reason ?? (operation === 'review' ? 'Mark reviewed' : operation === 'flag' ? 'Flag for review' : (labels[row.kind as Kind] ?? row.kind) + ' → ' + labels[preview.batch.action.kind!] + ' · ' + (categories.find(item => item.id === preview.batch.action.category_id)?.name ?? 'No category'))}</span></div>)}</div>
        {!preview.result.matched && <p className="empty">No existing matches. You can save a rule for future imports if enabled.</p>}
      </section>}
      {error && <p className="notice" role="alert">{error}</p>}
      <div className="form-actions"><button className="secondary" disabled={busy} onClick={() => setOperation(null)}>Cancel</button><button className="primary" disabled={busy || !preview || (!preview.result.applicable && !(operation === 'rule' && future))} onClick={() => void run(async () => {
        if (!preview) return
        const result = await request<{ applied: number; skipped: number }>(path + '/apply', 'POST', { ...preview.batch, preview_token: preview.result.preview_token })
        setOperation(null); setSelected([]); complete(`${result.applied} updated · ${result.skipped} skipped${operation === 'rule' ? ' · rule saved' : ''}.`)
      })}>{operation === 'rule' ? 'Apply preview & save rule' : 'Apply preview'}</button></div>
    </dialog>}
  </div>
}
