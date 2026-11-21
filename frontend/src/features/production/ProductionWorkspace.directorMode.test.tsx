import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Task, Variant } from '../../types'
import { ProductionWorkspace } from './ProductionWorkspace'

const noop = () => {}
const refresh = async () => {}
const variant = {
  id: 'variant-01', kind: 'STABLE', generationStatus: 'SUCCEEDED',
  validationStatus: 'PASSED', downloadArtifactId: 'artifact-01', promptText: '',
} as unknown as Variant

function renderDetail(settings: Record<string, unknown>, workflowKind = 'FIXED_TEMPLATE', compiler?: string) {
  const task = {
    id: 'task-director-mode', title: 'Test', createdAt: '2026-11-20T08:00:00Z',
    updatedAt: '2026-11-20T08:10:00Z', phase: 'DELIVERABLE',
    assets: [], assetCount: 2, variants: [variant],
    workflowKind, generationProvider: 'KLING', settings,
    compiledPlan: compiler ? { compiler } : null,
    actionCapabilities: { canRegenerate: false },
  } as unknown as Task

  return renderToStaticMarkup(<ProductionWorkspace
    task={task} onClose={noop} onChanged={refresh}
    onRemix={noop} onReuse={noop} onPlay={noop}
  />)
}

describe('task detail director mode', () => {
  it('shows smart storyboard when the compiled task used a cross-scene OOTD director', () => {
    const html = renderDetail({ generationMode: 'automatic', directorMode: 'cross_scene_v2' }, 'FIXED_TEMPLATE', 'allwe-ootd-sol-cross-scene-b-v1')
    expect(html).toContain('<dt>编排模式</dt><dd>智能分镜</dd>')
    expect(html).not.toContain('固定模板')
    expect(html).not.toContain('<dt>制作方式</dt>')
  })

  it('shows reference-guided when the compiled task used the reference-guided director', () => {
    const html = renderDetail({ generationMode: 'automatic', directorMode: 'reference_guided' }, 'FIXED_TEMPLATE', 'allwe-ootd-sol-simple-v1')
    expect(html).toContain('<dt>编排模式</dt><dd>遵循参考图</dd>')
  })

  it('uses the actual compiler rather than a stale selected mode', () => {
    const html = renderDetail({ generationMode: 'automatic', directorMode: 'cross_scene_v2' }, 'FIXED_TEMPLATE', 'allwe-ootd-sol-simple-v1')
    expect(html).toContain('<dt>编排模式</dt><dd>遵循参考图</dd>')
    expect(html).not.toContain('<dt>编排模式</dt><dd>智能分镜</dd>')
  })

  it('recognizes the validated dynamic OOTD compiler as smart storyboard', () => {
    const html = renderDetail({ generationMode: 'automatic', directorMode: 'reference_guided' }, 'FIXED_TEMPLATE', 'allwe-ootd-sol-dynamic-display-v1')
    expect(html).toContain('<dt>编排模式</dt><dd>智能分镜</dd>')
  })

  it('does not mislabel unrelated automatic compilers as smart storyboard', () => {
    const html = renderDetail({ generationMode: 'automatic', directorMode: 'cross_scene_v2' }, 'FIXED_TEMPLATE', 'ai-multimodal-director-v2.4.3-evidence-paced')
    expect(html).not.toContain('<dt>编排模式</dt>')
    expect(html).not.toContain('其他自动编排')
  })

  it('hides uncompiled selection rather than implying it was executed', () => {
    const html = renderDetail({ generationMode: 'automatic', directorMode: 'cross_scene_v2' })
    expect(html).not.toContain('<dt>编排模式</dt>')
  })

  it('can recover the actual mode of an old task even without directorMode', () => {
    const html = renderDetail({ generationMode: 'automatic' }, 'FIXED_TEMPLATE', 'allwe-ootd-sol-simple-v1')
    expect(html).toContain('<dt>编排模式</dt><dd>遵循参考图</dd>')
  })

  it('does not guess a mode for old automatic tasks missing directorMode', () => {
    const html = renderDetail({ generationMode: 'automatic' })
    expect(html).not.toContain('<dt>编排模式</dt>')
    expect(html).not.toContain('未记录')
  })

  it('hides the director-mode field for custom prompts and unrelated workflows', () => {
    const custom = renderDetail({ generationMode: 'custom', directorMode: 'cross_scene_v2' })
    const deep = renderDetail({ generationMode: 'automatic', directorMode: 'cross_scene_v2' }, 'DEEP_VIDEO')
    expect(custom).not.toContain('<dt>编排模式</dt>')
    expect(deep).not.toContain('<dt>编排模式</dt>')
    expect(deep).not.toContain('<dt>制作方式</dt>')
  })
})
