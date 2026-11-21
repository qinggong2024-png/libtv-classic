import { describe, expect, it } from 'vitest'
import type { Task } from '../../types'
import { depthVideoHistoryEntries } from './depthVideoHistory'

describe('depth video history selection', () => {
  it('includes intermediates and depth-only outputs, excluding final replacement videos', () => {
    const task = (id: string, settings: object, result: object | null, artifact: string | null) => ({
      id, workflowKind: 'DEEP_VIDEO', settings, depthVideoResult: result,
      variants: [{ downloadArtifactId: artifact }],
    }) as unknown as Task
    const entries = depthVideoHistoryEntries([
      task('intermediate', { deepVideoMode: 'recreate' }, { downloadId: 'depth' }, 'final'),
      task('depth-only', { deepVideoMode: 'depth' }, null, 'extracted'),
      task('replacement-only', { deepVideoMode: 'recreate' }, null, 'final-only'),
      task('unfinished', { deepVideoMode: 'depth' }, null, null),
    ])
    expect(entries.map(({ downloadId }) => downloadId)).toEqual(['depth', 'extracted'])
  })
})
