// 本文件由 App.tsx 拆分生成（2026-09-21 前端架构拆分 v1）。
// 应用外壳：跳转链接 + 侧边栏 + 移动端顶栏 + 底部导航 + ErrorBoundary 边界。
// 侧边栏信息浮层支持外部点击、焦点离开和 Escape 关闭。
import { type ComponentProps, type ReactNode, useEffect, useRef } from 'react'
import { HelpCircle, Settings, UserRound } from 'lucide-react'
import { ErrorBoundary } from '../ErrorBoundary'
import { ProductLogo } from '../shared/components/ProductLogo'
import type { AuthUser, SystemStatus } from '../types'
import type { View } from './appTypes'
import { accountRoleLabels } from '../accountRoleLabels'
import { DailyBudget } from '../shared/components/DailyBudget'

export type NavItem = { value: View; label: string; icon: ReactNode; badge?: number; badgeLabel?: string }

type Props = {
  nav: NavItem[]
  view: View
  onSwitchView: (next: View) => void
  actor: AuthUser | null
  system: SystemStatus | null
  error: string
  sidebarInfo: 'account' | 'help' | null
  onSidebarInfoChange: (next: 'account' | 'help' | null) => void
  devActorRole: 'user' | 'admin'
  onSwitchDevelopmentActor: () => void
  onSignOut: () => void
  errorResetKeys: ComponentProps<typeof ErrorBoundary>['resetKeys']
  onErrorBack: () => void
  children: ReactNode
  overlays: ReactNode
}

export function AppShell({
  nav,
  view,
  onSwitchView,
  actor,
  system,
  error,
  sidebarInfo,
  onSidebarInfoChange,
  devActorRole,
  onSwitchDevelopmentActor,
  onSignOut,
  errorResetKeys,
  onErrorBack,
  children,
  overlays,
}: Props) {
  const mobileNav = nav
  const roleLabel = actor?.isBrandAdmin ? '品牌管理员' : actor ? accountRoleLabels[actor.role] : '普通账号'
  const sidebarPopoverRef = useRef<HTMLDivElement>(null)
  const sidebarUtilityRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!sidebarInfo) return

    const dismissOutside = (event: Event) => {
      const target = event.target
      if (!(target instanceof Node)) return
      if (sidebarPopoverRef.current?.contains(target) || sidebarUtilityRef.current?.contains(target)) return
      onSidebarInfoChange(null)
    }
    const dismissOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      onSidebarInfoChange(null)
      sidebarUtilityRef.current?.querySelector<HTMLButtonElement>('[aria-expanded="true"]')?.focus()
    }

    document.addEventListener('pointerdown', dismissOutside, true)
    document.addEventListener('focusin', dismissOutside)
    document.addEventListener('keydown', dismissOnEscape)
    return () => {
      document.removeEventListener('pointerdown', dismissOutside, true)
      document.removeEventListener('focusin', dismissOutside)
      document.removeEventListener('keydown', dismissOnEscape)
    }
  }, [sidebarInfo, onSidebarInfoChange])

  return <div className="app-shell">
    <a className="skip-link" href="#main-content">跳到主要内容</a>
    <aside className="sidebar">
      <ProductLogo />
      <nav className="sidebar-main-nav" aria-label="主导航">{nav.filter((item) => item.value !== 'settings').map((item) => <button key={item.value} aria-current={view === item.value ? 'page' : undefined} onClick={() => onSwitchView(item.value)} className={view === item.value ? 'is-active' : ''}>{item.icon}<span>{item.label}</span>{item.badge ? <i title={item.badgeLabel} aria-label={item.badgeLabel}>{item.badge}</i> : null}</button>)}</nav>
      <div className="sidebar-bottom">
        <DailyBudget onOpenUsage={() => onSwitchView('settings')} />
        <div className="sidebar-status"><span className={error ? 'is-offline' : ''} /><span>{error ? '暂时连不上网站' : '经典版'}</span></div>
        <button className={`sidebar-settings ${view === 'settings' ? 'is-active' : ''}`} onClick={() => onSwitchView('settings')}><Settings size={18} /><span>设置</span></button>
        <div className="sidebar-utility" ref={sidebarUtilityRef}><button onClick={() => onSidebarInfoChange(sidebarInfo === 'help' ? null : 'help')} aria-label="帮助" title="帮助" aria-expanded={sidebarInfo === 'help'} aria-controls={sidebarInfo === 'help' ? 'sidebar-info-popover' : undefined}><HelpCircle size={17} /></button><button onClick={() => onSidebarInfoChange(sidebarInfo === 'account' ? null : 'account')} aria-label="账号" title="账号" aria-expanded={sidebarInfo === 'account'} aria-controls={sidebarInfo === 'account' ? 'sidebar-info-popover' : undefined}><UserRound size={17} /></button></div>
        {sidebarInfo ? <div className="sidebar-popover" id="sidebar-info-popover" ref={sidebarPopoverRef} role="status"><strong>{sidebarInfo === 'help' ? '制作帮助' : '当前账号'}</strong><p>{sidebarInfo === 'help' ? '上传服装与参考图后，确认方案即可开始生成。历史视频可在记录中继续处理。' : `${actor?.username ?? '生产账号'} · ${roleLabel}${actor?.brandName ? ` · ${actor.brandName}` : ''}`}</p>{sidebarInfo === 'account' && system?.sharedAuthentication ? <button onClick={onSignOut}>退出登录</button> : sidebarInfo === 'account' && system?.phaseOneAdminId ? <button onClick={onSwitchDevelopmentActor}>切换为{devActorRole === 'admin' ? '普通用户' : '管理员'}</button> : <button onClick={() => onSidebarInfoChange(null)}>关闭</button>}</div> : null}
      </div>
    </aside>
    <div className="app-body">
      <header className="mobile-topbar"><ProductLogo /><div className="service-state"><span className={error ? 'is-offline' : ''} />{error ? '暂时连不上网站' : '经典版'}</div></header>
      <div className="mobile-budget"><DailyBudget onOpenUsage={() => onSwitchView('settings')} /></div>
      <main id="main-content"><ErrorBoundary resetKeys={errorResetKeys} onBack={onErrorBack} backLabel="返回制作页">{children}</ErrorBoundary></main>
    </div>
    <nav className="mobile-nav" aria-label="移动端主导航" style={{ gridTemplateColumns: `repeat(${mobileNav.length}, minmax(0, 1fr))` }}>{mobileNav.map((item) => <button key={item.value} aria-current={view === item.value ? 'page' : undefined} onClick={() => onSwitchView(item.value)} className={view === item.value ? 'is-active' : ''}>{item.icon}<span>{item.label}</span>{item.badge ? <i title={item.badgeLabel} aria-label={item.badgeLabel}>{item.badge}</i> : null}</button>)}</nav>
    {overlays}
  </div>
}
