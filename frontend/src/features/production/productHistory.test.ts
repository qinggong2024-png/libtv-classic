import { describe, expect, it } from 'vitest'
import type { Asset, Task } from '../../types'
import { productHistoryEntries, sameReferenceFile } from './productHistory'

const asset = (id: string, role = 'PRODUCT', sha256 = id, filename = id + '.png', order = 101) =>
  ({ id, role, sha256, originalFilename: filename, order, thumbnailUrl: '/api/v1/assets/' + id + '/thumbnail' } as Asset)
const task = (id: string, date: string, siteProfileId: string | null, assets: Asset[], title = id) =>
  ({ id, createdAt: date, siteProfileId, assets, title } as Task)

describe('historical original reference images', () => {
  const older = task('old', '2026-11-20T01:00:00Z', 'pw', [asset('old-front', 'PRODUCT', 'hash'), asset('old-back')])
  const newer = task('new', '2026-11-24T01:00:00Z', 'pw', [asset('new-front', 'PRODUCT', 'hash', 'PW-101-front.png'), asset('shot', 'STORYBOARD'), asset('scene', 'BACKGROUND_REFERENCE'), asset('video', 'PRODUCT', 'video', 'test.mp4')], 'PW-101')
  const crossBrand = task('gc', '2026-11-24T02:00:00Z', 'gc', [asset('gc-product')])
  const unknownBrand = task('unknown', '2026-11-24T02:00:00Z', null, [asset('unknown-product')])
  const tasks = [older, crossBrand, unknownBrand, newer]

  it('includes only explicit same-brand product uploads, newest first, deduplicated by content hash', () => {
    expect(productHistoryEntries(tasks, 'pw').map(entry => entry.asset.id)).toEqual(['new-front', 'old-back'])
    expect(productHistoryEntries(tasks, 'gc').map(entry => entry.asset.id)).toEqual(['gc-product'])
    expect(productHistoryEntries(tasks, '')).toEqual([])
  })
  it('searches original names and task titles, not supplier subjects', () => {
    expect(productHistoryEntries(tasks, 'pw', 'PW-101').map(entry => entry.asset.id)).toEqual(['new-front'])
    expect(productHistoryEntries(tasks, 'pw', 'old-back').map(entry => entry.asset.id)).toEqual(['old-back'])
    expect(productHistoryEntries(tasks, 'pw', 'scene')).toEqual([])
  })
  it('does not confuse different bytes with the same file name and detects identical content under different names', async () => {
    const a = new File(['123'], 'same.png', { type: 'image/png' })
    const b = new File(['456'], 'same.png', { type: 'image/png' })
    const c = new File(['123'], 'different.png', { type: 'image/png' })
    expect(await sameReferenceFile(a, b)).toBe(false)
    expect(await sameReferenceFile(a, c)).toBe(true)
  })
})
