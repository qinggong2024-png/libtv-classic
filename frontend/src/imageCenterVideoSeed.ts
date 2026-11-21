import type { ImageAssetSet, ImageGenerationTask } from './imageCenterModel'
import {
  KLING_MAX_REFERENCES,
  subjectReferenceFilesFromImages,
  type SubjectReferenceFiles,
} from './subjectFiles'

export interface ImageCenterVideoSeed {
  id: string
  siteProfileId?: string
  storyboards: File[]
  storyboardOutputIds: string[]
  products: File[]
  subjectFiles: SubjectReferenceFiles
  sourceReferencesUnavailable?: boolean
}

export function getImageCenterVideoProductFiles(product: ImageAssetSet): File[] {
  const files = [product.front, product.back, product.side]
    .filter((file): file is File => Boolean(file))
  return files.filter((file, index) => files.indexOf(file) === index)
}

export function getImageCenterVideoStoryboardLimit(
  task: Pick<ImageGenerationTask, 'product' | 'referenceUrls'>,
): number {
  const productFiles = getImageCenterVideoProductFiles(task.product)
  const productCount = Math.max(productFiles.length, task.referenceUrls?.product.length ?? 0)
  return Math.max(1, KLING_MAX_REFERENCES - productCount)
}

export function createImageCenterVideoSeed(
  task: Pick<ImageGenerationTask, 'product' | 'siteProfileId'>,
  storyboards: File[],
  storyboardOutputIds: string[],
  id: string,
): ImageCenterVideoSeed {
  const productFiles = getImageCenterVideoProductFiles(task.product)

  return {
    id,
    siteProfileId: task.siteProfileId,
    storyboards,
    storyboardOutputIds,
    products: productFiles,
    subjectFiles: subjectReferenceFilesFromImages(productFiles),
    ...(productFiles.length ? {} : { sourceReferencesUnavailable: true }),
  }
}
