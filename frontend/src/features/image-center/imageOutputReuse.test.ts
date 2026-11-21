import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchImageAssetFile } from '../../api'
import type { ImageGenerationOutput } from '../../imageCenterModel'
import { parseImageOutputDrag, prepareImageOutputReference } from './imageOutputReuse'

vi.mock('../../api', () => ({ fetchImageAssetFile: vi.fn() }))

const output: ImageGenerationOutput = {
  id: 'result', label: '正面融合图', kind: 'fusion', view: 'front', file: null,
  url: '/api/v1/downloads/preview', downloadUrl: '/api/v1/downloads/original',
  qualificationStatus: 'PENDING',
}

describe('reuse image outputs for creation', () => {
  beforeEach(() => vi.resetAllMocks())

  it('reads only scoped task/output identifiers from a drag and rejects malformed data', () => {
    expect(parseImageOutputDrag(JSON.stringify({ taskId: 'task', outputId: 'result', url: 'https://untrusted.invalid' })))
      .toEqual({ taskId: 'task', outputId: 'result' })
    for (const value of ['', '{', 'null', '[]', '"image"', '{"taskId":1,"outputId":"result"}', '{"taskId":"task","outputId":""}']) {
      expect(parseImageOutputDrag(value)).toBeNull()
    }
  })

  it('reuses a local pending or rejected image for editing without changing its review', async () => {
    const file = new File(['image'], 'result.png', { type: 'image/png' })
    for (const qualificationStatus of ['PENDING', 'REJECTED'] as const) {
      const source = { ...output, file, qualificationStatus }
      expect(await prepareImageOutputReference(source)).toBe(file)
      expect(source.qualificationStatus).toBe(qualificationStatus)
    }
    expect(fetchImageAssetFile).not.toHaveBeenCalled()
  })

  it('fetches the authenticated original with a preview URL fallback', async () => {
    const file = new File(['image'], 'original.webp', { type: 'image/webp' })
    vi.mocked(fetchImageAssetFile).mockResolvedValue(file)
    expect(await prepareImageOutputReference(output)).toBe(file)
    expect(fetchImageAssetFile).toHaveBeenLastCalledWith(output.downloadUrl, output.label)
    await prepareImageOutputReference({ ...output, downloadUrl: null })
    expect(fetchImageAssetFile).toHaveBeenLastCalledWith(output.url, output.label)
  })

  it('rejects unavailable, empty, or unsupported files and propagates download failures', async () => {
    await expect(prepareImageOutputReference({ ...output, downloadUrl: '', url: '' })).rejects.toThrow('暂无可用文件')
    await expect(prepareImageOutputReference({ ...output, file: new File([], 'empty.png', { type: 'image/png' }) })).rejects.toThrow('非空')
    vi.mocked(fetchImageAssetFile).mockResolvedValue(new File(['html'], 'error.html', { type: 'text/html' }))
    await expect(prepareImageOutputReference(output)).rejects.toThrow('JPG、PNG 或 WebP')
    vi.mocked(fetchImageAssetFile).mockRejectedValue(new Error('Unavailable'))
    await expect(prepareImageOutputReference(output)).rejects.toThrow('Unavailable')
  })
})
