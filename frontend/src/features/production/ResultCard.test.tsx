import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Task, Variant } from '../../types'
import { ResultCard } from './ResultCard'

describe('generated videos without manual approval', () => {
  it.each(['PENDING', 'REJECTED', 'APPROVED'] as const)('keeps %s legacy results downloadable without review controls', (finalApprovalStatus) => {
    const task = { id: 'task', title: '成片', assets: [], updatedAt: '2026-10-15T00:00:00Z', settings: { durationSeconds: 10 } } as unknown as Task
    const variant = { id: 'variant', kind: 'STABLE', downloadArtifactId: 'artifact', generationStatus: 'SUCCEEDED', finalApprovalStatus } as Variant
    const markup = renderToStaticMarkup(<ResultCard task={task} variant={variant} canReview onPlay={() => {}} onRemix={() => {}} onOpen={() => {}} onFeedbackChanged={() => {}} />)
    expect(markup).toContain('下载')
    expect(markup).not.toContain('审核')
    expect(markup).not.toContain('拒绝交付')
    expect(markup).toContain('点赞此视频')
  })
})
