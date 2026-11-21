import { Check, Clipboard, LoaderCircle, Search, X } from 'lucide-react'
import { useState } from 'react'
import type { Task, Variant } from '../../types'
import { DataFreshness } from '../../shared/components/DataFreshness'
import { copyText } from '../../shared/utils/copyText'
import { CutflowLibrary } from '../production/CutflowLibrary'
import type { CutflowLibraryFilter } from '../production/cutflowLibraryModel'

export function CutflowView({ tasks, filter, query, sourceId, hasMore, loadingMore, lastSyncedAt, syncError, onLoadMore, onStateChange, onOpen, onPlay, canWrite, multiCutEnabled }: {
  tasks: Task[]; filter: CutflowLibraryFilter; query: string; sourceId: string | null
  hasMore: boolean; loadingMore: boolean; lastSyncedAt: number | null; syncError: boolean
  onLoadMore: () => void
  onStateChange: (filter: CutflowLibraryFilter, query: string, replace?: boolean, sourceId?: string | null) => void
  onOpen: (task: Task) => void; onPlay: (task: Task, variant: Variant) => void; canWrite: boolean
  multiCutEnabled: boolean
}) {
  const [copied, setCopied] = useState(false)
  const updateQuery = (value: string) => onStateChange(filter, value, true)
  const copyLink = async () => {
    try {
      await copyText(window.location.href)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch { setCopied(false) }
  }
  return <div className="page-view">
    <div className="page-title">
      <div><span>视频后期</span><h1>剪辑与成片</h1><p>选择原片送入剪辑，跟进剪辑任务，集中预览和下载回传成片。</p></div>
      <div className="records-page-actions">
        <DataFreshness timestamp={lastSyncedAt} stale={syncError} />
        <label className="search-field"><Search size={17} /><input value={query} onChange={event => updateQuery(event.target.value)} placeholder="搜索来源任务" aria-label="搜索来源任务" />{query ? <button type="button" className="search-field-clear" onClick={event => { event.preventDefault(); updateQuery('') }} aria-label="清除搜索关键词"><X size={15} /></button> : null}</label>
        <button type="button" className="icon-button" onClick={() => void copyLink()} aria-label="复制当前剪辑筛选链接" title="复制当前筛选链接">{copied ? <Check size={17} /> : <Clipboard size={17} />}</button>
      </div>
    </div>
    <CutflowLibrary tasks={tasks} query={query} filter={filter} selectedSourceId={sourceId} onSelect={value => onStateChange(filter, query, true, value)} onFilterChange={(value, selected) => onStateChange(value, query, false, selected)} onOpen={onOpen} onPlay={onPlay} canWrite={canWrite} multiCutEnabled={multiCutEnabled} />
    {hasMore ? <div className="records-load-more"><button className="secondary-button" disabled={loadingMore} onClick={onLoadMore}>{loadingMore ? <LoaderCircle className="spin" size={16} /> : null}{loadingMore ? '正在加载' : '加载更早记录'}</button></div> : null}
  </div>
}
