import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Task } from '../../types'
import { RecordsView } from './RecordsView'
import { taskAttentionSignature } from '../../domain/taskAttention'
import { TaskDatePicker } from './TaskDatePicker'
import { localDayKey, parseTaskDateRange, taskCreatedOn, taskDateLabel } from './TaskDateRange'

const noop = () => {}
const create = (phase: string, suffix: string): Task => ({
  id: suffix, clientTaskId: suffix, title: '任务' + suffix,
  phase, createdAt: '2026-11-18T12:00:00Z', updatedAt: '2026-11-19T12:00:00Z',
  assetCount: 0, variants: [], assets: [], settings: { autoSubmitToCutflow: false },
} as unknown as Task)
const view = (tasks: Task[], filter: 'all' | 'attention' | 'done' = 'all') => renderToStaticMarkup(
  <RecordsView tasks={tasks} initialFilter={filter} hasMore={false} loadingMore={false}
    lastSyncedAt={null} onLoadMore={noop} onOpen={noop} onPlay={noop}
    onRemix={noop} onFeedbackChanged={noop} onCreateTask={noop} />,
)

describe('任务中心 V3 日期筛选', () => {
  it('解析并拒绝不完整、逆序的 URL 日期范围', () => {
    expect(parseTaskDateRange('?from=2026-10-05&to=2026-11-19')).toEqual({ start: '2026-10-05', end: '2026-11-19' })
    expect(parseTaskDateRange('?from=2026-11-19')).toBeNull()
    expect(parseTaskDateRange('?from=2026-11-19&to=2026-10-19')).toBeNull()
    expect(parseTaskDateRange('?from=a&to=b')).toBeNull()
  })
  it('按创建日期匹配，而不是最近更新时间', () => {
    const task = create('DELIVERABLE', '001')
    const day = localDayKey(new Date(task.createdAt))
    expect(taskCreatedOn(task, { start: day, end: day })).toBe(true)
    expect(taskCreatedOn(task, { start: '2024-01-01', end: '2024-01-02' })).toBe(false)
    expect(taskDateLabel({ start: day, end: day }, new Date(task.createdAt))).toBe('今天')
  })
  it('仅显示四个主筛选，部分完成归已完成、失败归待处理，取消仅在全部任务内', () => {
    const tasks = [create('PARTIALLY_DELIVERABLE','partial-1'),create('ACTION_REQUIRED','failed-1'),create('CANCELLED','cancel-1')]
    const all = view(tasks)
    expect(all).toContain('全部任务')
    expect(all).toContain('待我处理')
    expect(all).toContain('进行中')
    expect(all).toContain('已完成')
    expect(all).not.toContain('更多筛选')
    expect(all).toContain('部分完成')
    expect(all).toContain('已取消')
    const attention = view(tasks, 'attention')
    expect(attention).toContain('任务failed-1')
    expect(attention).not.toContain('任务partial-1')
    expect(attention).not.toContain('任务cancel-1')
    const done = view(tasks, 'done')
    expect(done).toContain('任务partial-1')
    expect(done).not.toContain('任务failed-1')
    expect(done).not.toContain('任务cancel-1')
  })
  it('忽略待办后两处统计从同一分类器退出，但全部任务仍然保留记录', () => {
    const task = create('ACTION_REQUIRED', 'ignored-1')
    const signature = taskAttentionSignature(task, [])
    const acknowledged = renderToStaticMarkup(
      <RecordsView tasks={[task]} initialFilter="attention" hasMore={false} loadingMore={false}
        lastSyncedAt={null} onLoadMore={noop} onOpen={noop} onPlay={noop}
        onRemix={noop} onFeedbackChanged={noop} onCreateTask={noop}
        attentionAcknowledgements={{ [task.id]: signature }} />,
    )
    expect(acknowledged).not.toContain('任务ignored-1')
    expect(acknowledged).toContain('待我处理<span>0</span>')

    const all = renderToStaticMarkup(
      <RecordsView tasks={[task]} initialFilter="all" hasMore={false} loadingMore={false}
        lastSyncedAt={null} onLoadMore={noop} onOpen={noop} onPlay={noop}
        onRemix={noop} onFeedbackChanged={noop} onCreateTask={noop}
        attentionAcknowledgements={{ [task.id]: signature }} onRestoreAttention={noop} />,
    )
    expect(all).toContain('任务ignored-1')
    expect(all).toContain('已忽略提醒')
    expect(all).toContain('恢复提醒')
  })

  it('日期弹层仅保留标题、日历和快捷选项，无多余拖动说明', () => {
    const html = renderToStaticMarkup(<TaskDatePicker value={null} onChange={noop} />)
    expect(html).toContain('全部时间')
    expect(html).not.toContain('拖动一次')
    expect(html).not.toContain('按住拖动')
  })
})
