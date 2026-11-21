import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { MultiCutflowTask } from '../../api'
import { TaskFinalDelivery } from './TaskFinalDelivery'
import { TaskDetailDisclosure } from './TaskDetailDisclosure'

describe('A方案成片来源与资料分组', () => {
  it('按真实输入顺序展示来源且默认可见，尺寸与时长来自实际成片', () => {
    const task = { updatedAt: '2026-11-13T03:00:00Z', sources: [
      { clipId: 'second', order: 2, title: '第二条', durationSeconds: 8 },
      { clipId: 'first', order: 1, title: '第一条', durationSeconds: 5 },
    ], outputs: [{ id: 'output', mediaUrl: '/fixture/result', durationSeconds: 9.5, width: 1280, height: 720 }] } as MultiCutflowTask
    const html = renderToStaticMarkup(<TaskFinalDelivery task={task} onPreview={() => {}} />)
    expect(html.indexOf('第一条')).toBeLessThan(html.indexOf('第二条'))
    expect(html).toContain('9.5 秒')
    expect(html).toContain('1280 × 720')
    expect(html).toContain('1280 / 720')
    expect(html).toContain('download=""')
    expect(html).not.toContain('<details')
    expect(task.sources[0].clipId).toBe('second')
  })

  it('折叠入口先出现箭头与标题，并保留原生键盘语义及明确展开文案', () => {
    const html = renderToStaticMarkup(<TaskDetailDisclosure title="生成参数" hint="参考素材与生成设置"><p>原始配置</p></TaskDetailDisclosure>)
    expect(html).toContain('<details class="task-a-disclosure">')
    expect(html.indexOf('<svg')).toBeLessThan(html.indexOf('<strong>生成参数</strong>'))
    expect(html).toContain('展开')
    expect(html).toContain('收起')
  })
})
