export type GroupAccount = { kind?: string; provider_type?: string; subtype?: string | null }
export const accountGroups = ['Cash & savings', 'Investments & retirement', 'Health savings', 'Credit cards', 'Loans & credit lines', 'Other assets'] as const

export function accountGroup(account: GroupAccount): string {
  if (account.subtype === 'hsa') return 'Health savings'
  const type = account.provider_type ?? account.kind
  if (type === 'credit') return 'Credit cards'
  if (type === 'loan') return 'Loans & credit lines'
  if (type === 'investment') return 'Investments & retirement'
  if (['depository', 'cash', 'checking', 'savings'].includes(type ?? '')) return 'Cash & savings'
  return 'Other assets'
}

const labels: Record<string, string> = {
  depository: 'Cash account · subtype not yet synced', checking: 'Checking', savings: 'Savings', cash: 'Cash',
  cd: 'Certificate of deposit', 'money market': 'Money market', 'cash management': 'Cash management',
  investment: 'Investment', ira: 'IRA', 'roth': 'Roth IRA', '401k': '401(k)', '403B': '403(b)', hsa: 'HSA',
  credit: 'Credit card', 'credit card': 'Credit card', 'business credit card': 'Business credit card',
  loan: 'Loan', student: 'Student loan', mortgage: 'Mortgage', auto: 'Auto loan',
  'home equity': 'Home equity line of credit', property: 'Property', other: 'Other asset',
}
export function accountLabel(account: GroupAccount): string {
  const value = account.subtype || account.kind || account.provider_type || 'other'
  return labels[value] ?? value.replaceAll('_', ' ').replace(/^./, char => char.toUpperCase())
}
export function groupedAccounts<T extends GroupAccount>(accounts: T[]) {
  return accountGroups.map(name => ({ name, accounts: accounts.filter(account => accountGroup(account) === name) }))
    .filter(group => group.accounts.length)
}
