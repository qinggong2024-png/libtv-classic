import {isValidElement, type ReactElement, type ReactNode} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {getMotionActions, getSiteStyleProfiles, getVideoTemplates} from '../../api'
import {DataFreshness} from '../../shared/components/DataFreshness'
import type {MotionActionSummary, SiteStyleProfileSummary, VideoTemplateSummary} from '../../types'
import {ContentLibraryView} from './ContentLibraryView'

// Execute actual component handlers and reads; only hook scheduling is replaced.
const hooks = vi.hoisted(() => ({values: [] as unknown[], cursor: 0, mounting: true, effects: [] as Array<() => void | (() => void)>}))
vi.mock('react', async (original) => ({
  ...await original<typeof import('react')>(),
  useState(initial: unknown) {
    const index = hooks.cursor++
    if (hooks.mounting) hooks.values[index] = typeof initial === 'function' ? initial() : initial
    return [hooks.values[index], (value: unknown) => {
      hooks.values[index] = typeof value === 'function' ? value(hooks.values[index]) : value
    }]
  },
  useRef(initial: unknown) {
    const index = hooks.cursor++
    if (hooks.mounting) hooks.values[index] = {current: initial}
    return hooks.values[index]
  },
  useMemo: <T,>(factory: () => T) => factory(),
  useCallback: <T,>(callback: T) => callback,
  useDeferredValue: <T,>(value: T) => value,
  useEffect(effect: () => void | (() => void)) { if (hooks.mounting) hooks.effects.push(effect) },
}))
vi.mock('../../api', async (original) => ({
  ...await original<typeof import('../../api')>(),
  getMotionActions: vi.fn(), getSiteStyleProfiles: vi.fn(), getVideoTemplates: vi.fn(),
}))
type Node = ReactElement<Record<string, unknown>>
const action: MotionActionSummary = {
  id: 'test-action', label: '测试动作', category: 'detail', instruction: '测试原文 instruction', purpose: '测试用途',
  compatibleTemplates: ['test-template'], requirements: [], avoid: [], riskLevel: 'low',
  libraryScope: 'general', brandId: null, baseActionId: null,
}
const profile: SiteStyleProfileSummary = {
  id: 'test-style', name: '测试风格', version: 'v1', status: 'active', evidenceLevel: 'verified_site',
  siteUrl: '', sourceUrls: [], retrievedAt: '', tone: '测试语气', expressionLanguage: '测试表达', audience: '',
  palette: [], typography: '', cameraLanguage: '', openingRule: '测试开场原文', middleRule: '', endingRule: '',
  preferredShots: [], preferredActions: [], pacing: '', productPriorities: [], forbiddenMismatches: [], researchNote: '',
  motionEnergy: '', primaryMotionPool: [], microMotionPool: [], expressionPool: [], cameraPool: [], forbiddenMotion: [],
  preferredEnvironment: [], productRiskRules: [], dedupeRecentCount: 1, dedupeComboWindow: 1, dedupeFamilyWindow: 1,
  dedupeFamilyLimit: 1, libraryScope: 'general', brandId: null,
}
const template = {id: 'test-template', name: '测试模板名称'} as VideoTemplateSummary
const flush = () => new Promise((resolve) => setTimeout(resolve, 0))
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (cause: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return {promise, resolve, reject}
}
function nodes(value: ReactNode): Node[] {
  if (Array.isArray(value)) return value.flatMap(nodes)
  if (!isValidElement<Record<string, unknown>>(value)) return []
  return [value, ...nodes(value.props.children as ReactNode)]
}
function content(node: Node): string {
  const value = node.props.children
  return (Array.isArray(value) ? value : [value]).map((child) => isValidElement<Record<string, unknown>>(child)
    ? content(child) : typeof child === 'string' || typeof child === 'number' ? String(child) : '').join('')
}
async function mount() {
  const render = () => {
    hooks.cursor = 0
    return ContentLibraryView({actorId: null, canDeleteSubjects: false, canEditCatalog: true}) as Node
  }
  const initialMarkup = renderToStaticMarkup(render())
  hooks.mounting = false
  const cleanups = hooks.effects.map((effect) => effect())
  await flush()
  const find = (predicate: (node: Node) => boolean) => {
    const node = nodes(render()).find(predicate)
    if (!node) throw new Error('Expected library control missing')
    return node
  }
  const click = (node: Node) => (node.props.onClick as () => void)()
  return {
    initialMarkup, markup: () => renderToStaticMarkup(render()),
    select: (id: string) => click(find((node) => node.props.id === id + '-tab')),
    reload: () => click(find((node) => node.type === 'button' && content(node) === '重新加载内容库')),
    disabled: () => find((node) => node.type === 'button' && content(node) === '重新加载内容库').props.disabled,
    freshness: () => find((node) => node.type === DataFreshness).props,
    alerts: () => nodes(render()).filter((node) => node.props.role === 'alert').map(content),
    unmount: () => cleanups.forEach((cleanup) => cleanup?.()),
  }
}
beforeEach(() => {
  vi.resetAllMocks()
  hooks.values = []; hooks.cursor = 0; hooks.mounting = true; hooks.effects = []
  vi.stubGlobal('fetch', vi.fn(() => { throw new Error('Real API calls forbidden') }))
  vi.mocked(getMotionActions).mockResolvedValue([action])
  vi.mocked(getSiteStyleProfiles).mockResolvedValue([profile])
  vi.mocked(getVideoTemplates).mockResolvedValue([template])
})
afterEach(() => {
  expect(fetch).not.toHaveBeenCalled()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})
describe('ContentLibraryView read recovery', () => {
  it('preserves labelled panels and keyboard-focusable tabs during initial loading', async () => {
    const {initialMarkup} = await mount()
    expect(initialMarkup).toContain('role="tablist" aria-label="内容库归属"')
    expect(initialMarkup).toContain('id="general-panel" role="tabpanel" aria-labelledby="general-tab"')
    expect(initialMarkup).toContain('id="actions-panel" role="tabpanel" aria-labelledby="actions-tab"')
    expect(initialMarkup).toContain('aria-controls="actions-panel"')
    expect(initialMarkup).toContain('tabindex="0"')
    expect(initialMarkup).toContain('tabindex="-1"')
    expect(initialMarkup).not.toContain('新增第一条')
  })
  it.each(['actions', 'styles'] as const)('keeps failed %s distinct from empty when the other read succeeds', async (failed) => {
    if (failed === 'actions') vi.mocked(getMotionActions).mockRejectedValue(new Error('故障注入'))
    else vi.mocked(getSiteStyleProfiles).mockRejectedValue(new Error('故障注入'))
    const view = await mount()
    view.select(failed)
    expect(view.markup()).not.toContain('新增第一条')
    expect(view.markup()).not.toContain('当前库尚未录入内容')
    expect(view.markup()).not.toContain('已保留')
    expect(view.markup()).not.toContain('当前显示上次成功数据')
    expect(view.freshness()).toMatchObject({timestamp: null, stale: false})
    expect(view.disabled()).toBe(false)
    expect(view.alerts()).toHaveLength(1)
    view.select(failed === 'actions' ? 'styles' : 'actions')
    expect(view.markup()).toContain(failed === 'actions' ? '测试风格' : '测试动作')
    expect(view.freshness().timestamp).toEqual(expect.any(Number))
    expect(view.freshness().stale).toBe(false)
  })
  it('reports first-load total failure without claiming cached content or successful sync', async () => {
    vi.mocked(getMotionActions).mockRejectedValue(new Error('动作故障'))
    vi.mocked(getSiteStyleProfiles).mockRejectedValue(new Error('风格故障'))
    vi.mocked(getVideoTemplates).mockRejectedValue(new Error('模板故障'))
    const view = await mount()
    expect(view.alerts()).toHaveLength(3)
    for (const tab of ['actions', 'styles', 'brand']) {
      view.select(tab)
      expect(view.markup()).not.toContain('新增第一条')
      expect(view.markup()).not.toContain('已保留')
      expect(view.markup()).toContain('尚未同步')
      expect(view.disabled()).toBe(false)
    }
  })
  it.each(['actions', 'styles'] as const)('shows empty state only for successfully loaded %s', async (tab) => {
    vi.mocked(getMotionActions).mockResolvedValue([])
    vi.mocked(getSiteStyleProfiles).mockResolvedValue([])
    const view = await mount()
    view.select(tab)
    expect(view.markup()).toContain('新增第一条' + (tab === 'actions' ? '动作' : '风格'))
    view.select('brand')
    expect(view.markup()).toContain('还没有品牌专属' + (tab === 'actions' ? '动作' : '风格'))
  })
  it('retries from the real button, blocks repeated clicks and recovers all reads', async () => {
    vi.mocked(getMotionActions).mockRejectedValueOnce(new Error('动作故障'))
    vi.mocked(getSiteStyleProfiles).mockRejectedValueOnce(new Error('风格故障'))
    const view = await mount()
    view.reload()
    expect(view.disabled()).toBe(true)
    view.reload()
    await flush()
    expect(getMotionActions).toHaveBeenCalledTimes(2)
    expect(getSiteStyleProfiles).toHaveBeenCalledTimes(2)
    expect(getVideoTemplates).toHaveBeenCalledTimes(2)
    expect(view.markup()).toContain('测试原文 instruction')
    expect(view.alerts()).toEqual([])
    view.select('styles')
    expect(view.markup()).toContain('测试开场原文')
  })
  it('keeps cached cards during retry and failure with independent last-success timestamps', async () => {
    const clock = vi.spyOn(Date, 'now').mockReturnValue(1000)
    const firstStyles = deferred<SiteStyleProfileSummary[]>()
    vi.mocked(getSiteStyleProfiles).mockReturnValueOnce(firstStyles.promise)
    const view = await mount()
    expect(view.markup()).toContain('测试动作')
    expect(view.freshness().timestamp).toBe(1000)
    clock.mockReturnValue(2000)
    firstStyles.resolve([profile])
    await flush()
    view.select('styles')
    expect(view.freshness().timestamp).toBe(2000)
    const actions = deferred<MotionActionSummary[]>()
    const styles = deferred<SiteStyleProfileSummary[]>()
    vi.mocked(getMotionActions).mockReturnValueOnce(actions.promise)
    vi.mocked(getSiteStyleProfiles).mockReturnValueOnce(styles.promise)
    clock.mockReturnValue(3000)
    view.reload()
    expect(view.markup()).toContain('测试风格')
    view.select('actions')
    expect(view.markup()).toContain('测试动作')
    actions.reject(new Error('动作刷新故障'))
    styles.reject(new Error('风格刷新故障'))
    await flush()
    expect(view.markup()).toContain('测试原文 instruction')
    expect(view.freshness()).toMatchObject({timestamp: 1000, stale: true})
    expect(view.alerts().every((alert) => alert.includes('已保留上次成功读取的结果'))).toBe(true)
    view.select('styles')
    expect(view.markup()).toContain('测试开场原文')
    expect(view.freshness()).toMatchObject({timestamp: 2000, stale: true})
    view.reload()
    await flush()
    expect(view.freshness()).toMatchObject({timestamp: 3000, stale: false})
    expect(view.alerts()).toEqual([])
  })
  it('does not show a previous empty result as current empty state after refresh failure', async () => {
    vi.mocked(getMotionActions).mockResolvedValueOnce([]).mockRejectedValueOnce(new Error('刷新故障'))
    const view = await mount()
    expect(view.markup()).toContain('新增第一条动作')
    view.reload()
    await flush()
    expect(view.markup()).not.toContain('新增第一条动作')
    expect(view.freshness().stale).toBe(true)
  })
  it('keeps successful lists visible while template names are pending or fail, then recovers names', async () => {
    const pending = deferred<VideoTemplateSummary[]>()
    vi.mocked(getVideoTemplates).mockReturnValueOnce(pending.promise)
    const view = await mount()
    expect(view.markup()).toContain('测试动作')
    expect(view.markup()).toContain('test-template')
    pending.reject(new Error('模板故障'))
    await flush()
    expect(view.markup()).toContain('测试动作')
    expect(view.freshness().stale).toBe(false)
    expect(view.alerts()[0]).toContain('模板名称')
    expect(view.alerts()[0]).not.toContain('已保留')
    view.select('styles')
    expect(view.markup()).toContain('测试风格')
    view.reload()
    await flush()
    view.select('actions')
    expect(view.markup()).toContain('测试模板名称')
    expect(view.alerts()).toEqual([])
  })
  it('retains cached template names when only template refresh fails', async () => {
    const view = await mount()
    vi.mocked(getVideoTemplates).mockRejectedValueOnce(new Error('模板刷新故障'))
    view.reload()
    await flush()
    expect(view.markup()).toContain('测试模板名称')
    expect(view.markup()).toContain('测试动作')
    expect(view.alerts()[0]).toContain('已保留上次成功读取的结果')
    expect(view.freshness().stale).toBe(false)
  })
  it('ignores reads settling after unmount', async () => {
    const pending = deferred<MotionActionSummary[]>()
    vi.mocked(getMotionActions).mockReturnValueOnce(pending.promise)
    const view = await mount()
    view.unmount()
    const previousState = [...hooks.values]
    pending.resolve([action])
    await flush()
    expect(hooks.values).toEqual(previousState)
  })
})
