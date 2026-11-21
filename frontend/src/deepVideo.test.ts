import { describe, expect, it } from 'vitest'
import { referenceVideoError } from './deepVideo'

describe('reference video preflight', () => {
  const metadata = (duration: number) => ({ duration, width: 720, height: 1280 })

  it('blocks submission until metadata can be decoded', () => {
    expect(referenceVideoError('recreate', null)).not.toBe('')
    expect(referenceVideoError('depth', metadata(Infinity))).not.toBe('')
    expect(referenceVideoError('depth', { ...metadata(6), width: 0 })).not.toBe('')
  })

  it('matches inclusive server duration limits without rounding the input', () => {
    for (const seconds of [4, 5.2, 15]) expect(referenceVideoError('recreate', metadata(seconds))).toBe('')
    for (const seconds of [3.999, 15.001]) expect(referenceVideoError('recreate', metadata(seconds))).toContain('4–15')
    for (const seconds of [0.1, 10.001, 600]) expect(referenceVideoError('depth', metadata(seconds))).toBe('')
    for (const seconds of [0, 600.001]) expect(referenceVideoError('depth', metadata(seconds))).not.toBe('')
  })
})
