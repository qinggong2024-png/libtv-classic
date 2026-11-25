import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import { SubjectInputPanel } from './SubjectInputPanel'
import { createEmptySubjectReferenceFiles } from './subjectFiles'
import { fillSubjectDropSlots, isSubjectPanelDropExcluded } from './subjectPanelDrop'
import { swapSubjectReferenceFiles } from './subjectUploadReorder'

const baseProps = {
  onModeChange: vi.fn(),
  uploadedFiles: createEmptySubjectReferenceFiles(),
  onUploadedFilesChange: vi.fn(),
  selectedElementId: '',
  onSelectedElementIdChange: vi.fn(),
  storyboardReferenceCount: 5,
}

describe('SubjectInputPanel', () => {
  it('disables history Element selection for CLI models that do not serialize Elements', () => {
    const markup = renderToStaticMarkup(<SubjectInputPanel
      {...baseProps}
      mode="none"
      elements={[]}
      allowHistorySelection={false}
    />)
    expect(markup).toMatch(/disabled=""[^>]*>[^<]*<svg/)
    expect(markup).toContain('当前 CLI 模型不支持历史 Element 主体')

    const staleSelection = renderToStaticMarkup(<SubjectInputPanel
      {...baseProps}
      mode="history"
      elements={[]}
      allowHistorySelection={false}
    />)
    expect(staleSelection).toContain('请改用上传参考图')
  })

  it('offers older subject pages without hiding the already loaded subjects', () => {
    const markup = renderToStaticMarkup(<SubjectInputPanel
      {...baseProps}
      mode="history"
      elements={[]}
      hasMoreElements
      onLoadMoreElements={vi.fn()}
    />)
    expect(markup).toContain('加载更早的历史主体')
    const pending = renderToStaticMarkup(<SubjectInputPanel
      {...baseProps}
      mode="history"
      elements={[]}
      hasMoreElements
      loadingMoreElements
      onLoadMoreElements={vi.fn()}
    />)
    expect(pending).toContain('加载中')
    expect(pending).toContain('disabled')
  })

  it('swaps front and back files when their previews are dragged between slots', () => {
    const front = new File(['front'], 'front.png', { type: 'image/png' })
    const back = new File(['back'], 'back.png', { type: 'image/png' })
    const files = { frontalImage: front, backImage: back, detailImages: [] }

    expect(swapSubjectReferenceFiles(files, { slot: 'frontalImage' }, { slot: 'backImage' })).toEqual({
      frontalImage: back,
      backImage: front,
      detailImages: [],
    })
  })

  it('moves a side image into an empty front slot and preserves the side list', () => {
    const side = new File(['side'], 'side.png', { type: 'image/png' })
    const files = { frontalImage: null, backImage: null, detailImages: [side] }

    expect(swapSubjectReferenceFiles(files, { slot: 'detailImages', index: 0 }, { slot: 'frontalImage' })).toEqual({
      frontalImage: side,
      backImage: null,
      detailImages: [],
    })
  })

  it('fills empty subject slots in visual order for a panel-level drop', () => {
    const [front, back, side, detail] = ['front.png', 'back.png', 'side.png', 'detail.png']
      .map((name) => new File([name], name, { type: 'image/png' }))

    const result = fillSubjectDropSlots(createEmptySubjectReferenceFiles(), [front, back, side, detail])

    expect(result.acceptedCount).toBe(4)
    expect(result.files).toEqual({
      frontalImage: front,
      backImage: back,
      detailImages: [side, detail],
    })
  })

  it('preserves filled slots and rejects panel-level drop overflow', () => {
    const existingFront = new File(['front'], 'existing-front.png', { type: 'image/png' })
    const existingSide = new File(['side'], 'existing-side.png', { type: 'image/png' })
    const [back, detail, overflow] = ['back.png', 'detail.png', 'overflow.png']
      .map((name) => new File([name], name, { type: 'image/png' }))

    const result = fillSubjectDropSlots({
      frontalImage: existingFront,
      backImage: null,
      detailImages: [existingSide],
    }, [back, detail, overflow])

    expect(result.acceptedCount).toBe(2)
    expect(result.files).toEqual({
      frontalImage: existingFront,
      backImage: back,
      detailImages: [existingSide, detail],
    })
  })

  it('excludes nested controls and upload tiles from the panel-level drop target', () => {
    const closest = vi.fn().mockReturnValue({})
    const nestedControl = {
      closest,
    } as unknown as EventTarget
    const panelWhitespace = {
      closest: vi.fn().mockReturnValue(null),
    } as unknown as EventTarget

    expect(isSubjectPanelDropExcluded(nestedControl)).toBe(true)
    expect(closest).toHaveBeenCalledWith(expect.stringContaining('button'))
    expect(closest).toHaveBeenCalledWith(expect.stringContaining('.subject-upload-tile'))
    expect(isSubjectPanelDropExcluded(panelWhitespace)).toBe(false)
    expect(isSubjectPanelDropExcluded(null)).toBe(false)
  })

  it('offers one optional multi-image uploader without separate angle slots', () => {
    const markup = renderToStaticMarkup(<SubjectInputPanel {...baseProps} mode="none" elements={[]} />)
    expect(markup).toContain('产品参考图（选填）')
    expect(markup).toContain('用于保持产品外观、颜色、印花和结构一致')
    expect(markup).not.toContain('人物')
    expect(markup).toContain('从历史选择')
    expect(markup).toContain('拖拽或点击上传参考图')
    expect(markup).toContain('第一张为主参考，其余补充细节')
    expect(markup.match(/type="file"/g)).toHaveLength(1)
    expect(markup).toContain('multiple=""')
    expect(markup).not.toContain('subject-upload-grid')
    expect(markup).not.toContain('正面图')
    expect(markup).not.toContain('背面图')
    expect(markup).not.toContain('侧面 / 细节图')
    expect(markup).not.toContain('5/7')
    expect(markup).not.toContain('清除主体')
  })

  it('labels the V6 uploader exclusively as product references', () => {
    const front = new File(['front'], 'front.png', { type: 'image/png' })
    const markup = renderToStaticMarkup(<SubjectInputPanel
      {...baseProps}
      mode="upload"
      className="v6-subject"
      elements={[]}
      uploadedFiles={{ frontalImage: front, backImage: null, detailImages: [] }}
      onOpenProductHistory={vi.fn()}
    />)
    expect(markup).toContain('>产品参考图</h2>')
    expect(markup).toContain('用于保持产品外观、颜色、印花和结构一致')
    expect(markup).toContain('aria-label="产品参考图"')
    expect(markup).toContain('aria-label="已上传的产品参考图"')
    expect(markup).toContain('历史商品图')
    expect(markup).not.toContain('人物')
  })

  it('disables the shared uploader when the form is locked', () => {
    const markup = renderToStaticMarkup(<SubjectInputPanel {...baseProps} mode="upload" elements={[]} disabled />)
    expect(markup).toContain('class="upload-zone subject-unified-upload" disabled=""')
    expect(markup).toContain('aria-label="产品参考图"')
    expect(markup).not.toContain('frontal_image')
    expect(markup).not.toContain('refer_image')
  })

  it('renders local thumbnail containers for uploaded subject images', () => {
    const front = new File(['front'], 'front.png', { type: 'image/png' })
    const side = new File(['side'], 'side.png', { type: 'image/png' })
    const markup = renderToStaticMarkup(<SubjectInputPanel
      {...baseProps}
      mode="upload"
      uploadedFiles={{
        frontalImage: front,
        backImage: null,
        detailImages: [side],
      }}
      elements={[]}
    />)
    expect(markup).toContain('subject-reference-preview__image')
    expect(markup).toContain('aria-label="已上传的产品参考图"')
    expect(markup).not.toContain('人物')
    expect(markup).toContain('draggable="true"')
    expect(markup).toContain('主参考图预览：front.png')
    expect(markup).toContain('补充参考图 1预览：side.png')
    expect(markup).not.toContain('点击更换')
  })

  it('shows one shared uploader with all four references and prevents overflow', () => {
    const [front, back, side, detail] = ['front', 'back', 'side', 'detail'].map((name) => new File([name], `${name}.png`, { type: 'image/png' }))
    const markup = renderToStaticMarkup(<SubjectInputPanel {...baseProps} mode="upload" elements={[]} uploadedFiles={{ frontalImage: front, backImage: back, detailImages: [side, detail] }} />)
    expect(markup.match(/role="listitem"/g)).toHaveLength(4)
    expect(markup).toContain('class="upload-zone subject-unified-upload" disabled=""')
    expect(markup).toContain('移除主参考图')
    expect(markup).toContain('移除补充参考图 3')
  })

  it('renders usable history elements as thumbnail cards and hides unavailable entries', () => {
    const markup = renderToStaticMarkup(<SubjectInputPanel
      {...baseProps}
      mode="history"
      elements={[
        {
          id: 'ready-1',
          name: '黑色印花上衣',
          description: '正背面参考',
          status: 'active',
          imageUrls: ['front.jpg', 'back.jpg'],
        },
        {
          id: 'pending-1',
          name: '处理中主体',
          description: null,
          status: 'processing',
          imageUrls: [],
        },
      ]}
      selectedElementId="ready-1"
      elementsError="历史主体暂时无法刷新"
    />)
    expect(markup).toContain('subject-history-grid')
    expect(markup).toContain('subject-history-card is-selected')
    expect(markup).toContain('aria-pressed="true"')
    expect(markup).toContain('黑色印花上衣')
    expect(markup).not.toContain('处理中主体')
    expect(markup).not.toContain('<select')
    expect(markup).toContain('历史主体暂时无法刷新')
    expect(markup).not.toContain('6/7')
  })

  it('collapses history after six cards and exposes a more control', () => {
    const elements = Array.from({ length: 8 }, (_, index) => ({
      id: `ready-${index + 1}`,
      name: `主体 ${index + 1}`,
      description: null,
      status: 'active',
      imageUrls: [`subject-${index + 1}.jpg`],
    }))
    const markup = renderToStaticMarkup(<SubjectInputPanel
      {...baseProps}
      mode="history"
      elements={elements}
    />)
    expect(markup).toContain('主体 6')
    expect(markup).not.toContain('主体 7')
    expect(markup).toContain('查看更多（2）')
    expect(markup).toContain('aria-expanded="false"')
  })

  it.each(['succeed', 'ready', 'success', 'succeeded', 'completed', 'active'])(
    'allows a history element with the %s status',
    (status) => {
      const markup = renderToStaticMarkup(<SubjectInputPanel
        {...baseProps}
        mode="history"
        elements={[{
          id: `element-${status}`,
          name: `主体 ${status}`,
          description: null,
          status,
          imageUrls: [],
        }]}
        selectedElementId={`element-${status}`}
      />)
      expect(markup).toContain(`主体 ${status}`)
      expect(markup).toContain('aria-pressed="true"')
      expect(markup).not.toContain('暂不能用于生成')
    },
  )

  it('does not present a local placeholder job as a selectable history card', () => {
    const markup = renderToStaticMarkup(<SubjectInputPanel
      {...baseProps}
      mode="history"
      elements={[{
        id: 'local-job:123',
        name: '正在创建的主体',
        description: null,
        status: 'succeed',
        imageUrls: [],
        raw: { localJob: true },
      }]}
      selectedElementId="local-job:123"
    />)
    expect(markup).not.toContain('subject-history-card')
    expect(markup).toContain('暂无可用历史主体')
    expect(markup).toContain('暂不能用于生成')
  })

  it('keeps loading and empty history states accessible', () => {
    const loadingMarkup = renderToStaticMarkup(<SubjectInputPanel
      {...baseProps}
      mode="history"
      elements={[]}
      elementsLoading
    />)
    expect(loadingMarkup).toContain('role="status"')
    expect(loadingMarkup).toContain('正在加载历史主体')

    const emptyMarkup = renderToStaticMarkup(<SubjectInputPanel
      {...baseProps}
      mode="history"
      elements={[]}
    />)
    expect(emptyMarkup).toContain('暂无可用历史主体')
  })
})
