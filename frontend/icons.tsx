const paths = {
  logo: 'M3 16l5-5 4 4 8-8m-6 0h6v6',
  overview: 'M3 3h7v9H3z M14 3h7v5h-7z M3 16h7v5H3z M14 12h7v9h-7z',
  transactions: 'M4 7h15m-4-4 4 4-4 4M20 17H5m4-4-4 4 4 4',
  budgets: 'M11 3a9 9 0 1 0 10 10H11z M15 3v6h6a7 7 0 0 0-6-6z',
  accounts: 'M3 10h18L12 4z M5 10v8m5-8v8m4-8v8m5-8v8M3 20h18',
  plus: 'M12 5v14M5 12h14',
  right: 'M5 12h14m-5-5 5 5-5 5',
  income: 'M17 7 7 17m0-8v8h8',
  expense: 'M7 17 17 7m-8 0h8v8',
  sun: 'M12 4V2m0 20v-2M4 12H2m20 0h-2M5.6 5.6 4.2 4.2m15.6 15.6-1.4-1.4M5.6 18.4l-1.4 1.4m15.6-15.6-1.4 1.4 M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  moon: 'M20 15a8.5 8.5 0 0 1-11-11 8.5 8.5 0 1 0 11 11z',
} as const

export function Icon({ name }: { name: keyof typeof paths }) {
  return <svg className="glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={paths[name]} />
  </svg>
}
