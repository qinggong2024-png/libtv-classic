import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createVideoTask } from './api'
import type { Task, UploadResult } from './types'

const uploads = [{ id: 'reference-one' } as UploadResult]
const base = {
  generationMode: 'automatic' as const,
  elementIds: [],
  resultCount: 1,
  durationSeconds: 10,
  actionIntensity: 'standard' as const,
  cutMode: 'explicit_cuts' as const,
}

beforeEach(() => {
  vi.stubGlobal('window', globalThis)
  vi.stubGlobal('fetch', vi.fn(async () => new Response(
    JSON.stringify({ id: 'created-task', settings: {} } as Task),
    { status: 201, headers: { 'Content-Type': 'application/json' } },
  )))
})

afterEach(() => vi.unstubAllGlobals())

describe('director mode request persistence contract', () => {
  it.each(['reference_guided', 'cross_scene_v2'] as const)('submits directorMode %s to the backend', async (mode) => {
    await createVideoTask('真实编排模式测试', uploads, { ...base, directorMode: mode })
    const [url, options] = vi.mocked(fetch).mock.calls[0]
    const payload = JSON.parse(String(options?.body))
    expect(url).toBe('/api/v1/video-tasks')
    expect(payload.generationMode).toBe('automatic')
    expect(payload.directorMode).toBe(mode)
    expect(payload.storyboards).toEqual([{ uploadId: 'reference-one', order: 1, required: true }])
  })

  it('does not submit directorMode when using direct custom prompts', async () => {
    await createVideoTask('自定义', uploads, {
      ...base, generationMode: 'custom', customPrompt: 'Show the supplied garment',
      directorMode: 'cross_scene_v2',
    })
    const payload = JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))
    expect(payload.generationMode).toBe('custom')
    expect(payload).not.toHaveProperty('directorMode')
  })
})
