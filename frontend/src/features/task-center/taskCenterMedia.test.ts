import { describe, expect, it } from 'vitest'
import type { MultiCutflowTask } from '../../api'
import type { Task, Variant } from '../../types'
import { taskCutCoverStatus, taskPlayableMedia, taskPrimaryMedia } from './taskCenterMedia'
import { taskCenterState } from '../../domain/taskCenterState'

const variants = (count: number, ready = count) => Array.from({ length: count }, (_, i) => ({
  id: 'v' + (i + 1),
  downloadArtifactId: i < ready ? 'artifact-' + (i + 1) : null,
  generationStatus: i < ready ? 'SUCCEEDED' : 'PENDING',
})) as unknown as Variant[]
const task = (count: number, ready = count) => ({ variants: variants(count, ready) }) as Task
const batch = (id: string, createdAt: string, mediaUrl: string): MultiCutflowTask => ({
  id, status: 'COMPLETED', createdAt, updatedAt: createdAt, errorCode: null, errorMessage: null,
  sources: [], outputs: [{ id, createdAt, mediaUrl, sha256: 'sha', reviewStatus: 'PENDING', durationSeconds: 10, width: 1080, height: 1920 }],
})

describe('任务封面剪辑状态', () => {
  const raw = taskPrimaryMedia(taskPlayableMedia(task(1), []))
  const final = taskPrimaryMedia(taskPlayableMedia(task(1), [batch('old', '2026-11-01T00:00:00Z', '/cut/old.mp4')]))
  const resolved = (label: string, group: ReturnType<typeof taskCenterState>['group'] = 'active', manualSelection = false) => ({ group, label, manualSelection, readyCount: 1 })
  it.each([
    ['等待自动送剪', '等待剪辑'],
    ['CUT 已接单，排队中', '等待剪辑'],
    ['CUT 正在剪辑', '正在剪辑中'],
    ['等待成片回传', '等待成片回传'],
  ])('maps %s without pretending raw is final', (label, expected) => {
    expect(taskCutCoverStatus(raw, resolved(label), 1)).toEqual({ label: expected, busy: true })
  })
  it('keeps old final identifiable during recut and clears overlay when done', () => {
    expect(taskCutCoverStatus(final, resolved('CUT 正在剪辑'), 1)).toEqual({ label: '重新剪辑中', busy: true })
    expect(taskCutCoverStatus(final, resolved('成片可查看', 'done'), 1)).toBeNull()
  })
  it('keeps manual and failed originals clear, never guesses unknown or generation as editing', () => {
    expect(taskCutCoverStatus(raw, resolved('等待手动送剪', 'attention', true), 1)).toEqual({ label: '等待送剪', busy: false })
    expect(taskCutCoverStatus(raw, resolved('剪辑需要处理', 'attention'), 1)).toEqual({ label: '剪辑异常', busy: false })
    for (const label of ['正在核对剪辑状态', '状态待确认', '正在生成原片', '自动送剪等待较久']) {
      expect(taskCutCoverStatus(raw, resolved(label), 1)).toBeNull()
    }
    expect(taskCutCoverStatus(null, resolved('CUT 正在剪辑'), 1)).toBeNull()
  })
})

describe('部分原片完成时的封面', () => {
  const candidate = (count: number, ready: number, phase: Task['phase'] = 'GENERATING') => ({
    ...task(count, ready), id: 'partial-task', phase,
    settings: { autoSubmitToCutflow: true },
  }) as Task
  const overlay = (item: Task, batches: MultiCutflowTask[] = []) => taskCutCoverStatus(
    taskPrimaryMedia(taskPlayableMedia(item, batches)), taskCenterState(item, batches), item.variants.length,
  )
  it.each([[2, 1], [3, 2], [4, 1]])('shows the actual completed count for %i originals, %i ready', (count, ready) => {
    expect(overlay(candidate(count, ready))).toEqual({ label: `正在生成原片 ${ready}/${count}`, busy: true })
  })
  it.each([
    ['QUEUED', '等待生成原片'],
    ['DOWNLOADING', '正在下载原片'],
    ['VALIDATING', '正在校验原片'],
  ] as const)('shows %s instead of mislabeling as editing', (phase, label) => {
    expect(overlay(candidate(2, 1, phase))).toEqual({ label: label + ' 1/2', busy: true })
  })
  it('keeps recovery, unknown, failed, cancelled and manual states distinct', () => {
    const item = candidate(2, 1)
    const pending = item.variants[1]
    expect(overlay({ ...item, variants: [item.variants[0], { ...pending, providerStatus: 'waiting_balance' }] })).toBeNull()
    expect(overlay({ ...item, variants: [item.variants[0], { ...pending, generationStatus: 'UNKNOWN' }] })).toBeNull()
    expect(overlay(candidate(2, 1, 'ACTION_REQUIRED'))).toBeNull()
    expect(overlay(candidate(2, 1, 'CANCELLED'))).toBeNull()
    expect(overlay({ ...candidate(2, 1, 'PARTIALLY_DELIVERABLE'), settings: { ...item.settings, autoSubmitToCutflow: false } })).toEqual({ label: '等待送剪', busy: false })
    expect(overlay(candidate(2, 0))).toBeNull()
  })
  it('moves from partial raw to editing and finally a clear final cover', () => {
    const item = candidate(2, 2, 'DELIVERABLE')
    const editing = { ...batch('edit', '2026-11-02T00:00:00Z', ''), status: 'EDITING', outputs: [], sources: [{ taskId: item.id, variantId: 'v1', clipId: 'clip-1', order: 0, artifactId: 'artifact-1', title: 'clip', durationSeconds: 10 }] } satisfies MultiCutflowTask
    expect(overlay(item)).toEqual({ label: '等待剪辑', busy: true })
    expect(overlay(item, [editing])).toEqual({ label: '正在剪辑中', busy: true })
    expect(overlay(item, [{ ...batch('edit', '2026-11-02T00:00:00Z', '/cut/final.mp4'), sources: editing.sources }])).toBeNull()
  })
})

describe('任务视频可用性与封面优先级', () => {
  it('成片优先，仍保留全部已生成原片供选择与下载', () => {
    const media = taskPlayableMedia(task(2), [
      batch('older', '2026-11-01T00:00:00Z', '/cut/older.mp4'),
      batch('newer', '2026-11-02T00:00:00Z', '/cut/newer.mp4'),
    ])
    expect(media.map(item => item.kind)).toEqual(['final', 'original', 'original'])
    expect(taskPrimaryMedia(media)).toMatchObject({ kind: 'final', mediaUrl: '/cut/newer.mp4' })
  })

  it.each([1, 2, 3, 4])('%i 条原片均完整呈现', count => {
    const media = taskPlayableMedia(task(count), [])
    expect(media).toHaveLength(count)
    expect(media.map(item => item.label)).toEqual(Array.from({ length: count }, (_, i) => '原片 ' + (i + 1)))
  })

  it('部分成功显示已有原片；全未完成时退回任务进度', () => {
    expect(taskPlayableMedia(task(4, 1), [])).toHaveLength(1)
    expect(taskPrimaryMedia(taskPlayableMedia(task(2, 0), []))).toBeNull()
  })

  it('部分成功必须保留原始片段编号，而不是给第二条成功片重编号', () => {
    const candidate = task(3, 0)
    candidate.variants[1].downloadArtifactId = 'artifact-second'
    expect(taskPlayableMedia(candidate, []).map(item => item.label)).toEqual(['原片 2'])
  })

  it('CUT 只接单但没有成片文件时不能虚报成片', () => {
    const pending = { ...batch('queued', '2026-11-02T00:00:00Z', ''), outputs: [] }
    const media = taskPlayableMedia(task(2), [pending])
    expect(media[0]).toMatchObject({ kind: 'original' })
  })
})
