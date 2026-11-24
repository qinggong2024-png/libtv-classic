import { Check, Images, LoaderCircle, Search } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { fetchAssetFile, getTaskPage } from '../../api'
import { errorMessage } from '../../errorPresentation'
import { isSupportedImageFile } from '../../imageFiles'
import { SUBJECT_MAX_IMAGE_BYTES } from '../../subjectFiles'
import type { Task } from '../../types'
import { FoundationDrawer } from '../../shared/components/ui'
import { productHistoryEntries, sameReferenceFile, type ProductHistoryEntry } from './productHistory'
import './productHistory.css'

export function ProductHistoryPicker({ siteProfileId, maxSelection, existingFiles, onSelect, onClose }: {
  siteProfileId: string
  maxSelection: number
  existingFiles: readonly File[]
  onSelect: (files: File[]) => void
  onClose: () => void
}) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<ProductHistoryEntry[]>([])
  const mounted = useRef(false)
  const inFlight = useRef(false)
  useEffect(() => {
    let active = true
    mounted.current = true
    void getTaskPage().then(page => {
      if (active) { setTasks(page.items); setCursor(page.nextCursor) }
    }).catch(cause => { if (active) setError(errorMessage(cause, '历史商品图读取失败')) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false; mounted.current = false }
  }, [])
  const loadMore = async () => {
    if (loading || importing) return
    setLoading(true)
    setError('')
    try {
      const page = await getTaskPage(cursor ?? undefined)
      if (mounted.current) {
        setTasks(current => [...current, ...page.items.filter(task => !current.some(item => item.id === task.id))])
        setCursor(page.nextCursor)
      }
    } catch (cause) { if (mounted.current) setError(errorMessage(cause, '历史商品图读取失败')) }
    finally { if (mounted.current) setLoading(false) }
  }
  const toggle = (entry: ProductHistoryEntry) => {
    setError('')
    setSelected(current => current.some(item => item.key === entry.key)
      ? current.filter(item => item.key !== entry.key)
      : current.length < maxSelection ? [...current, entry] : current)
  }
  const confirm = async () => {
    if (inFlight.current || !selected.length || selected.length > maxSelection) return
    inFlight.current = true
    setImporting(true)
    setError('')
    try {
      const files: File[] = []
      for (const { asset } of selected) {
        const file = await fetchAssetFile('/api/v1/assets/' + encodeURIComponent(asset.id) + '/content', asset.originalFilename)
        if (!isSupportedImageFile(file) || file.size > SUBJECT_MAX_IMAGE_BYTES) throw new Error('原图格式不支持或超过 10 MB：' + asset.originalFilename)
        const duplicates = await Promise.all([...existingFiles, ...files].map(item => sameReferenceFile(item, file)))
        if (!duplicates.some(Boolean)) files.push(file)
      }
      if (!files.length) throw new Error('所选商品图已添加，无需重复添加。')
      if (mounted.current) onSelect(files)
    } catch (cause) { if (mounted.current) setError(errorMessage(cause, '原图读取失败，请重新选择或上传。')) }
    finally { inFlight.current = false; if (mounted.current) setImporting(false) }
  }
  const entries = productHistoryEntries(tasks, siteProfileId, query)
  return <FoundationDrawer open className="creation-drawer" title="历史商品 / 人物参考图" onClose={onClose}>
    <div className="product-history">
      <p className="product-history-note">仅显示当前品牌、当前账号历史任务上传的参考图。添加时读取原始图片，不使用缩略图生成。</p>
      <label className="search-field"><Search size={16}/><input aria-label="搜索商品编号或名称" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索商品编号、名称或任务"/></label>
      {error ? <p className="inline-alert inline-alert--danger" role="alert">{error}</p> : null}
      <div className="product-history-grid" role="group" aria-label="历史商品图">
        {entries.map(entry => {
          const checked = selected.some(item => item.key === entry.key)
          return <button type="button" key={entry.key} className={'product-history-card' + (checked ? ' is-selected' : '')} aria-pressed={checked} disabled={importing || (!checked && selected.length >= maxSelection)} onClick={() => toggle(entry)}>
            <span className="product-history-image"><img src={entry.asset.thumbnailUrl} alt="" loading="lazy"/>{checked ? <span className="product-history-check"><Check size={14}/></span> : null}</span>
            <strong title={entry.asset.originalFilename}>{entry.asset.originalFilename}</strong>
            <small>{entry.task.createdAt.slice(0, 10)}</small>
          </button>
        })}
      </div>
      {loading ? <p role="status"><LoaderCircle className="spin" size={17}/> 正在读取历史商品图</p> : !entries.length && !error ? <p className="image-history-empty"><Images size={20}/> 当前已读取记录中没有匹配的商品图{cursor ? '，可继续加载更早记录。' : '。'}</p> : null}
      {cursor || error && !tasks.length ? <button type="button" className="secondary-button" disabled={loading || importing} onClick={() => void loadMore()}>{cursor ? '加载更多历史记录' : '重新读取'}</button> : null}
      <div className="product-history-footer"><span>已选择 {selected.length} / {maxSelection} 张</span><button type="button" className="primary-button" disabled={importing || !selected.length || selected.length > maxSelection} onClick={() => void confirm()}>{importing ? '正在读取原图…' : '确认添加'}</button></div>
    </div>
  </FoundationDrawer>
}
