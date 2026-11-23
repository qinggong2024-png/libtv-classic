import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { AdminCenterView } from './features/admin/AdminCenterView'
import { allPages, memberPages, canReadTasks, hasPage, isManagementAdmin, permittedView } from './pageAccess'
import type { AuthUser } from './types'

const member: AuthUser = { userId: 'member', username: 'member', role: 'USER', pagePermissions: ['images'] }

describe('account page permissions', () => {
  it('requires an explicitly authorized administrator account and role for management', () => {
    expect(isManagementAdmin({ ...member, role: 'ADMIN' })).toBe(false)
    expect(isManagementAdmin({ ...member, username: 'classic-admin' })).toBe(false)
    expect(isManagementAdmin({ ...member, username: 'classic-admin', role: 'ADMIN' })).toBe(true)
    expect(isManagementAdmin({ ...member, username: 'classic-admin-2', role: 'ADMIN' })).toBe(true)
    expect(isManagementAdmin({ ...member, username: 'classic-admin-2' })).toBe(false)
    expect(isManagementAdmin({ ...member, username: 'classic-admin-20', role: 'ADMIN' })).toBe(false)
    const html = renderToStaticMarkup(createElement(AdminCenterView, {
      actor: { ...member, role: 'ADMIN', isBrandAdmin: true }, system: null,
      section: 'accounts', onSectionChange() {}, async onKlingChanged() {},
    }))
    expect(html).toContain('管理中心仅允许已授权的平台管理员访问')
    expect(html).not.toContain('品牌与成员权限')
  })

  it('hides every management section from members, brand admins and other admin names', () => {
    for (const actor of [member, { ...member, isBrandAdmin: true }, { ...member, role: 'ADMIN' as const }, { ...member, username: 'classic-admin' }]) {
      for (const section of ['overview', 'quality', 'accounts', 'providers', 'security'] as const) {
        const html = renderToStaticMarkup(createElement(AdminCenterView, { actor, system: null, section, onSectionChange() {}, async onKlingChanged() {} }))
        expect(html).toContain('没有管理中心权限')
        expect(html).not.toContain('管理中心导航')
        expect(html).not.toContain('用量与费用')
      }
    }
  })

  it('keeps empty grants distinct from legacy defaults and does not grant background tasks', () => {
    expect(memberPages.every((page) => hasPage({ ...member, pagePermissions: undefined }, page))).toBe(true)
    expect(allPages.some((page) => hasPage({ ...member, pagePermissions: [] }, page))).toBe(false)
    expect(canReadTasks(member)).toBe(false)
    expect(canReadTasks({ ...member, pagePermissions: ['records'] })).toBe(true)
    expect(hasPage(null, 'images')).toBe(false)
  })

  it('reserves the library for the management admin despite legacy grants', () => {
    for (const actor of [member, { ...member, role: 'ADMIN' as const }, { ...member, isBrandAdmin: true }]) {
      expect(hasPage({ ...actor, pagePermissions: allPages }, 'library')).toBe(false)
      expect(hasPage({ ...actor, pagePermissions: undefined }, 'library')).toBe(false)
      expect(permittedView({ ...actor, pagePermissions: allPages }, 'library', null)).toBe('images')
    }
    expect(hasPage({ ...member, username: 'classic-admin', role: 'ADMIN', pagePermissions: [] }, 'library')).toBe(true)
    const secondAdmin: AuthUser = { ...member, username: 'classic-admin-2', role: 'ADMIN', pagePermissions: [] }
    expect(hasPage(secondAdmin, 'library')).toBe(true)
    expect(permittedView(secondAdmin, 'admin', null)).toBe('admin')
  })

  it('protects deep links and permits task detail viewing without creation permission', () => {
    expect(permittedView(member, 'admin', null)).toBe('images')
    expect(permittedView(member, 'make', 'guessed-task')).toBe('images')
    expect(permittedView({ ...member, pagePermissions: [] }, 'make', null)).toBe('settings')
    const reviewer: AuthUser = { ...member, pagePermissions: ['records'] }
    expect(permittedView(reviewer, 'make', 'owned-task')).toBe('make')
    expect(hasPage(reviewer, 'make')).toBe(false)
  })

  it('uses existing task access for the task list and protects direct URLs', () => {
    expect(permittedView(member, 'records', null)).toBe('images')
    expect(permittedView({ ...member, pagePermissions: ['make'] }, 'records', null)).toBe('make')
    expect(permittedView({ ...member, pagePermissions: [] }, 'records', null)).toBe('settings')
    expect(permittedView({ ...member, pagePermissions: ['records'] }, 'records', null)).toBe('records')
    expect(permittedView({ ...member, username: 'classic-admin', role: 'ADMIN', pagePermissions: [] }, 'records', null)).toBe('records')
  })
})
