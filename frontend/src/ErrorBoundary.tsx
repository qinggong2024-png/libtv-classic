import { AlertTriangle, ArrowLeft, RefreshCw } from 'lucide-react'
import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { haveResetKeysChanged } from './errorBoundaryReset'
import type { ErrorBoundaryResetKey } from './errorBoundaryReset'
import { currentFrontendBuild, isModuleLoadError } from './errorBoundaryRecovery'

type ErrorBoundaryProps = {
  children: ReactNode
  resetKeys?: readonly ErrorBoundaryResetKey[]
  onBack?: () => void
  backLabel?: string
  fullPage?: boolean
}

type ErrorBoundaryState = {
  error: Error | null
}

export function ErrorFallback({
  onRetry,
  onBack,
  backLabel = '返回上一页',
  fullPage = false,
  error = null,
}: {
  onRetry: () => void
  error?: Error | null
  onBack?: () => void
  backLabel?: string
  fullPage?: boolean
}) {
  const Heading = fullPage ? 'h1' : 'h2'
  const moduleLoadFailure = isModuleLoadError(error)
  const errorType = moduleLoadFailure ? 'MODULE_LOAD' : 'PAGE_RENDER'
  return <div className={`error-fallback${fullPage ? ' error-fallback--full-page' : ''}`} role="alert">
    <span className="error-fallback__icon"><AlertTriangle size={24} /></span>
    <div>
      <Heading>{fullPage ? '页面暂时无法打开' : '此页面暂时无法显示'}</Heading>
      <p>{moduleLoadFailure
        ? '页面资源可能因版本更新或网络中断而加载失败。请先确认已保存当前制作内容，再点击“重新加载页面”。'
        : '页面发生运行异常。可尝试重试或返回制作页；如果反复出现，请将下方错误信息提供给管理员。'}</p>
      <p className="error-fallback__diagnostic">错误类型：{errorType} · 页面版本：{currentFrontendBuild()}</p>
    </div>
    <div className="error-fallback__actions">
      <button className="primary-button" type="button" onClick={onRetry}><RefreshCw size={16} />{moduleLoadFailure ? '重新加载页面' : '重试'}</button>
      {onBack ? <button className="secondary-button" type="button" onClick={onBack}><ArrowLeft size={16} />{backLabel}</button> : null}
    </div>
  </div>
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('React page error boundary caught an error', error, info.componentStack)
  }

  componentDidUpdate(previousProps: ErrorBoundaryProps) {
    if (this.state.error && haveResetKeysChanged(previousProps.resetKeys, this.props.resetKeys)) {
      this.setState({ error: null })
    }
  }

  private retry = () => {
    if (isModuleLoadError(this.state.error)) {
      window.location.reload()
      return
    }
    this.setState({ error: null })
  }

  render() {
    if (this.state.error) {
      return <ErrorFallback
        onRetry={this.retry}
        onBack={this.props.onBack}
        backLabel={this.props.backLabel}
        fullPage={this.props.fullPage}
        error={this.state.error}
      />
    }
    return this.props.children
  }
}
