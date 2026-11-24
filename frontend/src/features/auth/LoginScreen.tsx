import { AlertTriangle, ArrowRight, Eye, EyeOff, LoaderCircle } from 'lucide-react'
import { type FormEvent, useEffect, useState } from 'react'
import { ApiError, login } from '../../api'
import { ProductLogo } from '../../shared/components/ProductLogo'
import './loginScreen.css'

function loginErrorMessage(cause: unknown): string {
  if (!(cause instanceof ApiError)) return '暂时连不上网站，请检查网络后重试'
  if (cause.status === 401) return '账号或密码不正确，请重新输入'
  if (cause.status === 408) return '网站回应时间太长，请稍后重新登录'
  if (cause.status === 409) return '网站还没有开启账号登录，请联系管理员'
  if (cause.status >= 500) return '网站暂时没有正常回应，请稍后再试'
  return cause.message || '登录失败，请稍后重试'
}

export function LoginScreen({ onAuthenticated }: { onAuthenticated: () => Promise<void> }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    document.body.classList.add('login-open')
    return () => document.body.classList.remove('login-open')
  }, [])
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await login(username.trim(), password)
      await onAuthenticated()
    } catch (cause) {
      setError(loginErrorMessage(cause))
    } finally { setBusy(false) }
  }
  return <main className="login-view allwe-login">
    <header className="allwe-login__header"><ProductLogo /><span className="allwe-login__edition">经典版</span></header>
    <section className="allwe-login__shell" aria-label="ALLWE 广告视频素材中心账号登录">
      <aside className="allwe-login__brand">
        <div className="allwe-login__brand-copy">
          <img className="allwe-login__hero-logo" src="/brand/allwe-logo.png" width={1918} height={477} alt="ALLWE" />
          <h1><span>广告视频</span><span>素材中心</span></h1>
          <p>图片与视频制作工作台</p>
          <div className="allwe-login__rule" aria-hidden="true" />
          <div className="allwe-login__workflow"><span>图片中心</span><span aria-hidden="true">/</span><span>视频制作</span><span aria-hidden="true">/</span><span>内容库</span></div>
        </div>
      </aside>
      <div className="allwe-login__form-column">
      <form className="allwe-login__form" onSubmit={(event) => void submit(event)} aria-busy={busy} aria-labelledby="login-heading">
        <div className="allwe-login__heading"><span>账号登录</span><h2 id="login-heading">登录工作台</h2><p>使用管理员分配的账号进入工作台。</p></div>
        <div className="allwe-login__field">
          <label htmlFor="login-username">账号</label>
          <span className="allwe-login__input"><input id="login-username" name="username" value={username} onChange={(event) => { setUsername(event.target.value); setError('') }} placeholder="请输入账号" autoComplete="username" autoCapitalize="none" spellCheck={false} disabled={busy} aria-invalid={Boolean(error)} aria-describedby={error ? 'login-error' : undefined} autoFocus required /></span>
        </div>
        <div className="allwe-login__field">
          <label htmlFor="login-password">密码</label>
          <span className="allwe-login__input allwe-login__input--password"><input id="login-password" name="password" type={showPassword ? 'text' : 'password'} value={password} onChange={(event) => { setPassword(event.target.value); setError('') }} placeholder="请输入密码" autoComplete="current-password" disabled={busy} aria-invalid={Boolean(error)} aria-describedby={error ? 'login-error' : undefined} required /><button type="button" className="allwe-login__password-toggle" onClick={() => setShowPassword((current) => !current)} disabled={busy} aria-label={showPassword ? '隐藏密码' : '显示密码'} aria-pressed={showPassword} title={showPassword ? '隐藏密码' : '显示密码'}>{showPassword ? <EyeOff size={18} /> : <Eye size={18} />}</button></span>
        </div>
        <div className="allwe-login__feedback" aria-live="polite">
          {error ? <div id="login-error" className="allwe-login__error" role="alert"><AlertTriangle size={17} aria-hidden="true" />{error}</div> : null}
        </div>
        <button className="allwe-login__submit" type="submit" disabled={busy || !username.trim() || !password}>{busy ? <LoaderCircle className="spin" size={18} aria-hidden="true" /> : null}<span>{busy ? '正在验证账号' : '登录工作台'}</span>{busy ? null : <ArrowRight size={17} aria-hidden="true" />}</button>
        <p className="allwe-login__account-note">账号由管理员统一管理</p>
      </form>
      </div>
    </section>
    <footer className="allwe-login__footer"><span>ALLWE · 广告视频素材中心</span><span>图片与视频制作工作台</span></footer>
  </main>
}
