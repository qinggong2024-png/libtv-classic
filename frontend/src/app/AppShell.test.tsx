import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppShell } from './AppShell'

const render = () => renderToStaticMarkup(<AppShell nav={[]} view="settings" onSwitchView={() => {}} actor={null} system={null} error="" sidebarInfo={null} onSidebarInfoChange={() => {}} devActorRole="user" onSwitchDevelopmentActor={() => {}} onSignOut={() => {}} errorResetKeys={[]} onErrorBack={() => {}} overlays={null}><div>content</div></AppShell>)
afterEach(() => vi.unstubAllEnvs())
describe('sidebar feedback', () => {
  it('opens configured feedback in a separate tab and retains the account entry', () => {
    vi.stubEnv('VITE_FEEDBACK_URL', 'https://example.com/feedback?sheet=a&view=b')
    const html = render()
    expect(html).toContain('href="https://example.com/feedback?sheet=a&amp;view=b" target="_blank" rel="noopener noreferrer"')
    expect(html).toContain('aria-label="账号"')
    expect(html).not.toContain('制作帮助')
  })
  it('disables feedback when not configured', () => {
    vi.stubEnv('VITE_FEEDBACK_URL', '')
    expect(render()).toContain('尚未配置反馈地址')
  })
})
