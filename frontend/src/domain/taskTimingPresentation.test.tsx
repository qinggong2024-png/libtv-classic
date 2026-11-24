import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Task } from '../types'
import type { CutflowHandoff, MultiCutflowTask } from '../api'
import { TaskTimingTimeline } from '../shared/components/TaskTiming'
import { stageElapsedSeconds, taskTimingStages, taskTimingStoppedAt } from './taskTimingPresentation'

const START = '2026-10-15T15:08:00Z'
const STOP = '2026-10-15T15:08:06Z'
const LATER = Date.parse('2026-10-16T03:08:00Z')

function task(phase = 'ACTION_REQUIRED'): Task {
  return {
    phase, createdAt: START, updatedAt: STOP, workflowKind: 'FIXED_TEMPLATE',
    generationProvider: 'bach', variants: [{ generationStatus: 'UNKNOWN' }],
    timing: { stages: [
      { key: 'SOURCE_UPLOAD', status: 'COMPLETED', durationSeconds: 1, activeStartedAt: null, attemptCount: null },
      { key: 'GENERATION', status: 'ACTIVE', durationSeconds: null, activeStartedAt: START, attemptCount: 1 },
      { key: 'DOWNLOAD', status: 'PENDING', durationSeconds: null, activeStartedAt: null, attemptCount: null },
    ] },
  } as Task
}

describe('task timing at interruption', () => {
  it('freezes a legacy unknown snapshot at its interruption time', () => {
    const source = task()
    const stage = taskTimingStages(source)[1]
    expect(stage.status).toBe('UNKNOWN')
    expect(stageElapsedSeconds(stage, LATER)).toBe(6)
    expect(stageElapsedSeconds(stage, LATER + 3600000)).toBe(6)
    expect(taskTimingStoppedAt(source)).toBe(STOP)
    expect(source.timing?.stages[1].status).toBe('ACTIVE')
  })

  it.each(['ACTION_REQUIRED', 'PARTIALLY_DELIVERABLE', 'DELIVERABLE', 'CANCELLED'])(
    'does not leave a live stage on a settled %s task', (phase) => {
      expect(taskTimingStages(task(phase)).some((stage) => stage.status === 'ACTIVE')).toBe(false)
    },
  )

  it('keeps genuine generation ticking', () => {
    const stage = taskTimingStages(task('GENERATING'))[1]
    expect(stage.status).toBe('ACTIVE')
    expect(stageElapsedSeconds(stage, LATER + 1000)).toBe(stageElapsedSeconds(stage, LATER)! + 1)
  })

  it('preserves a server-recorded duration instead of recomputing from parent updates', () => {
    const source = task()
    source.updatedAt = '2026-10-16T03:08:00Z'
    source.timing!.stages[1] = {
      ...source.timing!.stages[1], status: 'UNKNOWN', durationSeconds: 6,
      activeStartedAt: null, stoppedAt: STOP,
    }
    expect(stageElapsedSeconds(taskTimingStages(source)[1], LATER)).toBe(6)
    expect(taskTimingStoppedAt(source)).toBe(STOP)
  })

  it('does not invent a duration when recorded times are inconsistent', () => {
    const source = task()
    source.updatedAt = '2026-10-15T15:07:00Z'
    expect(taskTimingStages(source)[1].durationSeconds).toBeNull()
  })

  it('does not turn a failed legacy completion timestamp into successful generation', () => {
    const source = task()
    source.variants[0].generationStatus = 'FAILED'
    source.timing!.stages[1] = {
      ...source.timing!.stages[1], status: 'COMPLETED', durationSeconds: 6, activeStartedAt: null,
    }
    const stage = taskTimingStages(source)[1]
    expect(stage.status).toBe('FAILED')
    expect(stageElapsedSeconds(stage, LATER)).toBe(6)
    expect(taskTimingStoppedAt(source)).toBe(STOP)
  })

  it('shows uncertainty and unexecuted download without a spinner or task age', () => {
    const html = renderToStaticMarkup(<TaskTimingTimeline task={task()} />)
    expect(html).toContain('本地处理已中断 · 云端状态待确认')
    expect(html).toContain('待核实 · 6秒')
    expect(html).toContain('停止前处理耗时')
    expect(html).toContain('未执行')
    expect(html).not.toContain('spin')
    expect(html).not.toContain('任务年龄')
    expect(html).not.toContain('已进行')
  })
})


describe('compact task timing presentation', () => {
  it('shows the real stage rail immediately, with only the breakdown collapsed', () => {
    const markup = renderToStaticMarkup(<TaskTimingTimeline task={task('DELIVERABLE')} />)
    expect(markup).toContain('task-timing--condensed')
    expect(markup).toContain('task-timing-stages--rail')
    expect(markup).toContain('制作阶段与耗时')
    expect(markup).toContain('耗时明细 · 处理与等待')
    expect(markup.indexOf('task-timing-stages--rail')).toBeLessThan(markup.indexOf('<details class="task-timing-breakdown">'))
    expect(markup).not.toContain('task-timing-compact__steps')
    expect(markup).toMatch(/<details class="task-timing-breakdown">/)
    expect(markup).toContain('实际总耗时')
    expect(markup).toContain('处理耗时')
    expect(markup.match(/id="task-timing-title"/g)).toHaveLength(1)
  })
})

describe('waiting and processing timeline', () => {
  it('shows elapsed, observed processing and stage gaps without inventing parallel duration', () => {
    const source = task('DELIVERABLE')
    source.actionCapabilities = { canEdit: false, canConfirm: false, canCancel: false, canRetryAnalysis: false, canRegenerate: false, canDownload: true }
    source.variants[0].generationStatus = 'COMPLETED'
    const start = Date.parse(START)
    const at = (seconds: number) => new Date(start + seconds * 1000).toISOString()
    source.timing!.stages[1] = {
      key: 'GENERATION', status: 'COMPLETED', durationSeconds: 6,
      startedAt: at(4), endedAt: at(10), activeStartedAt: null, attemptCount: 1,
    }
    source.timing!.stages[2] = {
      key: 'DOWNLOAD', status: 'COMPLETED', durationSeconds: 2,
      startedAt: at(10), endedAt: at(12), activeStartedAt: null, attemptCount: 1,
    }
    const html = renderToStaticMarkup(<TaskTimingTimeline task={source} />)
    expect(html).toContain('<dt>实际总耗时</dt><dd>12 秒</dd>')
    expect(html).toContain('<dt>累计处理耗时</dt><dd>8 秒</dd>')
    expect(html).toContain('<dt>累计等待耗时</dt><dd>4 秒</dd>')
    expect(html).toContain('前置间隔 4秒')
  })
})

describe('editing in task progress', () => {
  function sourceTask() {
    const source = task('DELIVERABLE')
    source.actionCapabilities = { ...source.actionCapabilities, canConfirm: false, canDownload: true }
    source.variants = [{ ...source.variants[0], id: 'variant-1', downloadArtifactId: 'original-1', generationStatus: 'COMPLETED' }]
    source.timing!.stages[1] = { ...source.timing!.stages[1], status: 'COMPLETED', durationSeconds: 6, activeStartedAt: null }
    return source
  }
  function handoff(status: string, finished = false): CutflowHandoff {
    return { id: 'edit-1', handoffKey: 'edit-1', sourceArtifactId: 'original-1', status,
      cutflowBatchId: null, cutflowEditorUrl: null, errorMessage: null,
      outputs: finished ? [{ id: 'output-1', sha256: 'a', reviewStatus: 'PENDING', durationSeconds: 5, createdAt: STOP, mediaUrl: '/edited.mp4' }] : [] }
  }
  it('adds recorded material analysis and completed CUT time to the task total without double-counting', () => {
    const source = sourceTask()
    source.timing!.stages.push({ key: 'ANALYSIS', status: 'COMPLETED', durationSeconds: 4, activeStartedAt: null, attemptCount: 1 })
    const finished = { ...handoff('PENDING_REVIEW', true), createdAt: START, updatedAt: STOP }
    const html = renderToStaticMarkup(<TaskTimingTimeline task={source} cutflowStates={{
      'variant-1': { enabled: true, handoffs: [finished] },
    }} />)
    expect(html).toContain('素材分析')
    expect(html).toContain('task-timing-stage is-completed')
    expect(html).toContain('剪辑完成 · 6秒')
    expect(html).not.toContain('<dt>剪辑（含等待）</dt>')
    // Source upload is hidden and excluded: generation 6s + analysis 4s + handoff 6s.
    expect(html).toContain('<dt>累计处理耗时</dt><dd>16 秒</dd>')
    expect(html).not.toContain('素材上传')
  })

  it('shows the four core stages with no upload, even for fixed templates', () => {
    const source = sourceTask()
    source.timing!.stages.push({ key: 'ANALYSIS', status: 'PENDING', durationSeconds: null, activeStartedAt: null, attemptCount: null })
    source.timing!.stages.push({ key: 'MATERIALIZATION', status: 'COMPLETED', durationSeconds: 12, activeStartedAt: null, attemptCount: 1 })
    const batch = {
      id: 'batch-1', status: 'PENDING_REVIEW', createdAt: START, updatedAt: STOP,
      sources: [{ clipId: 'clip-1', order: 1, taskId: 'task-1', variantId: 'variant-1', artifactId: 'original-1', title: 'original', durationSeconds: 8 }],
      outputs: [{ id: 'result-1', createdAt: STOP }],
    } as MultiCutflowTask
    const html = renderToStaticMarkup(<TaskTimingTimeline task={source} multiCutflows={[batch]} />)
    const stages = [...html.matchAll(/<li[^>]*class="task-timing-stage[^>]*"/g)]
    expect(stages).toHaveLength(4)
    expect(html.indexOf('素材分析')).toBeLessThan(html.indexOf('task-timing-label-short">生成'))
    expect(html).toContain('未单独计时')
    expect(html).not.toContain('素材上传')
    expect(html).not.toContain('素材准备')
    expect(html).not.toContain('<dt>剪辑（含等待）</dt>')
    expect(html).toContain('<dt>累计处理耗时</dt><dd>12 秒</dd>')
  })

  it('offers editing only after the source video is ready', () => {
    expect(renderToStaticMarkup(<TaskTimingTimeline task={task()} />)).not.toContain('task-timing-stage--cutflow')
    const html = renderToStaticMarkup(<TaskTimingTimeline task={sourceTask()} />)
    expect(html).toContain('待送剪辑')
    expect(html).toContain('可在下方送入剪辑')
  })
  it.each([
    ['EDITING', false, '正在剪辑', 'active'],
    ['PENDING_REVIEW', false, '等待成片回传', 'active'],
    ['PENDING_REVIEW', true, '剪辑完成', 'completed'],
    ['FAILED', false, '剪辑交接失败', 'failed'],
    ['CANCELED', false, '剪辑已取消', 'cancelled'],
    ['NEW_UNKNOWN_STATUS', false, '剪辑状态待确认', 'unknown'],
  ] as const)('shows %s from the linked editing handoff', (status, finished, label, cssStatus) => {
    const html = renderToStaticMarkup(<TaskTimingTimeline task={sourceTask()} cutflowStates={{ 'variant-1': { enabled: true, handoffs: [handoff(status, finished)] } }} />)
    expect(html).toContain(`task-timing-stage--cutflow is-${cssStatus}`)
    expect(html).toContain(`<strong id="task-timing-title">${label}</strong>`)
  })
  it('does not use an edit from an older source artifact', () => {
    const old = { ...handoff('EDITING'), sourceArtifactId: 'old-original' }
    const html = renderToStaticMarkup(<TaskTimingTimeline task={sourceTask()} cutflowStates={{ 'variant-1': { enabled: true, handoffs: [old] } }} />)
    expect(html).toContain('待送剪辑')
    expect(html).not.toContain('正在剪辑')
  })
  it('keeps an active second variant visible when the first edit is finished', () => {
    const source = sourceTask()
    source.variants.push({ ...source.variants[0], id: 'variant-2', downloadArtifactId: 'original-2' })
    const html = renderToStaticMarkup(<TaskTimingTimeline task={source} cutflowStates={{
      'variant-1': { enabled: true, handoffs: [handoff('PENDING_REVIEW', true)] },
      'variant-2': { enabled: true, handoffs: [{ ...handoff('EDITING'), sourceArtifactId: 'original-2' }] },
    }} />)
    expect(html).toContain('<strong id="task-timing-title">正在剪辑</strong>')
    expect(html).toContain('1/2 个原片已剪辑')
  })
  it('shows one task-level status for a multi-source edit', () => {
    const source = sourceTask()
    source.variants.push({ ...source.variants[0], id: 'variant-2', downloadArtifactId: 'original-2' })
    const batch = {
      id: 'batch-1',
      status: 'PENDING_REVIEW',
      errorCode: null,
      errorMessage: null,
      createdAt: START,
      updatedAt: STOP,
      sources: [
        { clipId: 'clip-1', order: 1, taskId: 'task-1', variantId: 'variant-1', artifactId: 'original-1', title: '套装', durationSeconds: 8 },
        { clipId: 'clip-2', order: 2, taskId: 'task-1', variantId: 'variant-2', artifactId: 'original-2', title: '套装', durationSeconds: 8 },
      ],
      outputs: [{ id: 'merged-1', sha256: 'a'.repeat(64), reviewStatus: 'PENDING', durationSeconds: 9, width: 720, height: 1280, createdAt: STOP, mediaUrl: '/merged.mp4' }],
    } as MultiCutflowTask
    const html = renderToStaticMarkup(<TaskTimingTimeline task={source} multiCutflows={[batch]} />)
    expect(html).toContain('<strong id="task-timing-title">合并剪辑完成</strong>')
    expect(html).toContain('2 条原片合并')
    expect(html).toContain('aria-label="剪辑，成片待审核"')
  })

  it('marks the fourth stage complete when the new single-source edit has returned a file', () => {
    const source = sourceTask()
    const batch = {
      id: 'single-1', status: 'PENDING_REVIEW', errorCode: null, errorMessage: null,
      createdAt: START, updatedAt: STOP,
      sources: [{ clipId: 'clip-1', order: 1, taskId: 'task-1', variantId: 'variant-1', artifactId: 'original-1', title: '原片', durationSeconds: 8 }],
      outputs: [{ id: 'edited-1', sha256: 'a'.repeat(64), reviewStatus: 'PENDING', durationSeconds: 9, width: 1080, height: 1920, createdAt: STOP, mediaUrl: '/edited.mp4' }],
    } as MultiCutflowTask
    const html = renderToStaticMarkup(<TaskTimingTimeline task={source} multiCutflows={[batch]} />)
    expect(html).toContain('task-timing-stage--cutflow is-completed')
    expect(html).toContain('剪辑完成')
    expect(html).toContain('单片剪辑')
    expect(html).not.toContain('待送剪辑')
  })

  it('describes a published multi-source package as sent and awaiting acknowledgement', () => {
    const source = sourceTask()
    source.variants.push({ ...source.variants[0], id: 'variant-2', downloadArtifactId: 'original-2' })
    const batch = {
      id: 'batch-waiting',
      status: 'READY',
      errorCode: 'CUTFLOW_STATUS_MISSING',
      errorMessage: '已送入剪辑，CUT 尚未回写接单状态；系统会继续自动检查，无需重复送剪。',
      createdAt: START,
      updatedAt: STOP,
      sources: [
        { clipId: 'clip-1', order: 1, taskId: 'task-1', variantId: 'variant-1', artifactId: 'original-1', title: '套装', durationSeconds: 8 },
        { clipId: 'clip-2', order: 2, taskId: 'task-1', variantId: 'variant-2', artifactId: 'original-2', title: '套装', durationSeconds: 8 },
      ],
      outputs: [],
    } as MultiCutflowTask
    const html = renderToStaticMarkup(<TaskTimingTimeline task={source} multiCutflows={[batch]} />)
    expect(html).toContain('<strong id="task-timing-title">合并剪辑中</strong>')
    expect(html).toContain('等待 CUT 接单超时，正在核对')
    expect(html).not.toContain('待送剪辑')
  })
})
