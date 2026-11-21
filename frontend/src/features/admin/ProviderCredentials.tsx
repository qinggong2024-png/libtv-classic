import { errorMessage } from '../../errorPresentation'
// 本文件由 App.tsx 拆分生成（2026-09-28 前端架构拆分 v2）。
// 代码为逐字搬运，未经逻辑改写。
import { AlertTriangle, Check, CheckCircle2, Eye, EyeOff, Globe2, LoaderCircle, LockKeyhole } from 'lucide-react'
import { type FormEvent, useCallback, useEffect, useState } from 'react'
import { getAdminCredentials, getKlingConfig, getWaveSpeedConfig, updateAdminCredentials, updateKlingConfig, updateWaveSpeedConfig } from '../../api'
import { type AdminCredentialsStatus, type KlingConfig } from '../../types'
import { formatTime } from '../../domain/taskPresentation'
import { WAVESPEED_RECREATE_MODEL_DEFAULT } from '../../wavespeedConfig'

function validateCompatibleBaseUrl(value: string): string {
  const normalized = value.trim()
  if (!normalized) return ''
  try {
    const url = new URL(normalized)
    const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
    if (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback)) return '服务地址请以 https:// 开头；连接本机服务时可以使用 http://。'
    if (url.username || url.password || url.search || url.hash) return '地址中不要包含账号、密码，也不要包含 ? 或 # 后面的内容。'
  } catch { return '请输入完整地址，例如 https://gateway.example.com/v1。' }
  return ''
}
export function ProviderCredentials({ adminId }: { adminId: string | null }) {
  const [status, setStatus] = useState<AdminCredentialsStatus | null>(null)
  const [modelKey, setModelKey] = useState('')
  const [modelBaseUrl, setModelBaseUrl] = useState('')
  const [baseUrlError, setBaseUrlError] = useState('')
  const [showModel, setShowModel] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    let active = true
    getAdminCredentials(adminId)
      .then((next) => { if (active) { setStatus(next); setModelBaseUrl(next.modelBaseUrl ?? ''); setError('') } })
      .catch((cause) => { if (active) setError(errorMessage(cause, '无法读取管理员配置')) })
    return () => { active = false }
  }, [adminId])

  const save = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault()
    const modelApiKey = modelKey.trim()
    const normalizedBaseUrl = modelBaseUrl.trim()
    const baseUrlChanged = normalizedBaseUrl !== (status?.modelBaseUrl ?? '')
    const nextBaseUrlError = validateCompatibleBaseUrl(normalizedBaseUrl)
    setBaseUrlError(nextBaseUrlError)
    if (nextBaseUrlError) return
    if (!modelApiKey && !baseUrlChanged) {
      setError('请至少更新一项分析服务配置')
      return
    }
    setBusy(true)
    setError('')
    setSaved(false)
    try {
      const next = await updateAdminCredentials(adminId, {
        ...(modelApiKey ? { modelApiKey } : {}),
        ...(baseUrlChanged && normalizedBaseUrl ? { modelBaseUrl: normalizedBaseUrl } : {}),
        ...(baseUrlChanged && !normalizedBaseUrl ? { clearModelBaseUrl: true } : {}),
      })
      setStatus(next)
      setModelBaseUrl(next.modelBaseUrl ?? '')
      setModelKey('')
      setSaved(true)
      window.setTimeout(() => setSaved(false), 2200)
    } catch (cause) { setError(errorMessage(cause, '服务密钥没有保存成功，请稍后再试')) }
    finally { setBusy(false) }
  }

  const credentialState = (configured: boolean | undefined) => {
    if (status === null && !error) return '正在读取'
    return configured ? '已配置' : '未配置'
  }
  const hasChanges = Boolean(
    modelKey.trim()
    || modelBaseUrl.trim() !== (status?.modelBaseUrl ?? ''),
  )

  return <form className="settings-section credential-section" onSubmit={(event) => void save(event)}>
    <div className="settings-section-heading">
      <div><span>仅管理员</span><h2>素材分析服务</h2><p>这里设置用于分析素材的 AI 服务。可灵的密钥请在可灵设置中填写。保存后不会显示完整密钥。</p></div>
      <button className="primary-button" type="submit" disabled={busy || !hasChanges}>{busy ? <LoaderCircle className="spin" size={16} /> : saved ? <Check size={16} /> : <LockKeyhole size={16} />}{busy ? '正在保存' : saved ? '已安全保存' : '保存配置'}</button>
    </div>
    <div className="credential-grid">
      <div className="credential-field">
        <label htmlFor="model-api-key"><strong>素材分析服务密钥</strong><small className={status?.modelConfigured ? 'is-configured' : ''}>{credentialState(status?.modelConfigured)}</small></label>
        <div className="secret-input"><LockKeyhole size={16} /><input id="model-api-key" type={showModel ? 'text' : 'password'} value={modelKey} onChange={(event) => setModelKey(event.target.value)} placeholder={status?.modelConfigured ? '输入新密钥以替换当前配置' : '输入素材分析服务密钥'} autoComplete="new-password" spellCheck={false} minLength={8} maxLength={4096} aria-describedby="model-key-help" /><button type="button" onClick={() => setShowModel((value) => !value)} aria-label={showModel ? '隐藏分析服务密钥' : '显示分析服务密钥'} title={showModel ? '隐藏' : '显示'}>{showModel ? <EyeOff size={16} /> : <Eye size={16} />}</button></div>
        <small id="model-key-help">用于服装、人物、场景分析和视频方案生成。</small>
      </div>
      <div className="credential-field credential-field--wide">
        <label htmlFor="model-base-url"><strong>OpenAI 兼容 Base URL</strong><small>{status?.modelBaseUrl ? '自定义' : '官方默认'}</small></label>
        <div className="secret-input"><Globe2 size={16} /><input id="model-base-url" type="url" value={modelBaseUrl} onChange={(event) => { setModelBaseUrl(event.target.value); if (baseUrlError) setBaseUrlError('') }} onBlur={() => setBaseUrlError(validateCompatibleBaseUrl(modelBaseUrl))} placeholder="https://gateway.example.com/v1" autoComplete="url" spellCheck={false} maxLength={2048} aria-describedby={`model-base-url-help${baseUrlError ? ' model-base-url-error' : ''}`} aria-invalid={Boolean(baseUrlError)} /></div>
        <small id="model-base-url-help">使用第三方分析服务时，请填写对方提供的完整服务地址，通常以 /v1 结尾；留空则使用 OpenAI 官方服务。</small>
        {baseUrlError ? <small id="model-base-url-error" className="field-error" role="alert">{baseUrlError}</small> : null}
      </div>
    </div>
    <div className="credential-feedback" aria-live="polite">
      {status?.updatedAt ? <p className="credential-updated">最近更新：{formatTime(status.updatedAt)}</p> : null}
      {error ? <div className="inline-alert inline-alert--danger credential-error" role="alert"><AlertTriangle size={16} />{error}</div> : null}
    </div>
  </form>
}

export function ProviderRoutingSettings({ adminId, onChanged }: { adminId: string; onChanged: () => Promise<void> }) {
  const [currentBackend, setCurrentBackend] = useState<KlingConfig['backend'] | null>(null)
  const [videoBackend, setVideoBackend] = useState<KlingConfig['backend']>('api')
  const [currentRecreateModel, setCurrentRecreateModel] = useState<string | null>(null)
  const [recreateModel, setRecreateModel] = useState<string>(WAVESPEED_RECREATE_MODEL_DEFAULT)
  const [busy, setBusy] = useState<'load' | 'save' | null>('load')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setBusy('load')
    try {
      const [config, wavespeed] = await Promise.all([getKlingConfig(adminId), getWaveSpeedConfig(adminId)])
      setCurrentBackend(config.backend)
      setVideoBackend(config.backend)
      const model = wavespeed.recreateVideoModel ?? WAVESPEED_RECREATE_MODEL_DEFAULT
      setCurrentRecreateModel(model)
      setRecreateModel(model)
      setError('')
    } catch (cause) {
      setError(errorMessage(cause, '暂时无法读取生成服务设置，请刷新后再试'))
    } finally { setBusy(null) }
  }, [adminId])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (currentBackend === null || currentRecreateModel === null || !hasChanges) return
    setBusy('save')
    setError('')
    setNotice('')
    try {
      if (currentRecreateModel !== recreateModel.trim()) {
        const config = await updateWaveSpeedConfig(adminId, { recreateVideoModel: recreateModel.trim() })
        if (config.recreateVideoModel !== recreateModel.trim()) throw new Error('服务端尚未支持视频复刻模型配置，请更新后端后重试。')
        setCurrentRecreateModel(config.recreateVideoModel)
        setRecreateModel(config.recreateVideoModel)
        setNotice('视频复刻模型已保存。')
      }
      if (currentBackend !== videoBackend) {
        const config = await updateKlingConfig(adminId, { backend: videoBackend })
        setCurrentBackend(config.backend)
        setVideoBackend(config.backend)
      }
      setNotice('选择生成服务已更新，仅影响后续新提交的任务。')
      await onChanged()
    } catch (cause) {
      setError(errorMessage(cause, '生成服务没有切换成功，请稍后再试'))
    } finally { setBusy(null) }
  }

  const videoHelp = videoBackend === 'api'
    ? '使用可灵官方服务的模型和视频参数。'
    : videoBackend === 'cli'
      ? '使用可灵 CLI / MCP 连接，以 Kling 3.0 Omni 生成视频。'
    : videoBackend === 'wavespeed'
      ? '使用下方 WaveSpeed 视频模型和参数。'
      : '使用下方 Bach 图片转视频模型和参数。'
  const hasChanges = currentBackend !== videoBackend || currentRecreateModel !== recreateModel.trim()

  return <form className="settings-section provider-section" onSubmit={(event) => void save(event)}>
    <div className="settings-section-heading">
      <div><span>生成服务选择</span><h2>选择生成服务</h2><p>分别管理图片、图生视频和视频复刻的通道。配置只影响后续新任务，历史任务仍使用原提供商和模型查询、下载。</p></div>
      <button className="primary-button" type="submit" disabled={busy !== null || currentBackend === null || currentRecreateModel === null || !hasChanges}>{busy === 'save' ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}{busy === 'save' ? '正在切换' : '应用生成服务选择'}</button>
    </div>
    {busy === 'load' && currentBackend === null ? <div className="account-loading"><LoaderCircle className="spin" size={20} />正在读取生成服务选择…</div> : null}
    {currentBackend !== null ? <div className="kling-settings-grid provider-routing-grid">
      <label htmlFor="image-provider-route"><strong>图片通道</strong><select id="image-provider-route" value="wavespeed" disabled><option value="wavespeed">WaveSpeed 图片服务</option></select><small>目前图片生成只能使用 WaveSpeed。</small></label>
      <label htmlFor="video-provider-route"><strong>视频通道</strong><select id="video-provider-route" value={videoBackend} disabled={busy !== null} onChange={(event) => setVideoBackend(event.target.value as KlingConfig['backend'])}><option value="api">可灵官方 API</option><option value="cli">可灵 CLI / MCP</option><option value="wavespeed">WaveSpeed 视频服务</option><option value="bach">Bach 图片转视频</option></select><small>{videoHelp}</small></label>
      <label htmlFor="recreate-provider-route"><strong>视频复刻通道</strong><select id="recreate-provider-route" value="wavespeed" disabled aria-describedby="recreate-provider-help"><option value="wavespeed">WaveSpeed 视频复刻服务</option></select><small id="recreate-provider-help">当前仅接入 WaveSpeed，独立于图生视频通道；共用下方 WaveSpeed 服务密钥。</small></label>
      <label className="provider-routing-model" htmlFor="recreate-video-model"><strong>视频复刻模型</strong><input id="recreate-video-model" value={recreateModel} onChange={(event) => setRecreateModel(event.target.value)} disabled={busy !== null} required maxLength={255} pattern="[A-Za-z0-9][A-Za-z0-9._\-]*(\/[A-Za-z0-9][A-Za-z0-9._\-]*)*\/video-edit" spellCheck={false} autoComplete="off" aria-describedby="recreate-model-help" /><small id="recreate-model-help">填写完整的 WaveSpeed Video Edit 模型名称，新复刻任务将提交到此模型。默认：bytedance/seedance-2.0/video-edit。自定义模型需要支持参考视频、参考图片和提示词，以及 4–15 秒、720p 视频；提取深度视频仍使用 Depth Anything。</small></label>
    </div> : null}
    <div className="kling-provider-feedback" aria-live="polite" aria-atomic="true">{notice ? <p><CheckCircle2 size={15} />{notice}</p> : null}{error ? <div className="inline-alert inline-alert--danger" role="alert"><AlertTriangle size={16} />{error}</div> : null}</div>
  </form>
}
