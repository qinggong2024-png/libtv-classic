import { ChevronLeft, ChevronRight } from 'lucide-react'

export function ReportPagination({ label, offset, limit, total, busy = false, onPage, onLimit }: {
  label: string; offset: number; limit: number; total: number; busy?: boolean
  onPage: (offset: number) => void; onLimit: (limit: number) => void
}) {
  return <nav className="usage-pagination" aria-label={`${label}分页`}>
    <label>每页<select aria-label={`${label}每页条数`} value={limit} disabled={busy} onChange={(event) => onLimit(Number(event.target.value))}>
      {[10, 20, 50].map((size) => <option value={size} key={size}>{size} 条</option>)}
    </select></label>
    <span role="status">第 {Math.floor(offset / limit) + 1} / {Math.max(1, Math.ceil(total / limit))} 页 · 共 {total} 条</span>
    <div><button className="icon-button" type="button" aria-label={`${label}上一页`} title="上一页" disabled={busy || offset === 0} onClick={() => onPage(Math.max(0, offset - limit))}><ChevronLeft size={18} /></button>
      <button className="icon-button" type="button" aria-label={`${label}下一页`} title="下一页" disabled={busy || offset + limit >= total} onClick={() => onPage(offset + limit)}><ChevronRight size={18} /></button></div>
  </nav>
}
