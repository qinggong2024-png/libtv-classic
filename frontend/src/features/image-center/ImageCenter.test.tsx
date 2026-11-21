import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ImageOutputCompare, OutputCard } from './ImageCenter'
import type { ImageGenerationOutput } from '../../imageCenterModel'
import { changeImageComparison, imageComparisonIds } from './imageComparison'

const output: ImageGenerationOutput = {
  id: 'output-1', kind: 'fusion', view: 'front', label: '正面结果',
  file: null, url: '', qualificationStatus: 'PENDING',
}

describe('image selection without manual review', () => {
  const pendingOutput = (id: string): ImageGenerationOutput => ({
    ...output,
    id,
    label: `结果 ${id}`,
    qualificationStatus: 'PENDING',
  })

  it('shows selection without a manual review step', () => {
    const markup = renderToStaticMarkup(<OutputCard taskId="task-1" output={output} selected onToggle={() => {}} onPreview={() => {}} />)
    expect(markup).toContain('aria-label="取消选择正面结果视频素材"')
    expect(markup).not.toContain('aria-label="审核正面结果"')
    expect(markup).not.toContain('待人工审核')
    expect(markup).not.toContain('已合格')
  })


  it('provides a keyboard-accessible editing action without changing selection or review controls', () => {
    const markup = renderToStaticMarkup(<OutputCard taskId="task-1" output={{ ...output, url: '/api/v1/downloads/result' }} selected={false} onToggle={() => {}} onPreview={() => {}} onReuse={() => {}} reusing />)
    expect(markup).toContain('aria-label="继续编辑正面结果"')
    expect(markup).toContain('正在载入')
    expect(markup).toContain('拖到“批量创作”继续编辑')
    expect(markup).not.toContain('待人工审核')
    expect(markup).toContain('aria-label="选择正面结果用于制作视频"')
  })


  it('renders independent left/right controls and task sources without generation actions', () => {
    const markup = renderToStaticMarkup(<ImageOutputCompare outputs={[pendingOutput('left'), pendingOutput('right')]} onClose={() => {}} />)
    expect(markup).toContain('并排比较')
    expect(markup).toContain('左侧图片')
    expect(markup).toContain('右侧图片')
    expect(markup).toContain('来源：')
    expect(markup).toContain('视频选片及顺序保持不变')
    expect(markup.match(/image-compare-panel__meta/g)).toHaveLength(2)
    expect(markup).not.toContain('保存')
  })

  it('compares without video selection and displays distinct sources for identical output labels', () => {
    const markup = renderToStaticMarkup(<ImageOutputCompare
      outputs={['a', 'b', 'c'].map((id) => ({ ...pendingOutput(id), label: '正面结果' }))}
      sources={{ a: '产品 A · 批次 1', b: '产品 B · 批次 2', c: '产品 C · 批次 3' }}
      onClose={() => {}}
    />)
    expect(markup.match(/<select/g)).toHaveLength(2)
    expect(markup).toContain('来源：产品 A · 批次 1')
    expect(markup).toContain('来源：产品 B · 批次 2')
    expect(markup).toContain('产品 C · 批次 3')
  })

  it('initializes from video order while ignoring missing and duplicate selection IDs', () => {
    const outputs = ['a', 'b', 'c'].map(pendingOutput)
    const selection = Object.freeze(['missing', 'c', 'c', 'a'])
    expect(imageComparisonIds(outputs, selection)).toEqual(['c', 'a'])
    expect(imageComparisonIds(outputs, [])).toEqual(['a', 'b'])
    expect(imageComparisonIds([outputs[1]], ['a', 'b'])).toEqual(['b'])
    expect(imageComparisonIds([], selection)).toEqual([])
    expect(selection).toEqual(['missing', 'c', 'c', 'a'])
  })

  it('changes either comparison side, swapping collisions without mutating video selection', () => {
    const selection = Object.freeze(['a', 'b', 'c'])
    const ids = imageComparisonIds(selection.map(pendingOutput), selection)
    expect(changeImageComparison(ids, 0, 'c')).toEqual(['c', 'b'])
    expect(changeImageComparison(ids, 1, 'c')).toEqual(['a', 'c'])
    expect(changeImageComparison(ids, 0, 'b')).toEqual(['b', 'a'])
    expect(changeImageComparison(ids, 1, 'a')).toEqual(['b', 'a'])
    expect(ids).toEqual(['a', 'b'])
    expect(selection).toEqual(['a', 'b', 'c'])
  })
})
