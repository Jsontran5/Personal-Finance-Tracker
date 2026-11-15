import { useEffect, useState } from 'react'

type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>
export function SyncFreshness({ request, revision, open }: { request: Request; revision: number; open: () => void }) {
  const [status, setStatus] = useState<{ synced_at: string | null; needs_reauth: number } | null>()
  const [error, setError] = useState(false)
  const [combined, setCombined] = useState<string>()
  useEffect(() => {
    const controller = new AbortController()
    setStatus(undefined); setCombined(undefined)
    request<{ connection?: { synced_at: string | null; needs_reauth: number } | null; connections?: { connected: number; needs_reauth: number }[] }>('/plaid/sandbox', 'GET', undefined, controller.signal)
      .then(result => {
        setStatus(result.connection ?? null); setError(false)
        if (result.connections) {
          const connected = result.connections.filter(item => item.connected).length
          const attention = result.connections.filter(item => item.needs_reauth).length
          setCombined(attention ? `${attention} institutions need reconnection` : connected ? `${connected} institutions · View sync status` : 'No test institutions connected')
        }
      })
      .catch(() => { if (!controller.signal.aborted) setError(true) })
    return () => controller.abort()
  }, [request, revision])
  const label = error ? 'Sync status unavailable' : combined ?? (status === undefined ? 'Checking sync status…' : status === null ? 'No test bank connected'
    : status.needs_reauth ? 'Bank reconnection needed' : status.synced_at ? 'Last sync ' + new Date(status.synced_at).toLocaleString() : 'Connected · not synced yet')
  return <button className="sync-freshness" onClick={open} title={label}>{label}</button>
}
