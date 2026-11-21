import { errorMessage } from '../../errorPresentation'
// 本文件由 App.tsx 拆分生成（2026-09-28 前端架构拆分 v2）。
// 代码为逐字搬运，未经逻辑改写。
import { AlertTriangle, Check, CheckCircle2, CircleAlert, Clapperboard, LoaderCircle, LockKeyhole, RefreshCw, RotateCcw, X } from 'lucide-react'
import { type FormEvent, useCallback, useEffect, useState } from 'react'
import { getKlingConfig, getKlingStatus, refreshKlingCapabilities, updateKlingConfig } from '../../api'
import { type KlingConfig, type KlingStatus } from '../../types'
import { type KlingConfigDraft, KLING_ASPECT_RATIO_OPTIONS, KLING_AUDIO_OPTIONS, KLING_BASE_URL_OPTIONS, KLING_MODEL_OPTIONS, KLING_RESOLUTION_OPTIONS, buildKlingConfigPatch, createKlingConfigDraft, formatKlingApiVersion, klingApiStatusPresentation, validateKlingConfig } from '../../klingConfig'

export function KlingProviderSettings({ adminId, onChanged }: { adminId: string; onChanged: () => Promise<void> }) {
  const [config, setConfig] = useState<KlingConfig | null>(null)
  const [draft, setDraft] = useState<KlingConfigDraft | null>(null)
  const [status, setStatus] = useState<KlingStatus | null>(null)
  const [busy, setBusy] = useState<'load' | 'save' | 'refresh' | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setBusy('load')
    try {
      const [nextConfig, nextStatus] = await Promise.all([
        getKlingConfig(adminId),
        getKlingStatus(adminId),
      ])
      setConfig(nextConfig)
      setDraft(createKlingConfigDraft(nextConfig))
      setStatus(nextStatus)
      setError('')
    } catch (cause) {
      setError(errorMessage(cause, '无法读取可灵通道配置'))
    } finally { setBusy(null) }
  }, [adminId])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const updateDraft = <K extends keyof KlingConfigDraft>(field: K, value: KlingConfigDraft[K]) => {
    setDraft((current) => current ? { ...current, [field]: value } : current)
    setError('')
    setNotice('')
  }

  const refreshCapabilities = async () => {
    setBusy('refresh')
    setError('')
    setNotice('')
    try {
      setStatus(await refreshKlingCapabilities(adminId))
      setNotice('已重新检查可灵连接。')
    } catch (cause) {
      setError(errorMessage(cause, '暂时连不上可灵，请稍后再试'))
    } finally { setBusy(null) }
  }

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!draft || !config) return
    const validationError = validateKlingConfig(draft, config.apiKeyConfigured)
    if (validationError) {
      setError(validationError)
      setNotice('')
      return
    }
    setBusy('save')
    setError('')
    setNotice('')
    try {
      const next = await updateKlingConfig(adminId, buildKlingConfigPatch(draft))
      setConfig(next)
      setDraft(createKlingConfigDraft(next))
      setNotice('可灵通道配置已保存；后续新任务使用新配置，已提交任务不受影响。')
      await onChanged()
      setStatus(await getKlingStatus(adminId))
    } catch (cause) {
      setError(errorMessage(cause, '可灵通道配置保存失败'))
    } finally { setBusy(null) }
  }

  const isReady = Boolean(status?.available && status.authenticated)
  const apiState = klingApiStatusPresentation(status?.status, status?.enabled, status?.reason)
  const apiVersion = formatKlingApiVersion(status?.cliVersion)

  const apiKeyWillExist = Boolean(draft?.apiKey.trim()) || Boolean(config?.apiKeyConfigured && !draft?.clearApiKey)
  const hasChanges = Boolean(config && draft && (
    draft.backend !== config.backend
    || draft.baseUrl.trim() !== (config.baseUrl ?? '')
    || draft.callbackUrl.trim() !== (config.callbackUrl ?? '')
    || draft.model !== config.model
    || draft.duration !== config.duration
    || draft.aspectRatio !== config.aspectRatio
    || draft.resolution !== config.resolution
    || draft.audio !== config.audio
    || draft.multiShot !== config.multiShot
    || draft.maxSubmitConcurrency !== config.maxSubmitConcurrency
    || draft.maxPollConcurrency !== config.maxPollConcurrency
    || draft.enabled !== config.enabled
    || draft.apiKey.trim()
    || draft.clearApiKey
  ))

  return <form className="settings-section kling-provider-section" onSubmit={(event) => void save(event)}>
    <div className="settings-section-heading">
      <div><span>{draft?.backend === 'cli' ? '可灵 CLI / MCP' : '可灵官方 API'}</span><h2>生产生成配置</h2><p>{draft?.backend === 'cli' ? 'CLI 使用本机 OAuth 登录，不需要填写 API Key。启用后可检查账号的 MCP 模型能力。' : '这里设置可灵的连接信息和默认生成选项。保存后会用于后续新任务。'}</p></div>
      <div className="provider-actions">
        <button className="secondary-button" type="button" disabled={busy !== null || !status?.enabled} onClick={() => void refreshCapabilities()}><RefreshCw className={busy === 'refresh' ? 'spin' : ''} size={16} />检查连接</button>
        <button className="primary-button" type="submit" disabled={busy !== null || !hasChanges}>{busy === 'save' ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}{busy === 'save' ? '正在保存' : '保存服务设置'}</button>
      </div>
    </div>

    {busy === 'load' && !draft ? <div className="account-loading"><LoaderCircle className="spin" size={20} />正在读取可灵配置…</div> : null}
    {draft ? <>
      <div className="kling-provider-toolbar">
        <div className="api-status-card">
          <div className="api-status-card-icon"><Clapperboard size={20} /></div>
          <div><small>可灵连接状态{apiVersion ? ` · ${apiVersion}` : ''}</small><strong>{apiState.label}</strong><span>{status ? apiState.message : '正在读取可灵连接状态'}</span></div>
          <span className={`api-status-state is-${apiState.tone}`}>{busy === 'refresh' ? <LoaderCircle className="spin" size={13} /> : isReady ? <CheckCircle2 size={13} /> : <CircleAlert size={13} />}{apiState.label}</span>
        </div>
        <div className="kling-enabled-control">
          <span><strong>开启可灵服务</strong><small>{draft.enabled ? '连接检查通过后，新任务可以发送到可灵' : '新任务不会提交到可灵'}</small></span>
          <label className="settings-switch">
            <input type="checkbox" checked={draft.enabled} disabled={busy !== null} aria-label="开启可灵服务" onChange={(event) => updateDraft('enabled', event.target.checked)} />
            <span aria-hidden="true" />
          </label>
        </div>
      </div>

      {draft.backend === 'cli' ? <div className="kling-config-block">
        <div className="kling-config-heading">
          <div><strong>CLI / MCP 连接详情</strong><small>ALLWE 调用本机 kling-cli，再由 CLI 连接可灵官方 MCP 服务。</small></div>
          <span className={status?.status === 'KLING_CLI_READY' ? 'is-configured' : 'is-missing'}>{status?.status === 'KLING_CLI_READY' ? 'OAuth 已授权' : draft.enabled ? '待检查连接' : '尚未启用'}</span>
        </div>
        <div className="kling-settings-grid">
          <div><strong>本机 CLI 版本</strong><p>{status?.cliVersion || '启用后可查询'}</p></div>
          <div><strong>当前生成模型</strong><p>kling-video-v3_0_omni</p></div>
          <div><strong>商品图参考能力</strong><p>{status?.supportedModels.includes('kling-video-v3_0_omni') ? '分镜图 + 独立商品图（最多 7 个图片参考槽位）' : '启用后检查账号模型支持'}</p></div>
          <div><strong>MCP 工具状态</strong><p>{status?.supportedTools.includes('omni_ref_video') ? 'omni_ref_video 已发现' : '待验证'}</p></div>
        </div>
        <small>CLI 不需要重新填写 API Key、API 地区和回调地址。新任务使用此通道；历史任务继续通过原通道查询。</small>
      </div> : null}

      {draft.backend !== 'cli' ? <>
      <div className="kling-config-block">
        <div className="kling-config-heading"><div><strong>服务密钥</strong><small>密钥保存在网站后台，保存后不会显示完整内容。</small></div><span className={apiKeyWillExist ? 'is-configured' : 'is-missing'}>{apiKeyWillExist ? '已配置' : '未配置'}</span></div>
        <div className="kling-api-key-row">
          <div className="credential-field">
            <label htmlFor="kling-api-key"><strong>可灵服务密钥</strong><small>{config?.apiKeyConfigured && !draft.clearApiKey ? '输入新密钥将替换现有密钥' : '保存后输入框会自动清空'}</small></label>
            <div className="secret-input"><LockKeyhole size={16} /><input id="kling-api-key" type="password" value={draft.apiKey} onChange={(event) => setDraft((current) => current ? { ...current, apiKey: event.target.value, clearApiKey: false } : current)} placeholder={config?.apiKeyConfigured && !draft.clearApiKey ? '已配置 · 输入新密钥进行替换' : '输入可灵官方 服务密钥'} autoComplete="new-password" spellCheck={false} minLength={8} maxLength={4096} aria-describedby="kling-api-key-help" /></div>
            <small id="kling-api-key-help">为安全起见，已保存的密钥永不显示完整内容；留空不会覆盖原值。</small>
          </div>
          <button className="danger-button" type="button" disabled={busy !== null || (!config?.apiKeyConfigured && !draft.apiKey)} onClick={() => setDraft((current) => current ? { ...current, apiKey: '', clearApiKey: !current.clearApiKey } : current)}>{draft.clearApiKey ? <RotateCcw size={15} /> : <X size={15} />}{draft.clearApiKey ? '取消清除' : '清除服务密钥'}</button>
        </div>
        {draft.clearApiKey ? <div className="inline-alert inline-alert--warning"><AlertTriangle size={16} />保存后会删除当前服务密钥。如果还要使用可灵，请先填写新密钥。</div> : null}
      </div>

      <div className="kling-settings-grid">
        <label htmlFor="kling-base-url"><strong>服务地区</strong><select id="kling-base-url" value={draft.baseUrl} disabled={busy !== null} onChange={(event) => updateDraft('baseUrl', event.target.value)}>{KLING_BASE_URL_OPTIONS.map((value) => <option key={value} value={value}>{value.includes('beijing') ? '中国站 · 北京' : '海外站 · 新加坡'}</option>)}</select><small>{draft.baseUrl}</small></label>
        <label htmlFor="kling-callback-url"><strong>结果通知地址</strong><input id="kling-callback-url" type="url" value={draft.callbackUrl} disabled={busy !== null} onChange={(event) => updateDraft('callbackUrl', event.target.value)} placeholder="https://example.com/api/kling/callback" autoComplete="url" spellCheck={false} maxLength={2048} /><small>可选；留空时由服务端轮询任务状态</small></label>
        <label htmlFor="kling-model"><strong>模型</strong><select id="kling-model" value={draft.model} disabled={busy !== null} onChange={(event) => updateDraft('model', event.target.value)}>{Array.from(new Set([...KLING_MODEL_OPTIONS, draft.model])).map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <label htmlFor="kling-duration"><strong>时长</strong><input id="kling-duration" type="number" min={3} max={draft.model === 'kling-3.0-omni' ? 15 : 30} step={1} value={draft.duration} disabled={busy !== null} onChange={(event) => updateDraft('duration', Number(event.target.value))} /><small>{draft.model === 'kling-3.0-omni' ? '允许范围 3–15 秒' : '允许范围 3–30 秒'}</small></label>
        <label htmlFor="kling-aspect-ratio"><strong>画面比例</strong><select id="kling-aspect-ratio" value={draft.aspectRatio} disabled={busy !== null} onChange={(event) => updateDraft('aspectRatio', event.target.value)}>{Array.from(new Set([...KLING_ASPECT_RATIO_OPTIONS, draft.aspectRatio])).map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <label htmlFor="kling-resolution"><strong>分辨率</strong><select id="kling-resolution" value={draft.resolution} disabled={busy !== null} onChange={(event) => updateDraft('resolution', event.target.value)}>{Array.from(new Set([...KLING_RESOLUTION_OPTIONS, draft.resolution])).map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <label htmlFor="kling-audio"><strong>音频</strong><select id="kling-audio" value={draft.audio} disabled={busy !== null} onChange={(event) => updateDraft('audio', event.target.value)}>{KLING_AUDIO_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
        <label htmlFor="kling-submit-concurrency"><strong>同时提交的任务数</strong><input id="kling-submit-concurrency" type="number" min={1} max={20} step={1} value={draft.maxSubmitConcurrency} disabled={busy !== null} onChange={(event) => updateDraft('maxSubmitConcurrency', Number(event.target.value))} /><small>允许范围 1–20</small></label>
        <label htmlFor="kling-poll-concurrency"><strong>同时查询的任务数</strong><input id="kling-poll-concurrency" type="number" min={1} max={50} step={1} value={draft.maxPollConcurrency} disabled={busy !== null} onChange={(event) => updateDraft('maxPollConcurrency', Number(event.target.value))} /><small>允许范围 1–50</small></label>
        <label className="kling-inline-switch"><span><strong>多镜头</strong><small>允许模型在一次生成中规划多个镜头</small></span><span className="settings-switch"><input type="checkbox" checked={draft.multiShot} disabled={busy !== null} aria-label="启用可灵多镜头" onChange={(event) => updateDraft('multiShot', event.target.checked)} /><span aria-hidden="true" /></span></label>
      </div>
      </> : null}
    </> : null}

    <div className="kling-provider-feedback" aria-live="polite" aria-atomic="true">
      {notice ? <p><CheckCircle2 size={15} />{notice}</p> : null}
      {error ? <div className="inline-alert inline-alert--danger" role="alert"><AlertTriangle size={16} />{error}</div> : null}
    </div>
  </form>
}
