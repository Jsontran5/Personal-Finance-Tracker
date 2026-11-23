import { Meter, MeterIndicator, MeterTrack } from '@ui-sdk/react/meter'

export function FinanceMeter({ label, value, max, over = false }: {
  label: string
  value: number
  max: number
  over?: boolean
}) {
  return <Meter aria-label={label} className="finance-meter" value={value} max={max} data-over={over || undefined} render={<span />}>
    <MeterTrack className="finance-meter-track" render={<span />}><MeterIndicator className="finance-meter-fill" render={<span />} /></MeterTrack>
  </Meter>
}
