import { errorMessage } from '../../errorPresentation'
// 本文件由 App.tsx 拆分生成（2026-09-28 前端架构拆分 v2）。
import { AlertTriangle, CheckCircle2, KeyRound, LoaderCircle, LogOut, Plus, RefreshCw, UserRound, Wallet, History } from 'lucide-react'
import { type FormEvent, useCallback, useEffect, useState } from 'react'
import { ApiError, changePassword, createAccountBrand, createAdminUser, deactivateAdminUser, getAccountBrands, getAdminUsers, resetAdminUserPassword } from '../../api'
import { type AccountBrand, type AdminUser, type AuthUser } from '../../types'
import { AccountPolicyForm } from './AccountPolicyForm'
import { GlobalUsagePricingForm } from './GlobalUsagePricingForm'
import { accountRoleLabels } from '../../accountRoleLabels'
import { AdminUsagePanel } from './AdminUsagePanel'
import { isManagementAdmin } from '../../pageAccess'
import { formatTime } from '../../domain/taskPresentation'
import { EmptyState } from '../../shared/components/Section'
import { FoundationDrawer } from '../../shared/components/ui'
import { AccountEventsPanel } from '../settings/AccountEventsPanel'
import { ReportPagination } from '../settings/ReportPagination'
import './accountManagement.css'

const accountTabs = [
  { value: 'accounts', label: '账号管理', icon: UserRound },
  { value: 'usage', label: '用量与费用', icon: Wallet },
  { value: 'events', label: '操作记录', icon: History },
] as const

export function UserManagement(props: { actor: AuthUser; sharedAuthentication: boolean; platform?: boolean }) {
  if (!isManagementAdmin(props.actor)) return <EmptyState icon={<UserRound size={24} />} title="没有管理中心权限" description="仅已授权的平台管理员可以查看账号管理与全站用量。" />
  return <ManagedAccounts key={props.actor.userId} {...props} adminId={props.actor.userId} />
}

function ManagedAccounts({ adminId, actor, sharedAuthentication, platform = true }: { adminId: string; actor: AuthUser; sharedAuthentication: boolean; platform?: boolean }) {
  const [tab, setTab] = useState<typeof accountTabs[number]['value']>('accounts')
  const [createOpen, setCreateOpen] = useState<'account' | 'brand' | null>(null)
  const [search, setSearch] = useState('')
  const [filterBrand, setFilterBrand] = useState('')
  const [filterState, setFilterState] = useState('')
  const [offset, setOffset] = useState(0)
  const [limit, setLimit] = useState(20)
  const [pricingOpen, setPricingOpen] = useState(false)
  const [users, setUsers] = useState<AdminUser[]>([])
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [resetUserId, setResetUserId] = useState<string | null>(null)
  const [resetPassword, setResetPassword] = useState('')
  const [changeOpen, setChangeOpen] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newPasswordConfirmation, setNewPasswordConfirmation] = useState('')
  const [busy, setBusy] = useState<string | null>('load')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [brands, setBrands] = useState<AccountBrand[]>([])
  const [brandName, setBrandName] = useState('')
  const [brandId, setBrandId] = useState('')
  const [role, setRole] = useState<AdminUser['role']>('USER')
  const [brandAdmin, setBrandAdmin] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)

  const load = useCallback(async () => {
    setBusy('load')
    try {
      const [accounts, accountBrands] = await Promise.all([getAdminUsers(adminId), getAccountBrands(adminId)])
      setUsers(accounts)
      setBrands(accountBrands)
      setError('')
    } catch (cause) {
      setError(errorMessage(cause, '无法读取账号列表'))
    } finally {
      setBusy(null)
    }
  }, [adminId])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalizedUsername = username.trim()
    if (!normalizedUsername || password.length < 12) return
    setBusy('create')
    setError('')
    setNotice('')
    try {
      const created = await createAdminUser(adminId, {
        username: normalizedUsername,
        password,
        role,
        brandId: brandId || null,
        isBrandAdmin: platform && brandAdmin,
      })
      setUsers((current) => [...current, created].sort((left, right) => left.username.localeCompare(right.username, 'zh-CN')))
      setUsername('')
      setPassword('')
      setCreateOpen(null)
      setSearch(''); setFilterBrand(''); setFilterState(''); setOffset(0)
      setNotice(`账号“${created.username}”已创建，可以使用临时密码登录。`)
    } catch (cause) {
      setError(errorMessage(cause, '账号创建失败'))
    } finally {
      setBusy(null)
    }
  }

  const reset = async (event: FormEvent<HTMLFormElement>, user: AdminUser) => {
    event.preventDefault()
    if (resetPassword.length < 12) return
    setBusy(`reset:${user.id}`)
    setError('')
    setNotice('')
    try {
      await resetAdminUserPassword(adminId, user.id, resetPassword)
      setResetUserId(null)
      setResetPassword('')
      setNotice(`账号“${user.username}”的密码已重置，旧登录会话已失效。`)
    } catch (cause) {
      setError(errorMessage(cause, '密码重置失败'))
    } finally {
      setBusy(null)
    }
  }

  const changeOwnPassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (newPassword.length < 12 || newPassword !== newPasswordConfirmation) return
    setBusy('change-own-password')
    setError('')
    setNotice('')
    try {
      await changePassword(currentPassword, newPassword)
      setChangeOpen(false)
      setCurrentPassword('')
      setNewPassword('')
      setNewPasswordConfirmation('')
      setNotice('你的密码已修改，其他设备上的旧登录会话已退出。')
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'CURRENT_PASSWORD_INVALID') setError('当前密码不正确，请重新输入。')
      else if (cause instanceof ApiError && cause.code === 'PASSWORD_UNCHANGED') setError('新密码不能与当前密码相同。')
      else setError(errorMessage(cause, '密码修改失败'))
    } finally {
      setBusy(null)
    }
  }

  const deactivate = async (user: AdminUser) => {
    if (!window.confirm(`停用账号“${user.username}”后，该账号会立即退出且无法继续登录。确定停用吗？`)) return
    setBusy(`deactivate:${user.id}`)
    setError('')
    setNotice('')
    try {
      await deactivateAdminUser(adminId, user.id)
      setUsers((current) => current.map((item) => item.id === user.id ? { ...item, isActive: false } : item))
      setNotice(`账号“${user.username}”已停用。`)
    } catch (cause) {
      setError(errorMessage(cause, '账号停用失败'))
    } finally {
      setBusy(null)
    }
  }

  const addBrand = async (event: FormEvent) => {
    event.preventDefault(); setBusy('brand'); setError(''); setNotice('')
    try {
      const brand = await createAccountBrand(adminId, brandName.trim())
      setBrands((current) => [...current, brand]); setBrandName(''); setBrandId(brand.id)
      setNotice(`品牌“${brand.name}”已创建。`)
      setCreateOpen(null)
    } catch (cause) { setError(errorMessage(cause, '品牌创建失败')) }
    finally { setBusy(null) }
  }

  const filtered = users.filter((user) => user.username.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())
    && (!filterBrand || (filterBrand === 'unassigned' ? !user.brandId : user.brandId === filterBrand))
    && (!filterState || user.isActive === (filterState === 'active')))
  const pageOffset = Math.min(offset, Math.max(0, Math.ceil(filtered.length / limit) - 1) * limit)
  const openCreate = (kind: 'account' | 'brand') => { setNotice(''); setError(''); setCreateOpen(kind) }
  const closeCreate = () => { if (busy === 'create' || busy === 'brand') return; setCreateOpen(null); setPassword(''); setError('') }
  return <div className="account-workspace">
    <nav className="account-tabs" role="tablist" aria-label="品牌与账号">
      {accountTabs.map(({ value, label, icon: Icon }, index) => <button key={value} id={`account-tab-${value}`} role="tab" type="button" aria-selected={tab === value} aria-controls={`account-panel-${value}`} tabIndex={tab === value ? 0 : -1} onClick={() => setTab(value)} onKeyDown={(event) => {
        const next = event.key === 'ArrowRight' ? (index + 1) % accountTabs.length : event.key === 'ArrowLeft' ? (index + accountTabs.length - 1) % accountTabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? accountTabs.length - 1 : null
        if (next === null) return
        event.preventDefault(); setTab(accountTabs[next].value); document.getElementById(`account-tab-${accountTabs[next].value}`)?.focus()
      }}><Icon size={17} />{label}</button>)}
    </nav>
    <div id={`account-panel-${tab}`} role="tabpanel" aria-labelledby={`account-tab-${tab}`}>
    {tab === 'usage' ? <><AdminUsagePanel actor={actor} users={users} brands={brands} />{platform ? <div className="account-pricing"><button className="secondary-button" type="button" aria-expanded={pricingOpen} onClick={() => setPricingOpen((v) => !v)}>全站单价</button>{pricingOpen ? <GlobalUsagePricingForm actorId={adminId} /> : null}</div> : null}</> : null}
    {tab === 'events' ? <AccountEventsPanel actorId={adminId} users={users} brands={brands} /> : null}
    {tab === 'accounts' ? <section className="settings-section account-management-section">
    <div className="settings-section-heading">
      <div><h2>账号管理</h2><p>共 {users.length} 个账号 · {brands.length} 个品牌</p></div>
      <div className="account-toolbar-actions"><button className="icon-button" type="button" aria-label="刷新列表" title="刷新列表" disabled={busy !== null} onClick={() => void load()}><RefreshCw className={busy === 'load' ? 'spin' : ''} size={16} /></button>{platform ? <button type="button" className="secondary-button" disabled={busy !== null} onClick={() => openCreate('brand')}><Plus size={16} />创建品牌</button> : null}<button className="primary-button" type="button" disabled={busy !== null} onClick={() => openCreate('account')}><Plus size={16} />创建账号</button></div>
    </div>
    <div className="account-policy-fields account-list-filters">
      <label>搜索账号<input type="search" value={search} placeholder="输入账号名称" onChange={(e) => { setSearch(e.target.value); setOffset(0) }} /></label>
      <label>品牌<select value={filterBrand} onChange={(e) => { setFilterBrand(e.target.value); setOffset(0) }}><option value="">全部品牌</option><option value="unassigned">未分配品牌</option>{brands.map((brand) => <option key={brand.id} value={brand.id}>{brand.displayName ?? brand.name}</option>)}</select></label>
      <label>账号状态<select value={filterState} onChange={(e) => { setFilterState(e.target.value); setOffset(0) }}><option value="">全部状态</option><option value="active">正常</option><option value="inactive">已停用</option></select></label>
    </div>
    <FoundationDrawer open={createOpen !== null} title={createOpen === 'brand' ? '创建品牌' : '创建账号'} onClose={closeCreate}>
    {createOpen === 'brand' ? <form className="brand-create-form" onSubmit={(event) => void addBrand(event)}><label htmlFor="new-brand-name">新品牌名称<input id="new-brand-name" value={brandName} onChange={(e) => setBrandName(e.target.value)} maxLength={80} required /></label><button type="submit" className="primary-button" disabled={busy !== null || !brandName.trim()}>创建品牌</button></form> : <form className="account-create-form" onSubmit={(event) => void create(event)}>
      <label htmlFor="new-account-username"><span>登录账号</span><input id="new-account-username" name="username" value={username} onChange={(event) => setUsername(event.target.value)} minLength={1} maxLength={80} autoComplete="off" spellCheck={false} placeholder="例如：designer-01" required /><small>不能包含空格，创建后不可修改</small></label>
      <label htmlFor="new-account-password"><span>临时密码</span><input id="new-account-password" name="new-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={12} maxLength={512} autoComplete="new-password" placeholder="至少 12 个字符" required /><small>请通过可信方式交给成员</small></label>
      {platform ? <label>所属品牌<select value={brandId} onChange={(e) => setBrandId(e.target.value)}><option value="">未分配（个人隔离）</option>{brands.map((brand) => <option key={brand.id} value={brand.id}>{brand.displayName ?? brand.name}</option>)}</select><small>作为生图、生视频和剪辑交接的品牌参考</small></label> : null}
      <label>角色<select value={role} onChange={(e) => setRole(e.target.value as AdminUser['role'])}>{Object.entries(accountRoleLabels).filter(([key]) => key !== 'ADMIN').map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      {platform ? <label className="account-brand-admin-check"><input type="checkbox" checked={brandAdmin} onChange={(e) => setBrandAdmin(e.target.checked)} />品牌管理员</label> : null}
      <button className="primary-button" type="submit" disabled={busy !== null || !username.trim() || password.length < 12}>{busy === 'create' ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />}{busy === 'create' ? '正在创建…' : '创建账号'}</button>
    </form>}
    {error ? <div className="inline-alert inline-alert--danger" role="alert">{error}</div> : null}
    </FoundationDrawer>
    <div className="account-feedback" aria-live="polite">
      {notice ? <p><CheckCircle2 size={15} />{notice}</p> : null}
      {error && !createOpen ? <div className="inline-alert inline-alert--danger" role="alert"><AlertTriangle size={16} />{error}</div> : null}
    </div>
    <div className="account-list" aria-busy={busy === 'load'}>
      {filtered.slice(pageOffset, pageOffset + limit).map((user) => <article className={`account-row${user.isActive ? '' : ' is-inactive'}`} key={user.id}>
        <span className="account-avatar" aria-hidden="true"><UserRound size={18} /></span>
        <div className="account-identity"><strong>{user.username}</strong><span>{user.isBrandAdmin ? '品牌管理员' : accountRoleLabels[user.role]} · {brands.find((b) => b.id === user.brandId)?.displayName ?? brands.find((b) => b.id === user.brandId)?.name ?? '未分配品牌'} · 创建于 {formatTime(user.createdAt)}</span><span>每日 USD：{user.dailyBudgetUsd == null ? '未设阈值' : `$${user.dailyBudgetUsd}`}</span></div>
        <span className={`account-state${user.isActive ? ' is-active' : ''}`}>{user.isActive ? '正常' : '已停用'}</span>
        {user.id === adminId && user.isActive && sharedAuthentication ? <div className="account-actions">
          <button className="secondary-button" type="button" disabled={busy !== null} onClick={() => { setChangeOpen(true); setResetUserId(null); setCurrentPassword(''); setNewPassword(''); setNewPasswordConfirmation(''); setNotice(''); setError('') }}><KeyRound size={15} />修改密码</button>
        </div> : null}
        {user.role !== 'ADMIN' && user.id !== adminId && (platform || !user.isBrandAdmin) ? <div className="account-actions">
          <button className="secondary-button" type="button" disabled={busy !== null} onClick={() => setEditing(editing === user.id ? null : user.id)}>权限与预算</button>
          {user.isActive ? <>
          <button className="secondary-button" type="button" disabled={busy !== null} onClick={() => { setResetUserId(user.id); setResetPassword(''); setNotice(''); setError('') }}><KeyRound size={15} />重置密码</button>
          <button className="danger-button" type="button" disabled={busy !== null} onClick={() => void deactivate(user)}>{busy === `deactivate:${user.id}` ? <LoaderCircle className="spin" size={15} /> : <LogOut size={15} />}停用</button>
          </> : null}
        </div> : null}
        {editing === user.id ? <AccountPolicyForm user={user} actorId={adminId} platform={platform} brands={brands} onClose={() => setEditing(null)} onSaved={(updated) => { setUsers((current) => current.map((u) => u.id === updated.id ? updated : u)); setEditing(null); setNotice(`账号“${updated.username}”的权限与预算已保存。`) }} /> : null}
        {resetUserId === user.id ? <form className="account-reset-form" onSubmit={(event) => void reset(event, user)}>
          <label htmlFor={`reset-password-${user.id}`}><span>为“{user.username}”设置新密码</span><input id={`reset-password-${user.id}`} type="password" value={resetPassword} onChange={(event) => setResetPassword(event.target.value)} minLength={12} maxLength={512} autoComplete="new-password" placeholder="至少 12 个字符" required /></label>
          <div><button className="secondary-button" type="button" disabled={busy !== null} onClick={() => { setResetUserId(null); setResetPassword('') }}>取消</button><button className="primary-button" type="submit" disabled={busy !== null || resetPassword.length < 12}>{busy === `reset:${user.id}` ? <LoaderCircle className="spin" size={15} /> : <KeyRound size={15} />}确认重置</button></div>
        </form> : null}
        {user.id === adminId && changeOpen ? <form className="account-reset-form account-change-password-form" onSubmit={(event) => void changeOwnPassword(event)}>
          <label htmlFor="current-account-password"><span>当前密码</span><input id="current-account-password" type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} maxLength={512} autoComplete="current-password" required /></label>
          <label htmlFor="new-account-password-self"><span>新密码</span><input id="new-account-password-self" type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} minLength={12} maxLength={512} autoComplete="new-password" placeholder="至少 12 个字符" required /></label>
          <label htmlFor="confirm-account-password-self"><span>确认新密码</span><input id="confirm-account-password-self" type="password" value={newPasswordConfirmation} onChange={(event) => setNewPasswordConfirmation(event.target.value)} minLength={12} maxLength={512} autoComplete="new-password" aria-invalid={Boolean(newPasswordConfirmation && newPassword !== newPasswordConfirmation)} required /></label>
          <div><button className="secondary-button" type="button" disabled={busy !== null} onClick={() => { setChangeOpen(false); setCurrentPassword(''); setNewPassword(''); setNewPasswordConfirmation('') }}>取消</button><button className="primary-button" type="submit" disabled={busy !== null || !currentPassword || newPassword.length < 12 || newPassword !== newPasswordConfirmation}>{busy === 'change-own-password' ? <LoaderCircle className="spin" size={15} /> : <KeyRound size={15} />}保存新密码</button></div>
        </form> : null}
      </article>)}
      {!filtered.length && busy !== 'load' ? <EmptyState icon={<UserRound size={24} />} title={users.length ? '没有符合条件的账号' : '还没有成员账号'} description={users.length ? '当前筛选条件下暂无记录。' : '暂无成员账号。'} /> : null}
      {busy === 'load' ? <div className="account-loading"><LoaderCircle className="spin" size={20} />正在读取成员账号…</div> : null}
    </div>
    <ReportPagination label="账号列表" offset={pageOffset} limit={limit} total={filtered.length} busy={busy === 'load'} onPage={(value) => { setOffset(value); setEditing(null); setResetUserId(null) }} onLimit={(value) => { setLimit(value); setOffset(0) }} />
  </section> : null}
  </div></div>
}
