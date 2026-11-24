// 本文件由 App.tsx 拆分生成（2026-09-28 前端架构拆分 v2）。
// 代码为逐字搬运，未经逻辑改写。


export function BrowserNotificationSetting({
  enabled,
  permission,
  onEnable,
  onDisable,
}: {
  enabled: boolean
  permission: NotificationPermission | 'unsupported'
  onEnable: () => Promise<void>
  onDisable: () => void
}) {
  const active = enabled && permission === 'granted'
  const unavailable = permission === 'unsupported'
  const blocked = permission === 'denied'
  const description = unavailable
    ? '当前浏览器不支持系统通知。'
    : blocked
      ? '通知权限已被浏览器阻止，请在地址栏的网站权限中重新允许。'
      : '接收待确认及视频完成通知。'
  const stateLabel = unavailable ? '不可用' : blocked ? '已阻止' : active ? '已开启' : '已关闭'
  return <section className="settings-section prompt-confirmation-section">
    <div className="settings-section-heading">
      <div><h2>浏览器通知</h2><p>{description}</p></div>
      <div className="prompt-confirmation-control">
        <span className={`prompt-confirmation-state${active ? ' is-active' : ''}`}>{stateLabel}</span>
        <label className="settings-switch" title={blocked ? '请先在浏览器的网站权限中允许通知' : undefined}>
          <input
            type="checkbox"
            checked={active}
            disabled={unavailable || blocked}
            aria-label="浏览器通知"
            onChange={(event) => event.target.checked ? void onEnable() : onDisable()}
          />
          <span aria-hidden="true" />
        </label>
      </div>
    </div>
  </section>
}
