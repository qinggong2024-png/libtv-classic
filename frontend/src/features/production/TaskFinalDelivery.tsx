import { Play } from 'lucide-react'
import type { MultiCutflowTask } from '../../api'
import { formatTime } from '../../domain/taskPresentation'

export function TaskFinalDelivery({ task, sourceLabel, onPreview, unseen = false }: {
  task: MultiCutflowTask
  sourceLabel?: (source: MultiCutflowTask['sources'][number]) => string
  onPreview: () => void
  unseen?: boolean
}) {
  const output = task.outputs[0]
  if (!output) return null
  const sources = [...task.sources].sort((a, b) => a.order - b.order)
  const jump = (id: string) => {
    const target = document.getElementById(id)
    if (target instanceof HTMLDetailsElement) target.open = true
    target?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' })
  }
  return <div className="task-a-delivery">
    <section className="task-a-delivery__result" aria-label="成片预览与下载">
      <header><div><h2>最新成片</h2><p>本任务最新回传的剪辑结果</p></div><span className="task-a-delivery__ready">{unseen ? '未看 · 可下载' : '可下载'}</span></header>
      <div className="task-a-delivery__content">
        <button type="button" className="task-a-delivery__preview" aria-label="预览剪辑成片" onClick={onPreview}>
          <video preload="metadata" muted playsInline src={output.mediaUrl} style={{ aspectRatio: `${output.width} / ${output.height}` }} />
          <span><Play size={20} />预览成片</span>
        </button>
        <div className="task-a-delivery__metadata"><span>剪辑成片</span><h3>{sources.length} 条原片组成的成片</h3>
          <dl><dt>成片时长</dt><dd>{output.durationSeconds.toFixed(1)} 秒</dd><dt>画面尺寸</dt><dd>{output.width} × {output.height}</dd><dt>完成时间</dt><dd>{formatTime(output.createdAt || task.updatedAt)}</dd></dl>
          <div className="task-a-delivery__actions"><a className="primary-button" href={`${output.mediaUrl}?download=true`} download>下载成片</a><button type="button" className="secondary-button" onClick={onPreview}>预览成片</button></div>
          <small>右侧保留本版本的原片输入顺序</small>
        </div>
      </div>
    </section>
    <aside className="task-a-delivery__sources cutflow-batch-result__insights" aria-label="剪辑来源">
      <h3>剪辑来源（{sources.length} 条）</h3><p>按以下顺序组成这一版成片</p>
      <ol aria-label="剪辑输入顺序">{sources.map(source => <li key={source.clipId}><b>{source.order}</b><div><strong>{sourceLabel?.(source) || source.title}</strong><small>原片时长 {source.durationSeconds} 秒</small></div></li>)}</ol>
      <nav aria-label="相关操作位置"><button type="button" onClick={() => jump('task-original-clips')}>查看原片与再次送剪</button><button type="button" onClick={() => jump('task-cutflow-records')}>查看剪辑记录</button></nav>
    </aside>
  </div>
}
