import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { HomeView } from './HomeView'

vi.mock('../production/ProductionComposer', () => ({
  ProductionComposer: () => <div data-testid="image-composer" />,
}))
vi.mock('../production/VideoRecreationPanel', () => ({
  VideoRecreationPanel: () => <div data-testid="video-remix" />,
}))

describe('creation-only video studio', () => {
  it('keeps both production modes without task lists or empty states', () => {
    const html = renderToStaticMarkup(<HomeView active seed={null} system={null} actorId={null} onCreated={() => {}} />)
    expect(html).toContain('data-testid="image-composer"')
    expect(html).toContain('data-testid="video-remix"')
    expect(html).toContain('image-to-video-tab')
    expect(html).toContain('video-remix-tab')
    expect(html).not.toMatch(/attention-panel|background-work|results-grid|home-section|task-center-refresh-error/)
  })
})
