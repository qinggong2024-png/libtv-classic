import { FileVideo, LoaderCircle } from 'lucide-react'
import { useState } from 'react'

// The same-origin media endpoint keeps session and ownership checks in place.
// A paused frame uses the browser's range loading instead of fetching a full blob.
export function CutflowVideoThumbnail({ src, alt }: { src: string; alt: string }) {
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading')
  return <div className="cutflow-video-thumbnail" role="img" aria-label={state === 'failed' ? `${alt}，缩略图不可用` : alt}>
    <video src={src} preload="metadata" muted playsInline aria-hidden="true" tabIndex={-1}
      onLoadedMetadata={(event) => {
        const video = event.currentTarget
        if (Number.isFinite(video.duration) && video.duration > 0) video.currentTime = Math.min(0.1, video.duration / 2)
      }}
      onLoadedData={() => setState('ready')}
      onError={() => setState('failed')}
    />
    {state !== 'ready' ? <div className="cutflow-video-thumbnail__placeholder" aria-hidden="true">
      {state === 'failed' ? <FileVideo size={20} /> : <LoaderCircle className="spin" size={20} />}
    </div> : null}
  </div>
}
