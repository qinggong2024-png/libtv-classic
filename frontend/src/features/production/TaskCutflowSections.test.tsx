import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { MultiCutflowTask } from '../../api'
import { TaskCutflowActivity, TaskCutflowDeliveries } from './TaskCutflowSections'

const source = { clipId: 'clip-1', order: 1, taskId: 'task-1', variantId: 'var-1', artifactId: 'artifact-1', title: '原片 01', durationSeconds: 10 }
const base: MultiCutflowTask = {
  id: 'active-1', status: 'EDITING', errorCode: null, errorMessage: null,
  createdAt: '2026-10-28T10:00:00Z', updatedAt: '2026-10-30T12:00:00Z',
  sources: [source], outputs: [],
} as MultiCutflowTask
const output = { id: 'output-1', sha256: 'a'.repeat(64), reviewStatus: 'PENDING', durationSeconds: 6,
  width: 1080, height: 1920, createdAt: '2026-10-30T13:00:00Z', mediaUrl: '/cutflow/media-1' }

describe('精简任务详情剪辑区', () => {
  it('只突出正在处理的批次；失败记录进入默认关闭的历史分组', () => {
    const failed = Array.from({ length: 8 }, (_, i) => ({
      ...base, id: 'failed-' + i, status: 'FAILED', errorMessage: '模拟失败详情-' + i,
      updatedAt: '2026-10-28T10:0' + i + ':00Z',
    } as MultiCutflowTask))
    const html = renderToStaticMarkup(<TaskCutflowActivity batches={[base, ...failed]} />)
    expect(html).toContain('当前进行中')
    expect(html).toContain('历史剪辑批次')
    expect(html).toContain('失败 8')
    expect(html.match(/class="task-cut-entry /g)).toHaveLength(5)
    expect(html).toContain('查看全部 8 个历史批次')
    expect(html).not.toContain('模拟失败详情-0')
    expect(html).not.toContain('<video')
  })

  it('没有当前剪辑时保持简洁空态，历史批次仍可查看', () => {
    const html = renderToStaticMarkup(<TaskCutflowActivity batches={[{ ...base, status: 'CANCELED' }]} />)
    expect(html).toContain('当前没有进行中的剪辑任务')
    expect(html).toContain('取消 1')
    expect(html).toContain('task-cut-history')
  })

  it('任务详情展开一次即可看见剪辑失败原因，不套第二层折叠', () => {
    const failed = { ...base, status: 'FAILED', errorMessage: '剪辑媒体校验失败' } as MultiCutflowTask
    const html = renderToStaticMarkup(<TaskCutflowActivity batches={[failed]} inlineHistory />)
    expect(html).toContain('剪辑媒体校验失败')
    expect(html).not.toContain('<details')
    expect(html).toContain('task-cut-entry--flat')
  })

  it('最新回传成片优先，较早版本默认折叠但保留下载', () => {
    const older = { ...base, id: 'done-old', status: 'PENDING_REVIEW',
      updatedAt: '2026-10-29T11:00:00Z', outputs: [{ ...output, id: 'out-old', mediaUrl: '/cutflow/old' }] } as MultiCutflowTask
    const newest = { ...older, id: 'done-new', updatedAt: '2026-10-30T13:00:00Z',
      outputs: [{ ...output, id: 'out-new', mediaUrl: '/cutflow/new' }] }
    const html = renderToStaticMarkup(<TaskCutflowDeliveries batches={[older, newest]} sourceLabel={s => s.title} />)
    expect(html).toContain('最新成片')
    expect(html).toContain('较早成片版本')
    expect(html).toContain('下载成片')
    expect(html).toContain('/cutflow/new')
    expect(html).toContain('/cutflow/old')
    expect(html.indexOf('/cutflow/new')).toBeLessThan(html.indexOf('/cutflow/old'))
  })

  it('最新成片主视觉与历史版本标签不重复渲染', () => {
    const older = { ...base, id: 'v1', status: 'PENDING_REVIEW',
      updatedAt: '2026-10-29T11:00:00Z', outputs: [{ ...output, id: 'old', mediaUrl: '/cutflow/old' }] } as MultiCutflowTask
    const newest = { ...older, id: 'v2', updatedAt: '2026-10-30T13:00:00Z',
      outputs: [{ ...output, id: 'new', mediaUrl: '/cutflow/new' }] }
    const hero = renderToStaticMarkup(<TaskCutflowDeliveries batches={[older, newest]} display="latest" sourceLabel={s => s.title} />)
    const history = renderToStaticMarkup(<TaskCutflowDeliveries batches={[older, newest]} display="history" sourceLabel={s => s.title} />)
    expect(hero).toContain('/cutflow/new')
    expect(hero).not.toContain('/cutflow/old')
    expect(history).toContain('/cutflow/old')
    expect(history).not.toContain('/cutflow/new')
    expect(hero.match(/<video/g)).toHaveLength(1)
    expect(history.match(/<video/g)).toHaveLength(1)
  })

  it('重复的多片批次只展示一次', () => {
    const duplicate = { ...base, id: 'duplicate-1', status: 'FAILED', updatedAt: '2026-10-29T19:03:00Z' } as MultiCutflowTask
    const duplicateAgain = { ...duplicate, id: 'duplicate-2' }
    const html = renderToStaticMarkup(<TaskCutflowActivity batches={[duplicate, duplicateAgain]} />)
    expect(html.match(/class="task-cut-entry /g)).toHaveLength(1)
    expect(html).toContain('历史剪辑批次 <strong>1</strong>')
  })
})
