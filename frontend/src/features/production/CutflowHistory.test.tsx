import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { MultiCutflowTask } from '../../api'
import { CutflowHistory } from './CutflowHistory'

const finished = {
  id: '0c424032-b507-4187-9a32-c6a500dc6877',
  status: 'PENDING_REVIEW',
  errorCode: null,
  errorMessage: null,
  createdAt: '2026-10-30T06:36:09Z',
  updatedAt: '2026-10-30T06:43:52Z',
  sources: [
    { clipId: 'clip1', order: 1, taskId: 'source-task', variantId: 'v1', artifactId: 'a1', title: 'TokyoTiger', durationSeconds: 8 },
    { clipId: 'clip2', order: 2, taskId: 'source-task', variantId: 'v2', artifactId: 'a2', title: 'TokyoTiger', durationSeconds: 8 },
  ],
  outputs: [{
    id: '425e7ba3-90ef-41f8-9dd5-391213646d62',
    sha256: 'a'.repeat(64), reviewStatus: 'PENDING',
    durationSeconds: 10.5, width: 1080, height: 1920,
    createdAt: '2026-10-30T06:43:52Z',
    mediaUrl: '/api/v1/cutflow-handoffs/multi/0c424032-b507-4187-9a32-c6a500dc6877/outputs/425e7ba3-90ef-41f8-9dd5-391213646d62',
  }],
} as MultiCutflowTask

describe('Task page CUT history visibility', () => {
  it('shows a recovered TokyoTiger CUT batch by default with its video, ID and download', () => {
    const html = renderToStaticMarkup(<CutflowHistory tasks={[finished]} loaded error="" onRefresh={() => undefined} />)
    expect(html).toContain('剪辑任务与成片')
    expect(html).toContain('0c424032-b507-4187-9a32-c6a500dc6877')
    expect(html).toContain('TokyoTiger')
    expect(html).toContain('10.5')
    expect(html).toContain('成片已回传')
    expect(html).toContain('下载成片')
    expect(html).toContain('/outputs/425e7ba3-90ef-41f8-9dd5-391213646d62')
    expect(html.match(/<video/g)).toHaveLength(1)
  })

  it('does not incorrectly put an unfinished job in the completed preview', () => {
    const active: MultiCutflowTask = { ...finished, id: 'unfinished', status: 'EDITING', outputs: [] }
    const html = renderToStaticMarkup(<CutflowHistory tasks={[active]} loaded error="" onRefresh={() => undefined} />)
    expect(html).toContain('还没有回传的剪辑成片')
    expect(html).not.toContain('剪辑任务编号：unfinished')
    expect(html).not.toContain('<video')
  })

  it('surfaces API failure without misreporting zero historical clips', () => {
    const html = renderToStaticMarkup(<CutflowHistory tasks={[]} loaded={false} error="API_ERROR" onRefresh={() => undefined} />)
    expect(html).toContain('API_ERROR')
    expect(html).not.toContain('还没有回传的剪辑成片')
  })
})
