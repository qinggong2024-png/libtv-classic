import { useEffect, useLayoutEffect, useRef } from 'react'
import { Check, Images, PanelsTopLeft } from 'lucide-react'
import type { DirectorMode } from '../../videoComposerSettings'
import './directorModeSwitch.css'

interface Motion {
  x: number
  v: number
  squash: number
  stretch: number
  frame: number
  lastTime: number
  elapsed: number
  impactSpeed: number
  direction: number
  phase: 'idle' | 'approach' | 'impact' | 'rebound'
  initialized: boolean
}

export function DirectorModeSwitch({ value, onChange, disabled = false }: {
  value: DirectorMode
  onChange: (mode: DirectorMode) => void
  disabled?: boolean
}) {
  const track = useRef<HTMLDivElement>(null)
  const thumb = useRef<HTMLDivElement>(null)
  const activeMode = useRef(value)
  const motion = useRef<Motion>({
    x: 0, v: 0, squash: 0, stretch: 0, frame: 0, lastTime: 0,
    elapsed: 0, impactSpeed: 0, direction: 0, phase: 'idle', initialized: false,
  })
  const dragging = useRef<null | {
    pointer: number; originX: number; startX: number; lastX: number
    lastTime: number; speed: number; moved: boolean
  }>(null)
  const ignoreClick = useRef(false)

  const distance = () => Math.max(0, (track.current?.clientWidth ?? 0) - 12 - (thumb.current?.offsetWidth ?? 0))
  const destination = () => activeMode.current === 'cross_scene_v2' ? distance() : 0
  const clamp = (x: number, min: number, max: number) => Math.max(min, Math.min(max, x))

  const paint = () => {
    const el = thumb.current
    if (!el) return
    const m = motion.current
    m.x = clamp(m.x, 0, distance())
    el.style.transformOrigin = activeMode.current === 'cross_scene_v2' ? 'right center' : 'left center'
    el.style.transform = `translate3d(${m.x.toFixed(2)}px,0,0) scale(${(1 - m.squash + m.stretch).toFixed(4)},${(1 + m.squash * .33 - m.stretch * .18).toFixed(4)})`
  }

  const stop = () => {
    const m = motion.current
    if (m.frame) cancelAnimationFrame(m.frame)
    m.frame = 0
    m.lastTime = 0
    m.phase = 'idle'
  }

  const impact = (speed: number, direction: number) => {
    const m = motion.current
    m.phase = 'impact'
    m.elapsed = 0
    m.impactSpeed = clamp(speed, 120, 1700)
    m.direction = direction
    m.x = destination()
    m.v = 0
    m.squash = 0
    m.stretch = 0
  }

  const tick = (now: number) => {
    const m = motion.current
    m.frame = 0
    const dt = m.lastTime ? Math.min((now - m.lastTime) / 1000, .025) : 1 / 60
    m.lastTime = now
    const wall = destination()
    if (m.phase === 'approach') {
      m.v += (150 * (wall - m.x) - 8 * m.v) * dt
      const next = m.x + m.v * dt
      if ((wall > 0 && next >= wall && m.v > 0) ||
          (wall === 0 && next <= 0 && m.v < 0)) {
        impact(Math.abs(m.v), wall > 0 ? 1 : -1)
      } else {
        m.x = clamp(next, 0, distance())
      }
    } else if (m.phase === 'impact') {
      // Q-soft compression happens only when the thumb physically reaches the end.
      m.elapsed += dt
      const progress = clamp(m.elapsed / .095, 0, 1)
      const peak = clamp(m.impactSpeed / 9000, .085, .13)
      m.squash = peak * Math.pow(Math.sin(Math.PI * progress), .72)
      m.x = wall
      if (progress >= 1) {
        m.phase = 'rebound'
        m.elapsed = 0
        m.squash = 0
        // Single inward recoil; critical damping avoids artificial double shaking.
        m.v = -m.direction * clamp(m.impactSpeed * .56, 290, 800)
      }
    } else if (m.phase === 'rebound') {
      m.elapsed += dt
      m.v += (180 * (wall - m.x) - 26.84 * m.v) * dt
      m.x = clamp(m.x + m.v * dt, 0, distance())
      m.stretch = clamp(Math.abs(m.v) / 15000, 0, .058) * Math.exp(-m.elapsed * 3.3)
      if (Math.abs(wall - m.x) < .22 && Math.abs(m.v) < 2.5 && m.stretch < .001) {
        m.x = wall
        m.v = 0
        m.stretch = 0
        m.phase = 'idle'
      }
    }
    paint()
    if (m.phase !== 'idle') m.frame = requestAnimationFrame(tick)
  }

  const animate = (released = false, pointerSpeed = 0) => {
    stop()
    const m = motion.current
    const wall = destination()
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      m.x = wall; m.v = 0; m.squash = 0; m.stretch = 0; paint()
      return
    }
    if (Math.abs(m.x - wall) < .2) {
      const outward = wall > 0 ? pointerSpeed > 200 : pointerSpeed < -200
      if (!released || !outward) {
        m.x = wall; m.v = 0; m.squash = 0; m.stretch = 0; paint()
        return
      }
      impact(Math.abs(pointerSpeed), wall > 0 ? 1 : -1)
    } else {
      m.v *= Math.sign(wall - m.x) === Math.sign(m.v) ? .32 : .12
      m.squash = 0
      m.stretch = 0
      m.phase = 'approach'
    }
    m.frame = requestAnimationFrame(tick)
  }

  useLayoutEffect(() => {
    activeMode.current = value
    const m = motion.current
    if (!m.initialized) {
      m.initialized = true
      m.x = destination()
      paint()
    } else if (m.phase !== 'idle' || Math.abs(m.x - destination()) > .2) {
      animate()
    } else {
      m.x = destination()
      paint()
    }
    // Motion state and DOM refs persist between value changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  useEffect(() => {
    const resize = () => {
      stop()
      const m = motion.current
      m.x = destination()
      m.squash = 0; m.stretch = 0
      paint()
    }
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(resize) : null
    if (track.current) observer?.observe(track.current)
    return () => { observer?.disconnect(); stop() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const pointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (disabled || event.button !== 0) return
    const m = motion.current
    dragging.current = {
      pointer: event.pointerId, originX: event.clientX, startX: m.x,
      lastX: event.clientX, lastTime: performance.now(), speed: 0, moved: false,
    }
  }
  const pointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const d = dragging.current
    if (!d || event.pointerId !== d.pointer || disabled) return
    const dx = event.clientX - d.originX
    if (!d.moved && Math.abs(dx) < 5) return
    if (!d.moved) {
      d.moved = true
      stop()
      event.currentTarget.setPointerCapture(event.pointerId)
    }
    const now = performance.now()
    d.speed = clamp((event.clientX - d.lastX) / Math.max(now - d.lastTime, 1) * 1000, -1000, 1000)
    d.lastTime = now
    d.lastX = event.clientX
    const m = motion.current
    m.x = clamp(d.startX + dx, 0, distance())
    m.v = 0; m.squash = 0; m.stretch = 0
    paint()
  }
  const pointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    const d = dragging.current
    if (!d || event.pointerId !== d.pointer) return
    dragging.current = null
    if (!d.moved) return
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    ignoreClick.current = true
    window.setTimeout(() => { ignoreClick.current = false }, 0)
    const chosen: DirectorMode = Math.abs(d.speed) > 350
      ? (d.speed > 0 ? 'cross_scene_v2' : 'reference_guided')
      : (motion.current.x >= distance() / 2 ? 'cross_scene_v2' : 'reference_guided')
    if (chosen !== activeMode.current) {
      activeMode.current = chosen
      onChange(chosen)
    } else {
      animate(true, d.speed)
    }
  }

  const cross = value === 'cross_scene_v2'
  return <section className="director-mode-setting" aria-label="分镜编排模式">
    <div className="director-mode-setting__heading"><strong>分镜编排模式</strong></div>
    <div className="director-mode-switch" ref={track} role="radiogroup" aria-label="分镜编排模式"
      onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp}
      onPointerCancel={() => { dragging.current = null; animate() }}
      onClickCapture={(event) => { if (ignoreClick.current) { event.preventDefault(); event.stopPropagation() } }}
      >
      <div ref={thumb} className="director-mode-switch__thumb" aria-hidden="true"
        style={{ transform: cross ? 'translateX(100%)' : 'translateX(0)' }} />
      <button type="button" role="radio" aria-checked={!cross} className={cross ? '' : 'selected'}
        disabled={disabled} onClick={() => onChange('reference_guided')}>
        <Images size={15} strokeWidth={1.9} />遵循参考图
      </button>
      <button type="button" role="radio" aria-checked={cross} className={cross ? 'selected' : ''}
        disabled={disabled} onClick={() => onChange('cross_scene_v2')}>
        <PanelsTopLeft size={15} strokeWidth={1.9} />智能分镜
      </button>
    </div>
    <div className="director-mode-setting__description" aria-live="polite">
      <div><strong>{cross ? '智能分镜' : '遵循参考图'}</strong>
        <p>{cross
          ? 'Sol 根据商品与参考图，自主规划自然动作、镜头和合乎情境的场景变化，必要时跨场景。'
          : 'Sol 优先依据上传的参考图编排镜头与动作，保持人物、服装与场景一致性。'}</p>
      </div>
      <span><Check size={13} />已自动保存</span>
    </div>
  </section>
}
