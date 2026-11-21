import {useCallback, useEffect, useRef, useState, type SetStateAction} from 'react'
import {errorMessage} from '../../errorPresentation'

type LibraryResource<T> = {
  items: T[]
  loading: boolean
  error: string
  lastSyncedAt: number | null
}

// Each read owns its success history. A failed refresh never replaces cached items
// or their timestamp, and a later request/unmount invalidates earlier responses.
export function useLibraryResource<T>(load: () => Promise<T[]>, failureMessage: string) {
  const [state, setState] = useState<LibraryResource<T>>({
    items: [], loading: true, error: '', lastSyncedAt: null,
  })
  const requestId = useRef(0)
  const reload = useCallback(async () => {
    const currentRequest = ++requestId.current
    setState((current) => ({...current, loading: true}))
    try {
      const items = await load()
      if (currentRequest !== requestId.current) return
      setState({items, loading: false, error: '', lastSyncedAt: Date.now()})
    } catch (cause) {
      if (currentRequest !== requestId.current) return
      const serverUnavailable = cause && typeof cause === 'object' && 'status' in cause && Number(cause.status) >= 500
      const error = serverUnavailable ? `${failureMessage}，服务暂时没有正常回应` : errorMessage(cause, failureMessage)
      setState((current) => ({...current, loading: false, error: error.replace(/[。.!]+$/, '')}))
    }
  }, [failureMessage, load])

  useEffect(() => {
    const initialRead = setTimeout(() => void reload(), 0)
    return () => { clearTimeout(initialRead); requestId.current += 1 }
  }, [reload])

  const setItems = useCallback((update: SetStateAction<T[]>) => {
    setState((current) => ({...current, items: typeof update === 'function' ? update(current.items) : update}))
  }, [])

  return {...state, hasLoadedOnce: state.lastSyncedAt !== null, reload, setItems}
}
