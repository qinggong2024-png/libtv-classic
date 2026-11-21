import { useContext } from 'react'
import { GenerationBudgetContext, formatBudgetUsd } from '../../generationBudget'

export function DailyBudget({ onOpenUsage }: { onOpenUsage: () => void }) {
  const { budget, failed } = useContext(GenerationBudgetContext)
  const exhausted = budget?.dailyBudgetUsd != null && budget.budgetUsedUsd >= budget.dailyBudgetUsd
  return <button type="button" className={`daily-budget${exhausted ? ' is-exhausted' : ''}`} onClick={onOpenUsage} aria-label="查看我的每日预算与使用记录">
    <span className="daily-budget__heading">每日预算 <small>USD</small></span>
    {budget ? <>
      <span className="daily-budget__row"><span>预算</span><strong>{budget.dailyBudgetUsd === null ? '不限额' : formatBudgetUsd(budget.dailyBudgetUsd)}</strong></span>
      <span className="daily-budget__row"><span>已用{budget.unpricedCount ? '（已知）' : ''}</span><strong>{formatBudgetUsd(budget.budgetUsedUsd)}</strong></span>
    </> : null}
    <small className="daily-budget__note">{failed ? '额度读取失败，点击查看记录' : !budget ? '正在读取额度…' : budget.unpricedCount ? `${budget.unpricedCount} 笔未定价 · 额度待核实` : exhausted ? '今日预算已用完' : '预估含预留 · 北京时间每日重置'}</small>
  </button>
}
