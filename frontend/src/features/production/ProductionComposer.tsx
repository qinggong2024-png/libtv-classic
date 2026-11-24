import { errorMessage, userFacingError } from '../../errorPresentation'
// 本文件由 App.tsx 拆分生成（2026-09-28 前端架构拆分 v2）。
// 代码为逐字搬运，未经逻辑改写。
import { AlertTriangle, ArrowRight, Check, CheckCircle2, CircleAlert, Images, LoaderCircle, LockKeyhole, Plus, Trash2, WandSparkles } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, createVideoTask, getSiteStyleProfiles, getTask, getVideoTemplates, querySubmissionRecovery, uploadStoryboard, type SubmissionRecoveryResult } from '../../api'
import { mapWithConcurrency } from '../../concurrency'
import { type SiteStyleProfileSummary, type SystemStatus, type Task, type VideoTemplateSummary } from '../../types'
import { CustomPromptField } from '../../CustomPromptField'
import { SubjectInputPanel } from '../../SubjectInputPanel'
import { ProductHistoryPicker } from './ProductHistoryPicker'
import { fillSubjectDropSlots } from '../../subjectPanelDrop'
import { IMAGE_FILE_ACCEPT, isSupportedImageFile } from '../../imageFiles'
import { type BatchUploadGroup, groupBatchUploadFiles } from '../../batchUpload'
import { type SubjectInputMode, type SubjectReferenceFiles, buildSubjectImageReferences, createEmptySubjectReferenceFiles, getSubjectReferenceCount, subjectReferenceFilesFromImages, validateSubjectReferenceFiles } from '../../subjectFiles'
import { createUuid } from '../../uuid'
import { providerPresentation } from '../../providerPresentation'
import { initialVideoComposerPromptState, loadVideoComposerSettings, saveVideoComposerSettings } from '../../videoComposerSettings'
import { formatVideoDurationOptions, getVideoDurationOptions, normalizeVideoDuration } from '../../videoDurationOptions'
import { isRetiredShortMohairVideoCopy } from '../../promptMaterialPresets'
import { type Material, type RemixSeed, type UploadBucket, UPLOAD_CONCURRENCY } from './productionTypes'
import { MATERIAL_DRAG_TYPE, MaterialCard } from './MaterialCard'
import { formatTime } from '../../domain/taskPresentation'
import { ImagePreview, LocalImage } from '../../shared/components/Media'
import { UploadStatus, type FileUploadState } from '../../shared/components/UploadStatus'
import { GenerationCostBadge } from '../../shared/components/GenerationCostBadge'
import { AccountSiteField } from './CutflowSiteSelect'
import { useAccountSiteSelection } from '../../useAccountSiteSelection'
import { GenerationAdvancedSettings, GenerationBrief } from '../../shared/components/GenerationAdvancedSettings'
import { DirectorModeSwitch } from '../../shared/components/DirectorModeSwitch'
import './creationV6.css'
import '../../shared/components/creationPolish.css'

// Monotonic timing is used only inside user-initiated submission handlers.
function submissionTimestamp() { return performance.now() }

type ManualVideoDraft = {
  key: string
  materials: Material[]
  subjectFiles: SubjectReferenceFiles
  subjectMode: SubjectInputMode
}
type ComposerBatchGroup = BatchUploadGroup & { manual?: ManualVideoDraft }
type SubmissionGroup = {
  group: ComposerBatchGroup
  identity: { clientTaskId: string; idempotencyKey: string }
  status: 'pending' | 'uploading' | 'submitting' | 'failed' | 'unknown' | 'submitted'
  request?: Parameters<typeof createVideoTask>
  resourceId?: string
}
// Keep the unboxing implementation intact; hide it until production acceptance.
const ENABLE_UNBOXING_AUTOMATIC_GENERATION = false
const groupStatusLabels = { pending: '待提交', uploading: '上传中', submitting: '提交中', failed: '明确失败，可继续', unknown: '结果未知，先核对', submitted: '已创建' }
import { VIDEO_DISPLAY_FOCUSES, type VideoDisplayFocus } from '../../ootdCreativeControls'

export function ProductionComposer({ seed, onCreated, system, actorId }: {
  seed: RemixSeed | null
  onCreated: (task: Task, open?: boolean) => void
  system: SystemStatus | null
  actorId: string | null
}) {
  const [storedSettings] = useState(() => loadVideoComposerSettings(localStorage))
  const [initialPromptState] = useState(() => initialVideoComposerPromptState(
    seed?.requirement,
    storedSettings,
    isRetiredShortMohairVideoCopy,
  ))
  const [storyboards, setStoryboards] = useState<Material[]>(() => seed?.storyboards.map((file, index) => ({ id: createUuid(), file, sourceImageOutputId: seed.storyboardOutputIds?.[index] })) ?? [])
  const [batchGroups, setBatchGroups] = useState<ComposerBatchGroup[]>([])
  const [manualDrafts, setManualDrafts] = useState<ManualVideoDraft[]>([])
  const [manualKey, setManualKey] = useState(() => createUuid())
  const [groupProgress, setGroupProgress] = useState<SubmissionGroup[]>([])
  const [previewGroupKey, setPreviewGroupKey] = useState('')
  const [templates, setTemplates] = useState<VideoTemplateSummary[]>([])
  const [profiles, setProfiles] = useState<SiteStyleProfileSummary[]>([])
  const [templateId, setTemplateId] = useState(storedSettings.templateId)
  const { choices, error: siteError, siteProfileId, setSiteProfileId, defaultProfileId } = useAccountSiteSelection(seed?.siteProfileId)
  const [generationMode, setGenerationMode] = useState<'automatic' | 'custom'>(initialPromptState.generationMode)
  const [directorMode, setDirectorMode] = useState(storedSettings.directorMode)
  const [videoType, setVideoType] = useState<'ootd' | 'unboxing'>('ootd')
  const [creativeBrief, setCreativeBrief] = useState(initialPromptState.creativeBrief)
  const [customPrompt, setCustomPrompt] = useState(initialPromptState.customPrompt)
  const [subjectFiles, setSubjectFiles] = useState<SubjectReferenceFiles>(() => seed?.subjectFiles?.frontalImage
    ? seed.subjectFiles
    : subjectReferenceFilesFromImages(seed?.products ?? []))
  const [subjectMode, setSubjectMode] = useState<SubjectInputMode>(() => subjectFiles.frontalImage ? 'upload' : 'none')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [resultCount, setResultCount] = useState(storedSettings.resultCount)
  const [durationSeconds, setDurationSeconds] = useState(storedSettings.durationSeconds)
  // The Sol director now owns natural movement amplitude; do not retain hidden legacy overrides.
  const [actionIntensity, setActionIntensity] = useState<'low' | 'standard' | 'high'>('standard')
  const [cutMode, setCutMode] = useState<'explicit_cuts' | 'continuous_when_possible'>(storedSettings.cutMode)
  const [autoSubmitToCutflow, setAutoSubmitToCutflow] = useState(true)
  const [displayFocus, setDisplayFocus] = useState<VideoDisplayFocus>('overall')
  const [catalogLoading, setCatalogLoading] = useState(true)
  const [catalogError, setCatalogError] = useState('')
  const [busy, setBusy] = useState(false)
  const [stage, setStage] = useState('正在上传素材')
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [submittedNotice, setSubmittedNotice] = useState('')
  const [uncertainSubmission, setUncertainSubmission] = useState<{
    clientTaskId: string
    idempotencyKey: string
    result: SubmissionRecoveryResult | null
    checking: boolean
  } | null>(null)
  const [resetting, setResetting] = useState(false)
  const [fileUploadStates, setFileUploadStates] = useState<Map<File, FileUploadState>>(() => new Map())
  const setFileUploadState = (file: File, state: FileUploadState) => setFileUploadStates(current => new Map(current).set(file, state))
  const [preview, setPreview] = useState<{ file: File; label: string } | null>(null)
  const storyboardInputRef = useRef<HTMLInputElement>(null)
  const batchInputRef = useRef<HTMLInputElement>(null)
  const resetTimerRef = useRef<number | null>(null)
  const uploadAbortRef = useRef<AbortController | null>(null)
  const submissionIdentityRef = useRef<{ clientTaskId: string; idempotencyKey: string } | null>(null)
  const submissionGroupsRef = useRef<SubmissionGroup[]>([])
  const submittingRef = useRef(false)
  const videoGeneration = system?.videoGeneration ?? system?.kling
  const activeProvider = providerPresentation(videoGeneration?.provider)
  const activeModel = videoGeneration?.model || videoGeneration?.models[0] || activeProvider.displayName
  // Fixed-video task schema caps the combined image/Element budget at 7.
  const referenceLimit = Math.min(7, videoGeneration?.imageInputLimit ?? 0)
  const productReferencesSupported = videoGeneration?.supportsProductReferences !== false
  const storyboardLimit = Math.min(7, Math.max(1, referenceLimit || 7))
  const validBatchGroups = batchGroups.filter((group) => !group.errors.length)
  const storyboardCountSupported = storyboards.length <= storyboardLimit
    && validBatchGroups.every((group) => group.storyboards.length <= storyboardLimit)
  const durationOptions = useMemo(
    () => {
      const options = getVideoDurationOptions(videoGeneration, generationMode === 'automatic' ? 1 : storyboards.length)
      return generationMode === 'automatic' ? options.filter((duration) => duration >= 5 && duration <= 15) : options
    },
    [generationMode, storyboards.length, videoGeneration],
  )
  const durationSupported = durationOptions.length > 0
  const normalizedDurationSeconds = durationSupported ? normalizeVideoDuration(durationSeconds, durationOptions) : durationSeconds
  const durationOptionsLabel = durationSupported ? formatVideoDurationOptions(durationOptions) : '无可用时长'
  const providerAvailable = Boolean(videoGeneration?.available && videoGeneration.authenticated)
  const subjectValidation = validateSubjectReferenceFiles(subjectFiles)
  const subjectReferenceCount = getSubjectReferenceCount(subjectMode, subjectFiles, '')
  const subjectReady = subjectMode === 'none'
    || (subjectMode === 'upload' && subjectValidation.isValid && productReferencesSupported)
  const providerInputsSupported = validBatchGroups.length
    ? validBatchGroups.every((group) => group.storyboards.length + (group.manual ? getSubjectReferenceCount(group.manual.subjectMode, group.manual.subjectFiles, '') : Number(Boolean(group.productFront)) + Number(Boolean(group.productBack))) <= referenceLimit)
    : storyboards.length + subjectReferenceCount <= referenceLimit
  const selectedTemplate = templates.find((item) => item.id === templateId) ?? null
  const selectedProfile = profiles.find((item) => item.id === siteProfileId) ?? null
  const customPromptReady = generationMode !== 'custom' || Boolean(customPrompt.trim())
  const productionReady = selectedTemplate?.status === 'active'
    && Boolean(siteProfileId)
    && (generationMode === 'custom' || selectedProfile?.status === 'active')
  const templateAcceptsCount = Boolean(selectedTemplate && (validBatchGroups.length
    ? validBatchGroups.every((group) => group.storyboards.length >= selectedTemplate.minImages && group.storyboards.length <= selectedTemplate.maxImages)
    : storyboards.length >= selectedTemplate.minImages && storyboards.length <= selectedTemplate.maxImages))
  const batchMode = batchGroups.length > 0
  const batchStarted = batchMode && groupProgress.length > 0
  const batchComplete = batchStarted && groupProgress.every((item) => item.status === 'submitted')
  const remainingGroupCount = batchStarted ? groupProgress.filter((item) => item.status !== 'submitted').length : validBatchGroups.length
  const formLocked = busy || resetting || batchStarted
  const materialsLocked = formLocked || batchMode || uncertainSubmission !== null
  const previewGroup = batchGroups.find((group) => group.key === previewGroupKey) ?? validBatchGroups[0]
  const displayedStoryboards = batchMode && previewGroup
    ? previewGroup.storyboards.map((file, index) => ({ id: `${previewGroup.key}:${index}`, file }))
    : storyboards
  const displayedSubjectFiles = batchMode && previewGroup
    ? previewGroup.manual?.subjectFiles ?? { frontalImage: previewGroup.productFront, backImage: previewGroup.productBack, detailImages: [] }
    : subjectFiles
  const publishGroupProgress = () => setGroupProgress(submissionGroupsRef.current.map((item) => ({ ...item })))
  useEffect(() => {
    let active = true
    Promise.all([getVideoTemplates(), getSiteStyleProfiles()])
      .then(([nextTemplates, nextProfiles]) => {
        if (!active) return
        setTemplates(nextTemplates)
        setProfiles(nextProfiles)
        setTemplateId((current) => nextTemplates.some((item) => item.id === current && item.status === 'active') ? current : nextTemplates.find((item) => item.status === 'active')?.id ?? '')
        setCatalogError('')
      })
      .catch((cause) => {
        if (active) setCatalogError(errorMessage(cause, '模板与站点风格加载失败'))
      })
      .finally(() => { if (active) setCatalogLoading(false) })
    return () => { active = false }
  }, [])
  useEffect(() => {
    saveVideoComposerSettings(localStorage, {
      templateId,
      siteProfileId,
      generationMode,
      customPrompt,
      resultCount,
      durationSeconds: normalizedDurationSeconds,
      actionIntensity,
      cutMode,
      directorMode,
    })
  }, [actionIntensity, customPrompt, cutMode, directorMode, generationMode, normalizedDurationSeconds, resultCount, siteProfileId, templateId])
  useEffect(() => () => {
    if (resetTimerRef.current !== null) window.clearTimeout(resetTimerRef.current)
    uploadAbortRef.current?.abort()
  }, [])
  const currentManual: ManualVideoDraft = { key: manualKey, materials: storyboards, subjectFiles, subjectMode }
  const hasDraftMaterials = (item: ManualVideoDraft) => item.materials.length > 0 || getSubjectReferenceCount(item.subjectMode, item.subjectFiles, '') > 0
  const manualReady = (item: ManualVideoDraft) => {
    const count = item.materials.length
    const refs = getSubjectReferenceCount(item.subjectMode, item.subjectFiles, '')
    const subjectOk = item.subjectMode === 'none' || (item.subjectMode === 'upload' && productReferencesSupported && validateSubjectReferenceFiles(item.subjectFiles).isValid)
    return count > 0 && count <= storyboardLimit && count + refs <= referenceLimit && subjectOk && Boolean(selectedTemplate && count >= selectedTemplate.minImages && count <= selectedTemplate.maxImages)
      && (generationMode === 'automatic' || getVideoDurationOptions(videoGeneration, count).includes(normalizedDurationSeconds))
  }
  const manualItems = [...manualDrafts, currentManual].filter(hasDraftMaterials)
  const readyManualItems = manualItems.filter(manualReady)
  const loadManual = (item?: ManualVideoDraft) => {
    setManualKey(item?.key ?? createUuid())
    setStoryboards(item?.materials ?? [])
    setSubjectFiles(item?.subjectFiles ?? createEmptySubjectReferenceFiles())
    setSubjectMode(item?.subjectMode ?? 'none')
    setHistoryOpen(false)
    setFileUploadStates(new Map())
    setGroupProgress([])
    submissionGroupsRef.current = []
    setError('')
    setNotice('')
    setSubmittedNotice('')
  }
  const newManual = () => {
    if (materialsLocked || submittingRef.current) return
    if (hasDraftMaterials(currentManual)) setManualDrafts(items => [...items, currentManual])
    loadManual()
  }
  const selectManual = (item: ManualVideoDraft) => {
    if (materialsLocked || submittingRef.current || item.key === manualKey) return
    setManualDrafts(items => [...items.filter(row => row.key !== item.key), ...(hasDraftMaterials(currentManual) ? [currentManual] : [])])
    loadManual(item)
  }
  const removeManual = (key: string) => {
    if (materialsLocked || submittingRef.current) return
    if (key === manualKey) loadManual()
    else setManualDrafts(items => items.filter(item => item.key !== key))
  }
  const toManualGroup = (item: ManualVideoDraft): ComposerBatchGroup => ({
    key: (item.subjectFiles.frontalImage?.name ?? item.materials[0]?.file.name ?? '商品').replace(/\.[^.]+$/, '') + ' · ' + item.key.slice(0, 6), manual: item, storyboards: item.materials.map(m => m.file),
    productFront: item.subjectMode === 'upload' ? item.subjectFiles.frontalImage : null,
    productBack: item.subjectMode === 'upload' ? item.subjectFiles.backImage : null, errors: [],
  })
  const addFiles = (files: File[]) => {
    if (materialsLocked) return
    submissionGroupsRef.current = []
    setSubmittedNotice('')
    const images = files.filter(isSupportedImageFile)
    const rejectedCount = files.length - images.length
    const accepted = images.slice(0, Math.max(0, storyboardLimit - storyboards.length)).map((file) => ({ id: createUuid(), file }))
    const notices: string[] = []
    if (rejectedCount) notices.push(`${rejectedCount} 个文件不是支持的 JPG、PNG 或 WEBP 图片`)
    if (images.length !== accepted.length) notices.push(`当前模型的分镜图最多添加 ${storyboardLimit} 张，超出部分未添加`)
    setNotice(notices.join('；'))
    setStoryboards((items) => [...items, ...accepted])
  }
  const addBatchFiles = (files: File[]) => {
    if (formLocked || uncertainSubmission) return
    if (!files.length) return
    if (batchMode) { setNotice('请先退出当前批量再导入新素材。'); return }
    const result = groupBatchUploadFiles(files)
    if (!result.groups.length) { setNotice('未匹配命名规则，请检查文件名。'); return }
    if (result.groups.length && !batchMode && hasDraftMaterials(currentManual)) setManualDrafts(items => [...items, currentManual])
    setManualKey(createUuid())
    setHistoryOpen(false)
    submissionGroupsRef.current = []
    setGroupProgress([])
    setPreviewGroupKey('')
    setBatchGroups(result.groups)
    const valid = result.groups.filter((group) => !group.errors.length)
    if (valid.length) {
      previewBatchGroup(valid[0])
    } else {
      setStoryboards([])
      setSubjectMode('none')
      setSubjectFiles(createEmptySubjectReferenceFiles())
    }
    setNotice([
      valid.length ? `已识别 ${valid.length} 组素材，将批量创建 ${valid.length} 个任务` : '没有识别到完整素材组',
      result.rejected.length ? `${result.rejected.length} 个文件未匹配命名规则` : '',
      ...result.groups.filter((group) => group.errors.length).map((group) => `${group.key}：${group.errors.join('、')}`),
    ].filter(Boolean).join('；'))
  }
  const previewBatchGroup = (group: ComposerBatchGroup) => {
    if (group.manual) { setStoryboards(group.manual.materials); setSubjectFiles(group.manual.subjectFiles); setSubjectMode(group.manual.subjectMode); setManualKey(group.manual.key); return }
    setStoryboards(group.storyboards.map((file) => ({ id: createUuid(), file })))
    setSubjectMode('upload')
    setSubjectFiles({ frontalImage: group.productFront, backImage: group.productBack, detailImages: [] })
  }
  const exitBatch = () => {
    if (busy || resetting || uncertainSubmission || (batchStarted && !batchComplete)) return
    if (!batchStarted && previewGroup) previewBatchGroup(previewGroup)
    setBatchGroups([])
    submissionGroupsRef.current = []
    setGroupProgress([])
    if (batchComplete) {
      setManualKey(createUuid())
      setStoryboards([])
      setSubjectMode('none')
      setSubjectFiles(createEmptySubjectReferenceFiles())
    }
    setNotice(batchComplete ? '本批任务已创建，可以上传下一组素材。' : '已退出批量，可编辑当前组素材后单独提交。')
  }
  const dropAt = (event: React.DragEvent, target: number) => {
    event.preventDefault()
    event.stopPropagation()
    if (materialsLocked) return
    const id = event.dataTransfer.getData(MATERIAL_DRAG_TYPE)
    const sourceBucket = event.dataTransfer.getData('text/material-bucket') as UploadBucket
    if (!id || sourceBucket !== 'storyboard') return
    setStoryboards((current) => {
      const next = [...current]
      const currentSourceIndex = next.findIndex((value) => value.id === id)
      if (currentSourceIndex < 0) return current
      const [moved] = next.splice(currentSourceIndex, 1)
      next.splice(Math.min(target, next.length), 0, moved)
      return next
    })
  }
  const remove = (id: string) => {
    if (!materialsLocked) setStoryboards((items) => items.filter((item) => item.id !== id))
  }
  const handleDrop = (event: React.DragEvent, target: number) => {
    if (materialsLocked) {
      event.preventDefault()
      return
    }
    if (event.dataTransfer.types.includes(MATERIAL_DRAG_TYPE)) {
      dropAt(event, target)
      return
    }
    if (!event.dataTransfer.files.length) return
    event.preventDefault()
    event.stopPropagation()
    addFiles(Array.from(event.dataTransfer.files))
  }
  const submit = async (allManual = false) => {
    const chosen = allManual && !batchMode ? readyManualItems : []
    const manualBatch = chosen.length > 0
    if (submittingRef.current || busy || resetting || batchComplete || uncertainSubmission || (batchMode && !validBatchGroups.length) || submissionGroupsRef.current.some((item) => item.status === 'unknown') || !durationSupported || !providerAvailable || !productionReady || !customPromptReady || (!manualBatch && (!storyboards.length || !storyboardCountSupported || !subjectReady || !providerInputsSupported || !templateAcceptsCount))) return
    if (allManual && !manualBatch) return
    const manualGroups = chosen.map(toManualGroup)
    if (manualBatch) {
      const chosenKeys = new Set(chosen.map(item => item.key))
      setManualDrafts(manualItems.filter(item => !chosenKeys.has(item.key)))
      setBatchGroups(manualGroups)
      setPreviewGroupKey(manualGroups[0].key)
      setStoryboards(chosen[0].materials)
      setSubjectMode(chosen[0].subjectMode)
      setSubjectFiles(chosen[0].subjectFiles)
    }
    submittingRef.current = true
    setBusy(true)
    setError('')
    setNotice('')
    setSubmittedNotice('')
    setUncertainSubmission(null)
    const uploadController = new AbortController()
    uploadAbortRef.current?.abort()
    uploadAbortRef.current = uploadController
    let uploadFailure: unknown
    let uploadFailureMessage: string | undefined
    let activeGroup: SubmissionGroup | null = null
    const blockedAdmissions: string[] = []
    let invokingProvider = false
    try {
      const uploadStartedAt = submissionTimestamp()
      let completedUploads = 0
      const subjectReferences = subjectMode === 'upload' ? buildSubjectImageReferences(subjectFiles) : []
      const materialGroups: ComposerBatchGroup[] = manualBatch ? manualGroups : validBatchGroups.length
        ? validBatchGroups
        : [{ key: storyboards[0]?.file.name.replace(/\.[^.]+$/, '') || 'video', productFront: subjectMode === 'upload' ? subjectFiles.frontalImage : null, productBack: subjectMode === 'upload' ? subjectFiles.backImage : null, storyboards: storyboards.map((item) => item.file), errors: [] }]
      if (!batchStarted) {
        submissionGroupsRef.current = materialGroups.map((group) => ({
          group, identity: { clientTaskId: createUuid(), idempotencyKey: createUuid() }, status: 'pending',
        }))
      }
      const submissionGroups = submissionGroupsRef.current
      publishGroupProgress()
      const uploadCount = submissionGroups.filter((item) => item.status !== 'submitted' && !item.request).reduce((total, { group }) => total + group.storyboards.length + (group.manual ? (group.manual.subjectMode === 'upload' ? buildSubjectImageReferences(group.manual.subjectFiles).length : 0) : validBatchGroups.length ? Number(Boolean(group.productFront)) + Number(Boolean(group.productBack)) : subjectReferences.length), 0)
      const uploadOne = async (file: File, label: string, sourceImageOutputId?: string) => {
        setFileUploadState(file, { phase: 'uploading' })
        try {
          const uploaded = await uploadStoryboard(file, uploadController.signal, sourceImageOutputId)
          setFileUploadState(file, { phase: 'validated' })
          completedUploads += 1
          setStage(`正在上传${label} ${completedUploads}/${uploadCount}`)
          setProgress(Math.round((completedUploads / uploadCount) * 52))
          return uploaded
        } catch (cause) {
          if (uploadFailure === undefined) {
            uploadFailure = cause
            const requestId = cause instanceof ApiError && cause.correlationId && /^[A-Za-z0-9-]{1,64}$/.test(cause.correlationId)
              ? '（排查编号：' + cause.correlationId + '）' : ''
            uploadFailureMessage = label + '“' + file.name + '”：' + errorMessage(cause, '上传未完成，暂时无法判断图片是否合格。') + requestId
            setFileUploadState(file, { phase: 'failed', message: uploadFailureMessage })
            uploadController.abort(cause)
          } else {
            setFileUploadState(file, { phase: 'cancelled' })
          }
          throw cause
        }
      }
      let firstTask: Task | null = null
      for (const [groupIndex, entry] of submissionGroups.entries()) {
        if (entry.status === 'submitted') continue
        if (entry.status === 'unknown') throw new Error('提交结果未知，请先核对任务状态')
        activeGroup = entry
        const { group, identity: submissionIdentity } = entry
        entry.status = 'uploading'
        publishGroupProgress()
        setStage(`正在上传第 ${groupIndex + 1}/${submissionGroups.length} 组：${group.key}`)
        if (!entry.request) {
          const [uploads, subjectUploads] = await Promise.all([
            mapWithConcurrency(group.storyboards, UPLOAD_CONCURRENCY, (file, index) => uploadOne(file, '第 ' + (groupIndex + 1) + ' 组 · 分镜图 ' + (index + 1), group.manual?.materials[index]?.sourceImageOutputId ?? (!validBatchGroups.length && !manualBatch ? storyboards[index]?.sourceImageOutputId : undefined))),
            group.manual
              ? mapWithConcurrency(group.manual.subjectMode === 'upload' ? buildSubjectImageReferences(group.manual.subjectFiles) : [], UPLOAD_CONCURRENCY, (reference, index) => uploadOne(reference.file, '商品参考图 ' + (index + 1)))
              : validBatchGroups.length
              ? mapWithConcurrency([group.productFront, group.productBack].filter((file): file is File => Boolean(file)), UPLOAD_CONCURRENCY, (file, index) => uploadOne(file, '第 ' + (groupIndex + 1) + ' 组 · 商品参考图 ' + (index + 1)))
              : mapWithConcurrency(subjectReferences, UPLOAD_CONCURRENCY, (reference, index) => uploadOne(reference.file, '商品参考图 ' + (index + 1))),
          ])
          setStage(generationMode === 'custom' ? `正在校验第 ${groupIndex + 1}/${submissionGroups.length} 组` : `正在创建第 ${groupIndex + 1}/${submissionGroups.length} 组任务`)
          setProgress(Math.round(52 + ((groupIndex + 1) / submissionGroups.length) * 46))
          // Retain the exact request as well as its identity: re-uploading or changing
          // the title/timing would conflict with the server's payload hash on retry.
          entry.request = [
            `${group.key.slice(0, 80)} · ${formatTime(new Date().toISOString())}`,
            uploads,
            {
              generationMode,
              directorMode,
              videoType: generationMode === 'automatic' ? videoType : 'ootd',
              displayFocus: videoType === 'ootd' ? displayFocus : 'overall',
              creativeBrief: creativeBrief.trim() || undefined,
              customPrompt: generationMode === 'custom' ? customPrompt.trim() : undefined,
              templateId,
              siteProfileId,
              elementIds: [],
              subjectUploads,
              resultCount,
              durationSeconds: normalizedDurationSeconds,
              actionIntensity: videoType === 'ootd' ? actionIntensity : 'standard',
              cutMode: generationMode === "automatic" ? "explicit_cuts" : cutMode,
              autoSubmitToCutflow,
            },
            (submissionTimestamp() - uploadStartedAt) / 1000,
            submissionIdentity,
          ]
        }
        window.localStorage.setItem(`libtv:submission-intent:${actorId ?? 'anonymous'}:${submissionIdentity.clientTaskId}`, JSON.stringify({
          ...submissionIdentity,
          kind: 'video',
          createdAt: new Date().toISOString(),
          requestHash: `${group.key}:${templateId}:${siteProfileId}:${normalizedDurationSeconds}:${resultCount}`,
        }))
        submissionIdentityRef.current = submissionIdentity
        entry.status = 'submitting'
        setStage(generationMode === 'custom' ? `正在校验第 ${groupIndex + 1}/${submissionGroups.length} 组` : `正在创建第 ${groupIndex + 1}/${submissionGroups.length} 组任务`)
        publishGroupProgress()
        invokingProvider = true
        const task = await createVideoTask(...entry.request)
        invokingProvider = false
        entry.status = 'submitted'
        entry.resourceId = task.id
        if (task.phase === 'ACTION_REQUIRED') blockedAdmissions.push(`${task.title}：${userFacingError({ code: task.phaseReasonCode })}`)
        publishGroupProgress()
        submissionIdentityRef.current = null
        // A local receipt-cleanup or callback failure must never undo acceptance.
        try { window.localStorage.removeItem(`libtv:submission-intent:${actorId ?? 'anonymous'}:${submissionIdentity.clientTaskId}`) } catch { /* Retain the receipt for reconciliation. */ }
        if (!firstTask) firstTask = task
        try { onCreated(task, task.phase === 'ANALYZING') } catch { setNotice('任务已创建，请前往任务中心查看。') }
      }
      const task = firstTask
      if (!task) throw new Error('没有可提交的素材组')
      setProgress(100)
      setSubmittedNotice(blockedAdmissions.length && submissionGroups.length > 1
        ? `已创建 ${submissionGroups.length} 个任务，其中 ${blockedAdmissions.length} 个未进入付费生成：${blockedAdmissions.join('；')}`
        : submissionGroups.length > 1
        ? `已批量创建 ${submissionGroups.length} 个独立任务`
        : task.phase === 'ACTION_REQUIRED'
        ? `“${task.title}”已创建，但未进入付费生成（包含自动送剪的预算校验）：${userFacingError({ code: task.phaseReasonCode })}`
        : task.phase === 'ANALYZING'
        ? `“${task.title}”已创建，AI 正在后台分析素材并编排提示词；编排成功后才会提交 ${activeProvider.displayName}。`
        : `“${task.title}”已进入 ${activeProvider.displayName} 生成队列，可以继续上传下一组素材`)
      if (batchMode || manualBatch) return
      const clearSubmittedMaterials = () => {
        setStoryboards([])
        setFileUploadStates(new Map())
        setBatchGroups([])
        submissionGroupsRef.current = []
        setGroupProgress([])
        setSubjectMode('none')
        setSubjectFiles(createEmptySubjectReferenceFiles())
        setHistoryOpen(false)
        setManualKey(createUuid())
        if (!manualDrafts.length) {
          setGenerationMode('automatic')
          setVideoType('ootd')
          setCreativeBrief('')
          setDisplayFocus('overall')
          setCustomPrompt('')
          setAutoSubmitToCutflow(true)
        }
        setResetting(false)
        resetTimerRef.current = null
      }
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        clearSubmittedMaterials()
      } else {
        setResetting(true)
        resetTimerRef.current = window.setTimeout(clearSubmittedMaterials, 220)
      }
    } catch (cause) {
      const failure = uploadFailure ?? cause
      const identity = submissionIdentityRef.current
      const uncertain = invokingProvider && identity && (
        failure instanceof ApiError
          ? failure.code === 'REQUEST_TIMEOUT' || failure.code === 'WRITE_UNCERTAIN' || failure.code.startsWith('IDEMPOTENCY_') || failure.mayHaveBeenAccepted || failure.mayHaveBeenCharged || failure.requiresReconciliation || failure.status >= 500
          : true
      )
      if (activeGroup && activeGroup.status !== 'submitted') {
        activeGroup.status = uncertain ? 'unknown' : 'failed'
        publishGroupProgress()
      }
      if (uncertain && identity) {
        setUncertainSubmission({ ...identity, result: null, checking: false })
        setError('提交结果未知，可能已创建任务；请先核对任务状态，不要直接重新提交。')
      } else {
        submissionIdentityRef.current = null
        setError(uploadFailureMessage ?? errorMessage(failure, '素材提交失败，请检查图片后重试'))
      }
    } finally {
      if (uploadAbortRef.current === uploadController) uploadAbortRef.current = null
      setBusy(false)
      submittingRef.current = false
      setProgress(0)
    }
  }

  const checkUncertainSubmission = async () => {
    if (!uncertainSubmission || uncertainSubmission.checking) return
    setUncertainSubmission((current) => current ? { ...current, checking: true } : current)
    try {
      const result = await querySubmissionRecovery({
        kind: 'video',
        clientId: uncertainSubmission.clientTaskId,
        idempotencyKey: uncertainSubmission.idempotencyKey,
      })
      if (result.state === 'submitted' && result.resourceId) {
        // Server confirmed the original task: reopen it, never submit another paid job.
        const entry = submissionGroupsRef.current.find((item) => item.identity.clientTaskId === uncertainSubmission.clientTaskId)
        if (entry) {
          entry.status = 'submitted'
          entry.resourceId = result.resourceId
          publishGroupProgress()
        }
        try { window.localStorage.removeItem(`libtv:submission-intent:${actorId ?? 'anonymous'}:${uncertainSubmission.clientTaskId}`) } catch { /* Keep the confirmed receipt. */ }
        setUncertainSubmission(null)
        submissionIdentityRef.current = null
        setError('')
        if (!batchMode) {
          setStoryboards([])
          setSubjectMode('none')
          setSubjectFiles(createEmptySubjectReferenceFiles())
          setHistoryOpen(false)
          setManualKey(createUuid())
        }
        setSubmittedNotice(batchMode ? '已核对并保留原任务；继续时只提交尚未成功的组。' : '已确认原任务创建成功；未重新提交。')
        try {
          onCreated(await getTask(result.resourceId), true)
        } catch {
          setNotice('原任务已确认创建，请前往任务中心查看；未重新提交。')
        }
        return
      }
      setUncertainSubmission((current) => current ? { ...current, result, checking: false } : current)
      setError(result.state === 'submitted'
        ? '已找到原提交，请打开任务记录继续处理。'
        : result.state === 'in_progress'
          ? '原提交仍在核对中，请稍后刷新任务记录。'
          : result.state === 'unknown'
            ? '仍无法安全判断结果，请联系运营处理；系统不会自动重试。'
            : '暂时找不到提交记录，但生成服务可能已收到任务。请联系管理员核对，先不要重新生成。')
    } catch (cause) {
      setUncertainSubmission((current) => current ? { ...current, checking: false } : current)
      setError(errorMessage(cause, '核对任务状态失败，请稍后重试'))
    }
  }

  const retryOriginalSubmission = async () => {
    const pending = uncertainSubmission
    if (!pending || pending.checking || busy || pending.result?.state !== 'not_found') return
    const entry = submissionGroupsRef.current.find((item) => item.identity.clientTaskId === pending.clientTaskId)
    if (!entry?.request || entry.identity.idempotencyKey !== pending.idempotencyKey) {
      setError('原请求的完整上传信息已不在当前页面，无法安全重试。请保留提交编号并联系管理员核对。')
      return
    }
    // The recovery read is advisory; re-check immediately before replaying.
    // Crucially we reuse the SAME payload, clientTaskId and Idempotency-Key.
    // The server will replay an accepted request rather than charge twice.
    setBusy(true)
    setUncertainSubmission((current) => current ? { ...current, checking: true } : current)
    try {
      const latest = await querySubmissionRecovery({
        kind: 'video',
        clientId: pending.clientTaskId,
        idempotencyKey: pending.idempotencyKey,
      })
      if (latest.state !== 'not_found') {
        setUncertainSubmission((current) => current ? { ...current, result: latest, checking: false } : current)
        setError('原任务状态已变化，请先点击“核对任务状态”，不要发起新任务。')
        return
      }
      const task = await createVideoTask(...entry.request)
      entry.status = 'submitted'
      entry.resourceId = task.id
      publishGroupProgress()
      try { window.localStorage.removeItem(`libtv:submission-intent:${actorId ?? 'anonymous'}:${pending.clientTaskId}`) } catch { /* Keep the confirmed receipt. */ }
      submissionIdentityRef.current = null
      setUncertainSubmission(null)
      setError('')
      setSubmittedNotice('已通过原提交编号找回或创建任务，没有生成第二个提交编号。')
      if (!batchMode) { setStoryboards([]); setSubjectFiles(createEmptySubjectReferenceFiles()); setSubjectMode('none'); setHistoryOpen(false); setManualKey(createUuid()) }
      try { onCreated(task, task.phase === 'ANALYZING') } catch {
        setNotice('原任务已确认创建，请前往任务中心查看。')
      }
    } catch (cause) {
      setUncertainSubmission((current) => current ? { ...current, result: null, checking: false } : current)
      setError(errorMessage(cause, '原编号重试仍无法确认结果，请再次核对任务状态；不要创建新编号。'))
    } finally {
      setBusy(false)
    }
  }

  const startIndependentTask = () => {
    if (!uncertainSubmission || busy || batchMode) return
    // The unresolved identity remains stored for operator reconciliation.
    // Clearing the form ensures the next submission is intentionally separate.
    const originalClientId = uncertainSubmission.clientTaskId
    setStoryboards([])
    setBatchGroups([])
    submissionGroupsRef.current = []
    setGroupProgress([])
    setSubjectMode('none')
    setSubjectFiles(createEmptySubjectReferenceFiles())
    setHistoryOpen(false)
    setCreativeBrief('')
    setVideoType('ootd')
    setDisplayFocus('overall')
    setCustomPrompt('')
    setUncertainSubmission(null)
    submissionIdentityRef.current = null
    setError('')
    setNotice(`原任务 ${originalClientId} 仍待核对，记录已保留。请上传不同任务的素材后再提交。`)
  }

  const storyboardUploadButton = <button type="button" className="upload-zone upload-zone--compact" disabled={materialsLocked || storyboards.length >= storyboardLimit} onClick={() => storyboardInputRef.current?.click()}><span className="upload-icon"><Plus size={22} /></span><span><strong>{storyboards.length ? '添加' : '点击或拖入'}</strong></span></button>
  return <section className={`composer${busy ? ' is-busy' : ''}${resetting ? ' is-resetting' : ''}`} aria-label="视频制作配置" aria-busy={busy || resetting}>
    {uncertainSubmission ? <div className="inline-alert inline-alert--warning" role="alert">
      <CircleAlert size={17} />
      <span>
        <strong>提交结果未知</strong>
        <small>已保留这次提交记录。请先核对任务是否成功，确认前不要重新生成，以免重复扣费。</small>
        {uncertainSubmission.result ? <small>核对结果：{uncertainSubmission.result.state}</small> : null}
      </span>
      <button type="button" className="secondary-button" disabled={uncertainSubmission.checking} onClick={() => void checkUncertainSubmission()}>
        {uncertainSubmission.checking ? <LoaderCircle className="spin" size={15} /> : <CheckCircle2 size={15} />}
        核对任务状态
      </button>
      {uncertainSubmission.result?.state === 'not_found' && submissionGroupsRef.current.some((entry) => entry.identity.clientTaskId === uncertainSubmission.clientTaskId && Boolean(entry.request)) ? <button type="button" className="secondary-button" disabled={uncertainSubmission.checking || busy} onClick={() => void retryOriginalSubmission()}>
        使用原提交编号安全重试
      </button> : null}
      {!batchMode ? <button type="button" className="secondary-button" disabled={uncertainSubmission.checking || busy} onClick={startIndependentTask}>
        创建其他独立任务（保留原记录）
      </button> : null}
    </div> : null}
    {batchMode ? <section className="composer-batch-import" aria-label="批量进度与预览">
      <strong>批量素材只读预览 · 已创建 {groupProgress.filter((item) => item.status === 'submitted').length}/{validBatchGroups.length} 组</strong>
      <p>批量素材只读，退出批量后可编辑；提交后锁定。</p>
      <div className="batch-groups" aria-live="polite">{batchGroups.map((group, index) => {
        const entry = groupProgress.find((item) => item.group.key === group.key)
        return <div key={group.key}>
          <button type="button" className="text-button" disabled={busy || resetting} aria-pressed={previewGroup?.key === group.key} onClick={() => setPreviewGroupKey(group.key)}>预览第 {index + 1} 组：{group.key}</button>
          <small>{group.storyboards.length} 张分镜 · {group.errors.length ? group.errors.join('、') : entry ? groupStatusLabels[entry.status] : '待提交'}{entry?.resourceId ? ` · 任务 ${entry.resourceId}` : ''}</small>
        </div>
      })}</div>
      <button type="button" className="secondary-button" disabled={busy || resetting || uncertainSubmission !== null || (batchStarted && !batchComplete)} onClick={exitBatch}>{batchComplete ? '结束本批，上传新素材' : '退出批量，编辑当前组'}</button>
    </section> : null}
    {seed?.sourceReferencesUnavailable && !subjectFiles.frontalImage ? <div className="inline-alert inline-alert--warning" role="status">历史任务没有可恢复的原始商品参考图。本次已带入选中的分镜，请在商品参考区重新上传原商品图，避免服装细节不一致。</div> : null}
    <div className="composer-inputs composer-inputs--video v6-workspace" inert={formLocked}>
      <div className="v6-materials">
        <header className="v6-panel-header"><h2>添加视频素材</h2>
          <button type="button" className="text-button upload-batch-button" disabled={formLocked} onClick={() => batchInputRef.current?.click()}><Images size={16} />按命名批量导入</button>
        </header>
      <div className="upload-panels upload-panels--with-subject upload-panels--unified">
        <section className={`upload-panel upload-panel--storyboard${storyboards.length ? ' upload-panel--has-items' : ''}`} onDragOver={(event) => {
          if (materialsLocked) return
          event.preventDefault()
          event.dataTransfer.dropEffect = event.dataTransfer.types.includes(MATERIAL_DRAG_TYPE) ? 'move' : 'copy'
        }} onDrop={(event) => handleDrop(event, storyboards.length)}>
          <input ref={storyboardInputRef} hidden type="file" multiple disabled={materialsLocked} accept={IMAGE_FILE_ACCEPT} onChange={(event) => { addFiles(Array.from(event.target.files ?? [])); event.currentTarget.value = '' }} />
          <input ref={batchInputRef} hidden type="file" multiple disabled={formLocked} accept={IMAGE_FILE_ACCEPT} onChange={(event) => { addBatchFiles(Array.from(event.target.files ?? [])); event.currentTarget.value = '' }} />
          <div className="upload-panel-head"><div><h2>{generationMode === 'automatic' ? '视频分镜图' : '视频画面'}</h2><small>{generationMode === 'automatic' ? '首张为首帧' : '上传图片，按播放顺序排列'}</small></div>{displayedStoryboards.length ? <span>{displayedStoryboards.length} 张已添加</span> : null}</div>
          {displayedStoryboards.length ? <div className="materials-grid" aria-label={batchMode ? '批量分镜只读预览' : '分镜图排序列表'}>{displayedStoryboards.map((material, index) => batchMode
            ? <article className="material-card" key={material.id}><div className="material-preview"><button type="button" className="material-preview-trigger" aria-label={`预览分镜 ${index + 1}：${material.file.name}`} onClick={() => setPreview({ file: material.file, label: `分镜图 ${index + 1}` })}><LocalImage file={material.file} alt={material.file.name} /></button><span className="order-index">{index + 1}</span></div><UploadStatus state={fileUploadStates.get(material.file) ?? { phase: 'preview' }} /></article>
            : <MaterialCard key={material.id} material={material} bucket="storyboard" index={index} uploadState={fileUploadStates.get(material.file) ?? { phase: 'preview' }} onRemove={() => remove(material.id)} onDropAt={(event, target) => handleDrop(event, target)} onPreview={() => setPreview({ file: material.file, label: `分镜图 ${index + 1}` })} />)}{!batchMode && storyboards.length < storyboardLimit ? storyboardUploadButton : null}</div> : storyboardUploadButton}
        </section>
        <SubjectInputPanel
          className="v6-subject"
          mode={batchMode && previewGroup?.manual ? previewGroup.manual.subjectMode : subjectMode}
          onModeChange={(mode) => { if (!materialsLocked && mode !== 'history') setSubjectMode(mode) }}
          uploadedFiles={displayedSubjectFiles}
          uploadStates={fileUploadStates}
          onUploadedFilesChange={(files) => { if (!materialsLocked) setSubjectFiles(files) }}
          elements={[]}
          selectedElementId=""
          onSelectedElementIdChange={() => {}}
          storyboardReferenceCount={displayedStoryboards.length}
          referenceLimit={referenceLimit || 7}
          allowHistorySelection={productReferencesSupported && Boolean(siteProfileId) && subjectReferenceCount < Math.min(4, Math.max(0, referenceLimit - displayedStoryboards.length))}
          onOpenProductHistory={() => setHistoryOpen(true)}
          disabled={materialsLocked}
        />
      </div>
      {!batchMode ? <section className="creation-video-queue" aria-label="待提交商品">
        <header><div><strong>待提交</strong><span className="creation-count">{manualItems.length}</span></div><button type="button" className="text-button" onClick={newManual} disabled={materialsLocked || !hasDraftMaterials(currentManual)}><Plus size={15}/>新建一款</button></header>
        {manualItems.length ? <div className="creation-queue-list">{manualItems.map(item => <div key={item.key} className={'creation-queue-row' + (item.key === manualKey ? ' is-current' : '')}>
          <button type="button" className="creation-queue-select" onClick={() => selectManual(item)} aria-pressed={item.key === manualKey} disabled={materialsLocked}>
            <span className="creation-queue-thumb">{item.materials[0] ? <LocalImage file={item.materials[0].file} alt="分镜"/> : <Images size={16}/>}</span>
            <span className="creation-queue-thumb">{item.subjectFiles.frontalImage ? <LocalImage file={item.subjectFiles.frontalImage} alt="商品"/> : <Images size={16}/>}</span>
            <span className="creation-queue-info"><strong>{item.subjectFiles.frontalImage?.name.replace(/\.[^.]+$/, '') ?? item.materials[0]?.file.name.replace(/\.[^.]+$/, '') ?? '新商品'}</strong><small>{item.materials.length} 张分镜 · {getSubjectReferenceCount(item.subjectMode, item.subjectFiles, '')} 张参考</small></span>
            <span className={'creation-queue-state ' + (manualReady(item) ? 'is-ready' : '')}>{manualReady(item) ? <CheckCircle2 size={15}/> : <CircleAlert size={15}/>} {manualReady(item) ? '已就绪' : '待完善'}</span>
          </button>
          <button type="button" className="creation-queue-remove" aria-label="移除待提交商品" disabled={materialsLocked} onClick={() => removeManual(item.key)}><Trash2 size={15}/></button>
        </div>)}</div> : <div className="creation-queue-empty"><Images size={24}/><span>暂无待提交素材</span></div>}
      </section> : null}
      </div>
      <div className="v6-config">
        <header className="v6-panel-header"><h2>视频配置</h2></header>
        <GenerationAdvancedSettings alwaysOpen summary="生成方式、展示重点与输出设置" customized={siteProfileId !== defaultProfileId || generationMode !== 'automatic' || videoType !== 'ootd' || displayFocus !== 'overall' || resultCount !== 1 || (durationSupported && normalizedDurationSeconds !== normalizeVideoDuration(10, durationOptions)) || actionIntensity !== 'standard' || cutMode !== 'explicit_cuts' || directorMode !== 'cross_scene_v2' || !autoSubmitToCutflow || Boolean(customPrompt.trim())} disabled={formLocked} onReset={() => { setGenerationMode('automatic'); setVideoType('ootd'); setDisplayFocus('overall'); setCustomPrompt(''); setSiteProfileId(defaultProfileId); setResultCount(1); setDurationSeconds(10); setActionIntensity('standard'); setCutMode('explicit_cuts'); setDirectorMode('cross_scene_v2'); setAutoSubmitToCutflow(true) }}>
          <div className="generation-route-tabs" role="radiogroup" aria-label="生成方式">
            <button type="button" className={`generation-route-tab${generationMode === 'automatic' ? ' is-active' : ''}`} role="radio" aria-checked={generationMode === 'automatic'} aria-controls="generation-route-panel" disabled={formLocked} onClick={() => setGenerationMode('automatic')}>
              <span className="generation-route-radio">{generationMode === 'automatic' ? <Check size={14} /> : null}</span>
              <span><strong>Sol 自动编排</strong><small>OOTD 穿搭展示，自动安排镜头和动作</small></span>
            </button>
            <button type="button" className={`generation-route-tab${generationMode === 'custom' ? ' is-active' : ''}`} role="radio" aria-checked={generationMode === 'custom'} aria-controls="generation-route-panel" disabled={formLocked} onClick={() => setGenerationMode('custom')}>
              <span className="generation-route-radio">{generationMode === 'custom' ? <Check size={14} /> : null}</span>
              <span><strong>自定义提示词</strong><small>直接使用你填写的提示词生成</small></span>
            </button>
          </div>
        <div className="composer-routing-options">
          <AccountSiteField compact value={siteProfileId} onChange={setSiteProfileId} choices={choices} error={siteError} disabled={formLocked} />
        </div>
        {generationMode === "automatic" ? <DirectorModeSwitch value={directorMode} onChange={setDirectorMode} disabled={formLocked} /> : null}
        <section className="generation-workflow" aria-labelledby="generation-workflow-title">
          <div className="generation-workflow-heading">
            <h2 id="generation-workflow-title">生成方式</h2>
            <small>{generationMode === 'custom' ? `自定义提示词直达 ${activeProvider.displayName}` : `Sol 根据首帧与分镜参考编排${videoType === 'unboxing' ? '开箱' : ' OOTD'}`}</small>
          </div>
          <div className="generation-route-panel" id="generation-route-panel">
            {generationMode === 'custom'
              ? <div className="generation-custom-panel"><CustomPromptField scope="video" label={`${activeModel} 提示词`} value={customPrompt} disabled={formLocked} onChange={setCustomPrompt} placeholder="输入画面动作、镜头和节奏要求" /></div>
              : <div className="generation-ootd-panel">
                  <div className="generation-ootd-fields">
                    {ENABLE_UNBOXING_AUTOMATIC_GENERATION ? <div className="generation-video-type" role="group" aria-label="视频类型">
                      <span>视频类型</span>
                      <div className="segmented-control">
                        {([['ootd', 'OOTD 穿搭'], ['unboxing', '开箱展示']] as const).map(([value, label]) => (
                          <label key={value} className={videoType === value ? 'is-selected' : ''}>
                            <input type="radio" name="auto-video-type" value={value} checked={videoType === value} disabled={formLocked} onChange={() => setVideoType(value)} />
                            <span>{label}</span>
                          </label>
                        ))}
                      </div>
                      <small>{videoType === 'unboxing' ? 'Sol 根据首帧中的真实包装、商品与道具安排开箱。' : '默认 OOTD：以真实首帧自然展示穿搭与印花。'}</small>
                    </div> : null}
                    {videoType === 'ootd'
                      ? <label className="catalog-field"><span>本条展示重点</span><select aria-label="本条展示重点" value={displayFocus} disabled={formLocked} onChange={(event) => setDisplayFocus(event.target.value as VideoDisplayFocus)}>{VIDEO_DISPLAY_FOCUSES.map((focus) => <option key={focus.value} value={focus.value}>{focus.label}</option>)}</select><small>依照首帧和实际印花细节展示，不固定镜头套路。</small></label>
                      : <div className="generation-unboxing-guidance"><strong>开箱展示重点</strong><small>保留真实取出与展开动作，展示商品全貌、材质和原始图案；不凭空新增包装。</small></div>}
                  </div>
                  <p className="generation-route-context">站点风格沿用上方所选商品品牌{selectedProfile?.tone ? ` · ${selectedProfile.tone}` : ''}；用户补充要求优先。</p>
                  {resultCount > 1 ? <p className="composer-ootd-note">多条结果按重点展示、细节呈现、场景行为、穿搭轮廓依次变化；共同保留本条重点与补充要求。</p> : null}
                  <p className="composer-ootd-note">从第一张首帧自然起镜，其他分镜图作为镜头参考；Sol 自主安排切镜与时长。</p>
                </div>}
          </div>
        </section>
        <div className="generation-output-options">
      <fieldset className="result-count-field composer-result-count"><legend>生成结果数</legend><div className="segmented-control">{([1, 2, 3, 4] as const).map((count) => <label key={count} className={resultCount === count ? 'is-selected' : ''}><input type="radio" name="result-count" value={count} checked={resultCount === count} onChange={() => setResultCount(count)} /><span>{count}</span></label>)}</div></fieldset>
      <label className="composer-duration-field" htmlFor="video-duration"><span>视频时长 <small id="video-duration-options">{durationOptionsLabel}</small></span><span><select id="video-duration" aria-label="视频时长" value={normalizedDurationSeconds} disabled={formLocked} aria-describedby="video-duration-options" onChange={(event) => setDurationSeconds(Number(event.target.value))}>{durationOptions.map((duration) => <option key={duration} value={duration}>{duration}</option>)}</select><small>秒</small></span></label>
        </div>
        <label className="composer-output-autocut">
          <span className="composer-output-autocut__control"><input type="checkbox" checked={autoSubmitToCutflow} onChange={(event) => setAutoSubmitToCutflow(event.target.checked)} disabled={formLocked} /><strong>生成后自动送 CUT</strong></span>
        </label>
        <GenerationBrief value={creativeBrief} onChange={setCreativeBrief} disabled={formLocked} placeholder="选填" />
        </GenerationAdvancedSettings>
      </div>
      </div>
    <div className="composer-submit-area" aria-live="polite">
    <div className="composer-submit-row">
      {busy ? <span className="composer-submit-status"><LoaderCircle className="spin" size={14} /><span>{stage}</span><strong>{progress}%</strong></span>
        : <span className="composer-submit-summary"><LockKeyhole size={14} /><span><strong>{!batchMode && manualDrafts.length ? readyManualItems.length + " 款就绪 · 共 " + readyManualItems.length * resultCount + " 条" : "预计 " + resultCount * (batchMode ? remainingGroupCount : 1) + " 条视频"} · 每条 {normalizedDurationSeconds} 秒</strong><small>点击“开始生成”即确认付费提交 · {autoSubmitToCutflow ? <>生成及剪辑额度同步预留 · 剪辑预算 <GenerationCostBadge category="cutflowPriceUsd" units={resultCount * (batchMode ? remainingGroupCount : manualDrafts.length ? readyManualItems.length : 1)} /></> : '仅生成原片'}</small></span></span>}
      <button className={"primary-button primary-button--large generation-priced-button" + (!batchMode && manualDrafts.length ? " creation-submit-current" : "")} disabled={busy || resetting || batchComplete || (batchMode && !validBatchGroups.length) || uncertainSubmission !== null || catalogLoading || !storyboards.length || !storyboardCountSupported || !durationSupported || !providerAvailable || !subjectReady || !providerInputsSupported || !productionReady || !templateAcceptsCount || !customPromptReady} onClick={() => void submit()}>{busy ? <><LoaderCircle className="spin" size={18} />正在提交</> : resetting ? <><CheckCircle2 size={18} />已提交</> : <><WandSparkles size={18} />{batchComplete ? '本批已全部创建' : batchStarted ? `继续未完成的 ${remainingGroupCount} 组` : !batchMode && manualDrafts.length ? '提交当前款' : '开始生成'}<ArrowRight size={17} /></>}<GenerationCostBadge category="videoPricePerSecondUsd" units={normalizedDurationSeconds * resultCount * (batchMode ? remainingGroupCount : 1)} /></button>
      {!batchMode && manualDrafts.length ? <button type="button" className="primary-button creation-submit-all" disabled={busy || resetting || Boolean(uncertainSubmission) || catalogLoading || !providerAvailable || !productionReady || !customPromptReady || !durationSupported || !readyManualItems.length} onClick={() => void submit(true)}>提交全部 {readyManualItems.length} 款 · {readyManualItems.length * resultCount} 条<ArrowRight size={16}/><GenerationCostBadge category="videoPricePerSecondUsd" units={normalizedDurationSeconds * resultCount * readyManualItems.length}/></button> : null}
      {busy ? <div className="composer-submit-progress progress-track"><span style={{ width: `${progress}%` }} /></div> : null}
    </div>
    <div className="composer-feedback" aria-live="polite">
      {error ? <div className="inline-alert inline-alert--danger" role="alert"><AlertTriangle size={17} /><span>{error}</span></div>
        : catalogError ? <div className="inline-alert inline-alert--danger" role="alert"><AlertTriangle size={16} /><span>{catalogError}</span></div>
          : !providerAvailable ? <div className="inline-alert inline-alert--danger" role="alert"><AlertTriangle size={16} />{activeProvider.displayLabel} 当前不可用。请让管理员配置连接信息、启用通道并验证连接。</div>
          : !siteProfileId ? <div className="inline-alert" role="alert"><CircleAlert size={17} /><span>{siteError || (choices ? '请选择商品所属品牌后再生成。' : '正在读取账号品牌。')}</span></div>
          : !productionReady && !catalogLoading ? <div className="inline-alert" role="alert"><CircleAlert size={17} /><span>{generationMode === 'automatic' ? '暂无可用的默认站点风格，请联系管理员；也可在高级设置中选择已验收的风格。' : '默认生产规格当前不可用。'}</span></div>
          : !subjectReady ? <div className="inline-alert" role="alert"><CircleAlert size={17} /><span>{subjectMode === 'upload' ? (!productReferencesSupported ? '当前模型不支持独立商品参考图，请更换模型或移除商品图。' : subjectValidation.errors[0]) : '请选择有效的商品参考图。'}</span></div>
          : !providerInputsSupported ? <div className="inline-alert inline-alert--danger" role="alert"><AlertTriangle size={16} />当前视频工作流（{activeModel}）最多接收 {referenceLimit} 张图片；请减少图片数量后再提交。</div>
            : !storyboardCountSupported ? <div className="inline-alert" role="alert"><CircleAlert size={17} /><span>{`当前模型的分镜图最多添加 ${storyboardLimit} 张。`}</span></div>
            : !durationSupported ? <div className="inline-alert" role="alert"><CircleAlert size={17} /><span>当前模型没有可用的自动编排 5–15 秒时长，请联系管理员检查模型配置。</span></div>
            : submittedNotice ? <div className="inline-alert inline-alert--success" role="status"><CheckCircle2 size={17} /><span>{submittedNotice}</span></div>
              : notice ? <div className="inline-alert" role="status"><CircleAlert size={17} /><span>{notice}</span></div>
                : !storyboards.length ? <div className="inline-alert"><CircleAlert size={17} /><span>请先上传视频画面</span></div>
                  : !templateAcceptsCount ? <div className="inline-alert" role="alert"><CircleAlert size={17} /><span>当前模板接受 {selectedTemplate?.minImages}–{selectedTemplate?.maxImages} 张分镜图</span></div>
                    : !customPromptReady ? <div className="inline-alert" role="alert"><CircleAlert size={17} /><span>请在高级设置中填写完整提示词，或恢复推荐设置。</span></div>
                  : null}
    </div>
    </div>
    {historyOpen && !materialsLocked && siteProfileId ? <ProductHistoryPicker key={manualKey + ':' + siteProfileId + ':' + actorId} siteProfileId={siteProfileId} maxSelection={Math.max(0, Math.min(4 - subjectReferenceCount, referenceLimit - displayedStoryboards.length - subjectReferenceCount))} existingFiles={buildSubjectImageReferences(subjectFiles).map(item => item.file)} onClose={() => setHistoryOpen(false)} onSelect={(files) => {
      const result = fillSubjectDropSlots(subjectFiles, files)
      setSubjectFiles(result.files)
      setSubjectMode('upload')
      setHistoryOpen(false)
      submissionGroupsRef.current = []
      setSubmittedNotice('')
    }} /> : null}
    {preview ? <ImagePreview file={preview.file} label={preview.label} onClose={() => setPreview(null)} /> : null}
  </section>
}
