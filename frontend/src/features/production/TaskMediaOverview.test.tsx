import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { MultiCutflowTask } from '../../api'
import type { Task, Variant } from '../../types'
import { cutflowDispatchPresentation } from './cutflowDispatchPresentation'
import { TaskMediaOverview } from './TaskMediaOverview'

const variant = (id: string, ready = true, failed = false): Variant => ({
  id, kind: 'STABLE', generationStatus: failed ? 'FAILED' : ready ? 'SUCCEEDED' : 'RUNNING',
  downloadStatus: ready ? 'READY' : 'PENDING',
  validationStatus: ready ? 'PASSED' : 'PENDING',
  downloadArtifactId: ready ? 'artifact-' + id : null,
}) as unknown as Variant
const task = (variants: Variant[]): Task => ({
  id: 'demo', phase: 'DELIVERABLE', title: '商品', createdAt: '2026-11-24T00:00:00Z',
  settings: { autoSubmitToCutflow: false }, variants,
}) as unknown as Task
const batch = (status: string, withOutput = false): MultiCutflowTask => ({
  id: 'cut-01', status, createdAt: '2026-11-24T00:01:00Z', updatedAt: '2026-11-24T00:02:00Z',
  errorCode: null, errorMessage: null, sources: [{ clipId: 'c', order: 1, taskId: 'demo', variantId: 'v1', artifactId: 'artifact-v1', title: '原片', durationSeconds: 10 }],
  outputs: withOutput ? [{ id: 'output-1', createdAt: '2026-11-24T00:05:00Z', mediaUrl: '/final.mp4', sha256: 'sha', reviewStatus: 'PENDING', durationSeconds: 10, width: 1080, height: 1920 }] : [],
})
const render = (variants: Variant[], batches: MultiCutflowTask[], inProgress = false, hasEdit = batches.length > 0) => {
  const last = batches[0] ?? null
  const status = cutflowDispatchPresentation(
    { autoSubmitToCutflow: false } as Task['settings'], hasEdit, inProgress, batches.some(item => item.outputs.length > 0),
    last?.status === 'FAILED', true, false, false, last,
  )
  return renderToStaticMarkup(<TaskMediaOverview task={task(variants)} batches={batches} cutflowActive={inProgress}
    cutflowStatus={status} canOperateCutflow onCutflowAction={() => {}} onPlayOriginal={() => {}} />)
}

describe('简洁详情里的 CUT 状态与下一步操作', () => {
  it('仅展示四个真实生产阶段，不包含生图占位', () => {
    const html = render([variant('v1')], [])
    expect(html.match(/class="allwe-detail-stages__step is-/g)).toHaveLength(4)
    for (const label of ['方案', '原片', '剪辑', '交付']) expect(html).toContain(`<small>${label}</small>`)
    expect(html).not.toContain('<small>生图</small>')
  })
  it('原片全部就绪、CUT 未接单时仅显示待送剪；保留选择入口', () => {
    const html = render([variant('v1'), variant('v2'), variant('v3')], [])
    expect(html).toContain('原片已就绪 · 待送剪')
    expect(html).not.toContain('>已完成</strong>')
    expect(html).toContain('allwe-detail-overview__action')
    expect(html).toContain('选择原片送剪')
    expect(html.match(/class="allwe-detail-overview__clip"/g)).toHaveLength(3)
  })

  it('只读权限不能暴露送剪执行按钮', () => {
    const status = cutflowDispatchPresentation({autoSubmitToCutflow: false} as Task['settings'], false, false, false)
    const html = renderToStaticMarkup(<TaskMediaOverview task={task([variant('v1')])} batches={[]} cutflowActive={false}
      cutflowStatus={status} canOperateCutflow={false} onCutflowAction={() => {}} onPlayOriginal={() => {}} />)
    expect(html).toContain('原片已就绪 · 待送剪')
    expect(html).not.toContain('allwe-detail-overview__action')
  })

  it('CUT 已接单排队时显示权威状态，不把排队当成成片', () => {
    const html = render([variant('v1')], [batch('ACCEPTED')], true)
    expect(html).toContain('CUT 已接单，排队中')
    expect(html).toContain('尚未回传')
    expect(html).not.toContain('成片已回传')
  })

  it('CUT 失败时显示失败原因入口，而不是已完成', () => {
    const html = render([variant('v1')], [batch('FAILED')])
    expect(html).toContain('剪辑任务失败')
    expect(html).toContain('查看原因')
    expect(html).toContain('role="alert"')
  })

  it('CUT 有成片文件时才确认已回传', () => {
    const html = render([variant('v1')], [batch('PENDING_REVIEW', true)])
    expect(html).toContain('成片已回传')
    expect(html).toContain('下载成片')
    expect(html).not.toContain('原片已就绪 · 待送剪')
  })

  it('部分原片成功时保留真实编号和异常占位', () => {
    const html = render([variant('v1', false, true), variant('v2')], [])
    expect(html).toContain('部分原片可用')
    expect(html).toContain('原片 1')
    expect(html).toContain('原片 2')
    expect(html).toContain('失败')
    expect(html).not.toContain('>已完成</strong>')
  })
})
