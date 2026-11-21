import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { MultiCutflowTask } from '../../api'
import { CutflowControl } from './CutflowControl'

const submittedBatch = {
  id: 'batch-1',
  status: 'READY',
  errorCode: null,
  errorMessage: null,
  createdAt: '2026-10-29T10:00:00Z',
  updatedAt: '2026-10-29T10:05:00Z',
  sources: [
    {
      clipId: 'clip-1',
      order: 1,
      taskId: 'task-1',
      variantId: 'variant-1',
      artifactId: 'artifact-1',
      title: '套装',
      durationSeconds: 8,
    },
  ],
  outputs: [],
} as MultiCutflowTask

describe('输入原片内的剪辑状态', () => {
  it('已送剪时读取任务级状态，不再显示尚未剪辑', () => {
    const html = renderToStaticMarkup(
      <CutflowControl
        taskId="task-1"
        variantId="variant-1"
        artifactId="artifact-1"
        allowSubmit={false}
        multiSourceTasks={[submittedBatch]}
      />,
    )
    expect(html).toContain('素材已就绪，等待 CUT 接单')
    expect(html).toContain('成片会显示在下方“多片合并成片”')
    expect(html).not.toContain('尚未剪辑')
  })
})
