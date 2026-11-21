import { AlertTriangle, LoaderCircle, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { fetchAssetObjectUrl } from '../../api'
import { errorMessage } from '../../errorPresentation'
import type { Asset } from '../../types'
import { useDismiss } from '../hooks/useDismiss'

export function TaskAssetPreview({ asset, label, onClose }: { asset: Asset; label: string; onClose: () => void }) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const dialogRef = useRef<HTMLElement>(null)
  const isVideo = asset.role === 'REFERENCE_VIDEO'
  useDismiss(true, onClose, dialogRef)
  useEffect(() => {
    let active = true
    let loaded = ''
    void fetchAssetObjectUrl(`/api/v1/assets/${asset.id}/content`).then((value) => {
      if (!active) { URL.revokeObjectURL(value); return }
      loaded = value
      setUrl(value)
    }).catch((cause) => { if (active) setError(errorMessage(cause, '素材加载失败，请稍后重试')) })
    return () => { active = false; if (loaded) URL.revokeObjectURL(loaded) }
  }, [asset.id])
  return createPortal(<div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <section ref={dialogRef} className="video-modal video-modal--content-fit" role="dialog" aria-modal="true" aria-labelledby="task-asset-preview-title">
      <header><div><span>{label} · {isVideo ? '视频预览' : '图片预览'}</span><h2 id="task-asset-preview-title">{asset.originalFilename}</h2></div><button type="button" className="icon-button" onClick={onClose} aria-label="关闭素材预览"><X size={19} /></button></header>
      <div className={`video-stage video-stage--content-fit task-asset-preview-stage${url && !error ? ' has-media' : ''}`}>
        {error ? <div className="video-error" role="alert"><AlertTriangle size={24} /><strong>{error}</strong></div>
          : url ? isVideo
            ? <video src={url} controls autoPlay playsInline tabIndex={0} onError={() => setError('视频无法播放，文件可能已损坏或浏览器不支持此格式。')} />
            : <img src={url} alt={`${label}：${asset.originalFilename}`} onError={() => setError('图片无法显示，请稍后重试。')} />
            : <span role="status" aria-label="素材加载中"><LoaderCircle className="spin" size={28} /></span>}
      </div>
    </section>
  </div>, document.body)
}
