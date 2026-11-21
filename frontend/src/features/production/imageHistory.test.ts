import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchImageAssetFile } from '../../api'
import type { ImageGenerationOutput, ImageGenerationTask } from '../../imageCenterModel'
import { imageHistoryEntries, prepareHistoryImages } from './imageHistory'

vi.mock('../../api', () => ({ fetchImageAssetFile: vi.fn() }))
const image = () => new File(['image'], 'reference.png', { type: 'image/png' })
const output = (id: string, values: Partial<ImageGenerationOutput> = {}): ImageGenerationOutput => ({
  id, kind: 'fusion', view: 'front', label: id, file: null, url: `/images/${id}`, qualificationStatus: 'QUALIFIED', ...values,
})
const task = (createdAt: string, outputs: ImageGenerationOutput[]) => ({ createdAt, outfitName: '产品 A', outputs }) as ImageGenerationTask
beforeEach(() => vi.clearAllMocks())

describe('reference images from generation history', () => {
  it('shows recent results first, keeps review states and omits unavailable or duplicate outputs', () => {
    const old = output('old')
    const newest = output('new', { qualificationStatus: 'PENDING' })
    const missing = output('missing', { url: '' })
    expect(imageHistoryEntries([task('2026-10-01', [old, missing]), task('2026-10-15', [newest, old])]).map(({ output }) => output.id)).toEqual(['new', 'old'])
  })

  it('preserves selection order and source output IDs across local and authenticated images', async () => {
    const local = image()
    vi.mocked(fetchImageAssetFile).mockResolvedValue(image())
    const entries = [output('remote', { downloadUrl: '/downloads/source' }), output('local', { file: local })].map((output) => ({ output, outfitName: '产品 A' }))
    const prepared = await prepareHistoryImages(entries)
    expect(prepared.map((item) => item.sourceImageOutputId)).toEqual(['remote', 'local'])
    expect(prepared[1].file).toBe(local)
    expect(fetchImageAssetFile).toHaveBeenCalledExactlyOnceWith('/downloads/source', '产品 A-remote')
  })

  it('accepts pending, rejected and legacy results without a review write', async () => {
    vi.mocked(fetchImageAssetFile).mockResolvedValue(image())
    const sources = ['PENDING', 'REJECTED', undefined].map((qualificationStatus, index) => output(`source-${index}`, { qualificationStatus: qualificationStatus as ImageGenerationOutput['qualificationStatus'] }))
    const prepared = await prepareHistoryImages(sources.map((output) => ({ output, outfitName: '产品 A' })))
    expect(prepared.map((item) => item.sourceImageOutputId)).toEqual(sources.map((item) => item.id))
    expect(sources.map((item) => item.qualificationStatus)).toEqual(['PENDING', 'REJECTED', undefined])
  })

  it('rejects empty or oversized saved images and surfaces authenticated fetch errors', async () => {
    for (const file of [new File([], 'empty.png', { type: 'image/png' }), new File([new Uint8Array(10485761)], 'large.png', { type: 'image/png' })]) {
      await expect(prepareHistoryImages([{ output: output('invalid', { file }), outfitName: '产品 A' }])).rejects.toThrow('10 MB')
    }
    vi.mocked(fetchImageAssetFile).mockRejectedValue(new Error('图片已失效'))
    await expect(prepareHistoryImages([{ output: output('gone'), outfitName: '产品 A' }])).rejects.toThrow('图片已失效')
  })
})
