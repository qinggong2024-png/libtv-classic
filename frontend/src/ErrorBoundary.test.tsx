import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import { ErrorBoundary, ErrorFallback } from './ErrorBoundary'
import { haveResetKeysChanged } from './errorBoundaryReset'

describe('ErrorBoundary', () => {
  it('turns a rendering error into its recoverable error state', () => {
    const error = new Error('broken provider response')

    expect(ErrorBoundary.getDerivedStateFromError(error)).toEqual({ error })
  })

  it('detects navigation changes so a failed view can recover automatically', () => {
    expect(haveResetKeysChanged(['admin', 'providers'], ['admin', 'overview'])).toBe(true)
    expect(haveResetKeysChanged(['admin', 'providers'], ['admin', 'providers'])).toBe(false)
    expect(haveResetKeysChanged([], ['make'])).toBe(true)
  })

  it('renders retry and back actions instead of a blank page', () => {
    const markup = renderToStaticMarkup(<ErrorFallback onRetry={vi.fn()} onBack={vi.fn()} backLabel="返回制作页" />)

    expect(markup).toContain('role="alert"')
    expect(markup).toContain('此页面暂时无法显示')
    expect(markup).toContain('<h2>此页面暂时无法显示</h2>')
    expect(markup).toContain('重试')
    expect(markup).toContain('PAGE_RENDER')
    expect(renderToStaticMarkup(<ErrorFallback onRetry={vi.fn()} error={new TypeError('Failed to fetch dynamically imported module')} />)).toContain('MODULE_LOAD')
    expect(renderToStaticMarkup(<ErrorFallback onRetry={vi.fn()} error={new TypeError('Failed to fetch dynamically imported module')} />)).toContain('重新加载页面')
    expect(markup).toContain('返回制作页')
  })
})
