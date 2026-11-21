import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Task } from '../../types'
import { TaskAdaptiveProgress } from './TaskAdaptiveProgress'

const task = {
  id: 'active-video',
  phase: 'GENERATING',
  createdAt: '2026-11-12T01:00:00Z',
  updatedAt: '2026-11-12T01:01:00Z',
  workflowKind: 'FIXED_TEMPLATE',
  actionCapabilities: {},
  variants: [
    { id: 'one', downloadArtifactId: 'ready' },
    { id: 'two', downloadArtifactId: null },
  ],
} as unknown as Task

describe('compact production status', () => {
  it('shows only confirmed raw-output count, stage and total waiting time', () => {
    const html = renderToStaticMarkup(<TaskAdaptiveProgress task={task} />)
    expect(html).toContain('aria-label="当前制作进度"')
    expect(html).toContain('原片已就绪 1/2')
    expect(html).toContain('任务累计用时')
    expect(html).toContain('aria-current="step"')
    expect(html).not.toContain('预计剩余')
  })

  it('uses the recorded active stage start for actual stage duration', () => {
    const withActive = {
      ...task,
      timing: { stages: [{ key: 'GENERATION', status: 'ACTIVE', activeStartedAt: new Date(Date.now() - 12000).toISOString(), durationSeconds: 0 }] },
    } as unknown as Task
    const html = renderToStaticMarkup(<TaskAdaptiveProgress task={withActive} />)
    expect(html).toContain('本阶段已用')
    expect(html).not.toContain('任务累计用时')
  })

  it('uses the replacement-stage language for deep video workflows', () => {
    const deep = { ...task, workflowKind: 'DEEP_VIDEO' } as unknown as Task
    const html = renderToStaticMarkup(<TaskAdaptiveProgress task={deep} />)
    expect(html).toContain('视频制作')
    expect(html).not.toContain('原片生成中')
  })
})
