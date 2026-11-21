import { afterEach, describe, expect, it, vi } from 'vitest'
import { readAppLocation } from './useAppNavigation'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('records navigation state', () => {
  it.each(['cutflow-ready', 'cutflow-editing', 'cutflow-finished'])('opens legacy %s links in the task list', (filter) => {
    vi.stubGlobal('window', { location: { search: `?view=records&filter=${filter}&q=shirt` } })
    expect(readAppLocation()).toMatchObject({ view: 'records', recordsFilter: 'all', recordsQuery: 'shirt' })
  })
  it.each(['cutflow-ready', 'cutflow-editing', 'cutflow-finished'])('redirects old editing links to the task list', (filter) => {
    vi.stubGlobal('window', { location: { search: `?view=cutflow&filter=${filter}&q=shirt&source=variant-2` } })
    expect(readAppLocation()).toMatchObject({ view: 'records', recordsFilter: 'all', recordsQuery: 'shirt' })
  })
  it.each(['', '&filter=unknown', '&filter=done'])('opens the task list for an editing URL with %s', (suffix) => {
    vi.stubGlobal('window', { location: { search: `?view=cutflow${suffix}` } })
    expect(readAppLocation()).toMatchObject({ view: 'records', recordsFilter: suffix === '&filter=done' ? 'done' : 'all' })
  })
  it('does not open an editing source when viewing generation records', () => {
    vi.stubGlobal('window', { location: { search: '?view=records&source=variant-2' } })
    expect(readAppLocation()).toMatchObject({ view: 'records' })
  })

  it('reads a task and variant focus from a task detail URL', () => {
    vi.stubGlobal('window', { location: { search: '?task=task-1&variant=variant-2' } })
    expect(readAppLocation()).toMatchObject({ view: 'make', detailId: 'task-1', focusVariantId: 'variant-2' })
  })
  it('reads only supported filter and query values from the URL', () => {
    vi.stubGlobal('window', { location: { search: '?view=records&filter=failed&q=coat%20test' } })
    expect(readAppLocation()).toMatchObject({
      view: 'records',
      recordsFilter: 'failed',
      recordsQuery: 'coat test',
    })
  })

  it('falls back to all records for invalid filters', () => {
    vi.stubGlobal('window', { location: { search: '?view=records&filter=unknown&q=term' } })
    expect(readAppLocation()).toMatchObject({
      view: 'records',
      recordsFilter: 'all',
      recordsQuery: 'term',
    })
  })
})
