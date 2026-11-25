/** Account additions expand tracking from their first snapshot, never retroactively. */
export function recordedHistory<Id extends string | number>(
  history: { date: string; accounts: { account_id: Id; balance_minor?: number | null; value_minor?: number | null; saved_balance_minor?: number | null }[] }[],
  selected: Id[], measure: 'net' | 'assets' | 'debt' = 'net',
) {
  const firstSeen = new Map<Id, string>()
  for (const point of history) for (const account of point.accounts) {
    if (!firstSeen.has(account.account_id) || point.date < firstSeen.get(account.account_id)!) firstSeen.set(account.account_id, point.date)
  }
  return history.map(point => {
    const active = selected.filter(id => firstSeen.has(id) && firstSeen.get(id)! <= point.date)
    const values = new Map(point.accounts.map(account => [account.account_id, 'balance_minor' in account ? account.balance_minor : account.value_minor]))
    const additions = selected.some(id => firstSeen.has(id) && firstSeen.get(id)! < point.date)
      ? active.filter(id => firstSeen.get(id) === point.date).length : 0
    // A tracking-change snapshot already stores its peers' cached balances. Use
    // those captured values for this new point, never as earlier observations.
    let cached = false
    if (additions) for (const account of point.accounts) {
      if (active.includes(account.account_id) && values.get(account.account_id) == null && account.saved_balance_minor != null) {
        values.set(account.account_id, account.saved_balance_minor)
        cached = true
      }
    }
    return {
      date: point.date,
      value_minor: active.length && active.every(id => values.get(id) != null)
        ? active.reduce((sum, id) => { const value = values.get(id)!; return sum + (measure === 'assets' ? Math.max(value, 0) : measure === 'debt' ? Math.max(-value, 0) : value) }, 0)
        : null,
      note: additions ? `${additions} ${additions === 1 ? 'account added' : 'accounts added'} to tracking${cached ? ' · includes cached balances saved with this snapshot' : ''}` : undefined,
    }
  })
}
