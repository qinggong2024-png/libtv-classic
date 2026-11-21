import { describe, expect, it } from 'vitest'

import {
  createImageCenterVideoSeed,
  getImageCenterVideoStoryboardLimit,
} from './imageCenterVideoSeed'

function image(name: string): File {
  return new File(['image'], name, { type: 'image/jpeg' })
}

describe('image center video seed', () => {
  it('carries the source image brand into video production', () => {
    const product = image('product.jpg')
    const seed = createImageCenterVideoSeed(
      { product: { front: product, back: null, side: null }, siteProfileId: 'pagewings' },
      [product], ['output'], 'seed',
    )
    expect(seed.siteProfileId).toBe('pagewings')
    expect(seed).not.toHaveProperty('prompt')
  })
  it('hands off selected images without carrying image prompts into video production', () => {
    const productFront = image('product-front.jpg')
    const productBack = image('product-back.jpg')
    const productSide = image('product-side.jpg')
    const storyboard = image('storyboard.jpg')
    const seed = createImageCenterVideoSeed(
      { product: { front: productFront, back: productBack, side: productSide } },
      [storyboard],
      ['output-1'],
      'video-seed',
    )

    expect(seed).toEqual({
      id: 'video-seed',
      storyboards: [storyboard],
      storyboardOutputIds: ['output-1'],
      products: [productFront, productBack, productSide],
      subjectFiles: {
        frontalImage: productFront,
        backImage: productBack,
        detailImages: [productSide],
      },
    })
    expect(seed).not.toHaveProperty('requirement')
    expect(seed).not.toHaveProperty('prompt')
  })

  it('reserves provider reference slots for every product image', () => {
    expect(getImageCenterVideoStoryboardLimit({ product: { front: image('front'), back: null, side: null } })).toBe(6)
    expect(getImageCenterVideoStoryboardLimit({ product: { front: image('front'), back: image('back'), side: null } })).toBe(5)
    expect(getImageCenterVideoStoryboardLimit({ product: { front: image('front'), back: image('back'), side: image('side') } })).toBe(4)
  })

  it('does not duplicate the same product file across reference slots', () => {
    const product = image('product.jpg')
    const seed = createImageCenterVideoSeed(
      { product: { front: product, back: product, side: null } },
      [image('storyboard.jpg')],
      ['output-1'],
      'video-seed',
    )

    expect(seed.products).toEqual([product])
    expect(seed.subjectFiles).toEqual({
      frontalImage: product,
      backImage: null,
      detailImages: [],
    })
  })

  it('leaves subject references empty when a historical task has no product image', () => {
    const storyboard = image('storyboard.jpg')
    const seed = createImageCenterVideoSeed(
      { product: { front: null, back: null, side: null } },
      [storyboard],
      ['output-1'],
      'video-seed',
    )

    expect(seed.products).toEqual([])
    expect(seed.subjectFiles).toEqual({
      frontalImage: null,
      backImage: null,
      detailImages: [],
    })
    expect(seed.storyboards).toEqual([storyboard])
    expect(getImageCenterVideoStoryboardLimit({ product: { front: null, back: null, side: null } })).toBe(7)
  })
})
