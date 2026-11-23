import { describe, expect, it } from 'vitest'
import type { AccountBrand } from '../../types'
import { accountBrandOptions, equivalentBrandIds, selectableBrands } from './brandOptions'

const brands: AccountBrand[] = [
  { id: 'legacy', name: '暗黑', displayName: '暗黑 / NW / NerdyWave', shortName: 'NW', fullName: 'NerdyWave', siteProfileId: 'nerdywave' },
  { id: 'nw', name: 'NW', displayName: 'NW / NerdyWave', shortName: 'NW', fullName: 'NerdyWave', siteProfileId: 'nerdywave' },
  { id: 'pw', name: 'PW', displayName: 'PW / PageWings', shortName: 'PW', fullName: 'PageWings', siteProfileId: 'pagewings' },
  { id: 'y2k', name: 'y2k', displayName: 'y2k/CherryKitten', shortName: 'y2k', fullName: 'CherryKitten', siteProfileId: 'cherrykitten' },
]

describe('canonical brand selectors and historical identity', () => {
  it('shows one NerdyWave and independently shows CherryKitten', () => {
    expect(selectableBrands(brands).map((item) => item.id)).toEqual(['nw', 'pw', 'y2k'])
    expect(equivalentBrandIds(brands, 'nw').sort()).toEqual(['legacy', 'nw'])
    expect(equivalentBrandIds(brands, 'y2k')).toEqual(['y2k'])
  })
  it('does not silently change an account assigned to a historical alias', () => {
    expect(accountBrandOptions(brands).map((item) => item.id)).not.toContain('legacy')
    expect(accountBrandOptions(brands, 'legacy').map((item) => item.id)).toContain('legacy')
    expect(accountBrandOptions(brands, 'nw').map((item) => item.id)).not.toContain('legacy')
  })
  it('keeps unknown or independent brands separate', () => {
    const data = [...brands, { id: 'unknown', name: 'Special' }]
    expect(selectableBrands(data)).toHaveLength(4)
    expect(equivalentBrandIds(data, 'unknown')).toEqual(['unknown'])
  })
})
