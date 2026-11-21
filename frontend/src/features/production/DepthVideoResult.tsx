import { Download, LoaderCircle, Play, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { saveArtifact } from '../../api'
import { errorMessage } from '../../errorPresentation'
import { VideoPreview } from '../../shared/components/Media'
import { VideoThumbnail } from '../../shared/components/VideoThumbnail'

export function DepthVideoResult({ result, title, onReuse }: {
  result: { downloadId: string }
  title: string
  onReuse?: () => void
}) {
  const [preview, setPreview] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const download = async () => {
    setBusy(true)
    setError('')
    try { await saveArtifact(result.downloadId, `${title}-depth.mp4`) }
    catch (cause) { setError(errorMessage(cause, '深度视频下载失败，请稍后重试')) }
    finally { setBusy(false) }
  }
  return <section className="variant-status-list" aria-label="深度视频结果">
    <div className="variant-status">
      <button className="variant-result-preview" onClick={() => setPreview(true)} aria-label="播放深度视频">
        <VideoThumbnail artifactId={result.downloadId} alt="深度视频封面" />
        <span aria-hidden="true"><Play size={18} fill="currentColor" /></span>
      </button>
      <div><strong>深度视频</strong><small>深度视频已保存，可直接用于替换或下载复用。</small>
        {error ? <small role="alert" className="variant-action-error">{error}</small> : null}
      </div>
      <div className="variant-status-actions">
        {onReuse ? <button className="compact-button" onClick={onReuse}><RefreshCw size={15} />用于替换</button> : null}
        <button className="compact-button" onClick={() => setPreview(true)}><Play size={15} />预览</button>
        <button className="compact-button" disabled={busy} onClick={() => void download()}>
          {busy ? <LoaderCircle className="spin" size={15} /> : <Download size={15} />}下载深度视频
        </button>
      </div>
    </div>
    {preview ? <VideoPreview artifactId={result.downloadId} title={`${title} · 深度视频`} onClose={() => setPreview(false)} /> : null}
  </section>
}
