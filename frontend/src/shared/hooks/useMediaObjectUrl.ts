import { useEffect, useState } from 'react'
import { errorMessage } from '../../errorPresentation'

/** Own each authenticated blob URL until cleanup, including late completions. */
export function useMediaObjectUrl(source: string, load: (source: string) => Promise<string>, fallback: string, enabled = true) {
  const [result, setResult] = useState<{ source: string; url: string; error: string } | null>(null)
  if (result && (result.source !== source || !enabled)) setResult(null)
  useEffect(() => {
    if (!enabled) return
    let active = true
    let loaded = ''
    void load(source).then((url) => {
      if (!active) {
        URL.revokeObjectURL(url)
        return
      }
      loaded = url
      setResult({ source, url, error: '' })
    }).catch((cause) => {
      if (active) setResult({ source, url: '', error: errorMessage(cause, fallback) })
    })
    return () => {
      active = false
      if (loaded) URL.revokeObjectURL(loaded)
    }
  }, [enabled, fallback, load, source])
  // Hide the previous source synchronously, before the replacement effect runs.
  return enabled && result?.source === source ? result : { url: '', error: '' }
}
