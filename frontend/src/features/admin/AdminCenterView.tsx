// 经典版管理中心：按生产运营、组织数据、平台配置组织页面。
import { ChartNoAxesCombined, Clapperboard, History, KeyRound, LayoutDashboard, Plug, ShieldCheck, UserRound } from 'lucide-react'
import { type AuthUser, type SystemStatus } from '../../types'
import { ErrorBoundary } from '../../ErrorBoundary'
import { OperationsPanel } from '../../OperationsPanel'
import { AdminVideoReview } from './AdminVideoReview'
import { KlingProviderSettings } from './KlingProviderSettings'
import { KlingBillingPanel } from './KlingBillingPanel'
import { WaveSpeedProviderSettings } from './WaveSpeedProviderSettings'
import { UserManagement } from './UserManagement'
import { BachProviderSettings } from './BachProviderSettings'
import { PromptConfirmationSetting } from './PromptConfirmationSetting'
import { CreativeV25PreviewPanel } from './CreativeV25PreviewPanel'
import { type AdminSection } from '../../app/appTypes'
import { EmptyState } from '../../shared/components/Section'
import { ProviderCredentials, ProviderRoutingSettings } from './ProviderCredentials'
import { isManagementAdmin } from '../../pageAccess'
import './adminCenter.css'

export function AdminCenterView({
  system,
  actor,
  section,
  onSectionChange,
  onKlingChanged,
  onShowTasks,
}: {
  system: SystemStatus | null
  actor: AuthUser | null
  section: AdminSection
  onSectionChange: (section: AdminSection) => void
  onKlingChanged: () => Promise<void>
  onShowTasks?: () => void
}) {
  const isAdmin = isManagementAdmin(actor)
  const activeVideo = system?.videoGeneration ?? system?.kling
  const environmentLabel = system?.environment === 'production'
    ? '正式生产'
    : system?.environment === 'staging'
      ? '预发布'
      : system?.environment === 'test'
        ? '测试环境'
        : '开发环境'
  const groups: Array<{ title: string; items: Array<{ value: AdminSection; label: string; icon: React.ReactNode }> }> = [
    { title: '生产运营', items: [
      { value: 'overview', label: '运行总览', icon: <LayoutDashboard size={17} /> },
      { value: 'quality', label: '视频巡检', icon: <Clapperboard size={17} /> },
    ] },
    { title: '组织与数据', items: [
      { value: 'accounts', label: '品牌与账号', icon: <UserRound size={17} /> },
      { value: 'usage', label: '用量与费用', icon: <ChartNoAxesCombined size={17} /> },
      { value: 'events', label: '操作记录', icon: <History size={17} /> },
    ] },
    { title: '平台配置', items: [
      { value: 'providers', label: '生成服务', icon: <Plug size={17} /> },
      { value: 'security', label: '生产策略', icon: <KeyRound size={17} /> },
    ] },
  ]
  const currentSection = section
  const currentGroup = groups.find((group) => group.items.some((item) => item.value === currentSection)) ?? groups[0]
  const currentItem = currentGroup.items.find((item) => item.value === currentSection) ?? currentGroup.items[0]
  if (!isAdmin) return <div className="page-view"><EmptyState icon={<ShieldCheck size={24} />} title="没有管理中心权限" description="管理中心仅允许已授权的平台管理员访问。" /></div>
  return <div className="page-view admin-view">
    <header className="page-title admin-page-heading">
      <div><h1>管理中心</h1></div>
      <div className={`system-health ${system ? 'is-online' : 'is-warning'}`} role="status"><span /><div><strong>{system ? '状态已读取' : '正在读取状态'}</strong><small>{environmentLabel}</small></div></div>
    </header>
    <div className="admin-layout">
      <nav className="admin-sidebar" aria-label="管理中心导航">
        {groups.map((group) => <div className="admin-nav-group" key={group.title}>
          <h2>{group.title}</h2>
          {group.items.map((item) => <button type="button" key={item.value} className={currentSection === item.value ? 'is-active' : ''} onClick={() => onSectionChange(item.value)} aria-current={currentSection === item.value ? 'page' : undefined}>
            {item.icon}<span>{item.label}</span>
          </button>)}
        </div>)}
      </nav>
      <section className="admin-main" aria-label="管理工作区" id="admin-main-content">
        <div className="admin-content-heading"><span>{currentGroup.title}</span><h2>{currentItem.label}</h2></div>
    <ErrorBoundary resetKeys={[section]} onBack={() => onSectionChange('overview')} backLabel="返回运行总览">
    <div className="admin-section-content" key={section}>
      {currentSection === 'overview' ? <>
        <section className="settings-section admin-operations-card"><OperationsPanel adminId={actor?.userId ?? null} onShowTasks={onShowTasks} onShowBilling={() => onSectionChange('usage')} /></section>
      </> : null}
      {currentSection === 'quality' ? <section className="settings-section"><AdminVideoReview adminId={actor!.userId} /></section> : null}
      {currentSection === 'accounts' || currentSection === 'usage' || currentSection === 'events' ? <UserManagement actor={actor!} section={currentSection} platform sharedAuthentication={system?.sharedAuthentication ?? false} /> : null}
      {currentSection === 'providers' ? isAdmin ? <>
        <details className="admin-disclosure" open><summary>服务路由</summary><div className="admin-disclosure-body"><ProviderRoutingSettings key={`routing-${activeVideo?.backend ?? 'unknown'}`} adminId={actor!.userId} onChanged={onKlingChanged} /></div></details>
        <details className="admin-disclosure"><summary>Kling 视频生成</summary><div className="admin-disclosure-body"><KlingProviderSettings key={`kling-${activeVideo?.backend ?? 'unknown'}`} adminId={actor!.userId} onChanged={onKlingChanged} /></div></details>
        <details className="admin-disclosure"><summary>WaveSpeed 图片生成</summary><div className="admin-disclosure-body"><WaveSpeedProviderSettings key={`wavespeed-${activeVideo?.backend ?? 'unknown'}`} adminId={actor!.userId} onChanged={onKlingChanged} /></div></details>
        <details className="admin-disclosure"><summary>Bach 服务</summary><div className="admin-disclosure-body"><BachProviderSettings key={`bach-${activeVideo?.backend ?? 'unknown'}`} adminId={actor!.userId} onChanged={onKlingChanged} /></div></details>
        <details className="admin-disclosure"><summary>Kling 计费</summary><div className="admin-disclosure-body"><KlingBillingPanel adminId={actor!.userId} /></div></details>
      </> : <EmptyState icon={<ShieldCheck size={24} />} title="需要管理员权限" description="只有管理员可以修改生成服务的密钥、地址和连接设置。" /> : null}
      {currentSection === 'security' ? <>
        <details className="admin-disclosure" open><summary>生成前确认</summary><div className="admin-disclosure-body"><PromptConfirmationSetting adminId={actor!.userId} /></div></details>
        <details className="admin-disclosure"><summary>分析服务</summary><div className="admin-disclosure-body"><CreativeV25PreviewPanel adminId={actor!.userId} /></div></details>
        <details className="admin-disclosure"><summary>连接凭据</summary><div className="admin-disclosure-body">{isAdmin ? <ProviderCredentials adminId={actor!.userId} /> : <EmptyState icon={<KeyRound size={24} />} title="连接信息由管理员设置" description="你可以管理生成前的确认方式，服务密钥请联系管理员设置。" />}</div></details>
      </> : null}
    </div>
    </ErrorBoundary>
      </section>
    </div>
  </div>
}
