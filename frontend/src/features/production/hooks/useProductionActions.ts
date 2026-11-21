import { errorMessage } from '../../../errorPresentation'
// 本文件由 App.tsx 拆分生成（2026-09-21 前端架构拆分 v1）。
// 逐字搬运 App() 中的生产动作编排：改单、加载更早记录、复刻素材、一键复用、
// 图片中心视频交接、播放、新建任务回填。API 调用与顺序完全不变。
import { useCallback, useRef, useState } from 'react'
import { confirmTask, fetchAssetFile, getTask, getTaskPage, reuseTask } from '../../../api'
import { variantDisplayName } from '../../../domain/taskPresentation'
import { createImageCenterVideoSeed, getImageCenterVideoStoryboardLimit } from '../../../imageCenterVideoSeed'
import { resolveImageCenterReferences } from '../../../imageCenterReferences'
import type { Task, Variant, VariantKind } from '../../../types'
import { createUuid } from '../../../uuid'
import type { ImageCenterVideoHandoff } from '../../image-center/ImageCenter'
import { imageCenterOutputFile } from '../productionTypes'
import type { RemixSeed } from '../productionTypes'

type Options = {
  detailId: string | null
  nextTaskCursor: string | null
  loadingMoreTasks: boolean
  setLoadingMoreTasks: (value: boolean) => void
  setNextTaskCursor: (value: string | null) => void
  setTasks: React.Dispatch<React.SetStateAction<Task[]>>
  refreshTasks: () => Promise<void>
  setError: (message: string) => void
  navigate: (nextView: 'images' | 'make' | 'records' | 'library' | 'admin' | 'settings', nextDetailId?: string | null) => void
}

export function useProductionActions({
  detailId,
  nextTaskCursor,
  loadingMoreTasks,
  setLoadingMoreTasks,
  setNextTaskCursor,
  setTasks,
  refreshTasks,
  setError,
  navigate,
}: Options) {
  const [video, setVideo] = useState<{ artifactId: string; title: string } | null>(null)
  const [seed, setSeed] = useState<RemixSeed | null>(null)
  const [imageComposerSeed, setImageComposerSeed] = useState<RemixSeed | null>(null)
  const [remixStage, setRemixStage] = useState<'materials' | 'reuse' | null>(null)
  const pendingImageVideoSeedsRef = useRef<RemixSeed[]>([])
  const pendingImageSelectionClearRef = useRef<(() => void) | null>(null)

  const taskChanged = useCallback(async () => {
    if (detailId) {
      try {
        const changed = await getTask(detailId)
        setTasks((current) => current.map((task) => task.id === changed.id ? changed : task))
        return
      } catch { /* 下面的轮询刷新是兜底。 */ }
    }
    await refreshTasks()
  }, [detailId, refreshTasks, setTasks])

  const loadMoreTasks = useCallback(async () => {
    if (!nextTaskCursor || loadingMoreTasks) return
    setLoadingMoreTasks(true)
    try {
      const page = await getTaskPage(nextTaskCursor)
      setTasks((current) => {
        const existingIds = new Set(current.map((task) => task.id))
        return [...current, ...page.items.filter((task) => !existingIds.has(task.id))]
      })
      setNextTaskCursor(page.nextCursor)
    } catch (cause) {
      setError(errorMessage(cause, '无法加载更早记录'))
    } finally {
      setLoadingMoreTasks(false)
    }
  }, [loadingMoreTasks, nextTaskCursor, setError, setLoadingMoreTasks, setNextTaskCursor, setTasks])

  const remix = async (task: Task) => {
    setRemixStage('materials')
    setError('')
    setSeed(null)
    navigate('make')
    window.scrollTo({ top: 0, behavior: 'auto' })
    try {
      const assets = [...task.assets].sort((a, b) => a.order - b.order)
      const loaded = await Promise.all(assets.map(async (asset) => ({ asset, file: await fetchAssetFile(task.workflowKind === 'DEEP_VIDEO' ? `/api/v1/assets/${asset.id}/content` : asset.thumbnailUrl, asset.originalFilename) })))
      if (task.workflowKind === 'DEEP_VIDEO') {
        const reference = loaded.find(({ asset }) => asset.role === 'REFERENCE_VIDEO')
        if (!reference) throw new Error('原任务的参考视频不可用，请重新上传。')
        setSeed({
          id: createUuid(), storyboards: [], products: [],
          deepVideo: {
            reference: reference.file,
            referenceVideoType: task.settings?.referenceVideoType === 'depth' ? 'depth' : 'original',
            images: loaded.filter(({ asset }) => asset.role === 'PRODUCT').map(({ file }) => file),
            background: loaded.find(({ asset }) => asset.role === 'BACKGROUND_REFERENCE')?.file,
            mode: task.settings?.deepVideoMode === 'depth' ? 'depth' : 'recreate',
            prompt: task.userRequirement,
            aspectRatio: task.settings?.aspectRatio === '16:9' ? '16:9' : '9:16',
            siteProfileId: task.siteProfileId ?? undefined,
          },
        })
        navigate('make')
        return
      }
      setSeed({
        id: createUuid(),
        storyboards: loaded.filter(({ asset }) => asset.role !== 'PRODUCT' && asset.usedForGeneration !== false).map(({ file }) => file),
        products: loaded.filter(({ asset }) => asset.role === 'PRODUCT' || asset.usedForGeneration === false).map(({ file }) => file),
        requirement: '',
        siteProfileId: task.siteProfileId ?? undefined,
      })
    } catch (cause) { setError(errorMessage(cause, '无法加载原素材')) } finally { setRemixStage(null) }
  }

  const prepareDepthVideo = async (task: Task) => {
    const downloadId = task.depthVideoResult?.downloadId ?? (
      task.settings?.deepVideoMode === 'depth' ? task.variants.find((variant) => variant.downloadArtifactId)?.downloadArtifactId : null
    )
    if (!downloadId) return
    setRemixStage('materials')
    setError('')
    try {
      const reference = await fetchAssetFile(`/api/v1/downloads/${downloadId}`, `${task.title}-depth.mp4`)
      setSeed({ id: createUuid(), storyboards: [], products: [], deepVideo: {
        reference, referenceVideoType: 'depth', mode: 'recreate', images: [],
        prompt: task.settings?.deepVideoMode === 'recreate' ? task.userRequirement : '',
        aspectRatio: task.settings?.aspectRatio === '16:9' ? '16:9' : '9:16',
        siteProfileId: task.siteProfileId ?? undefined,
      } })
      navigate('make')
    } catch (cause) { setError(errorMessage(cause, '无法读取深度视频，请重新选择或上传。')) }
    finally { setRemixStage(null) }
  }

  const reuseCompleted = async (task: Task, variants: VariantKind[]) => {
    if (!variants.length) return
    setRemixStage('reuse')
    setError('')
    try {
      const reused = await reuseTask(task, variants)
      setTasks((current) => [reused, ...current.filter((item) => item.id !== reused.id)])
      navigate('make', reused.id)
      try {
        const confirmed = await confirmTask(reused)
        setTasks((current) => current.map((item) => item.id === confirmed.id ? confirmed : item))
      } catch (cause) {
        try {
          const changed = await getTask(reused.id)
          setTasks((current) => current.map((item) => item.id === changed.id ? changed : item))
        } catch { /* 全局任务轮询仍是兜底。 */ }
        throw cause
      }
    } catch (cause) {
      setError(errorMessage(cause, '一键复用失败，请稍后重试'))
    } finally {
      setRemixStage(null)
    }
  }

  const prepareImageCenterVideos = async (handoffs: ImageCenterVideoHandoff[], onSubmitted: () => void) => {
    if (!handoffs.length) return
    setError('')
    try {
      const seeds = await Promise.all(handoffs.map(async ({ task, outputs }) => {
        const storyboardLimit = getImageCenterVideoStoryboardLimit(task)
        if (outputs.length > storyboardLimit) {
          throw new Error(`“${task.outfitName}”选择的分镜超过 ${storyboardLimit} 张，请减少分镜后再送入视频队列。`)
        }
        const restoredTask = await resolveImageCenterReferences(task)
        const storyboards = await Promise.all(outputs.map((output, index) => imageCenterOutputFile(output, task.outfitName, index)))
        return createImageCenterVideoSeed(restoredTask, storyboards, outputs.map((output) => output.id), createUuid())
      }))
      pendingImageVideoSeedsRef.current = seeds.slice(1)
      pendingImageSelectionClearRef.current = onSubmitted
      setImageComposerSeed(seeds[0])
    } catch (cause) {
      throw new Error(errorMessage(cause, '无法准备图片中心的视频素材'), { cause })
    }
  }

  const play = (task: Task, variant: Variant) => {
    if (!variant.downloadArtifactId) { navigate('make', task.id); return }
    setVideo({ artifactId: variant.downloadArtifactId, title: `${task.title} · ${variantDisplayName(variant)}` })
  }

  const created = useCallback((task: Task, open = false) => {
    setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)])
    const nextSeed = pendingImageVideoSeedsRef.current.shift() ?? null
    setSeed(nextSeed)
    if (open) navigate('make', task.id)
    else if (nextSeed) navigate('make')
  }, [navigate, setTasks])

  const imageComposerCreated = useCallback((task: Task) => {
    setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)])
    pendingImageSelectionClearRef.current?.()
    pendingImageSelectionClearRef.current = null
    const nextSeed = pendingImageVideoSeedsRef.current.shift() ?? null
    setImageComposerSeed(nextSeed)
    if (task.phase === 'ANALYZING' && !nextSeed) navigate('make', task.id)
  }, [navigate, setTasks])

  const closeImageComposer = () => {
    pendingImageVideoSeedsRef.current = []
    pendingImageSelectionClearRef.current = null
    setImageComposerSeed(null)
  }

  const feedbackChanged = useCallback((changed: Task) => {
    setTasks((current) => current.map((task) => task.id === changed.id ? changed : task))
  }, [setTasks])

  return {
    video, setVideo,
    seed, setSeed,
    imageComposerSeed,
    remixStage,
    taskChanged,
    loadMoreTasks,
    remix,
    reuseCompleted,
    prepareDepthVideo,
    prepareImageCenterVideos,
    play,
    created,
    imageComposerCreated,
    closeImageComposer,
    feedbackChanged,
  }
}
