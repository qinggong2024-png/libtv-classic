import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Task, Variant } from '../../types'
import { VariantStatus } from './WorkspacePanels'

const task = { id: 'task-1', title: 'task', phase: 'PARTIALLY_DELIVERABLE' } as unknown as Task
const baseVariant = {
  id: 'variant-1', kind: 'STABLE', generationStatus: 'UNKNOWN',
  downloadStatus: 'NOT_READY', downloadArtifactId: null, provider: 'kling_official_cli',
} as unknown as Variant

function render(variant: Variant) {
  return renderToStaticMarkup(
    <VariantStatus task={task} variant={variant} onChanged={async () => {}} onPlay={() => {}} />,
  )
}

describe('unknown cloud submissions without generationId', () => {
  it('explains manual verification and does not imply refresh checks the provider', () => {
    const html = render({ ...baseVariant, providerTaskId: null })
    expect(html).toContain('未取得可灵云端任务 ID')
    expect(html).toContain('刷新本地状态')
    expect(html).toContain('不要重新生成整组')
    expect(html).not.toContain('重新查询状态')
  })

  it('does not show the no-id warning if a cloud generation id exists', () => {
    const html = render({ ...baseVariant, providerTaskId: 'remote-generation-id' })
    expect(html).not.toContain('未取得可灵云端任务 ID')
    expect(html).toContain('刷新本地状态')
  })
})
