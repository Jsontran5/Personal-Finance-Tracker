import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

export function DeleteTransaction({ transaction, account, destination, amount, close, remove }: {
  transaction: { merchant: string; date: string }; account: string; destination?: string; amount: string;
  close: () => void; remove: () => Promise<void>
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const cancel = useRef<HTMLButtonElement>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { dialog.current?.showModal(); cancel.current?.focus() }, [])
  async function confirm() {
    if (!acknowledged || busy) return
    setBusy(true); setError('')
    try { await remove() }
    catch (failure) { setError((failure as Error).message); setBusy(false) }
  }
  // A separate DOM dialog avoids implicit submission of the background edit form.
  return createPortal(<dialog ref={dialog} className="delete-transaction-dialog" aria-labelledby="delete-transaction-title" aria-describedby="delete-transaction-warning" onCancel={event => {
    event.preventDefault(); event.stopPropagation(); if (!busy) close()
  }}>
    <div className="dialog-heading"><h2 id="delete-transaction-title">Delete transaction?</h2></div>
    <p className="muted">Saved transaction</p>
    <dl className="delete-summary"><div><dt>Merchant or source</dt><dd>{transaction.merchant}</dd></div>
      <div><dt>Amount</dt><dd className="numeric">{amount}</dd></div><div><dt>Date</dt><dd>{transaction.date}</dd></div>
      <div><dt>Account</dt><dd>{account}{destination && ' → ' + destination}</dd></div></dl>
    <p className="notice" id="delete-transaction-warning">This permanently deletes the transaction. There is no Undo.</p>
    <label className="checkbox delete-acknowledgement"><input type="checkbox" checked={acknowledged} disabled={busy} onChange={event => setAcknowledged(event.target.checked)} />I understand this permanently deletes the transaction.</label>
    {error && <p className="notice" role="alert">{error}</p>}
    <div className="form-actions"><button ref={cancel} type="button" className="secondary" disabled={busy} onClick={close}>Cancel</button>
      <button type="button" className="danger" disabled={!acknowledged || busy} onClick={() => void confirm()}>{busy ? 'Deleting…' : 'Delete transaction'}</button></div>
  </dialog>, document.body)
}
