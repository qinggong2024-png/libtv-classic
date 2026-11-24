import { errorMessage, plainText } from '../../errorPresentation'
import { ArrowRight, Film, Images, LoaderCircle, Plus, RefreshCw, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { ApiError, createDeepVideoTask, getDeepVideoStatus, getTask, querySubmissionRecovery, uploadReferenceVideo, uploadStoryboard, type DeepVideoMode, type DeepVideoStatus } from '../../api'
import { IMAGE_FILE_ACCEPT, isSupportedImageFile } from '../../imageFiles'
import { readJsonStorage, removeStorage, writeJsonStorage } from '../../safeStorage'
import { createUuid } from '../../uuid'
import { type Task } from '../../types'
import { LocalImage } from '../../shared/components/Media'
import { GenerationCostBadge } from '../../shared/components/GenerationCostBadge'
import { ImageHistoryPicker } from './ImageHistoryPicker'
import type { DeepVideoSeed, Material } from './productionTypes'
import { referenceVideoError, type ReferenceVideoMetadata } from '../../deepVideo'
import { ImageMentionInput } from './ImageMentionInput'
import { imageMentionError, removeImageMentions } from './imageMentions'
import { DepthVideoHistoryPicker } from './DepthVideoHistoryPicker'
import { AccountSiteField } from './CutflowSiteSelect'
import { useAccountSiteSelection } from '../../useAccountSiteSelection'
import { GenerationAdvancedSettings } from '../../shared/components/GenerationAdvancedSettings'
import './creationRemix.css'

const DEFAULT_PROMPT = '保持参考视频的镜头顺序、人物动作、运镜和节奏，用目标参考图中的人物与服装替换原主体。'

type Identity = { clientTaskId: string; idempotencyKey: string }

export function VideoRecreationPanel({ actorId, imageDraftScope = actorId, active, onCreated, seed }: { actorId: string | null; imageDraftScope?: string | null; active: boolean; onCreated: (task: Task, open?: boolean) => void; seed?: DeepVideoSeed }) {
  const [path, setPath] = useState<'quick' | 'deep'>(seed?.referenceVideoType === 'depth' || seed?.mode === 'depth' ? 'deep' : 'quick')
  const [creativeBrief, setCreativeBrief] = useState('')
  const [referenceType, setReferenceType] = useState<'original' | 'depth'>(seed?.referenceVideoType ?? 'original')
  const [depthHistoryOpen, setDepthHistoryOpen] = useState(false)
  const [reference, setReference] = useState<File | null>(seed?.reference ?? null)
  const [metadata, setMetadata] = useState<ReferenceVideoMetadata | null>(null)
  const [metadataFailed, setMetadataFailed] = useState(false)
  const previewRef = useRef<HTMLVideoElement>(null)
  const [images, setImages] = useState<Material[]>(() => seed?.images.map((file) => ({ id: createUuid(), file })) ?? [])
  const [background, setBackground] = useState<Material | null>(() => seed?.background ? { id: createUuid(), file: seed.background } : null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [imagesDragging, setImagesDragging] = useState(false)
  const [videoDragging, setVideoDragging] = useState(false)
  const videoDragDepth = useRef(0)
  const imageInputRef = useRef<HTMLInputElement>(null)
  const backgroundInputRef = useRef<HTMLInputElement>(null)
  const imageDragDepth = useRef(0)
  const [mode, setMode] = useState<DeepVideoMode>(seed?.mode ?? 'recreate')
  const [prompt, setPrompt] = useState(seed?.prompt?.trim() ? seed.prompt : DEFAULT_PROMPT)
  const { choices, error: siteError, siteProfileId, setSiteProfileId, defaultProfileId: defaultSiteProfileId } = useAccountSiteSelection(seed?.siteProfileId)
  const [ratio, setRatio] = useState<'9:16' | '16:9'>(seed?.aspectRatio ?? '9:16')
  const [status, setStatus] = useState<DeepVideoStatus | null>(null)
  const [statusError, setStatusError] = useState('')
  const [statusRevision, setStatusRevision] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [stage, setStage] = useState('')
  const storageKey = `deep-video-pending:${actorId ?? 'anonymous'}`
  const [pending, setPending] = useState<Identity | null>(() => readJsonStorage('session', storageKey, null))
  const inputRef = useRef<HTMLInputElement>(null)
  const submittingRef = useRef(false)

  useEffect(() => {
    const video = previewRef.current
    if (!reference || !video) return
    const url = URL.createObjectURL(reference)
    video.src = url
    return () => {
      video.pause()
      video.removeAttribute('src')
      video.load()
      URL.revokeObjectURL(url)
    }
  }, [reference])

  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    void getDeepVideoStatus(controller.signal).then((value) => {
      if (!controller.signal.aborted) { setStatus(value); setStatusError('') }
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) { setStatus(null); setStatusError(errorMessage(cause, '无法读取通道状态')) }
    })
    return () => controller.abort()
  }, [active, statusRevision])

  function selectVideo(file: File | undefined) {
    if (!file || busy || pending) return
    setError('')
    setNotice('')
    if (!/\.(mp4|mov)$/i.test(file.name) || file.size <= 0 || file.size > 200 * 1024 * 1024) {
      setError('请选择不超过 200 MB 的 MP4 或 MOV 视频')
      return
    }
    setMetadata(null)
    setMetadataFailed(false)
    setReference(file)
  }

  function addImages(files: File[]) {
    addMaterials(files.map((file) => ({ id: createUuid(), file })))
  }

  function addMaterials(selected: Material[]) {
    if (busy || pending || !selected.length) return
    const maximum = background ? 4 : 5
    if (selected.length + images.length > maximum || selected.some(({ file }) => !isSupportedImageFile(file) || file.size <= 0 || file.size > 10 * 1024 * 1024)) {
      setError(`最多 ${maximum} 张目标 JPG、PNG、WebP 图片，每张不超过 10 MB，且不能为空文件`)
      return
    }
    setImages((current) => [...current, ...selected])
    setError('')
    setNotice('')
  }

  function selectBackground(file: File | undefined) {
    if (!file || busy || pending) return
    if (images.length >= 5) {
      setError('已有 5 张目标参考图时无法再添加背景图，请先移除一张目标参考图')
      return
    }
    if (!isSupportedImageFile(file) || file.size <= 0 || file.size > 10 * 1024 * 1024) {
      setError('背景参考图必须是 JPG、PNG 或 WebP，且不超过 10 MB')
      return
    }
    setBackground({ id: createUuid(), file })
    setError('')
    setNotice('')
  }

  async function recover() {
    if (!pending || submittingRef.current) return
    submittingRef.current = true
    setBusy(true)
    try {
      const result = await querySubmissionRecovery({ kind: 'video', clientId: pending.clientTaskId, idempotencyKey: pending.idempotencyKey })
      if (result.state === 'submitted' && result.resourceId) {
        const task = await getTask(result.resourceId)
        removeStorage('session', storageKey)
        setPending(null)
        setReference(null)
        setImages([])
        setBackground(null)
        setPrompt(DEFAULT_PROMPT)
      setCreativeBrief('')
        setError('')
        setNotice('已找回原任务，请在任务记录查看进度。')
        onCreated(task, false)
      } else setError('原提交尚未确认，请稍后核对任务状态或联系管理员；请勿重复提交。')
    } catch (cause) { setError(errorMessage(cause, '核对失败')) }
    finally { submittingRef.current = false; setBusy(false) }
  }

  async function submit() {
    if (!siteProfileId || !reference || submittingRef.current || pending || !status?.enabled || metadataFailed || referenceVideoError(mode, metadata) || (mode === 'recreate' && (!images.length || !prompt.trim() || effectivePrompt.length > 2800 || imageMentionError(effectivePrompt, images.length)))) return
    submittingRef.current = true
    setBusy(true)
    setError('')
    setNotice('')
    let identity: Identity | null = null
    try {
      setStage('正在上传参考视频')
      const video = await uploadReferenceVideo(reference)
      if (mode === 'recreate' && (video.durationSeconds < 4 || video.durationSeconds > 15)) throw new Error('人物与商品替换需要 4–15 秒视频，请先裁剪后上传。')
      setStage('正在上传目标素材')
      const uploads = mode === 'recreate' ? await Promise.all(images.map(({ file, sourceImageOutputId }) => uploadStoryboard(file, undefined, sourceImageOutputId))) : []
      const backgroundUpload = mode === 'recreate' && background ? await uploadStoryboard(background.file) : null
      const nextIdentity = { clientTaskId: createUuid(), idempotencyKey: createUuid() }
      if (!writeJsonStorage('session', storageKey, nextIdentity)) throw new Error('浏览器无法保存提交记录，请允许站点存储后重试。')
      identity = nextIdentity
      setPending(identity)
      setStage('正在创建任务')
      const task = await createDeepVideoTask({
        clientTaskId: identity.clientTaskId, title: `深度视频 · ${reference.name}`.slice(0, 160),
        referenceVideoUploadId: video.id, referenceVideoType: mode === 'recreate' ? referenceType : 'original', mode, subjectImages: uploads.map((upload, i) => ({ uploadId: upload.id, order: i + 1 })),
        backgroundImageUploadId: backgroundUpload?.id,
        prompt: mode === 'recreate' ? effectivePrompt : '', aspectRatio: ratio, paidSubmissionConsent: true,
        siteProfileId: siteProfileId || undefined,
      }, identity.idempotencyKey)
      removeStorage('session', storageKey)
      setPending(null)
      setReference(null)
      setImages([])
      setBackground(null)
      setPrompt(DEFAULT_PROMPT)
      setCreativeBrief('')
      setNotice(task.phase === 'ACTION_REQUIRED' ? '任务已保存，通道或预算限制需要处理，请查看任务记录。' : '任务已提交，可继续制作；进度和结果见任务记录。')
      onCreated(task, false)
    } catch (cause) {
      const definitive = cause instanceof ApiError && [400, 401, 403, 404, 413, 422].includes(cause.status)
      if (identity && !definitive) setError('提交结果暂未确认，请核对原任务状态，避免重复扣费。')
      else {
        removeStorage('session', storageKey)
        setPending(null)
        setError(errorMessage(cause, '提交失败'))
      }
    } finally { submittingRef.current = false; setBusy(false) }
  }

  const effectivePrompt = [prompt.trim(), creativeBrief.trim()].filter(Boolean).join('\n')
  const locked = busy || Boolean(pending)
  const mentionError = mode === 'recreate' ? (effectivePrompt.length > 2800 ? '补充要求与复刻要求合计不能超过 2800 字，请适当删减。' : imageMentionError(effectivePrompt, images.length)) : ''
  const videoError = metadataFailed ? '浏览器无法解码此视频，请更换有效的 MP4 或 MOV 文件。' : referenceVideoError(mode, metadata)
  const ready = Boolean(siteProfileId && reference && !videoError && !mentionError && status?.enabled && !locked && (mode === 'depth' || (images.length && prompt.trim())))
  return <section className="video-recreation" aria-label="爆款视频复刻配置">
    <div className="video-recreation-body recreation-workbench">
      <div className="recreation-materials">
        <h2>添加复刻素材</h2>
        <div className="video-reference-column">
          <div className="deep-video-image-heading"><strong>{referenceType === 'depth' ? '深度视频' : '参考视频'}</strong></div>
        <section className={`video-reference-upload${reference ? ' has-video' : ''}${videoDragging && !locked ? ' is-dragging' : ''}`}
          onDragEnter={event => { event.preventDefault(); if (!locked && event.dataTransfer.types.includes('Files')) { videoDragDepth.current += 1; setVideoDragging(true) } }}
          onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = locked ? 'none' : 'copy' }}
          onDragLeave={event => { event.preventDefault(); videoDragDepth.current = Math.max(0, videoDragDepth.current - 1); if (!videoDragDepth.current) setVideoDragging(false) }}
          onDrop={event => { event.preventDefault(); videoDragDepth.current = 0; setVideoDragging(false); selectVideo(event.dataTransfer.files[0]) }}>
          <input ref={inputRef} aria-label={referenceType === 'depth' ? '深度视频' : '参考视频'} hidden type="file" accept=".mp4,.mov,video/mp4,video/quicktime" disabled={locked} onChange={(event) => { selectVideo(event.target.files?.[0]); event.target.value = '' }} />
          {reference ? <div className="video-reference-preview"><video ref={previewRef} controls playsInline preload="metadata" style={metadata && metadata.width > 0 && metadata.height > 0 ? { aspectRatio: String(metadata.width / metadata.height), width: "min(100%, calc(min(290px, 37vh) * " + metadata.width / metadata.height + "))", height: "auto" } : undefined} onLoadedMetadata={(event) => { const video = event.currentTarget; setMetadata({ duration: video.duration, width: video.videoWidth, height: video.videoHeight }); setMetadataFailed(false) }} onError={() => setMetadataFailed(true)} /><div className="recreation-video-meta"><div><strong title={reference.name}>{reference.name}</strong><small>{(reference.size / 1024 / 1024).toFixed(1)} MB{metadata ? ` · ${metadata.duration.toFixed(2)} 秒 · ${metadata.width} × ${metadata.height}` : ''}</small></div><button type="button" className="video-reference-replace" disabled={locked} onClick={() => inputRef.current?.click()}><RefreshCw size={15}/>更换</button><button type="button" className="recreation-icon-button" aria-label="移除参考视频" disabled={locked} onClick={() => { setReference(null); setMetadata(null); setMetadataFailed(false); setError(''); setNotice('') }}><X size={16}/></button></div></div> : <button type="button" className="video-reference-dropzone" disabled={locked} onClick={() => inputRef.current?.click()}><span className="video-reference-icon"><Plus size={24}/></span><strong>{videoDragging ? '松开即可上传' : '点击或拖入视频'}</strong><small>MP4 / MOV · 200 MB 以内 · {mode === 'recreate' ? '4–15 秒' : '最长 10 分钟'}</small></button>}
        </section>
        </div>
      {mode === 'recreate' && referenceType === 'depth' ? <div className="video-source-help">
        <button type="button" className="text-button" disabled={locked} onClick={() => setDepthHistoryOpen(true)}><Film size={15} />从历史结果选择</button>

      </div> : null}
        {mode === 'recreate' ? <>
            <section className={`deep-video-image-upload${imagesDragging && !locked ? ' is-dragging' : ''}`} aria-label="目标人物 / 商品参考图" aria-disabled={locked}
              onDragEnter={(event) => {
                event.preventDefault()
                if (locked || !event.dataTransfer.types.includes('Files')) return
                imageDragDepth.current += 1
                setImagesDragging(true)
              }}
              onDragOver={(event) => {
                event.preventDefault()
                event.dataTransfer.dropEffect = locked || images.length >= (background ? 4 : 5) ? 'none' : 'copy'
              }}
              onDragLeave={(event) => {
                event.preventDefault()
                imageDragDepth.current = Math.max(0, imageDragDepth.current - 1)
                if (!imageDragDepth.current) setImagesDragging(false)
              }}
              onDrop={(event) => {
                event.preventDefault()
                imageDragDepth.current = 0
                setImagesDragging(false)
                addImages(Array.from(event.dataTransfer.files))
              }}>
              <div className="deep-video-image-heading"><strong>目标人物 / 商品图 <span className="recreation-count" aria-live="polite">{images.length}/{background ? 4 : 5}</span></strong><button type="button" className="text-button deep-video-history-button" disabled={locked || images.length >= (background ? 4 : 5) || !imageDraftScope} onClick={() => setHistoryOpen(true)}><Images size={15}/>生图记录</button></div>
              <input ref={imageInputRef} aria-label="目标参考图" hidden type="file" multiple accept={IMAGE_FILE_ACCEPT} disabled={locked || images.length >= (background ? 4 : 5)} onChange={(event) => {
                addImages(Array.from(event.target.files ?? []))
                event.target.value = ''
              }} />
              <div className="recreation-target-strip">{images.length ? <ol className="deep-video-images" aria-label="已添加的目标参考图">{images.map(({ id, file }, i) => <li key={id} title={`@Image ${i + 1} · ${file.name}`}><LocalImage file={file} alt={`参考图 ${i + 1}：${file.name}`} /><span>@Image {i + 1}</span><button type="button" className="material-remove" aria-label={`移除参考图 ${i + 1}`} disabled={locked} onClick={() => { setPrompt((current) => removeImageMentions(current, i)); setCreativeBrief((current) => removeImageMentions(current, i)); setImages((current) => current.filter((_, index) => index !== i)) }}><X size={15} /></button></li>)}</ol> : null}{images.length < (background ? 4 : 5) ? <button type="button" className="deep-video-image-dropzone" disabled={locked || images.length >= (background ? 4 : 5)} onClick={() => imageInputRef.current?.click()}>
                <span className="recreation-add-icon"><Plus size={21}/></span>
                <span><strong>{images.length >= (background ? 4 : 5) ? `已添加 ${background ? 4 : 5} 张参考图` : imagesDragging ? '松开即可添加参考图' : images.length ? '添加' : '点击或拖入'}</strong><small>{images.length >= (background ? 4 : 5) ? '移除一张后可继续添加' : `1–${background ? 4 : 5} 张 JPG、PNG、WebP · 每张最多 10 MB`}</small></span>
              </button> : null}</div>
            </section>
          <section className="deep-video-background-upload" aria-label="背景参考图">
            <div className="deep-video-image-heading"><strong>背景参考图 <span className="recreation-optional">选填</span></strong><span className="recreation-count">{background ? 1 : 0}/1</span></div>
            <input ref={backgroundInputRef} aria-label="背景参考图" hidden type="file" accept={IMAGE_FILE_ACCEPT} disabled={locked} onChange={(event) => { selectBackground(event.target.files?.[0]); event.target.value = '' }} />
            <div className="deep-video-background-control">
              {background ? <div className="deep-video-background-preview"><LocalImage file={background.file} alt={`背景参考图：${background.file.name}`} /><span title={background.file.name}>{background.file.name}</span><button type="button" className="material-remove" aria-label="移除背景参考图" disabled={locked} onClick={() => setBackground(null)}><X size={15} /></button></div> : <button type="button" className="secondary-button recreation-background-add" title="不添加时使用模特参考图的原有背景" disabled={locked} onClick={() => backgroundInputRef.current?.click()}><Plus size={16}/>添加背景</button>}

            </div>
          </section>
        </> : null}
      </div>
      <div className="recreation-config">
        <h2>复刻配置</h2>
      <fieldset className="recreation-paths" disabled={locked}><legend>复刻方式</legend><div className="recreation-segment" data-active={path === 'deep' ? 'right' : 'left'}><span className="recreation-segment-thumb" aria-hidden="true"/>
        <label><input type="radio" name="recreation-path" checked={path === 'quick'} onChange={() => { setPath('quick'); setMode('recreate'); if (referenceType !== 'original') { setReference(null); setMetadata(null); setMetadataFailed(false) }; setReferenceType('original'); setError('') }} /><span><strong>一键复刻</strong></span></label>
        <label><input type="radio" name="recreation-path" checked={path === 'deep'} onChange={() => { setPath('deep'); setMode('depth'); if (referenceType !== 'original') { setReference(null); setMetadata(null); setMetadataFailed(false) }; setReferenceType('original'); setError('') }} /><span><strong>深度提取与复刻</strong></span></label>
      </div></fieldset>
      {path === 'deep' ? <div className="recreation-steps" data-active={mode === 'recreate' ? 'right' : 'left'} role="group" aria-label="深度操作步骤"><span className="recreation-segment-thumb" aria-hidden="true"/>
        <button type="button" aria-pressed={mode === 'depth'} disabled={locked} onClick={() => { if (referenceType !== 'original') { setReference(null); setMetadata(null); setMetadataFailed(false) }; setMode('depth'); setReferenceType('original'); setError('') }}><span>1</span>提取深度视频</button>
        <button type="button" aria-pressed={mode === 'recreate'} disabled={locked} onClick={() => { if (referenceType !== 'depth') { setReference(null); setMetadata(null); setMetadataFailed(false) }; setMode('recreate'); setReferenceType('depth'); setError('') }}><span>2</span>用深度视频复刻</button>
      </div> : null}
      <AccountSiteField compact value={siteProfileId} onChange={setSiteProfileId} choices={choices} error={siteError} disabled={locked} />
      <GenerationAdvancedSettings alwaysOpen summary={mode === 'recreate' ? '背景参考图、输出画幅与复刻要求' : '提取模型与输出说明'} customized={mode === 'recreate' && (Boolean(background) || ratio !== '9:16' || siteProfileId !== defaultSiteProfileId || prompt !== DEFAULT_PROMPT)} disabled={locked} onReset={() => { setBackground(null); setRatio('9:16'); setSiteProfileId(defaultSiteProfileId); setPrompt(DEFAULT_PROMPT) }}>
        <p className="generation-model-note">{mode === 'recreate' ? `生成模型：${status?.recreateModel || 'Seedance 2.0'} · 输出 720p` : '提取模型：Depth Anything · 输出沿用原片时长与尺寸'}</p>
        {mode === 'recreate' ? <>
          <div className="video-recreation-options"><label className="video-recreation-field"><span>输出画幅</span><select value={ratio} disabled={locked} onChange={(event) => setRatio(event.target.value as typeof ratio)}><option value="9:16">竖屏 9:16 · 720 × 1280</option><option value="16:9">横屏 16:9 · 1280 × 720</option></select></label></div>
          <ImageMentionInput label="复刻要求" value={prompt} placeholder="输入 @ 引用图片" images={images} disabled={locked} onChange={setPrompt} />
        </> : null}
      </GenerationAdvancedSettings>
        {mode === 'recreate' ? <>
            <ImageMentionInput label="补充要求" placeholder="选填 · 输入 @ 引用图片" maxLength={600} value={creativeBrief} images={images} disabled={locked} onChange={setCreativeBrief} />
            {mentionError ? <p className="inline-alert inline-alert--danger" role="alert">{mentionError}</p> : null}
        </> : null}
      {mode === 'recreate' && !prompt.trim() ? <p className="inline-alert" role="status">复刻要求为空，请填写，或恢复推荐设置。</p> : null}
      {reference && videoError ? <p className="inline-alert" role="status">{videoError}</p> : null}
      {!status?.enabled ? <p className="inline-alert" role="status">{statusError || plainText(status?.reason, '正在读取 WaveSpeed 服务状态…')}</p> : null}
      {!status?.enabled ? <button type="button" className="compact-button" disabled={locked} onClick={() => setStatusRevision((value) => value + 1)}><RefreshCw size={15} />重新检查通道</button> : null}
      {error ? <p className="inline-alert inline-alert--danger" role="alert">{error}</p> : null}
      {notice ? <p className="inline-alert" role="status">{notice}</p> : null}
      {pending ? <button type="button" className="secondary-button" disabled={busy} onClick={() => void recover()}>核对原任务状态</button> : null}
    <footer className="video-recreation-footer" role="group" aria-label="生成操作"><span><Film size={16} />{mode === 'depth' ? '预计 1 条深度视频 · 仅提取费用' : referenceType === 'original' ? '预计 1 条复刻视频 · 含提取与复刻费用' : '预计 1 条复刻视频 · 仅复刻费用'}</span><button type="button" className="primary-button primary-button--large generation-priced-button" disabled={!ready} onClick={() => void submit()}>{busy ? <LoaderCircle className="spin" size={17} /> : <RefreshCw size={17} />}<span>{busy ? stage || '正在核对' : mode === 'recreate' ? referenceType === 'depth' ? '开始深度复刻' : '一键复刻' : '提取深度视频'}</span><GenerationCostBadge additionalCategory={mode === 'recreate' && referenceType === 'original' ? 'depthVideoPricePerSecondUsd' : undefined} category={mode === 'recreate' ? 'recreateVideoPricePerSecondUsd' : 'depthVideoPricePerSecondUsd'} units={metadata && !metadataFailed && Number.isFinite(metadata.duration) ? Math.ceil(metadata.duration) : null} />{!busy ? <ArrowRight size={15} aria-hidden="true"/> : null}</button></footer>
      </div>
    </div>
    {depthHistoryOpen && active && mode === 'recreate' && referenceType === 'depth' ? <DepthVideoHistoryPicker onClose={() => setDepthHistoryOpen(false)} onSelect={(file) => { selectVideo(file); setDepthHistoryOpen(false) }} /> : null}
    {historyOpen && active && mode === 'recreate' ? <ImageHistoryPicker key={imageDraftScope} scope={imageDraftScope} remaining={(background ? 4 : 5) - images.length} addedOutputIds={images.flatMap((image) => image.sourceImageOutputId ? [image.sourceImageOutputId] : [])} onAdd={addMaterials} onClose={() => setHistoryOpen(false)} /> : null}

  </section>
}
