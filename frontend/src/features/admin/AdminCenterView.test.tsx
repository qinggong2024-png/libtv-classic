import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import type { AuthUser } from '../../types'
import { AdminCenterView } from './AdminCenterView'

vi.mock('./UserManagement', () => ({
  UserManagement: ({ section }: { section: string }) => <div data-account-section={section}>账号工作区</div>,
}))

const actor = { userId: 'admin-id', username: 'classic-admin', role: 'ADMIN' } as AuthUser
const render = (section: 'accounts' | 'usage' | 'events') => renderToStaticMarkup(
  <AdminCenterView actor={actor} section={section} system={null} onSectionChange={vi.fn()} onKlingChanged={async () => {}} />,
)

describe('management center hierarchy', () => {
  it.each(['accounts', 'usage', 'events'] as const)('opens %s as an independent management page', (section) => {
    const html = render(section)
    expect(html).toContain(`data-account-section="${section}"`)
    expect(html).toContain('生产运营')
    expect(html).toContain('组织与数据')
    expect(html).toContain('平台配置')
    expect(html).toContain('aria-label="管理中心导航"')
    expect(html).not.toContain('account-tabs')
  })

  it('marks the selected page as current in the sidebar', () => {
    const html = render('usage')
    expect(html).toMatch(/aria-current="page"[^>]*><svg[^>]*>.*?<\/svg><span>用量与费用<\/span><\/button>/)
    expect(html).not.toContain('账号用量、费用和单价')
  })

  it('blocks users without platform administration access', () => {
    const html = renderToStaticMarkup(<AdminCenterView
      actor={{ ...actor, username: 'regular-admin' }}
      section="accounts"
      system={null}
      onSectionChange={vi.fn()}
      onKlingChanged={async () => {}}
    />)
    expect(html).toContain('没有管理中心权限')
    expect(html).not.toContain('管理中心导航')
  })
})
