export type Flow = { direction: 'in' | 'out' | 'none' | 'unknown'; from: string; to: string; basis: 'confirmed' | 'manual' | 'reported' | 'description' | 'unavailable' }

export function TransactionFlow({ flow }: { flow: Flow }) {
  return <span className="transaction-flow" data-help="From and To use confirmed account pairs first, then supplied bank counterparties or explicit direction descriptions. A bank-reported name is not a confirmed link to one of your accounts. In/Out and the amount sign describe direction, not income/spending classification.">
    <span className="flow-endpoint"><b>From</b><span>{flow.from}</span></span>
    <span className="flow-arrow" aria-hidden="true">→</span>
    <span className="flow-endpoint"><b>To</b><span>{flow.to}</span></span>
    {flow.basis !== 'confirmed' && flow.basis !== 'manual' && <span className="flow-source">{flow.basis === 'reported' ? 'Bank-reported counterparty' : flow.basis === 'description' ? 'Bank description · other account not confirmed' : 'Other side not provided by bank'}</span>}
  </span>
}
