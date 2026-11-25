import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Task, Variant } from '../../types'
import { RecordsView } from './RecordsView'
import { ProductionWorkspace } from '../production/ProductionWorkspace'

const noop = () => {}
const refresh = async () => {}
const variant = {
  id: 'variant-01', kind: 'STABLE', generationStatus: 'SUCCEEDED',
  validationStatus: 'PASSED', downloadArtifactId: 'artifact-01', promptText: '',
} as unknown as Variant
const task = {
  id: 'task-01', title: 'PW T恤', createdAt: '2026-10-29T10:00:00Z',
  updatedAt: '2026-10-29T10:10:00Z', phase: 'DELIVERABLE',
  assets: [], assetCount: 2, variants: [variant],
  workflowKind: 'FIXED_TEMPLATE', generationProvider: 'KLING',
  settings: { durationSeconds: 10, generationMode: 'custom' },
  actionCapabilities: { canRegenerate: false },
} as unknown as Task

const renderDetail = (item: Task, multiCutReady = false) => renderToStaticMarkup(
  <ProductionWorkspace task={item} onClose={noop} onChanged={refresh} onRemix={noop} onReuse={noop} onPlay={noop} multiCutReady={multiCutReady} />,
)

it('keeps feedback with ready originals in task detail, not on the creation page', () => {
  const html = renderToStaticMarkup(<ProductionWorkspace task={task} onClose={noop} onChanged={refresh} onRemix={noop} onReuse={noop} onPlay={noop} onFeedbackChanged={noop} />)
  expect(html).toContain('result-feedback')
  expect(html.match(/class="result-feedback"/g)).toHaveLength(1)
  expect(renderDetail(task)).not.toContain('result-feedback')
})

describe('经典版自适应任务详情', () => {
  it('任务中心的工具按钮用于刷新状态而非复制链接', () => {
    const html = renderToStaticMarkup(<RecordsView tasks={[task]} hasMore={false} loadingMore={false}
      lastSyncedAt={null} onLoadMore={noop} onRefresh={refresh} onOpen={noop} onPlay={noop}
      onRemix={noop} onFeedbackChanged={noop} onCreateTask={noop} />)
    expect(html).toContain('aria-label="刷新任务状态"')
    expect(html).not.toContain('复制当前任务筛选链接')
    expect(html).not.toContain('aria-label="正在刷新任务状态"')
  })

  it('历史未加载完时不把本地搜索当作全量搜索，并保留继续查找入口', () => {
    const html = renderToStaticMarkup(<RecordsView tasks={[task]} hasMore loadingMore={false} lastSyncedAt={null} onLoadMore={noop} onOpen={noop} onPlay={noop} onRemix={noop} onFeedbackChanged={noop} onCreateTask={noop} initialQuery="不存在的任务" />)
    expect(html).toContain('仅搜索已加载记录')
    expect(html).toContain('已加载记录中没有匹配任务')
    expect(html).toContain('继续加载历史并查找')
    expect(html).toContain('value="不存在的任务"')
  })

  it('可以通过编号检索，已加载完成后不再显示继续加载入口', () => {
    const html = renderToStaticMarkup(<RecordsView tasks={[task]} hasMore={false} loadingMore={false} lastSyncedAt={null} onLoadMore={noop} onOpen={noop} onPlay={noop} onRemix={noop} onFeedbackChanged={noop} onCreateTask={noop} initialQuery="TASK-01" />)
    expect(html).toContain('当前筛选匹配 1 条任务')
    expect(html).toContain('PW T恤')
    expect(html).not.toContain('继续加载历史并查找')
  })

  it('一条任务只有一个主卡片，不重复铺出最近原片', () => {
    const html = renderToStaticMarkup(<RecordsView tasks={[task]} hasMore={false} loadingMore={false} lastSyncedAt={null} onLoadMore={noop} onOpen={noop} onPlay={noop} onRemix={noop} onFeedbackChanged={noop} onCreateTask={noop} multiCutReady={false} />)
    expect(html).toContain('任务中心')
    expect(html.match(/class="task-center-card"/g)).toHaveLength(1)
    expect(html).toContain('1/1 原片')
    expect(html).not.toContain('最近生成原片')
  })

  it('原片完成且无剪辑回执时，只显示一个剪辑状态区而非重复进度', () => {
    const html = renderDetail(task)
    expect(html).not.toContain('aria-label="当前剪辑状态"')
    expect(html).toContain('原片已就绪 · 待送剪')
    expect(html).toContain('选择原片送剪')
    expect(html.match(/原片已就绪 · 待送剪/g)).toHaveLength(1)
    expect(html).toContain('aria-label="剪辑与成片"')
    expect(html).toContain('role="tablist"')
    expect(html).toContain('参考素材与生成设置')
    expect(html).not.toContain('task-flow-journey')
    expect(html).not.toContain('task-cut-next-step')
  })

  it('多源剪辑状态仍在查询时，不虚构送剪结果', () => {
    const html = renderDetail(task, true)
    expect(html).toContain('正在核对剪辑状态')
    expect(html).not.toContain('自动送剪已提交')
  })

  it('生成中展示阶段、真实原片数量和折叠耗时，不显示大型阶段条', () => {
    const generating = { ...task, phase: 'GENERATING',
      variants: [{ ...variant, generationStatus: 'RUNNING', downloadArtifactId: null }],
    } as unknown as Task
    const html = renderDetail(generating, true)
    expect(html).toContain('aria-label="生产流程"')
    expect(html).toContain('正在生成视频')
    expect(html).toContain('生成原片')
    expect(html).toContain('<small>0 / 1</small>')
    expect(html).toContain('<strong>制作记录</strong>')
    expect(html).not.toContain('workspace-stages')
    expect(html).not.toContain('task-flow-journey')
  })

  it('需要人工处理的失败任务将原因和唯一重新制作入口置前', () => {
    const failed = { ...task, phase: 'ACTION_REQUIRED',
      variants: [{ ...variant, generationStatus: 'FAILED', downloadArtifactId: null }],
      actionCapabilities: { canRegenerate: true, canCancel: false },
    } as unknown as Task
    const html = renderDetail(failed, true)
    expect(html).toContain('生成暂时无法继续')
    expect(html).toContain('使用原素材重新制作')
    expect(html.match(/使用原素材重新制作/g)).toHaveLength(1)
    expect(html).toContain('<strong>制作记录</strong>')
    expect(html).not.toContain('workspace-stages')
    expect(html).not.toContain('task-flow-journey')
  })

  it('已阻止重复风控的任务不再提供直接原素材重做按钮', () => {
    const blocked = { ...task, phase: 'ACTION_REQUIRED',
      phaseReasonCode: 'KLING_PRIOR_RISK_CONTROL_REJECTED',
      variants: [{ ...variant, generationStatus: 'FAILED', downloadArtifactId: null }],
      actionCapabilities: { canRegenerate: true, canCancel: false },
    } as unknown as Task
    const html = renderDetail(blocked, true)
    expect(html).toContain('已阻止重复风控提交')
    expect(html).toContain('至少两次被可灵审核拒绝')
    expect(html).not.toContain('使用原素材重新制作')
    expect(html).not.toContain('使用相同素材再制作')
  })

  it('关闭自动送剪的任务只提示主动选择原片，不误认为已提交', () => {
    const manual = { ...task, settings: { ...task.settings, autoSubmitToCutflow: false } } as Task
    const html = renderDetail(manual)
    expect(html).toContain('原片已就绪 · 待送剪')
    expect(html).toContain('预览原片后选择送剪')
    expect(html).not.toContain('task-flow-journey')
  })

  it('待送剪的桌面任务将原片选择控件贴近视频，并把技术资料放在原片后面', () => {
    const manual = { ...task, settings: { ...task.settings, autoSubmitToCutflow: false } } as Task
    const html = renderDetail(manual, true)
    const mediaIndex = html.indexOf('variant-status__media')
    expect(mediaIndex).toBeGreaterThan(-1)
    expect(html.indexOf('task-direct-cut-button', mediaIndex)).toBeGreaterThan(mediaIndex)
    expect(html.indexOf('task-direct-cut-button', mediaIndex)).toBeLessThan(html.indexOf('variant-status-actions', mediaIndex))
    expect(html).toContain('直接送剪')
    expect(html.indexOf('task-original-clips')).toBeLessThan(html.indexOf('参考素材与生成设置'))
  })

  it('多条原片保留选片工作台，历史与技术资料只出现一个顶层入口', () => {
    const many = { ...task, variants: [variant, { ...variant, id: 'variant-02', kind: 'DYNAMIC' }],
      settings: { ...task.settings, autoSubmitToCutflow: false } } as unknown as Task
    const html = renderDetail(many, true)
    expect(html.match(/<label class="cutflow-source-toggle/g)).toHaveLength(2)
    expect(html).not.toContain('task-direct-cut-button')
    expect(html).toContain('aria-label="剪辑与成片"')
    expect(html).toContain('role="tablist"')
    expect(html).toContain('<strong>生成参数</strong>')
    expect(html).toContain('<strong>制作记录</strong>')
    expect(html).not.toContain('task-edit-records')
    expect(html).not.toContain('task-final-legacy-history')
    expect(html.indexOf('task-original-clips')).toBeLessThan(html.indexOf('task-cutflow-records'))
    expect(html.indexOf('<strong>生成参数</strong>')).toBeLessThan(html.indexOf('task-cutflow-records'))
  })

  it('三条生成原片保留网格卡片与唯一批量送剪入口，且不破坏单条路径', () => {
    const multiple = { ...task, settings: { ...task.settings, autoSubmitToCutflow: false },
      variants: [variant, { ...variant, id: 'variant-02' }, { ...variant, id: 'variant-03' }],
    } as unknown as Task
    const html = renderDetail(multiple, true)
    expect(html).toContain('task-a-originals')
    expect(html).not.toContain('<details class="task-detail-section task-original-collapsible')
    expect(html).toContain('variant-status-list--gallery')
    expect(html.match(/class="production-video-result production-video-result--tile/g)).toHaveLength(3)
    expect(html.match(/class="variant-status variant-status--tile"/g)).toHaveLength(3)
    expect(html.match(/<label class="cutflow-source-toggle/g)).toHaveLength(3)
    expect(html).toContain('原片 01')
    expect(html).toContain('原片 02')
    expect(html).toContain('原片 03')
    expect(html).toContain('task-original-gallery__sidebar')
    expect(html).toContain('批量送剪')
    expect(html.match(/class="primary-button task-original-gallery__submit"/g)).toHaveLength(1)
    expect(html).toContain('选择原片后送剪')
    expect(html).not.toContain('task-direct-cut-button')
    const singleHtml = renderDetail(task, true)
    expect(singleHtml).not.toContain('task-original-gallery')
    expect(singleHtml).toContain('task-direct-cut-button')
  })

  it('制作耗时单层收起，参数与制作记录独立收起，并保留素材和历史信息', () => {
    const example = { ...task, variants: [{ ...variant, promptText: 'Keep garment print exact.' }] } as Task
    const html = renderDetail(example, true)
    const disclosureStart = html.indexOf('<details class="task-a-disclosure"')
    const progressStart = html.indexOf('class="task-timing task-timing--condensed task-timing-breakdown"')
    expect(progressStart).toBeGreaterThanOrEqual(0)
    expect(progressStart).toBeLessThan(disclosureStart)
    expect(html).toContain('制作耗时')
    expect(html.slice(progressStart, disclosureStart).match(/<details\b/g) ?? []).toHaveLength(0)
    expect(html).not.toContain('task-timing-breakdown" open')
    const section = html.slice(disclosureStart)
    expect(section).toContain('<strong>生成参数</strong>')
    expect(section).toContain('<strong>制作记录</strong>')
    expect(section.match(/<details class="task-a-disclosure"/g)).toHaveLength(2)
    expect(section).not.toContain('<details class="task-a-disclosure" open=')
    expect(section).toContain('参考素材与生成设置')
    expect(section).toContain('本次提示词与历史方案')
    expect(section).toContain('任务编号与技术信息')
    expect(section).toContain('Keep garment print exact.')
    expect(section).toContain('任务编号')
    expect(section).not.toContain('task-technical-group')
    expect(section).not.toContain('task-detail-source')
    expect(section.indexOf('参考素材与生成设置')).toBeLessThan(section.indexOf('本次提示词与历史方案'))
    expect(section.indexOf('本次提示词与历史方案')).toBeLessThan(section.indexOf('<strong>制作记录</strong>'))
  })

  it('提示词在视频生成中和失败后均可查看，无需等待生成完成', () => {
    const generating = { ...task, phase: 'GENERATING',
      variants: [{ ...variant, generationStatus: 'RUNNING', downloadArtifactId: null, promptText: 'EARLY_SAVED_PROMPT' }],
    } as unknown as Task
    const generatingHtml = renderDetail(generating, true)
    expect(generatingHtml).toContain('EARLY_SAVED_PROMPT')
    expect(generatingHtml).toContain('1 条提示词已保存')
    expect(generatingHtml).toContain('id="task-generation-parameters"')
    expect(generatingHtml).not.toContain('task-a-disclosure" id="task-generation-parameters" open=')

    const failed = { ...task, phase: 'ACTION_REQUIRED',
      variants: [{ ...variant, generationStatus: 'FAILED', downloadArtifactId: null, promptText: 'REJECTED_TASK_PROMPT' }],
      actionCapabilities: { canRegenerate: true, canCancel: false },
    } as unknown as Task
    const failedHtml = renderDetail(failed, true)
    expect(failedHtml).toContain('REJECTED_TASK_PROMPT')
    expect(failedHtml).toContain('查看已保存的')
    expect(failedHtml).toContain('1 条提示词')
  })

  it('自动送剪设置不代表剪辑端已经有接单回执', () => {
    const automatic = { ...task, settings: { ...task.settings, autoSubmitToCutflow: true } } as Task
    const html = renderDetail(automatic)
    expect(html).toContain('等待自动送剪')
    expect(html).not.toContain('task-adaptive-cut')
    expect(html).toContain('allwe-detail-overview__status')
    expect(html).not.toContain('CUT 已接单，排队中')
    expect(html).toContain('无需操作')
    expect(html).not.toContain('已送剪成功')
    expect(html).not.toContain('task-flow-journey')
  })

  it('旧版已完成任务仍有历史查看入口，但不再铺三段式进度', () => {
    const oldTask = { ...task, id: 'legacy-completed-20261026',
      createdAt: '2026-10-26T08:00:00Z', updatedAt: '2026-10-26T08:19:00Z',
      workflowKind: 'LEGACY', settings: { durationSeconds: 10 },
      variants: [{ ...variant, id: 'legacy-variant' }],
      actionCapabilities: { canRegenerate: false, canCancel: false, canRetryAnalysis: false },
    } as unknown as Task
    const html = renderDetail(oldTask, true)
    expect(html).toContain('task-flow-completed')
    expect(html).toContain('剪辑记录与历史版本')
    expect(html).toContain('aria-label="剪辑与成片"')
    expect(html).not.toContain('task-flow-journey')
  })
})
