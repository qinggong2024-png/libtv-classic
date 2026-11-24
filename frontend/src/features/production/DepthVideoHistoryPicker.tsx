import { LoaderCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { fetchAssetFile, getTaskPage } from '../../api'
import { errorMessage } from '../../errorPresentation'
import type { Task } from '../../types'
import { FoundationDrawer } from '../../shared/components/ui'
import { VideoThumbnail } from '../../shared/components/VideoThumbnail'
import { depthVideoHistoryEntries } from './depthVideoHistory'

export function DepthVideoHistoryPicker({ onSelect, onClose }: {
  onSelect: (file: File) => void
  onClose: () => void
}) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState<string | null>(null)
  const [error, setError] = useState('')
  const mounted = useRef(false)
  const importingRef = useRef(false)
  useEffect(() => {
    mounted.current = true
    void getTaskPage().then((page) => {
      if (mounted.current) { setTasks(page.items); setCursor(page.nextCursor) }
    }).catch((cause) => { if (mounted.current) setError(errorMessage(cause, '历史结果读取失败')) })
      .finally(() => { if (mounted.current) setLoading(false) })
    return () => { mounted.current = false }
  }, [])
  const loadMore = async () => {
    if (loading) return
    setLoading(true)
    setError('')
    try {
      const page = await getTaskPage(cursor ?? undefined)
      if (mounted.current) {
        setTasks((current) => [...current, ...page.items.filter((task) => !current.some((item) => item.id === task.id))])
        setCursor(page.nextCursor)
      }
    } catch (cause) { if (mounted.current) setError(errorMessage(cause, '历史结果读取失败')) }
    finally { if (mounted.current) setLoading(false) }
  }
  const choose = async (id: string, title: string) => {
    if (importingRef.current) return
    importingRef.current = true
    setImporting(id)
    setError('')
    try {
      const file = await fetchAssetFile(`/api/v1/downloads/${id}`, `${title}-depth.mp4`)
      if (mounted.current) onSelect(file)
    } catch (cause) { if (mounted.current) setError(errorMessage(cause, '无法读取深度视频，文件可能已失效，请重新选择。')) }
    finally { importingRef.current = false; if (mounted.current) setImporting(null) }
  }
  const entries = depthVideoHistoryEntries(tasks)
  return <FoundationDrawer open className="creation-drawer" title="选择已有深度视频" onClose={onClose}>

    {error ? <p className="inline-alert inline-alert--danger" role="alert">{error}<button className="text-button" disabled={loading || Boolean(importing)} onClick={() => void loadMore()}>重试读取</button></p> : null}
    <div className="depth-history-list">{entries.map(({ task, downloadId }) => <button type="button" className="depth-history-item" key={task.id} disabled={Boolean(importing)} onClick={() => void choose(downloadId, task.title)}>
      <span><VideoThumbnail artifactId={downloadId} alt="深度视频封面" /></span>
      <span><strong>{task.title}</strong><small>{String(task.settings?.durationSeconds ?? '—')} 秒 · {importing === downloadId ? '正在读取视频…' : '选用此深度视频'}</small></span>
    </button>)}</div>
    {loading ? <p role="status"><LoaderCircle className="spin" size={17} />正在读取历史结果</p> : !entries.length && !error ? <div className="image-history-empty">暂无可用的深度视频</div> : null}
    {cursor ? <button className="secondary-button" disabled={loading || Boolean(importing)} onClick={() => void loadMore()}>加载更多</button> : null}
  </FoundationDrawer>
}
