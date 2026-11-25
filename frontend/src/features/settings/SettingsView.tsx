import { useEffect } from 'react'
import { ChevronDown, LogOut } from 'lucide-react'
import './personalSettings.css'
import { type SystemStatus } from '../../types'
import { removeStorage, writeStorage } from '../../safeStorage'
import { BrowserNotificationSetting } from './BrowserNotificationSetting'
import { PersonalPasswordSetting, PersonalVideoLearningSetting } from './PersonalSettings'
import { AccountUsagePanel } from './AccountUsagePanel'

export function SettingsView({
  system,
  onLogout,
  browserNotificationsEnabled,
  browserNotificationPermission,
  onEnableBrowserNotifications,
  onDisableBrowserNotifications,
  actorId,
}: {
  system: SystemStatus | null
  onLogout: () => void
  browserNotificationsEnabled: boolean
  browserNotificationPermission: NotificationPermission | 'unsupported'
  onEnableBrowserNotifications: () => Promise<void>
  onDisableBrowserNotifications: () => void
  actorId?: string
}) {
  useEffect(() => {
    writeStorage('local', 'generation-provider', 'kling')
    removeStorage('local', 'default-model')
    removeStorage('local', ['default-output-variants', 'video-direction', 'default-ratio', 'default-duration', 'max-concurrency', 'retry-count', 'save-directory'])
  }, [])
  return <div className="page-view settings-view personal-settings">
    <header className="personal-settings-header"><h1>个人设置</h1></header>
    <div className="personal-settings-layout">
      <AccountUsagePanel key={actorId} actorId={actorId} />
      <aside className="personal-settings-sidebar">
        <div className="settings-section personal-settings-group">
          <h2>工作偏好</h2>
          <BrowserNotificationSetting enabled={browserNotificationsEnabled} permission={browserNotificationPermission} onEnable={onEnableBrowserNotifications} onDisable={onDisableBrowserNotifications} />
          <details className="personal-settings-disclosure"><summary>智能学习 <ChevronDown size={16} aria-hidden="true" /></summary><PersonalVideoLearningSetting /></details>
        </div>
        {system?.sharedAuthentication ? <section className="settings-section personal-settings-account">
          <h2>账号与安全</h2>
          {system?.sharedAuthentication ? <><details className="personal-settings-disclosure"><summary>修改密码 <ChevronDown size={16} aria-hidden="true" /></summary><PersonalPasswordSetting /></details>
          <button className="secondary-button personal-settings-logout" type="button" onClick={onLogout}><LogOut size={16} />退出登录</button></> : null}
        </section> : null}
      </aside>
    </div>
  </div>
}
