import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { UploadStatus, type FileUploadState } from './UploadStatus'

describe('upload validation status', () => {
  it.each([
    ['preview', '本地预览 · 待后台校验'],
    ['uploading', '正在上传并校验'],
    ['validated', '已上传 · 后台校验通过'],
    ['failed', '上传未完成 · 查看错误原因'],
    ['cancelled', '已中止 · 尚未通过校验'],
  ] as const)('labels %s accurately', (phase, label) => {
    const markup = renderToStaticMarkup(<UploadStatus state={{ phase }} />)
    expect(markup).toContain(label)
    expect(markup).toContain('role="status"')
    if (phase !== 'validated') expect(markup).not.toContain('后台校验通过')
  })
  it('renders failure details as escaped text', () => {
    const state: FileUploadState = { phase: 'failed', message: '<img src=x onerror=alert(1)>' }
    const markup = renderToStaticMarkup(<UploadStatus state={state} />)
    expect(markup).not.toContain('<img')
    expect(markup).toContain('&lt;img')
  })
})
