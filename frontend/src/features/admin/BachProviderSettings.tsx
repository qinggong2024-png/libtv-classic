import { errorMessage } from '../../errorPresentation'
// 本文件由 App.tsx 拆分生成（2026-09-28 前端架构拆分 v2）。
// 代码为逐字搬运，未经逻辑改写。
import { AlertTriangle, CheckCircle2, CircleAlert, Clapperboard, LoaderCircle, LockKeyhole, RotateCcw, X } from 'lucide-react'
import { type FormEvent, useCallback, useEffect, useState } from 'react'
import { getBachConfig, updateBachConfig } from '../../api'
import { type BachConfig } from '../../types'

export function BachProviderSettings({ adminId, onChanged }: { adminId: string; onChanged: () => Promise<void> }) {
  const [config, setConfig] = useState<BachConfig | null>(null)
  const [accessKey, setAccessKey] = useState('')
  const [secretKey, setSecretKey] = useState('')
  const [clearAccessKey, setClearAccessKey] = useState(false)
  const [clearSecretKey, setClearSecretKey] = useState(false)
  const [busy, setBusy] = useState<'load' | 'save' | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setBusy('load')
    try {
      setConfig(await getBachConfig(adminId))
      setAccessKey('')
      setSecretKey('')
      setClearAccessKey(false)
      setClearSecretKey(false)
      setError('')
    } catch (cause) {
      setError(errorMessage(cause, '无法读取 Bach 服务 配置'))
    } finally { setBusy(null) }
  }, [adminId])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!config) return
    if (config.enabled && !accessKey.trim() && !config.accessKeyConfigured && (clearAccessKey || !config.accessKeyConfigured)) {
      setError('启用 Bach 前请先填写 Access密钥。')
      return
    }
    if (config.enabled && !secretKey.trim() && !config.secretKeyConfigured && (clearSecretKey || !config.secretKeyConfigured)) {
      setError('启用 Bach 前请先填写保密密钥。')
      return
    }
    setBusy('save')
    setError('')
    setNotice('')
    const patch: Record<string, unknown> = {}
    patch.enabled = config.enabled
    patch.model = config.model
    patch.resolution = config.resolution
    patch.duration = config.duration
    patch.aspectRatio = config.aspectRatio
    patch.generateAudio = config.generateAudio
    if (accessKey.trim()) patch.accessKey = accessKey.trim()
    if (secretKey.trim()) patch.secretKey = secretKey.trim()
    if (clearAccessKey) patch.clearAccessKey = true
    if (clearSecretKey) patch.clearSecretKey = true
    try {
      const next = await updateBachConfig(adminId, patch)
      setConfig(next)
      setAccessKey('')
      setSecretKey('')
      setClearAccessKey(false)
      setClearSecretKey(false)
      setNotice('Bach AK/SK 配置已安全保存；选择 Bach 通道后用于后续新任务。')
      await onChanged()
    } catch (cause) {
      setError(errorMessage(cause, 'Bach 服务 配置保存失败'))
    } finally { setBusy(null) }
  }

  return <form className="settings-section provider-section" onSubmit={(event) => void save(event)}>
    <div className="settings-section-heading">
      <div><span>Bach 服务</span><h2>首尾帧视频配置</h2><p>AK/SK 只发送到服务端并加密保存，页面不会读取或显示完整内容原值。当前阶段接入 Image-to-Video。</p></div>
      <button className="primary-button" type="submit" disabled={busy !== null || !config}>{busy === 'save' ? <LoaderCircle className="spin" size={16} /> : <LockKeyhole size={16} />}{busy === 'save' ? '正在保存' : '保存 Bach 配置'}</button>
    </div>
    {busy === 'load' && !config ? <div className="account-loading"><LoaderCircle className="spin" size={20} />正在读取 Bach 配置…</div> : null}
    {config ? <>
      <div className="kling-provider-toolbar">
        <div className="api-status-card">
          <div className="api-status-card-icon"><Clapperboard size={20} /></div>
          <div><small>Bach 连接信息</small><strong>{config.credentialsConfigured ? '已配置' : '未配置'}</strong><span>{config.credentialsConfigured ? '保存后可在上方通道选择中启用 Bach。' : '请填写访问密钥和保密密钥。'}</span></div>
          <span className={`api-status-state is-${config.credentialsConfigured ? 'success' : 'warning'}`}>{config.credentialsConfigured ? <CheckCircle2 size={13} /> : <CircleAlert size={13} />}{config.credentialsConfigured ? '已配置' : '待配置'}</span>
        </div>
        <div className="kling-enabled-control"><span><strong>启用 Bach</strong><small>{config.enabled ? '允许后续任务使用 Bach' : '新任务不会提交到 Bach'}</small></span><label className="settings-switch"><input type="checkbox" checked={config.enabled} onChange={(event) => setConfig((current) => current ? { ...current, enabled: event.target.checked } : current)} aria-label="开启 Bach 服务" /><span aria-hidden="true" /></label></div>
      </div>
      <div className="kling-api-key-row">
        <div className="credential-field"><label htmlFor="bach-access-key"><strong>访问密钥（AccessKey）</strong><small>{config.accessKeyConfigured && !clearAccessKey ? '已配置 · 输入新的访问密钥 可替换' : '保存后输入框自动清空'}</small></label><div className="secret-input"><LockKeyhole size={16} /><input id="bach-access-key" type="password" value={accessKey} onChange={(event) => { setAccessKey(event.target.value); setClearAccessKey(false) }} placeholder={config.accessKeyConfigured && !clearAccessKey ? '已配置 · 输入新的访问密钥 进行替换' : '输入 Bach 访问密钥'} autoComplete="new-password" spellCheck={false} minLength={8} maxLength={4096} /></div><small>密钥保存在网站后台，保存后只显示是否已填写。</small></div>
        <button className="danger-button" type="button" disabled={busy !== null || (!config.accessKeyConfigured && !accessKey)} onClick={() => setClearAccessKey((value) => !value)}>{clearAccessKey ? <RotateCcw size={15} /> : <X size={15} />}{clearAccessKey ? '取消清除访问密钥' : '清除访问密钥'}</button>
      </div>
      <div className="kling-api-key-row">
        <div className="credential-field"><label htmlFor="bach-secret-key"><strong>保密密钥（SecretKey）</strong><small>{config.secretKeyConfigured && !clearSecretKey ? '已配置 · 输入新的保密密钥 可替换' : '保存后输入框自动清空'}</small></label><div className="secret-input"><LockKeyhole size={16} /><input id="bach-secret-key" type="password" value={secretKey} onChange={(event) => { setSecretKey(event.target.value); setClearSecretKey(false) }} placeholder={config.secretKeyConfigured && !clearSecretKey ? '已配置 · 输入新的保密密钥 进行替换' : '输入 Bach 保密密钥'} autoComplete="new-password" spellCheck={false} minLength={8} maxLength={4096} /></div><small>保密密钥会安全保存，保存后不会显示完整内容。</small></div>
        <button className="danger-button" type="button" disabled={busy !== null || (!config.secretKeyConfigured && !secretKey)} onClick={() => setClearSecretKey((value) => !value)}>{clearSecretKey ? <RotateCcw size={15} /> : <X size={15} />}{clearSecretKey ? '取消清除保密密钥' : '清除保密密钥'}</button>
      </div>
      <div className="kling-settings-grid">
        <label><strong>模型</strong><input value={config.model} readOnly /></label>
        <label><strong>分辨率</strong><select value={config.resolution} onChange={(event) => setConfig((current) => current ? { ...current, resolution: event.target.value } : current)}><option value="720p">720p</option><option value="1080p">1080p</option></select></label>
        <label><strong>时长</strong><input type="number" min={1} max={8} value={config.duration} onChange={(event) => setConfig((current) => current ? { ...current, duration: Number(event.target.value) } : current)} /><small>首帧 1–8 秒；首尾帧 1–6 秒</small></label>
        <label><strong>画面比例</strong><select value={config.aspectRatio} onChange={(event) => setConfig((current) => current ? { ...current, aspectRatio: event.target.value } : current)}><option value="9:16">9:16</option><option value="16:9">16:9</option><option value="1:1">1:1</option></select></label>
        <label className="kling-inline-switch"><span><strong>生成音频</strong><small>仅在 Bach 任务 payload 中启用音频</small></span><span className="settings-switch"><input type="checkbox" checked={config.generateAudio} onChange={(event) => setConfig((current) => current ? { ...current, generateAudio: event.target.checked } : current)} /><span aria-hidden="true" /></span></label>
      </div>
    </> : null}
    <div className="kling-provider-feedback" aria-live="polite" aria-atomic="true">{notice ? <p><CheckCircle2 size={15} />{notice}</p> : null}{error ? <div className="inline-alert inline-alert--danger" role="alert"><AlertTriangle size={16} />{error}</div> : null}</div>
  </form>
}
