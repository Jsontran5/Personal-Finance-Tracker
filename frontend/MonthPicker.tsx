import { useId, useState } from 'react'
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from '@ui-sdk/react/popover'
import { Icon } from './icons'

const shortName = new Intl.DateTimeFormat('en-US', { month: 'short', timeZone: 'UTC' })
const longName = new Intl.DateTimeFormat('en-US', { month: 'long', timeZone: 'UTC' })
const months = Array.from({ length: 12 }, (_, index) => {
  const date = new Date(Date.UTC(2000, index, 1))
  return { short: shortName.format(date), long: longName.format(date) }
})

function monthKey(year: number, month: number) {
  return String(year).padStart(4, '0') + '-' + String(month).padStart(2, '0')
}

export function previousMonth(value: string) {
  const [year, month] = value.split('-').map(Number)
  return month === 1 ? monthKey(year - 1, 12) : monthKey(year, month - 1)
}

export function MonthPicker({ label, value, onChange, disabled, max = '9999-12' }: {
  label: string
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  max?: string
}) {
  const [valueYear, valueMonth] = value.split('-').map(Number)
  const [open, setOpen] = useState(false)
  const [year, setYear] = useState(valueYear)
  const [draft, setDraft] = useState(value)
  const currentMonth = useId()
  const formatted = months[valueMonth - 1].long + ' ' + valueYear
  const draftValid = /^(?!0000)[0-9]{4}-(0[1-9]|1[0-2])$/.test(draft) && draft <= max

  function select(next: string) {
    onChange(next)
    setOpen(false)
  }

  return <Popover open={open && !disabled} onOpenChange={next => {
    if (next) { setYear(valueYear); setDraft(value) }
    setOpen(next)
  }}>
    <PopoverTrigger className="month-picker-trigger" aria-label={label} aria-describedby={currentMonth} data-month={value} disabled={disabled}>
      <span className="month-picker-icon"><Icon name="calendar" /></span>
      <span className="month-picker-caption"><small>{label}</small><strong id={currentMonth}>{formatted}</strong></span>
      <Icon name="chevronDown" />
    </PopoverTrigger>
    <PopoverContent className="month-picker-panel" align="end" sideOffset={8} initialFocus='[aria-current="date"]'>
      <PopoverTitle>{label}</PopoverTitle>
      <div className="month-picker-year">
        <button type="button" className="secondary" aria-label="Previous year" disabled={year <= 1} onClick={() => setYear(year - 1)}><Icon name="chevronLeft" /></button>
        <strong className="numeric" aria-live="polite">{year}</strong>
        <button type="button" className="secondary" aria-label="Next year" disabled={year >= Number(max.slice(0, 4))} onClick={() => setYear(year + 1)}><Icon name="chevronRight" /></button>
      </div>
      <div className="month-picker-grid">
        {months.map((name, index) => {
          const key = monthKey(year, index + 1)
          return <button key={name.short} type="button" className="month-picker-cell" aria-label={name.long + ' ' + year}
            aria-current={key === value ? 'date' : undefined} disabled={key > max} onClick={() => select(key)}>{name.short}</button>
        })}
      </div>
      <form className="month-picker-jump" onSubmit={event => {
        event.preventDefault()
        if (draftValid) select(draft)
      }}>
        <label>Jump to month<input type="month" aria-label={'Jump to ' + label.toLowerCase()} min="0001-01" max={max}
          value={draft} onChange={event => setDraft(event.target.value)} required /></label>
        <button type="submit" className="secondary small" disabled={!draftValid}>Go</button>
      </form>
    </PopoverContent>
  </Popover>
}
