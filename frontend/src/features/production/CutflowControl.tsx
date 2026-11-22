import { Download, ExternalLink, LoaderCircle, Play, RefreshCw, Scissors } from 'lucide-react'
import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { ApiError, bindCutflowSourceSite, createCutflowHandoff, fetchAssetObjectUrl, listCutflowHandoffs, retryCutflowHandoff, saveAsset, type CutflowHandoff, type MultiCutflowTask } from '../../api'
import { CutflowSiteSelect } from './CutflowSiteSelect'
import { CutflowDiagnostics } from './CutflowDiagnostics'
import { CutflowVideoThumbnail } from './CutflowVideoThumbnail'
import { PortalVideoModal } from '../../shared/components/Media'
import { GenerationCostBadge } from '../../shared/components/GenerationCostBadge'
import { cutflowPresentation, cutflowRecoveryHint, cutflowReviewLabel, multiCutflowPresentation } from '../../domain/cutflowPresentation'
import './cutflow.css'

const revisionStatusLabel: Record<string, string> = {
  QUEUED: '等待剪辑',
  READY: '已送剪，等待接单',
  IMPORTING: '正在导入',
  EDITING: '正在剪辑',
  PENDING_REVIEW: '成片待审核',
  FAILED: '剪辑失败',
  CANCELED: '已取消',
  UNKNOWN: '状态待确认',
}

type PlayableOutput = Pick<MultiCutflowTask['outputs'][number], 'id' | 'mediaUrl' | 'reviewStatus' | 'durationSeconds'>

export function CutflowControl({ taskId, variantId, artifactId, siteProfileId, canWrite = true, allowSubmit = true, revisionTasks = [], multiSourceTasks = [], sourceLabel, onSubmitted, onStateRead }: { taskId: string; variantId: string; artifactId: string; siteProfileId?: string | null; canWrite?: boolean; allowSubmit?: boolean; revisionTasks?: MultiCutflowTask[]; multiSourceTasks?: MultiCutflowTask[]; sourceLabel?: string; onSubmitted?: () => void; onStateRead?: (state: { enabled: boolean; handoffs: CutflowHandoff[] }) => void }) {
  const [sourceSite, setSourceSite] = useState('')
  const [siteBound, setSiteBound] = useState(Boolean(siteProfileId))
  const [siteRequired, setSiteRequired] = useState(true)
  const [refreshToken, setRefreshToken] = useState(0)
  const [handoffs, setHandoffs] = useState<CutflowHandoff[]>([])
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [readError, setReadError] = useState('')
  const [reading, setReading] = useState(true)
  const [uncertain, setUncertain] = useState(false)
  const [mediaBusy, setMediaBusy] = useState<string | null>(null)
  const [preview, setPreview] = useState<{ url: string; title: string } | null>(null)
  const mutation = useRef(false)
  const mediaOperation = useRef(false)
  const reconciliationPending = useRef(false)
  const mounted = useRef(false)
  const readVersion = useRef(0)
  const previewTrigger = useRef<HTMLButtonElement | null>(null)
  const current = handoffs.find(item => item.sourceArtifactId === artifactId)
  const outputs = handoffs.filter(item => item.sourceArtifactId === artifactId).flatMap(item => item.outputs)
  const revisionOutputs = revisionTasks.flatMap(item => item.outputs)
  const outputCount = outputs.length + revisionOutputs.length
  const latestMultiSourceTask = [...multiSourceTasks].sort((left, right) => (
    Date.parse(right.updatedAt) - Date.parse(left.updatedAt)
  ))[0] ?? null
  const latestMultiSourcePresentation = latestMultiSourceTask
    ? multiCutflowPresentation(latestMultiSourceTask)
    : null
  const reportState = useEffectEvent((state: { enabled: boolean; handoffs: CutflowHandoff[] }) => onStateRead?.(state))
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      const version = readVersion.current
      let delay = 15000
      try {
        const result = await listCutflowHandoffs(variantId, controller.signal)
        if (!controller.signal.aborted && !mutation.current && version === readVersion.current) {
          setHandoffs(result.handoffs)
          reportState(result)
          setLoaded(result.enabled)
          setSiteRequired(result.siteRequired !== false)
          setReadError(result.enabled ? '' : '剪辑交接尚未启用')
          const latest = result.handoffs.find(item => item.sourceArtifactId === artifactId)
          if (latest && reconciliationPending.current) {
            reconciliationPending.current = false
            setUncertain(false)
            setError('')
          }
          if (latest && cutflowPresentation(latest).active) delay = 5000
        }
      } catch (cause) {
        if (!controller.signal.aborted && version === readVersion.current) setReadError(cause instanceof Error ? cause.message : '剪辑状态读取失败')
      } finally {
        if (!controller.signal.aborted) {
          setReading(false)
          timer = setTimeout(() => void refresh(), delay)
        }
      }
    }
    void refresh()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [variantId, artifactId, refreshToken])
  useEffect(() => {
    if (!preview && previewTrigger.current) {
      previewTrigger.current.focus()
      previewTrigger.current = null
    }
    return () => { if (preview) URL.revokeObjectURL(preview.url) }
  }, [preview])
  async function submit() {
    if (mutation.current || !loaded || !canWrite || readError || uncertain || (current && current.status !== 'FAILED')) return
    mutation.current = true
    readVersion.current += 1
    setBusy(true)
    setError('')
    try {
      const item = current?.status === 'FAILED'
        ? await retryCutflowHandoff(current.id) : await createCutflowHandoff(taskId, variantId)
      if (mounted.current) {
        setHandoffs(previous => [item, ...previous.filter(other => other.id !== item.id)])
        onSubmitted?.()
      }
    } catch (cause) {
      if (mounted.current) {
        const unknown = !(cause instanceof ApiError && cause.status >= 400 && cause.status < 500 && !cause.mayHaveBeenAccepted)
        reconciliationPending.current = unknown
        setUncertain(unknown)
        setError(`${cause instanceof Error ? cause.message : '提交失败'}${unknown ? '；请刷新剪辑状态核对提交结果。' : ''}`)
      }
    } finally {
      mutation.current = false
      if (mounted.current) {
        setBusy(false)
        setRefreshToken(value => value + 1)
      }
    }
  }
  async function bindSite() {
    if (mutation.current || !sourceSite || !canWrite) return
    mutation.current = true
    setBusy(true)
    setError('')
    try {
      await bindCutflowSourceSite(taskId, sourceSite)
      if (mounted.current) setSiteBound(true)
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : '站点保存失败，请重试')
    } finally {
      mutation.current = false
      if (mounted.current) setBusy(false)
    }
  }
  async function handleMedia(output: PlayableOutput, index: number, trigger?: HTMLButtonElement, versionLabel = '剪辑成片') {
    if (mediaOperation.current) return
    mediaOperation.current = true
    setMediaBusy(`${trigger ? 'preview' : 'download'}:${output.id}`)
    setError('')
    try {
      if (trigger) {
        previewTrigger.current = trigger
        const url = await fetchAssetObjectUrl(output.mediaUrl)
        if (mounted.current) setPreview({ url, title: `${sourceLabel ? `${sourceLabel} · ` : ''}${versionLabel} ${index + 1} · ${cutflowReviewLabel(output.reviewStatus)}` })
        else URL.revokeObjectURL(url)
      } else {
        await saveAsset(`${output.mediaUrl}?download=true`, `edited-${output.id}.mp4`)
      }
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : '成片读取失败，请重试')
    } finally {
      mediaOperation.current = false
      if (mounted.current) setMediaBusy(null)
    }
  }
  if (!allowSubmit && !current && !outputCount && !revisionTasks.length && !readError) {
    if (!loaded && !latestMultiSourceTask) return <section className="cutflow-control cutflow-control--empty" aria-label="剪辑成片"><div className="cutflow-control__toolbar"><h3>剪辑成片</h3></div><div className="cutflow-control__body"><small role="status"><LoaderCircle size={14} className="spin" />正在读取剪辑状态</small></div></section>
    if (latestMultiSourceTask && latestMultiSourcePresentation) {
      const detail = latestMultiSourcePresentation.active
        ? (latestMultiSourceTask.errorMessage || '已送入剪辑，成片会显示在下方“多片合并成片”。')
        : latestMultiSourcePresentation.completed
          ? '成片已回传，请在下方“多片合并成片”预览或下载。'
          : latestMultiSourceTask.status === 'FAILED'
            ? '可在上方重新选择原片送剪。'
            : latestMultiSourceTask.status === 'CANCELED'
              ? '如有需要，可在上方重新选择原片送剪。'
              : '请刷新状态后核对；确认结束后再重新送剪。'
      return <section className="cutflow-control cutflow-control--empty" aria-label="剪辑成片"><div className="cutflow-control__toolbar"><h3>剪辑成片</h3></div><div className="cutflow-control__body"><p>{latestMultiSourcePresentation.label}</p><small>{detail}</small></div></section>
    }
    return <section className="cutflow-control cutflow-control--empty" aria-label="剪辑成片"><div className="cutflow-control__toolbar"><h3>剪辑成片</h3></div><div className="cutflow-control__body"><p>尚未剪辑</p><small>在上方选择“选择送剪”，再按当前顺序送入剪辑。</small></div></section>
  }
  if (!loaded && !reading && !current && !error && !revisionTasks.length) return null
  return <section className="cutflow-control" aria-label="剪辑成片">
    <div className="cutflow-control__toolbar">
      <h3>剪辑成片{outputCount ? ` · ${outputCount}` : ''}</h3>
      <button type="button" className="icon-button" title="刷新剪辑状态" aria-label="刷新剪辑状态" disabled={reading || busy} onClick={() => { setReading(true); setRefreshToken(value => value + 1) }}><RefreshCw size={15} className={reading ? 'spin' : undefined} /></button>
    </div>
    <div className="cutflow-control__body">
      {!loaded && !readError ? <span role="status">正在读取剪辑状态</span> : null}
      {allowSubmit && canWrite && loaded && siteRequired && !current && !siteBound && !siteProfileId ? <div data-card-interactive>
        <CutflowSiteSelect value={sourceSite} onChange={setSourceSite} disabled={busy} />
        <button type="button" className="compact-button" disabled={busy || !sourceSite} onClick={() => void bindSite()}>保存站点关联</button>
      </div> : null}
      {allowSubmit && canWrite && (!current || current.status === 'FAILED') ? <button className="primary-button generation-priced-button cutflow-submit" type="button" disabled={busy || !loaded || Boolean(readError) || uncertain || (siteRequired && !current && !siteBound && !siteProfileId)} onClick={() => void submit()}>
        {busy ? <LoaderCircle size={16} className="spin" /> : current ? <RefreshCw size={16} /> : <Scissors size={16} />}
        {busy ? '正在送入剪辑' : current ? '重试送入剪辑' : '送入剪辑'}
        <GenerationCostBadge category="cutflowPriceUsd" units={1} />
      </button> : null}
      {!canWrite && loaded && !current ? <small>当前账号仅可查看剪辑结果</small> : null}
      {current?.cutflowEditorUrl && current.editMode !== 'automatic' ? <a className="text-button" href={current.cutflowEditorUrl} target="_blank" rel="noreferrer"><ExternalLink size={16} />打开剪辑工作台</a> : null}
      {latestMultiSourcePresentation ? <small role="status">最近一次合并剪辑：{latestMultiSourcePresentation.label}。成片显示在下方“多片合并成片”。</small> : null}
      {!outputCount && !latestMultiSourcePresentation ? <small>{current || revisionTasks.length ? '成片回传后可在这里预览和下载。' : '送入剪辑后，成片会显示在这里。'}</small> : null}
      {outputs.map((output, index) => <div className="variant-status cutflow-control__output" key={output.id}>
        <button type="button" className="variant-result-preview" aria-label={`播放剪辑成片 ${index + 1}`} disabled={mediaBusy !== null} onClick={event => void handleMedia(output, index, event.currentTarget)}>
          <CutflowVideoThumbnail key={output.mediaUrl} src={output.mediaUrl} alt={`剪辑成片 ${index + 1}封面`} />
          <span aria-hidden="true">{mediaBusy === `preview:${output.id}` ? <LoaderCircle className="spin" size={18} /> : <Play size={18} fill="currentColor" />}</span>
        </button>
        <div><strong>剪辑成片 {index + 1}</strong><small>{cutflowReviewLabel(output.reviewStatus)}{output.durationSeconds > 0 ? ` · ${Number(output.durationSeconds.toFixed(2))} 秒` : ''}</small></div>
        <div className="variant-status-actions cutflow-control__output-actions">
          <button type="button" className="compact-button" aria-label={`预览剪辑成片 ${index + 1}`} disabled={mediaBusy !== null} onClick={event => void handleMedia(output, index, event.currentTarget)}>{mediaBusy === `preview:${output.id}` ? <LoaderCircle size={16} className="spin" /> : <Play size={16} />}预览</button>
          <button type="button" className="compact-button" title={`下载剪辑成片 ${index + 1}`} aria-label={`下载剪辑成片 ${index + 1}`} disabled={mediaBusy !== null} onClick={() => void handleMedia(output, index)}>{mediaBusy === `download:${output.id}` ? <LoaderCircle size={16} className="spin" /> : <Download size={16} />}下载成片</button>
        </div>
      </div>)}
      {revisionTasks.length ? <div className="cutflow-revisions" aria-label="重新剪辑版本">
        <h4>重新剪辑版本</h4>
        {revisionTasks.map((revision, revisionIndex) => {
          const revisionLabel = `第 ${revisionTasks.length - revisionIndex} 次重剪`
          return <div className="cutflow-revision" key={revision.id}>
          <div className="cutflow-revision__status">
            <strong>{revisionLabel}</strong>
            <span>{revisionStatusLabel[revision.status] ?? '状态待确认'}</span>
          </div>
          {revision.errorMessage ? <small role="alert">{revision.errorMessage}</small> : null}
          {revision.outputs.map((output, index) => <div className="variant-status cutflow-control__output" key={output.id}>
            <button type="button" className="variant-result-preview" aria-label={`播放${revisionLabel}的成片 ${index + 1}`} disabled={mediaBusy !== null} onClick={event => void handleMedia(output, index, event.currentTarget, revisionLabel)}>
              <CutflowVideoThumbnail key={output.mediaUrl} src={output.mediaUrl} alt={`重新剪辑版本 ${revisionIndex + 1} 成片 ${index + 1} 封面`} />
              <span aria-hidden="true">{mediaBusy === `preview:${output.id}` ? <LoaderCircle className="spin" size={18} /> : <Play size={18} fill="currentColor" />}</span>
            </button>
            <div><strong>{revisionLabel}</strong><small>{cutflowReviewLabel(output.reviewStatus)}{output.durationSeconds > 0 ? ` · ${Number(output.durationSeconds.toFixed(2))} 秒` : ''}</small></div>
            <div className="variant-status-actions cutflow-control__output-actions">
              <button type="button" className="compact-button" disabled={mediaBusy !== null} onClick={event => void handleMedia(output, index, event.currentTarget, revisionLabel)}>{mediaBusy === `preview:${output.id}` ? <LoaderCircle size={16} className="spin" /> : <Play size={16} />}预览</button>
              <button type="button" className="compact-button" disabled={mediaBusy !== null} onClick={() => void handleMedia(output, index, undefined, revisionLabel)}>{mediaBusy === `download:${output.id}` ? <LoaderCircle size={16} className="spin" /> : <Download size={16} />}下载成片</button>
            </div>
          </div>)}
        </div>
        })}
      </div> : null}
      {readError || error || current?.errorMessage ? <small role="alert">{[readError, error, current?.errorMessage].filter(Boolean).join('；')}</small> : null}
      {cutflowRecoveryHint(current?.errorCode) ? <small>{cutflowRecoveryHint(current?.errorCode)}</small> : null}
      {current ? <CutflowDiagnostics key={current.id} handoffId={current.id} /> : null}
    </div>
    {preview ? <PortalVideoModal src={preview.url} title={preview.title} playbackErrorMessage="成片无法播放，请重新预览或下载后查看" onClose={() => setPreview(null)} /> : null}
  </section>
}
