// 本文件由 App.tsx 拆分生成（2026-09-28 前端架构拆分 v2）。
// 代码为逐字搬运，未经逻辑改写。
import { AlertTriangle, FileImage, Package, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { fetchArtifactObjectUrl, fetchAssetObjectUrl } from '../../api'
import { type Task } from '../../types'
import { useDismiss } from '../hooks/useDismiss'
import { useMediaObjectUrl } from '../hooks/useMediaObjectUrl'

export function AuthenticatedImage({ src, alt, eager = false }: { src: string; alt: string; eager?: boolean }) {
  // A new source owns new visibility/load/error state, even when revisiting a URL.
  return <AuthenticatedImageSource key={src} src={src} alt={alt} eager={eager} />
}

function AuthenticatedImageSource({ src, alt, eager }: { src: string; alt: string; eager: boolean }) {
  const placeholderRef = useRef<HTMLSpanElement>(null)
  const [nearViewport, setNearViewport] = useState(() => typeof IntersectionObserver === 'undefined')
  const enabled = eager || nearViewport
  const { url, error } = useMediaObjectUrl(src, fetchAssetObjectUrl, '图片不可用', enabled)
  useEffect(() => {
    if (enabled || !placeholderRef.current) return
    let active = true
    const observer = new IntersectionObserver((entries) => {
      if (active && entries.some((entry) => entry.isIntersecting)) {
        setNearViewport(true)
        observer.disconnect()
      }
    }, { rootMargin: '200px' })
    observer.observe(placeholderRef.current)
    return () => { active = false; observer.disconnect() }
  }, [enabled])
  if (error) return <span className="image-fallback"><FileImage size={20} /><small>图片不可用</small></span>
  return url ? <img src={url} alt={alt} loading={eager ? 'eager' : 'lazy'} /> : <span ref={placeholderRef} className="image-skeleton" aria-label="图片加载中" />
}

export function LocalImage({ file, alt }: { file: File; alt: string }) {
  const [preview, setPreview] = useState<{ file: File; src: string } | null>(null)
  useEffect(() => {
    let active = true
    const src = URL.createObjectURL(file)
    queueMicrotask(() => {
      if (active) setPreview({ file, src })
    })
    return () => {
      active = false
      URL.revokeObjectURL(src)
    }
  }, [file])
  return preview?.file === file
    ? <img src={preview.src} alt={alt} draggable={false} />
    : <span className="image-skeleton" aria-label="图片加载中" />
}

export function ImagePreview({ file, label, onClose }: { file: File; label: string; onClose: () => void }) {
  const dialogRef = useRef<HTMLElement>(null)
  useDismiss(true, onClose, dialogRef)
  return createPortal(<div className="modal-backdrop image-preview-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <section ref={dialogRef} className="image-preview-modal" role="dialog" aria-modal="true" aria-labelledby="image-preview-title">
      <h2 id="image-preview-title" className="sr-only">{label}：{file.name}</h2>
      <div className="image-preview-stage">
        <LocalImage file={file} alt={`${label}大图：${file.name}`} />
      </div>
      <button type="button" className="icon-button image-preview-close" onClick={onClose} aria-label="关闭图片预览" title="关闭预览"><X size={19} /></button>
    </section>
  </div>, document.body)
}

export function TaskThumb({ task, large = false }: { task: Task; large?: boolean }) {
  const asset = [...task.assets].sort((a, b) => a.order - b.order)[0]
  return <span className={`task-thumb ${large ? 'task-thumb--large' : ''}`}>
    {asset ? <AuthenticatedImage src={asset.thumbnailUrl} alt={`${task.title} ${task.workflowKind === 'DEEP_VIDEO' ? '参考视频' : '产品图'}`} eager={large} /> : <Package size={20} />}
  </span>
}

/** 全站共用视频弹窗，供原片和剪辑成片复用。 */
export function VideoModal({ src, title, loadError, playbackErrorMessage = '视频无法播放，请重新打开或下载后查看', onClose, onPlaying, aspectRatioHint }: { src?: string; title: string; loadError?: string; playbackErrorMessage?: string; onClose: () => void; onPlaying?: () => void; aspectRatioHint?: number | null }) {
  const [playbackError, setPlaybackError] = useState<{ src: string | undefined; message: string } | null>(null)
  const [mediaSize, setMediaSize] = useState<{ src: string; ratio: number } | null>(null)
  if (playbackError && playbackError.src !== src) setPlaybackError(null)
  const dialogRef = useRef<HTMLElement>(null)
  useDismiss(true, onClose, dialogRef)
  const failure = loadError || (playbackError?.src === src ? playbackError?.message : '')
  const hasMedia = Boolean(src && !failure)
  const mediaRatio = src && mediaSize?.src === src ? mediaSize.ratio
    : aspectRatioHint && Number.isFinite(aspectRatioHint) && aspectRatioHint > 0 ? aspectRatioHint : null
  const fittedWidth = mediaRatio ? `min(calc((100dvh - 176px) * ${mediaRatio}), calc(100vw - 40px), 1080px)` : undefined
  return <div className="modal-backdrop video-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <section ref={dialogRef} className="video-modal video-modal--content-fit" role="dialog" aria-modal="true" aria-labelledby="video-title"
      style={{ width: fittedWidth ?? 'min(340px, calc(100vw - 32px))', minWidth: 0, maxWidth: 'calc(100vw - 32px)', maxHeight: 'calc(100dvh - 24px)', gridTemplateColumns: 'minmax(0, 1fr)' }}>
      <header><div><span>视频预览</span><h2 id="video-title">{title}</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭预览"><X size={19} /></button></header>
      {/* Keep main's native-ratio preview even in classic builds without its CSS. */}
      <div className={`video-stage video-stage--content-fit${hasMedia ? ' has-media' : ''}`}
        style={{ width: fittedWidth ?? 'min(340px, calc(100vw - 32px))', height: mediaRatio ? 'auto' : 'min(60dvh, 420px)', minWidth: 0, maxWidth: '100%', maxHeight: 'calc(100dvh - 166px)', aspectRatio: mediaRatio ? String(mediaRatio) : 'auto', background: hasMedia ? '#10151d' : 'var(--surface-soft)', color: 'var(--primary)' }}>
        {failure
          ? <div className="video-error" style={{ maxWidth: 310, padding: 24, color: 'var(--danger)', textAlign: 'center' }}><AlertTriangle size={24} /><strong>{failure}</strong></div>
          : src
            ? <video key={src} src={src} controls tabIndex={0} autoPlay playsInline onPlaying={onPlaying}
              onLoadedMetadata={(event) => { const { videoWidth, videoHeight } = event.currentTarget; if (videoWidth > 0 && videoHeight > 0) setMediaSize({ src, ratio: videoWidth / videoHeight }) }}
              onError={() => setPlaybackError({ src, message: playbackErrorMessage })}
              style={{ position: 'static', inset: 'auto', display: 'block', width: '100%', height: '100%', maxWidth: '100%', maxHeight: '100%', aspectRatio: 'auto', objectFit: 'contain', background: '#10151d' }} />
            : <span role="status" aria-label="视频加载中">视频加载中，请稍候</span>}
      </div>
    </section>
  </div>
}

/** CUT media uses the exact same video dialog as generated clips, with authenticated blob loading. */
/** Keep the player above task drawers and inspection panels, never inside clipped content. */
export function PortalVideoModal(props: Parameters<typeof VideoModal>[0]) {
  return createPortal(<VideoModal {...props} />, document.body)
}

export function RemoteVideoPreview({ mediaUrl, title, onClose, onPlaying }: { mediaUrl: string; title: string; onClose: () => void; onPlaying?: () => void }) {
  const { url, error } = useMediaObjectUrl(mediaUrl, fetchAssetObjectUrl, '剪辑成片加载失败')
  return <PortalVideoModal src={url} title={title} loadError={error} onClose={onClose} onPlaying={onPlaying} />
}

export function VideoPreview({ artifactId, title, onClose }: { artifactId: string; title: string; onClose: () => void }) {
  const { url, error } = useMediaObjectUrl(artifactId, fetchArtifactObjectUrl, '视频加载失败')
  return <PortalVideoModal src={url} title={title} loadError={error} onClose={onClose} />
}
