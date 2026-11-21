import { describe, expect, it } from 'vitest'
import { accountSiteSelection } from './accountSiteSelection'

const sites = [
  { id: 'pagewings', name: 'PageWings', version: '1', siteUrl: 'https://www.pagewings.com/' },
  { id: 'guidingcross', name: 'GuidingCross', version: '1', siteUrl: 'https://www.guidingcross.com/' },
]

describe('account brand selection', () => {
  it('uses the assigned brand before a previous selection or seed', () => {
    expect(accountSiteSelection({ sites, defaultProfileId: 'pagewings' }, 'guidingcross')).toBe('pagewings')
  })

  it('leaves unassigned accounts empty until they choose a brand', () => {
    const choices = { sites, defaultProfileId: null }
    expect(accountSiteSelection(choices)).toBe('')
    expect(accountSiteSelection({ sites: sites.slice(0, 1), defaultProfileId: null })).toBe('')
    expect(accountSiteSelection(choices, 'guidingcross')).toBe('guidingcross')
  })

  it('rejects stale or inaccessible brand selections', () => {
    expect(accountSiteSelection({ sites: [], defaultProfileId: 'pagewings' }, 'pagewings')).toBe('')
    expect(accountSiteSelection({ sites, defaultProfileId: null }, 'removed')).toBe('')
  })
})
