import { useState } from 'react'
import { RefreshCw } from 'lucide-react'
import type { MultiCutflowTask } from '../../api'
import { CutflowBatchResult } from './CutflowBatchResult'
import './cutflow.css'

// This account-scoped edit history is separate from the generated-video task list.
export function CutflowHistory({ tasks, loaded, error, onRefresh }: {
  tasks: MultiCutflowTask[]
  loaded: boolean
  error: string
  onRefresh: () => void
}) {
  const [tab, setTab] = useState<'finished' | 'active'>('finished')
  const [showAll, setShowAll] = useState(false)
  const finished = tasks.filter(task => task.outputs.length > 0)
  const active = tasks.filter(task => task.outputs.length === 0)
  const chosen = tab === 'finished' ? finished : active
  const visible = showAll ? chosen : chosen.slice(0, 3)

  return <section className="cutflow-history" aria-label="剪辑任务与成片">
    <div className="cutflow-history__heading">
      <div><span>视频后期</span><h2>剪辑任务与成片</h2><p>合并剪辑的记录独立于生成原片，完成后在这里预览、下载并审核。</p></div>
      <button type="button" className="secondary-button" disabled={!loaded && !error} onClick={onRefresh}><RefreshCw size={15} />刷新剪辑记录</button>
    </div>
    <div className="filter-tabs" aria-label="剪辑记录状态">
      <button type="button" className={tab === 'finished' ? 'is-active' : ''} aria-pressed={tab === 'finished'} onClick={() => { setTab('finished'); setShowAll(false) }}>剪辑成片 <span>{finished.length}</span></button>
      <button type="button" className={tab === 'active' ? 'is-active' : ''} aria-pressed={tab === 'active'} onClick={() => { setTab('active'); setShowAll(false) }}>剪辑任务 <span>{active.length}</span></button>
    </div>
    {error ? <p role="alert" className="cutflow-library__warning">剪辑记录暂时无法更新，以下可能是上次结果：{error}</p> : null}
    {!loaded && !tasks.length && !error ? <p role="status" className="cutflow-history__message">正在读取剪辑记录…</p> : null}
    {loaded && !chosen.length && !error ? <p className="cutflow-history__message">{tab === 'finished' ? '还没有回传的剪辑成片。' : '当前没有待处理的剪辑任务。'}</p> : null}
    {visible.length ? <div className="cutflow-history__list">{visible.map(task => <div className="cutflow-history__entry" key={task.id}>
      <CutflowBatchResult task={task} />
      <small className="cutflow-history__id">剪辑任务编号：{task.id}</small>
    </div>)}</div> : null}
    {chosen.length > 3 ? <button type="button" className="text-button cutflow-history__more" onClick={() => setShowAll(value => !value)}>{showAll ? '收起较早记录' : ('查看全部 ' + chosen.length + ' 条' + (tab === 'finished' ? '剪辑成片' : '剪辑任务'))}</button> : null}
  </section>
}
