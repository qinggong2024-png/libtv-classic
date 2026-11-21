import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { MultiCutflowTask } from '../../api'
import { CutflowBatchResult } from './CutflowBatchResult'

const task = {
  id: 'batch-1',
  status: 'PENDING_REVIEW',
  errorCode: null,
  errorMessage: null,
  createdAt: '2026-10-29T10:00:00Z',
  updatedAt: '2026-10-29T10:05:00Z',
  sources: [
    { clipId: 'clip-1', order: 1, taskId: 'task-1', variantId: 'variant-1', artifactId: 'artifact-1', title: '套装', durationSeconds: 8 },
    { clipId: 'clip-2', order: 2, taskId: 'task-1', variantId: 'variant-2', artifactId: 'artifact-2', title: '套装', durationSeconds: 8 },
  ],
  outputs: [
    {
      id: 'render-1',
      sha256: 'a'.repeat(64),
      reviewStatus: 'PENDING',
      durationSeconds: 9.5,
      width: 720,
      height: 1280,
      createdAt: '2026-10-29T10:05:00Z',
      mediaUrl: '/api/v1/cutflow-handoffs/multi/batch-1/outputs/render-1',
    },
    {
      id: 'render-2',
      sha256: 'b'.repeat(64),
      reviewStatus: 'PENDING',
      durationSeconds: 10,
      width: 720,
      height: 1280,
      createdAt: '2026-10-29T10:05:00Z',
      mediaUrl: '/api/v1/cutflow-handoffs/multi/batch-1/outputs/render-2',
    },
  ],
} as MultiCutflowTask

describe('多片合并剪辑结果', () => {
  it('即使协议返回多条输出，也只在任务级区域展示第一条合并成片，并保留原片输入顺序', () => {
    const html = renderToStaticMarkup(<CutflowBatchResult task={task} sourceLabel={source => `结果 ${source.order}`} />)
    expect(html.match(/<video/g)).toHaveLength(1)
    expect(html).toContain('outputs/render-1')
    expect(html).toContain('cutflow-batch-result__preview')
    expect(html).toContain('aria-label=')
    expect(html).not.toContain('outputs/render-2')
    expect(html).toContain('结果 1')
    expect(html).toContain('结果 2')
    expect(html).toContain('剪辑成片')
    expect(html).toContain('下载成片')
    expect(html.indexOf('cutflow-batch-result__preview')).toBeLessThan(html.indexOf('cutflow-batch-result__download'))
    expect(html.indexOf('cutflow-batch-result__download')).toBeLessThan(html.indexOf('cutflow-batch-result__meta'))
  })

  it('shows a submitted job as waiting for CUT acknowledgement', () => {
    const html = renderToStaticMarkup(
      <CutflowBatchResult task={{ ...task, status: 'READY', outputs: [] }} />,
    )
    expect(html).toContain('素材已就绪，等待 CUT 接单')
    expect(html).not.toContain('正在交付')
    expect(html).not.toContain('尚未剪辑')
  })

  it('places source trace and jump links in the finished latest-result side panel only once', () => {
    const html = renderToStaticMarkup(<CutflowBatchResult task={task} latest />)
    expect(html.match(/剪辑来源（2 条）/g)).toHaveLength(1)
    expect(html).toContain('cutflow-batch-result__insights')
    expect(html).toContain('查看原片')
    expect(html).toContain('查看剪辑记录')
    expect(html.indexOf('task-a-delivery__result')).toBeLessThan(html.indexOf('task-a-delivery__sources'))
    expect(html).not.toContain('<details')
    expect(html).toContain('download=""')
  })
})
