import { isValidElement, type ReactElement, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, createVideoTask, getKlingElements, getSiteStyleProfiles, getTask, getVideoTemplates, querySubmissionRecovery, uploadStoryboard } from '../../api'
import { CustomPromptField } from '../../CustomPromptField'
import { SubjectInputPanel } from '../../SubjectInputPanel'
import { GenerationAdvancedSettings, GenerationBrief } from '../../shared/components/GenerationAdvancedSettings'
import type { SystemStatus } from '../../types'
import { MaterialCard } from './MaterialCard'
import { ProductionComposer } from './ProductionComposer'
import { ProductHistoryPicker } from './ProductHistoryPicker'
import type { RemixSeed } from './productionTypes'

// Exercise the composer's actual event handlers without a browser dependency.
// Child components remain React elements; their separate suites cover rendering.
const hooks = vi.hoisted(() => ({ values: [] as unknown[], cursor: 0, mounting: true, effects: [] as Array<() => unknown> }))
vi.mock('react', async (importOriginal) => ({
  ...await importOriginal<typeof import('react')>(),
  useState(initial: unknown) {
    const index = hooks.cursor++
    if (hooks.mounting) hooks.values[index] = typeof initial === 'function' ? initial() : initial
    return [hooks.values[index], (value: unknown) => {
      hooks.values[index] = typeof value === 'function' ? value(hooks.values[index]) : value
    }]
  },
  useRef(initial: unknown) {
    const index = hooks.cursor++
    if (hooks.mounting) hooks.values[index] = { current: initial }
    return hooks.values[index]
  },
  useMemo: <T,>(factory: () => T) => factory(),
  useEffect(effect: () => unknown) { if (hooks.mounting) hooks.effects.push(effect) },
}))
vi.mock('../../api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../api')>(),
  getVideoTemplates: vi.fn(), getSiteStyleProfiles: vi.fn(), getKlingElements: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  uploadStoryboard: vi.fn(), createVideoTask: vi.fn(), getTask: vi.fn(), querySubmissionRecovery: vi.fn(),
}))
vi.mock('../../useAccountSiteSelection', () => ({
  useAccountSiteSelection: () => ({
    choices: { sites: [{ id: 'brand' }], defaultProfileId: 'brand' }, error: '',
    siteProfileId: 'brand', setSiteProfileId: vi.fn(), defaultProfileId: 'brand',
  }),
}))

type Node = ReactElement<Record<string, unknown>>
const image = (name: string) => new File([name], name, { type: 'image/png' })
const seed = (names = ['first.png']): RemixSeed => ({ id: 'seed', storyboards: names.map(image), products: [], requirement: '用户补充要求' })
const system = {
  sharedAuthentication: true,
  videoGeneration: {
    backend: 'cli', provider: 'kling_official_cli', model: 'kling-video-v3_0_omni', models: ['kling-video-v3_0_omni'],
    available: true, authenticated: true, imageInputLimit: 7, supportsProductReferences: true, supportsElementReferences: false,
    videoDurations: Array.from({ length: 13 }, (_, n) => String(n + 3)), tailImageVideoDurations: ['5', '10'],
  },
} as SystemStatus

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
function find(tree: Node, predicate: (node: Node) => boolean): Node {
  const found = nodes(tree).find(predicate)
  if (!found) throw new Error('Expected composer control was missing')
  return found
}
function invoke(node: Node, handler: string, value?: unknown) {
  return (node.props[handler] as (value?: unknown) => unknown)(value)
}
const autoCutflowCheckbox = (tree: Node) => find(find(tree, (node) => node.props.className === 'composer-output-autocut'), (node) => node.type === 'input' && node.props.type === 'checkbox')
const submitButton = (tree: Node) => find(tree, (node) => node.type === 'button' && String(node.props.className).includes('generation-priced-button'))
const route = (tree: Node, label: string) => find(tree, (node) => node.props.role === 'radio' && content(node).includes(label))
const durations = (tree: Node) => nodes(find(tree, (node) => node.props.id === 'video-duration')).filter((node) => node.type === 'option').map((node) => node.props.value)
const button = (tree: Node, label: string) => find(tree, (node) => node.type === 'button' && content(node).includes(label))
function importBatch(tree: Node, keys = ['a', 'b', 'c']) {
  invoke(nodes(tree).filter((node) => node.type === 'input' && node.props.type === 'file')[1], 'onChange', {
    target: { files: keys.flatMap((key) => [`${key}_front.png`, `${key}_shot_1.png`]).map(image) }, currentTarget: { value: '' },
  })
}
const recovery = (state: Awaited<ReturnType<typeof querySubmissionRecovery>>['state'], resourceId: string | null = null) => ({
  state, resourceId, source: 'local', checkedAt: '', auditId: 'audit', resourceStatus: null, stateVersion: null, retryAllowed: false as const,
})

async function mount(input = seed(), actorId: string | null = 'member', status = system) {
  const props = { seed: input, actorId, system: status, onCreated: vi.fn() }
  const render = () => {
    hooks.cursor = 0
    return ProductionComposer(props) as Node
  }
  render()
  hooks.mounting = false
  for (const effect of hooks.effects) effect()
  await new Promise((resolve) => setTimeout(resolve, 0))
  return { render, props }
}

beforeEach(() => {
  vi.resetAllMocks()
  hooks.values = []; hooks.cursor = 0; hooks.mounting = true; hooks.effects = []
  const stored = new Map<string, string>()
  const storage = { getItem: (key: string) => stored.get(key) ?? null, setItem: (key: string, value: string) => stored.set(key, value), removeItem: (key: string) => stored.delete(key) }
  vi.stubGlobal('localStorage', storage)
  vi.stubGlobal('window', { localStorage: storage, matchMedia: () => ({ matches: true }) })
  vi.mocked(getKlingElements).mockResolvedValue({ items: [], total: 0, pageNum: 1, pageSize: 50, official: false })
  vi.mocked(getVideoTemplates).mockResolvedValue([{ id: 'template', status: 'active', minImages: 1, maxImages: 7 }] as Awaited<ReturnType<typeof getVideoTemplates>>)
  vi.mocked(getSiteStyleProfiles).mockResolvedValue([{ id: 'brand', name: 'Brand', status: 'active' }] as Awaited<ReturnType<typeof getSiteStyleProfiles>>)
  vi.mocked(uploadStoryboard).mockImplementation(async (file) => ({ id: file.name }) as Awaited<ReturnType<typeof uploadStoryboard>>)
  vi.mocked(createVideoTask).mockResolvedValue({ id: 'task', title: 'OOTD', phase: 'QUEUED' } as Awaited<ReturnType<typeof createVideoTask>>)
})
afterEach(() => vi.unstubAllGlobals())

describe('OOTD composer integration', () => {
  it('freezes unboxing selection while keeping automatic OOTD generation', async () => {
    const { render } = await mount()
    expect(nodes(render()).filter((node) => node.props['aria-label'] === '视频类型')).toHaveLength(0)
    expect(nodes(render()).filter((node) => node.type === 'input' && node.props.name === 'auto-video-type')).toHaveLength(0)
    expect(nodes(render()).filter((node) => node.props['aria-label'] === '本条展示重点')).toHaveLength(1)
    expect(route(render(), 'Sol 自动编排').props['aria-checked']).toBe(true)
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(createVideoTask).toHaveBeenCalledTimes(1))
    expect(vi.mocked(createVideoTask).mock.calls[0][2]).toMatchObject({
      generationMode: 'automatic', videoType: 'ootd', displayFocus: 'overall', actionIntensity: 'standard',
    })
  })

  it.each(['admin', 'member', null])('offers OOTD by default to actor %s without a rollout gate', async (actorId) => {
    const { render } = await mount(seed(), actorId)
    expect(route(render(), 'Sol 自动编排').props['aria-checked']).toBe(true)
    expect(submitButton(render()).props.disabled).toBe(false)
    expect(nodes(render()).filter((node) => node.type === 'select').map(content).join('')).not.toContain('明确切镜')
    expect(nodes(render()).map(content).join('')).not.toContain('从风格与动作库生成方案')
  })

  it('shows one route panel at a time while retaining custom input on route changes', async () => {
    const { render } = await mount()
    expect(nodes(render()).filter((node) => node.type === CustomPromptField)).toHaveLength(0)
    expect(nodes(render()).filter((node) => node.props['aria-label'] === '本条展示重点')).toHaveLength(1)
    expect(nodes(render()).map(content).join('')).not.toContain('让 Sol 看商品参考图来安排镜头')
    invoke(route(render(), '自定义提示词'), 'onClick')
    expect(nodes(render()).filter((node) => node.type === CustomPromptField)).toHaveLength(1)
    expect(nodes(render()).filter((node) => node.props['aria-label'] === '本条展示重点')).toHaveLength(0)
    invoke(find(render(), (node) => node.type === CustomPromptField), 'onChange', 'A saved custom shot instruction.')
    invoke(route(render(), 'Sol 自动编排'), 'onClick')
    expect(nodes(render()).filter((node) => node.type === CustomPromptField)).toHaveLength(0)
    expect(nodes(render()).filter((node) => node.props['aria-label'] === '本条展示重点')).toHaveLength(1)
    invoke(route(render(), '自定义提示词'), 'onClick')
    expect(find(render(), (node) => node.type === CustomPromptField).props.value).toBe('A saved custom shot instruction.')
  })

  it('offers every automatic duration from 5 through 15 and normalizes old 3-second settings', async () => {
    localStorage.setItem('fixed-duration-seconds', '3')
    const { render } = await mount()
    expect(durations(render())).toEqual(Array.from({ length: 11 }, (_, n) => n + 5))
    expect(find(render(), (node) => node.props.id === 'video-duration').props.value).toBe(5)
    expect(find(render(), (node) => node.type === 'input' && node.props.type === 'file').props.multiple).toBe(true)
  })

  it('allows multiple ordered storyboards in automatic OOTD', async () => {
    const { render } = await mount(seed(['first.png', 'second.png']))
    expect(nodes(render()).filter((node) => node.type === MaterialCard)).toHaveLength(2)
    expect(submitButton(render()).props.disabled).toBe(false)
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(createVideoTask).toHaveBeenCalledTimes(1))
    expect(vi.mocked(createVideoTask).mock.calls[0][1].map((item) => item.id)).toEqual(['first.png', 'second.png'])
  })

  it('retains custom prompts, multiple images and the original provider durations when switching routes', async () => {
    const { render } = await mount(seed(['first.png', 'second.png']))
    invoke(route(render(), '自定义提示词'), 'onClick')
    const prompt = 'Keep this custom prompt exactly as entered.'
    invoke(find(render(), (node) => node.type === CustomPromptField), 'onChange', prompt)
    expect(durations(render())).toEqual([5, 10])
    expect(find(render(), (node) => node.type === 'input' && node.props.type === 'file').props.multiple).toBe(true)
    expect(submitButton(render()).props.disabled).toBe(false)
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(createVideoTask).toHaveBeenCalledTimes(1))
    const [, uploads, options] = vi.mocked(createVideoTask).mock.calls[0]
    expect(uploads.map((item) => item.id)).toEqual(['first.png', 'second.png'])
    expect(options).toMatchObject({ generationMode: 'custom', customPrompt: prompt, creativeBrief: '用户补充要求', autoSubmitToCutflow: true })
  })

  it('preserves multi-image state when switching back to OOTD and permits submission', async () => {
    const { render } = await mount(seed(['first.png', 'second.png']))
    invoke(route(render(), '自定义提示词'), 'onClick')
    invoke(route(render(), 'Sol 自动编排'), 'onClick')
    expect(nodes(render()).filter((node) => node.type === MaterialCard)).toHaveLength(2)
    expect(submitButton(render()).props.disabled).toBe(false)
    expect(durations(render())).toEqual(Array.from({ length: 11 }, (_, n) => n + 5))
  })

  it.each([5, 10, 15])('passes %s seconds, the first frame and separate product references to automatic submission', async (duration) => {
    const input = seed()
    input.products = [image('product.png')]
    const { render } = await mount(input)
    invoke(find(render(), (node) => node.props.id === 'video-duration'), 'onChange', { target: { value: String(duration) } })
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(createVideoTask).toHaveBeenCalledTimes(1))
    const [, uploads, options, , identity] = vi.mocked(createVideoTask).mock.calls[0]
    expect(uploads.map((item) => item.id)).toEqual(['first.png'])
    expect(options).toMatchObject({ generationMode: 'automatic', durationSeconds: duration, creativeBrief: '用户补充要求', autoSubmitToCutflow: true, subjectUploads: [{ id: 'product.png' }] })
    expect(options?.customPrompt).toBeUndefined()
    expect(identity).toEqual({ clientTaskId: expect.any(String), idempotencyKey: expect.any(String) })
  })

  it('opens own product history without querying Kling subjects and submits selected files as product uploads', async () => {
    const { render } = await mount()
    expect(getKlingElements).not.toHaveBeenCalled()
    const panel = find(render(), node => node.type === SubjectInputPanel)
    expect(panel.props.allowHistorySelection).toBe(true)
    invoke(panel, 'onOpenProductHistory')
    const picker = find(render(), node => node.type === ProductHistoryPicker)
    expect(picker.props.siteProfileId).toBe('brand')
    invoke(picker, 'onSelect', [image('original-front.png'), image('original-back.png')])
    expect(find(render(), node => node.type === SubjectInputPanel).props.mode).toBe('upload')
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(createVideoTask).toHaveBeenCalledTimes(1))
    expect(vi.mocked(createVideoTask).mock.calls[0][2]).toMatchObject({
      subjectUploads: [{ id: 'original-front.png' }, { id: 'original-back.png' }],
      elementIds: [],
    })
  })

  it('accepts multiple dropped storyboards and keeps their upload order', async () => {
    const { render } = await mount(seed([]))
    const input = find(render(), (node) => node.type === 'input' && node.props.type === 'file')
    invoke(input, 'onChange', { target: { files: [image('first.png'), image('second.png')] }, currentTarget: { value: '' } })
    expect(nodes(render()).filter((node) => node.type === MaterialCard).map((node) => (node.props.material as { file: File }).file.name)).toEqual(['first.png', 'second.png'])
    expect(submitButton(render()).props.disabled).toBe(false)
  })

  it('accepts multiple storyboards per batch group with separate product images', async () => {
    const { render } = await mount()
    const input = nodes(render()).filter((node) => node.type === 'input' && node.props.type === 'file')[1]
    invoke(input, 'onChange', { target: { files: ['a_front.png', 'a_shot_1.png', 'b_front.png', 'b_shot_1.png', 'b_shot_2.png'].map(image) }, currentTarget: { value: '' } })
    expect(submitButton(render()).props.disabled).toBe(false)
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(createVideoTask).toHaveBeenCalledTimes(2))
    expect(vi.mocked(createVideoTask).mock.calls.map(([, uploads]) => uploads.map((item) => item.id))).toEqual([
      ['a_shot_1.png'], ['b_shot_1.png', 'b_shot_2.png'],
    ])
    expect(vi.mocked(createVideoTask).mock.calls.map(([, , options]) => options?.subjectUploads?.map((item) => item.id))).toEqual([
      ['a_front.png'], ['b_front.png'],
    ])
  })

  it('submits a valid single-frame batch once per group', async () => {
    const { render } = await mount()
    invoke(nodes(render()).filter((node) => node.type === 'input' && node.props.type === 'file')[1], 'onChange', {
      target: { files: ['a_front.png', 'a_shot_1.png', 'b_front.png', 'b_shot_1.png'].map(image) }, currentTarget: { value: '' },
    })
    expect(submitButton(render()).props.disabled).toBe(false)
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(createVideoTask).toHaveBeenCalledTimes(2))
    expect(vi.mocked(createVideoTask).mock.calls.map(([, uploads]) => uploads.map((item) => item.id))).toEqual([['a_shot_1.png'], ['b_shot_1.png']])
    expect(vi.mocked(createVideoTask).mock.calls[0][4]).not.toEqual(vi.mocked(createVideoTask).mock.calls[1][4])
  })

  it('retains the accepted group after a later upload failure and only continues unfinished groups', async () => {
    let fail = true
    vi.mocked(uploadStoryboard).mockImplementation(async (file) => {
      if (file.name === 'b_shot_1.png' && fail) throw new Error('injected upload failure')
      return { id: file.name } as Awaited<ReturnType<typeof uploadStoryboard>>
    })
    const { render, props } = await mount()
    importBatch(render())
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('明确失败，可继续'))
    expect(content(render())).toContain('已创建 1/3 组')
    expect(querySubmissionRecovery).not.toHaveBeenCalled()
    fail = false
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('已创建 3/3 组'))
    expect(vi.mocked(createVideoTask).mock.calls.map(([, uploads]) => uploads[0].id)).toEqual(['a_shot_1.png', 'b_shot_1.png', 'c_shot_1.png'])
    expect(vi.mocked(uploadStoryboard).mock.calls.filter(([file]) => file.name.startsWith('a_'))).toHaveLength(2)
    expect(props.onCreated).toHaveBeenCalledTimes(3)
    expect(submitButton(render()).props.disabled).toBe(true)
  })

  it('reuses the complete request and identity after a definite later submission rejection', async () => {
    vi.mocked(createVideoTask).mockResolvedValueOnce({ id: 'accepted-a', phase: 'ANALYZING' } as Awaited<ReturnType<typeof createVideoTask>>)
      .mockRejectedValueOnce(new ApiError(422, 'LOCAL_VALIDATION_FAILED', 'injected rejection'))
    const { render, props } = await mount()
    importBatch(render())
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('明确失败，可继续'))
    const rejectedRequest = vi.mocked(createVideoTask).mock.calls[1]
    expect(props.onCreated).toHaveBeenCalledWith(expect.objectContaining({ id: 'accepted-a' }), true)
    expect(find(render(), (node) => node.type === GenerationBrief).props.disabled).toBe(true)
    expect(button(render(), '退出批量').props.disabled).toBe(true)
    // A second click before React renders must not start a duplicate loop.
    const continueButton = submitButton(render())
    invoke(continueButton, 'onClick')
    invoke(continueButton, 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('已创建 3/3 组'))
    expect(createVideoTask).toHaveBeenCalledTimes(4)
    expect(vi.mocked(createVideoTask).mock.calls[2]).toEqual(rejectedRequest)
    expect(vi.mocked(createVideoTask).mock.calls.map(([, uploads]) => uploads[0].id)).toEqual(['a_shot_1.png', 'b_shot_1.png', 'b_shot_1.png', 'c_shot_1.png'])
    expect(vi.mocked(uploadStoryboard).mock.calls.filter(([file]) => file.name.startsWith('b_'))).toHaveLength(2)
  })

  it.each(['unknown', 'in_progress', 'not_found'] as const)('keeps a later %s result blocked until confirmed and preserves earlier success', async (state) => {
    vi.mocked(createVideoTask).mockResolvedValueOnce({ id: 'accepted-a' } as Awaited<ReturnType<typeof createVideoTask>>)
      .mockRejectedValueOnce(new ApiError(503, 'WRITE_UNCERTAIN', 'injected lost receipt'))
    vi.mocked(querySubmissionRecovery).mockResolvedValueOnce(recovery(state)).mockResolvedValueOnce(recovery('submitted', 'accepted-b'))
    // Confirmation is authoritative even if opening the task details fails.
    vi.mocked(getTask).mockRejectedValue(new Error('injected detail read failure'))
    const { render } = await mount()
    importBatch(render())
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('结果未知，先核对'))
    const originalIdentity = vi.mocked(createVideoTask).mock.calls[1][4]!
    invoke(button(render(), '核对任务状态'), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain(`核对结果：${state}`))
    expect(submitButton(render()).props.disabled).toBe(true)
    invoke(submitButton(render()), 'onClick')
    expect(createVideoTask).toHaveBeenCalledTimes(2)
    expect(content(render())).toContain('已创建 1/3 组')
    expect(querySubmissionRecovery).toHaveBeenLastCalledWith({ kind: 'video', clientId: originalIdentity.clientTaskId, idempotencyKey: originalIdentity.idempotencyKey })
    invoke(button(render(), '核对任务状态'), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('已创建 2/3 组'))
    expect(content(render())).toContain('任务 accepted-b')
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('已创建 3/3 组'))
    expect(vi.mocked(createVideoTask).mock.calls.map(([, uploads]) => uploads[0].id)).toEqual(['a_shot_1.png', 'b_shot_1.png', 'c_shot_1.png'])
  })

  it.each([
    new ApiError(409, 'IDEMPOTENCY_REQUEST_IN_PROGRESS', 'pending'),
    new ApiError(409, 'IDEMPOTENCY_KEY_REUSED', 'conflict'),
    new ApiError(422, 'ACCEPTANCE_UNCERTAIN', 'accepted', null, null, true),
    new ApiError(422, 'CHARGE_UNCERTAIN', 'charged', null, null, false, true),
    new ApiError(422, 'RECONCILIATION_REQUIRED', 'reconcile', null, null, false, false, true),
  ])('requires reconciliation for %s instead of treating every 4xx as safe to repeat', async (failure) => {
    vi.mocked(createVideoTask).mockRejectedValue(failure)
    const { render } = await mount()
    importBatch(render())
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('结果未知，先核对'))
    expect(submitButton(render()).props.disabled).toBe(true)
    expect(createVideoTask).toHaveBeenCalledTimes(1)
  })

  it('shows original batch materials read-only and allows editing the selected group after exit', async () => {
    const { render } = await mount()
    importBatch(render(), ['a', 'b'])
    expect(find(render(), (node) => node.type === 'input' && node.props.type === 'file').props.disabled).toBe(true)
    expect(nodes(render()).filter((node) => node.type === MaterialCard)).toHaveLength(0)
    const subjectPanel = find(render(), (node) => node.type === SubjectInputPanel)
    expect(subjectPanel.props.disabled).toBe(true)
    invoke(subjectPanel, 'onUploadedFilesChange', { frontalImage: image('ignored.png'), backImage: null, detailImages: [] })
    invoke(find(render(), (node) => node.type === 'input' && node.props.type === 'file'), 'onChange', { target: { files: [image('ignored-shot.png')] }, currentTarget: { value: '' } })
    expect((find(render(), (node) => node.type === SubjectInputPanel).props.uploadedFiles as { frontalImage: File }).frontalImage.name).toBe('a_front.png')
    invoke(button(render(), '预览第 2 组'), 'onClick')
    expect(content(render())).toContain('批量素材只读预览')
    expect((find(render(), (node) => node.type === SubjectInputPanel).props.uploadedFiles as { frontalImage: File }).frontalImage.name).toBe('b_front.png')
    invoke(button(render(), '退出批量'), 'onClick')
    const storyboard = find(render(), (node) => node.type === MaterialCard)
    invoke(storyboard, 'onRemove')
    invoke(find(render(), (node) => node.type === 'input' && node.props.type === 'file'), 'onChange', { target: { files: [image('edited-shot.png')] }, currentTarget: { value: '' } })
    invoke(find(render(), (node) => node.type === SubjectInputPanel), 'onUploadedFilesChange', { frontalImage: image('edited-product.png'), backImage: null, detailImages: [] })
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(createVideoTask).toHaveBeenCalledTimes(1))
    expect(vi.mocked(createVideoTask).mock.calls[0][1].map((item) => item.id)).toEqual(['edited-shot.png'])
    expect(vi.mocked(createVideoTask).mock.calls[0][2]?.subjectUploads?.map((item) => item.id)).toEqual(['edited-product.png'])
  })

  it('preview selection does not change the actual per-group batch submissions', async () => {
    const { render } = await mount()
    importBatch(render(), ['a', 'b'])
    invoke(button(render(), '预览第 2 组'), 'onClick')
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('已创建 2/2 组'))
    expect(vi.mocked(createVideoTask).mock.calls.map(([, uploads, options]) => [uploads[0].id, options?.subjectUploads?.[0].id])).toEqual([['a_shot_1.png', 'a_front.png'], ['b_shot_1.png', 'b_front.png']])
  })

  it('does not undo accepted groups when receipt cleanup and the parent callback fail', async () => {
    vi.spyOn(localStorage, 'removeItem').mockImplementation(() => { throw new Error('injected storage failure') })
    const { render, props } = await mount()
    props.onCreated.mockImplementation(() => { throw new Error('injected callback failure') })
    importBatch(render(), ['a', 'b'])
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('已创建 2/2 组'))
    expect(createVideoTask).toHaveBeenCalledTimes(2)
    expect(submitButton(render()).props.disabled).toBe(true)
    expect(querySubmissionRecovery).not.toHaveBeenCalled()
  })

  it('retains unknown status after a reconciliation read failure and finishes without recreating the last group', async () => {
    vi.mocked(createVideoTask).mockResolvedValueOnce({ id: 'accepted-a' } as Awaited<ReturnType<typeof createVideoTask>>)
      .mockRejectedValueOnce(new ApiError(503, 'WRITE_UNCERTAIN', 'injected lost receipt'))
    vi.mocked(querySubmissionRecovery).mockRejectedValueOnce(new Error('injected query failure')).mockResolvedValueOnce(recovery('submitted', 'accepted-b'))
    vi.mocked(getTask).mockResolvedValue({ id: 'accepted-b' } as Awaited<ReturnType<typeof getTask>>)
    const { render } = await mount()
    importBatch(render(), ['a', 'b'])
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('结果未知，先核对'))
    invoke(button(render(), '核对任务状态'), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('核对任务状态失败，请稍后重试'))
    expect(submitButton(render()).props.disabled).toBe(true)
    invoke(button(render(), '核对任务状态'), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('已创建 2/2 组'))
    expect(submitButton(render()).props.disabled).toBe(true)
    expect(createVideoTask).toHaveBeenCalledTimes(2)
    expect(button(render(), '结束本批').props.disabled).toBe(false)
  })

  it('safely recovers a not_found submission using the exact original request identity', async () => {
    vi.mocked(createVideoTask)
      .mockRejectedValueOnce(new ApiError(503, 'WRITE_UNCERTAIN', 'injected response loss'))
      .mockResolvedValueOnce({ id: 'original-task', title: 'OOTD', phase: 'ANALYZING' } as Awaited<ReturnType<typeof createVideoTask>>)
    vi.mocked(querySubmissionRecovery).mockResolvedValue(recovery('not_found'))
    const { render, props } = await mount()
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('提交结果未知'))
    invoke(button(render(), '核对任务状态'), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('核对结果：not_found'))
    expect(submitButton(render()).props.disabled).toBe(true)
    const originalRequest = vi.mocked(createVideoTask).mock.calls[0]
    invoke(button(render(), '使用原提交编号安全重试'), 'onClick')
    await vi.waitFor(() => expect(createVideoTask).toHaveBeenCalledTimes(2))
    expect(vi.mocked(createVideoTask).mock.calls[1]).toEqual(originalRequest)
    expect(querySubmissionRecovery).toHaveBeenCalledTimes(2)
    await vi.waitFor(() => expect(props.onCreated).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'original-task' }), true,
    ))
    expect(content(render())).not.toContain('提交结果未知')
  })

  it.each(['unknown', 'in_progress'] as const)('never exposes an original replay for %s', async (state) => {
    vi.mocked(createVideoTask).mockRejectedValue(new ApiError(503, 'WRITE_UNCERTAIN', 'injected response loss'))
    vi.mocked(querySubmissionRecovery).mockResolvedValue(recovery(state))
    const { render } = await mount()
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('提交结果未知'))
    invoke(button(render(), '核对任务状态'), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain(`核对结果：${state}`))
    expect(nodes(render()).some((node) => node.type === 'button'
      && content(node).includes('使用原提交编号安全重试'))).toBe(false)
    expect(createVideoTask).toHaveBeenCalledTimes(1)
  })

  it('blocks another submit while the original submission result is unknown', async () => {
    vi.mocked(createVideoTask).mockRejectedValue(new ApiError(503, 'WRITE_UNCERTAIN', 'unavailable'))
    const { render } = await mount()
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(nodes(render()).map(content).join('')).toContain('提交结果未知'))
    expect(submitButton(render()).props.disabled).toBe(true)
    invoke(submitButton(render()), 'onClick')
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(createVideoTask).toHaveBeenCalledTimes(1)
  })

  it('distinguishes local preview, server validation and the exact failed image', async () => {
    const { render } = await mount(seed(['valid_shot.png', 'bad_shot.png']))
    const cards = () => nodes(render()).filter(node => node.type === MaterialCard)
    expect(cards().map(card => card.props.uploadState)).toEqual([{ phase: 'preview' }, { phase: 'preview' }])
    let rejectSecond!: (cause: unknown) => void
    vi.mocked(uploadStoryboard).mockImplementation(async file => {
      if (file.name === 'bad_shot.png') return new Promise((_, reject) => { rejectSecond = reject })
      return { id: file.name } as Awaited<ReturnType<typeof uploadStoryboard>>
    })
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(cards()[0].props.uploadState).toEqual({ phase: 'validated' }))
    expect(cards()[1].props.uploadState).toEqual({ phase: 'uploading' })
    rejectSecond(new ApiError(422, 'IMAGE_DIMENSIONS_INVALID', '图片尺寸为 100 × 640 像素，宽、高均须在 128～8192 像素之间。', null, false, false, false, false, 'support-42'))
    await vi.waitFor(() => expect(content(render())).toContain('分镜图 2'))
    expect(cards()[1].props.uploadState).toMatchObject({ phase: 'failed' })
    expect(content(render())).toContain('bad_shot.png')
    expect(content(render())).toContain('100 × 640')
    expect(content(render())).toContain('排查编号：support-42')
    expect(createVideoTask).not.toHaveBeenCalled()
  })

  it('does not label an upload processing error as a bad image or a paid submission', async () => {
    const { render } = await mount()
    vi.mocked(uploadStoryboard).mockRejectedValue(new ApiError(500, 'UPLOAD_PROCESSING_FAILED', 'internal ValueError'))
    await invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(content(render())).toContain('尚不能认定图片不合格'))
    expect(content(render())).not.toContain('提交结果未知')
    expect(createVideoTask).not.toHaveBeenCalled()
    expect(submitButton(render()).props.disabled).toBe(false)
  })

  it('keeps automatic editing inside advanced output settings and restores its default', async () => {
    const { render } = await mount()
    const advanced = () => find(render(), (node) => node.type === GenerationAdvancedSettings)
    const routing = find(render(), (node) => node.props.className === 'composer-routing-options')
    expect(nodes(routing).filter((node) => node.type === 'input' && node.props.type === 'checkbox')).toHaveLength(0)
    expect(nodes(render()).filter((node) => node.props.className === 'composer-output-autocut')).toHaveLength(1)
    expect(nodes(advanced()).filter((node) => node.props.className === 'composer-output-autocut')).toHaveLength(1)
    expect(autoCutflowCheckbox(render()).props.checked).toBe(true)
    expect(advanced().props.customized).toBe(false)
    invoke(autoCutflowCheckbox(render()), 'onChange', { target: { checked: false } })
    expect(advanced().props.customized).toBe(true)
    expect(content(find(render(), (node) => node.props.className === 'composer-submit-summary'))).toContain('仅生成原片')
    invoke(advanced(), 'onReset')
    expect(autoCutflowCheckbox(render()).props.checked).toBe(true)
    expect(advanced().props.customized).toBe(false)
  })

  it('keeps the existing automatic Cutflow opt-out in the submission', async () => {
    const { render } = await mount()
    invoke(autoCutflowCheckbox(render()), 'onChange', { target: { checked: false } })
    invoke(submitButton(render()), 'onClick')
    await vi.waitFor(() => expect(createVideoTask).toHaveBeenCalledTimes(1))
    expect(vi.mocked(createVideoTask).mock.calls[0][2]?.autoSubmitToCutflow).toBe(false)
  })

  it('disables generation safely if the configured provider has no OOTD duration', async () => {
    const { render } = await mount(seed(), 'member', { ...system, videoGeneration: { ...system.videoGeneration!, videoDurations: ['3', '4'] } })
    expect(submitButton(render()).props.disabled).toBe(true)
    expect(durations(render())).toEqual([])
    expect(nodes(render()).map(content).join('')).toContain('当前模型没有可用的自动编排 5–15 秒时长')
  })
})
