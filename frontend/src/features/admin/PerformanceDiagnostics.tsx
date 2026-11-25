import { useState } from 'react'
// Read-only, already-deployed same-origin endpoint; no shared API contract changes.
type PerformanceSnapshot = {
  scope: string
  windowSeconds: number
  sampleLimitPerKey: number
  uptimeSeconds: number
  rows: Array<{ kind: string; key: string; totalSinceStart: number; errorsSinceStart: number; locksSinceStart: number; sampleCount: number; p95Ms: number; maxMs: number }>
  activeTransactions: Array<{ key: string; ageMs: number; writePhaseMs: number | null }>
}

async function getPerformanceSnapshot(adminId: string): Promise<PerformanceSnapshot> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 8000)
  try {
    const response = await fetch('/api/v1/admin/performance', {
      credentials: 'same-origin', headers: { 'X-User-Id': adminId },
      signal: controller.signal, cache: 'no-store',
    })
    if (!response.ok) throw new Error('Performance snapshot unavailable')
    const value = await response.json()
    if (!value || !Array.isArray(value.rows) || !Array.isArray(value.activeTransactions)) {
      throw new Error('Invalid performance snapshot')
    }
    return value as PerformanceSnapshot
  } finally { window.clearTimeout(timeout) }
}

const labels: Record<string, string> = {
  http_ttfb: 'API 首包',
  sql: 'SQL 执行',
  transaction: '事务持续',
  write_phase: '写入阶段',
}

export function PerformanceDiagnostics({ adminId }: { adminId: string }) {
  const [data, setData] = useState<PerformanceSnapshot | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  const [kind, setKind] = useState('http_ttfb')
  const refresh = async () => {
    if (loading) return
    setLoading(true)
    setError(false)
    try { setData(await getPerformanceSnapshot(adminId)) }
    catch { setError(true) }
    finally { setLoading(false) }
  }
  const rows = data?.rows.filter(row => row.kind === kind) ?? []
  return <details className="ops-band">
    <summary>性能诊断 · API 进程</summary>
    <p className="ops-sampled-at">按需读取，不自动轮询。最近 15 分钟每项最多 256 个样本；重启后重新统计。</p>
    <button type="button" className="secondary-button" disabled={loading} onClick={() => void refresh()}>
      {loading ? '读取中…' : '读取性能指标'}
    </button>
    {error && <p role="alert">暂时无法读取性能指标，请稍后重试。</p>}
    {data && <>
      <p>本进程运行 {data.uptimeSeconds} 秒 · 当前事务（最多显示 20 条）：{data.activeTransactions.length}</p>
      <label>指标类型 <select value={kind} onChange={event => setKind(event.target.value)}>
        {Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', textAlign: 'left', fontSize: '0.85rem' }}>
          <thead><tr><th>接口 / 阶段</th><th>样本</th><th>P95 ms</th><th>最大 ms</th><th>累计异常</th><th>累计锁异常</th></tr></thead>
          <tbody>{rows.slice(0, 12).map(row => <tr key={row.key}>
            <td style={{ overflowWrap: 'anywhere' }}>{row.key}</td><td>{row.sampleCount}</td><td>{row.p95Ms}</td><td>{row.maxMs}</td><td>{row.errorsSinceStart}</td><td>{row.locksSinceStart}</td>
          </tr>)}</tbody>
        </table>
      </div>
      {!rows.length && <p>暂无该类样本。</p>}
      {data.activeTransactions.length > 0 && <div>
        <h4>当前事务</h4>
        {data.activeTransactions.slice(0, 8).map((row, index) => <p key={index}>{row.key} · {row.ageMs} ms{row.writePhaseMs !== null ? ` · 写入阶段 ${row.writePhaseMs} ms` : ''}</p>)}
      </div>}
      <small>首包不代表视频下载完成；写入阶段包含锁获取等待，不代表准确持锁时长。Runner / CUT Worker 另有日志。</small>
    </>}
  </details>
}
