// 本文件由 App.tsx 拆分生成（2026-09-28 前端架构拆分 v2）。
// 代码为逐字搬运，未经逻辑改写。
import { Activity, ChevronRight, Clapperboard, KeyRound, LayoutDashboard, ShieldCheck, UserRound } from 'lucide-react'
import { type AuthUser, type SystemStatus } from '../../types'
import { ErrorBoundary } from '../../ErrorBoundary'
import { OperationsPanel } from '../../OperationsPanel'
import { providerPresentation } from '../../providerPresentation'
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
}: {
  system: SystemStatus | null
  actor: AuthUser | null
  section: AdminSection
  onSectionChange: (section: AdminSection) => void
  onKlingChanged: () => Promise<void>
}) {
  const isAdmin = isManagementAdmin(actor)
  const activeVideo = system?.videoGeneration ?? system?.kling
  const activeProvider = providerPresentation(activeVideo?.provider)
  const providerStateLabel = activeVideo?.available && activeVideo.authenticated
    ? '已就绪'
    : activeVideo?.enabled ? '待验证' : '未启用'
  const environmentLabel = system?.environment === 'production'
    ? '正式生产'
    : system?.environment === 'staging'
      ? '预发布'
      : system?.environment === 'test'
        ? '测试环境'
        : '开发环境'
  const allSections: Array<{ value: AdminSection; label: string; description: string; icon: React.ReactNode }> = [
    { value: 'overview', label: '运行总览', description: '生产开关、队列与告警', icon: <Activity size={17} /> },
    { value: 'accounts', label: '品牌与账号', description: '权限、使用记录与美元预算', icon: <UserRound size={17} /> },
    { value: 'providers', label: '生成提供商', description: '图片和视频服务设置', icon: <Clapperboard size={17} /> },
    { value: 'security', label: '流程与分析', description: '确认策略和分析服务', icon: <KeyRound size={17} /> },
  ]
  const sections = allSections
  const currentSection = section
  if (!isAdmin) return <div className="page-view"><EmptyState icon={<ShieldCheck size={24} />} title="没有管理中心权限" description="管理中心仅允许已授权的平台管理员访问。" /></div>
  return <div className="page-view admin-view">
    <div className="page-title">
      <div><span>生产控制台</span><h1>管理中心</h1><p>集中管理运行状态、生成通道与生产安全边界。</p></div>
      <div className={`system-health ${system ? 'is-online' : 'is-warning'}`}><span /><div><strong>{system ? '生产服务在线' : '正在读取服务状态'}</strong><small>管理员权限</small></div></div>
    </div>
    <nav className="subnav-tabs" aria-label="管理中心导航">
      {sections.map((item) => <button key={item.value} className={currentSection === item.value ? 'is-active' : ''} onClick={() => onSectionChange(item.value)} aria-current={currentSection === item.value ? 'page' : undefined}>
        <span>{item.icon}</span><span><strong>{item.label}</strong><small>{item.description}</small></span><ChevronRight size={16} />
      </button>)}
    </nav>
    <ErrorBoundary resetKeys={[section]} onBack={() => onSectionChange('overview')} backLabel="返回运行总览">
    <div className="admin-section-content" key={section}>
      {currentSection === 'overview' ? <>
        <section className="admin-summary-grid" aria-label="系统摘要">
          <div><span className="summary-icon summary-icon--blue"><LayoutDashboard size={19} /></span><span><small>运行环境</small><strong>{environmentLabel}</strong></span></div>
          <div><span className={`summary-icon ${activeVideo?.available && activeVideo.authenticated ? 'summary-icon--green' : 'summary-icon--amber'}`}><Activity size={19} /></span><span><small>{activeProvider.displayLabel}</small><strong>{providerStateLabel}</strong></span></div>
          <div><span className="summary-icon summary-icon--green"><Clapperboard size={19} /></span><span><small>生成模型</small><strong>{activeVideo?.model || activeVideo?.models[0] || '待配置'}</strong></span></div>
        </section>
        <section className="settings-section admin-operations-card"><OperationsPanel adminId={actor?.userId ?? null} /></section>
      </> : null}
      {currentSection === 'accounts' ? <UserManagement actor={actor!} platform sharedAuthentication={system?.sharedAuthentication ?? false} /> : null}
      {currentSection === 'providers' ? isAdmin ? <>
        <ProviderRoutingSettings key={`routing-${activeVideo?.backend ?? 'unknown'}`} adminId={actor!.userId} onChanged={onKlingChanged} />
        <KlingBillingPanel adminId={actor!.userId} />
        <KlingProviderSettings key={`kling-${activeVideo?.backend ?? 'unknown'}`} adminId={actor!.userId} onChanged={onKlingChanged} />
        <WaveSpeedProviderSettings key={`wavespeed-${activeVideo?.backend ?? 'unknown'}`} adminId={actor!.userId} onChanged={onKlingChanged} />
        <BachProviderSettings key={`bach-${activeVideo?.backend ?? 'unknown'}`} adminId={actor!.userId} onChanged={onKlingChanged} />
      </> : <EmptyState icon={<ShieldCheck size={24} />} title="需要管理员权限" description="只有管理员可以修改生成服务的密钥、地址和连接设置。" /> : null}
      {currentSection === 'security' ? <>
        <PromptConfirmationSetting adminId={actor!.userId} />
        <CreativeV25PreviewPanel adminId={actor!.userId} />
        {isAdmin ? <ProviderCredentials adminId={actor!.userId} /> : <EmptyState icon={<KeyRound size={24} />} title="连接信息由管理员设置" description="你可以管理生成前的确认方式，服务密钥请联系管理员设置。" />}
      </> : null}
    </div>
    </ErrorBoundary>
  </div>
}
