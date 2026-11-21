import { useState, type FormEvent } from 'react'
import { previewCreativeV25, type CreativeV25PreviewResult } from '../../api'
import { errorMessage } from '../../errorPresentation'

export function CreativeV25PreviewPanel({ adminId }: { adminId: string }) {
  const [taskId, setTaskId] = useState('')
  const [model, setModel] = useState<'gpt-6.1-sol' | 'gpt-6.1-terra'>('gpt-6.1-sol')
  const [variantCount, setVariantCount] = useState<1 | 2>(1)
  const [creativeMode, setCreativeMode] = useState<'showcase' | 'usp'>('showcase')
  const [costConfirmed, setCostConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<CreativeV25PreviewResult | null>(null)
  const validId = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(taskId.trim())
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (busy || !validId || !costConfirmed) return
    setBusy(true)
    setError('')
    setResult(null)
    try {
      setResult(await previewCreativeV25(adminId, {
        taskId: taskId.trim(),
        model,
        variantCount,
        creativeMode,
        confirmModelCost: true,
      }))
    } catch (cause) {
      setError(errorMessage(cause, '编排预览失败，请查看中转站用量，避免立即重复提交。'))
    } finally {
      setBusy(false)
      setCostConfirmed(false)
    }
  }
  return <section className="settings-section creative-v25-preview" aria-label="AI 导演 V2.5 隔离试点">
    <div className="settings-section-heading">
      <div><span>管理员受控试点</span><h2>AI 导演 V2.5 · 编排预览</h2>
        <p>输入已有视频任务 ID，读取该任务的分镜图和商品图，生成可灵提示词。不会新增任务或调用可灵，也不会更改旧任务。</p></div>
    </div>
    <form onSubmit={(event) => void submit(event)} style={{ display: 'grid', gap: 12, maxWidth: 660 }}>
      <label>历史任务 ID（完整 UUID）
        <input style={{ display: 'block', width: '100%', marginTop: 5 }} value={taskId}
          onChange={(event) => setTaskId(event.target.value)}
          placeholder="例如 00000000-0000-0000-0000-000000000000" disabled={busy} />
      </label>
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
        <label>AI 模型
          <select style={{ display: 'block', marginTop: 5 }} value={model} disabled={busy}
            onChange={(event) => setModel(event.target.value as typeof model)}>
            <option value="gpt-6.1-sol">GPT-6.1 Sol · low</option>
            <option value="gpt-6.1-terra">GPT-6.1 Terra · none</option>
          </select>
        </label>
        <label>编排方案
          <select style={{ display: 'block', marginTop: 5 }} value={variantCount} disabled={busy}
            onChange={(event) => setVariantCount(Number(event.target.value) as 1 | 2)}>
            <option value={1}>单方案</option><option value={2}>双方案</option>
          </select>
        </label>
      </div>
      <label>创意模式（只替换导演规则，不增加输出字段）
        <select style={{ display: 'block', marginTop: 5 }} value={creativeMode} disabled={busy}
          onChange={(event) => setCreativeMode(event.target.value as 'showcase' | 'usp')}>
          <option value="showcase">自然商品展示（原版）</option>
          <option value="usp">USP 成交表达（Hook / 动作 / 购买理由）</option>
        </select>
      </label>
      <label style={{ display: 'flex', gap: 7, alignItems: 'flex-start' }}>
        <input type="checkbox" checked={costConfirmed} disabled={busy}
          onChange={(event) => setCostConfirmed(event.target.checked)} />
        <span>确认发起一次付费大模型编排请求；失败不会自动重试，最长等待约 3 分钟。不会产生可灵视频费用。</span>
      </label>
      <div><button className="primary-button" type="submit" disabled={!validId || !costConfirmed || busy}>
        {busy ? 'AI 正在分析参考图，请勿重复提交…' : '生成 V2.5 编排预览'}
      </button></div>
    </form>
    {error ? <div className="inline-alert inline-alert--danger" role="alert" style={{ marginTop: 12 }}>{error}</div> : null}
    {result ? <div aria-live="polite" style={{ marginTop: 18, display: 'grid', gap: 14 }}>
      <p><strong>预览完成</strong> · {result.model} · {result.storyboardCount} 张分镜 / {result.productCount} 张商品图 · {result.durationSeconds} 秒 · 耗时 {result.elapsedSeconds} 秒 · 未提交可灵</p>
      {result.variants.map((variant, index) => <div className="settings-section" key={index} style={{ padding: 16 }}>
        <h3>方案 {index + 1} · {variant.idea}</h3>
        <p><strong>前三秒 Hook：</strong>{variant.hook}</p>
        <p><strong>购买理由：</strong>{variant.buyerReason}</p>
        <details><summary>完整镜头动作与参考图事实（{variant.beats.length} 个镜头）</summary>
          <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify({ beats: variant.beats, visualFacts: variant.visualFacts }, null, 2)}</pre>
        </details>
        {!variant.klingPromptWithinLimit ? <p role="alert" style={{ color: 'var(--danger, #b42318)' }}>提示词超过当前可灵提交长度限制，仅用于诊断，请勿直接提交。</p> : null}
        <details open><summary>可灵提示词（仅预览，不会自动提交）</summary>
          <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{variant.prompt}</pre>
        </details>
        <button type="button" className="secondary-button"
          onClick={() => void navigator.clipboard.writeText(variant.prompt)}>复制提示词</button>
      </div>)}
    </div> : null}
  </section>
}
