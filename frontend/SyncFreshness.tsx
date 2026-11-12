import { useEffect, useState } from 'react'

type Request = <T>(path: string, method?: string, body?: object, signal?: AbortSignal) => Promise<T>
export function SyncFreshness({ request, revision, open }: { request: Request; revision: number; open: () => void }) {
  const [status, setStatus] = useState<{ synced_at: string | null; needs_reauth: number } | null>()
  const [error, setError] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    request<{ connection: { synced_at: string | null; needs_reauth: number } | null }>('/plaid/sandbox', 'GET', undefined, controller.signal)
      .then(result => { setStatus(result.connection); setError(false) })
      .catch(() => { if (!controller.signal.aborted) setError(true) })
    return () => controller.abort()
  }, [request, revision])
  const label = error ? 'Sync status unavailable' : status === undefined ? 'Checking sync status…' : status === null ? 'No test bank connected'
    : status.needs_reauth ? 'Bank reconnection needed' : status.synced_at ? 'Last sync ' + new Date(status.synced_at).toLocaleString() : 'Connected · not synced yet'
  return <button className="sync-freshness" onClick={open} title={label}>{label}</button>
}
