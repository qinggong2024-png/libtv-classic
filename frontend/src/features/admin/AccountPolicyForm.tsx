import { errorMessage } from '../../errorPresentation'
import { useState, type FormEvent } from 'react'
import { updateAccountPolicy } from '../../api'
import type { AccountBrand, AccountPolicy, AdminUser, PagePermission } from '../../types'
import { accountRoleLabels } from '../../accountRoleLabels'
import { memberPages, pageLabels } from '../../pageAccess'
import { accountBrandOptions } from './brandOptions'

export function AccountPolicyForm({ user, actorId, platform, brands, onSaved, onClose }: {
  user: AdminUser; actorId: string; platform: boolean; brands: AccountBrand[]
  onSaved: (user: AdminUser) => void; onClose: () => void
}) {
  const [role, setRole] = useState(user.role)
  const [brandId, setBrandId] = useState(user.brandId ?? '')
  const [brandAdmin, setBrandAdmin] = useState(user.isBrandAdmin ?? false)
  const [active, setActive] = useState(user.isActive)
  const [budget, setBudget] = useState(user.dailyBudgetUsd?.toString() ?? '')
  const [pages, setPages] = useState<PagePermission[]>((user.pagePermissions ?? memberPages).filter((page) => page !== 'library'))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const save = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true); setError('')
    const money = (value: string) => value.trim() === '' ? null : Number(value)
    const policy: AccountPolicy = { role, isActive: active, dailyBudgetUsd: money(budget), pagePermissions: pages }
    if (platform) Object.assign(policy, { brandId: brandId || null, isBrandAdmin: brandAdmin })
    try { onSaved(await updateAccountPolicy(actorId, user.id, policy)) }
    catch (cause) { setError(errorMessage(cause, '保存失败，请重试')) }
    finally { setBusy(false) }
  }
  return <form className="account-policy-form" onSubmit={(event) => void save(event)} aria-label={`${user.username} 的权限与预算`}>
    <h3>权限与美元预算 · {user.username}</h3>
    <div className="account-policy-fields">
      <label>账号角色<select value={role} onChange={(event) => setRole(event.target.value as AdminUser['role'])}>{Object.entries(accountRoleLabels).filter(([key]) => key !== 'ADMIN').map(([key, name]) => <option value={key} key={key}>{name}</option>)}</select></label>
      {platform ? <label>所属品牌<select value={brandId} onChange={(event) => setBrandId(event.target.value)}><option value="">未分配（个人隔离）</option>{accountBrandOptions(brands, brandId).map((b) => <option value={b.id} key={b.id}>{b.displayName ?? b.name}{b.id === brandId && !accountBrandOptions(brands).some((item) => item.id === b.id) ? '（历史归属）' : ''}</option>)}</select></label> : null}
      <label>每日预算（USD）<input type="number" min="0" max="1000000" step="0.000001" value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="留空为不限额" /></label>
    </div>
    <div role="group" aria-label="页面权限"><h4>页面权限</h4><div className="account-policy-checks">
      {memberPages.map((page) => <label key={page}><input type="checkbox" checked={pages.includes(page)} onChange={(event) => setPages((current) => event.target.checked ? [...current, page] : current.filter((value) => value !== page))} />{pageLabels[page]}</label>)}
    </div><p>仅显示和允许已勾选的页面；设置始终可用。内容库与管理中心仅供已授权的平台管理员使用。</p></div>
    <div className="account-policy-checks">
      {platform ? <label><input type="checkbox" checked={brandAdmin} onChange={(e) => setBrandAdmin(e.target.checked)} />品牌管理员</label> : null}
      <label><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />允许登录与生成</label>
    </div>
    <p>按全站统一单价计算，此账号的图片与视频共用每日额度，北京时间每日零点重置。已预留任务及结果待核对任务的预估金额会暂时占用额度；0 表示禁止正金额预算占用，留空表示不限额。</p>
    {error ? <div className="inline-alert inline-alert--danger" role="alert">{error}</div> : null}
    <div className="account-actions"><button type="button" className="secondary-button" disabled={busy} onClick={onClose}>取消</button><button className="primary-button" disabled={busy} type="submit">{busy ? '正在保存…' : '保存权限与预算'}</button></div>
  </form>
}
