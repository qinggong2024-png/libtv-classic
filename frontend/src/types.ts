export type StatusTone = 'neutral' | 'success' | 'warning' | 'danger' | 'active'
export type VariantKind = 'STABLE' | 'DYNAMIC'
export type FeedbackValue = 'LIKE' | 'DISLIKE'
export interface VideoLearningPreference {
  enabled: boolean
  feedbackCount: number
  scopeCount: number
  summary: string
  updatedAt: string | null
}
export type FeedbackReason =
  | 'GARMENT_ACCURATE'
  | 'PRODUCT_CLEAR'
  | 'NATURAL_MOTION'
  | 'COMFORTABLE_CAMERA'
  | 'GOOD_PACING'
  | 'STRONG_APPEAL'
  | 'GARMENT_DISTORTED'
  | 'COLOR_INACCURATE'
  | 'LOGO_TEXT_INCORRECT'
  | 'FACE_HAND_ARTIFACT'
  | 'STIFF_MOTION'
  | 'CAMERA_TOO_FAST'
  | 'CHAOTIC_EDITING'
  | 'PRODUCT_UNCLEAR'

export interface Asset {
  id: string
  order: number
  required: boolean
  originalFilename: string
  sha256: string
  width: number
  height: number
  sizeBytes?: number
  thumbnailUrl: string
  role?: 'STORYBOARD' | 'PRODUCT' | string
  usedForAnalysis?: boolean
  usedForGeneration?: boolean
}

export interface Variant {
  id: string
  kind: VariantKind
  revision: number
  budgetDurationSeconds?: number | null
  promptText: string
  promptHash: string | null
  generationStatus: string
  downloadStatus: string
  validationStatus: string
  finalApprovalStatus: 'PENDING' | 'APPROVED' | 'REJECTED'
  qualityGates: Record<string, { verification?: string; outcome?: string }>
  finalApprovedBy: string | null
  finalApprovedAt: string | null
  humanReviewSeconds: number | null
  reviewNotes: string | null
  downloadArtifactId: string | null
  errorCode: string | null
  provider: string
  providerTaskId: string | null
  providerStatus: string | null
  executionBackend: string | null
  errorMessage?: string | null
  errorDetails?: Record<string, unknown> | unknown[] | null
  retryable?: boolean | null
  providerErrorMessage?: string | null
  providerErrorDetails?: Record<string, unknown> | null
  providerErrorRetryable?: boolean | null
  variantIndex?: number | null
  variantKey?: string | null
  variantSeed?: Record<string, string | number>
  feedback: FeedbackValue | null
  feedbackReasons: FeedbackReason[]
  feedbackComment: string | null
}

export interface Task {
  id: string
  userId: string
  clientTaskId: string
  title: string
  assetCount: number
  userRequirement: string
  analysisModel: string
  generationProvider: string
  phase: string
  phaseReasonCode: string | null
  consistencyLevel: string | null
  analysisResult: Record<string, unknown> | null
  analysisRunId: string | null
  analysisStage: string | null
  analysisStartedAt: string | null
  analysisHeartbeatAt: string | null
  canvasProjectUuid: string | null
  stateVersion: number
  assets: Asset[]
  variants: Variant[]
  pointsQuota: {
    dailyLimit: number | null
    consumed: number
    reserved: number
    uncertain: number
    remaining: number | null
  }
  actionCapabilities: {
    canEdit: boolean
    canConfirm: boolean
    canCancel: boolean
    canRetryAnalysis: boolean
    canRegenerate: boolean
    canDownload: boolean
  }
  workflowKind?: 'FIXED_TEMPLATE' | 'LEGACY_ANALYSIS' | string
  templateId?: string | null
  templateVersion?: string | null
  siteProfileId?: string | null
  siteProfileVersion?: string | null
  settings?: Record<string, unknown>
  compiledPlan?: Record<string, unknown> | null
  materialValidation?: Record<string, unknown> | null
  paidSubmissionConsentAt?: string | null
  depthVideoResult?: { downloadId: string; completedAt: string | null } | null
  timing?: {
    stages: Array<{
      key: 'SOURCE_UPLOAD' | 'ANALYSIS' | 'CONFIRMATION' | 'MATERIALIZATION' | 'GENERATION' | 'DEPTH_EXTRACTION' | 'REPLACEMENT' | 'DOWNLOAD'
      status: 'COMPLETED' | 'ACTIVE' | 'PENDING' | 'FAILED' | 'INTERRUPTED' | 'UNKNOWN' | 'CANCELLED'
      durationSeconds: number | null
      activeStartedAt: string | null
      stoppedAt?: string | null
      startedAt?: string | null
      endedAt?: string | null
      attemptCount: number | null
    }>
  }
  createdAt: string
  updatedAt: string
}

export interface VideoTemplateSummary {
  id: string
  version: string
  status: 'draft' | 'active' | 'retired'
  name: string
  description: string
  minImages: number
  maxImages: number
  defaultSettings: {
    durationSeconds: number
    ratio: string
    sound: boolean
    subtitles: boolean
    smartStoryboard: boolean
    continuityStrategy: 'explicit_cuts' | 'continuous_when_possible'
  }
  actionPresets: Record<string, { label: string; instruction: string }>
  cameraPresets: Record<string, { label: string; instruction: string }>
  tempoPresets: Record<string, { label: string; instruction: string }>
  sourceNote: string
}

export interface SiteStyleProfileSummary {
  id: string
  version: string
  status: 'draft' | 'active' | 'retired'
  name: string
  siteUrl: string
  sourceUrls: string[]
  retrievedAt: string
  evidenceLevel: 'verified_site' | 'restricted_site'
  tone: string
  expressionLanguage: string
  audience: string
  palette: string[]
  typography: string
  cameraLanguage: string
  openingRule: string
  middleRule: string
  endingRule: string
  preferredShots: string[]
  preferredActions: string[]
  pacing: string
  productPriorities: string[]
  forbiddenMismatches: string[]
  researchNote: string
  motionEnergy: string
  primaryMotionPool: string[]
  microMotionPool: string[]
  expressionPool: string[]
  cameraPool: string[]
  forbiddenMotion: string[]
  preferredEnvironment: string[]
  productRiskRules: string[]
  dedupeRecentCount: number
  dedupeComboWindow: number
  dedupeFamilyWindow: number
  dedupeFamilyLimit: number
  libraryScope: 'general' | 'brand'
  brandId: string | null
}

export interface MotionActionSummary {
  productFocus?: 'upper_garment' | 'lower_garment' | 'footwear'
  id: string
  label: string
  category: 'outfit' | 'detail' | 'back' | 'interaction' | 'product'
  instruction: string
  purpose: string
  compatibleTemplates: string[]
  requirements: string[]
  avoid: string[]
  riskLevel: 'low' | 'conditional'
  libraryScope: 'general' | 'brand'
  brandId: string | null
  baseActionId: string | null
}

export type MotionActionWrite = MotionActionSummary

export type SiteStyleProfileWrite = Omit<SiteStyleProfileSummary, 'version'>

export interface PromptAssistantResponse {
  task: Task
  advice: string
}

export interface BatchConfirmationResponse {
  results: Array<{
    taskId: string
    status: 'SUCCEEDED' | 'FAILED'
    task: Task | null
    errorCode: string | null
    message: string | null
  }>
  succeededCount: number
  failedCount: number
}

export type PagePermission = 'images' | 'make' | 'records' | 'library'

export interface AuthUser {
  userId: string
  username: string
  role: 'ADMIN' | 'OPERATOR' | 'USER' | 'TRUSTED_USER' | 'AUDITOR'
  brandId?: string | null
  brandName?: string | null
  isBrandAdmin?: boolean
  pagePermissions?: PagePermission[]
  canAccessManagement?: boolean
}

export interface AdminUser {
  id: string
  username: string
  role: AuthUser['role']
  isActive: boolean
  createdAt: string
  brandId?: string | null
  isBrandAdmin?: boolean
  dailyBudgetUsd?: number | null
  imagePriceUsd?: number | null
  videoPricePerSecondUsd?: number | null
  depthVideoPricePerSecondUsd?: number | null
  recreateVideoPricePerSecondUsd?: number | null
  pagePermissions?: PagePermission[]
}

export interface AccountBrand {
  id: string
  name: string
  displayName?: string
  shortName?: string
  fullName?: string
  siteProfileId?: string
}
export interface AccountPolicy {
  role?: AuthUser['role']
  brandId?: string | null
  isBrandAdmin?: boolean
  isActive?: boolean
  dailyBudgetUsd?: number | null
  pagePermissions?: PagePermission[]
}
export interface GlobalUsagePricing {
  version: number
  imagePriceUsd: number | null
  videoPricePerSecondUsd: number | null
  depthVideoPricePerSecondUsd: number | null
  recreateVideoPricePerSecondUsd: number | null
  cutflowPriceUsd: number | null
  legacyConflicts: string[]
}
export interface GenerationBudget {
  day: string
  currency: 'USD'
  dailyBudgetUsd: number | null
  budgetUsedUsd: number
  unpricedCount: number
  prices: Pick<GlobalUsagePricing, 'imagePriceUsd' | 'videoPricePerSecondUsd' | 'depthVideoPricePerSecondUsd' | 'recreateVideoPricePerSecondUsd' | 'cutflowPriceUsd'>
}
export interface AccountUsageTotals {
  recordCount: number
  imageCount: number
  videoSeconds: number
  cutflowClips: number
  unknownVideoCount: number
  estimatedUsd: number
  reservedUsd: number
  uncertainUsd: number
  releasedUsd: number
  unpricedCount: number
  budgetUsedUsd: number
}

export interface AccountUsageSummary extends AccountUsageTotals {
  userId: string
  username: string
  brandId: string | null
  isActive: boolean
  dailyBudgetUsd: number | null
  todayBudgetUsedUsd: number
  todayUnpricedCount: number
  todayRemainingUsd: number | null
}

export interface AccountUsageReport extends AccountUsageTotals {
  day: string
  startDate: string
  endDate: string
  budgetDay: string
  summaries: AccountUsageSummary[]
  summaryTotal?: number
  eventTotal: number
  currency: 'USD'
  total: number
  policies: { userId: string; username: string; dailyBudgetUsd: number | null }[]
  items: { id: string; username: string; kind: string; category?: string | null; operationId: string; units: number; amountUsd: number | null; status: string; createdAt: string }[]
  events: { id: string; username: string; action: string; createdAt: string }[]
}

export interface AccountEventReport {
  startDate: string
  endDate: string
  total: number
  items: { id: string; username: string; action: string; createdAt: string; entityType: string; entityId: string }[]
}

export interface UploadResult {
  id: string
  originalFilename: string
  sha256: string
  width: number
  height: number
  mimeType: string
  sizeBytes: number
  thumbnailUrl: string
}

export interface FixedVideoTaskOptions {
  generationMode: 'automatic' | 'custom'
  directorMode?: 'cross_scene_v2' | 'reference_guided'
  videoType?: 'ootd' | 'unboxing'
  displayFocus?: import('./ootdCreativeControls').VideoDisplayFocus
  creativeBrief?: string
  customPrompt?: string
  templateId?: string
  siteProfileId?: string
  elementIds: string[]
  subjectUploads?: UploadResult[]
  resultCount: number
  durationSeconds: number
  actionIntensity: 'low' | 'standard' | 'high'
  cutMode: 'explicit_cuts' | 'continuous_when_possible'
  autoSubmitToCutflow?: boolean
}

export interface PromptPreset {
  id: string
  scope: PromptPresetScope
  name: string
  content: string
  sortOrder: number
  createdAt: string
  updatedAt: string
}

export type PromptPresetScope = 'video' | 'image_fusion' | 'image_storyboard'

export interface PromptPresetWrite {
  scope: PromptPresetScope
  name: string
  content: string
  sortOrder: number
}

export interface KlingElement {
  id: string
  name: string | null
  description: string | null
  status: string
  imageUrls: string[]
  videoUrl: string | null
  voiceId: string | null
  createdAt: string | null
  updatedAt: string | null
  raw?: {
    localJob?: boolean
    errorMessage?: string | null
    [key: string]: unknown
  }
}

export interface KlingElementList {
  items: KlingElement[]
  pageNum: number
  pageSize: number
  total: number
  official: boolean
}

export interface KlingElementCreate {
  name: string
  description: string
  imageReferences?: string[]
  videoReferences?: string[]
  voiceId?: string
  tagIds?: string[]
  callbackUrl?: string
  externalId?: string
}

export interface VideoGenerationStatus {
  enabled: boolean
  backend: 'cli' | 'api' | 'wavespeed' | 'bach'
  provider: string
  model?: string | null
  status: string
  available: boolean
  authenticated: boolean
  reason: string | null
  cliVersion: string | null
  models: string[]
  supportsMultipleImages: boolean
  supportsElementReferences?: boolean
  supportsProductReferences?: boolean
  imageInputLimit: number
  videoDurations: string[]
  tailImageVideoDurations: string[]
  fallbackMode: 'disabled' | 'first_last_frame'
}

export interface SystemStatus {
  environment: string
  sourceRevision: string
  libtvAdapter: string
  aiAdapter: string
  openaiModel: string
  openaiConfigured: boolean
  libtvConfigured: boolean
  outputRetentionDays: number
  downloadConcurrency: number
  technicalValidationConcurrency: number
  zipEnabled: boolean
  phaseOneUserId: string | null
  phaseOneAdminId: string | null
  sharedAuthentication: boolean
  csrfCookieName: string
  cutflowMultiSourceReady: boolean
  cutflowContractVersion?: string
  cutflowStatusAuthority?: 'nas-status' | 'legacy'
  videoGeneration?: VideoGenerationStatus
  /** @deprecated Use videoGeneration. */
  kling: VideoGenerationStatus
}

export interface KlingStatus {
  enabled: boolean
  backend: 'cli' | 'api' | 'wavespeed' | 'bach'
  provider: string
  status: string
  available: boolean
  authenticated: boolean
  reason: string | null
  cliVersion: string | null
  checkedAt: string | null
  supportedModels: string[]
  supportedTools: string[]
  videoDurations: string[]
  aspectRatios: string[]
  imageInputLimit: number
  supportsTailImage: boolean
  supportsMultipleImages: boolean
  supportsWatermarkFreeOutput: boolean
  verificationMode: string
  verificationMayGenerateOrCharge: boolean
}

export interface KlingCreditsStatus {
  status: 'ready' | 'unavailable' | 'not_applicable'
  availableCredits?: number
  recent7DayCredits?: number
  warningThresholdCredits?: number
  estimatedDaysRemaining?: number | null
  lowBalance?: boolean
  admissionBlocked: boolean
}

export interface KlingBillingReport {
  source: 'stored_kling_provider_task_receipts'
  scope: 'most_recent_provider_attempts'
  scanLimit: number
  scannedAttempts: number
  receiptCount: number
  invalidReceiptCount: number
  cliCreditsTotal: number
  cliCreditReceiptCount: number
  totals: Array<{ type: 'unit' | 'cash'; packageType: string | null; amount: string }>
  items: Array<{
    attemptId: string
    taskId: string
    taskTitle: string
    providerTaskId: string | null
    provider: string
    model: string | null
    status: string
    creditsConsumed: number | null
    createdAt: string
    charges: Array<{ type: 'unit' | 'cash'; amount: string; packageType: string | null; listPrice: string | null }>
  }>
}

export interface KlingConfig {
  backend: 'cli' | 'api' | 'wavespeed' | 'bach'
  apiKeyConfigured: boolean
  baseUrl: string
  callbackUrl: string | null
  model: string
  duration: number
  aspectRatio: string
  resolution: string
  audio: string
  multiShot: boolean
  maxSubmitConcurrency: number
  maxPollConcurrency: number
  enabled: boolean
  provider: string
  updatedAt?: string | null
}

export interface KlingConfigPatch {
  backend?: 'cli' | 'api' | 'wavespeed' | 'bach'
  apiKey?: string
  clearApiKey?: boolean
  baseUrl?: string
  callbackUrl?: string | null
  clearCallbackUrl?: boolean
  model?: string
  duration?: number
  aspectRatio?: string
  resolution?: string
  audio?: string
  multiShot?: boolean
  maxSubmitConcurrency?: number
  maxPollConcurrency?: number
  enabled?: boolean
}

export type WaveSpeedCredentialSource = 'admin' | 'environment' | 'none'

export interface WaveSpeedConfig {
  recreateVideoModel: string
  enabled: boolean
  apiKeyConfigured: boolean
  credentialSource: WaveSpeedCredentialSource
  baseUrl: string
  maxSubmitConcurrency: number
  maxPollConcurrency: number
  videoModel: string
  videoDuration: number
  videoAspectRatio: string
  videoSound: boolean
  provider: string
  videoProvider: string
  videoBackendActive: boolean
  status: string
  available: boolean
  checkedAt: string | null
  reason: string | null
  updatedAt: string | null
  verificationMode: 'prediction_read_only_lookup'
  verificationMayGenerateOrCharge: false
}

export interface WaveSpeedConfigPatch {
  recreateVideoModel?: string
  enabled?: boolean
  apiKey?: string
  clearApiKey?: boolean
  baseUrl?: string
  maxSubmitConcurrency?: number
  maxPollConcurrency?: number
  videoModel?: string
  videoDuration?: number
  videoAspectRatio?: string
  videoSound?: boolean
}

export type BachCredentialSource = 'admin' | 'environment' | 'none'

export interface BachConfig {
  enabled: boolean
  accessKeyConfigured: boolean
  secretKeyConfigured: boolean
  credentialsConfigured: boolean
  credentialSource: BachCredentialSource
  baseUrl: string
  model: string
  resolution: string
  duration: number
  aspectRatio: string
  generateAudio: boolean
  maxSubmitConcurrency: number
  maxPollConcurrency: number
  provider: string
  updatedAt: string | null
}

export interface BachConfigPatch {
  accessKey?: string
  secretKey?: string
  clearAccessKey?: boolean
  clearSecretKey?: boolean
  enabled?: boolean
  baseUrl?: string
  model?: string
  resolution?: string
  duration?: number
  aspectRatio?: string
  generateAudio?: boolean
  maxSubmitConcurrency?: number
  maxPollConcurrency?: number
}

export type ImageGenerationBatchStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'write_uncertain'
  | 'manual_review'

export interface ImageGenerationBatchOutput {
  id: string
  kind: 'fusion' | 'storyboard'
  view: 'front' | 'back' | 'shot'
  label: string
  url: string
  downloadUrl?: string | null
  artifactId?: string | null
  qualificationStatus?: 'PENDING' | 'QUALIFIED' | 'REJECTED'
  qualityGates?: Record<string, { verification?: string; outcome?: string }>
  reviewedAt?: string | null
  humanReviewSeconds?: number | null
}

export interface ImageGenerationBatchItem {
  id: string
  clientTaskId: string
  outfitId: string
  outfitName?: string
  sku?: string
  prompts?: { fusion: string; storyboard: string }
  userRequirement?: string
  referenceUrls?: { model: string[]; product: string[] }
  storyboardShots?: Array<{ order: number; instruction: string }>
  status: ImageGenerationBatchStatus
  providerTaskId: string | null
  providerTaskIds: string[]
  modelId: string
  outputs: ImageGenerationBatchOutput[]
  error?: {
    code: string
    message: string
    retryable: boolean | null
    details?: unknown
  } | null
}

export interface ImageGenerationBatch {
  id: string
  name?: string
  provider?: 'gpt' | 'nanobanana'
  configuration?: {
    siteProfileId?: string | null
    modes: Array<'fusion' | 'storyboard'>
    resultCount: 1 | 2 | 3 | 4
    aspectRatio: string
    resolution: string
    quality: 'low' | 'medium' | 'high'
    allowBackInference: boolean
  }
  clientBatchId?: string
  status: ImageGenerationBatchStatus
  items: ImageGenerationBatchItem[]
  createdAt?: string
  updatedAt?: string
}

export interface ImageGenerationHistoryPage {
  items: ImageGenerationBatch[]
  hasMore: boolean
  nextOffset: number
}

export interface ImageGenerationClientManifest {
  clientBatchId: string
  siteProfileId?: string
  name: string
  provider: 'gpt' | 'nanobanana'
  modes: Array<'fusion' | 'storyboard'>
  resultCount: 1 | 2 | 3 | 4
  aspectRatio: string
  resolution: string
  quality?: string
  outputFormat: 'png'
  allowBackInference: boolean
  paidSubmissionConsent: true
  expectedGenerationCount: number
  items: Array<{
    clientTaskId: string
    outfitId: string
    outfitName: string
    sku: string
    prompts: { fusion: string; storyboard: string }
    userRequirement?: string
    storyboardShots: Array<{ order: number; instruction: string }>
    referenceFileIndexes: number[]
    modelReferenceFileIndexes: number[]
    productReferenceFileIndexes: number[]
    referenceRoles: Array<{
      fileIndex: number
      role: 'model.reference' | 'product.reference'
    }>
  }>
}

export interface AdminCredentialsStatus {
  libtvConfigured: boolean
  modelConfigured: boolean
  modelBaseUrl: string | null
  updatedAt: string | null
}

export interface LibTVAccount {
  accountId: string
  accountName: string
  accountType: string
  isActive: boolean
  teamId: string | number | null
}

export interface LibTVAccountStatus {
  available: boolean
  authenticated: boolean
  loginInProgress: boolean
  activeAccount: LibTVAccount | null
  accounts: LibTVAccount[]
}

export interface LibTVLoginStart {
  sessionId: string
  loginUrl: string
  startedAt: string
  alreadyRunning: boolean
}

export interface LibTVAccountSwitch {
  ok: boolean
  accountId: string
  accountName: string
  accountType: string
  teamId: string | number | null
  alreadyActive: boolean
}

export interface Reconciliation {
  attemptId: string
  projectUuid: string
  nodeKey: string
  taskId: string | null
  status: string
  chargeStatus: string
  errorCode: string | null
  updatedAt: string
}

export interface AdminVideoRecord {
  artifactId: string
  attemptId: string
  taskId: string
  title: string
  username: string
  brandId: string | null
  brandName: string | null
  createdAt: string | null
  durationSeconds: number | null
  width: number | null
  height: number | null
  variant: string
  provider: string
  validationStatus: string
  finalApprovalStatus: string
}
export interface AdminVideoPage {
  total: number
  limit: number
  offset: number
  days: number
  items: AdminVideoRecord[]
}

export type OperationsIssueCategory = 'cloudUnknown' | 'reservationReview' | 'blockedPrepared'

export interface OperationsIssueRecord {
  taskId: string
  title: string
  username: string
  brandName: string | null
  taskPhase: string
  phaseReasonCode: string | null
  taskCreatedAt: string | null
  variantType: string | null
  attemptId: string
  attemptStatus: string
  provider: string
  providerTaskId: string | null
  providerStatus: string | null
  lastErrorCode: string | null
  errorMessage: string | null
  chargeStatus: string | null
  submittedAt: string | null
  updatedAt: string | null
  reconciledAt: string | null
  reconcileError: string | null
  reservationId: string | null
  reservationStatus: string | null
  reservationChargeStatus: string | null
  estimatedPoints: number | null
  actualPoints: number | null
  reservationCreatedAt: string | null
}

export interface OperationsIssuePage {
  category: OperationsIssueCategory
  total: number
  limit: number
  offset: number
  items: OperationsIssueRecord[]
}

export interface OperationsSnapshot {
  runtime: {
    version: number
    config: {
      globalPaused: boolean
      paidSubmissionsPaused: boolean
      libtvAuthRequired?: boolean
      promptConfirmationRequired?: boolean
      generationConcurrency: { sameProject: number; perUser: number; global: number }
      preparedQueue: { lowWatermark: number; highWatermark: number }
      autoConfirmLevels: string[]
    }
  }
  sampledAt?: string
  queues: {
    prepared: number
    actionablePrepared?: number
    actionableTotal?: number
    blockedPrepared?: number
    generation: number
    running: number
    unknown: number
  }
  productionHealth?: {
    queue: {
      actionableTotal: number
      actionablePrepared: number
      dispatchPending: number
      readyCommands: number
      blockedBudgetUsd: number
      blockedLegacyLimit: number
      blockedOther: number
      oldestActionableWaitSeconds: number | null
      launched30m: number
    }
    cloudUnknownAttempts: number
    reservationsAwaitingReview: number
    issueSamples: Record<'blockedPrepared' | 'cloudUnknown' | 'reservationReview', Array<{
      taskId: string
      title: string
    }>>
    reservationOutcomes: {
      succeeded: number
      failed: number
      cloudUnknown: number
      other: number
    }
  }
  metrics: {
    duplicateGenerationTotal: number
    crossTaskConnectionTotal: number
    unknownAfterSubmitRate: number
    generationSlotUtilization: number
    runnerActiveCommandHosts: number
    runnerOnlineHosts?: number
    runnerOrphanProcesses: number
    // Counts of generations (reservations), not point amounts. The names keep the
    // historical `points*` wording for API compatibility; the backend switched the
    // calculation from `sum(estimated_points)` to a row count on 2026-09-21, when the
    // per-generation estimate fell from 120 to 1 and the unit became "generations".
    pointsReserved: number
    pointsConsumed: number
    pointsUncertain: number
  }
  pipelinePerformance: {
    windowDays: number
    stages: Record<string, {
      sampleCount: number
      p50Seconds: number | null
      p95Seconds: number | null
    }>
    automationBottleneck: string | null
    currentConstraint: string
    throughput: {
      completedVideos24h: number
      completedTasks24h: number
    }
    backlog: {
      analysis: number
      confirmation: number
      materialization: number
      queued: number
      generation: number
      delivery: number
    }
  }
  runners: Array<{ id: string; hostname: string; status: string; freeDiskPercent: number; lastHeartbeatAt: string }>
  canvases: Array<{ projectUuid: string; userId: string; mutationState: string; executionState: string; preparedVideos: number }>
  points: Record<string, number>
  alerts: Array<{ severity: string; code: string; action: string }>
}

export type RuntimeConfigSnapshot = OperationsSnapshot['runtime'] & {
  reason: string
  createdAt: string | null
}
