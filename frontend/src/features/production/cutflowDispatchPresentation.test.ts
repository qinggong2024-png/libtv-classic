import { describe, expect, it } from 'vitest'
import { cutflowDispatchPresentation } from './cutflowDispatchPresentation'

describe('simple CUT status on completed video tasks', () => {
  it('distinguishes enabled auto-send from an actual queued job', () => {
    const result = cutflowDispatchPresentation({ autoSubmitToCutflow: true }, false, false, false)
    expect(result.headline).toBe('等待自动送剪')
    expect(result.tone).toBe('neutral')
    expect(result.waiting).toBe(true)
    expect(result.modeLabel).toBe('自动送剪')
    expect(result.action).toBeNull()
    expect(result.detail).not.toContain('已送剪成功')
  })

  it('gives manual sending a clear next action', () => {
    const result = cutflowDispatchPresentation({ autoSubmitToCutflow: false }, false, false, false)
    expect(result.headline).toBe('等待你送剪')
    expect(result.action).toBe('select')
    expect(result.actionLabel).toBe('选择原片送剪')
  })

  it('does not fabricate a historical preference', () => {
    const result = cutflowDispatchPresentation({}, false, false, false)
    expect(result.modeLabel).toBe('送剪方式未知')
    expect(result.headline).toBe('还没有送剪')
  })

  it('avoids false unsubmitted states before CUT history loads', () => {
    const result = cutflowDispatchPresentation({ autoSubmitToCutflow: true }, false, false, false, false, false)
    expect(result.headline).toBe('正在核对剪辑状态')
    expect(result.action).toBeNull()
  })

  it('shows a recoverable read failure rather than assuming no CUT exists', () => {
    const result = cutflowDispatchPresentation({ autoSubmitToCutflow: true }, false, false, false, false, false, true)
    expect(result.headline).toBe('剪辑状态同步异常')
    expect(result.actionLabel).toBe('重新查询')
  })

  it('an active editing job takes precedence over the saved mode', () => {
    const result = cutflowDispatchPresentation({ autoSubmitToCutflow: false }, true, true, false, false)
    expect(result.headline).toBe('已建立剪辑任务，正在处理')
    expect(result.action).toBeNull()
  })

  it('latest failure takes priority over older delivered outputs', () => {
    const result = cutflowDispatchPresentation({ autoSubmitToCutflow: true }, true, false, true, true)
    expect(result.headline).toBe('剪辑任务失败')
    expect(result.action).toBe('history')
  })

  it('delivered cuts have an actionable result shortcut', () => {
    const result = cutflowDispatchPresentation({ autoSubmitToCutflow: false }, true, false, true)
    expect(result.headline).toBe('成片已完成')
    expect(result.actionLabel).toBe('查看成片')
  })

  it('uncompleted old records do not claim an active queue', () => {
    const result = cutflowDispatchPresentation({ autoSubmitToCutflow: true }, true, false, false)
    expect(result.headline).toBe('剪辑任务待核实')
    expect(result.action).toBe('history')
  })
})
