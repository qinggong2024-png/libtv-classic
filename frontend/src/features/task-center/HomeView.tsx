import { Images, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import type { SystemStatus, Task } from '../../types'
import { ProductionComposer } from '../production/ProductionComposer'
import type { RemixSeed } from '../production/productionTypes'
import { VideoRecreationPanel } from '../production/VideoRecreationPanel'

export function HomeView({ active, seed, system, actorId, imageDraftScope = actorId, onCreated }: { active: boolean; seed: RemixSeed | null; system: SystemStatus | null; actorId: string | null; imageDraftScope?: string | null; onCreated: (task: Task, open?: boolean) => void }) {
  const [modeSelection, setModeSelection] = useState<{ mode: 'image-to-video' | 'video-remix'; seedId: string | undefined }>({ mode: seed?.deepVideo ? 'video-remix' : 'image-to-video', seedId: seed?.id })
  const productionMode = modeSelection.seedId === seed?.id ? modeSelection.mode : seed?.deepVideo ? 'video-remix' : 'image-to-video'
  const setProductionMode = (mode: 'image-to-video' | 'video-remix') => setModeSelection({ mode, seedId: seed?.id })
  return <div className="home-view" hidden={!active}>
    <div className="home-title"><div><h1>视频制作</h1><p>从图片制作视频，或根据参考视频复刻动作与镜头。</p></div></div>
    <nav className="video-production-tabs" role="tablist" aria-label="视频制作方式" onKeyDown={(event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
      event.preventDefault()
      const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'))
      const current = tabs.indexOf(event.target as HTMLButtonElement)
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length
      tabs[next]?.focus()
      tabs[next]?.click()
    }}>
      <button id="image-to-video-tab" type="button" role="tab" tabIndex={productionMode === 'image-to-video' ? 0 : -1} aria-selected={productionMode === 'image-to-video'} aria-controls="image-to-video-panel" className={productionMode === 'image-to-video' ? 'is-active' : ''} onClick={() => setProductionMode('image-to-video')}>
        <Images size={17} /><span>图生视频</span>
      </button>
      <button id="video-remix-tab" type="button" role="tab" tabIndex={productionMode === 'video-remix' ? 0 : -1} aria-selected={productionMode === 'video-remix'} aria-controls="video-remix-panel" className={productionMode === 'video-remix' ? 'is-active' : ''} onClick={() => setProductionMode('video-remix')}>
        <RefreshCw size={17} /><span>视频复刻</span>
      </button>
    </nav>
    <div id="image-to-video-panel" role="tabpanel" aria-labelledby="image-to-video-tab" hidden={productionMode !== 'image-to-video'}>
      <div className="home-hero-grid home-hero-grid--full">
        <ProductionComposer key={`${imageDraftScope ?? actorId}:${seed?.id ?? 'new-production'}`} seed={seed?.deepVideo ? null : seed} onCreated={onCreated} system={system} actorId={actorId} />
      </div>
    </div>
    <div id="video-remix-panel" role="tabpanel" aria-labelledby="video-remix-tab" hidden={productionMode !== 'video-remix'}>
      <VideoRecreationPanel key={`${imageDraftScope ?? actorId}:${seed?.deepVideo ? seed.id : 'new'}`} seed={seed?.deepVideo} imageDraftScope={imageDraftScope} active={active && productionMode === 'video-remix'} actorId={actorId} onCreated={onCreated} />
    </div>
  </div>
}
