import { Download, LoaderCircle, Play, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { fetchArtifactObjectUrl, fetchAssetObjectUrl, markMultiCutflowOutputViewed, saveArtifact, saveAsset } from '../../api'
import { VideoThumbnail } from '../../shared/components/VideoThumbnail'
import { useDismiss } from '../../shared/hooks/useDismiss'
import { useMediaObjectUrl } from '../../shared/hooks/useMediaObjectUrl'
import type { CutCoverStatus, PlayableMedia } from './taskCenterMedia'

function LazyFinalFrame({ src }: { src: string }) {
  const rootRef = useRef<HTMLSpanElement>(null)
  const [near, setNear] = useState(() => typeof IntersectionObserver === 'undefined')
  useEffect(() => {
    if (near || !rootRef.current) return
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setNear(true); observer.disconnect() }
    }, { rootMargin: '200px' })
    observer.observe(rootRef.current)
    return () => observer.disconnect()
  }, [near])
  return <span ref={rootRef} className="allwe-media-cover__frame">
    {near ? <video src={src} preload="metadata" muted playsInline aria-hidden="true" /> : null}
  </span>
}

export function TaskMediaCover({ media, className = '', cutStatus }: { media: PlayableMedia; className?: string; cutStatus?: CutCoverStatus | null }) {
  return <span className={'allwe-media-cover ' + className + (cutStatus ? ' has-cut-status' : '') + (cutStatus?.busy ? ' is-cut-busy' : '')}>
    {media.kind === 'final'
      ? <LazyFinalFrame src={media.mediaUrl} />
      : <VideoThumbnail artifactId={media.artifactId} alt={media.label} />}
    <span className="allwe-media-cover__play" aria-hidden="true"><Play size={18} fill="currentColor" /></span>
    {cutStatus ? <span className="allwe-media-cover__cut-status">
      {cutStatus.busy ? <LoaderCircle className="spin" size={18} aria-hidden="true" /> : null}
      <span>{cutStatus.label}</span>
    </span> : null}
    <span className="allwe-media-cover__label">{media.kind === 'final' ? cutStatus?.busy ? '上一版成片' : '成片' : media.label}</span>
  </span>
}

function PreviewVideo({ media, onPlaying, onRatio }: { media: PlayableMedia; onPlaying: () => void; onRatio: (ratio: number) => void }) {
  const source = media.kind === 'final' ? media.mediaUrl : media.artifactId
  const load = media.kind === 'final' ? fetchAssetObjectUrl : fetchArtifactObjectUrl
  const { url, error } = useMediaObjectUrl(source, load, '视频暂时无法播放')
  return <div className="allwe-quick-video__stage">
    {error
      ? <div className="allwe-quick-video__message" role="alert">{error}</div>
      : url ? <video key={url} src={url} controls autoPlay playsInline onPlaying={onPlaying} onLoadedMetadata={event => {
        const { videoWidth, videoHeight } = event.currentTarget
        if (videoWidth > 0 && videoHeight > 0) onRatio(videoWidth / videoHeight)
      }} /> : <LoaderCircle className="spin" size={22} aria-label="视频加载中" />}
  </div>
}

export function TaskQuickPreview({ media, startKey, onClose, onViewed }: {
  media: PlayableMedia[]
  startKey: string
  onClose: () => void
  onViewed?: () => void
}) {
  const [selectedKey, setSelectedKey] = useState(startKey)
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState('')
  const selected = media.find(item => item.key === selectedKey) ?? media[0]
  const viewedRef = useRef(new Set<string>())
  const onPlaying = () => {
    if (selected?.kind !== 'final' || selected.viewedAt || viewedRef.current.has(selected.key)) return
    viewedRef.current.add(selected.key)
    void markMultiCutflowOutputViewed(selected.batchId, selected.outputId)
      .then(() => onViewed?.())
      .catch(() => viewedRef.current.delete(selected.key))
  }
  const download = async () => {
    if (!selected || downloading) return
    setDownloading(true)
    setDownloadError('')
    try {
      if (selected.kind === 'final') {
        await saveAsset(selected.mediaUrl + (selected.mediaUrl.includes('?') ? '&' : '?') + 'download=true', 'ALLWE-成片.mp4')
      } else {
        await saveArtifact(selected.artifactId, 'ALLWE-' + selected.label.replace(/\s+/g, '-') + '.mp4')
      }
    } catch {
      setDownloadError('下载失败，请重试')
    } finally {
      setDownloading(false)
    }
  }
  // The modal uses the existing app-level focus / Escape handling convention.
  return <TaskQuickPreviewPortal selected={selected} media={media} onClose={onClose}
    onSelect={key => { setSelectedKey(key); setDownloadError('') }}
    onDownload={() => void download()} onPlaying={onPlaying} downloading={downloading} downloadError={downloadError} />
}

function TaskQuickPreviewPortal({ selected, media, onClose, onSelect, onDownload, onPlaying, downloading, downloadError }: {
  selected: PlayableMedia | undefined
  media: PlayableMedia[]
  onClose: () => void
  onSelect: (key: string) => void
  onDownload: () => void
  onPlaying: () => void
  downloading: boolean
  downloadError: string
}) {
  const dialogRef = useRef<HTMLElement>(null)
  useDismiss(true, onClose, dialogRef)
  const [dimensions, setDimensions] = useState<{ key: string; ratio: number } | null>(null)
  if (!selected) return null
  const ratio = dimensions?.key === selected.key ? dimensions.ratio : 9 / 16
  return createPortal(<div className="allwe-quick-video__backdrop" onMouseDown={event => {
    if (event.target === event.currentTarget) onClose()
  }}>
    <section ref={dialogRef} className="allwe-quick-video" style={{ width: `min(391px, calc(100vw - 40px), calc(max(0px, min(80.5dvh, 678.5px, 100dvh - 120px)) * ${ratio}))`, '--allwe-video-ratio': ratio } as React.CSSProperties} role="dialog" aria-modal="true" aria-label="视频预览">
      <button type="button" className="allwe-quick-video__close" onClick={onClose} aria-label="关闭预览"><X size={18} /></button>
      <PreviewVideo key={selected.key} media={selected} onPlaying={onPlaying} onRatio={ratio => setDimensions({ key: selected.key, ratio })} />
      <div className="allwe-quick-video__footer">
        <div className="allwe-quick-video__tabs" aria-label="选择视频">
          {media.map(item => <button key={item.key} type="button" aria-pressed={selected.key === item.key}
            className={selected.key === item.key ? 'is-active' : ''}
            onClick={() => onSelect(item.key)}>{item.label}</button>)}
        </div>
        <button type="button" className="allwe-quick-video__download" disabled={downloading} onClick={onDownload}>
          {downloading ? <LoaderCircle className="spin" size={15} /> : <Download size={15} />}
          {selected.kind === 'final' ? '下载成片' : '下载原片'}
        </button>
      </div>
      {downloadError ? <p className="allwe-quick-video__error" role="alert">{downloadError}</p> : null}
    </section>
  </div>, document.body)
}
