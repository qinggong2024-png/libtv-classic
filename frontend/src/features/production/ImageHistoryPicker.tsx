import { errorMessage } from '../../errorPresentation'
import { Check, Images, LoaderCircle, RefreshCw } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { ApiError } from '../../api'
import { loadImageCenterDraft } from '../../imageCenterStore'
import { mergeImageHistory } from '../../imageCenterHistory'
import { useImageHistory } from '../../useImageHistory'
import type { ImageGenerationTask } from '../../imageCenterModel'
import { AuthenticatedImage, LocalImage } from '../../shared/components/Media'
import { FoundationDrawer } from '../../shared/components/ui'
import { imageHistoryEntries, prepareHistoryImages, type ImageHistoryEntry } from './imageHistory'
import type { Material } from './productionTypes'

export function ImageHistoryPicker({ scope, remaining, addedOutputIds, onAdd, onClose }: {
  scope: string | null
  remaining: number
  addedOutputIds: string[]
  onAdd: (images: Material[]) => void
  onClose: () => void
}) {
  const [localTasks, setLocalTasks] = useState<ImageGenerationTask[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const history = useImageHistory(scope)
  const entries = useMemo(() => imageHistoryEntries(mergeImageHistory(localTasks, history.batches)), [localTasks, history.batches])
  const mounted = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  useEffect(() => {
    let cancelled = false
    void loadImageCenterDraft(scope).then((draft) => {
      if (!cancelled) setLocalTasks(draft?.tasks ?? [])
    }).catch(() => {
      if (!cancelled) setError('无法读取生图记录，请重试。')
    }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [scope, revision])

  async function confirm() {
    if (importing || !selectedIds.length || selectedIds.length > remaining) return
    setImporting(true)
    setError('')
    try {
      const selected = selectedIds.map((id) => entries.find(({ output }) => output.id === id))
      if (selected.some((entry) => !entry)) throw new Error('生图记录已变化，请重新选择图片。')
      const images = await prepareHistoryImages(selected.filter((entry): entry is ImageHistoryEntry => Boolean(entry)))
      if (mounted.current) { onAdd(images); onClose() }
    } catch (cause) {
      if (mounted.current) setError(cause instanceof ApiError ? '无法读取所选图片，图片可能已失效或当前账号无权访问。请重新选择或重试。' : errorMessage(cause, '无法读取所选图片，请重试。'))
    } finally { if (mounted.current) setImporting(false) }
  }

  return <FoundationDrawer open title="从生图记录选择" eyebrow="目标人物 / 商品参考图" onClose={onClose} footer={<>
    <span className="image-history-selection" aria-live="polite">已选 {selectedIds.length}/{remaining} 张</span>
    <button type="button" className="secondary-button" onClick={onClose}>取消</button>
    <button type="button" className="primary-button" disabled={loading || history.loading || importing || !selectedIds.length} onClick={() => void confirm()}>{importing ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />}{importing ? '正在读取图片' : '添加参考图'}</button>
  </>}>
    <p className="image-history-intro">当前账号已保存的生图历史，可跨浏览器直接选择图片。按选择顺序追加，最多还能添加 {remaining} 张。</p>
    {history.error ? <div className="inline-alert inline-alert--danger" role="alert">{history.error}<button type="button" className="text-button" disabled={history.loading} onClick={history.refresh}><RefreshCw size={14} />重试同步</button></div> : null}
    {error ? <div className="inline-alert inline-alert--danger" role="alert">{error}<button type="button" className="text-button" disabled={importing} onClick={() => { setLoading(true); setError(''); setSelectedIds([]); setRevision((value) => value + 1) }}><RefreshCw size={14} />重新读取</button></div> : null}
    {loading || history.loading ? <p className="image-history-empty" role="status"><LoaderCircle size={22} className="spin" />正在读取生图记录</p> : !entries.length && !history.error ? <div className="image-history-empty"><Images size={28} /><strong>暂无可用的生图记录</strong><p>在图片中心生成图片后，即可在这里选择。</p></div> : <div className="image-history-grid">
      {entries.map(({ output, outfitName }) => {
        const selectedIndex = selectedIds.indexOf(output.id)
        const selected = selectedIndex >= 0
        const added = addedOutputIds.includes(output.id)
        const disabled = importing || added || (!selected && selectedIds.length >= remaining)
        return <button type="button" className={`image-history-card${selected ? ' is-selected' : ''}`} key={output.id} aria-pressed={selected} disabled={disabled} aria-label={`${outfitName} · ${output.label}`} onClick={() => setSelectedIds((current) => selected ? current.filter((id) => id !== output.id) : [...current, output.id])}>
          <span className="image-history-card-preview">{output.file ? <LocalImage file={output.file} alt={output.label} /> : <AuthenticatedImage src={output.downloadUrl || output.url} alt={output.label} />}<span className="image-history-card-check" aria-hidden="true">{selected ? selectedIndex + 1 : added ? <Check size={14} /> : null}</span></span>
          <span className="image-history-card-title" title={outfitName}>{outfitName}</span><span className="image-history-card-label" title={output.label}>{output.label}</span>
          <small>{added ? '已添加' : '可选用'}</small>
        </button>
      })}
    </div>}
    {history.hasMore ? <button type="button" className="secondary-button" disabled={history.loading || importing} onClick={history.loadMore}>加载更多生图记录</button> : null}
  </FoundationDrawer>
}
