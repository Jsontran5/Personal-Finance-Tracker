import type { ReactNode } from 'react'
import {
  SegmentedControl,
  SegmentedControlIndicator,
  SegmentedControlItem,
} from '@ui-sdk/react/segmented-control'

export function ViewSelector<T extends string>({ label, value, onChange, options, className }: {
  label: string
  value: T
  onChange: (value: T) => void
  options: readonly { value: T; label: ReactNode }[]
  className?: string
}) {
  return <SegmentedControl<T>
    aria-label={label}
    className={className ? 'segmented view-selector ' + className : 'segmented view-selector'}
    value={value}
    onValueChange={next => { if (next !== null) onChange(next) }}
  >
    {options.map(option => <SegmentedControlItem key={option.value} value={option.value}>{option.label}</SegmentedControlItem>)}
    <SegmentedControlIndicator />
  </SegmentedControl>
}
