import { afterEach, expect, it, vi } from 'vitest'
import { saveAsset } from './api'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it.each([
  [null, 'edited-result.mp4'],
  ['attachment; filename="PMO-TT261114AA01.mp4"', 'PMO-TT261114AA01.mp4'],
  ["attachment; filename*=UTF-8''PMO-TT261114AA02.mp4", 'PMO-TT261114AA02.mp4'],
  ["attachment; filename*=UTF-8''%ZZ", 'edited-result.mp4'],
])('uses the server filename %s and keeps the blob alive for Chromium', async (header, expected) => {
  vi.useFakeTimers()
  vi.stubGlobal('window', globalThis)
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new Blob(['video'], { type: 'video/mp4' }), {
    headers: header ? { 'Content-Disposition': header } : {},
  })))
  const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test-video')
  const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  let attached = false
  const anchor = { href: '', download: '', hidden: false, click: vi.fn(() => {
    expect(attached).toBe(true)
    expect(anchor.download).toBe(expected)
    expect(revoke).not.toHaveBeenCalled()
  }), remove: vi.fn() }
  vi.stubGlobal('document', { createElement: () => anchor, body: { appendChild: () => { attached = true } } })
  await saveAsset('/api/v1/cutflow-handoffs/id/outputs/result', 'edited-result.mp4')
  expect(create).toHaveBeenCalledOnce()
  expect(anchor.click).toHaveBeenCalledOnce()
  expect(anchor.remove).toHaveBeenCalledOnce()
  expect(revoke).not.toHaveBeenCalled()
  vi.advanceTimersByTime(60_000)
  expect(revoke).toHaveBeenCalledWith('blob:test-video')
})
