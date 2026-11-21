import { useEffect, useState } from 'react'
import { getCutflowDiagnostics, type CutflowDiagnostics as Diagnostics } from '../../api'
import { cutflowStageLabel, cutflowTime } from '../../domain/cutflowPresentation'

export function CutflowDiagnostics({ handoffId }: { handoffId: string }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<Diagnostics | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      try {
        const result = await getCutflowDiagnostics(handoffId, controller.signal)
        if (!controller.signal.aborted) { setData(result); setError('') }
      } catch {
        if (!controller.signal.aborted) setError('链路状态暂不可读取，稍后自动重查。')
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => void refresh(), 10_000)
      }
    }
    void refresh()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [handoffId, open])
  return <details className="cutflow-diagnostics" data-card-interactive onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>链路状态</summary>
    {open ? <div>
      {error ? <p role="status">{error}{data ? '以下为上次读取结果。' : ''}</p> : null}
      {!data && !error ? <p role="status">正在读取链路状态…</p> : null}
      {data ? <>
        <dl>
          <dt>发送进程</dt><dd>{({ online: '运行中', stale: '心跳已过期，请检查发送进程', unknown: '尚无心跳记录' })[data.sender.state]}</dd>
          <dt>剪辑阶段</dt><dd>{cutflowStageLabel(data.remoteStage)}</dd>
          <dt>最近收到状态</dt><dd>{cutflowTime(data.lastContactAt)}</dd>
          <dt>最近阶段进展</dt><dd>{cutflowTime(data.lastProgressAt)}</dd>
          <dt>剪辑端诊断</dt><dd>{data.diagnosticsState === 'available'
            ? data.workerAlive === false ? '处理进程离线' : data.scopeMatches === false ? '处理范围不含当前交付' : data.workerAlive === true ? '处理进程运行中' : '运行状态待确认'
            : data.diagnosticsState === 'stale' ? '诊断已过期，等待更新' : '剪辑端尚未提供运行诊断'}</dd>
          {data.nextRetryAt ? <><dt>下次自动查询</dt><dd>{cutflowTime(data.nextRetryAt)}</dd></> : null}
          {data.retryCount > 0 ? <><dt>连续异常次数</dt><dd>{data.retryCount}</dd></> : null}
        </dl>
        <small>每 10 秒读取本机记录；最近收到状态与阶段进展分别记录。</small>
        <dl className="cutflow-diagnostics__identity">
          <dt>交付编号</dt><dd>{data.handoffKey}</dd>
          {data.batchId ? <><dt>剪辑批次</dt><dd>{data.batchId}</dd></> : null}
          {data.errorCode ? <><dt>错误码</dt><dd>{data.errorCode}</dd></> : null}
        </dl>
      </> : null}
    </div> : null}
  </details>
}
