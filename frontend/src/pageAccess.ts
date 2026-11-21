import type { AuthUser, PagePermission } from './types'
import type { View } from './app/appTypes'

export const pageLabels: Record<PagePermission, string> = {
  images: '图片中心', make: '视频制作', records: '任务', library: '内容库',
}
export const allPages = Object.keys(pageLabels) as PagePermission[]

export function isManagementAdmin(actor: Pick<AuthUser, 'username' | 'role'> | null) {
  return Boolean(actor && ['classic-admin', 'classic-admin-2'].includes(actor.username) && actor.role === 'ADMIN')
}

export const memberPages = allPages.filter((page) => page != 'library')

export function hasPage(actor: AuthUser | null, page: PagePermission) {
  if (page === 'library') return isManagementAdmin(actor)
  return Boolean(actor && (isManagementAdmin(actor) || (actor.pagePermissions ?? allPages).includes(page)))
}

export function canReadTasks(actor: AuthUser | null) {
  return hasPage(actor, 'make') || hasPage(actor, 'records')
}

export function permittedView(actor: AuthUser | null, requested: View, taskId: string | null): View {
  if (requested === 'settings') return requested
  if (requested === 'admin' ? isManagementAdmin(actor) : hasPage(actor, requested)) return requested
  if (requested === 'make' && taskId && hasPage(actor, 'records')) return requested
  return allPages.find((page) => hasPage(actor, page)) ?? 'settings'
}
